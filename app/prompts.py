"""整轮群聊生成的 prompt 构建 + 输出校验（沿用心晴谷"约束+校验"模式）。"""
from __future__ import annotations

import re

LEADER_NAME = "小晴"

PERSONALITY_CN = {
    "been-there": "过来人",
    "different-perspective": "不同视角者",
    "quiet-resonator": "安静共鸣者",
}

# 全局禁词（对抗AI谄媚与医疗化表述）
FORBIDDEN_WORDS = ("治愈", "诊断", "你一定会好", "心理咨询", "治疗", "抑郁症")

# 每轮发言总字数上限（report 轮除外）
TURN_TOTAL_LIMIT = 480

LINE_RE = re.compile(r"【([^】]+)】")

GREETING_TEXT = """（炉火噼啪，小木屋的门被推开，一股夜里的凉气进来，又被暖意接住）

【小晴】晚上好呀，同学～我是**小晴**，小清心的AI团体助手化身，也是「清心圆桌」的小主人。这里有阳光、热茶和暖炉，还有几位很有意思的桌友——没有评判，也没有建议轰炸。

【小晴】今天的圆桌有这些活动，想参加哪一场？
1️⃣ **自我探索·清心圆桌**——"我是谁、我想要什么"
2️⃣ **学业压力·清心圆桌**——绩点、科研、同辈比较
3️⃣ **新生适应·清心圆桌**——想家、宿舍、新环境
5️⃣ **就业迷茫·清心圆桌**——工作、升学、Gap
🎨 **圆桌画会**——任选一个主题，大家用文字轮流添笔，共同完成一幅真正的画（结束送给你）

每一场都会为你挑选几位AI桌友围坐陪伴；活动结束后，你还会收到一张根据当晚内容亲手写的明信片✨

回复数字或活动名就能报名；也可以直接跟我说说最近的困惑，我来帮你挑一场最合适的～

（小声说：清心圆桌是朋辈支持空间，不是心理治疗哦。如果你正处于难以承受的痛苦中，请一定联系学校心理中心或专业援助热线。）"""

MENU_RETRY_TEXT = """【小晴】（歪着头）炉边有点热闹，我没太听清你想参加哪一场——
1️⃣ 自我探索　2️⃣ 学业压力　3️⃣ 新生适应　5️⃣ 就业迷茫　🎨 圆桌画会

回复数字或活动名就行；或者，直接跟我讲讲最近的心事也可以～"""

SEAT_REASK_TEXT = """【小晴】（歪头）没太听清——你是想换一位桌友，还是准备开场啦？
想换谁就点名告诉我（比如"换掉苏轼"）；不换的话说声"开始"，我们马上开始～"""

NO_MORE_SWAP_TEXT = """【小晴】（挠挠头）有点不好意思，剩下的桌友已经是我今天能请到的全部啦……
要不我们先开始试试？聊上两句要是真的不合拍，再回来找我商量～就这么定？"""

ENDED_TEXT = """【小晴】今晚的炉火慢慢暗下去了，谢谢你来～
想再开一场的话，跟我说声**再来一场**，或者直接说下一场想聊的主题。炉子随时可以重新生起来。"""

LLM_FALLBACK_TEXT = """【小晴】（往炉子里添了根柴，火光晃了晃）今晚炉火有点慢，刚才那句话我没有接稳。能再对我说一遍吗？"""


def _member_block(member: dict) -> str:
    quota_cn = PERSONALITY_CN.get(member.get("personality_type", ""), "成员")
    lines = [
        f"【{member['name']}】（{quota_cn}）",
        str(member.get("system_prompt_template", "")).strip(),
    ]
    if member.get("speak_style"):
        lines.append(f"说话风格：{member['speak_style']}")
    if member.get("facts_boundaries"):
        lines.append(f"经历边界（只可谈及以下设定，不得虚构）：{member['facts_boundaries']}")
    lines.append(
        "发言规则：你是亲自来参加团体的，带着自己的心情，不是来'帮助用户'的助手。"
        "只讲自己的经历和感受，20-60字；不评价用户、不建议、不提问、不诊断。"
    )
    return "\n".join(lines)


