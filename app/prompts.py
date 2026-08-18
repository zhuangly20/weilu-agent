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
FORBIDDEN_WORDS = ("治愈", "诊断", "你一定会好", "心理咨询", "治疗", "抑郁症", "今晚")

# 每轮发言总字数上限（report 轮除外）
TURN_TOTAL_LIMIT = 480

LINE_RE = re.compile(r"【([^】]+)】")

GREETING_TEXT = """（推开玻璃门，阳光暖房里热茶正温，圆桌边刚好留了一个座位）

【小晴】你好呀，同学～我是**小晴**，小清心的AI团体助手化身，也是「清心圆桌」的小主人。这里有阳光、热茶和一桌好聊伴——没有评判，也没有建议轰炸。

【小晴】今天的圆桌有这些活动，想参加哪一场？
1️⃣ **自我探索·清心圆桌**——"我是谁、我想要什么"
2️⃣ **减压安心之旅·清心圆桌**——学业、科研、生活，把压力放到圆桌上
3️⃣ **新生适应·清心圆桌**——想家、宿舍、新环境
5️⃣ **就业迷茫·清心圆桌**——工作、升学、Gap
🎨 **圆桌画会**——任选一个主题，大家用文字轮流添笔，共同完成一幅真正的画（结束送给你）

每一场都会为你挑选几位AI桌友围坐陪伴；活动结束后，你还会收到一份根据本场内容写给你的成长报告✨

回复数字或活动名就能报名；也可以直接跟我说说最近的困惑，我来帮你挑一场最合适的～

（小声说：清心圆桌是朋辈支持空间，不是心理治疗哦。如果你正处于难以承受的痛苦中，请一定联系学校心理中心或专业援助热线。）"""

MENU_RETRY_TEXT = """【小晴】（歪着头）桌边有点热闹，我没太听清你想参加哪一场——
1️⃣ 自我探索　2️⃣ 减压安心之旅　3️⃣ 新生适应　5️⃣ 就业迷茫　🎨 圆桌画会

回复数字或活动名就行；或者，直接跟我讲讲最近的心事也可以～"""

SEAT_REASK_TEXT = """【小晴】（歪头）没太听清——你是想换一位桌友，还是准备开场啦？
想换谁就点名告诉我（比如"换掉苏轼"）；不换的话说声"开始"，我们马上开始～"""

NO_MORE_SWAP_TEXT = """【小晴】（挠挠头）有点不好意思，剩下的桌友已经是我今天能请到的全部啦……
要不我们先开始试试？聊上两句要是真的不合拍，再回来找我商量～就这么定？"""

ENDED_TEXT = """【小晴】茶要凉啦，谢谢你来～
想再开一场的话，跟我说声\"再来一场\"，或者直接说下一场想聊的主题。圆桌随时可以重新坐满。"""

LLM_FALLBACK_TEXT = """【小晴】（碰了碰茶杯，抬眼笑了笑）刚才那句话我没有接稳。能再对我说一遍吗？"""


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
        "承诺'会好起来'；每轮最多出现一个问句，且只向真人同学提问。\n"
        "共情补充规则：共情说法每轮都要换，严禁重复同一句式"
        "（尤其不许反复使用\"你把…说出来，边界感很清楚\"这类模板）；"
        "多用正常化回应（\"这个阶段很多同龄人都这样，是压力下的正常反应\"）；"
        "同学质疑或批评时，先具体承认自己哪一句没接好，不辩解、"
        "不把批评反弹回同学身上。"
    )


# ---------- 相邀 / 换人（小晴主持介绍，团友本人各亮相一句） ----------


