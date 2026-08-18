"""导演编排器：8轮制流程（相邀→开场→6轮正式）+ 换人 + 组队 + 历史压缩。

plan_turn() 是纯函数（可测试），返回本轮计划；
执行（LLM调用/流式/生图/明信片）由 main.py 按 plan 进行。
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from . import prompts, safety
from .config import ProviderConfig, load_characters, load_settings, load_theme_config
from .llm import generate, stream_generate
from .session import (
    ENDED,
    FORM_CHAT,
    FORM_PAINTING,
    GREETING,
    MARKER_RE,
    MAX_SWAPS,
    SLOT_KEYS,
    STAGE_BY_ROUND,
    STAGE_BY_ROUND_PAINTING,
    TOTAL_ROUNDS,
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

GREETING_MIN_LEN = 8  # 低于此长度的开场（如"你好"）走菜单，实质性倾诉直接进相邀

# 每轮期望的最少成员发言数（校验用；不达标触发非流式重试）
STAGE_MIN_MEMBERS = {
    "invite": 4, "ignite": 4, "share": 2, "depth": 2,
    "persp": 2, "heart": 4, "close": 4, "report": 0,
}
STAGE_MIN_MEMBERS_PAINTING = {
    "invite": 4, "ignite": 2, "strokes": 4, "reveal": 0,
    "resonance": 3, "meaning": 2, "heart": 4, "report": 0,
}

# 从历史发言里识别"添笔"句式（成员+小晴的笔触）
STROKE_LINE_RE = None  # 延迟初始化，见 extract_strokes


def progress_line(round_no: int, stage_label: str) -> str:
    """用户可见的环节进度行（代码追加，格式固定）。"""
    if round_no >= TOTAL_ROUNDS:
        return f"📍 环节 {round_no}/{TOTAL_ROUNDS} · {stage_label}｜本场最后一环"
    return f"📍 环节 {round_no}/{TOTAL_ROUNDS} · {stage_label}｜还剩{TOTAL_ROUNDS - round_no}个环节"


def _finalize_body(plan: "TurnPlan", body: str) -> str:
    """统一收尾：开场白前置、标记行/进度行/资源卡片追加（仅拼接，不改内容）。"""
    if plan.meta.get("warm_opening"):
        body = plan.meta["warm_opening"].strip() + "\n\n" + body
    parts = [body]
    if plan.marker:
        parts.append(plan.marker)
        parts.append(progress_line(int(plan.meta.get("round") or 1),
                                   str(plan.meta.get("stage_label") or "环节")))
    if plan.meta.get("resource_card_text"):
        parts.append(plan.meta["resource_card_text"])
    return "\n\n".join(p for p in parts if p.strip())


def _parse_report_json(text: str) -> dict | None:
    """解析报告 JSON（容忍 markdown 代码围栏）；结构不对返回 None。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    try:
        obj = json.loads(t)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    obj.setdefault("leader_note", "")
    obj.setdefault("pressure_note", "")
    obj.setdefault("review", [])
    obj.setdefault("member_tips", [])
    obj.setdefault("takeaways", [])
    obj.setdefault("encouragement", "")
    obj.setdefault("pressure_before", None)
    return obj


@dataclass
class TurnPlan:
    kind: str  # "scripted" | "generate"
    script: str = ""  # scripted 时的完整文本（含标记）
    system_prompt: str = ""
    user_content: str = ""
    marker: str = ""  # generate 完成后追加的标记行
    meta: dict[str, Any] = field(default_factory=dict)


def _resolve_theme_by_label(label: str, themes: list[dict]) -> dict | None:
    for t in themes:
        if label in (t.get("label"), t.get("full_label")):
            return t
    return None


def _team_from_ids(ids: list[str], characters: dict) -> list[dict]:
    team = [characters[i] for i in ids if i in characters]
    if len(team) != 4:
        return []
    return team