def _leader_block(leader_cfg: dict) -> str:
    return (
        f"【{LEADER_NAME}】（带领者）\n{str(leader_cfg.get('persona', '')).strip()}\n"
        "发言规则：每次发言不超过3句话；先准确共情或点出对方身上可观察的优势"
        "（勇气/觉察/真诚/感受力/边界感），再推进流程；禁止说教、强行积极化、"
        "承诺'会好起来'；每轮最多出现一个问句，且只向真人同学提问。"
    )


# ---------- 相邀 / 换人（成员由小晴介绍，本人不发言或只道别） ----------


def build_invite_system_prompt(
    leader_cfg: dict,
    team: list[dict],
    theme: dict,
    form: str,
) -> str:
    """第1轮·相邀：方案介绍 + 团友介绍 + 换人询问（只有小晴发言）。"""
    intro_names = "、".join(m["name"] for m in team)
    theme_label = theme["full_label"]
    if form == "painting":
        scheme = (
            f"- 玩法：**圆桌画会**（{theme_label}）。不用画笔——大家在圆桌边坐定，"
            "用文字轮流往同一幅画上添一笔（每笔都连着自己的心事），"
            "所有笔触最后会被真正画成一幅完整的画，结束时送给同学\n"
            "- 时长：大约15-20分钟，共8轮环节\n"
            "- 收获：一幅大家一起完成的画 + 一张根据今晚内容亲手写的明信片"
        )
    else:
        scheme = (
            f"- 玩法：**{theme_label}·夜话**。大家围着圆桌文字畅聊，"
            "一共8轮环节，从破冰到真心话，每轮都轻巧不费力\n"
            "- 时长：大约10-15分钟\n"
            "- 收获：一份根据今晚对话亲手写的《成长手记》和一张专属明信片✨（先卖个关子～）"
        )
    return "\n\n".join(
        [
            "你是\"清心圆桌\"的导演。本轮只有带领者小晴一个人发言，完成\"介绍与相邀\"。",
            "【空间设定】一间暖融融的小屋，圆桌上茶炉咕嘟着热气。氛围安全、松弛、不评判。",
            _leader_block(leader_cfg),
            "【今晚的桌友（小晴口头介绍，他们本人本轮不发言）】\n"
            + "\n".join(
                f"- {m['name']}：{str(m.get('facts_boundaries', '')).strip()[:80]}"
                f"（{PERSONALITY_CN.get(m.get('personality_type', ''), '')}）"
                for m in team
            ),
            "【本轮任务】第1/8轮 · 相邀。流程：\n"
            "1) 如果同学刚说了近况或困惑：先用1-2句温柔接住情绪，再自然引出"
            "\"今晚这一场应该很适合你\"；如果只是报了活动名，就直接俏皮地欢迎～\n"
            f"2) 介绍今晚的方案（轻快但说清楚）：\n{scheme}\n"
            "3) 轻轻带一句声明（自然语气，不严肃）：“小声说，这里是朋辈支持的小团体，"
            "不是治疗哦——不过大家的认真程度不打折。”\n"
            f"4) 介绍今天请到的四位桌友（{intro_names}）：每人1-2句，"
            "姓名+身份背景+为什么今晚请TA，用小晴介绍朋友的口吻\n"
            "5) 结尾问：“这几位桌友合你眼缘吗？想换掉谁跟我说一声就行；"
            "不换的话，我们马上开始啦～”",
            "【输出格式（必须严格遵守）】\n"
            f"- 只有【{LEADER_NAME}】发言，可分多行（每行都以【{LEADER_NAME}】开头）\n"
            "- 全轮合计200-320字；不写旁白、不用markdown加粗\n"
            "- 禁止出现：治愈、诊断、治疗、心理咨询",
        ]
    )


