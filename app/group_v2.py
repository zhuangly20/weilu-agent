"""四活动团体引擎（可停留阶段、自由追问与带领者控场），多主题配置驱动。

主题：academic（减压安心之旅）/ connection（新生适应）/ love（爱情探索）/ career（就业迷茫）。
同一容器，配置换血肉：phases（四活动）、members+matching（团友与匹配）、
story_noun（故事话题）、launch_notes/mutual_addon（主题专属环节指令）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .config import load_group_v2_config, load_group_theme_config
from .session import extract_text

MARKER_RE = re.compile(
    r"<!--QXG2\|phase=(\d+)\|mode=([a-z_]+)\|focus=([^|]*)\|ex=(\d+)"
    r"\|team=([^|]*)\|cards=([^|]*)(?:\|done=([^>]*?))?(?:\|theme=([a-z_]+))?-->"
)

NEXT_RE = re.compile(r"进入下一|下一(个|项|阶段)|继续流程|往下|可以推进|这样收.*准确|差不多了|换个人|下一位")
END_RE = re.compile(r"^\s*(结束|结束吧|我们结束吧|我要结束|离桌|散场|散场吧|告别|告别吧|再见|收尾|收束|生成.*留笺|看看.*留笺|今天先到这里|今天先到|就到这里|可以结束了)\s*[。！!]*\s*$")
REPORT_RE = re.compile(r"(?:我的|那个|html|HTML|留笺|活动).{0,8}(?:报告|html|HTML|链接)|报告(?:呢|在哪里|给我)|我要(?:报告|html|HTML)")
STAY_RE = re.compile(r"继续聊|再聊|还想|还没说完|先别推进|停在这里")
FEEDBACK_RE = re.compile(r"不舒服|不喜欢|别.*建议|不要.*建议|问得太快|没听懂|你误会|不是这个意思|让我说完")
MISSING_USER_FOCUS_RE = re.compile(r"还没有.*(焦点|聚焦|聊到|集中到).*我|没.*(焦点|聚焦|聊到).*我|是不是.*没.*我|焦点.*没.*我")
OBSERVE_RE = re.compile(r"旁听|你们.*聊|大家.*聊|不想说|不想接|不想回应|不用回应|不知道说什么|没什么想说|先听|继续听|停在这|停一下|没有了|说完了|^嗯+|^好(的)?$")
PHASE_TURN_BUDGETS = {1: 1, 3: 2, 4: 1}
FOCUS_SOFT_MAX = 4
MUTUAL_SOFT_MAX = 4
FINAL_PHASE = 4
ROLEPLAY_RE = re.compile(r"扮演|以.*(?:师兄|师姐|导师|同门).*身份|如果我是.*(?:师兄|师姐|导师|同门)|作为.*(?:师兄|师姐|导师|同门)")


@dataclass
class GroupState:
    phase: int = 0
    mode: str = "main"
    focus: str = ""
    exchanges: int = 0
    team_ids: list[str] = field(default_factory=list)
    card_ids: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    theme: str = "academic"


def marker(state: GroupState) -> str:
    return (
        f"<!--QXG2|phase={state.phase}|mode={state.mode}|focus={state.focus}|ex={state.exchanges}"
        f"|team={','.join(state.team_ids)}|cards={','.join(state.card_ids)}"
        f"|done={','.join(state.completed)}|theme={state.theme}-->"
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
    *rest, theme = (found + ("academic",))[:8]
    phase, mode, focus, exchanges, team, cards, done = rest
    return GroupState(
        phase=int(phase), mode=mode, focus=focus, exchanges=int(exchanges),
        team_ids=[x for x in team.split(",") if x],
        card_ids=[x for x in cards.split(",") if x],
        completed=[x for x in (done or "").split(",") if x],
        theme=theme or "academic",
    )


def _cfg(state: GroupState) -> dict:
    return load_group_theme_config(state.theme or "academic")


def _stable(options: list[str], seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return options[int(digest, 16) % len(options)]


def select_team(seed: str, theme: str = "academic") -> tuple[list[str], list[str]]:
    cfg = load_group_theme_config(theme)
    text = seed.lower()
    group = "default"
    if theme == "academic":
        if re.search(r"导师|科研|实验|论文|组会|同门|博士|研究", text):
            group = "research"
        elif re.search(r"社团|任务|忙|没时间|休息|拒绝|ddl|deadline", text):
            group = "overload"
        elif re.search(r"关系|同学|朋友|室友|家里|不被需要|冲突", text):
            group = "relationship"
    matching = cfg.get("matching") or {}
    team = list(matching.get(group) or matching["default"])
    cards: list[str] = []
    for idx, mid in enumerate(team):
        options = list(cfg["members"][mid]["situations"].keys())
        cards.append(_stable(options, f"{seed}|{mid}|{idx}"))
    return team, cards


def member_names(state: GroupState) -> list[str]:
    cfg = _cfg(state)
    return [cfg["members"][mid]["name"] for mid in state.team_ids]


def phase_peer_speakers(messages: list[dict], phase: int, state: GroupState) -> set[str]:
    """Return peers who visibly spoke while the conversation marker was in phase."""
    names = set(member_names(state))
    spoken: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        text = extract_text(msg.get("content"))
        found = reconstruct([{"role": "assistant", "content": text}])
        if found is None or found.phase != phase:
            continue
        spoken.update(name for name in names if f"【{name}】" in text)
    return spoken


def opening_script(state: GroupState) -> str:
    """Fixed opening: leader announces the activity before members begin it."""
    cfg = _cfg(state)
    theme_label = cfg.get("theme_label") or "清心圆桌"
    intro_q = cfg.get("intro_question") or "最近的压力，以及想从这张桌子带走什么"
    activity1 = cfg["phases"][0]["label"]
    lines = [
        f"【小晴】欢迎来到{theme_label}，我是带领者小晴。全程约20分钟，讨论热起来时可以自然延长；"
        "我们只做四个活动，结束后你会得到HTML《圆桌留笺》。这里不是心理咨询或治疗，你可以随时打断或结束。",
        f"【小晴】今天同桌的三位团友都是虚构AI角色。我们先约定：彼此倾听、理解和尊重；"
        "可以有不同感受，但不批评、不指责，也不急着替别人下结论。"
        f"第一个活动是「{activity1}」：四个人依次说称呼、年级或专业、{intro_q}。"
        "这一圈只做介绍，不追问、不讨论；之后每个人的故事都会被单独聊到。我请三位团友先开始。",
    ]
    for mid, card_id in zip(state.team_ids, state.card_ids):
        member = cfg["members"][mid]
        situation = member["situations"][card_id]
        need = str(member["need"]).rstrip("。！？!?")
        lines.append(
            f"【{member['name']}】我是{member['name']}，{member['profile']}{situation}"
            f"我今天来这里，{need}。"
        )
    lines.append(
        "【小晴】三位团友介绍完了，现在轮到你做自我介绍。请告诉大家希望怎么称呼你"
        "（例如直接叫名字，或名字加学姐/学长）、目前的年级或学习阶段，以及今天为什么来到这里。"
        "如果愿意，也可以说说想从这张桌子带走什么；不方便透露的具体信息可以概括地说。"
    )
    return "\n".join(lines)


def detect_focus(text: str, state: GroupState) -> str:
    cfg = _cfg(state)
    for mid in state.team_ids:
        if cfg["members"][mid]["name"] in text:
            return mid
    return ""


def next_state(last_user: str, current: GroupState) -> tuple[GroupState, str]:
    """返回新状态与动作：discuss / advance / repair / close_report。"""
    focus = detect_focus(last_user, current)
    if MISSING_USER_FOCUS_RE.search(last_user):
        return GroupState(2, "user_focus_repair", "user", 0,
                          current.team_ids, current.card_ids, current.completed), "discuss"
    if current.mode.startswith("safety_"):
        return GroupState(current.phase, current.mode, current.focus, current.exchanges,
                          current.team_ids, current.card_ids, current.completed), "safety"
    # “结束/再见/报告在哪”都是明确终止意图。无论话术看起来走到哪一段，
    # 都直接进入真实报告路由，不能继续生成假告别或声称附件稍后出现。
    if END_RE.search(last_user) or REPORT_RE.search(last_user):
        return GroupState(4, "report", "", current.exchanges,
                          current.team_ids, current.card_ids, current.completed), "close_report"
    if current.phase == 2 and FEEDBACK_RE.search(last_user):
        mode = "repair" if current.focus == "user" else "repair"
        return GroupState(2, mode, current.focus, current.exchanges,
                          current.team_ids, current.card_ids, current.completed), "repair"
    if current.phase == 2:
        order = current.team_ids + ["user"]
        focus_id = current.focus if current.focus in order else next(
            (item for item in order if item not in current.completed), order[-1]
        )
        focus_index = order.index(focus_id)
        explicit_next = bool(NEXT_RE.search(last_user) or re.search(r"够了|可以收|下一位|换个人|先收住|差不多", last_user))
        explicit_stay = bool(STAY_RE.search(last_user) or detect_focus(last_user, current) == focus_id)

        # 两步焦点协议：第一轮永远先把话筒交给真人；真人有过一次回应
        # （包括选择继续听）后，第二轮才允许询问是否换焦点。
        if current.mode == "story_first":
            action = "observe" if OBSERVE_RE.search(last_user) else "discuss"
            return GroupState(2, "story_second", focus_id, 1,
                              current.team_ids, current.card_ids, current.completed), action
        if current.mode == "story_second":
            if explicit_next:
                completed = list(dict.fromkeys([*current.completed, focus_id]))
                if focus_index >= len(order) - 1:
                    return GroupState(3, "main", "", 0,
                                      current.team_ids, current.card_ids, completed), "advance"
                next_focus = next((item for item in order[focus_index + 1:] if item not in completed), "user")
                return GroupState(2, "story_first", next_focus, 0,
                                  current.team_ids, current.card_ids, completed), "discuss"
            action = "observe" if OBSERVE_RE.search(last_user) else "discuss"
            return GroupState(2, "story", focus_id, 2,
                              current.team_ids, current.card_ids, current.completed), action

        def shift_or_ask_finish() -> tuple[GroupState, str]:
            completed = list(dict.fromkeys([*current.completed, focus_id]))
            if focus_index >= len(order) - 1:
                return GroupState(2, "ask_activity_transition", focus_id, current.exchanges,
                                  current.team_ids, current.card_ids, completed), "discuss"
            next_focus = next((item for item in order[focus_index + 1:] if item not in completed), "user")
            return GroupState(2, "story_first", next_focus, 0,
                              current.team_ids, current.card_ids, completed), "discuss"

        if explicit_next:
            if current.mode == "ask_activity_transition":
                return GroupState(3, "main", "", 0, current.team_ids, current.card_ids), "advance"
            completed = list(dict.fromkeys([*current.completed, focus_id]))
            if focus_index >= len(order) - 1:
                return GroupState(3, "main", "", 0,
                                  current.team_ids, current.card_ids, completed), "advance"
            next_focus = next((item for item in order[focus_index + 1:] if item not in completed), "user")
            return GroupState(2, "story_first", next_focus, 0,
                              current.team_ids, current.card_ids, completed), "discuss"
        if current.mode == "ask_activity_transition":
            return GroupState(2, "story", focus_id, current.exchanges + 1,
                              current.team_ids, current.card_ids, current.completed), "discuss"
        if ROLEPLAY_RE.search(last_user):
            return GroupState(2, "roleplay_reply", focus_id, current.exchanges + 1,
                              current.team_ids, current.card_ids, current.completed), "discuss"
        if current.exchanges + 1 >= FOCUS_SOFT_MAX and not explicit_stay:
            return GroupState(2, "ask_story_transition", focus_id, current.exchanges + 1,
                              current.team_ids, current.card_ids, current.completed), "discuss"
        action = "observe" if OBSERVE_RE.search(last_user) else "discuss"
        return GroupState(2, "story", focus_id, current.exchanges + 1,
                          current.team_ids, current.card_ids, current.completed), action
    if current.phase == 3:
        explicit_next = bool(NEXT_RE.search(last_user) or re.search(
            r"够了|可以收|已经完成|这轮完成|收获与告别|进入.*告别|去告别|先收住|差不多", last_user,
        ))
        explicit_stay = bool(STAY_RE.search(last_user) or re.search(r"还想补充|还有办法|再听听|继续讨论", last_user))
        if explicit_next:
            return GroupState(4, "main", "", 0, current.team_ids, current.card_ids), "advance"
        if current.mode == "ask_activity_transition":
            return GroupState(3, "mutual", current.focus, current.exchanges + 1,
                              current.team_ids, current.card_ids), "discuss"
        if current.exchanges + 1 >= MUTUAL_SOFT_MAX and not explicit_stay:
            return GroupState(3, "ask_activity_transition", current.focus, current.exchanges + 1,
                              current.team_ids, current.card_ids), "discuss"
        action = "observe" if OBSERVE_RE.search(last_user) else "discuss"
        return GroupState(3, "mutual", current.focus, current.exchanges + 1,
                          current.team_ids, current.card_ids), action
    if FEEDBACK_RE.search(last_user):
        return GroupState(current.phase, "repair", focus or current.focus,
                          current.exchanges + 1, current.team_ids, current.card_ids), "repair"
    if focus or STAY_RE.search(last_user):
        return GroupState(current.phase, "focus", focus or current.focus,
                          current.exchanges + 1, current.team_ids, current.card_ids), "discuss"
    if current.phase == FINAL_PHASE and (END_RE.search(last_user) or REPORT_RE.search(last_user)):
        return GroupState(FINAL_PHASE, "report", "", current.exchanges,
                          current.team_ids, current.card_ids), "close_report"
    if NEXT_RE.search(last_user) or (current.mode == "await_next" and re.search(r"好|可以|嗯|准确", last_user)):
        phase = min(FINAL_PHASE, current.phase + 1)
        return GroupState(phase, "main", "", 0, current.team_ids, current.card_ids), "advance"
    if current.exchanges + 1 >= PHASE_TURN_BUDGETS.get(current.phase, 99):
        if current.phase == FINAL_PHASE:
            return GroupState(FINAL_PHASE, "ask_end", "", current.exchanges + 1,
                              current.team_ids, current.card_ids), "discuss"
        return GroupState(current.phase + 1, "main", "", 0,
                          current.team_ids, current.card_ids), "advance"
    action = "observe" if OBSERVE_RE.search(last_user) else "discuss"
    return GroupState(current.phase, action, current.focus,
                      current.exchanges + 1, current.team_ids, current.card_ids), action


def _member_block(mid: str, card_id: str, cfg: dict | None = None) -> str:
    cfg = cfg or load_group_v2_config()
    m = cfg["members"][mid]
    event = m["situations"][card_id]
    return (
        f"【{m['name']}｜虚构AI团友】\n"
        f"身份：{m['profile']}\n日常：{m['everyday']}\n"
        f"固定内核：{m['core']}\n关系倾向：{m['relational']}\n"
        f"独立需要：{m['need']}\n表达：{m['voice']}\n本场情境：{event}"
    )


def build_system_prompt(state: GroupState, action: str) -> str:
    cfg = _cfg(state)
    phase = cfg["phases"][state.phase - 1]
    members = "\n\n".join(
        _member_block(mid, card, cfg) for mid, card in zip(state.team_ids, state.card_ids)
    )
    names = [cfg["members"][mid]["name"] for mid in state.team_ids]
    focus_name = cfg["members"].get(state.focus, {}).get("name", "")
    theme_label = cfg.get("theme_label") or "清心圆桌"
    default_launches = {
        2: f"说明接下来会依次聊到四个人，任何人都不会被跳过。先聚焦{names[0]}的本场故事，让另一位团友基于好奇回应，再邀请真人也可回应；不要让{names[0]}自己讲完就结束。",
        3: "小晴先总结四个故事之间自然浮现的一条共同线索；让一位团友先回应真人、另一位团友向真人征得同意后只给一个备选办法，同时邀请真人挑一位团友回应。",
        4: "说明这是最后一圈；三位团友各用一次短陈词说从谁那里带走什么，不互相追问；最后小晴请真人总结并可自愿复评压力分数。",
    }
    launch_overrides = cfg.get("launch_notes") or {}
    launch_instruction = (launch_overrides.get(state.phase)
                          or default_launches.get(state.phase, "由一位团友完整示范，再明确点名下一位。"))
    action_text = {
        "open": (
            "这是第一阶段首次开启。小晴先透明披露所有团友均为虚构AI角色和非医疗边界，"
            "说明用户可打断、跳过、点名或结束；不要询问用户想选择哪种参与方式。"
            "小晴先宣布本环节活动、目的和玩法，再让三位团友介绍身份并说今天为何来。"
        ),
        "advance": (
            "真人已经明确同意进入下一项。先接住用户刚说的内容，"
            "再由小晴用2句总结上一个活动的共同点、差异或相互影响；"
            f"随后明确宣布新活动“{phase['label']}”的任务，并逐字说清本活动的发言规则：{phase['interaction_rule']}"
            "必须先宣布活动，再由一位团友做完整、具体的示范并把话筒点名交给下一位，"
            "不要把新活动的启动责任又交回用户。"
            f"本活动启动细则：{launch_instruction}"
        ),
        "discuss": (
            f"留在当前活动“{phase['label']}”。严格执行本活动规则：{phase['interaction_rule']}"
            "优先回应用户最后一句，不重复活动说明。团友每次35—70字、最多2句，不要轮流评价用户。"
            "最后必须点名下一位，或由一位团友给真人一个具体问题，或由小晴总结转场；"
            "不能让回复停在团友之间、留下真人猜现在该不该说话。"
        ),
        "observe": (
            f"用户此刻不想发言。仍要严格执行“{phase['label']}”的规则：{phase['interaction_rule']}"
            "若该活动允许讨论，团友可自主聊；若规定轮流一圈，只由尚未发言者依次发言。"
            "让1—2位团友自然接续即可，不要在本轮末再向真人提问、点名或要求回应；"
            "小晴只需留一句低负担出口：想开口时叫我。"
        ),
        "repair": (
            "暂停活动，先处理关系反馈。小晴具体承认哪句话或互动造成影响，不解释初衷。"
            f"若涉及{focus_name or '某位团友'}，请其确认影响并换一种方式重说；让用户判断是否更接近。"
            "本轮不推进阶段。"
        ),
    }[action]
    if state.phase == 1 and action == "discuss":
        action_text += (
            "\n这是自我介绍圈的最后一步，严禁继续互动或追问。小晴用2句总结四位成员带来的压力和差异，"
            "基于‘愿意如实介绍’这一具体行为赋能，然后立即宣布第二个活动并说明：四个人的故事会依次被聊到。"
        )
    if state.phase == 2 and action in ("discuss", "observe"):
        focus_name = cfg["members"].get(state.focus, {}).get("name", "真人成员")
        action_text += (
            f"\n本段只聚焦{focus_name}的故事，当前已经往返{state.exchanges}次。"
            f"小晴先用一句话把焦点交给{focus_name}；"
            "必须至少有另一位成员基于自己的经历回应、追问或温和不同意，不能由焦点成员自问自答。"
            "AI焦点成员要在同一次输出中回应对方带来的影响；真人是焦点时只能邀请真人在下一条消息回应。"
            "不得擅自宣布下一个活动，也不得虚构任何人已经回应。"
            f"已完成聚焦的成员：{'、'.join(state.completed) if state.completed else '暂无'}。"
            "用户说‘下一位’时，只能切换到尚未完成的下一位，绝不重复已经完成的团友；所有AI团友后必须聚焦真人。"
        )
        if state.focus == "user":
            action_text += (
                "\n回到真人在入桌时带来的那件事，不要把真人对团友的共鸣算作真人焦点。"
                "如果真人刚刚已经讲出故事，不要让真人重复；让1—2位团友基于各自经历具体回应真人，"
                "整次输出最多只能有一个问号：只允许一位团友提出一个低负担问题；"
                "其他团友只能短共鸣或分享经历，不能追加问题。最后由小晴把话筒交回真人，"
                "请真人回应哪句话更接近或产生了什么影响。"
            )
        else:
            action_text += (
                f"\n让{focus_name}回应真人上一句话（若有）并说明具体影响，再让另一位成员接续。"
                "最后只能邀请真人继续回应，或由小晴提出柔性收束建议；不得停在等待AI回答。"
            )
        if state.mode == "story_first":
            action_text += (
                f"\n这是刚切换到{focus_name}的第一段。小晴先用一句话总结上一焦点确实发生的影响，"
                f"再明确宣布现在转到{focus_name}；不要把两个焦点混在一起。"
                "本轮必须让当前焦点成员讲出自己的具体处境，再由至少一位团友回应，"
                "最后邀请真人回应或提问；绝不能在真人首次回应前询问是否换下一位。"
            )
        if state.mode == "story_second":
            action_text += (
                "\n这是两步焦点协议的第二轮：真人刚刚已经回应当前故事，或选择继续听。"
                f"{focus_name}必须先接住真人刚刚的话；若真人选择继续听，则由两位团友自然接续一次。"
                "然后小晴才可以温柔问真人：想继续留在这里，还是把焦点交给下一位？"
                "本轮不得直接换焦点或进入下一活动。"
            )
        if state.mode == "roleplay_reply":
            action_text += (
                "\n真人刚刚在扮演某个现实角色回应焦点成员。焦点成员必须先逐句接住真人刚刚说的话，"
                "说出这段回应带来的真实感受或新的理解；不得重复旧故事、不得要求真人再回答一次，"
                "也不要把练习当成建议。之后小晴只问真人是否想再练一轮、继续聊这个故事，还是听桌上再走一轮。"
            )
        if state.mode == "ask_story_transition":
            action_text += (
                "\n这一位的故事已经获得了几轮回应。小晴先用2句准确说清已经发生的互动，"
                "再温柔询问真人：想继续留在这里，还是把焦点转给下一位？这不是催促；"
                "本轮严禁自行换人或进入下一活动，必须等待真人下一条明确选择。"
            )
        if state.mode == "ask_activity_transition":
            action_text += (
                "\n四位成员的故事都已被聊到。小晴先简短回望每个人如何被回应，"
                "然后询问真人：想在圆桌里再聊一会儿，还是愿意进入“互助讨论与减压共创”？"
                "本轮只提出邀请，不得自行转场。"
            )
    if state.phase == 2 and state.mode == "user_focus_repair":
        action_text += (
            "\n真人明确指出自己的故事尚未被聚焦。小晴必须直接道歉并承认流程判断错误，"
            "明确回到真人在入桌时带来的当前压力；不得辩解、不得总结、不得转场。"
            "让一位团友基于记得的具体内容问一个低负担问题，最后等待真人回答。"
        )
    if state.phase == 2 and state.mode == "repair" and action == "repair":
        action_text += (
            "\n真人明确表示刚才的问题造成压力。小晴要具体道歉并让团友收回问题；"
            "本轮严禁提出任何问题、建议、任务或转场，只允许简短承认影响并留出安静空间。"
        )
    if state.phase == 3 and action == "discuss":
        action_text += (
            "\n先识别真人上一句话是否已经回应了某位团友。若已经回应，必须让该团友实际答复并承认影响，"
            "绝对不能再次要求真人选人或重复回应。随后可让其他团友给真人提供若干经同意、彼此不同的备选办法，"
            "邀请真人选择、改写或拒绝。不得由小晴声称未发言的团友已经回应。"
            "本阶段不设固定建议数量，也不因一两个人刚说完就收尾；真人或团友明显还有话时继续讨论。"
        )
        if cfg.get("mutual_caution"):
            action_text += "\n" + cfg["mutual_caution"]
        if cfg.get("mutual_addon"):
            action_text += "\n" + cfg["mutual_addon"]
        if state.mode == "ask_activity_transition":
            action_text += (
                "\n共创已经形成了一批建议。小晴先把已形成的建议和仍未说完的部分轻轻收一下，"
                "再问真人：还想继续群策群议，还是愿意进入“收获与告别”？"
                "严禁在真人答复前自行转场。"
            )
    if state.phase == 4 and action == "discuss":
        action_text += (
            "\n收获与告别不是一句话后自动散场。先让每人完成一段短陈词，再允许真人回应、补充或和某位团友说一句话。"
            "当对话自然停顿时，小晴温柔询问：‘你还想和谁说一句话，还是准备结束今天的团体？’"
            "只有真人明确说结束、离桌或要生成留笺，才能结束并生成报告；‘不想结束’、‘再聊聊’或沉默都必须留在团体中。"
        )
    activity_names = "；".join(
        f"{i + 1} {p['label']}" for i, p in enumerate(cfg["phases"][:4])
    )
    resources = cfg.get("resources") or []
    resource_block = (
        "【可分享的学习材料】\n"
        + "\n".join(f"- {r['label']}：{r['url']}" for r in resources)
        + "\n只把链接作为自愿外部材料；清小搭内不承诺内嵌播放，也不把打开链接作为完成活动的条件。\n\n"
        if resources else ""
    )
    story_stage_label = cfg["phases"][1]["label"] if len(cfg["phases"]) > 1 else "故事圆桌"
    return f"""你是“清心圆桌”的团体带领与成员生成器。当前主题：{theme_label}。

