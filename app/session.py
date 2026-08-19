"""会话状态重建：从消息历史解析进度标记（协议无用户ID，无状态设计）。

每轮 assistant 回复末尾的程序化标记行（v3，8轮制）：
（圆桌进度：第{n}/8轮 · {stage_label}｜主题：{theme}｜形式：画会?｜桌友：id1,id2,id3,id4）
- 既是进度条，也是状态重建锚点（含队伍ID：换人后跨轮持久）
- 换人轮重复发"第1/8轮 · 相邀"标记（阵容更新、仍在确认阶段）

流程（主题×形式）：
  1 相邀（方案介绍+团友亮相+换人询问；换人后重发）
  2 开场（确认后：圆桌活动=破冰问题 / 画会=小晴起第一笔）
  圆桌活动 3-8：入话 → 深谈 → 视角 → 真心话 → 临别赠言 → 手记
  画会 3-8：落笔 → 揭晓(生图) → 回响 → 心声 → 真心话 → 手记
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

TOTAL_ROUNDS = 8

# 圆桌活动形式轮次表
STAGE_BY_ROUND = {
    1: "invite",
    2: "ignite",
    3: "share",
    4: "depth",
    5: "persp",
    6: "heart",
    7: "close",
    8: "report",
}

# 画会形式轮次表
STAGE_BY_ROUND_PAINTING = {
    1: "invite",
    2: "ignite",
    3: "strokes",
    4: "reveal",
    5: "resonance",
    6: "meaning",
    7: "heart",
    8: "report",
}

FORM_CHAT = "chat"
FORM_PAINTING = "painting"
PAINTING_LABEL = "画会"  # 标记里的形式名

FORM_KEYWORD_RE = re.compile(r"画会|画画|绘画|一起画|画一幅|画个画|画张画")

MAX_SWAPS = 2  # 每场最多换2位团友

# 换人意图与确认意图
SWAP_KEYWORD_RE = re.compile(r"换掉|换一个|换人|不要.{0,6}$|不喜欢|换了他|换了她|换他|换她|都换掉|全都换")
CONFIRM_KEYWORD_RE = re.compile(r"开始|好的|可以|没问题|就这样|就他们|不换|不用换|开炉|开始吧|好呀|好嘞|OK|ok|冲|来吧|嗯")

MARKER_RE = re.compile(
    r"（圆桌进度：第\s*(\d+)\s*/\s*8\s*轮\s*·\s*([^｜）]+)"
    r"｜主题：([^）｜]+)"
    r"(?:｜形式：([^）｜]+))?"
    r"(?:｜桌友：([^）]+))?）"
)

GREETING = "greeting"
ENDED = "ended"

RESET_INTENT_RE = re.compile(r"再来|再开|再来一场|再来一局|新的一场|换.*主题|再聊一场|重新开始")

# 槽位顺序（与 build_team / 主题slots 对应）
SLOT_KEYS = ("bt_hist", "dp_hist", "bt_peer", "qr_peer")


@dataclass
class SessionState:
    stage: str = GREETING  # greeting | 1..8 的阶段id | ended
    theme_id: str | None = None
    next_round: int = 1
    theme_label_raw: str = ""
    form: str = FORM_CHAT
    team_ids: list[str] = field(default_factory=list)  # 当前队伍（槽位顺序）
    markers_seen: list[tuple[int, str, str]] = field(default_factory=list)
    team_variants: int = 1  # 出现过的不同阵容数（换人计数 = variants-1）


def parse_marker(text: str) -> tuple[int, str, str, str, list[str]] | None:
    """返回 (round_no, stage_label, theme_label, form, team_ids) 取最后一次出现。"""
    found = MARKER_RE.findall(text)
    if not found:
        return None
    round_no, stage_label, theme_label, form, team_raw = found[-1]
    form = FORM_PAINTING if form.strip() == PAINTING_LABEL else FORM_CHAT
    team_ids = [t.strip() for t in team_raw.split(",") if t.strip()] if team_raw.strip() else []
    return int(round_no), stage_label.strip(), theme_label.strip(), form, team_ids


def extract_text(content: object) -> str:
    """兼容 content 为字符串或多模态数组（只取 text part，其余优雅忽略）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "\n".join(p for p in parts if p)
    return ""