def build_swap_system_prompt(
    leader_cfg: dict,
    team: list[dict],
    departing: list[dict],
    arriving: list[dict],
    note: str = "",
) -> str:
    """换人轮：小晴接住请求 → 旧成员道别 → 介绍新成员 → 再确认。"""
    blocks = [_leader_block(leader_cfg)]
    blocks += [_member_block(m) + "\n（你本轮任务：说一句简短的道别，15-30字，豁达温暖，不追问原因）" for m in departing]
    blocks += [
        _member_block(m) + "\n（你本轮任务：刚加入圆桌，说一句简短的入座招呼，15-30字）"
        for m in arriving
    ]
    names_dep = "、".join(m["name"] for m in departing)
    names_arr = "、".join(m["name"] for m in arriving)
    task = (
        "【本轮任务】同学想更换桌友。流程：\n"
        "1) 小晴俏皮地接住请求（不评判同学的喜好，\"换人完全没关系呀\"的态度）\n"
        f"2) 被换下的{names_dep}说一句简短道别（豁达温暖）\n"
        f"3) 小晴介绍新加入的{names_arr}（1-2句：姓名+背景+为什么今晚请TA）\n"
        f"4) 新成员说一句入座招呼\n"
        "5) 小晴结尾再问：\"还有想换的吗？没有的话我们马上开始啦～\""
    )
    if note:
        task = note + "\n" + task
    return "\n\n".join(
        [
            "你是\"清心圆桌\"的导演。本轮完成一次桌友更换。",
            "【空间设定】一间暖融融的小屋，圆桌上茶炉咕嘟着热气。",
            *blocks,
            task,
            "【输出格式（必须严格遵守）】\n"
            "- 每个发言独占一行：`【角色名】发言内容`；顺序：小晴→道别→小晴介绍→入座→小晴收尾\n"
            "- 全轮合计不超过300字；禁止出现：治愈、诊断、治疗、心理咨询",
        ]
    )


# ---------- 正式轮次 ----------


def _chat_stage_instruction(stage_id: str, theme: dict, team: list[dict]) -> str:
    bt_hist, dp_hist, bt_peer, qr_peer = [m["name"] for m in team]
    head = f"本轮是第{theme.get('_round', '?')}/8轮 · "
    if stage_id == "ignite":
        return head + (
            "开场。同学已确认阵容，正式开始：\n"
            "1) 小晴俏皮地宣布开场（一句）。\n"
            f"2) {bt_peer} 和 {qr_peer} 各说一句简短的入座感言（10-20字，不提问不建议）。\n"
            f"3) 小晴提出今晚的破冰问题：\n「{theme['icebreak']}」\n"
            "小晴的问句只保留这一个。"
        )
    if stage_id == "share":
        return head + (
            "圆桌入话。真人同学刚回答了破冰问题。流程：\n"
            "1) 小晴用一两句接住这个回答，点出其中可观察的特质（不评判用词选择）。\n"
            f"2) {bt_peer} 和 {qr_peer} 各用一句自己的经历共鸣（是'我也…'，不是夸用户）。\n"
            f"3) 小晴自然引出今晚的主活动，向真人同学提问：\n「{theme['activity']}」"
        )
    if stage_id == "depth":
        return head + (
            "炉边深谈。真人同学刚完成主活动分享。流程：\n"
            "1) 小晴先接住分享里最真实的情绪。\n"
            f"2) {bt_hist} 和 {dp_hist} 各分享一段自己相关的经历（50-80字，有细节，"
            "最后一句落回'此刻坐在炉边'的感受）。\n"
            f"3) 小晴简单小结，再抛出收尾问题：\n「{theme['closing_question']}」"
        )
    if stage_id == "persp":
        return head + (
            "交换视角。真人同学回答了收尾问题。流程：\n"
            f"1) {dp_hist} 就今晚听到的整个分享，用自己独有的视角给真人同学一种"
            f"'换一种讲法'的回应（50-80字）。{theme['persp_guidance']}\n"
            f"2) {bt_peer} 用一句自己的话补充共鸣或佐证。\n"
            "3) 小晴一句话点出这场视角交换里值得留下的东西。"
        )
    if stage_id == "heart":
        return head + (
            "真心话。流程：\n"
            "1) 小晴一句话开启（'散场之前……'之类的语气）。\n"
            "2) 四位成员依次对真人同学说一句来自今晚的真心话（每人20-40字，"
            "只对同学说；可以说自己的变化或想送出的话；不建议、不提问）。\n"
            "3) 小晴一句轻轻收住。"
        )
    if stage_id == "close":
        return head + (
            "收夜。流程：\n"
            "1) 小晴做总结（不超过4句）：必须呼应真人同学今晚真实说过的内容，"
            "不得虚构他说过的话；点出一两位成员带来的东西；结尾开放而温柔。\n"
            "2) 四位成员各一句道别（15-30字）。\n"
            "3) 小晴最后说明：稍后会为这位同学生成一份今晚的《成长手记》。"
        )
    raise ValueError(f"unknown chat stage {stage_id}")


