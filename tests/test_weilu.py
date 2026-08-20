"""清心圆桌测试：协议符合性（对照接入指南§8）+ 状态机 + 安全 + 换人 + 全流程回放。"""
from __future__ import annotations

import asyncio
import io

import httpx
import pytest
import pytest_asyncio

from app import director, group_v2, prompts, safety
from app.config import load_characters, load_theme_config
from app.main import app
from app.session import (
    ENDED,
    FORM_CHAT,
    FORM_PAINTING,
    GREETING,
    build_team,
    detect_form,
    detect_theme,
    extract_text,
    is_confirm,
    make_marker,
    parse_swap_request,
    reconstruct,
    swap_member,
    wants_reset,
)

API_KEY = "sk-weilu-dev-key"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# 稳定seed会选出的队伍id（与 build_team 的 stable_pick 对应，直接构造）
TEAM_ACADEMIC = ["zengguofan", "einstein", "chenmo", "xiaoman"]
TEAM_SELF = ["sushi", "zhuangzi", "linzhiheng", "xiaoman"]


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------- 协议符合性（接入指南 §8 自测清单） ----------


@pytest.mark.asyncio
async def test_models_200_and_auth(client):
    resp = await client.get("/v1/models", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"]
    assert (await client.get("/v1/models")).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert (await client.get("/v1/models", headers=bad)).status_code == 401


@pytest.mark.asyncio
async def test_nonstream_minimal_chat(client, monkeypatch):
    async def fake_generate(providers, messages, **kw):
        return "【小晴】你好。"

    monkeypatch.setattr("app.director.generate", fake_generate)
    resp = await client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "你好"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"]
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_stream_frames_order(client, monkeypatch):
    async def fake_stream(providers, messages, **kw):
        for piece in ("【小晴】", "你好", "呀"):
            yield piece

    monkeypatch.setattr("app.director.stream_generate", fake_stream)
    resp = await client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={"stream": True, "messages": [{"role": "user", "content": "学业压力大"}]},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = [ln[6:] for ln in resp.text.splitlines() if ln.startswith("data: ")]
    assert events[-1] == "[DONE]"
    import json

    frames = [json.loads(e) for e in events[:-1]]
    assert frames[0]["choices"][0]["delta"].get("role") == "assistant"
    contents = "".join(f["choices"][0]["delta"].get("content", "") for f in frames)
    assert "【小晴】" in contents
    stop = frames[-1]
    assert stop["choices"][0]["finish_reason"] == "stop"
    assert stop.get("usage", {}).get("total_tokens", 0) > 0


@pytest.mark.asyncio
async def test_probe_fast_path(client):
    resp = await client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={"stream": True, "max_tokens": 1, "messages": [{"role": "user", "content": "你好"}]},
    )
    assert resp.status_code == 200
    assert "data: [DONE]" in resp.text


@pytest.mark.asyncio
async def test_stream_string_false_is_not_stream(client):
    resp = await client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={"stream": "false", "messages": [{"role": "user", "content": "你好"}]},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_multimodal_content_array(client, monkeypatch):
    async def fake_generate(providers, messages, **kw):
        return "【小晴】你好。"

    monkeypatch.setattr("app.director.generate", fake_generate)
    resp = await client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "最近很迷茫"},
                        {"type": "image_url", "image_url": {"url": "https://x/p.png"}},
                    ],
                }
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"]


# ---------- 会话状态机（8轮制 + 换人） ----------


def _round_msg(round_no, label, theme, form=FORM_CHAT, team_ids=None):
    return {
        "role": "assistant",
        "content": f"【小晴】……\n\n{make_marker(round_no, label, theme, form, team_ids)}",
    }


def test_marker_v3_roundtrip():
    m = make_marker(3, "圆桌入话", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC)
    parsed = None
    from app.session import parse_marker

    parsed = parse_marker("xx\n\n" + m)
    assert parsed[0] == 3 and parsed[1] == "圆桌入话"
    assert parsed[2] == "减压安心之旅" and parsed[3] == FORM_CHAT
    assert parsed[4] == TEAM_ACADEMIC
    # 画会形式
    m2 = make_marker(4, "画作揭晓", "减压安心之旅", FORM_PAINTING, TEAM_ACADEMIC)
    assert parse_marker("x" + m2)[3] == FORM_PAINTING


def test_reconstruct_stages_and_team():
    msgs = [
        {"role": "user", "content": "学业压力大"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
    ]
    st = reconstruct(msgs)
    assert st.stage == "ignite" and st.next_round == 2
    assert st.team_ids == TEAM_ACADEMIC and st.team_variants == 1

    msgs.append({"role": "user", "content": "开炉吧"})
    msgs.append(_round_msg(2, "开场", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC))
    st = reconstruct(msgs)
    assert st.stage == "share" and st.next_round == 3

    msgs = [{"role": "user", "content": "x"},
            _round_msg(8, "成长手记", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC)]
    assert reconstruct(msgs).stage == ENDED


def test_reconstruct_counts_swap_variants():
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "换掉曾国藩"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, ["simaqian", "einstein", "chenmo", "xiaoman"]),
    ]
    st = reconstruct(msgs)
    assert st.team_variants == 2  # 换过1次
    assert st.team_ids[0] == "simaqian"
    assert st.stage == "ignite"  # 仍在确认阶段


def test_detect_theme_and_form():
    themes = load_theme_config()["themes"]
    assert detect_theme("1", themes)["id"] == "academic"
    assert detect_theme("2", themes)["id"] == "connection"
    assert detect_theme("3", themes)["id"] == "love"
    assert detect_theme("4", themes)["id"] == "career"
    assert detect_theme("我想聊学业压力", themes)["id"] == "academic"
    assert detect_theme("最近科研做不动，导师催得紧", themes)["id"] == "academic"
    assert detect_theme("想家，室友合不来", themes)["id"] == "connection"
    assert detect_theme("我到底想要什么呢", themes)["id"] == "self"
    assert detect_theme("秋招投简历压力大", themes)["id"] == "career"
    assert detect_theme("你好", themes) is None
    assert detect_form("画会") == FORM_PAINTING
    assert detect_form("想一起画画") == FORM_PAINTING
    assert detect_form("减压安心之旅") == FORM_CHAT


def test_parse_swap_request():
    team = ["曾国藩", "爱因斯坦", "陈默", "小满"]
    assert parse_swap_request("换掉曾国藩", team) == [0]
    assert parse_swap_request("爱因斯坦不太合适，换一个", team) == [1]
    assert parse_swap_request("都换掉吧", team) == [0, 1, 2, 3]
    assert parse_swap_request("小满和陈默都换掉", team) == [2, 3]
    assert parse_swap_request("开炉吧", team) is None
    assert parse_swap_request("好的", team) is None


def test_is_confirm():
    assert is_confirm("开炉吧")
    assert is_confirm("好的，就这样")
    assert not is_confirm("我想换掉苏轼")


def test_swap_member_same_slot():
    themes = {t["id"]: t for t in load_theme_config()["themes"]}
    chars = load_characters()
    new_id = swap_member(themes["academic"], chars, TEAM_ACADEMIC, 0)
    assert new_id in themes["academic"]["slots"]["bt_hist"]
    assert new_id not in TEAM_ACADEMIC


