"""减压安心之旅 v2：可停留阶段、自由追问与带领者控场。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .config import load_group_v2_config
from .session import extract_text

MARKER_RE = re.compile(
    r"<!--QXG2\|phase=(\d+)\|mode=([a-z_]+)\|focus=([^|]*)\|ex=(\d+)"
    r"\|team=([^|]*)\|cards=([^>]*)-->"
)

NEXT_RE = re.compile(r"进入下一|下一(个|项|阶段)|继续流程|往下|可以推进|这样收.*准确|差不多了")
END_RE = re.compile(r"结束|离桌|生成.*留笺|看看.*留笺|今天先到|就到这里")
STAY_RE = re.compile(r"继续聊|再聊|还想|还没说完|先别推进|停在这里")
FEEDBACK_RE = re.compile(r"不舒服|不喜欢|别.*建议|不要.*建议|问得太快|没听懂|你误会|不是这个意思|让我说完")


@dataclass
class GroupState:
    phase: int = 0
    mode: str = "main"
    focus: str = ""
    exchanges: int = 0
    team_ids: list[str] = field(default_factory=list)
    card_ids: list[str] = field(default_factory=list)


def marker(state: GroupState) -> str:
    return (
        f"<!--QXG2|phase={state.phase}|mode={state.mode}|focus={state.focus}|ex={state.exchanges}"
        f"|team={','.join(state.team_ids)}|cards={','.join(state.card_ids)}-->"
    )


def reconstruct(messages: list[dict]) -> GroupState | None:
    found: tuple[str, ...] | None = None
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        hits = MARKER_RE.findall(extract_text(msg.get("content")))
        if hits:
            found = hits[-1]
    if found is None:
        return None
    phase, mode, focus, exchanges, team, cards = found
    return GroupState(
        phase=int(phase), mode=mode, focus=focus, exchanges=int(exchanges),
        team_ids=[x for x in team.split(",") if x],
        card_ids=[x for x in cards.split(",") if x],
    )


def _stable(options: list[str], seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return options[int(digest, 16) % len(options)]


def select_team(seed: str) -> tuple[list[str], list[str]]:
    cfg = load_group_v2_config()
    text = seed.lower()
    if re.search(r"导师|科研|实验|论文|组会|同门|博士|研究", text):
        group = "research"
    elif re.search(r"社团|任务|忙|没时间|休息|拒绝|ddl|deadline", text):
        group = "overload"
    elif re.search(r"关系|同学|朋友|室友|家里|不被需要|冲突", text):
        group = "relationship"
    else:
        group = "default"
    team = list(cfg["matching"][group])
    cards: list[str] = []
    for idx, mid in enumerate(team):
        options = list(cfg["members"][mid]["situations"].keys())
        cards.append(_stable(options, f"{seed}|{mid}|{idx}"))
    return team, cards


def member_names(state: GroupState) -> list[str]:
    cfg = load_group_v2_config()
    return [cfg["members"][mid]["name"] for mid in state.team_ids]


def opening_script(state: GroupState) -> str:
    """Fixed, instant opening: disclose boundaries and introduce every peer first."""
    cfg = load_group_v2_config()
    lines = [
        "【小晴】欢迎来到减压安心之旅。我是带领者小晴。这里是结构式AI同伴活动，不是心理咨询或治疗；你可以随时旁听、跳过、点名、打断或结束。",
        "【小晴】今天同桌的三位团友都是虚构AI角色。先让他们正式介绍自己，再由你决定怎么参与。",
    ]
    for mid, card_id in zip(state.team_ids, state.card_ids):
        member = cfg["members"][mid]
        situation = member["situations"][card_id]
        lines.append(f"【{member['name']}】我是{member['name']}。{situation}")
    lines.append("【小晴】今天你更想被听见、一起理清、听听不同视角，还是先旁听？回复一个选项就可以。")
    return "\n".join(lines)


def detect_focus(text: str, state: GroupState) -> str:
    cfg = load_group_v2_config()
    for mid in state.team_ids:
        if cfg["members"][mid]["name"] in text:
            return mid
    return ""


def next_state(last_user: str, current: GroupState) -> tuple[GroupState, str]:
    """返回新状态与动作：discuss / advance / repair / close_report。"""
    focus = detect_focus(last_user, current)
    if FEEDBACK_RE.search(last_user):
        return GroupState(current.phase, "repair", focus or current.focus,
                          current.exchanges + 1, current.team_ids, current.card_ids), "repair"
    if focus or STAY_RE.search(last_user):
        return GroupState(current.phase, "focus", focus or current.focus,
                          current.exchanges + 1, current.team_ids, current.card_ids), "discuss"
    if current.phase == 8 and END_RE.search(last_user):
        return GroupState(8, "report", "", current.exchanges,
                          current.team_ids, current.card_ids), "close_report"
    if NEXT_RE.search(last_user) or (current.mode == "await_next" and re.search(r"好|可以|嗯|准确", last_user)):
        phase = min(8, current.phase + 1)
        return GroupState(phase, "main", "", 0, current.team_ids, current.card_ids), "advance"
    return GroupState(current.phase, "main", current.focus,
                      current.exchanges + 1, current.team_ids, current.card_ids), "discuss"


def _member_block(mid: str, card_id: str) -> str:
    cfg = load_group_v2_config()
    m = cfg["members"][mid]
    event = m["situations"][card_id]
    return (
        f"【{m['name']}｜虚构AI团友】\n"
        f"固定内核：{m['core']}\n关系倾向：{m['relational']}\n"
        f"独立需要：{m['need']}\n表达：{m['voice']}\n本场情境：{event}"
    )


def build_system_prompt(state: GroupState, action: str) -> str:
    cfg = load_group_v2_config()
    phase = cfg["phases"][state.phase - 1]
    members = "\n\n".join(
        _member_block(mid, card) for mid, card in zip(state.team_ids, state.card_ids)
    )
    names = [cfg["members"][mid]["name"] for mid in state.team_ids]
    focus_name = cfg["members"].get(state.focus, {}).get("name", "")
    action_text = {
        "open": (
            "这是第一阶段首次开启。小晴先透明披露所有团友均为虚构AI角色和非医疗边界，"
            "说明用户可打断、跳过、点名或结束；询问更需要被听见、一起理清、不同视角还是先旁听。"
            "三位团友各用一句话说今天为何来，不讲完整履历。"
        ),
        "advance": (
            "先由小晴用2—3句总结上一个阶段谈到的共同点、差异和相互影响，并说明总结可被校正；"
            f"随后自然开启新活动“{phase['label']}”，解释目的和可拒绝权，只提出一个主要邀请。"
        ),
        "discuss": (
            f"留在当前活动“{phase['label']}”自由讨论。优先回应用户最后一句，不重复活动说明。"
            "最多两位团友发言；允许团友直接回应彼此。若本阶段目标已经明显发生，"
            "小晴在结尾简短收起并询问用户想继续停留、请人收桌还是进入下一项；否则不催推进。"
        ),
        "repair": (
            "暂停活动，先处理关系反馈。小晴具体承认哪句话或互动造成影响，不解释初衷。"
            f"若涉及{focus_name or '某位团友'}，请其确认影响并换一种方式重说；让用户判断是否更接近。"
            "本轮不推进阶段。"
        ),
    }[action]
    return f"""你是“清心圆桌”的团体带领与成员生成器。当前是减压安心之旅v2。