def _painting_stage_instruction(stage_id: str, theme: dict, team: list[dict]) -> str:
    bt_hist, dp_hist, bt_peer, qr_peer = [m["name"] for m in team]
    pc = theme.get("painting", {})
    head = f"本轮是第{theme.get('_round', '?')}/8轮 · "
    if stage_id == "ignite":
        return head + (
            "开场。同学已确认阵容，画会正式开始：\n"
            "1) 小晴俏皮地宣布画会开始（一句）。\n"
            f"2) 小晴起第一笔，定下画面的底色与氛围：以「我希望画上有」开头，"
            f"20-40字，与主题「{theme['label']}」相关。\n"
            "3) 小晴预告：接下来四位桌友会依次添笔，然后轮到同学（一句带过，不提问）。"
        )
    if stage_id == "strokes":
        return head + (
            f"落笔。今晚的画{pc.get('framing', '')}。四位成员依次各添一笔：\n"
            "1) 每人一句，以「我希望画上有」或「我想在这幅画上加上」开头，20-40字；"
            "笔触要和自己的心事有关——画的是画，也是自己。\n"
            "2) 不得删除、否定或覆盖前面人的笔触。\n"
            "3) 最后小晴邀请真人同学落笔：「现在轮到你——你想在这幅画上添什么？"
            "一笔就好，什么都行。」并温和提醒：你落笔后，画作会慢慢显影，大约需要半分钟。"
        )
    if stage_id == "reveal":
        return head + (
            "画作揭晓。真人同学刚落完自己那一笔。本轮只有小晴发言：\n"
            "1) 先接住同学这一笔（不评价好坏，只接住它放进画里的感觉）。\n"
            "2) 宣布：大家的笔触已经合在一起，画作正在显影/已经完成（系统会把画作图片单独呈现）。\n"
            "3) 提问："
            f"「{pc.get('reflection_1', '第一次看到这幅完成的画，你的第一感受是什么？')}」\n"
            f"注意：{LEADER_NAME}不要替同学描述画面的样子，画作会以图片呈现。"
        )
    if stage_id == "resonance":
        return head + (
            "画边回响。真人同学说了看到画作的第一感受。流程：\n"
            f"1) {dp_hist}、{bt_peer}、{qr_peer} 依次回应：引用今晚大家的笔触（说得出谁画了什么），"
            "讲这些笔触放在一起时自己心里的感觉；不评价真人同学那一笔的好坏。\n"
            "2) 小晴接住同学的第一感受，再提问："
            f"「{pc.get('reflection_2', '画下你自己那一笔的时候，你心里在想什么？')}」"
        )
    if stage_id == "meaning":
        return head + (
            "笔触心声。真人同学说了画下自己那一笔时的心情。流程：\n"
            f"1) {bt_hist} 和 {dp_hist} 依次表露自己那一笔背后的心事（50-80字，有细节："
            "为什么画它、它连着自己的哪段经历，最后一句落回坐在炉边的此刻）。\n"
            "2) 小晴轻轻收束今晚的画与心事（2句以内，不再提问）。"
        )
    raise ValueError(f"unknown painting stage {stage_id}")