def build_digest(messages: list[dict], char_cap: int = 2600) -> str:
    """历史压缩：每条消息截断、assistant 只留发言行骨架，总量封顶。"""
    entries: list[str] = []
    for msg in messages:
        role = msg.get("role")
        text = extract_text(msg.get("content")).strip()
        if not text or role not in ("user", "assistant"):
            continue
        if role == "user":
            entries.append(f"同学：{text[:200]}")
        else:
            body = MARKER_RE.sub("", text).strip()
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()][:8]
            snippet = " / ".join(ln[:90] for ln in lines)
            entries.append(f"团体：{snippet[:600]}")
    kept: list[str] = []
    total = 0
    for entry in reversed(entries):
        if total + len(entry) > char_cap and kept:
            break
        kept.append(entry)
        total += len(entry)
    return "\n".join(reversed(kept))


def extract_strokes(messages: list[dict]) -> tuple[list[str], str]:
    """收集历史里所有「我希望画上有…」式笔触 + 用户最新一笔。"""
    import re

    global STROKE_LINE_RE
    if STROKE_LINE_RE is None:
        STROKE_LINE_RE = re.compile(
            r"【[^】]+】\s*((?:我希望画上有|我想在这幅画上加上|我想加上|我想画上|我添上)[^【\n]*)"
        )
    contributions: list[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        text = extract_text(msg.get("content"))
        for m in STROKE_LINE_RE.finditer(text):
            s = m.group(1).strip()
            if s:
                contributions.append(s)
    user_stroke = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_stroke = extract_text(msg.get("content")).strip()
            break
    return contributions, user_stroke


# ---------- 附件（明信片 / 画会画作） ----------


def build_attachments(plan: TurnPlan, body: str) -> list[dict]:
    """report 轮：解析手记 → 渲染明信片 PNG → 返回 attachments 列表。"""
    if plan.meta.get("stage") != "report":
        return []
    try:
        from . import files, postcard

        parsed = postcard.parse_handnote(body)
        theme_label = str(plan.meta.get("theme_label") or parsed.get("theme") or "清心圆桌")
        png = postcard.render_postcard(
            theme_label=theme_label,
            message=parsed["message"],
            takeaways=parsed["takeaways"],
            member_names=list(plan.meta.get("team", [])),
        )
        token = files.put(png)
        base = load_settings().public_base_url
        return [
            {
                "fileUrl": f"{base}/files/{token}",
                "fileName": "清心圆桌·明信片.png",
                "fileType": "image",
                "mimeType": "image/png",
                "fileSize": len(png),
            }
        ]
    except Exception:  # noqa: BLE001 - 明信片失败绝不能打断对话
        return []


def _painting_prompt_for(plan: TurnPlan, messages: list[dict]) -> str | None:
    """为画会揭晓轮组装生图prompt（笔触从历史解析，无状态设计）。"""
    if plan.meta.get("form") != FORM_PAINTING or plan.meta.get("stage") != "reveal":
        return None
    from . import imagegen

    themes = {t["id"]: t for t in load_theme_config()["themes"]}
    theme = themes.get(str(plan.meta.get("theme")), {})
    framing = str((theme.get("painting") or {}).get("framing") or "一群人共同创作的画")
    contributions, user_stroke = extract_strokes(messages)
    return imagegen.build_painting_prompt(framing, contributions, user_stroke)


def _image_attachment(img_bytes: bytes) -> list[dict]:
    from . import files

    token = files.put(img_bytes, mime="image/jpeg", ext="jpg")
    base = load_settings().public_base_url
    return [
        {
            "fileUrl": f"{base}/files/{token}",
            "fileName": "圆桌画会·共创画作.jpg",
            "fileType": "image",
            "mimeType": "image/jpeg",
            "fileSize": len(img_bytes),
        }
    ]


# ---------- 计划（纯函数） ----------


def plan_turn(messages: list[dict]) -> TurnPlan:
    themes_cfg = load_theme_config()
    themes: list[dict] = themes_cfg["themes"]
    leader_cfg: dict = themes_cfg["leader"]
    characters = load_characters()

    user_texts = [
        extract_text(m.get("content"))
        for m in messages
        if m.get("role") == "user" and extract_text(m.get("content")).strip()
    ]
    last_user = user_texts[-1] if user_texts else ""
    seed_text = user_texts[0] if user_texts else ""

    state = reconstruct(messages)

    # 危机检测（high 立即脚本化响应，不推进状态）
    crisis = safety.detect(last_user)
    if crisis == "high":
        return TurnPlan(kind="scripted", script=safety.aid_reply(),
                        meta={"stage": state.stage, "crisis": "high"})

    if state.stage == GREETING:
        form = detect_form(last_user)
        theme = detect_theme(last_user, themes)
        if theme is None and (len(last_user.strip()) >= GREETING_MIN_LEN or form == FORM_PAINTING):
            theme = themes[0]
        if theme is None:
            has_history = any(m.get("role") == "assistant" for m in messages)
            return TurnPlan(
                kind="scripted",
                script=prompts.MENU_RETRY_TEXT if has_history else prompts.GREETING_TEXT,
                meta={"stage": GREETING, "crisis": crisis},
            )
        return _invite_plan(messages, last_user, theme, form, leader_cfg, characters, seed_text, crisis)

    if state.stage == ENDED:
        if wants_reset(last_user):
            return TurnPlan(kind="scripted", script=prompts.GREETING_TEXT,
                            meta={"stage": GREETING, "reset": True})
        # 散场后的追问：小晴单独应答（无标记 → 状态保持 ENDED）
        theme_label = state.theme_label_raw or "清心圆桌"
        digest = build_digest(messages[:-1])
        return TurnPlan(
            kind="generate",
            system_prompt=prompts.build_encore_system_prompt(leader_cfg, theme_label),
            user_content=prompts.build_turn_user_content(digest, last_user),
            marker="",
            meta={"stage": "encore", "team": [], "form": state.form},
        )

    theme = _resolve_theme_by_label(state.theme_label_raw, themes)
    if theme is None:
        return TurnPlan(kind="scripted", script=prompts.MENU_RETRY_TEXT,
                        meta={"stage": state.stage, "fallback": "theme-lost"})

    # 队伍：优先取标记里携带的ID（换人后阵容），缺失则按seed重建
    team = _team_from_ids(state.team_ids, characters)
    if not team:
        team = build_team(theme, characters, f"{seed_text}|{theme['id']}")
    team_ids = [m["id"] for m in team]

    # ---- 第2轮·定席分支（换人 / 确认开场 / 没听清） ----
    if state.stage == "ignite":
        swap_slots = parse_swap_request(last_user, [m["name"] for m in team])
        swaps_used = state.team_variants - 1
        if swap_slots:
            remaining = MAX_SWAPS - swaps_used
            if remaining <= 0:
                return TurnPlan(kind="scripted", script=prompts.NO_MORE_SWAP_TEXT,
                                meta={"stage": "seat", "swaps_used": swaps_used})
            # 一次最多换到剩余额度；告知LLM对被裁剪的请求做说明
            applied = swap_slots[:remaining]
            truncated = len(swap_slots) > remaining
            departing, arriving, new_ids = [], [], list(team_ids)
            for slot in applied:
                new_id = swap_member(theme, characters, new_ids, slot)
                if new_id is None:
                    continue
                departing.append(team[slot])
                arriving.append(characters[new_id])
                new_ids[slot] = new_id
            if not arriving:
                return TurnPlan(kind="scripted", script=prompts.NO_MORE_SWAP_TEXT,
                                meta={"stage": "seat", "swaps_used": swaps_used})
            note = ""
            if truncated:
                note = f"（注意：同学想换的人较多，但每场最多换{MAX_SWAPS}位，本轮只换了前{len(arriving)}位，小晴要俏皮地说明这一点）"
            system_prompt = prompts.build_swap_system_prompt(
                leader_cfg, team, departing, arriving, note=note
            )
            marker = make_marker(1, "相邀", theme["label"], state.form, new_ids)
            digest = build_digest(messages[:-1])
            extra = safety.medium_empathy_instruction() if crisis == "medium" else ""
            user_content = prompts.build_turn_user_content(digest, last_user, extra)
            return TurnPlan(
                kind="generate", system_prompt=system_prompt, user_content=user_content,
                marker=marker,
                meta={"stage": "seat", "round": 1, "theme": theme["id"], "theme_label": theme["label"],
                      "form": state.form, "team": [characters[i]["name"] for i in new_ids if i in characters],
                      "departing": [m["name"] for m in departing], "arriving": [m["name"] for m in arriving],
                      "stage_label": "相邀", "crisis": crisis},
            )
        if is_confirm(last_user) or len(last_user.strip()) >= GREETING_MIN_LEN:
            # 明确确认，或实质性发言（视为默许阵容、把话接进开场）
            label_cfg = themes_cfg.get("painting_stages", {}) if state.form == FORM_PAINTING else themes_cfg["stages"]
            marker = make_marker(2, label_cfg["ignite"]["label"], theme["label"], state.form, team_ids)
            system_prompt = prompts.build_turn_system_prompt(
                leader_cfg, team, "ignite", theme, themes_cfg["stages"], 2,
                form=state.form, painting_stages_cfg=themes_cfg.get("painting_stages"),
            )
            digest = build_digest(messages[:-1])
            extra = safety.medium_empathy_instruction() if crisis == "medium" else ""
            extra = "\n".join(x for x in (extra, _interaction_note(last_user, team, at_seat=True)) if x)
            user_content = prompts.build_turn_user_content(digest, last_user, extra)
            return TurnPlan(
                kind="generate", system_prompt=system_prompt, user_content=user_content,
                marker=marker,
                meta={"stage": "ignite", "round": 2, "theme": theme["id"], "theme_label": theme["label"],
                      "form": state.form, "team": [m["name"] for m in team], "crisis": crisis},
            )
        # 没听清：轻量重问，不推进状态
        return TurnPlan(kind="scripted", script=prompts.SEAT_REASK_TEXT,
                        meta={"stage": "seat", "crisis": crisis})

    # ---- 第3-8轮：正式流程 ----
    form = state.form
    table = STAGE_BY_ROUND_PAINTING if form == FORM_PAINTING else STAGE_BY_ROUND
    round_no = state.next_round
    stage_id = table[round_no]
    label_cfg = themes_cfg.get("painting_stages", {}) if form == FORM_PAINTING else themes_cfg["stages"]
    marker = make_marker(round_no, label_cfg[stage_id]["label"], theme["label"], form, team_ids)

    if stage_id == "report":
        system_prompt = prompts.build_report_system_prompt(leader_cfg, theme)
        digest = build_digest(messages)
        user_content = "【本场完整对话记录（摘要）】\n" + digest + "\n\n请生成《成长手记》。"
    else:
        system_prompt = prompts.build_turn_system_prompt(
            leader_cfg, team, stage_id, theme, themes_cfg["stages"], round_no,
            form=form, painting_stages_cfg=themes_cfg.get("painting_stages"),
        )
        digest = build_digest(messages[:-1])
        extra = safety.medium_empathy_instruction() if crisis == "medium" else ""
        extra = "\n".join(x for x in (extra, _interaction_note(last_user, team)) if x)
        user_content = prompts.build_turn_user_content(digest, last_user, extra)

    plan = TurnPlan(
        kind="generate", system_prompt=system_prompt, user_content=user_content,
        marker=marker,
        meta={"stage": stage_id, "round": round_no, "theme": theme["id"], "theme_label": theme["label"],
              "form": form, "team": [m["name"] for m in team], "stage_label": label_cfg[stage_id]["label"],
              "crisis": crisis},
    )
    if form == FORM_CHAT and stage_id == "persp" and theme.get("persp_variant") == "breathing":
        plan.meta["resource_card_text"] = prompts.build_resource_card(
            themes_cfg.get("resources") or {}, load_settings().public_base_url
        )
        plan.meta["slow_pacing"] = True
    if stage_id == "report" and theme.get("report_variant") == "html":
        plan.meta["report_html"] = True
    pp = _painting_prompt_for(plan, messages)
    if pp:
        plan.meta["painting_prompt"] = pp
    return plan


def _interaction_note(last_user: str, team: list[dict], at_seat: bool = False) -> str:
    """从用户发言识别点名/没说完/想要某类桌友 → 返回附加指令（空串=无）。"""
    notes: list[str] = []
    if not last_user:
        return ""
    named = [m["name"] for m in team if m["name"] and m["name"] in last_user]
    if named:
        names = "、".join(named)
        notes.append(
            f"【特别指令】同学刚点名了{names}——本轮{names}第一个发言："
            "先正面回应同学刚才的话（如果同学提了问题，先回答问题，"
            "可以温和地说自己的真实感受，但不评判带领者和其他成员），"
            "再分享自己的相关经历；小晴开头一句轻轻把话头递过去。"
        )
    if re.search(r"还没(说|讲)完|还没结束|我还想(说|聊)|我还没", last_user):
        notes.append(
            "【特别指令】同学表示还没说完——小晴本轮不要推进新环节，"
            "先把说话空间还给同学（\"你想说的我们都想听\"），"
            "成员只做一两句简短回应，不抢话、不总结。"
        )
    if at_seat and ("想要" in last_user or "有没有" in last_user) and re.search(
        r"同学|朋友|伙伴|桌友|学长|学姐", last_user
    ):
        notes.append(
            "【特别指令】同学表达了想和某类桌友同坐（见原话）。"
            "请对照在场成员的人物卡：如果已有符合的成员，小晴要明确点出来"
            "（例如\"你要的博士生同学，陈默就是呀\"）；如果没有，"
            "小晴主动提出可以换人（\"想换哪位跟我说一声\"）。这个请求不能被忽略。"
        )
    return "\n".join(notes)


def _invite_plan(
    messages: list[dict], last_user: str, theme: dict, form: str,
    leader_cfg: dict, characters: dict, seed_text: str, crisis: str | None,
) -> TurnPlan:
    """第1轮·相邀：方案介绍 + 四位团友各自亮相 + 换人询问。"""
    team = build_team(theme, characters, f"{seed_text}|{theme['id']}")
    team_ids = [m["id"] for m in team]
    marker = make_marker(1, "相邀", theme["label"], form, team_ids)
    system_prompt = prompts.build_invite_system_prompt(leader_cfg, team, theme, form)
    digest = build_digest(messages[:-1]) if len(messages) > 1 else ""
    extra = safety.medium_empathy_instruction() if crisis == "medium" else ""
    user_content = prompts.build_turn_user_content(digest, last_user, extra)
    return TurnPlan(
        kind="generate", system_prompt=system_prompt, user_content=user_content,
        marker=marker,
        meta={"stage": "invite", "round": 1, "theme": theme["id"], "theme_label": theme["label"],
              "form": form, "team": [m["name"] for m in team], "stage_label": "相邀",
              "warm_opening": str(theme.get("warm_opening") or "").strip() or None,
              "invite_members_silent": theme.get("intro_style") == "leader_brief" or None,
              "crisis": crisis},
    )


# ---------- 执行 ----------


async def _legacy_report(plan: TurnPlan, providers: list[ProviderConfig]) -> str:
    """JSON 报告解析失败时的兜底：退回旧版文本成长手记。"""
    themes_cfg = load_theme_config()
    theme = next(
        (t for t in themes_cfg["themes"] if t["id"] == plan.meta.get("theme")),
        themes_cfg["themes"][0],
    )
    system = prompts.build_report_system_prompt(themes_cfg["leader"], {**theme, "report_variant": None})
    return await generate(
        providers,
        [{"role": "system", "content": system}, {"role": "user", "content": plan.user_content}],
        temperature=0.7, max_tokens=1200,
    )


async def _generate_report_fields(
    plan: TurnPlan, providers: list[ProviderConfig]
) -> tuple[str, dict | None]:
    """HTML 报告模式：让 LLM 输出 JSON 素材；两次解析失败退回文本手记。"""
    llm_messages = [
        {"role": "system", "content": plan.system_prompt},
        {"role": "user", "content": plan.user_content},
    ]
    try:
        for temp in (0.6, 0.3):
            text = await generate(providers, llm_messages, temperature=temp, max_tokens=900)
            fields = _parse_report_json(text)
            if fields is not None:
                return text, fields
    except Exception:
        pass
    return await _legacy_report(plan, providers), None


def _report_display_text(fields: dict) -> str:
    lines = [
        "【小晴】（把一份小报告轻轻放到你手边）"
        + str(fields.get("leader_note") or "这是你这一场的收获——")
    ]
    pb = fields.get("pressure_before")
    if isinstance(pb, (int, float)) and not isinstance(pb, bool) and 0 <= pb <= 10:
        lines.append(f"🌡 压力温度：开场{int(pb)}分 → 现在，你想给自己打几分？")
    lines.append("📎 你的《成长报告》和明信片就在下面的附件卡片里，点开就能看～")
    return "\n".join(lines)


def _report_attachments(plan: TurnPlan, fields: dict) -> list[dict]:
    """HTML 成长报告（长时效 token）+ 明信片 PNG。"""
    from . import files, postcard, report_html

    theme_label = str(plan.meta.get("theme_label") or "清心圆桌")
    member_names = list(plan.meta.get("team", []))
    themes_cfg = load_theme_config()
    settings = load_settings()
    attachments: list[dict] = []
    try:
        html = report_html.render(
            fields, theme_label=theme_label, member_names=member_names,
            resources=themes_cfg.get("resources") or {}, public_base=settings.public_base_url,
        )
        token = files.put(html, mime="text/html", ext="html", ttl=24 * 3600)
        attachments.append({
            "fileUrl": f"{settings.public_base_url}/files/{token}",
            "fileName": "清心圆桌·成长报告.html",
            "fileType": "file",
            "mimeType": "text/html",
            "fileSize": len(html),
        })
    except Exception:  # noqa: BLE001 - 报告失败不打断对话
        pass
    try:
        takeaways = [str(t)[:20] for t in (fields.get("takeaways") or [])][:3] or ["慢一点，也没关系。"]
        message = str(fields.get("encouragement") or "")[:40] or "把压力说出来，就是照顾自己的开始。"
        png = postcard.render_postcard(
            theme_label=theme_label, message=message,
            takeaways=takeaways, member_names=member_names,
        )
        token = files.put(png)
        attachments.append({
            "fileUrl": f"{settings.public_base_url}/files/{token}",
            "fileName": "清心圆桌·明信片.png",
            "fileType": "image",
            "mimeType": "image/png",
            "fileSize": len(png),
        })
    except Exception:  # noqa: BLE001
        pass
    return attachments


async def execute_plan(
    plan: TurnPlan, providers: list[ProviderConfig]
) -> tuple[str, list[str], list[dict]]:
    """非流式执行：返回 (完整文本, 校验问题列表, 附件列表)。校验失败重试一次。"""
    if plan.kind == "scripted":
        return plan.script, [], []
    from . import imagegen

    allowed = [prompts.LEADER_NAME] + list(plan.meta.get("team", []))
    min_map = STAGE_MIN_MEMBERS_PAINTING if plan.meta.get("form") == FORM_PAINTING else STAGE_MIN_MEMBERS
    min_members = min_map.get(str(plan.meta.get("stage")), 0)
    if str(plan.meta.get("stage")) == "seat":  # 换人轮：道别成员也可发言
        allowed += list(plan.meta.get("departing", []))
        min_members = 1
    if plan.meta.get("invite_members_silent"):  # 相邀轮小晴代介绍：成员不发言
        min_members = 0
    llm_messages = [
        {"role": "system", "content": plan.system_prompt},
        {"role": "user", "content": plan.user_content},
    ]
    img_task = None
    if plan.meta.get("painting_prompt"):
        img_task = asyncio.create_task(imagegen.generate_painting(plan.meta["painting_prompt"]))
    if plan.meta.get("report_html"):
        text, fields = await _generate_report_fields(plan, providers)
        if fields is not None:
            body = _report_display_text(fields)
            attachments = _report_attachments(plan, fields)
            return _finalize_body(plan, body), [], attachments
        # JSON 解析失败 → text 已是旧版文本手记，走下方通用流程
        body = text.strip()
        attachments = build_attachments(plan, body)
        return _finalize_body(plan, body), [], attachments
    text = await generate(providers, llm_messages, temperature=0.85, max_tokens=1200)
    issues = prompts.validate_turn(text, allowed, min_members)
    if issues and plan.meta.get("stage") != "report":
        retry = await generate(providers, llm_messages, temperature=0.7, max_tokens=1200)
        retry_issues = prompts.validate_turn(retry, allowed, min_members)
        if len(retry_issues) < len(issues):
            text, issues = retry, retry_issues
    body = text.strip()
    attachments: list[dict] = []
    if img_task is not None:
        img = await img_task
        if img:
            attachments.extend(_image_attachment(img))
    attachments.extend(build_attachments(plan, body))
    return _finalize_body(plan, body), issues, attachments


async def stream_plan(
    plan: TurnPlan, providers: list[ProviderConfig]
) -> AsyncIterator[tuple[str, str]]:
    """流式执行：yield ("delta", text) 增量；结尾 yield ("final", 完整文本含标记)。

    增量经 pacer 重放：发言行逐字、行间停顿，呈现"成员一个个发言"的节奏；
    呼吸站轮使用更慢的节奏参数。生成失败时 yield ("final", 降级文案)。
    """
    from .pacer import paced

    if plan.kind == "scripted":

        async def script_source():
            for ch in _chunk(plan.script, 16):
                yield ch

        async for delta in paced(script_source(), scripted=True):
            yield "delta", delta
        yield "final", plan.script
        return

    from . import imagegen

    async def paced_text(text: str):
        async def src():
            for ch in _chunk(text, 16):
                yield ch

        async for d in paced(src(), scripted=True):
            yield d

    llm_messages = [
        {"role": "system", "content": plan.system_prompt},
        {"role": "user", "content": plan.user_content},
    ]
    img_task = None
    if plan.meta.get("painting_prompt"):
        img_task = asyncio.create_task(imagegen.generate_painting(plan.meta["painting_prompt"]))

    if plan.meta.get("report_html"):
        text, fields = await _generate_report_fields(plan, providers)
        display = _report_display_text(fields) if fields is not None else text.strip()
        async for delta in paced_text(display):
            yield "delta", delta
        attachments = (
            _report_attachments(plan, fields) if fields is not None
            else build_attachments(plan, display)
        )
        if attachments:
            yield "attachments", attachments
        final = _finalize_body(plan, display)
        if len(final) > len(display):
            yield "delta", final[len(display):]
        yield "final", final
        return

    streamed_prefix = ""
    if plan.meta.get("warm_opening"):
        streamed_prefix = plan.meta["warm_opening"].strip() + "\n\n"
        async for delta in paced_text(streamed_prefix):
            yield "delta", delta

    pace_kwargs = {}
    if plan.meta.get("slow_pacing"):  # 呼吸站：字更慢、发言行间停顿更长
        pace_kwargs = {"char_ms": 55.0, "pause_ms": 4200.0}
    collected: list[str] = []
    try:
        async for delta in paced(
            stream_generate(providers, llm_messages, temperature=0.85, max_tokens=1200),
            **pace_kwargs,
        ):
            collected.append(delta)
            yield "delta", delta
    except Exception:
        if img_task is not None:
            img_task.cancel()
        if not collected:
            async for delta in paced_text(prompts.LLM_FALLBACK_TEXT):
                yield "delta", delta
            yield "final", prompts.LLM_FALLBACK_TEXT
            return
    body = "".join(collected).strip()
    attachments: list[dict] = []
    if img_task is not None:
        img = await img_task
        if img:
            attachments.extend(_image_attachment(img))
    attachments.extend(build_attachments(plan, body))
    if attachments:
        yield "attachments", attachments
    final = _finalize_body(plan, body)
    tail = final[len(streamed_prefix):] if final.startswith(streamed_prefix) else final
    if len(tail) > len(body):
        yield "delta", tail[len(body):]
    yield "final", final


def _chunk(text: str, size: int = 24) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]