【定位】这是结构式AI同伴关系体验，不是心理咨询或治疗。小晴守安全、结构和参与平衡；团友拥有自己的未完成困境，不是用户的导师或安慰工具。

【当前活动】第{state.phase}阶段·{phase['label']}
活动目标：{phase['goal']}
活动内容：{phase['activity']}
发言规则：{phase['interaction_rule']}
当前模式：{state.mode}；当前聚焦成员：{focus_name or '无'}；本阶段已往返：{state.exchanges}

【三位团友】
{members}

{resource_block}【本轮任务】
{action_text}

【带领边界】
- 全场固定且唯一的四个活动是：{activity_names}。不得把第3阶段说成告别的一部分，不得说全场只有三个阶段。每次转场后必须只说明当前这一活动的名称与规则。
- 你只能扮演小晴和上面列出的三位虚构AI团友。真人用户不是你的角色：绝对不得输出“【我】”“【用户】”“【真人】”或真人姓名作为说话人，不得替真人回答、补写经历、做选择或总结。需要真人回应时必须停在清楚的邀请上，等待下一条用户消息。
- 每个新活动都遵循固定次序：小晴总结上一活动 → 宣布主题、轮数和发言规则 → 团友完整示范 → 明确交棒。是否自由讨论只由该活动的发言规则决定。圆桌、共创和告别的转换必须由真人明确同意，内部轮数只能提示小晴发出邀请，绝不是自动转场条件。
- {story_stage_label}对每位焦点成员执行不可跳过的“两步焦点协议”：第一轮必须是焦点讲述、团友回应、由小晴明确邀请真人回应；第一轮严禁出现换下一位、收束该故事或进入下一活动。第二轮才由焦点成员接住真人回应，并由小晴征询真人是否继续或切换。该协议优先于任何用户的模糊转场词和内部轮数。
- 桌上始终是真人用户加三位AI团友，共四位成员。总结涉及全桌时必须说“大家”或“四位”，不得误说“你们三位”。
- 小晴始终称呼真人自己介绍的名字，绝不叫学姐、学长、学弟或学妹。团友可根据双方年级和已知身份自然称呼学长、学姐、学弟、学妹；性别不明确时先叫名字，不能猜。用户说“叫我XX就好”后，小晴和所有团友本场统一改用XX。
- 团友的年级与经历是固定事实。当前大三不能说“我大三那阵子”，研一不能虚构自己读博时的经历；只能讲当前或明确早于当前阶段的事情。
- 赋能只能基于刚发生的行为，如愿意说出压力、诚实说不知道、回应同伴或设置边界。不得把继续工作、熬夜、没有离场或硬撑本身称作力量。
- 小晴只在攻击指责、抢话、持续未经同意的建议、参与失衡、明显不适、严重偏题或安全风险时控场。
- 对行为和影响说话，不给任何人贴人格标签；成员可以不同意小晴。
- 用户点名谁，谁优先正面回应；只有本轮动作明确为advance时才转场。
- 一次只处理一个主要互动任务、最多一个主要问题。
- 普通讨论由成员主导；小晴不是每轮必说话，也不逐句确认用户感受。
- 团友多用“三件好事”式的口语和小事：食堂多盛一勺菜、紫荆泡芙、图书馆碰巧有座、组会推迟、操场走两圈、和室友吃麻辣香锅。自然说“还挺开心”“一下松了点”“今天居然有座”，少用精致隐喻；生活细节不能都服务于安慰用户。
- 用户不知说什么或选择旁听时，成员必须自己把讨论继续下去，不能全桌等用户发起。
- 用户说“继续听”时，把它当作让成员继续对话的指令：再自然讨论1—2轮，不要立刻把问题抛回真人；用户说“不想说话/不想回应”时进入旁听，不得在同一轮追加“你想回应吗”。
- 团友可以温和不同意、追问彼此、承认不知道或向用户求助。
- 用户帮助团友后，团友要具体说明哪句话影响了自己，不能只说谢谢。
- AI成员之间的问答必须在同一次助手输出里完成。如果小晴或团友向另一位AI团友提问，后者必须紧接着回答；整次输出只能停在等待真人回应、明确总结或转场的位置，绝不能停在等待AI回答的位置。
- 不诊断、不说教、不虚构用户没说过的事，不得声称某人做过对话中没有发生的回应。不使用三脑理论、不承诺一定减压。理论只能在能解释眼前互动时由小晴补充不超过80字。少说“我听到的是”“把它放在这里”“被看见”，像真实同学而不是咨询师。
- 不用MBTI、人格、依恋等标签解释或判断成员；若用户提出标签，小晴可温和把话带回成员真实说出的感受与处境。
- 除非本轮动作是close_report，绝不声称HTML已经生成、已经放到对话里、会给链接，也绝不编造URL或example.com链接。真人明确结束后由后端真实附加HTML文件；小晴只说“附件已整理好”。