def build_turn_system_prompt(
    leader_cfg: dict,
    team: list[dict],
    stage_id: str,
    theme: dict,
    stages_cfg: dict,
    round_no: int,
    form: str = "chat",
    painting_stages_cfg: dict | None = None,
) -> str:
    theme = {**theme, "_round": round_no}
    if form == "painting":
        task = _painting_stage_instruction(stage_id, theme, team)
        ordered = stage_id in ("strokes", "heart")
    else:
        task = _chat_stage_instruction(stage_id, theme, team)
        ordered = stage_id in ("heart", "close")
    speakers = [f"【{LEADER_NAME}】"] + [f"【{m['name']}】" for m in team]
    return "\n\n".join(
        [
            "你是\"清心圆桌\"的导演。清心圆桌是一个明亮温暖的线上朋辈支持空间：一位真人同学"
            "和几位AI成员围桌而坐。你的唯一任务：根据当前进度和真人同学的最后发言，"
            "写出本轮的团体对话（剧本式群聊）。",
            "【空间设定】一间暖融融的小屋，圆桌上茶炉咕嘟着热气。氛围安全、松弛、不评判，像老友围坐谈心。",
            "【你要扮演的角色】",
            _leader_block(leader_cfg),
            *[_member_block(m) for m in team],
            "【团体铁律】\n"
            "- 所有人的发言里不得出现这些词：治愈、诊断、治疗、心理咨询、你一定会好\n"
            "- 成员只讲自己的经历与感受，不评价用户、不建议、不提问\n"
            "- 历史成员不得虚构史实，不得使用自己时代之后的事物\n"
            "- 虚构同龄成员不得提具体校名，说\"我们学校\"即可\n"
            "- 成员之间可以互相轻轻呼应，但不要抢话",
            "【本轮任务】\n" + task,
            "【输出格式（必须严格遵守）】\n"
            "- 每个发言独占一行，格式：`【角色名】发言内容`\n"
            f"- 本轮只有这些角色发言、顺序也按此：{' → '.join(speakers) if ordered else '按任务说明'}\n"
            "- 不写旁白、不写标题、不用markdown加粗、不输出任何解释\n"
            f"- 全轮发言合计不超过{TURN_TOTAL_LIMIT}字",
        ]
    )


def build_turn_user_content(
    history_digest: str,
    user_msg: str,
    extra_instruction: str = "",
) -> str:
    parts = []
    if history_digest:
        parts.append("【对话记录（摘要）】\n" + history_digest)
    parts.append("【真人同学刚才说】\n" + (user_msg.strip() or "（沉默，没说话）"))
    if extra_instruction:
        parts.append("【特别提示（优先级最高）】\n" + extra_instruction)
    parts.append("现在写出本轮团体对话。只输出对话行。")
    return "\n\n".join(parts)


def build_report_system_prompt(leader_cfg: dict, theme: dict) -> str:
    return "\n\n".join(
        [
            "你是\"清心圆桌\"的导演。本轮只有小晴一个人发言，生成本次圆桌的《成长手记》。",
            _leader_block(leader_cfg),
            "【输出格式（必须严格遵守，逐行输出）】",
            "【小晴】（把一份手写便签放到你手边）这是今晚的成长手记——",
            "🕯 今晚主题：…",
            "🪵 你带来的：…（2-3句，只能基于对话记录里真人同学真实说过的话，不得虚构）",
            "🔥 炉边的回响：…（2-3句，概括成员们带来的共鸣与视角，点到1-2位成员的名字）",
            "✨ 值得带走的：…（3句话，每句一行，以 · 开头，可化用今晚对话里的表达）",
            "🌱 留给下次的：…（1句开放式邀请，不说教、不布置任务）",
            "【规则】全文300字以内；不得出现：治愈、诊断、治疗、心理咨询；"
            "不承诺改变；语气像朋友写的便签，不是报告。",
            f"【今晚主题】{theme['full_label']}；报告侧重：{theme['report_focus']}。",
        ]
    )


def validate_turn(text: str, allowed_names: list[str], min_members: int = 0) -> list[str]:
    """输出校验（流式路径用于事后监测，非流式路径用于重试决策）。"""
    issues: list[str] = []
    names = LINE_RE.findall(text)
    if not names:
        issues.append("no-speaker-lines")
        return issues
    allowed = set(allowed_names)
    member_lines = 0
    for name in names:
        if name not in allowed:
            issues.append(f"unknown-speaker:{name}")
        elif name != LEADER_NAME:
            member_lines += 1
    if member_lines < min_members:
        issues.append(f"too-few-members:{member_lines}<{min_members}")
    for word in FORBIDDEN_WORDS:
        if word in text:
            issues.append(f"forbidden-word:{word}")
    if LEADER_NAME not in names:
        issues.append("leader-missing")
    if len(text) > TURN_TOTAL_LIMIT * 1.6:
        issues.append("too-long")
    for line in text.splitlines():
        m = LINE_RE.search(line)
        if m and m.group(1) != LEADER_NAME and len(line) > 150:
            issues.append(f"member-line-too-long:{m.group(1)}")
            break
    return issues
