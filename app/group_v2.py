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
OBSERVE_RE = re.compile(r"旁听|你们.*聊|大家.*聊|不想说|不知道说什么|没什么想说|先听|继续听|^嗯+|^好(的)?$")
PHASE_TURN_BUDGETS = {1: 2, 2: 3, 3: 3, 4: 3, 5: 2, 6: 3, 7: 2, 8: 2}


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
    """Fixed opening: leader announces the activity before members begin it."""
    cfg = load_group_v2_config()
    lines = [
        "【小晴】欢迎来到减压安心之旅。我是带领者小晴。这里是结构式AI同伴活动，不是心理咨询或治疗；你可以随时旁听、跳过、点名、打断或结束。",
        "【小晴】今天同桌的三位团友都是虚构AI角色。我们的第一个活动是“为什么来到这张桌子”：每个人简单介绍自己的年级和专业，再说一件最近带来的压力。大家先听和好奇，不急着给办法。我请三位团友先开始。",
    ]
    for mid, card_id in zip(state.team_ids, state.card_ids):
        member = cfg["members"][mid]
        situation = member["situations"][card_id]
        lines.append(f"【{member['name']}】我是{member['name']}，{member['profile']}{situation}")
    lines.append(
        "【小晴】三位团友介绍完了，现在也给你一个正式的位置。请按你愿意的程度介绍一下："
        "希望大家怎么称呼你、年级或专业，以及今天为什么来到这里。可以只说其中一项，也可以说“我先听一轮”。"
    )
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
    if current.phase == 8 and (END_RE.search(last_user) or NEXT_RE.search(last_user)):
        return GroupState(8, "report", "", current.exchanges,
                          current.team_ids, current.card_ids), "close_report"
    if NEXT_RE.search(last_user) or (current.mode == "await_next" and re.search(r"好|可以|嗯|准确", last_user)):
        phase = min(8, current.phase + 1)
        return GroupState(phase, "main", "", 0, current.team_ids, current.card_ids), "advance"
    if current.exchanges + 1 >= PHASE_TURN_BUDGETS[current.phase]:
        if current.phase == 8:
            return GroupState(8, "report", "", current.exchanges + 1,
                              current.team_ids, current.card_ids), "close_report"
        return GroupState(current.phase + 1, "main", "", 0,
                          current.team_ids, current.card_ids), "advance"
    action = "observe" if OBSERVE_RE.search(last_user) else "discuss"
    return GroupState(current.phase, action, current.focus,
                      current.exchanges + 1, current.team_ids, current.card_ids), action


def _member_block(mid: str, card_id: str) -> str:
    cfg = load_group_v2_config()
    m = cfg["members"][mid]
    event = m["situations"][card_id]
    return (
        f"【{m['name']}｜虚构AI团友】\n"
        f"身份：{m['profile']}\n日常：{m['everyday']}\n"
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
            "说明用户可打断、跳过、点名或结束；不要询问用户想选择哪种参与方式。"
            "小晴先宣布本环节活动、目的和玩法，再让三位团友介绍身份并说今天为何来。"
        ),
        "advance": (
            "这是带领者按活动时间主动转场，不是用户必须负责推进。先接住用户刚说的内容，"
            "再由小晴用2句总结上一个活动的共同点、差异或相互影响；"
            f"随后明确宣布新活动“{phase['label']}”要讨论或完成什么、怎么参与和可以跳过。"
            "必须先宣布活动，再由一位团友示范并引出成员讨论，"
            "不要把新活动的启动责任又交回用户。"
        ),
        "discuss": (
            f"留在当前活动“{phase['label']}”自由讨论。优先回应用户最后一句，不重复活动说明。"
            "本轮小晴通常不出现。让2—3位团友直接接彼此的话，每人推进自己的经历或分歧。"
            "不要轮流评价用户，也不要把每个话题折回用户；结尾可以停在团友的话上，不必提问。"
        ),
        "observe": (
            f"用户选择旁听。围绕当前活动“{phase['label']}”，让三位团友自主聊出4—6次有来有回的短发言。"
            "他们要追问彼此、补充具体校园生活细节、出现轻微分歧或笑点；不向用户提问，不总结用户。"
            "小晴除非需要控场，否则完全不出现。"
        ),
        "repair": (
            "暂停活动，先处理关系反馈。小晴具体承认哪句话或互动造成影响，不解释初衷。"
            f"若涉及{focus_name or '某位团友'}，请其确认影响并换一种方式重说；让用户判断是否更接近。"
            "本轮不推进阶段。"
        ),
    }[action]
    if state.phase == 1 and action == "discuss":
        action_text += (
            "\n这是开场自我介绍后的轮次交接。团友回应后，小晴必须在最后回来一次："
            "若用户说了称呼就用其称呼；具体感谢其介绍，然后只问一个低负担、容易回答的问题，"
            "帮助他从刚才的话里挑出今天最占位置的一块压力。可给2—4个来自用户原话的方向，"
            "并明确也可以回答“我先听”。不要笼统地问‘想聊什么’或让用户自行发起话题。"
        )
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
- 每个新活动都遵循固定次序：小晴总结上一活动 → 宣布新活动主题、任务和边界 → 团友示范 → 自由讨论。不得让团友在活动说明之前自行开聊。
- 小晴只在攻击指责、抢话、持续未经同意的建议、参与失衡、明显不适、严重偏题或安全风险时控场。
- 对行为和影响说话，不给任何人贴人格标签；成员可以不同意小晴。
- 用户点名谁，谁优先正面回应；只有本轮动作明确为advance时才转场。
- 一次只处理一个主要互动任务、最多一个主要问题。
- 普通讨论由成员主导；小晴不是每轮必说话，也不逐句确认用户感受。
- 团友既谈压力，也谈专业、年级、食堂、图书馆、课程、组会、社团和琐碎日常；生活细节不能都服务于安慰用户。
- 用户不知说什么或选择旁听时，成员必须自己把讨论继续下去，不能全桌等用户发起。
- 团友可以温和不同意、追问彼此、承认不知道或向用户求助。
- 用户帮助团友后，团友要具体说明哪句话影响了自己，不能只说谢谢。
- 不诊断、不说教、不虚构用户没说过的事、不使用三脑理论、不承诺一定减压。

【输出】只输出剧本式对话，每行“【小晴】…”或“【{names[0]}】…”等；不写标题、旁白、规则或进度。普通轮180—420字，旁听讨论可到520字。"""


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