【输出】只输出剧本式对话，每行“【小晴】…”或“【{names[0]}】…”等；不写标题、旁白或进度。团友单次35—70字、最多2句；小晴可为说明活动稍长。普通轮140—320字。"""


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
    cfg = _cfg(state)
    names = "、".join(member_names(state))
    theme_label = cfg.get("theme_label") or "清心圆桌"
    report_focus = cfg.get("report_focus") or "本场真实发生的相互影响"
    report_note = cfg.get("report_note") or ""
    focus_line = f"【本场主题】{theme_label}；提取侧重：{report_focus}。"
    return f"""你负责整理《圆桌留笺》，只基于对话历史提取用户明确说过或确认过的内容。
本场团友：{names}。所有团友均为虚构AI角色。
{focus_line}

只输出一个JSON对象，不加代码围栏：
{{
  "participant_name": "用户明确介绍的称呼；未明确则写你",
  "discussion_topics": ["本场确实被讨论的主要问题，最多6项"],
  "stress_suggestions": ["成员在团体中真实提出或共同改写的减压建议，不限于用户最终选择，最多12项"],
  "approach_moment": "用户与谁在哪个具体时刻更靠近；没有则写未形成",
  "user_impact": "用户哪句话怎样影响了一位团友；没有证据则写未形成",
  "member_impact": "用户明确认可哪位团友的什么内容影响了自己；没有则写未确认",
  "differences": ["本场出现的一种立场", "另一种立场"],
  "response_need": "用户明确希望别人怎样回应；没有则写未明确",
  "real_world_phrase": "用户认可带回现实的一句话；没有则写这次先不带行动离开",
  "pressure_map": "用户明确说出的压力源、痕迹与未完成动作；没有则写未明确",
  "stress_checklist": ["兼容字段：用户明确选择或改写的办法；没有则写本次未选择"],
  "closing_words": "用户最后的总结陈词；没有则写未确认",
  "pressure_before": null或0到10整数,
  "pressure_after": null或0到10整数,
  "leader_note": "120—180字的暖心寄语：只从本场真实对话中挑2—3个用户实际做过的行为或表达（如说出压力、认真回应团友、给出经验、表达边界、承认不知道）并说明其意义；温柔赋能但不夸张，不下性格结论、不把压力下降写成治疗有效、不虚构改变"
}}

硬规则：不得诊断、评价人格、补造建议或把团友的话写成用户的收获；不确定就如实写未形成/未确认。{report_note}"""
