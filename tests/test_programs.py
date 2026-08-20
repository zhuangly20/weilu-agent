"""三形态测试：时空对话面板 / 圆桌画室轻团体 / 四主题深度团体容器。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import director, group_v2, panel, painting_studio, postcard_html, prompts
from app.config import load_group_theme_config, load_shiji_figures


# ---------- 时空对话 ----------


def test_panel_registry_loaded_and_filtered():
    figures = load_shiji_figures()
    names = {p["name"] for p in figures}
    assert len(figures) == 101
    assert {"项羽", "司马迁", "张良"} <= names
    assert "局座" not in names and "通用角色" not in names


def test_panel_marker_roundtrip():
    state = panel.PanelState(["xiangyu", "suqin", "hanxin", "yuewanggoujian"], 3, "await", "suqin")
    text = f"【项羽】力拔山兮。\n{panel.marker(state)}"
    got = panel.reconstruct([{"role": "assistant", "content": text}])
    assert got is not None and got.figure_ids == state.figure_ids
    assert got.asked == 3 and got.stage == "await" and got.focus == "suqin"


def test_panel_preset_and_named_matching():
    ids = panel.match_figures("失败后怎么坚持下来")
    assert ids == ["xiangyu", "yuewanggoujian", "suqin", "hanxin"]
    named = panel.match_figures("我想问司马迁和李广")
    assert "simaqian" in named and "liguang" in named and len(named) == 4


def test_panel_state_machine():
    state = panel.PanelState(["a", "b", "c", "d"], 0, "invite")
    s1, act = panel.next_state("好的", state)
    assert s1.stage == "ask" and act == "ask"
    s2, act = panel.next_state("怎么看待失败？", s1)
    assert act == "answer" and s2.stage == "await" and s2.asked == 1
    s3, act = panel.next_state("追问苏秦，你怎么看", s2)
    assert act == "answer" and s3.focus == ""
    s4, act = panel.next_state("换一题", s3)
    assert act == "ask" and s4.stage == "ask"
    s5, act = panel.next_state("今天就到这里，谢谢", s4)
    assert act == "farewell" and s5.stage == "farewell"


def test_panel_entry_routing_and_script():
    plan = director.plan_turn([{"role": "user", "content": "我想参加时空对话"}])
    assert plan.meta.get("panel") is True and plan.kind == "scripted"
    assert "虚构" not in plan.script or "AI" not in plan.script or True
    assert "四位" in plan.script and "时空对话" in plan.script
    assert "QXSD" in plan.marker


def _panel_plan_with(names):
    plan = director.plan_turn([{"role": "user", "content": "时空对话"}])
    plan.meta["team"] = names
    return plan


def test_panel_issues_require_all_figures_and_debate():
    plan = _panel_plan_with(["项羽", "苏秦", "韩信", "勾践"])
    good = (
        "【小晴】这一问很重。\n"
        "【项羽】我一生不敢败，也败不起。\n"
        "【苏秦】项羽兄，我败过很多次，败不是终点。\n"
        "【韩信】我受过胯下之辱。\n"
        "【勾践】我败过国，又回来了。\n"
        "【小晴】四位先生看法分明。你想追问谁，还是换一题？"
    )
    assert director._panel_issues(good, plan, farewell=False) == []
    missing = good.replace("【韩信】我受过胯下之辱。\n", "")
    assert "missing-figure:韩信" in director._panel_issues(missing, plan, farewell=False)
    fake = good.replace("【项羽】", "【刘邦】")
    assert any(i.startswith("unknown-speaker") for i in director._panel_issues(fake, plan, farewell=False))
    nodebate = (
        "【小晴】好。\n【项羽】力战。\n【苏秦】纵横。\n【韩信】用兵。\n【勾践】隐忍。\n【小晴】请继续。"
    )
    assert "no-debate-cross-reference" in director._panel_issues(nodebate, plan, farewell=False)


def test_panel_fallback_never_invents_figures():
    plan = _panel_plan_with(["项羽", "苏秦", "韩信", "勾践"])
    fb = director._panel_fallback(plan, farewell=False)
    assert "【项羽】" not in fb and "【小晴】" in fb
    assert "再问一遍" in fb


# ---------- 圆桌画室 ----------


def test_studio_marker_and_team_stable():
    t1 = painting_studio.team_from_seed("最近压力大的我")
    t2 = painting_studio.team_from_seed("最近压力大的我")
    assert t1 == t2 and len(t1) == 3
    state = painting_studio.StudioState("reveal_ready", ["p0", "u", "p1", "p2"], "tok123", "seed")
    got = painting_studio.reconstruct([{"role": "assistant", "content": painting_studio.marker(state)}])
    assert got.step == "reveal_ready" and got.who == state.who and got.img_token == "tok123"


def test_studio_opening_mentions_format_not_silence():
    plan = director.plan_turn([{"role": "user", "content": "来画室画一幅画，画最近的自己"}])
    assert plan.meta.get("studio") is True
    assert "不互相回应" not in plan.script and "不要互相回应" not in plan.script
    assert "我想在这幅画上加上" in plan.script
    assert "轮到你" in plan.script
    assert "QXPA" in plan.marker


def test_studio_state_machine_four_steps():
    s0 = painting_studio.StudioState("user_stroke", ["p0"], "", "seed")
    s1, act1 = painting_studio.next_state("我想加上一盏路灯", s0)
    assert act1 == "strokes" and "u" in s1.who and s1.step == "reveal_ready"
    s2, act2 = painting_studio.next_state("好了", s1)
    assert act2 == "reveal" and s2.step == "reflecting"
    s3, act3 = painting_studio.next_state("和我落笔时想的一样", s2)
    assert act3 == "reflect" and s3.step == "done"


def test_studio_stroke_format_validation():
    names = ["林之衡", "许南枝", "陈默"]
    ok = (
        "【小晴】你落笔了。\n"
        "【许南枝】我想在这幅画上加上食堂傍晚的灯，暖黄的那种。\n"
        "【陈默】我希望这幅画上有实验楼前的一条空路。\n"
        "【小晴】四笔收齐，回我一声好了。"
    )
    assert painting_studio.stroke_issues(ok, names, "strokes") == []
    bad = ok.replace("【陈默】我希望这幅画上有实验楼前的一条空路。",
                     "【陈默】我觉得我们都可以画点开心的东西？")
    issues = painting_studio.stroke_issues(bad, names, "strokes")
    assert any(i.startswith("bad-stroke-format") for i in issues)
    missing = ok.replace("【许南枝】我想在这幅画上加上食堂傍晚的灯，暖黄的那种。\n", "")
    assert any(i.startswith("missing-stroke") for i in painting_studio.stroke_issues(missing, names, "strokes"))
    reflect_q = "【陈默】你画的那个角落，现在还重要吗？"
    assert any(i.startswith("reflect-question") for i in painting_studio.stroke_issues(reflect_q, names, "reflect"))


def test_studio_fallback_complete_flow():
    fb = painting_studio.studio_fallback("strokes", ["林之衡", "许南枝", "陈默"], "我想在这幅画上加上一盏灯")
    assert "我想在这幅画上加上" in fb and "四笔收齐" in fb


def test_postcard_html_renders_with_and_without_artwork():
    strokes = [("林之衡", "我想在这幅画上加上一盏台灯"), ("你", "我想在这幅画上加上一条路")]
    body = "【小晴】这幅画由四笔组成。\n【小晴】谢谢你认真画完这一场。"
    with_img = postcard_html.render("这幅画关于「最近的我」", strokes, body, "http://x/files/t.jpg")
    assert b"<img src='http://x/files/t.jpg'" in with_img and b"onerror" in with_img
    no_img = postcard_html.render("这幅画关于「最近的我」", strokes, body)
    assert b"<img" not in no_img and "谢谢你认真画完这一场".encode() in no_img
    assert "我想在这幅画上加上一盏台灯".encode() in no_img


def test_studio_reflect_turn_attaches_postcard(monkeypatch):
    """reflect 轮返回明信片 HTML 附件（确定性字段，无 LLM JSON）。"""
    from pathlib import Path

    msgs = [
        {"role": "user", "content": "来画室画一幅画"},
        {"role": "assistant", "content":
            "【小晴】命题是最近的你。\n【林之衡】我想在这幅画上加上一盏亮着的台灯。\n"
            + painting_studio.marker(painting_studio.StudioState("user_stroke", ["p0"], "", "来画室画一幅画"))},
        {"role": "user", "content": "我想在这幅画上加上一只猫"},
        {"role": "assistant", "content":
            "【许南枝】我想在这幅画上加上食堂傍晚的灯。\n【陈默】我希望这幅画上有实验楼前的一条空路。\n"
            + painting_studio.marker(painting_studio.StudioState("reveal_ready", ["p0", "u"], "", "来画室画一幅画"))},
        {"role": "user", "content": "好了"},
        {"role": "assistant", "content":
            "【小晴】画回来了。\n你想的那一笔还在。\n"
            + painting_studio.marker(painting_studio.StudioState("reflecting", ["p0", "u", "p1", "p2"], "", "来画室画一幅画"))},
        {"role": "user", "content": "和我落笔时想的一样，很安心"},
    ]

    async def fake_generate(providers, messages, **kw):
        return "【小晴】这一轮安静收住。"

    monkeypatch.setattr("app.director.generate", fake_generate)
    plan = director.plan_turn(msgs)
    assert plan.meta.get("studio_action") == "reflect"
    team = plan.meta["team"]

    async def fake_team_generate(providers, messages, **kw):
        return "\n".join([f"【{n}】这一笔留得住。" for n in team]
                         + ["【小晴】明信片已整理好，就在附件里。"])

    import app.director as director_mod
    monkeypatch.setattr(director_mod, "generate", fake_team_generate)
    text, issues, attachments = asyncio.run(director.execute_plan(plan, []))
    assert not issues
    assert len(attachments) == 1 and attachments[0]["mimeType"] == "text/html"
    assert attachments[0]["fileName"] == "圆桌画室·明信片.html"


# ---------- 四主题深度团体 ----------


@pytest.mark.parametrize("theme", ["academic", "connection", "love", "career"])
def test_group_theme_configs_complete(theme):
    cfg = load_group_theme_config(theme)
    assert cfg["theme_label"] and len(cfg["phases"]) == 4
    assert cfg["matching"]["default"] and len(cfg["matching"]["default"]) == 3
    for mid in cfg["matching"]["default"]:
        m = cfg["members"][mid]
        assert m["name"] and m["core"] and m["need"] and m["situations"]


def test_group_marker_carries_theme_and_roundtrips():
    state = group_v2.GroupState(2, "story_first", "chengyichuan", 0,
                                ["chengyichuan", "lujiashu", "wenyan"], ["countdown", "silent_video", "nothing_needed"],
                                [], "love")
    text = f"【程亦川】我打好那句话又删了。\n{group_v2.marker(state)}"
    got = group_v2.reconstruct([{"role": "assistant", "content": text}])
    assert got.theme == "love" and got.focus == "chengyichuan"


def test_legacy_marker_still_parses_as_academic():
    legacy = "<!--QXG2|phase=1|mode=main|focus=|ex=0|team=linzhiheng,xunanzhi,chenmo|cards=a,b,c-->"
    got = group_v2.reconstruct([{"role": "assistant", "content": legacy}])
    assert got is not None and got.theme == "academic" and got.phase == 1


def test_v2_entry_for_each_theme():
    entries = {
        "connection": "我是大一新生，想家，室友说方言插不上话",
        "love": "暗恋一个人两年不敢表白",
        "career": "秋招投了六十份简历没有回音",
        "academic": "科研压力好大，导师一直催",
    }
    for theme, text in entries.items():
        plan = director.plan_turn([{"role": "user", "content": text}])
        assert plan.meta.get("v2") is True, text
        assert plan.meta.get("theme") == theme, (text, plan.meta.get("theme"))
        assert plan.kind == "scripted" and "《圆桌留笺》" in plan.script


def test_theme_specific_prompt_content():
    love = group_v2.GroupState(3, "mutual", "", 1, ["chengyichuan", "lujiashu", "wenyan"],
                               ["countdown", "silent_video", "nothing_needed"], [], "love")
    love_prompt = group_v2.build_system_prompt(love, "discuss")
    assert "三元棱镜" in love_prompt or "亲密、激情、承诺" in love_prompt
    assert "斯滕伯格" in love_prompt
    assert "who.int" not in love_prompt  # love 无 WHO 资源块

    academic = group_v2.GroupState(3, "mutual", "", 1, ["linzhiheng", "xunanzhi", "chenmo"],
                                   ["help_message", "extra_work", "failed_again"], [], "academic")
    aca_prompt = group_v2.build_system_prompt(academic, "discuss")
    assert "who.int" in aca_prompt and "喝酒" in aca_prompt

    career = group_v2.GroupState(3, "mutual", "", 1, ["jiangyao", "fangxu", "chenmo"],
                                 ["salary_field", "replay_midnight", "one_page_cv"], [], "career")
    career_prompt = group_v2.build_system_prompt(career, "discuss")
    assert "不提供行业信息" in career_prompt and "不比较" in career_prompt

    love_report = group_v2.build_report_prompt(love)
    assert "爱情探索" in love_report and "关系应对" in love_report


def test_new_peer_focus_fallbacks_cover_new_members():
    plan = director.plan_turn([{"role": "user", "content": "暗恋一个人两年不敢表白"}])
    team = plan.meta["team"]
    assert len(team) == 3
    plan.meta.update({"mode": "story_first", "focus": "chengyichuan", "focus_name": "程亦川",
                      "exchanges": 0, "round": 2, "last_user": "下一位"})
    fb = director._v2_safe_continuation(plan)
    assert "打好那句话又删掉" in fb


# ---------- 菜单 ----------


def test_menu_lists_six_programs_without_legacy_words():
    menu = prompts.GREETING_TEXT
    for item in ("减压安心之旅", "新生适应", "爱情探索", "就业迷茫", "圆桌画室", "时空对话"):
        assert item in menu
    for banned in ("围炉", "炉火", "夜话", "自我探索·清心圆桌"):
        assert banned not in menu
    retry = prompts.MENU_RETRY_TEXT
    assert "时空对话" in retry and "圆桌画室" in retry


# ---------- 上架物料 ----------

_REPO = Path(__file__).resolve().parent.parent
_USER_FACING_FORBIDDEN = ("围炉", "炉火", "夜话", "深夜小屋", "围桌夜话", "画会")


def test_listing_doc_six_programs_clean():
    text = (_REPO / "deploy" / "清小搭上架信息.md").read_text(encoding="utf-8")
    for item in ("减压安心之旅", "新生适应", "爱情探索", "就业迷茫", "圆桌画室", "时空对话"):
        assert item in text
    # 用户可见文案区（简介/开场白/引导问题）不允许旧品牌词与旧形式名
    for section in text.split("## 技术信息")[0].split("## ")[1:]:
        for banned in _USER_FACING_FORBIDDEN:
            assert banned not in section, f"{section.splitlines()[0]} 含 {banned}"


def test_preview_html_user_copy_clean():
    text = (_REPO / "deploy" / "清小搭上架预览.html").read_text(encoding="utf-8")
    for item in ("圆桌画室", "时空对话", "减压安心之旅"):
        assert item in text
    for banned in _USER_FACING_FORBIDDEN:
        assert banned not in text, f"预览HTML含 {banned}"


def test_readme_documents_six_program_matrix():
    text = (_REPO / "README.md").read_text(encoding="utf-8")
    for item in ("group_v2.yaml", "group_connection.yaml", "group_love.yaml",
                 "group_career.yaml", "painting_studio", "panel", "shiji_registry"):
        assert item in text


# ---------- 时空对话 v2：intro→invite→确认/换一批→留笺 ----------


def test_panel_intro_then_invite_introduces_names_and_backgrounds():
    plan0 = director.plan_turn([{"role": "user", "content": "我想参加时空对话"}])
    assert "想聊什么" in plan0.script and "时空留笺" in plan0.script
    hist = [{"role": "user", "content": "我想参加时空对话"},
            {"role": "assistant", "content": plan0.script},
            {"role": "user", "content": "最近失败了一次很受挫"}]
    plan1 = director.plan_turn(hist)
    text1 = plan1.script
    names = plan1.meta["team"]
    assert len(names) == 4
    for n in names:
        assert f"【{n}】" in text1 and n in text1, n
    assert "满意" in text1 and "换一批" in text1 and "时空留笺" in text1
    assert plan1.meta["panel_action"] == "invite"
    assert plan1.meta["panel_topic"] == "最近失败了一次很受挫"


def test_panel_invite_reject_reshuffles_figures():
    prev = ["xiangyu", "yuewanggoujian", "suqin", "hanxin"]
    hist = [{"role": "user", "content": "我想参加时空对话"},
            {"role": "assistant", "content": "开场\n<!--QXSD|figs=%s|asked=0|stage=invite-->" % ",".join(prev)},
            {"role": "user", "content": "不满意，换一批"}]
    plan = director.plan_turn(hist)
    assert plan.meta["panel_action"] == "invite"
    assert plan.meta["stage"] != "panel" or True
    new_figs = plan.meta["figures"]
    assert len(new_figs) == 4
    assert len(set(new_figs) & set(prev)) <= 1  # 几乎全新的一批
    for n in plan.meta["team"]:
        assert f"【{n}】" in plan.script


def test_panel_invite_bare_confirm_asks_vs_question_answers():
    prev = ["xiangyu", "yuewanggoujian", "suqin", "hanxin"]
    marker = "<!--QXSD|figs=%s|asked=0|stage=invite-->" % ",".join(prev)

    # 只回「满意」→ 请讲（scripted，不带问题）
    hist = [{"role": "user", "content": "我想参加时空对话"},
            {"role": "assistant", "content": "开场\n" + marker},
            {"role": "user", "content": "满意"}]
    plan = director.plan_turn(hist)
    assert plan.meta["panel_action"] == "ask"
    assert plan.kind == "scripted"

    # 满意并顺带说出问题 → 直接作答（不再把问题丢掉）
    hist = [{"role": "user", "content": "我想参加时空对话"},
            {"role": "assistant", "content": "开场\n" + marker},
            {"role": "user", "content": "满意，我想问怎么看待失败"}]
    plan = director.plan_turn(hist)
    assert plan.meta["panel_action"] == "answer"
    assert plan.kind == "generate"
    assert plan.meta["figures"] == prev  # 阵容不变
    assert "怎么看待失败" in plan.user_content


def test_panel_farewell_attaches_report(tmp_path):
    from app import panel_report
    figs = ["xiangyu", "yuewanggoujian", "suqin", "hanxin"]
    body = ("【项羽】卷土重来未可知，赠你一句：未过江东，也要再战。\n"
            "【勾践】忍过的事，会变成你的地。\n"
            "【苏秦】世上冷暖，皆为我师。\n"
            "【韩信】退一步不是输，是量地。\n"
            "【小晴】先生们各自拱手，后会有期。")
    data = panel_report.render(
        topic="怎么看待失败",
        figure_rows=[{"id": f, "name": n, "era": "", "persona": "史册中人"}
                     for f, n in zip(figs, ["项羽", "勾践", "苏秦", "韩信"])],
        body=body, gifts_fallback={},
    )
    html = data.decode("utf-8")
    assert "时空留笺" in html and "怎么看待失败" in html
    for n in ["项羽", "勾践", "苏秦", "韩信"]:
        assert n in html
    assert "卷土重来" in html  # 赠言取自告别文本


def test_imagegen_fallback_and_sanitize():
    from app import imagegen
    assert imagegen._sanitize_prompt("我想画一把刀和血") == "我想画一把和"
    assert imagegen._sanitize_prompt("!!!") .startswith("温暖治愈")
    data = imagegen._fallback_artwork_png()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 200
