"""导演编排器：8轮制流程（相邀→开炉→6轮正式）+ 换人 + 组队 + 历史压缩。

plan_turn() 是纯函数（可测试），返回本轮计划；
执行（LLM调用/流式/生图/明信片）由 main.py 按 plan 进行。
"""
from __future__ import annotations

import asyncio
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
    "invite": 0, "ignite": 2, "share": 2, "depth": 2,
    "persp": 2, "heart": 4, "close": 4, "report": 0,
}
STAGE_MIN_MEMBERS_PAINTING = {
    "invite": 0, "ignite": 0, "strokes": 4, "reveal": 0,
    "resonance": 3, "meaning": 2, "heart": 4, "report": 0,
}

# 从历史发言里识别"添笔"句式（成员+小晴的笔触）
STROKE_LINE_RE = None  # 延迟初始化，见 extract_strokes


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
        theme_label = str(plan.meta.get("theme_label") or parsed.get("theme") or "围炉夜话")
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
                "fileName": "围炉夜话·明信片.png",
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
            "fileName": "围炉画会·共创画作.jpg",
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
        return TurnPlan(kind="scripted", script=prompts.ENDED_TEXT,
                        meta={"stage": ENDED})

    theme = _resolve_theme_by_label(state.theme_label_raw, themes)
    if theme is None:
        return TurnPlan(kind="scripted", script=prompts.MENU_RETRY_TEXT,
                        meta={"stage": state.stage, "fallback": "theme-lost"})

    # 队伍：优先取标记里携带的ID（换人后阵容），缺失则按seed重建
    team = _team_from_ids(state.team_ids, characters)
    if not team:
        team = build_team(theme, characters, f"{seed_text}|{theme['id']}")
    team_ids = [m["id"] for m in team]

    # ---- 第2轮·定席分支（换人 / 确认开炉 / 没听清） ----
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
                      "crisis": crisis},
            )
        if is_confirm(last_user) or len(last_user.strip()) >= GREETING_MIN_LEN:
            # 明确确认，或实质性发言（视为默许阵容、把话接进开炉）
            label_cfg = themes_cfg.get("painting_stages", {}) if state.form == FORM_PAINTING else themes_cfg["stages"]
            marker = make_marker(2, label_cfg["ignite"]["label"], theme["label"], state.form, team_ids)
            system_prompt = prompts.build_turn_system_prompt(
                leader_cfg, team, "ignite", theme, themes_cfg["stages"], 2,
                form=state.form, painting_stages_cfg=themes_cfg.get("painting_stages"),
            )
            digest = build_digest(messages[:-1])
            extra = safety.medium_empathy_instruction() if crisis == "medium" else ""
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
        user_content = "【今晚完整对话记录（摘要）】\n" + digest + "\n\n请生成《成长手记》。"
    else:
        system_prompt = prompts.build_turn_system_prompt(
            leader_cfg, team, stage_id, theme, themes_cfg["stages"], round_no,
            form=form, painting_stages_cfg=themes_cfg.get("painting_stages"),
        )
        digest = build_digest(messages[:-1])
        extra = safety.medium_empathy_instruction() if crisis == "medium" else ""
        user_content = prompts.build_turn_user_content(digest, last_user, extra)

    plan = TurnPlan(
        kind="generate", system_prompt=system_prompt, user_content=user_content,
        marker=marker,
        meta={"stage": stage_id, "round": round_no, "theme": theme["id"], "theme_label": theme["label"],
              "form": form, "team": [m["name"] for m in team], "crisis": crisis},
    )
    pp = _painting_prompt_for(plan, messages)
    if pp:
        plan.meta["painting_prompt"] = pp
    return plan


def _invite_plan(
    messages: list[dict], last_user: str, theme: dict, form: str,
    leader_cfg: dict, characters: dict, seed_text: str, crisis: str | None,
) -> TurnPlan:
    """第1轮·相邀：方案介绍 + 团友亮相 + 换人询问（只有小晴发言）。"""
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
              "form": form, "team": [m["name"] for m in team], "crisis": crisis},
    )


# ---------- 执行 ----------


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
    llm_messages = [
        {"role": "system", "content": plan.system_prompt},
        {"role": "user", "content": plan.user_content},
    ]
    img_task = None
    if plan.meta.get("painting_prompt"):
        img_task = asyncio.create_task(imagegen.generate_painting(plan.meta["painting_prompt"]))
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
    if plan.marker:
        body = body + "\n\n" + plan.marker
    return body, issues, attachments


async def stream_plan(
    plan: TurnPlan, providers: list[ProviderConfig]
) -> AsyncIterator[tuple[str, str]]:
    """流式执行：yield ("delta", text) 增量；结尾 yield ("final", 完整文本含标记)。

    增量经 pacer 重放：发言行逐字、行间停顿，呈现"成员一个个发言"的节奏。
    生成失败时 yield ("final", 降级文案)（无标记，状态不推进）。
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

    llm_messages = [
        {"role": "system", "content": plan.system_prompt},
        {"role": "user", "content": plan.user_content},
    ]
    img_task = None
    if plan.meta.get("painting_prompt"):
        img_task = asyncio.create_task(imagegen.generate_painting(plan.meta["painting_prompt"]))
    collected: list[str] = []
    try:
        async for delta in paced(
            stream_generate(providers, llm_messages, temperature=0.85, max_tokens=1200)
        ):
            collected.append(delta)
            yield "delta", delta
    except Exception:
        if img_task is not None:
            img_task.cancel()
        if not collected:
            fallback_src = _chunk(prompts.LLM_FALLBACK_TEXT, 16)

            async def fb_source():
                for ch in fallback_src:
                    yield ch

            async for delta in paced(fb_source(), scripted=True):
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
    if plan.marker:
        marker_text = ("\n\n" if body else "") + plan.marker
        yield "delta", marker_text
        body = body + marker_text
    yield "final", body


def _chunk(text: str, size: int = 24) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]