【定位】这是结构式AI同伴关系体验，不是心理咨询或治疗。小晴守安全、结构和参与平衡；团友拥有自己的未完成困境，不是用户的导师或安慰工具。

【当前活动】第{state.phase}阶段·{phase['label']}
活动目标：{phase['goal']}
活动内容：{phase['activity']}
当前模式：{state.mode}；当前聚焦成员：{focus_name or '无'}；本阶段已往返：{state.exchanges}

【三位团友】
{members}

【本轮动作】
{action_text}

【带领边界】
- 小晴只在攻击指责、抢话、持续未经同意的建议、参与失衡、明显不适、严重偏题或安全风险时控场。
- 对行为和影响说话，不给任何人贴人格标签；成员可以不同意小晴。
- 用户点名谁，谁优先正面回应；不要自动转场。
- 一次只处理一个主要互动任务、最多一个主要问题。
- 不要求全员发言；通常只让1—2位最相关成员出现。
- 团友可以温和不同意、追问彼此、承认不知道或向用户求助。
- 用户帮助团友后，团友要具体说明哪句话影响了自己，不能只说谢谢。
- 不诊断、不说教、不虚构用户没说过的事、不使用三脑理论、不承诺一定减压。

【输出】只输出剧本式对话，每行“【小晴】…”或“【{names[0]}】…”等；不写标题、旁白、规则或进度。总计120—320字，宁可少说。"""


def build_user_content(messages: list[dict], last_user: str, cap: int = 4200) -> str:
    entries: list[str] = []
    for msg in messages[:-1]:
        role = msg.get("role")
        text = MARKER_RE.sub("", extract_text(msg.get("content"))).strip()
        if not text or role not in ("user", "assistant"):
            continue
        entries.append(("同学" if role == "user" else "团体") + "：" + text[:700])
    history = "\n".join(entries)[-cap:]
    return f"【对话历史】\n{history}\n\n【同学刚才说】\n{last_user}\n\n请继续当前团体过程。"


def build_report_prompt(state: GroupState) -> str:
    names = "、".join(member_names(state))
    return f"""你负责整理《圆桌留笺》，只基于对话历史提取用户明确说过或确认过的内容。
本场团友：{names}。所有团友均为虚构AI角色。

只输出一个JSON对象，不加代码围栏：
{{
  "approach_moment": "用户与谁在哪个具体时刻更靠近；没有则写未形成",
  "user_impact": "用户哪句话怎样影响了一位团友；没有证据则写未形成",
  "member_impact": "用户明确认可哪位团友的什么内容影响了自己；没有则写未确认",
  "differences": ["本场出现的一种立场", "另一种立场"],
  "response_need": "用户明确希望别人怎样回应；没有则写未明确",
  "real_world_phrase": "用户认可带回现实的一句话；没有则写这次先不带行动离开",
  "pressure_before": null或0到10整数,
  "pressure_after": null或0到10整数,
  "leader_note": "小晴对本场团体过程的40字内收束，不下性格结论"
}}

硬规则：不得诊断、评价人格、补造建议或把团友的话写成用户的收获；不确定就如实写未形成/未确认。"""