def reconstruct(messages: list[dict]) -> SessionState:
    """扫描 assistant 历史里的最后一个标记行，推断阶段/形式/当前队伍/换人次数。"""
    state = SessionState()
    seen_teams: list[tuple[str, ...]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        parsed = parse_marker(extract_text(msg.get("content")))
        if parsed:
            round_no, _, theme_label, form, team_ids = parsed
            state.markers_seen.append((round_no, _, theme_label))
            state.theme_label_raw = theme_label
            state.form = form
            if team_ids:
                state.team_ids = team_ids
                key = tuple(team_ids)
                if key not in seen_teams:
                    seen_teams.append(key)
    if not state.markers_seen:
        return state
    state.team_variants = max(1, len(seen_teams))
    last_round = state.markers_seen[-1][0]
    if last_round >= TOTAL_ROUNDS:
        state.stage = ENDED
        state.next_round = TOTAL_ROUNDS
        return state
    state.next_round = last_round + 1
    table = STAGE_BY_ROUND_PAINTING if state.form == FORM_PAINTING else STAGE_BY_ROUND
    state.stage = table[state.next_round]
    return state


def detect_theme(text: str, themes: list[dict]) -> dict | None:
    """从用户发言识别主题：显式点名 > 关键词计分。"""
    normalized = text.strip()
    if not normalized:
        return None
    menu_words = {
        "1": "self", "2": "academic", "3": "connection", "5": "career",
        "一": "self", "二": "academic", "三": "connection", "五": "career",
    }
    for token in re.split(r"[\s,，。.、!！?？]", normalized):
        if token in menu_words:
            matched = next((t for t in themes if t["id"] == menu_words[token]), None)
            if matched:
                return matched
    for t in themes:
        for name in (t["label"], t["full_label"]):
            if name and name in normalized:
                return t
    best: tuple[int, dict | None] = (0, None)
    for t in themes:
        score = sum(1 for kw in t.get("keywords", []) if kw and kw in normalized)
        if score > best[0]:
            best = (score, t)
    if best[0] >= 1 and len(normalized) >= 4:
        return best[1]
    return None


def detect_form(text: str) -> str:
    """从用户发言识别形式：绘画关键词 → painting，否则 chat。"""
    if FORM_KEYWORD_RE.search(text):
        return FORM_PAINTING
    for token in re.split(r"[\s,，。.、!！?？]", text.strip()):
        if token in {"4", "四", "画会"}:
            return FORM_PAINTING
    return FORM_CHAT


def wants_reset(text: str) -> bool:
    return bool(RESET_INTENT_RE.search(text))


def parse_swap_request(text: str, team_names: list[str]) -> list[int] | None:
    """解析换人请求 → 要换的槽位下标列表；None=非换人请求。

    点名优先（"小满和陈默都换掉"=换这两位）；无点名才视为全换（"都换掉"）。
    """
    if not SWAP_KEYWORD_RE.search(text):
        return None
    slots: list[int] = []
    for idx, name in enumerate(team_names):
        if name and name in text:
            slots.append(idx)
    if slots:
        return slots
    if re.search(r"都换掉|全都换|全部换", text):
        return [0, 1, 2, 3]
    return None  # 有换人词但没点名 → 视为普通确认/闲聊


def is_confirm(text: str) -> bool:
    return bool(CONFIRM_KEYWORD_RE.search(text))


def stable_pick(candidates: list[str], seed: str) -> str:
    """按 seed 稳定选一个候选（同一会话前后一致，跨会话有变化）。"""
    if not candidates:
        raise ValueError("empty candidates")
    if len(candidates) == 1:
        return candidates[0]
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return candidates[int(digest, 16) % len(candidates)]


def build_team(theme: dict, characters: dict, seed: str) -> list[dict]:
    """按治疗配额组队：2过来人 + 1不同视角 + 1安静共鸣者（槽位顺序见 SLOT_KEYS）。"""
    slots = theme["slots"]
    chosen: list[dict] = []
    for slot_key in SLOT_KEYS:
        pool = [cid for cid in slots.get(slot_key, []) if cid in characters]
        if not pool:
            raise ValueError(f"theme {theme['id']} slot {slot_key} has no valid candidates")
        cid = stable_pick(pool, f"{seed}:{slot_key}")
        chosen.append(characters[cid])
    quota = {"been-there": 2, "different-perspective": 1, "quiet-resonator": 1}
    actual: dict[str, int] = {}
    for member in chosen:
        actual[member["personality_type"]] = actual.get(member["personality_type"], 0) + 1
    assert actual == quota, f"team quota violated: {actual}"
    return chosen


def swap_member(theme: dict, characters: dict, team_ids: list[str], slot_index: int) -> str | None:
    """同槽位替补：返回新成员id；池子用尽返回 None。"""
    slot_key = SLOT_KEYS[slot_index]
    pool = [cid for cid in theme["slots"].get(slot_key, []) if cid in characters]
    unused = [cid for cid in pool if cid not in team_ids]
    if not unused:
        return None
    current = team_ids[slot_index]
    # 从未用候选里取与当前不同的第一个（池内轮换）
    return unused[0]


def make_marker(
    round_no: int,
    stage_label: str,
    theme_label: str,
    form: str = FORM_CHAT,
    team_ids: list[str] | None = None,
) -> str:
    base = f"（圆桌进度：第{round_no}/{TOTAL_ROUNDS}轮 · {stage_label}｜主题：{theme_label}"
    if form == FORM_PAINTING:
        base += f"｜形式：{PAINTING_LABEL}"
    if team_ids:
        base += "｜桌友：" + ",".join(team_ids)
    return base + "）"