def test_wants_reset():
    assert wants_reset("再来一场")
    assert not wants_reset("我觉得好多了")


def test_build_team_quota():
    themes = {t["id"]: t for t in load_theme_config()["themes"]}
    chars = load_characters()
    for theme in themes.values():
        team = build_team(theme, chars, f"seed|{theme['id']}")
        types = sorted(m["personality_type"] for m in team)
        assert types == sorted(["been-there", "been-there", "different-perspective", "quiet-resonator"])
        team2 = build_team(theme, chars, f"seed|{theme['id']}")
        assert [m["id"] for m in team] == [m["id"] for m in team2]


# ---------- 安全 ----------


def test_crisis_levels():
    assert safety.detect("我最近总想死") == "high"
    assert safety.detect("活着没意思") == "medium"
    assert safety.detect("今天天气不错") is None


def test_aid_reply_has_hotline():
    reply = safety.aid_reply()
    assert "010-62785252" in reply
    assert "安全" in reply and "小晴" in reply


def test_plan_crisis_high_overrides_everything():
    msgs = [
        {"role": "user", "content": "学业压力大"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "我不想活了"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.kind == "scripted"
    assert "010-62785252" in plan.script
    assert plan.meta["crisis"] == "high"


# ---------- 导演计划 ----------


def test_plan_greeting_short_text_gives_menu():
    plan = director.plan_turn([{"role": "user", "content": "你好"}])
    assert plan.kind == "scripted"
    assert "1️⃣" in plan.script


def test_plan_greeting_substantive_starts_invite():
    plan = director.plan_turn([{"role": "user", "content": "最近感觉自己特别迷茫，不知道想要什么"}])
    assert plan.kind == "generate"
    assert plan.meta["stage"] == "invite" and plan.meta["round"] == 1
    assert "圆桌进度：第1/8轮" in plan.marker and "相邀" in plan.marker
    assert "桌友：" in plan.marker
    assert plan.meta["theme"] == "self"
    assert len(plan.meta["team"]) == 4
    # 相邀轮：小晴主持 + 四位团友各自亮相一句
    assert "本次为你召唤的AI团友" in plan.system_prompt
    assert "自我介绍" in plan.system_prompt
    assert director.STAGE_MIN_MEMBERS["invite"] == 4


def test_plan_seat_confirm_ignites():
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "开炉吧，就他们几位"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.kind == "generate"
    assert plan.meta["stage"] == "ignite" and plan.meta["round"] == 2
    assert "第2/8轮" in plan.marker and "自我介绍与天气站" in plan.marker
    assert plan.meta["team"] == ["曾国藩", "爱因斯坦", "陈默", "小满"]
    # 开场轮（减压安心之旅变体）：第一人称自我介绍+期待+内心天气+压力打分邀请
    assert "自我介绍" in plan.system_prompt
    assert "内心天气" in plan.system_prompt
    assert "打个分" in plan.system_prompt
    assert director.STAGE_MIN_MEMBERS["ignite"] == 4


def test_seat_reply_requesting_peer_type_is_not_ignored():
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "我想要一个博士生同学，因为我开学要读博，我感觉很焦虑"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "ignite"
    assert "对照在场成员" in plan.user_content
    assert "不能被忽略" in plan.user_content


def test_midround_spotlight_when_member_named():
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "好的开始吧"},
        _round_msg(2, "开场", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "我想问问陈默你怎么看，你是不是也觉得主持人小晴不专业"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "share" and plan.meta["round"] == 3
    assert "点名了陈默" in plan.user_content
    assert "不评判带领者" in plan.system_prompt  # 团体铁律：点名应答规则


def test_midround_unfinished_signal_holds_stage():
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "好的开始吧"},
        _round_msg(2, "开场", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "我还没说完呢，我想和林徽因说话"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "share"
    assert "还没说完" in plan.user_content or "说话空间还给同学" in plan.user_content


def test_ended_encore_answers_instead_of_canned_reply():
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "好的开始吧"},
        _round_msg(2, "开场", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "继续"},
        _round_msg(3, "圆桌入话", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "继续"},
        _round_msg(4, "深谈·正常化", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "继续"},
        _round_msg(5, "交换视角", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "继续"},
        _round_msg(6, "真心话", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "继续"},
        _round_msg(7, "临别赠言", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "继续"},
        _round_msg(8, "成长手记", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "明信片为什么没有给我生成出来"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.kind == "generate"
    assert plan.meta["stage"] == "encore"
    assert plan.marker == ""  # 无标记 → 状态保持 ENDED
    assert "明信片" in plan.system_prompt or "再来一场" in plan.system_prompt
    # 再来一场仍然走重开
    msgs[-1] = {"role": "user", "content": "再来一场"}
    plan2 = director.plan_turn(msgs)
    assert plan2.kind == "scripted" and plan2.meta.get("reset") is True


def test_plan_seat_ambiguous_reasks_without_marker():
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "呃"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.kind == "scripted" and plan.marker == ""
    assert "换" in plan.script


def test_plan_swap_flow():
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "换掉曾国藩吧"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.kind == "generate"
    assert plan.meta["stage"] == "seat"
    assert plan.meta["departing"] == ["曾国藩"]
    assert len(plan.meta["arriving"]) == 1
    # 标记仍是第1轮·相邀（新阵容），仍在确认阶段
    assert "第1/8轮 · 相邀" in plan.marker
    new_ids = plan.marker.split("桌友：")[1].rstrip("）").split(",")
    assert new_ids[0] != "zengguofan"
    assert "司马迁" in plan.system_prompt  # 新成员人设已注入（同槽位替补）


def test_plan_swap_cap():
    after_first = ["simaqian", "einstein", "chenmo", "xiaoman"]       # 换1次：曾国藩→司马迁
    after_second = ["simaqian", "newton", "suxiao", "wenyan"]          # 换2次（超量示意）
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "换掉曾国藩"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, after_first),
        {"role": "user", "content": "再换一次新阵容"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, after_second),  # variants=3 → 已用完额度
        {"role": "user", "content": "再换掉苏晓"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.kind == "scripted"
    assert "全部" in plan.script  # NO_MORE_SWAP


def test_plan_mid_session_uses_marker_team():
    # 换人后的队伍要延续（标记携带ID）
    swapped = ["simaqian", "newton", "suxiao", "wenyan"]
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "换掉爱因斯坦"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "开炉吧"},
        _round_msg(2, "开场", "减压安心之旅", FORM_CHAT, swapped),
        {"role": "user", "content": "像一台一直开着但没人用的电视机"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "share" and plan.meta["round"] == 3
    assert plan.meta["team"] == ["司马迁", "牛顿", "苏晓", "温言"]
    assert "simaqian" in plan.marker  # 队伍ID延续进新标记


def test_plan_ended_reset():
    msgs = [{"role": "user", "content": "x"},
            _round_msg(8, "成长手记", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
            {"role": "user", "content": "再来一场"}]
    plan = director.plan_turn(msgs)
    assert plan.kind == "scripted" and plan.meta.get("reset")


def test_validate_turn():
    ok = "【小晴】欢迎。\n【苏轼】我也是，被贬那年常这样。\n【小满】我也是。"
    assert prompts.validate_turn(ok, ["小晴", "苏轼", "小满"]) == []
    issues = prompts.validate_turn("【小晴】你会被治愈的。", ["小晴"])
    assert any("forbidden-word" in i for i in issues)
    issues = prompts.validate_turn("【陌生人】大家好", ["小晴"])
    assert any("unknown-speaker" in i for i in issues)


def test_validate_turn_rejects_legacy_product_imagery():
    for forbidden in ("围炉", "炉火", "火炉", "火焰", "夜话", "深夜", "夜里", "小屋", "🔥", "🪵"):
        issues = prompts.validate_turn(f"【小晴】{forbidden}", ["小晴"])
        assert f"forbidden-word:{forbidden}" in issues


@pytest.mark.asyncio
async def test_stream_hides_legacy_generated_imagery(monkeypatch):
    async def fake_stream(providers, messages, **kw):
        yield "【小晴】我们围炉聊聊。"

    monkeypatch.setattr("app.director.stream_generate", fake_stream)
    plan = director.plan_turn([{"role": "user", "content": "我想参加减压安心之旅"}])
    events = [event async for event in director.stream_plan(plan, [])]
    visible = "".join(payload for kind, payload in events if kind == "delta")
    final = next(payload for kind, payload in events if kind == "final")
    assert "围炉" not in visible and "围炉" not in final
    assert "欢迎来到减压安心之旅" in final
    assert "虚构AI角色" in final


@pytest.mark.asyncio
async def test_report_rejects_legacy_generated_imagery(monkeypatch):
    async def fake_generate(providers, messages, **kw):
        return '{"leader_note":"围炉聊聊", "pressure_note":"压力", "review":[], "member_tips":[], "takeaways":[], "encouragement":"", "pressure_before":null}'

    monkeypatch.setattr("app.director.generate", fake_generate)
    plan = director.plan_turn(_stress_msgs_to(8) + [{"role": "user", "content": "继续"}])
    text, _issues, attachments = await director.execute_plan(plan, [])
    assert "围炉" not in text
    assert "本场主题：清心圆桌活动" in text
    assert len(attachments) == 1 and attachments[0]["mimeType"] == "image/png"


# ---------- 明信片 ----------

HANDNOTE = """【小晴】（把一份手写便签放到你手边）这是今天的成长手记——

📝 本场主题：减压安心之旅

🫧 你带来的：你说最近科研压力大。

💬 桌友们的回响：苏晓说她也有过背着石头的日子。

✨ 值得带走的：
· 慢一点也没关系。
· 石头已经被看清了一些。
· 说出来本身就是松动。

🌱 留给下次的：下次说说石头什么时候会变轻。"""


def test_parse_handnote():
    from app import postcard

    parsed = postcard.parse_handnote(HANDNOTE)
    assert parsed["theme"] == "减压安心之旅"
    assert parsed["message"] == "下次说说石头什么时候会变轻。"
    assert parsed["takeaways"] == ["慢一点也没关系。", "石头已经被看清了一些。", "说出来本身就是松动。"]


def test_parse_handnote_fallback():
    from app import postcard

    parsed = postcard.parse_handnote("【小晴】随手写的几句话")
    assert parsed["message"]
    assert parsed["takeaways"]


def test_postcard_renderer_has_no_legacy_fire_implementation():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "postcard.py").read_text(encoding="utf-8")
    for legacy in ("_draw_fire", "FIRE_ORANGE", "FIRE_YELLOW", "炉火", "火焰", "🪵", "🔥"):
        assert legacy not in source


def test_render_postcard_png():
    from app import postcard

    png = postcard.render_postcard(
        theme_label="减压安心之旅",
        message="圆桌会记得你说过的每一句话。",
        takeaways=["慢一点也没关系。", "石头已经被看清了一些。", "说出来本身就是松动。"],
        member_names=["苏轼", "牛顿", "苏晓", "小满"],
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 50_000


def test_postcard_worst_case_no_overlap():
    from PIL import Image

    from app import postcard

    png = postcard.render_postcard(
        theme_label="学业压力与内卷焦虑",
        message="字" * 40,
        takeaways=["字" * 20] * 3,
        member_names=["司马迁", "王阳明", "林之衡", "顾一帆"],
    )
    img = Image.open(io.BytesIO(png))
    # 暖色明亮版：浅底深字——文字区应有足量暖棕墨色像素
    region = img.crop((110, 360, 970, 900)).convert("RGB")
    pixels = list(region.getdata())
    text_pixels = sum(1 for r, g, b in pixels if r < 160 and g < 140 and b < 120)
    assert text_pixels > 500


def test_postcard_shorten():
    from app import postcard

    assert postcard._shorten("慢一点也没关系。", 20) == "慢一点也没关系。"
    long = "你很诚实地说出了压力从哪里来，也说出了它压在身上的样子。"
    s = postcard._shorten(long, 20)
    assert len(s) <= 21 and (s.endswith("。") or s.endswith("，") or s.endswith("…"))


@pytest.mark.asyncio
async def test_report_returns_postcard_attachment(client, monkeypatch):
    async def fake_generate(providers, messages, **kw):
        return HANDNOTE

    monkeypatch.setattr("app.director.generate", fake_generate)
    msgs = [
        {"role": "user", "content": "聊学业压力"},
        _round_msg(7, "临别赠言", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "谢谢大家"},
    ]
    resp = await client.post("/v1/chat/completions", headers=HEADERS, json={"messages": msgs})
    assert resp.status_code == 200
    data = resp.json()
    att = data.get("x_soda", {}).get("attachments", [])
    assert len(att) == 1
    assert att[0]["fileType"] == "image" and att[0]["mimeType"] == "image/png"
    path = att[0]["fileUrl"].split("/", 3)[3]
    file_resp = await client.get("/" + path)
    assert file_resp.status_code == 200
    assert file_resp.content[:4] == b"\x89PNG"


# ---------- 画会 ----------


def test_plan_greeting_painting():
    """绘画入口现在统一进入圆桌画室轻团体（不再走v1画会邀请）。"""
    plan = director.plan_turn([{"role": "user", "content": "一起画画吧"}])
    assert plan.kind == "scripted"
    assert plan.meta.get("studio") is True
    assert "QXPA" in plan.marker
    assert "我想在这幅画上加上" in plan.script
    assert "圆桌画室" in plan.script


def test_painting_strokes_and_reveal(monkeypatch):
    from app.session import make_marker as mm

    async def fake_generate(providers, messages, **kw):
        return "【小晴】好，画作正在显影。"

    async def fake_painting(prompt, timeout=150.0):
        return b"\xff\xd8\xff\xe0FAKEJPEG"

    monkeypatch.setattr("app.director.generate", fake_generate)
    monkeypatch.setattr("app.imagegen.generate_painting", fake_painting)

    team = ["zengguofan", "einstein", "chenmo", "xiaoman"]
    strokes_msg = (
        "【小晴】我希望画上有一轮明亮的太阳，旁边有一张圆桌和几杯热茶。\n"
        "【曾国藩】我希望画上有江上的一叶小舟。\n"
        "【爱因斯坦】我想加上一盏还亮着的灯。\n"
        "【陈默】我想在这幅画上加上一条没走完的栈道。\n"
        "【小满】我想加上一扇虚掩的门。\n\n"
        + mm(3, "落笔", "减压安心之旅", FORM_PAINTING, team)
    )
    msgs = [
        {"role": "user", "content": "画会，聊学业压力"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_PAINTING, team),
        {"role": "user", "content": "开炉吧"},
        _round_msg(2, "开场", "减压安心之旅", FORM_PAINTING, team),
        {"role": "user", "content": "（成员们添笔）"},
        {"role": "assistant", "content": strokes_msg},
        {"role": "user", "content": "我想加上一轮刚升起来的月亮"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "reveal" and plan.meta["form"] == FORM_PAINTING
    assert "月亮" in plan.meta.get("painting_prompt", "")
    assert "小舟" in plan.meta["painting_prompt"]
    body, issues, attachments = asyncio.run(director.execute_plan(plan, []))
    assert "第4/8轮" in body and "形式：画会" in body
    assert len(attachments) == 1
    assert attachments[0]["mimeType"] == "image/jpeg"


# ---------- 流式节奏器 ----------


def _run_paced(chunks, **env):
    import asyncio
    import os

    from app.pacer import paced

    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:

        async def source():
            for c in chunks:
                yield c

        async def collect():
            out, timings = [], []
            start = asyncio.get_event_loop().time()
            async for d in paced(source()):
                if d:
                    out.append(d)
                    timings.append(asyncio.get_event_loop().time() - start)
            return "".join(out), timings

        return asyncio.run(collect())
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_pacer_preserves_content():
    text = "【小晴】欢迎来到圆桌。\n\n【苏轼】哈哈，我来喝杯热茶。\n\n【小满】我也是。\n"
    chunks = [text[i : i + 3] for i in range(0, len(text), 3)]
    out, _ = _run_paced(chunks, WEILU_PACE_CHAR_MS="1", WEILU_PACE_PAUSE_MS="10")
    assert out == text


def test_pacer_pauses_between_speakers():
    text = "【A】第一句。\n\n【B】第二句。\n"
    out, timings = _run_paced(
        [text], WEILU_PACING="true", WEILU_PACE_CHAR_MS="1", WEILU_PACE_PAUSE_MS="200"
    )
    assert out == text
    t_b = timings[out.index("【B】")]
    t_a = timings[out.index("【A】")]
    assert t_b - t_a >= 0.15


def test_pacer_disabled_passthrough():
    text = "【A】一。\n\n【B】二。\n"
    out, timings = _run_paced([text], WEILU_PACING="false")
    assert out == text
    assert timings[-1] < 0.05


# ---------- fake LLM 全流程回放（8轮制） ----------


FAKE_TURN = (
    "【小晴】欢迎来到清心圆桌，今天我们不评判、不着急。\n"
    "【苏轼】哈哈，我今天是来躲清静的。\n"
    "【小满】我……有点紧张，但很高兴坐在这里。\n"
    "【曾国藩】老夫惯常早睡，今天破例坐一坐。\n"
    "【陈默】实验室刚出来，来桌边坐坐。\n"
    "【小晴】那我们开始今天的第一个问题吧。"
)


def test_full_session_flow_with_fake_llm(monkeypatch):
    """完整8轮会话（含换人一次）：状态逐轮推进、队伍延续、标记闭环。"""
    async def fake_generate(providers, messages, **kw):
        return FAKE_TURN

    monkeypatch.setattr("app.director.generate", fake_generate)
    characters = load_characters()

    # 旧流程仍由其他主题使用；减压主题已切换到可停留的v2状态机。
    messages = [{"role": "user", "content": "最近不知道自己真正想要什么"}]

    def run_plan():
        plan = director.plan_turn(messages)
        if plan.kind == "scripted":
            return plan.script
        text, _issues, _att = asyncio.run(director.execute_plan(plan, []))
        assert "【小晴】" in text and plan.marker in text
        return text

    # 1) 相邀（队伍由seed选出，从标记动态读取）
    messages.append({"role": "assistant", "content": run_plan()})
    st = reconstruct(messages)
    original_first = st.team_ids[0]
    assert st.stage == "ignite"

    # 2) 换掉第一位成员 → 新阵容标记，仍在确认阶段
    messages.append({"role": "user", "content": f"换掉{characters[original_first]['name']}"})
    messages.append({"role": "assistant", "content": run_plan()})
    st = reconstruct(messages)
    assert st.team_variants == 2 and st.team_ids[0] != original_first
    assert st.stage == "ignite"

    # 3) 确认开炉 → 8轮走完
    for reply in ["开炉吧", "像一台没人用的电视机", "是一块黑色石头", "慢一点也没关系",
                  "谢谢大家", "嗯", "期待", "好"]:
        messages.append({"role": "user", "content": reply})
        messages.append({"role": "assistant", "content": run_plan()})

    final = reconstruct(messages)
    assert final.stage == ENDED
    assert final.team_ids[0] != original_first  # 换人阵容延续到最后
    assert final.team_variants == 2


def test_report_prompt_is_leader_only():
    leader = load_theme_config()["leader"]
    theme = next(t for t in load_theme_config()["themes"] if t["id"] == "self")
    prompt = prompts.build_report_system_prompt(leader, theme)
    assert "成长手记" in prompt
    assert "小晴" in prompt


def test_each_stage_introduces_activity():
    cfg = load_theme_config()
    leader = cfg["leader"]
    theme = next(t for t in cfg["themes"] if t["id"] == "self")  # 通用主题（无主题变体）
    team = build_team(theme, load_characters(), "intro-seed")
    stages = cfg["stages"]
    pstages = cfg.get("painting_stages", {})

    # 圆桌各正式环节：小晴先介绍活动名称与目的
    chat_names = {
        "ignite": "相似圈", "share": "主题分享", "depth": None,
        "persp": "交换视角", "heart": "真心话", "close": "总结与告别",
    }
    for stage_id, name in chat_names.items():
        p = prompts.build_turn_system_prompt(leader, team, stage_id, theme, stages, 2, form=FORM_CHAT)
        assert ("介绍本环节" in p) or ("介绍并开启本环节" in p), stage_id
        if name:
            assert name in p, stage_id

    # 画会各环节（含第7轮真心话）：不缺分支，且都有介绍指令
    paint_names = {
        "ignite": "画会开场", "strokes": "落笔", "reveal": "画作揭晓",
        "resonance": "画边回响", "meaning": "笔触心声", "heart": "真心话",
    }
    for stage_id, name in paint_names.items():
        p = prompts.build_turn_system_prompt(
            leader, team, stage_id, theme, stages, 3,
            form=FORM_PAINTING, painting_stages_cfg=pstages,
        )
        assert ("介绍本环节" in p) or ("介绍并开启本环节" in p), stage_id
        assert name in p, stage_id


# ---------- 减压安心之旅（主题改造） ----------


def test_stress_theme_routing_broad():
    themes = load_theme_config()["themes"]
    assert detect_theme("最近心累，什么都不想干", themes)["id"] == "academic"
    assert detect_theme("工作压力好大，喘不过气", themes)["id"] == "academic"
    assert detect_theme("失眠好几天了，撑不住", themes)["id"] == "academic"
    assert detect_theme("想家了，宿舍也融不进去", themes)["id"] == "connection"
    assert detect_theme("我是谁，想要什么", themes)["id"] == "self"


def test_stress_v2_contract_and_three_consistent_peers():
    plan = director.plan_turn([{"role": "user", "content": "我想参加减压安心之旅"}])
    assert plan.meta["stage"] == "v2_contract" and plan.meta["theme"] == "academic"
    assert plan.meta["v2"] is True and len(plan.meta["team"]) == 3
    assert "虚构AI" in plan.system_prompt and "不是心理咨询或治疗" in plan.system_prompt
    assert "不要询问用户想选择哪种参与方式" in plan.system_prompt
    assert "一次只处理一个主要互动任务" in plan.system_prompt
    assert plan.script.index("第一个活动") < plan.script.index("【林之衡】")
    assert "约20分钟" in plan.script and "四个活动" in plan.script and "《圆桌留笺》" in plan.script
    assert "每个人的故事都会被单独聊到" in plan.script
    assert "这一圈只做介绍，不追问、不讨论" in plan.script
    assert "回复一个选项" not in plan.script
    assert "告诉大家希望怎么称呼你" in plan.script and "名字加学姐/学长" in plan.script
    assert "现在轮到你做自我介绍" in plan.script
    assert "正式的位置" not in plan.script and "我先听一轮" not in plan.script
    state = group_v2.reconstruct([{"role": "assistant", "content": plan.marker}])
    assert state is not None and state.phase == 1 and len(state.team_ids) == 3


def test_stress_v2_named_followup_stays_in_phase():
    first = director.plan_turn([{"role": "user", "content": "科研压力很大，导师一直催"}])
    messages = [
        {"role": "user", "content": "科研压力很大，导师一直催"},
        {"role": "assistant", "content": "【小晴】欢迎。\n" + first.marker},
        {"role": "user", "content": f"{first.meta['team'][0]}，你刚才为什么不敢求助？"},
    ]
    follow = director.plan_turn(messages)
    state = group_v2.reconstruct([{"role": "assistant", "content": follow.marker}])
    assert follow.meta["v2"] is True and state is not None
    assert state.phase == 1 and state.mode == "focus" and state.focus


def test_stress_v2_self_intro_is_closed_and_facilitator_advances():
    first = director.plan_turn([{"role": "user", "content": "最近压力很大"}])
    base = [
        {"role": "user", "content": "最近压力很大"},
        {"role": "assistant", "content": "【小晴】欢迎。\n" + first.marker},
    ]
    stay = director.plan_turn(base + [{"role": "user", "content": "我叫凌云，博六，最近压力很大"}])
    stay_state = group_v2.reconstruct([{"role": "assistant", "content": stay.marker}])
    assert stay_state is not None and stay_state.phase == 2
    advance = director.plan_turn(base + [{"role": "user", "content": "可以进入下一项"}])
    advance_state = group_v2.reconstruct([{"role": "assistant", "content": advance.marker}])
    assert advance_state is not None and advance_state.phase == 2


def test_v2_facilitator_uses_soft_focus_limit_after_user_story():
    state = group_v2.GroupState(
        phase=2, mode="story", focus="user", exchanges=3,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    advanced, action = group_v2.next_state("我感觉肩膀有点紧", state)
    assert action == "discuss" and advanced.phase == 2
    assert advanced.mode == "ask_story_transition" and advanced.focus == "user"


def test_v2_user_can_continue_or_shift_a_focus():
    state = group_v2.GroupState(
        phase=2, mode="story", focus="linzhiheng", exchanges=3,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    stayed, action = group_v2.next_state("我还想继续聊林之衡这件事", state)
    assert action == "discuss" and stayed.focus == "linzhiheng" and stayed.exchanges == 4
    shifted, action = group_v2.next_state("这段差不多了，下一位吧", state)
    assert action == "discuss" and shifted.focus == "xunanzhi"
    assert shifted.mode == "story_first" and shifted.exchanges == 0


def test_v2_story_circle_observer_does_not_skip_people():
    state = group_v2.GroupState(
        phase=2, mode="main", exchanges=0,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    observed, action = group_v2.next_state("你们自己聊，我先旁听", state)
    prompt = group_v2.build_system_prompt(observed, action)
    assert action == "observe" and observed.phase == 2
    assert "四个故事的压力圆桌" in prompt


def test_v2_opening_closes_without_interview():
    state = group_v2.GroupState(
        phase=1, mode="main", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    prompt = group_v2.build_system_prompt(state, "discuss")
    assert "严禁继续互动或追问" in prompt
    assert "立即宣布第二个活动" in prompt
    assert "不得误说“你们三位”" in prompt
    assert "不得把继续工作、熬夜、没有离场或硬撑本身称作力量" in prompt


def test_v2_opening_handoff_validator_rejects_stranded_user():
    first = director.plan_turn([{"role": "user", "content": "我想参加减压安心之旅"}])
    plan = director.plan_turn([
        {"role": "user", "content": "我想参加减压安心之旅"},
        {"role": "assistant", "content": first.script},
        {"role": "user", "content": "我叫凌云，博六，最近压力很大"},
    ])
    stranded = "【林之衡】我也会慌。\n【许南枝】事情叠在一起很累。\n【陈默】我知道那种悬着。"
    handed = stranded + "\n【小晴】我听见大家都在谈悬着。之衡，你来接。\n【林之衡】凌云，哪一块最想先讲给我们听？"
    assert plan.meta["action"] == "advance"
    assert director._v2_advance_ok(stranded, plan) is False
    assert director._v2_advance_ok(handed, plan) is False


def test_v2_advance_validator_requires_visible_activity_and_peer_demo():
    state = group_v2.GroupState(
        phase=1, mode="main", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    messages = [
        {"role": "user", "content": "我想参加减压安心之旅"},
        {"role": "assistant", "content": "【小晴】开场。\n" + group_v2.marker(state)},
        {"role": "user", "content": "没有"},
    ]
    plan = director.plan_turn(messages)
    assert plan.meta["action"] == "advance" and plan.meta["stage_label"] == "四个故事的压力圆桌"
    missing = "【小晴】我们把刚才收在这里。\n【林之衡】我也有压力。"
    complete = ("【小晴】接下来是四个故事的压力圆桌，每个人都会被聊到。"
                "\n【林之衡】我删消息是怕被看低。\n【许南枝】你删掉后还会继续查吗？"
                "\n【小晴】现在轮到你，也可以回应之衡一句。")
    assert director._v2_advance_ok(missing, plan) is False
    assert director._v2_advance_ok(complete, plan) is True
    premature = ("【小晴】接下来是四个故事的压力圆桌。"
                 "\n【林之衡】我删消息是怕被看低。"
                 "\n【许南枝】我也会躲聊天框。"
                 "\n【小晴】之衡的故事先聊到这里，下一位是许南枝。")
    assert director._v2_advance_ok(premature, plan) is False


def test_v2_facilitator_cues_user_when_peer_discussion_forgets():
    state = group_v2.GroupState(
        phase=3, mode="main", exchanges=0,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    plan = director._v2_plan([], "我的压力来自论文，胸口很紧", state, None)
    stranded = "【林之衡】我也会胸口紧。\n【许南枝】我有时在食堂排队会突然想到任务。\n【陈默】身体常常比想法先知道。"
    fixed = director._ensure_v2_participant_cue(stranded, plan)
    assert fixed.startswith(stranded)
    assert "【小晴】陈默刚才说到这里" in fixed
    assert "你想接一句" in fixed and "继续听" in fixed

    already_cued = stranded + "\n【陈默】你想从哪个时刻说起？"
    assert director._ensure_v2_participant_cue(already_cued, plan) == already_cued
    leader_cued = stranded + "\n【小晴】现在轮到你，请说一小段就好。"
    assert director._ensure_v2_participant_cue(leader_cued, plan) == leader_cued


def test_v2_never_allows_ai_to_speak_as_real_participant():
    state = group_v2.GroupState(
        phase=2, mode="main", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    plan = director._v2_plan([], "我叫凌云", state, None)
    forged = "【许南枝】我想问问你。\n【凌云】我博一时也删过消息。"
    assert "unknown-speaker:凌云" in director._v2_output_issues(forged, plan)
    forged_me = "【林之衡】你怎么看？\n【我】我觉得还好。"
    assert "unknown-speaker:我" in director._v2_output_issues(forged_me, plan)
    valid = "【许南枝】我想问问你。\n【小晴】现在轮到你回应。"
    assert director._v2_output_issues(valid, plan) == []
    stranded_ai = "【小晴】许南枝，你把累说出来以后是什么感觉？"
    assert "unanswered-ai-cue" in director._v2_output_issues(stranded_ai, plan)


def test_v2_missing_user_focus_reopens_story_instead_of_generating_report():
    state = group_v2.GroupState(
        phase=4, mode="main", exchanges=0,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    reopened, action = group_v2.next_state("是不是还没有把焦点集中到我这里啊", state)
    assert action == "discuss" and reopened.phase == 2
    assert reopened.mode == "user_focus_repair" and reopened.focus == "user"
    assert reopened.exchanges == 0


def test_v2_mutual_and_closing_transitions_require_multiple_members():
    base = {"v2": True, "action": "advance", "team": ["林之衡", "许南枝", "陈默"]}
    mutual = type("Plan", (), {"meta": {**base, "round": 3, "stage_label": "互助讨论与减压共创"}})()
    one_peer = "【小晴】进入互助讨论与减压共创，看看共同线索。\n【林之衡】我会删消息。\n【小晴】轮到你。"
    assert director._v2_advance_ok(one_peer, mutual) is False
    closing = type("Plan", (), {"meta": {**base, "round": 4, "stage_label": "收获与告别"}})()
    incomplete = "【小晴】进入收获与告别。\n【林之衡】我带走一句话。\n【小晴】轮到你。"
    assert director._v2_advance_ok(incomplete, closing) is False


def test_v2_final_phase_requires_explicit_end_before_html():
    state = group_v2.GroupState(
        phase=4, mode="main", exchanges=0,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    stayed, action = group_v2.next_state("我不想结束", state)
    assert action == "discuss" and stayed.phase == 4 and stayed.mode != "report"
    closed, action = group_v2.next_state("今天先到这里", state)
    assert action == "close_report" and closed.mode == "report"


@pytest.mark.asyncio
async def test_v2_final_phase_stream_returns_roundtable_html(monkeypatch):
    state = group_v2.GroupState(
        phase=4, mode="main", exchanges=0,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    messages = [
        {"role": "user", "content": "我想参加减压安心之旅"},
        {"role": "assistant", "content": "【小晴】到了告别环节。\n" + group_v2.marker(state)},
        {"role": "user", "content": "今天先到这里"},
    ]
    plan = director.plan_turn(messages)
    assert plan.meta.get("report_v2") is True

    async def fake_generate(providers, llm_messages, **kwargs):
        return '{"approach_moment":"一起聊到不敢开口","user_impact":"一句熟能生巧影响了之衡","member_impact":"未确认","differences":["先休息","先做一点"],"response_need":"未明确","real_world_phrase":"先做一点","pressure_before":9,"pressure_after":8,"leader_note":"谢谢你和大家坐到最后。"}'

    monkeypatch.setattr("app.director.generate", fake_generate)
    events = [event async for event in director.stream_plan(plan, [])]
    attachments = next(payload for kind, payload in events if kind == "attachments")
    assert attachments[0]["fileName"].endswith("圆桌留笺.html")


def test_v2_opening_gives_year_major_and_everyday_identity():
    state = group_v2.GroupState(
        phase=1, team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    opening = group_v2.opening_script(state)
    assert "研一，生命学院生物信息方向" in opening
    assert "大三，公共管理专业" in opening
    assert "博三，材料学院能源材料方向" in opening
    assert "我今天来这里" in opening
    assert "彼此倾听、理解和尊重" in opening
    assert "不批评、不指责" in opening


def test_v2_story_and_mutual_transitions_wait_for_user_consent():
    state = group_v2.GroupState(
        phase=2, mode="story", focus="chenmo", exchanges=4,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    waiting, action = group_v2.next_state("我还在想这件事", state)
    assert action == "discuss" and waiting.phase == 2
    assert waiting.mode == "ask_story_transition"
    stayed, action = group_v2.next_state("我还想再聊聊", waiting)
    assert action == "discuss" and stayed.mode == "story"

    mutual = group_v2.GroupState(
        phase=3, mode="mutual", exchanges=4,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    asking, action = group_v2.next_state("这个办法我先记住了", mutual)
    assert action == "discuss" and asking.phase == 3
    assert asking.mode == "ask_activity_transition"
    advanced, action = group_v2.next_state("可以进入下一项", asking)
    assert action == "advance" and advanced.phase == 4


def test_v2_story_focus_never_repeats_a_completed_peer_or_skips_user():
    state = group_v2.GroupState(
        phase=2, mode="story", focus="chenmo", exchanges=2,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
        completed=["linzhiheng", "xunanzhi"],
    )
    next_state, action = group_v2.next_state("下一位", state)
    assert action == "discuss" and next_state.focus == "user"
    assert next_state.mode == "story_first"
    assert next_state.completed == ["linzhiheng", "xunanzhi", "chenmo"]


def test_v2_last_story_explicit_transition_enters_mutual_immediately():
    state = group_v2.GroupState(
        phase=2, mode="story_second", focus="user", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
        completed=["linzhiheng", "xunanzhi", "chenmo"],
    )
    advanced, action = group_v2.next_state("够了，转场", state)
    assert action == "advance" and advanced.phase == 3


def test_v2_close_words_and_report_request_generate_real_report_route():
    closing = group_v2.GroupState(
        phase=4, mode="main", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    closed, action = group_v2.next_state("散场吧", closing)
    assert action == "close_report" and closed.mode == "report"
    old_done = group_v2.GroupState(
        phase=2, mode="story", focus="user", exchanges=3,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
        completed=["linzhiheng", "xunanzhi", "chenmo", "user"],
    )
    closed, action = group_v2.next_state("我的HTML报告呢", old_done)
    assert action == "close_report" and closed.phase == 4

    mutual = group_v2.GroupState(
        phase=3, mode="mutual", exchanges=2,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    closed, action = group_v2.next_state("再见", mutual)
    assert action == "close_report" and closed.mode == "report"
    closed, action = group_v2.next_state("我的html报告在哪", mutual)
    assert action == "close_report" and closed.mode == "report"


def test_v2_mutual_can_explicitly_enter_farewell_stage():
    mutual = group_v2.GroupState(
        phase=3, mode="mutual", exchanges=3,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    farewell, action = group_v2.next_state("收获与告别", mutual)
    assert action == "advance" and farewell.phase == 4


def test_v2_complete_focus_order_reaches_user_then_real_report():
    state = group_v2.GroupState(
        phase=2, mode="story_second", focus="xunanzhi", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
        completed=["linzhiheng"],
    )
    state, action = group_v2.next_state("下一位", state)
    assert action == "discuss" and state.focus == "chenmo" and state.mode == "story_first"
    state, action = group_v2.next_state("我觉得陈默很尽责", state)
    assert state.focus == "chenmo" and state.mode == "story_second"
    state, action = group_v2.next_state("下一位", state)
    assert action == "discuss" and state.focus == "user" and state.mode == "story_first"
    state, action = group_v2.next_state("我的压力是同时做太多事", state)
    assert state.focus == "user" and state.mode == "story_second"
    state, action = group_v2.next_state("够了，进入下一项", state)
    assert action == "advance" and state.phase == 3
    assert state.completed == ["linzhiheng", "xunanzhi", "chenmo", "user"]
    state, action = group_v2.next_state("收获与告别", state)
    assert action == "advance" and state.phase == 4
    state, action = group_v2.next_state("再见", state)
    assert action == "close_report" and state.mode == "report"


def test_v2_user_focus_rejects_multiple_questions_and_fake_report_claims():
    state = group_v2.GroupState(
        phase=2, mode="story", focus="user", exchanges=2,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    plan = director._v2_plan([], "我压力很大", state, None)
    too_many = "【林之衡】你最担心什么？\n【许南枝】论文还是秋招更重要？"
    assert "too-many-user-focus-questions" in director._v2_output_issues(too_many, plan)
    fake = "【小晴】HTML已经生成，链接是 https://example.com/report"
    assert "premature-report-claim" in director._v2_output_issues(fake, plan)
    fake_later = "【小晴】报告会在活动结束后由系统附上，你退出后就会看到。"
    assert "premature-report-claim" in director._v2_output_issues(fake_later, plan)


def test_v2_shifted_focus_uses_display_name_and_hard_round_protocol():
    previous = group_v2.GroupState(
        phase=2, mode="story", focus="xunanzhi", exchanges=2,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
        completed=["linzhiheng"],
    )
    plan = director._v2_plan([], "下一位", previous, None)
    assert plan.meta["focus"] == "chenmo"
    assert plan.meta["focus_name"] == "陈默"
    assert plan.meta["mode"] == "story_first"
    fallback = director._v2_safe_continuation(plan)
    assert "【陈默】" in fallback
    assert "我们留在林之衡" not in fallback
    assert "异常数据" in fallback and "请教消息" not in fallback

    missing = "【林之衡】我先聊聊自己的事。\n【小晴】你想说点什么？"
    assert "missing-new-focus-speaker" in director._v2_output_issues(missing, plan)
    no_user_cue = "【陈默】我把电脑关了。\n【许南枝】我也躲过问题。\n【小晴】陈默，你还想补充吗？"
    assert "missing-user-first-round-cue" in director._v2_output_issues(no_user_cue, plan)

    first = group_v2.GroupState(
        phase=2, mode="story_first", focus="chenmo", exchanges=0,
        team_ids=previous.team_ids, card_ids=previous.card_ids,
        completed=["linzhiheng", "xunanzhi"],
    )
    second_plan = director._v2_plan([], "我也有过", first, None)
    no_choice = "【陈默】谢谢你，我听到了。\n【小晴】你还想说什么？"
    assert "missing-story-transition-choice" in director._v2_output_issues(no_choice, second_plan)


@pytest.mark.asyncio
async def test_v2_nonstream_hard_validation_replaces_invalid_model_output(monkeypatch):
    async def fake_generate(*args, **kwargs):
        return "【小晴】现在轮到你。\n【凌云】我最近很慌。\n【陈默】我懂。"

    monkeypatch.setattr("app.director.generate", fake_generate)
    current = group_v2.GroupState(
        phase=2, mode="story_second", focus="chenmo", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
        completed=["linzhiheng", "xunanzhi"],
    )
    plan = director._v2_plan([], "下一位", current, None)
    body, issues, _attachments = await director.execute_plan(plan, [])
    assert issues
    assert "【凌云】" not in body
    assert "现在轮到你的故事" in body

    first = group_v2.GroupState(
        phase=2, mode="story_first", focus="linzhiheng", exchanges=0,
        team_ids=current.team_ids, card_ids=current.card_ids,
    )
    second_plan = director._v2_plan([], "我也有类似经历", first, None)
    body, issues, _attachments = await director.execute_plan(second_plan, [])
    assert issues
    assert "下一位" in body and ("继续" in body or "留在" in body)


def test_v2_mutual_fallback_gives_advice_when_user_asks_for_it():
    state = group_v2.GroupState(
        phase=3, mode="mutual", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    plan = director._v2_plan([], "我愿意听听大家有哪些具体办法", state, None)
    text = director._v2_safe_continuation(plan)
    assert "担心的事" in text and "卡住了，还是累了" in text and "走十分钟" in text


def test_v2_story_first_round_must_hand_off_to_user_before_transition_choice():
    state = group_v2.GroupState(
        phase=2, mode="story_first", focus="linzhiheng", exchanges=0,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    second, action = group_v2.next_state("下一位", state)
    assert action == "discuss" and second.focus == "linzhiheng"
    assert second.mode == "story_second"
    prompt = group_v2.build_system_prompt(second, action)
    assert "两步焦点协议的第二轮" in prompt
    assert "才可以温柔问真人" in prompt


def test_v2_crisis_stays_in_safety_state_until_user_confirms_and_resumes():
    state = group_v2.GroupState(
        phase=2, mode="story", focus="chenmo", exchanges=2,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    first = director.plan_turn([
        {"role": "user", "content": "压力很大"},
        {"role": "assistant", "content": "【小晴】继续。\n" + group_v2.marker(state)},
        {"role": "user", "content": "我不想活了"},
    ])
    assert first.meta["crisis"] == "high" and "010-62785252" in first.script
    confirmed = director.plan_turn([
        {"role": "assistant", "content": first.script},
        {"role": "user", "content": "我有抑郁症"},
    ])
    assert confirmed.meta["stage"] == "v2_safety"
    assert "圆桌还先暂停着" in confirmed.script


def test_v2_roundtable_note_is_visual_and_safe():
    from app import report_v2_html

    data = report_v2_html.render({
        "participant_name": "凌云",
        "discussion_topics": ["毕业论文与秋招叠加", "不敢向别人求助"],
        "stress_suggestions": ["先找熟悉的师姐聊一次简历", "跑步后再决定是否继续工作"],
        "approach_moment": "你追问南枝为什么不拒绝，她第一次说出了怕不被需要。",
        "user_impact": "你说拒绝不等于抛下别人，南枝重新理解了边界。",
        "member_impact": "你确认陈默的停顿让自己愿意慢一点说。",
        "differences": ["先停下来照顾身体", "先完成一个最小动作"],
        "response_need": "先听我说完，再问我要不要办法。",
        "real_world_phrase": "我现在不需要办法，能先听我说五分钟吗？",
        "pressure_before": 8, "pressure_after": 6,
        "leader_note": "这场圆桌留下的不是统一答案，而是更清楚的需要。",
    }, ["林之衡", "许南枝", "陈默"])
    text = data.decode("utf-8")
    assert "QINGXIN ROUNDTABLE" in text and "这次主要讨论了什么" in text
    assert "林之衡" in text and "凌云" in text and "AI带领者" in text
    assert "data:image/jpeg;base64," in text and "qingxin-roundtable-bg" not in text
    assert "团体共同提炼的减压建议" in text and "先找熟悉的师姐" in text
    assert "010-62785252" in text and "010-62782007" in text and "清华小清心" in text
    assert "为什么斑马不得胃溃疡" in text and "自我关怀的力量" in text
    assert "心理评估" in text and "@media(max-width:620px)" in text


def test_v2_mutual_discussion_is_elastic_and_advice_is_not_capped():
    state = group_v2.GroupState(
        phase=3, mode="mutual", exchanges=1,
        team_ids=["linzhiheng", "xunanzhi", "chenmo"],
        card_ids=["help_message", "extra_work", "failed_again"],
    )
    continued, action = group_v2.next_state("我还没说完，我还有两个办法想补充", state)
    assert action == "discuss" and continued.phase == 3
    prompt = group_v2.build_system_prompt(continued, action)
    assert "不设固定建议数量" in prompt and "多个" in prompt
    assert "小晴始终称呼真人" in prompt


def test_stress_ignite_intro_weather():
    msgs = [
        {"role": "user", "content": "我想参加减压安心之旅"},
        _round_msg(1, "相邀", "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC),
        {"role": "user", "content": "开始吧"},
    ]
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "ignite"
    assert "自我介绍" in plan.system_prompt and "内心天气" in plan.system_prompt
    assert "期待" in plan.system_prompt
    assert "打个分" in plan.system_prompt  # 压力温度计


def test_stress_depth_has_normalization():
    msgs = _stress_msgs_to(4) + [{"role": "user", "content": "论文一直改，肩膀一直是紧的"}]
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "depth"
    assert "正常化" in plan.system_prompt or "正常反应" in plan.system_prompt


def _stress_msgs_to(round_no: int) -> list[dict]:
    """构造进行到第 round_no 轮（不含该轮标记）的会话历史。"""
    labels = {1: "相邀", 2: "自我介绍与天气站", 3: "压力地图", 4: "深谈·正常化", 5: "呼吸站",
              6: "真心话", 7: "我的减压清单"}
    fills = {1: "开始吧", 2: "论文改不完", 3: "肩膀紧、心里像阴天", 4: "最沉的是怕来不及",
             5: "怕自己不行", 6: "谢谢大家", 7: "嗯"}
    msgs = [{"role": "user", "content": "我想参加减压安心之旅"}]
    for r in range(1, round_no):
        msgs.append({"role": "user", "content": fills[r]})
        msgs.append(_round_msg(r, labels[r], "减压安心之旅", FORM_CHAT, TEAM_ACADEMIC))
    return msgs


def test_stress_persp_is_breathing_station():
    msgs = _stress_msgs_to(5) + [{"role": "user", "content": "怕自己不行"}]
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "persp"
    assert "呼吸站" in plan.system_prompt and "腹式呼吸" in plan.system_prompt
    assert "情绪脑" in plan.system_prompt  # 三脑白话讲法
    assert "http" not in plan.system_prompt  # URL 只能由代码追加，prompt 里绝不出现
    card = plan.meta.get("resource_card_text") or ""
    assert "bilibili" in card and "再练一次" in card
    assert "three-brains.html" in card
    assert plan.meta.get("slow_pacing") is True


def test_progress_line_format():
    assert director.progress_line(3, "压力地图") == "📍 环节 3/8 · 压力地图｜还剩5个环节"
    assert director.progress_line(8, "成长报告") == "📍 环节 8/8 · 成长报告｜本场最后一环"


def test_finalize_appends_marker_progress_and_card():
    msgs = _stress_msgs_to(5) + [{"role": "user", "content": "怕自己不行"}]
    plan = director.plan_turn(msgs)
    final = director._finalize_body(plan, "【小晴】好")
    assert "（圆桌进度：第5/8轮" in final
    assert "📍 环节 5/8" in final and "还剩3个环节" in final
    assert "bilibili" in final


def test_no_stove_wording_anywhere():
    for text in (prompts.GREETING_TEXT, prompts.MENU_RETRY_TEXT, prompts.ENDED_TEXT,
                 prompts.LLM_FALLBACK_TEXT, prompts.SEAT_REASK_TEXT, prompts.NO_MORE_SWAP_TEXT):
        assert "炉" not in text
    cfg = load_theme_config()
    for t in cfg["themes"]:
        assert "炉" not in t["menu_desc"]


@pytest.mark.asyncio
async def test_report_html_flow(monkeypatch):
    import json as _json

    payload = _json.dumps({
        "leader_note": "这一场你把压力摊开来看了看，还练了呼吸。",
        "pressure_note": "论文反复修改与紧绷的肩膀",
        "review": ["相邀开桌", "天气站相识", "压力地图", "深谈与正常化", "呼吸站"],
        "member_tips": [{"name": "曾国藩", "tip": "大事拆成小步子"},
                        {"name": "爱因斯坦", "tip": "先安顿情绪脑"},
                        {"name": "陈默", "tip": "写下来就不乱"},
                        {"name": "小满", "tip": "允许自己慢"}],
        "takeaways": ["先呼吸再想", "把大事拆小", "说出来就是松动"],
        "encouragement": "你不是撑不住，只是提醒得太用力。",
        "pressure_before": 7,
    }, ensure_ascii=False)

    async def fake_generate(providers, messages, **kw):
        return payload

    monkeypatch.setattr("app.director.generate", fake_generate)
    msgs = _stress_msgs_to(8)
    msgs.append({"role": "user", "content": "继续"})  # 进入第8轮·报告
    plan = director.plan_turn(msgs)
    assert plan.meta["stage"] == "report" and plan.meta.get("report_html") is True
    text, issues, attachments = await director.execute_plan(plan, [])
    assert "压力温度：开场7分" in text
    assert "📍 环节 8/8" in text
    names = [a["fileName"] for a in attachments]
    assert any(n.endswith(".html") for n in names), names
    assert any(n.endswith(".png") for n in names), names
    from app import files as file_store

    html_att = next(a for a in attachments if a["fileName"].endswith(".html"))
    token = html_att["fileUrl"].rsplit("/", 1)[-1]
    data, mime = file_store.get(token)
    assert mime == "text/html"
    page = data.decode("utf-8")
    assert "减压清单" in page and "先呼吸再想" in page
    assert "7 分" in page  # 压力温度前测块
    assert "bilibili" in page  # 练习链接
    assert "斑马" in page  # 书单