def build_invite_system_prompt(
    leader_cfg: dict,
    team: list[dict],
    theme: dict,
    form: str,
) -> str:
    """第1轮·相邀：方案介绍 + 桌友亮相 + 换人询问。

    intro_style=leader_brief（减压安心之旅）：四位桌友本轮不发言，
    由小晴用一段话向同学简要介绍（详细的第一人称自我介绍留到开场轮）。
    """
    intro_names = "、".join(m["name"] for m in team)
    theme_label = theme["full_label"]
    leader_brief = theme.get("intro_style") == "leader_brief"
    has_warm_opening = bool(str(theme.get("warm_opening") or "").strip())
    if form == "painting":
        scheme = (
            f"- 玩法：**圆桌画会**（{theme_label}）。不用画笔——大家在圆桌边坐定，"
            "用文字轮流往同一幅画上添一笔（每笔都连着自己的心事），"
            "所有笔触最后会被真正画成一幅完整的画，结束时送给同学\n"
            "- 节奏：每轮小晴会请两三位不同的桌友发言，不是每轮全员都说话；"
            "想听谁多说两句，随时点名\n"
            "- 时长：大约15-20分钟，共8轮环节\n"
            "- 收获：一幅大家一起完成的画 + 一份根据本场内容写给你的成长报告"
        )
    else:
        scheme = (
            f"- 玩法：**{theme_label}·夜话**。大家围着圆桌文字畅聊，"
            "一共8轮环节，从相识到真心话，每轮都轻巧不费力\n"
            "- 节奏：每轮小晴会请两三位不同的桌友发言，不是每轮全员都说话；"
            "想听谁多说两句，随时点名\n"
            "- 时长：大约10-15分钟\n"
            "- 收获：一份根据本场对话写给你的成长报告和一张专属明信片✨（先卖个关子～）"
        )
    blocks = [
        "你是\"清心圆桌\"的导演。本轮完成\"介绍与相邀\"。",
        "【空间设定】一间洒满阳光的玻璃暖房，圆桌上热茶正温。氛围安全、松弛、不评判。",
        _leader_block(leader_cfg),
    ]
    if leader_brief:
        blocks += [
            _member_block(m) + "\n（你本轮任务：保持安静，不发言。小晴会向同学介绍你；"
            "你的详细自我介绍留到下一轮。）"
            for m in team
        ]
        steps = ["1) 如果同学刚说了近况或困惑：先用1-2句温柔接住情绪，再自然引出"
                 "\"这一场应该很适合你\"；如果只是报了活动名，就直接俏皮地欢迎～"]
        if not has_warm_opening:
            steps.append(
                "2) 轻轻带一句声明（自然语气，不严肃）：\"小声说，这里是朋辈支持的小团体，"
                "不是治疗哦——不过大家的认真程度不打折。\""
            )
        steps += [
            f"{len(steps) + 1}) 介绍本场方案（轻快但说清楚）：\n{scheme}",
            f"{len(steps) + 2}) 小晴用一段话把四位桌友介绍给同学：{intro_names}，"
            "每人一句（TA是谁、为什么请TA来这一场；信息从上面人物卡提炼，口吻轻快，"
            "不说\"他能帮助你\"这类定位，只说\"他为什么在\"）",
            f"{len(steps) + 3}) 小晴结尾问：\"这几位桌友合你眼缘吗？想换掉谁跟我说一声就行；"
            "不换的话，我们马上开始，正式认识一下大家～\"",
        ]
        blocks += [
            "【本轮任务】第1/8轮 · 相邀。流程：\n" + "\n".join(steps),
            "【输出格式（必须严格遵守）】\n"
            f"- 本轮只有【{LEADER_NAME}】发言，每段话单独一行，以【{LEADER_NAME}】开头\n"
            "- 全轮合计260-380字；不写旁白、不用markdown加粗\n"
            "- 不得出现\"今晚/深夜/夜里\"等夜晚时间词，统一用\"这场/今天/本场\"\n"
            "- 介绍桌友只介绍其人，不评价同学、不提问\n"
            "- 禁止出现：治愈、诊断、治疗、心理咨询",
        ]
    else:
        blocks += [
            _member_block(m) + "\n（你本轮任务：向同学自我介绍一句，25-45字——我是谁、身份背景、"
            "今天为什么坐在桌边。第一人称、本人语气，不提问、不建议、不评价同学）"
            for m in team
        ]
        blocks += [
            "【本轮任务】第1/8轮 · 相邀。流程：\n"
            "1) 如果同学刚说了近况或困惑：先用1-2句温柔接住情绪，再自然引出"
            "\"这一场应该很适合你\"；如果只是报了活动名，就直接俏皮地欢迎～\n"
            f"2) 介绍本场方案（轻快但说清楚）：\n{scheme}\n"
            "3) 轻轻带一句声明（自然语气，不严肃）：\"小声说，这里是朋辈支持的小团体，"
            "不是治疗哦——不过大家的认真程度不打折。\"\n"
            f"4) 转场引出团友：\"本次为你召唤的AI团友是——\"，"
            f"然后{intro_names}按上面顺序各说一句自我介绍（每人单独一行）\n"
            "5) 小晴结尾问：\"这几位桌友合你眼缘吗？想换掉谁跟我说一声就行；"
            "不换的话，我们马上开始啦～\"",
            "【输出格式（必须严格遵守）】\n"
            f"- 小晴的每段话单独一行，以【{LEADER_NAME}】开头；"
            "每位桌友的自我介绍各占一行，以【姓名】开头\n"
            "- 全轮合计260-380字；不写旁白、不用markdown加粗\n"
            "- 不得出现\"今晚/深夜/夜里\"等夜晚时间词，统一用\"这场/今天/本场\"\n"
            "- 桌友介绍只谈自己（身份背景+今天带来什么），不评价同学、不提问\n"
            "- 禁止出现：治愈、诊断、治疗、心理咨询",
        ]
    return "\n\n".join(blocks)


