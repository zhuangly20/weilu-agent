"""导演编排器：8轮制流程（相邀→开场→6轮正式）+ 换人 + 组队 + 历史压缩。

plan_turn() 是纯函数（可测试），返回本轮计划；
执行（LLM调用/流式/生图/明信片）由 main.py 按 plan 进行。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from . import group_v2, panel, painting_studio, prompts, safety
from .config import (
    ProviderConfig,
    load_characters,
    load_group_theme_config,
    load_group_v2_config,
    load_settings,
    load_theme_config,
)
from .llm import generate, stream_generate

logger = logging.getLogger(__name__)
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


def _stage_label(theme: dict, stage_id: str, stages: dict) -> str:
    """优先采用主题专属环节名，其他主题沿用共用阶段标签。"""
    return str((theme.get("stage_labels") or {}).get(stage_id) or stages[stage_id]["label"])


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
        if plan.meta.get("progress_note"):
            parts.append(plan.meta["progress_note"])
        elif plan.meta.get("v2"):
            parts.append(f"📍 {plan.meta.get('stage_label') or '团体讨论'} · 小晴会带领转场")
        else:
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


def _report_fields_policy_issues(fields: dict) -> list[str]:
    """报告字段会进入 HTML 与明信片，因此在渲染前统一检查。"""
    return prompts.forbidden_word_issues(json.dumps(fields, ensure_ascii=False))


def _v2_opening_handoff_ok(text: str, plan: "TurnPlan") -> bool:
    """Opening interaction must visibly hand the floor from leader to one peer."""
    if not (plan.meta.get("v2") and plan.meta.get("round") == 1
            and plan.meta.get("action") == "discuss"):
        return True
    speakers = re.findall(r"^【([^】]+)】", text, re.MULTILINE)
    if prompts.LEADER_NAME not in speakers or not speakers:
        return False
    last_leader = len(speakers) - 1 - speakers[::-1].index(prompts.LEADER_NAME)
    if last_leader >= len(speakers) - 1 or speakers[-1] not in plan.meta.get("team", []):
        return False
    final_block = text.rsplit(f"【{speakers[-1]}】", 1)[-1]
    return "？" in final_block or "?" in final_block


async def _generate_v2_opening_handoff(
    plan: "TurnPlan", providers: list[ProviderConfig], messages: list[dict[str, str]],
) -> str:
    """Buffer and validate the one turn where a missing cue strands the user."""
    last = ""
    for attempt in range(2):
        current = messages
        if attempt:
            current = [
                messages[0],
                {"role": "user", "content": messages[1]["content"] + (
                    "\n\n上一次结构不完整，请重写整轮：团友短回应后，小晴总结并明确交棒；"
                    "最后必须由一位团友向真人问一个具体、容易回答的问题。"
                )},
            ]
        last = await generate(providers, current, temperature=0.72, max_tokens=800)
        if _v2_opening_handoff_ok(last, plan):
            return last
    # Deterministic last resort: never leave the participant without a turn cue.
    peer = (plan.meta.get("team") or ["团友"])[0]
    return last.rstrip() + (
        "\n【小晴】我先收一下：大家都从自己的经历靠近了你，也看见你愿意把几重压力说出来。"
        f"我把话筒交给{peer}，我们先沿着一条线听。"
        f"\n【{peer}】你刚才说的几件事里，哪一件最让你想先讲给我们听？可以慢慢想，也可以继续听或停一下。"
    )


def _v2_advance_issues(text: str, plan: "TurnPlan") -> list[str]:
    """转场文本不达标的具体原因；为空即通过。"""
    if not (plan.meta.get("v2") and plan.meta.get("action") == "advance"):
        return []
    issues: list[str] = []
    label = str(plan.meta.get("stage_label") or "")
    speakers = re.findall(r"^【([^】]+)】", text, re.MULTILINE)
    if label not in text or prompts.LEADER_NAME not in speakers:
        issues.append("missing-stage-label-or-leader")
    if any(s not in {prompts.LEADER_NAME, *plan.meta.get("team", [])} for s in speakers):
        issues.append("unknown-speaker")
    if not any(s in plan.meta.get("team", []) for s in speakers):
        issues.append("no-peer-line")
    phase = int(plan.meta.get("round") or 0)
    team = list(plan.meta.get("team") or [])
    if phase == 2 and sum(f"【{name}】" in text for name in team) < 2:
        issues.append("phase2-needs-2-peers")
    if phase == 2:
        # 结构性要求（阶段名在场、两位团友示范）已保证转场不吞第一段聚焦；
        # 交还话术不做枚举匹配（"想对一帆说的"这类自然表达枚不完），
        # 小晴任意一行出现问句或提到"你"即视为把话筒交还真人。
        leader_lines = [l for l in text.splitlines() if l.startswith(f"【{prompts.LEADER_NAME}】")]
        if not any("？" in l or "?" in l or "你" in l for l in leader_lines):
            issues.append("phase2-no-handover-to-user")
    if phase == 3 and ("共同" not in text or sum(f"【{name}】" in text for name in team) < 2):
        issues.append("phase3-needs-common-thread")
    if phase == 4 and not all(f"【{name}】" in text for name in team):
        issues.append("phase4-needs-all-peers")
    # 只宣布"我来示范"而不给出示范内容，不算完成转场。
    if re.search(r"【[^】]+】我先来(做个)?示范[^\n]*$", text.strip()):
        issues.append("demo-announced-not-shown")
    if issues:
        return issues
    # 结尾交还不限于最后一块——示范收在最后、小晴交还在中间同样成立。
    leader_any = any(
        ("？" in l or "?" in l or "你" in l or re.search(r"轮到|进入|结束|留笺", l))
        for l in text.splitlines() if l.startswith(f"【{prompts.LEADER_NAME}】")
    )
    if not leader_any:
        issues.append("no-final-handover")
    return issues


def _v2_advance_ok(text: str, plan: "TurnPlan") -> bool:
    return not _v2_advance_issues(text, plan)


def _ensure_v2_participant_cue(text: str, plan: "TurnPlan") -> str:
    """If peers forget to hand back the floor, the facilitator does it once."""
    last_user = str(plan.meta.get("last_user") or "")
    if not (plan.meta.get("v2") and plan.meta.get("action") == "discuss"):
        return text
    if re.search(r"不想说|不想接|不想回应|不用回应|继续听|先听|旁听|停在这|停一下|没有了|说完了", last_user):
        return text
    if str(plan.meta.get("mode") or "").startswith(("ask_", "safety_")):
        return text
    matches = list(re.finditer(r"^【([^】]+)】", text, re.MULTILINE))
    if not matches:
        return text
    final_block = text[matches[-1].end():]
    if ("？" in final_block or "?" in final_block or "继续听" in final_block
            or re.search(r"轮到你|请你|请.{0,8}(来说|来|接)|你可以.{0,8}(接|说|回应)|你想接", final_block)):
        return text
    team = set(plan.meta.get("team") or [])
    peer = next((m.group(1) for m in reversed(matches) if m.group(1) in team), "团友")
    return text.rstrip() + f"\n【小晴】{peer}刚才说到这里。你想接一句，或者继续听都可以。"


def _v2_output_issues(text: str, plan: "TurnPlan") -> list[str]:
    """Validate the public cast without requiring Xiaoqing in every peer-led turn."""
    issues = prompts.forbidden_word_issues(text)
    allowed = {prompts.LEADER_NAME, *list(plan.meta.get("team") or [])}
    speakers = re.findall(r"^【([^】]+)】", text, re.MULTILINE)
    if not speakers:
        issues.append("no-speaker-lines")
    issues.extend(f"unknown-speaker:{name}" for name in speakers if name not in allowed)
    if speakers:
        final_block = text.rsplit(f"【{speakers[-1]}】", 1)[-1].strip()
        if ("？" in final_block or "?" in final_block) and any(
            re.search(rf"^(?:{re.escape(name)})[，,:：]|{re.escape(name)}[，,]?你", final_block)
            for name in plan.meta.get("team", [])
        ):
            issues.append("unanswered-ai-cue")
    if (int(plan.meta.get("round") or 0) == 2
            and str(plan.meta.get("focus") or "") == "user"
            and text.count("？") + text.count("?") > 1):
        issues.append("too-many-user-focus-questions")
    if int(plan.meta.get("round") or 0) == 2 and str(plan.meta.get("mode") or "") == "story_first":
        focus = str(plan.meta.get("focus") or "")
        focus_name = str(plan.meta.get("focus_name") or "")
        if focus != "user" and (not focus_name or f"【{focus_name}】" not in text):
            issues.append("missing-new-focus-speaker")
        if focus != "user":
            leader_blocks = "\n".join(re.findall(r"^【小晴】([^【]*)", text, re.MULTILINE))
            if not re.search(r"现在我想先听你|真人|同学|你想对.{0,8}(?:回应|说|问)", leader_blocks):
                issues.append("missing-user-first-round-cue")
        if focus == "user" and not re.search(r"(?:你|真人|同学).{0,20}(?:说|讲|愿意|时刻|故事)", text):
            issues.append("missing-user-focus-invitation")
        if re.search(r"继续.{0,16}(?:下一位|换焦点)|(?:下一位|换焦点).{0,16}继续", text):
            issues.append("premature-story-transition-choice")
    if (int(plan.meta.get("round") or 0) == 2
            and str(plan.meta.get("mode") or "") == "story_second"
            and not (re.search(r"继续|留在|再聊", text)
                     and re.search(r"下一位|换焦点|转给下一位|交给下一位", text))):
        issues.append("missing-story-transition-choice")
    if re.search(r"没有标准流程|一共三个阶段|群策群力.*属于.*告别", text):
        issues.append("wrong-four-stage-structure")
    if not plan.meta.get("report_v2") and re.search(
        r"example\.com|HTML.{0,12}(?:已经生成|已整理好|会生成|稍后|退出后|系统附上|链接)|"
        r"(?:链接|附件|文件).{0,12}(?:已经生成|已整理好|稍后|系统|会出现)|"
        r"报告.{0,16}(?:稍后|系统附上|活动结束|退出后|会出现)|报告不是由我|没有办法给你", text,
    ):
        issues.append("premature-report-claim")
    return issues


def _v2_safe_continuation(plan: "TurnPlan") -> str:
    """Context-shaped fallback: never answer a valid member with a canned apology."""
    phase = int(plan.meta.get("round") or 1)
    exchange = int(plan.meta.get("exchanges") or 0)
    focus = str(plan.meta.get("focus") or "")
    focus_name = str(plan.meta.get("focus_name") or focus)
    last_user = str(plan.meta.get("last_user") or "")
    team = list(plan.meta.get("team") or ["团友甲", "团友乙", "团友丙"])
    if phase == 2:
        if str(plan.meta.get("mode") or "") == "story_first":
            if focus == "user":
                return (
                    "【小晴】现在轮到你的故事。你入桌时带来的那件事，这一段先不替你分析，也不急着给办法。"
                    f"\n【{team[2]}】刚才你一直在听我们三个人，也照顾到了我们的故事。"
                    "现在我们想多知道一点它具体怎样发生；不用说完整，从最近的一刻开始就好。"
                    "\n【小晴】你愿意先说一个最近的时刻吗？"
                )
            target = focus_name if focus_name in team else team[0]
            other = next(name for name in team if name != target)
            focus_openings = {
                "linzhiheng": "我那条请教消息删了好几次。最怕的不是不会，是问出口以后别人觉得我不该在这里。",
                "xunanzhi": "那晚我已经累得眼皮发沉，还是回了‘没问题’。最怕的不是多做一点，是拒绝以后关系会变。",
                "chenmo": "我看到异常数据后把电脑关了，谁也没告诉。最怕的不是重做，是开口后被问‘之前怎么没发现’。",
                "chengyichuan": "我打好那句话又删掉了。最怕的不是被拒绝，是说了以后连现在这样都没有了。",
                "lujiashu": "视频里我们各自看着手机。最怕的不是距离，是心里那点疲惫说不清从哪来。",
                "wenyan": "他说我好像什么都不需要。其实我需要，只是那句‘想见你’一直没打出去。",
                "guyifan": "我把课表排得满满的。最怕的不是跟不上，是一停下来就觉得自己不该在这里。",
                "shenzhixia": "我一个人吃饭的时候会把手机举得很高。最怕的不是孤独，是别人看出来我孤独。",
                "jiangyao": "简历每次填到期望薪资就关掉网页。最怕的不是没工作，是承认自己还在原地。",
                "fangxu": "每次面试完我都复盘到凌晨。最怕的不是没消息，是把没消息都算成我不够好。",
            }
            opening = focus_openings.get(focus, "我想先把自己最近卡住的那件事说具体一点。")
            return (
                f"【{target}】{opening}"
                f"\n【{other}】我能理解那种先躲开一下、过一会儿又得回来面对的感觉。"
                f"\n【小晴】{target}先把最怕的事说出来了。现在我想先听你："
                f"你想对{target}回应一句，还是想问{target}一个具体问题？"
            )
        if focus == "user":
            return (
                "【小晴】现在轮到你的故事。刚才你说的压力，我们不急着分析或给办法，先让大家多知道一点它怎样发生。"
                f"\n【{team[2]}】我先说说它让我想到自己的哪一段：有时事情堆在一起，我也会先僵住。你不用马上回答。"
            )
        target = focus_name if focus_name in team else team[0]
        if re.search(r"扮演|以.*(?:师兄|师姐|导师|同门).*身份|如果我是.*(?:师兄|师姐|导师|同门)|作为.*(?:师兄|师姐|导师|同门)", last_user):
            return (
                f"【{target}】我刚才认真听了你扮演的那句回应。有人说‘忙完给你看看’，"
                "我一下没那么像在添麻烦了；原来对方忙，不等于我的问题不该问。"
                "我想把消息先整理短一点，再试着发出去。"
                f"\n【小晴】{target}已经接住了你刚才的练习。你想再练一轮、继续聊这件事，还是听桌上再走一轮？"
            )
        target_i = team.index(target)
        other = team[(target_i + 1) % len(team)]
        if str(plan.meta.get("mode") or "") == "story_second":
            return (
                f"【{target}】刚才你回应我的那句话，我收到了。它没有替我解决问题，"
                "但让我知道这件事可以先被说出来。"
                f"\n【{other}】我也记住了刚才那种停住又继续的感觉。"
                f"\n【小晴】{target}已经接住了这一轮。你想继续留在这里，还是把焦点交给下一位？"
            )
        return (
            f"【小晴】我们留在{target}的故事里，不把话题抢走。"
            f"\n【{target}】我先把刚才被回应到的地方想一想。有人愿意听，我已经没那么像一个人卡着。"
            f"\n【{other}】我也有过先躲开、过一会儿又得回来面对的时刻。"
            f"\n【小晴】你想接一句，或者继续听都可以。"
        )
    if phase == 3:
        if re.search(r"愿意听|听听.*办法|有哪些.*办法|想听.*建议", last_user):
            return (
                f"【{team[0]}】我有一个自己试过的：把‘担心的事’和‘今天实际能做的事’分成两栏，"
                "先只做右边最小的一项。"
                f"\n【{team[1]}】我的办法是先问自己‘现在是卡住了，还是累了’。"
                "卡住就求助，累了就先休息，不把两种情况混在一起。"
                f"\n【{team[2]}】我会先离开屏幕走十分钟。问题不会消失，但身体没那么紧时，"
                "比较容易重新打开它。"
                "\n【小晴】这三个都只是备选。你觉得哪一个比较贴近现在，也可以说都不适合。"
            )
        if re.search(r"真的愿意|愿意试|可以试", last_user):
            return (
                "【小晴】我听清楚了：你是真的愿意试，不需要再证明或重复确认。"
                f"\n【{team[0]}】那就先把办法留在这里。哪天合适就试，没做到也不用向我们交作业。"
            )
        return (
            f"【{team[0]}】我听见你刚才说的了，不再让你重复选择。我们可以沿着这句话继续，"
            "也可以把已经有用的办法先留在这里。"
            f"\n【{team[1]}】我先不追问。你想听我们补充就听，想停一下也可以。"
        )
    return "【小晴】不用勉强总结。你也可以只说一句此刻愿意留下的话，或者说‘今天先到这里’。"


async def _generate_v2_advance(
    plan: "TurnPlan", providers: list[ProviderConfig], messages: list[dict[str, str]],
) -> str:
    """Generate and verify facilitator summary → activity announcement → peer demo."""
    last = ""
    for attempt in range(2):
        current = messages
        if attempt:
            current = [messages[0], {"role": "user", "content": messages[1]["content"] + (
                f"\n\n上一次转场结构不完整，请重写：小晴先总结上一活动，再明确说出新活动“{plan.meta.get('stage_label')}”"
                "的轮数、是否允许讨论和发言顺序；团友必须说出完整示范内容，最后明确点名下一位。"
            )}]
        last = await generate(providers, current, temperature=0.7, max_tokens=800)
        issues = _v2_advance_issues(last, plan)
        if issues:
            logger.warning("v2 advance issues=%s (attempt %d) stage_label=%s theme=%s tail=%r",
                           issues, attempt + 1, plan.meta.get("stage_label"), plan.meta.get("theme"),
                           last[-120:].replace("\n", "|"))
        if not issues and not prompts.forbidden_word_issues(last):
            return last
    theme = str(plan.meta.get("theme") or "academic")
    cfg = load_group_theme_config(theme)
    phase = cfg["phases"][int(plan.meta.get("round") or 1) - 1]
    team = list(plan.meta.get("team") or ["团友甲", "团友乙", "团友丙"])
    peer = team[0]
    other = team[1] if len(team) > 1 else "团友"
    third = team[2] if len(team) > 2 else "另一位团友"
    NL = chr(10)
    def L(speaker: str, text: str) -> str:
        return f"【{speaker}】{text}"
    demos_by_theme: dict[str, dict[int, str]] = {
        "academic": {
            2: NL.join([
                L(peer, "我那条请教消息删了三次。其实最怕的不是不会，是师兄看完觉得我不该进这个方向。"),
                L(other, "我有点好奇，你删完以后是继续自己查，还是干脆不碰了？我答应太多事时也会先躲开聊天框。"),
                L(peer, "我会继续查，查到很晚。你说你也会躲聊天框，我一下觉得这事没那么丢人了。"),
                L("小晴", f"{peer}刚才说，他最怕的不是不会，而是被看成不该在这个方向。"),
                L("小晴", f"现在我想先听你：你想对{peer}回应一句，还是想问他一个具体问题？"),
            ]),
            3: NL.join([
                L("小晴", "四个故事里有一条共同线索：大家不是不知道事情在，而是一个人扛久了更难迈出第一步。我们先自由聊一会儿；给办法前先问对方需不需要。"),
                L(peer, "我想先回应你说的那块压力：它不是一句'赶紧开始'就能解决的。如果你愿意听办法，我可以说一个我试过但不一定适合你的。"),
                L(other, "我也想听听大家能不能帮我想想：很累时，怎样既不硬撑，也不把关系推远。"),
            ]),
            4: NL.join([
                L(peer, "我带走的是：不用等完全准备好，才有资格开口求助。"),
                L(other, "我带走的是：照顾关系的时候，也可以诚实说出自己的限度。"),
                L(third, "我带走的是：重要的事可以先缓一缓，停一下不等于逃避。"),
                L("小晴", "三位团友都说完了。最后轮到你：可以说一句最想带走的话；愿意的话，也可以再报一次0到10的压力温度。"),
            ]),
        },
        "connection": {
            2: NL.join([
                L(peer, "开学第二周，我一个人在食堂吃完了一整顿饭，总觉得全场都在看我。后来才发现，根本没人抬头。"),
                L(other, "我也有类似的时刻——第一次在班群说话没人回，我盯着屏幕看了很久。你后来是怎么让自己还敢在群里说话的？"),
                L(peer, "我先只回别人的话，不主动起话头。回着回着，就有人记得我了。"),
                L("小晴", f"{peer}的办法是：不用逼自己主动开场，先从回应别人开始。"),
                L("小晴", "现在轮到你：来到这里之后，哪个小瞬间让你觉得「我好像能待下去」？或者哪个瞬间最难？"),
            ]),
            3: NL.join([
                L("小晴", "四个故事里有一条共同线索：大家不是不想连接，而是怕第一步迈得太大。我们为彼此设计「最小的一步」——小到明天就能做、没成也不心疼。"),
                L(peer, "我的一步是：明天打饭时跟食堂阿姨说一声谢谢，并且抬头看她一眼。"),
                L(other, "那我的一步是：在班群里回一条我确实知道答案的问题，只回这一条。"),
            ]),
            4: NL.join([
                L(peer, "我带走的是：陌生感会过去，它不是我的错。"),
                L(other, "我带走的是：不用马上有很多朋友，先有一个能说话的角落就够了。"),
                L(third, "我带走的是：想家的时候可以主动打一个电话，而不是一个人等它过去。"),
                L("小晴", "三位团友都说完了。最后轮到你：可以对刚拖着行李箱抵达这里的自己说一句话，或者说一句最想带走的话。"),
            ]),
        },
        "love": {
            2: NL.join([
                L(peer, "让我心动的那个瞬间：有次我讲了个很冷的笑话，全场安静了两秒，那个人在角落里真的笑出了声。就那一秒，我记到了现在。"),
                L(other, f"我想问{peer}：如果笑的人换成一个更耀眼的人，你还会心动吗？我在意的好像总是「这个人到底看上了我什么」。"),
                L(peer, "对我来说，被懂得比被羡慕更打动我。"),
                L("小晴", f"{peer}的心动更靠近「被懂得」。现在轮到你：说说让你心里一动的那个人、那个瞬间——不用说出真实名字，用代号就好。"),
            ]),
            3: NL.join([
                L("小晴", f"我用斯滕伯格的爱情三元论——亲密、激情、承诺——来照一照大家的故事。{peer}的故事里，「亲密」那束光最亮：最在意被懂得；{other}的故事里，「承诺」的分量很重：担心的是关系配不配走得远。"),
                L(peer, "被你这么一说，我明白自己为什么患得患失了——我把亲密当成了全部。"),
                L(other, "我也想听听真人的故事：哪个瞬间让你最心动，哪个瞬间让你觉得「就是这个人」？"),
            ]),
            4: NL.join([
                L(peer, "我带走的是：心动是真实的，不必因为「不知道结果」就否定它。"),
                L(other, "我带走的是：一段关系缺哪一角，可以先补自己能补的那一角。"),
                L(third, "我带走的是：喜欢一个人，也仍然可以照顾好自己的生活。"),
                L("小晴", "三位团友都说完了。最后轮到你：可以说一句最想带走的话，也可以说说你现在最想补上是哪一角。"),
            ]),
        },
        "career": {
            2: NL.join([
                L(peer, "投到第四十多份简历的时候，我已把「已读不回」当成常态。最难的不是被拒绝，是家里人问「最近怎么样」，我只能说「挺好的」。"),
                L(other, "我停在gap的第七个月。别人问「你打算干嘛」，我总感觉自己在辩解。你现在怎么回答这个问题？"),
                L(peer, "我会说「我在等一个对的机会」——说出口的时候，自己心里也稳一点。"),
                L("小晴", f"{peer}找到了一句能让自己站稳的话。现在轮到你：说说你正站在哪个路口，以及最近一个具体的时刻。"),
            ]),
            3: NL.join([
                L("小晴", "四个故事里有一条共同线索：难的不是没有选择，而是等待时不知道自己算什么。今天不聊该选哪条路，也不比较谁的选择更好，我们聊怎么和这段不确定相处。"),
                L(peer, "我的办法是给等待一个形状：每周只投固定的份数，剩下的时间归我自己。"),
                L(other, "我想试试你这个。我给自己的一步是：每周留一天，完全不提「工作」两个字。"),
            ]),
            4: NL.join([
                L(peer, "我带走的是：等待不等于落后，它只是我的一段现在。"),
                L(other, "我带走的是：不用拿别人的时间表来审判自己。"),
                L(third, "我带走的是：路可以边走边看，不必一次定终身。"),
                L("小晴", "三位团友都说完了。最后轮到你：可以说一句「我和不确定的关系」，或者说一句最想带走的话。"),
            ]),
        },
    }
    demos = demos_by_theme.get(theme, demos_by_theme["academic"])
    demo = demos.get(
        int(plan.meta.get("round") or 1),
        NL.join([
            L(peer, "我先从一件具体的小事说起。"),
            L("小晴", "下一位轮到你。"),
        ]),
    )
    return (
        "【小晴】我先把刚才收在这里：大家说出了各自真实的一块，也听见了处境之间的相同和不同。"
        f"\n【小晴】接下来进入“{phase['label']}”。{phase['activity']}发言规则是：{phase.get('interaction_rule', '我会明确点名下一位。')}"
        f"\n{demo}"
    )


def _safe_report_fallback() -> str:
    """供应商异常或违反公开文案边界时使用的安全报告回退。"""
    return "\n".join([
        "【小晴】（把一份手写便签放到你手边）这是今天的成长手记——",
        "📝 本场主题：清心圆桌活动",
        "🫧 你带来的：你认真把这一场的感受放到了圆桌上。",
        "💬 桌友们的回响：大家用自己的经历，陪你把话说完整。",
        "✨ 值得带走的：",
        "· 慢一点，也没关系。",
        "· 先照顾身体，再处理眼前的事。",
        "· 把想法说出来，给自己一点空间。",
        "🌱 留给下次的：下次想从哪一件小事开始聊？",
    ])


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
            body = group_v2.MARKER_RE.sub("", MARKER_RE.sub("", text)).strip()
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

    v2_state = group_v2.reconstruct(messages)
    panel_state = panel.reconstruct(messages)
    studio_state = painting_studio.reconstruct(messages)
    state = reconstruct(messages)

    # 危机检测：v2 使用可持续的安全状态，绝不回落到普通团体兜底。
    crisis = safety.detect(last_user)
    if crisis == "high":
        if v2_state is not None:
            safe_state = group_v2.GroupState(
                v2_state.phase, "safety_check", v2_state.focus, v2_state.exchanges,
                v2_state.team_ids, v2_state.card_ids, v2_state.completed, v2_state.theme,
            )
            plan = TurnPlan(
                kind="scripted", marker=group_v2.marker(safe_state),
                meta={"v2": True, "stage": "v2_safety", "stage_label": "安全确认", "crisis": "high"},
            )
            plan.script = _finalize_body(plan, safety.aid_reply())
            return plan
        if panel_state is not None:
            plan = TurnPlan(
                kind="scripted", marker=panel.marker(panel_state),
                meta={"panel": True, "stage": "panel_safety", "stage_label": "安全确认",
                      "crisis": "high", "team": panel.figure_names(panel_state.figure_ids)},
            )
            plan.script = _finalize_body(plan, safety.aid_reply())
            return plan
        if studio_state is not None:
            plan = TurnPlan(
                kind="scripted", marker=painting_studio.marker(studio_state),
                meta={"studio": True, "stage": "studio_safety", "stage_label": "安全确认",
                      "crisis": "high", "team": []},
            )
            plan.script = _finalize_body(plan, safety.aid_reply())
            return plan
        return TurnPlan(kind="scripted", script=safety.aid_reply(),
                        meta={"stage": state.stage, "crisis": "high"})

    if panel_state is not None:
        return _panel_plan(messages, last_user, panel_state, crisis)
    if studio_state is not None:
        return _studio_plan(messages, last_user, studio_state, crisis)
    if v2_state is not None:
        if v2_state.mode.startswith("safety_"):
            unsafe = bool(re.search(r"不安全|有.*(?:计划|工具|东西)|已经.*(?:伤害|吃药|割)", last_user))
            safe = bool(re.search(r"安全|没事|好了|在宿舍|有人陪|我在", last_user))
            if unsafe or v2_state.mode == "safety_check" and not safe:
                next_mode = "safety_check"
                text = safety.aid_reply() if unsafe else safety.safety_followup()
            elif v2_state.mode == "safety_check":
                next_mode = "safety_support"
                text = safety.safety_support()
            elif re.search(r"继续|好了|联系|有人陪|放远", last_user):
                resume_mode = "story" if v2_state.phase == 2 else ("mutual" if v2_state.phase == 3 else "main")
                resumed = group_v2.GroupState(
                    v2_state.phase, resume_mode, v2_state.focus, v2_state.exchanges,
                    v2_state.team_ids, v2_state.card_ids, v2_state.completed, v2_state.theme,
                )
                return _v2_plan(messages, last_user, resumed, None)
            else:
                next_mode = "safety_support"
                text = safety.safety_support()
            safe_state = group_v2.GroupState(
                v2_state.phase, next_mode, v2_state.focus, v2_state.exchanges,
                v2_state.team_ids, v2_state.card_ids, v2_state.completed, v2_state.theme,
            )
            plan = TurnPlan(
                kind="scripted", marker=group_v2.marker(safe_state),
                meta={"v2": True, "stage": "v2_safety", "stage_label": "安全确认", "crisis": "safety"},
            )
            plan.script = _finalize_body(plan, text)
            return plan
        return _v2_plan(messages, last_user, v2_state, crisis)

    if state.stage == GREETING:
        form = detect_form(last_user)
        theme = detect_theme(last_user, themes)
        # 时空对话入口：显式点名（或菜单6），亦可直接点出两位人物开场
        if (panel.detect_panel_entry(last_user)
                or last_user.strip() in {"6", "六", "6.", "6、"}):
            # v2 流程：先问话题/想找谁，人物在用户回答后才分配（intro 轮 figs 留空）
            initial = panel.PanelState([], 0, "intro", "", "")
            return _panel_plan(messages, last_user, initial, crisis, initial_open=True)
        # 圆桌画室入口：绘画类关键词统一进入轻团体画室
        if form == FORM_PAINTING:
            return _studio_plan(messages, last_user, painting_studio.StudioState(),
                                crisis, initial_open=True, topic_seed=seed_text or last_user)
        if theme is None and len(last_user.strip()) >= GREETING_MIN_LEN:
            # 无关键词匹配的实质倾诉默认进入减压团体（最通用的深度容器）
            theme = next((t for t in themes if t.get("id") == "academic"), themes[0])
        if theme is None:
            has_history = any(m.get("role") == "assistant" for m in messages)
            return TurnPlan(
                kind="scripted",
                script=prompts.MENU_RETRY_TEXT if has_history else prompts.GREETING_TEXT,
                meta={"stage": GREETING, "crisis": crisis},
            )
        if theme.get("id") in {"academic", "connection", "love", "career"} and form == FORM_CHAT:
            theme_id = str(theme["id"])
            team_ids, card_ids = group_v2.select_team(seed_text or last_user, theme_id)
            initial = group_v2.GroupState(
                phase=1, mode="main", focus="", exchanges=0,
                team_ids=team_ids, card_ids=card_ids, completed=[], theme=theme_id,
            )
            return _v2_plan(messages, last_user, initial, crisis, initial_open=True)
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
            stage_label = _stage_label(theme, "ignite", label_cfg)
            marker = make_marker(2, stage_label, theme["label"], state.form, team_ids)
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
                      "form": state.form, "team": [m["name"] for m in team], "stage_label": stage_label, "crisis": crisis},
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
    stage_label = _stage_label(theme, stage_id, label_cfg)
    marker = make_marker(round_no, stage_label, theme["label"], form, team_ids)

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
              "form": form, "team": [m["name"] for m in team], "stage_label": stage_label,
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


def _v2_plan(
    messages: list[dict], last_user: str, current: group_v2.GroupState,
    crisis: str | None, initial_open: bool = False,
) -> TurnPlan:
    """四活动团体（academic/connection/love/career 共用容器）。"""
    if initial_open:
        state, action = current, "open"
    else:
        state, action = group_v2.next_state(last_user, current)
        state.theme = current.theme  # next_state 内部构造不携带主题，此处回填
        if state.phase == 2 and action == "advance" and not state.focus:
            state = group_v2.GroupState(
                2, "story_first", state.team_ids[0], 0,
                state.team_ids, state.card_ids, [], state.theme,
            )
    group_cfg = load_group_theme_config(state.theme)
    phase_cfg = group_cfg["phases"][state.phase - 1]
    focus_name = "user"
    if state.focus and state.focus != "user":
        focus_name = group_cfg["members"].get(state.focus, {}).get("name", state.focus)
    system_prompt = (
        group_v2.build_report_prompt(state)
        if action == "close_report"
        else group_v2.build_system_prompt(state, action)
    )
    extra = safety.medium_empathy_instruction() if crisis == "medium" else ""
    user_content = group_v2.build_user_content(messages, last_user)
    if extra:
        user_content += "\n\n【安全优先提示】\n" + extra
    plan = TurnPlan(
        kind="generate", system_prompt=system_prompt, user_content=user_content,
        marker=group_v2.marker(state),
        meta={
            "v2": True, "stage": f"v2_{phase_cfg['id']}", "round": state.phase,
            "theme": state.theme,
            "theme_label": group_cfg.get("theme_label") or "清心圆桌",
            "form": FORM_CHAT,
            "team": group_v2.member_names(state), "stage_label": phase_cfg["label"],
            "action": action, "report_v2": action == "close_report", "crisis": crisis,
            "exchanges": state.exchanges,
            "focus": state.focus,
            "focus_name": focus_name,
            "mode": state.mode,
            "last_user": last_user,
        },
    )
    if initial_open:
        # The opening is stable product copy, not a generation task. This makes it
        # instant and guarantees peers are introduced before taking part.
        plan.kind = "scripted"
        plan.script = _finalize_body(plan, group_v2.opening_script(state))
        # Keep marker metadata available to tests/callers; the scripted executor
        # returns plan.script directly, so it is not appended a second time.
    return plan


def _panel_plan(messages: list[dict], last_user: str, current: panel.PanelState,
                crisis: str | None, initial_open: bool = False) -> TurnPlan:
    """时空对话：问话题→配四人介绍确认→一问四答+辩论→追问/换题/换人→HTML留笺。"""
    if initial_open:
        state, action = current, "intro"
    else:
        state, action = panel.next_state(last_user, current)
    names = panel.figure_names(state.figure_ids)
    base_meta = {
        "panel": True, "stage": "panel", "team": names,
        "figures": state.figure_ids, "crisis": crisis,
        "panel_action": action, "panel_focus": state.focus, "last_user": last_user,
        "panel_topic": state.topic,
    }
    if action == "intro":
        plan = TurnPlan(
            kind="scripted", marker=panel.marker(state),
            meta={**base_meta, "stage_label": "时空对话",
                  "progress_note": "⏳ 时空对话 · 想先听听你的话题"},
        )
        plan.script = _finalize_body(plan, panel.intro_script())
        return plan
    if action == "invite":
        note = "⏳ 时空对话 · 四位先生待入座，等你确认"
        plan = TurnPlan(
            kind="scripted", marker=panel.marker(state),
            meta={**base_meta, "stage_label": "时空对话", "progress_note": note},
        )
        plan.script = _finalize_body(plan, panel.opening_script(state))
        return plan
    if action == "ask":
        plan = TurnPlan(
            kind="scripted", marker=panel.marker(state),
            meta={**base_meta, "stage_label": "时空对话",
                  "progress_note": "⏳ 时空对话 · 请提问"},
        )
        plan.script = _finalize_body(
            plan, "【小晴】好，请讲——你的这个问题，四位先生都会给出自己的答案，然后辩上一轮。")
        return plan
    is_farewell = action == "farewell"
    system_prompt = panel.build_system_prompt(state, last_user, is_farewell=is_farewell)
    user_content = panel.build_user_content(messages, last_user)
    note = "⏳ 时空对话 · 辩论已收束，可追问/换题/换人/结束" if not is_farewell else "⏳ 时空对话 · 本场结束"
    return TurnPlan(
        kind="generate", system_prompt=system_prompt, user_content=user_content,
        marker=panel.marker(state),
        meta={**base_meta, "stage_label": "时空对话", "progress_note": note},
    )


def _panel_issues(text: str, plan: TurnPlan, farewell: bool) -> list[str]:
    """面板输出校验：只许小晴与四位先生发言；作答轮四人齐全且有真实交锋。"""
    text = re.sub(r"\*+", "", text)
    issues = prompts.forbidden_word_issues(text)
    names = list(plan.meta.get("team", []))
    speakers = re.findall(r"^【([^】]+)】", text, re.MULTILINE)
    if not speakers:
        return ["no-speaker-lines"]
    issues.extend(f"unknown-speaker:{n}" for n in speakers if n not in {"小晴", *names})
    for n in names:
        if f"【{n}】" not in text:
            issues.append(f"missing-figure:{n}")
    if not farewell and not plan.meta.get("panel_focus"):
        # 追问轮以目标人物为先，其余人回应即可，不强制互相点名
        debate = False
        for line in re.findall(r"^【[^】]+】.*$", text, re.MULTILINE):
            speaker = re.match(r"^【([^】]+)】", line)
            if speaker and any(m != speaker.group(1) and m in line for m in names):
                debate = True
                break
        if not debate:
            issues.append("no-debate-cross-reference")
        if len(text) > 1300:
            issues.append("too-long")
    return issues


def _panel_fallback(plan: TurnPlan, farewell: bool) -> str:
    names = list(plan.meta.get("team", [])) or ["先生们"]
    if farewell:
        listed = "、".join(names)
        return f"【小晴】（先生们各自拱了拱手）{listed}今日就先回去了——谢谢你的问题，让他们又活了一回。"
    return (
        "【小晴】（四位先生似乎还在斟酌用词）刚才这一问没接稳——"
        "麻烦你把问题再问一遍，或换个问法；也可以点名让某位先生先答。"
    )


def _panel_report_attachment(plan: TurnPlan, body: str) -> dict | None:
    """告别轮：《时空留笺》HTML 报告（四位人物赠言）。"""
    from . import files, panel_report

    figs = list(plan.meta.get("figures") or [])
    by_id = panel.figure_by_id()
    rows, quotes = [], {}
    for fid in figs:
        p = by_id.get(fid)
        if not p:
            continue
        persona = "，".join((p.get("personality") or [])[:2]) or "史册中人"
        rows.append({"id": fid, "name": p["name"], "era": p.get("era", ""), "persona": persona})
        quotes[p["name"]] = p.get("quote", "")
    if not rows:
        return None
    data = panel_report.render(
        topic=str(plan.meta.get("panel_topic") or ""),
        figure_rows=rows, body=body, gifts_fallback=quotes,
    )
    token = files.put(data, mime="text/html", ext="html", ttl=24 * 3600)
    url = f"{base}/files/{token}" if (base := load_settings().public_base_url) else f"/files/{token}"
    return {
        "fileName": "时空留笺.html",
        "mimeType": "text/html",
        "url": url,
    }


def _studio_plan(messages: list[dict], last_user: str, current: painting_studio.StudioState,
                 crisis: str | None, initial_open: bool = False,
                 topic_seed: str = "") -> TurnPlan:
    """圆桌画室：轻团体绘画共创（小晴问一次，每人对一次）。"""
    if initial_open:
        state = painting_studio.StudioState(
            "user_stroke", ["p0"], "", topic_seed or last_user,
        )
        team = painting_studio.team_from_seed(state.topic_seed)
        action = "open"
    else:
        state, action = painting_studio.next_state(last_user, current)
        team = painting_studio.team_from_seed(state.topic_seed or (topic_seed or ""))
    names = painting_studio.member_names(team)
    meta = {
        "studio": True, "stage": "studio", "team": names, "crisis": crisis,
        "studio_action": action, "last_user": last_user,
        "studio_state": state, "studio_team": team,
        "stage_label": "圆桌画室",
    }
    if initial_open:
        note = "🎨 圆桌画室 · 等你落笔"
        plan = TurnPlan(
            kind="scripted", marker=painting_studio.marker(state),
            meta={**meta, "progress_note": note},
        )
        plan.script = _finalize_body(plan, painting_studio.opening_script(state, team))
        return plan
    user_stroke = painting_studio.user_stroke_text(last_user)
    strokes = painting_studio.parse_strokes(messages)
    if painting_studio.NO_STROKE_RE.search(last_user):
        strokes = strokes + [("小晴（代笔）", "我想在这幅画上加上一杯冒着热气的茶，代表你的位置")]
    system_prompt = painting_studio.build_system_prompt(state, team, action, user_stroke, strokes)
    note = {
        "strokes": "🎨 圆桌画室 · 四笔收齐，准备合成",
        "reveal": "🎨 圆桌画室 · 画作揭晓",
        "reflect": "🎨 圆桌画室 · 明信片在附件里",
    }.get(action, "🎨 圆桌画室")
    plan = TurnPlan(
        kind="generate", system_prompt=system_prompt,
        user_content=f"【用户刚才说】\n{last_user}\n\n请完成本轮画室输出。",
        marker=painting_studio.marker(state),
        meta={**meta, "progress_note": note, "studio_user_stroke": user_stroke,
              "studio_strokes": strokes},
    )
    if action == "reveal":
        from . import imagegen

        plan.meta["painting_prompt"] = imagegen.build_painting_prompt(
            painting_studio.framing_from_seed(state.topic_seed),
            [t for _, t in strokes],
            user_stroke,
        )
    return plan


def _studio_fallback(plan: TurnPlan, action: str) -> str:
    return painting_studio.studio_fallback(
        action, list(plan.meta.get("team", [])),
        str(plan.meta.get("studio_user_stroke") or ""),
    )


def _studio_postcard_attachment(plan: TurnPlan, body: str) -> list[dict]:
    """明信片：字段确定性来自对话（笔触+小晴寄语），图缺时回落文字排版。"""
    from . import files, postcard_html

    try:
        state: painting_studio.StudioState = plan.meta["studio_state"]
        strokes = list(plan.meta.get("studio_strokes") or [])
        user_stroke = str(plan.meta.get("studio_user_stroke") or "")
        if len(strokes) < 4 and user_stroke:
            strokes.append(("你", painting_studio.user_stroke_text(
                str(plan.meta.get("last_user") or ""))))
        artwork_url = ""
        if state.img_token:
            artwork_url = f"{load_settings().public_base_url}/files/{state.img_token}"
        data = postcard_html.render(
            painting_studio.framing_from_seed(state.topic_seed),
            strokes, body, artwork_url=artwork_url,
        )
        token = files.put(data, mime="text/html", ext="html", ttl=24 * 3600)
        return [{
            "fileUrl": f"{load_settings().public_base_url}/files/{token}",
            "fileName": "圆桌画室·明信片.html", "fileType": "file",
            "mimeType": "text/html", "fileSize": len(data),
        }]
    except Exception:
        return []


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
            if fields is not None and not _report_fields_policy_issues(fields):
                return text, fields
    except Exception:
        pass
    fallback = await _legacy_report(plan, providers)
    if prompts.forbidden_word_issues(fallback):
        fallback = _safe_report_fallback()
    return fallback, None


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


def _v2_report_attachment(plan: TurnPlan, fields: dict) -> list[dict]:
    """新版《圆桌留笺》单HTML附件。"""
    from . import files, report_v2_html

    try:
        data = report_v2_html.render(fields, list(plan.meta.get("team", [])))
        token = files.put(data, mime="text/html", ext="html", ttl=24 * 3600)
        return [{
            "fileUrl": f"{load_settings().public_base_url}/files/{token}",
            "fileName": "清心圆桌·圆桌留笺.html", "fileType": "file",
            "mimeType": "text/html", "fileSize": len(data),
        }]
    except Exception:
        return []


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
    if plan.meta.get("report_v2"):
        fields = None
        for temp in (0.4, 0.2):
            try:
                raw = await generate(providers, llm_messages, temperature=temp, max_tokens=700)
                candidate = _parse_report_json(raw)
                if candidate is not None and not _report_fields_policy_issues(candidate):
                    fields = candidate
                    break
            except Exception:
                continue
        fields = fields or {
            "participant_name": "你", "discussion_topics": ["本场带来的压力"],
            "stress_suggestions": ["本次没有勉强整理建议"],
            "approach_moment": "未形成", "user_impact": "未形成",
            "member_impact": "未确认", "differences": [],
            "response_need": "未明确", "real_world_phrase": "这次先不带行动离开",
            "pressure_map": "未明确", "stress_checklist": ["本次未选择"], "closing_words": "未确认",
            "pressure_before": None, "pressure_after": None,
            "leader_note": "谢谢你认真参与这场圆桌。",
        }
        body = "【小晴】谢谢你和大家认真坐完这一场。我们今天在这里离桌。《圆桌留笺》已经整理好，附件里可以打开。"
        return _finalize_body(plan, body), [], _v2_report_attachment(plan, fields)
    if plan.meta.get("report_html"):
        text, fields = await _generate_report_fields(plan, providers)
        if fields is not None:
            body = _report_display_text(fields)
            attachments = _report_attachments(plan, fields)
            return _finalize_body(plan, body), [], attachments
        # JSON 解析失败 → text 已是经策略检查的文本成长手记。
        body = text.strip()
        attachments = build_attachments(plan, body)
        return _finalize_body(plan, body), [], attachments
    if plan.meta.get("panel"):
        farewell = plan.meta.get("panel_action") == "farewell"
        text = await generate(providers, llm_messages, temperature=0.8, max_tokens=1500)
        issues = _panel_issues(text, plan, farewell)
        if issues:
            retry = await generate(providers, llm_messages, temperature=0.65, max_tokens=1500)
            if not _panel_issues(retry, plan, farewell):
                text, issues = retry, []
        if issues:
            text = _panel_fallback(plan, farewell)
        attachments: list[dict] = []
        if farewell:
            report = _panel_report_attachment(plan, text)
            if report:
                attachments.append(report)
        return _finalize_body(plan, text.strip()), issues, attachments
    if plan.meta.get("studio"):
        action = str(plan.meta.get("studio_action") or "")
        def _studio_issues(t: str) -> list[str]:
            return (painting_studio.stroke_issues(t, list(plan.meta.get("team", [])), action)
                    + prompts.forbidden_word_issues(t))

        text = await generate(providers, llm_messages, temperature=0.85, max_tokens=900)
        issues = _studio_issues(text)
        if issues:
            retry = await generate(providers, llm_messages, temperature=0.7, max_tokens=900)
            if not _studio_issues(retry):
                text, issues = retry, []
        if issues:
            text = _studio_fallback(plan, action)
        body = text.strip()
        attachments: list[dict] = []
        if action == "reveal" and img_task is not None:
            img = await img_task
            if img:
                from . import files

                token = files.put(img, mime="image/jpeg", ext="jpg")
                state: painting_studio.StudioState = plan.meta["studio_state"]
                state.img_token = token
                plan.marker = painting_studio.marker(state)
                attachments.append({
                    "fileUrl": f"{load_settings().public_base_url}/files/{token}",
                    "fileName": "圆桌画室·共同画作.jpg", "fileType": "image",
                    "mimeType": "image/jpeg", "fileSize": len(img),
                })
        if action == "reflect":
            attachments.extend(_studio_postcard_attachment(plan, body))
        return _finalize_body(plan, body), issues, attachments
    if (plan.meta.get("v2") and plan.meta.get("round") == 1
            and plan.meta.get("action") == "discuss"):
        text = await _generate_v2_opening_handoff(plan, providers, llm_messages)
        text = _ensure_v2_participant_cue(text.strip(), plan)
        return _finalize_body(plan, text), [], []
    if plan.meta.get("v2") and plan.meta.get("action") == "advance":
        text = await _generate_v2_advance(plan, providers, llm_messages)
        return _finalize_body(plan, text.strip()), [], []
    turn_tokens = 800 if plan.meta.get("v2") else 1200
    text = await generate(providers, llm_messages, temperature=0.85, max_tokens=turn_tokens)
    issues = prompts.validate_turn(text, allowed, min_members)
    if plan.meta.get("v2"):
        issues.extend(x for x in _v2_output_issues(text, plan) if x not in issues)
    if issues and plan.meta.get("stage") != "report":
        retry = await generate(providers, llm_messages, temperature=0.7, max_tokens=turn_tokens)
        retry_issues = prompts.validate_turn(retry, allowed, min_members)
        if plan.meta.get("v2"):
            retry_issues.extend(x for x in _v2_output_issues(retry, plan) if x not in retry_issues)
        if len(retry_issues) < len(issues):
            text, issues = retry, retry_issues
    # 非流式接口也必须执行与流式接口相同的硬兜底。此前这里只记录
    # 校验问题却仍返回违规文本，模型因而可能替真人发言或漏掉转场选择。
    body = (
        _v2_safe_continuation(plan)
        if issues and plan.meta.get("v2")
        else (prompts.LLM_FALLBACK_TEXT if issues else text.strip())
    )
    body = _ensure_v2_participant_cue(body, plan)
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

    if plan.meta.get("report_v2"):
        fields = None
        for temp in (0.4, 0.2):
            try:
                raw = await generate(providers, llm_messages, temperature=temp, max_tokens=700)
                candidate = _parse_report_json(raw)
                if candidate is not None and not _report_fields_policy_issues(candidate):
                    fields = candidate
                    break
            except Exception:
                continue
        fields = fields or {
            "participant_name": "你", "discussion_topics": ["本场带来的压力"],
            "stress_suggestions": ["本次没有勉强整理建议"],
            "approach_moment": "未形成", "user_impact": "未形成",
            "member_impact": "未确认", "differences": [],
            "response_need": "未明确", "real_world_phrase": "这次先不带行动离开",
            "pressure_map": "未明确", "stress_checklist": ["本次未选择"], "closing_words": "未确认",
            "pressure_before": None, "pressure_after": None,
            "leader_note": "谢谢你认真参与这场圆桌。",
        }
        display = "【小晴】谢谢你和大家认真坐完这一场。我们今天在这里离桌。《圆桌留笺》已经整理好，附件里可以打开。"
        async for delta in paced_text(display):
            yield "delta", delta
        attachments = _v2_report_attachment(plan, fields)
        if attachments:
            yield "attachments", attachments
        final = _finalize_body(plan, display)
        if len(final) > len(display):
            yield "delta", final[len(display):]
        yield "final", final
        return

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

    if plan.meta.get("v2") and plan.meta.get("action") == "advance":
        display = (await _generate_v2_advance(plan, providers, llm_messages)).strip()
        async for delta in paced_text(display):
            yield "delta", delta
        final = _finalize_body(plan, display)
        if len(final) > len(display):
            yield "delta", final[len(display):]
        yield "final", final
        return

    if plan.meta.get("panel"):
        farewell = plan.meta.get("panel_action") == "farewell"
        body = ""
        try:
            body = (await generate(providers, llm_messages, temperature=0.8, max_tokens=1500)).strip()
        except Exception:
            body = ""
        issues = _panel_issues(body, plan, farewell) if body else ["llm-error"]
        if issues:
            try:
                retry = await generate(providers, llm_messages, temperature=0.65, max_tokens=1500)
                if not _panel_issues(retry, plan, farewell):
                    body, issues = retry.strip(), []
            except Exception:
                pass
        if issues:
            body = _panel_fallback(plan, farewell)
        async for delta in paced_text(body):
            yield "delta", delta
        final = _finalize_body(plan, body)
        if len(final) > len(body):
            yield "delta", final[len(body):]
        attachments: list[dict] = []
        if farewell:
            report = _panel_report_attachment(plan, body)
            if report:
                attachments.append(report)
        yield "final", final, attachments
        return

    if plan.meta.get("studio"):
        action = str(plan.meta.get("studio_action") or "")
        body = ""
        try:
            body = (await generate(providers, llm_messages, temperature=0.85, max_tokens=900)).strip()
        except Exception:
            body = ""

        def _studio_issues(t: str) -> list[str]:
            return (painting_studio.stroke_issues(t, list(plan.meta.get("team", [])), action)
                    + prompts.forbidden_word_issues(t))

        issues = _studio_issues(body) if body else ["llm-error"]
        if issues:
            try:
                retry = await generate(providers, llm_messages, temperature=0.7, max_tokens=900)
                if not _studio_issues(retry):
                    body, issues = retry.strip(), []
            except Exception:
                pass
        if issues:
            if img_task is not None:
                img_task.cancel()
            body = _studio_fallback(plan, action)
        async for delta in paced_text(body):
            yield "delta", delta
        attachments: list[dict] = []
        if action == "reveal" and img_task is not None:
            img = await img_task
            if img:
                from . import files

                token = files.put(img, mime="image/jpeg", ext="jpg")
                state: painting_studio.StudioState = plan.meta["studio_state"]
                state.img_token = token
                plan.marker = painting_studio.marker(state)
                attachments.append({
                    "fileUrl": f"{load_settings().public_base_url}/files/{token}",
                    "fileName": "圆桌画室·共同画作.jpg", "fileType": "image",
                    "mimeType": "image/jpeg", "fileSize": len(img),
                })
        if action == "reflect":
            attachments.extend(_studio_postcard_attachment(plan, body))
        if attachments:
            yield "attachments", attachments
        final = _finalize_body(plan, body)
        if len(final) > len(body):
            yield "delta", final[len(body):]
        yield "final", final
        return

    if (plan.meta.get("v2") and plan.meta.get("round") == 1
            and plan.meta.get("action") == "discuss"):
        display = (await _generate_v2_opening_handoff(plan, providers, llm_messages)).strip()
        display = _ensure_v2_participant_cue(display, plan)
        async for delta in paced_text(display):
            yield "delta", delta
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
        async for delta in stream_generate(
            providers, llm_messages, temperature=0.85,
            max_tokens=800 if plan.meta.get("v2") else 1200,
        ):
            collected.append(delta)
    except Exception:
        if img_task is not None:
            img_task.cancel()
        if not collected:
            fallback = _v2_safe_continuation(plan) if plan.meta.get("v2") else prompts.LLM_FALLBACK_TEXT
            async for delta in paced_text(fallback):
                yield "delta", delta
            yield "final", _finalize_body(plan, fallback) if plan.meta.get("v2") else fallback
            return
    body = "".join(collected).strip()
    issues = _v2_output_issues(body, plan) if plan.meta.get("v2") else prompts.forbidden_word_issues(body)
    if issues and plan.meta.get("v2"):
        try:
            retry = await generate(providers, llm_messages, temperature=0.65, max_tokens=800)
            retry_issues = _v2_output_issues(retry, plan)
            if not retry_issues:
                body, issues = retry.strip(), []
        except Exception:
            pass
    if issues:
        body = _v2_safe_continuation(plan) if plan.meta.get("v2") else prompts.LLM_FALLBACK_TEXT
    body = _ensure_v2_participant_cue(body, plan)
    visible = streamed_prefix + body
    async for delta in paced_text(visible):
        yield "delta", delta
    attachments: list[dict] = []
    if img_task is not None:
        img = await img_task
        if img:
            attachments.extend(_image_attachment(img))
    attachments.extend(build_attachments(plan, body))
    if attachments:
        yield "attachments", attachments
    final = _finalize_body(plan, body)
    tail = final[len(visible):] if final.startswith(visible) else final
    if tail:
        yield "delta", tail
    yield "final", final


def _chunk(text: str, size: int = 24) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]