def build_encore_system_prompt(leader_cfg: dict, theme_label: str) -> str:
    """散场后追问：小晴单独回应同学的问题（无标记，状态停在 ENDED）。"""
    return "\n\n".join(
        [
            "你是\"清心圆桌\"的带领者小晴。本场团体已经散场（成长手记和明信片都发出过了），"
            "同学散场后又发来一条消息。只有你一个人回应，不重新开启团体流程。",
            _leader_block(leader_cfg),
            "【本轮任务】回应同学这条消息，2-4句：\n"
            "1) 先正面回答同学的疑问或接住情绪（不知道答案就诚实说不知道）。\n"
            "2) 常见问题可以这样答：问报告/明信片——在上一条\"成长报告\"消息的下方附件卡片里，"
            "点开就能看；想继续聊——说声\"再来一场\"或直接说下一个主题，就能重新开桌。\n"
            f"3) 如果同学还想和某位桌友说话，温和说明可以下一场点名请TA来"
            f"（本场主题：{theme_label}）。",
            "【输出格式】只有【小晴】发言，一行或几行均可，合计不超过90字；"
            "不写旁白、不用markdown；不得出现\"今晚/深夜/夜里\"等夜晚时间词；"
            "禁止出现：治愈、诊断、治疗、心理咨询。",
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
        f"3) 小晴介绍新加入的{names_arr}（1-2句：姓名+背景+为什么请TA）\n"
        f"4) 新成员说一句入座招呼\n"
        "5) 小晴结尾再问：\"还有想换的吗？没有的话我们马上开始啦～\""
    )
    if note:
        task = note + "\n" + task
    return "\n\n".join(
        [
            "你是\"清心圆桌\"的导演。本轮完成一次桌友更换。",
            "【空间设定】一间洒满阳光的玻璃暖房，圆桌上热茶正温。",
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
        if theme.get("ignite_variant") == "intro_weather":
            return head + (
                "开场。同学已确认阵容，正式开始：\n"
                "1) 小晴先介绍并开启本环节（对同学说，1-2句）：这是「自我介绍与天气站」——"
                "大家先互相认识：每位桌友做自我介绍、说说期待，再报一句自己现在的"
                "\"内心天气\"（比如\"多云转晴\"），顺便预告：每轮会请两三位不同的桌友发言，"
                "想听谁多说随时点名。\n"
                "2) 四位成员依次第一人称自我介绍（每人40-60字：我是谁/我为什么来/"
                "我对这场团体的期待），结尾各报一句自己的内心天气（具象、不沉重、不提问）。\n"
                f"3) 小晴转向同学，提出本场的破冰问题：\n「{theme['icebreak']}」\n"
                "4) 小晴顺口请同学给压力打个分（0-10，0是完全没压力，10是快撑不住；"
                "报个数字就行，不想打也完全可以）。\n"
                "小晴的问句只保留破冰问题和打分邀请这两处。"
            )
        return head + (
            "开场。同学已确认阵容，正式开始：\n"
            "1) 小晴先介绍并开启本环节（对同学说，1-2句）：这是「相似圈」热身——"
            "大家轮流说\"我想知道谁和我一样……\"，找找彼此的共同点、先放松下来；"
            "稍后还会请同学给此刻的心情打个分（1-10，可选）。"
            "同时预告：每轮会请两三位不同的桌友发言，想听谁多说随时点名。\n"
            "2) 玩一轮\"相似圈\"热身：四位成员依次各说一句"
            "\"我想知道谁和我一样，……\"（每人12-25字，说一个自己真实的小习惯、"
            "小窘境或小期待，中性或正向，不提问、不建议）。\n"
            f"3) 小晴提出本场的破冰问题：\n「{theme['icebreak']}」\n"
            "4) 小晴顺口请同学给此刻心情打个分（1-10分，报个数字就行，"
            "不想打也完全可以）。\n"
            "小晴的问句只保留破冰问题和打分邀请这两处。"
        )
    if stage_id == "share":
        return head + (
            "圆桌入话。真人同学刚回答了破冰问题。流程：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：这是「主题分享」，"
            "想先听听同学的想法，让桌友们更了解你；然后用一两句接住同学刚才的回答，"
            "点出其中可观察的特质（不评判用词选择）。\n"
            f"2) {bt_peer} 和 {qr_peer} 各用一句自己的经历共鸣（是'我也…'，不是夸用户）。\n"
            f"3) 小晴自然引出本场的主活动，向真人同学提问：\n「{theme['activity']}」\n"
            "引出时必须用半句话点明这个活动和本场主题的关联"
            "（比如\"老地方的东西会跟着我们来新环境，我们正想听听你的\"），"
            "让同学明白这不是偏题。"
        )
    if stage_id == "depth":
        normalize = (
            "然后必须做一次正常化回应（用自己的话，比如\"压力大的时候有这些反应，"
            "是压力下的正常反应，很多同龄人都经历过这一段\"；每场只此一次、"
            "严禁复读模板句式）。\n"
            if theme.get("normalize_in_depth") else ""
        )
        return head + (
            "圆桌深谈。真人同学刚完成主活动分享。流程：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：接下来想往深里聊一点，"
            "请同学就刚才的事再讲一个具体的细节或感受；然后接住分享里最真实的情绪，"
            f"{normalize}"
            f"2) {bt_hist} 和 {dp_hist} 各分享一段自己相关的经历（50-80字，有细节，"
            "最后一句落回'此刻坐在桌边'的感受）。\n"
            f"3) 小晴简单小结，再抛出收尾问题：\n「{theme['closing_question']}」"
        )
    if stage_id == "persp":
        if theme.get("persp_variant") == "breathing":
            return head + (
                "呼吸站·给压力换个讲法。流程：\n"
                "1) 小晴先介绍并开启本环节（对同学说，1-2句）：接下来是「呼吸站」——"
                "把压力先放一放，跟小晴做一分钟腹式呼吸；做完，桌友再给你的压力"
                "换一种讲法。如果此刻不方便深呼吸，跟着看就好，不用勉强。\n"
                "2) 小晴用4-6行慢节奏短句带呼吸引导（每行都以【小晴】开头，一行一事）："
                "先请同学坐稳、把肩膀松下来；然后\"用鼻子慢慢吸气——1、2、3\"→"
                "\"停一下\"→\"用嘴慢慢呼气——1、2、3\"→再带一轮→"
                "末句\"动动手指和脚趾，慢慢回来\"。\n"
                f"3) {dp_hist} 用白话讲一个道理并给同学的压力换个讲法（50-80字）："
                "人一慌，情绪脑会把负责思考的脑区挤下线，所以会觉得\"脑子空白、"
                "什么都不行\"；刚才的呼吸就是把思考的部分请回来。"
                f"{theme['persp_guidance']}\n"
                f"4) {bt_peer} 用一句自己的话补充共鸣或佐证。\n"
                "5) 小晴一句轻轻收束。"
            )
        return head + (
            "交换视角。真人同学回答了收尾问题。流程：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：这是「交换视角」，"
            "请桌友们从各自的经历出发，给同学看到的这件事另一种讲法。\n"
            f"2) {dp_hist} 就本场听到的整个分享，用自己独有的视角给真人同学一种"
            f"'换一种讲法'的回应（50-80字）。{theme['persp_guidance']}\n"
            f"3) {bt_peer} 用一句自己的话补充共鸣或佐证。\n"
            "4) 小晴一句话点出这场视角交换里值得留下的东西。"
        )
    if stage_id == "heart":
        return head + (
            "真心话。流程：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：散场之前是「真心话」环节，"
            "每位桌友想对同学说一句心里话。\n"
            "2) 四位成员依次对真人同学说一句来自本场圆桌的真心话（每人20-40字，"
            "只对同学说；可以说自己的变化或想送出的话；不建议、不提问）。\n"
            "3) 小晴一句轻轻收住。"
        )
    if stage_id == "close":
        coping = (
            "（每人15-30字：一句自己的减压小方法——从自己的经历和人设里长出来，"
            "一说就能懂，比如\"我会去做一道菜，慢慢等它熟\"）"
            if theme.get("leave_coping_tip") else "（15-30字）"
        )
        return head + (
            "临别赠言。流程：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：这是「总结与告别」，"
            "一起回顾这一场，再好好道别。\n"
            "2) 小晴做总结（不超过4句）：必须呼应真人同学本场真实说过的内容，"
            "不得虚构他说过的话；点出一两位成员带来的东西；结尾开放而温柔。\n"
            f"3) 四位成员各一句道别{coping}。\n"
            "4) 小晴最后说明：稍后会为这位同学生成一份本场的成长报告。"
        )
    raise ValueError(f"unknown chat stage {stage_id}")


def _painting_stage_instruction(stage_id: str, theme: dict, team: list[dict]) -> str:
    bt_hist, dp_hist, bt_peer, qr_peer = [m["name"] for m in team]
    pc = theme.get("painting", {})
    head = f"本轮是第{theme.get('_round', '?')}/8轮 · "
    if stage_id == "ignite":
        return head + (
            "开场。同学已确认阵容，画会正式开始：\n"
            "1) 小晴先介绍并开启本环节（对同学说，1句）：画会开场，"
            "先由小晴给这幅画定一个底色与氛围。\n"
            f"2) 小晴起第一笔，定下画面的底色与氛围：以「我希望画上有」开头，"
            f"20-40字，与主题「{theme['label']}」相关。\n"
            "3) 小晴预告：接下来四位桌友会依次添笔，然后轮到同学（一句带过，不提问）。"
        )
    if stage_id == "strokes":
        return head + (
            f"落笔。这场画会的画{pc.get('framing', '')}。四位成员依次各添一笔：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：这是「落笔」环节，"
            "四位桌友依次往同一幅画上添一笔，每人一笔、一笔一个心事。\n"
            "2) 每人一句，以「我希望画上有」或「我想在这幅画上加上」开头，20-40字；"
            "笔触要和自己的心事有关——画的是画，也是自己。\n"
            "3) 不得删除、否定或覆盖前面人的笔触。\n"
            "4) 最后小晴邀请真人同学落笔：「现在轮到你——你想在这幅画上添什么？"
            "一笔就好，什么都行。」并温和提醒：你落笔后，画作会慢慢显影，大约需要半分钟。"
        )
    if stage_id == "reveal":
        return head + (
            "画作揭晓。真人同学刚落完自己那一笔。本轮只有小晴发言：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：这是「画作揭晓」，"
            "大家的笔触合在一起了，一起看看完成的画。\n"
            "2) 先接住同学这一笔（不评价好坏，只接住它放进画里的感觉）。\n"
            "3) 宣布：大家的笔触已经合在一起，画作正在显影/已经完成（系统会把画作图片单独呈现）。\n"
            "4) 提问："
            f"「{pc.get('reflection_1', '第一次看到这幅完成的画，你的第一感受是什么？')}」\n"
            f"注意：{LEADER_NAME}不要替同学描述画面的样子，画作会以图片呈现。"
        )
    if stage_id == "resonance":
        return head + (
            "画边回响。真人同学说了看到画作的第一感受。流程：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：这是「画边回响」，"
            "请桌友们说说看到大家笔触放在一起时的感受。\n"
            f"2) {dp_hist}、{bt_peer}、{qr_peer} 依次回应：引用大家刚才的笔触（说得出谁画了什么），"
            "讲这些笔触放在一起时自己心里的感觉；不评价真人同学那一笔的好坏。\n"
            "3) 小晴接住同学的第一感受，再提问："
            f"「{pc.get('reflection_2', '画下你自己那一笔的时候，你心里在想什么？')}」"
        )
    if stage_id == "meaning":
        return head + (
            "笔触心声。真人同学说了画下自己那一笔时的心情。流程：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：这是「笔触心声」，"
            "请桌友们各自说说自己那一笔背后的心事。\n"
            f"2) {bt_hist} 和 {dp_hist} 依次表露自己那一笔背后的心事（50-80字，有细节："
            "为什么画它、它连着自己的哪段经历，最后一句落回坐在桌边的此刻）。\n"
            "3) 小晴轻轻收束这场画会的画与心事（2句以内，不再提问）。"
        )
    if stage_id == "heart":
        return head + (
            "真心话。流程：\n"
            "1) 小晴先介绍本环节（对同学说，1句）：散场之前是「真心话」环节，"
            "每位桌友想对同学说一句心里话。\n"
            "2) 四位成员依次对真人同学说一句来自本场圆桌的真心话（每人20-40字，"
            "只对同学说；可以说自己的变化或想送出的话；不建议、不提问）。\n"
            "3) 小晴一句轻轻收住。"
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
            "【空间设定】一间洒满阳光的玻璃暖房，圆桌上热茶正温。氛围安全、松弛、不评判，像老友围坐谈心。",
            "【你要扮演的角色】",
            _leader_block(leader_cfg),
            *[_member_block(m) for m in team],
            "【团体铁律】\n"
            "- 所有人的发言里不得出现这些词：治愈、诊断、治疗、心理咨询、你一定会好\n"
            "- 不得出现\"今晚/深夜/夜里\"等夜晚时间词，统一用\"这场/今天/本场\"\n"
            "- 成员只讲自己的经历与感受，不评价用户、不建议、不提问\n"
            "- 如果同学直接向某位成员提问，该成员先正面回应这个问题"
            "（简短、真诚，可以只说自己的真实感受），再分享自己的经历；"
            "任何成员不评判带领者和其他成员\n"
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
    if theme.get("report_variant") == "html":
        return "\n\n".join(
            [
                "你是\"清心圆桌\"的导演。本轮只有小晴一个人。任务：基于对话记录，"
                "为同学提取本场成长报告的内容素材。只输出一个 JSON 对象，"
                "不加任何解释、不用 markdown 代码块、首尾不留空白符以外的文字。",
                _leader_block(leader_cfg),
                "【JSON 结构（字段名与类型必须完全一致）】",
                "{",
                "\"leader_note\": \"小晴对同学说的话，80字内，像把一份小报告轻轻递到同学手边（口吻温暖，含一个具体呼应）\",",
                "\"pressure_note\": \"一句话概括同学带来的压力，30字内，只能基于对话记录\",",
                "\"review\": [\"环节要点\", \"...\"]（4-8条，每条15字内，按环节顺序）,",
                "\"member_tips\": [{\"name\": \"成员名\", \"tip\": \"TA道别时留下的减压小方法，20字内\"}]（4条，来自对话记录）,",
                "\"takeaways\": [\"减压建议\", \"...\"]（恰好3条，每条20字内，只能化用本场对话里出现过的内容，不得引入外来建议）,",
                "\"encouragement\": \"想对同学说的鼓励，40字内，温暖、不说教、不承诺改变\",",
                "\"pressure_before\": 同学开场报过的压力分数（0-10的整数），没有则填 null",
                "}",
                "【硬规则】不得出现：治愈、诊断、治疗、心理咨询；不得虚构同学没说过的话；"
                "成员没留下减压方法时，tip 里写TA本场说过一句代表的话。",
                f"【本场主题】{theme['full_label']}；提取侧重：{theme['report_focus']}。",
            ]
        )
    return "\n\n".join(
        [
            "你是\"清心圆桌\"的导演。本轮只有小晴一个人发言，生成本次圆桌的《成长手记》。",
            _leader_block(leader_cfg),
            "【输出格式（必须严格遵守，逐行输出）】",
            "【小晴】（把一份手写便签放到你手边）这是今天的成长手记——",
            "🕯 本场主题：…",
            "🪵 你带来的：…（2-3句，只能基于对话记录里真人同学真实说过的话，不得虚构）",
            "🔥 桌友们的回响：…（2-3句，概括成员们带来的共鸣与视角，点到1-2位成员的名字）",
            "✨ 值得带走的：…（3句话，每句一行，以 · 开头，可化用本场对话里的表达）",
            "🌱 留给下次的：…（1句开放式邀请，不说教、不布置任务）",
            "【规则】全文300字以内；不得出现：治愈、诊断、治疗、心理咨询；"
            "不承诺改变；语气像朋友写的便签，不是报告。\n"
            "如果对话记录里同学报过心情分数（1-10打分），在\"值得带走的\"之后"
            "加一行\"🌡 心情温度：开场X分 → 现在？\"（？处可温和地留给同学自己填）；"
            "同学没打过分数就完全不要提。",
            f"【本场主题】{theme['full_label']}；报告侧重：{theme['report_focus']}。",
        ]
    )


def build_resource_card(resources: dict, public_base: str) -> str:
    """呼吸站末尾的资源卡片（固定文本，代码追加，URL 永不经 LLM 之手）。"""
    lines = ["🔗 想再练一次？"]
    if resources.get("breathing_video"):
        lines.append(f"· 腹式呼吸教学视频：{resources['breathing_video']}")
    if resources.get("mindfulness_audio"):
        lines.append(f"· 一分钟正念音频：{resources['mindfulness_audio']}")
    if resources.get("three_brains_path"):
        lines.append(f"· 为什么压力会\"脑子空白\"？一张图看懂：{public_base}{resources['three_brains_path']}")
    return "\n".join(lines)


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
