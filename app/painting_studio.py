"""圆桌画室：轻团体绘画共创（小晴问一次、每人对一次）。

流程（四次 LLM/脚本输出）：
  1. 开场脚本    命题+规则+团友A一笔+邀请用户落笔（固定脚本，零延迟）
  2. strokes     用户落笔后，团友B、C各添一笔并预告合成
  3. reveal      合成画作并揭晓；团友可即席感受一两句，小晴立即收住并抛出反思问题
  4. reflect     三位团友各一句回顾+小晴总结+明信片HTML附件

状态为"等待什么"：user_stroke → reveal_ready → reflecting → done。
完成条件全部可枚举：who 列表四位各一笔（用户可由小晴代笔）。
图像生成失败时以文字画完成流程，绝不阻塞完成条件。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .config import load_group_v2_config
from .session import extract_text

MARKER_RE = re.compile(
    r"<!--QXPA\|step=([a-z_]+)\|who=([^|>]*)(?:\|img=([^|>]*))?-->"
)
STUDIO_NAME_RE = re.compile(r"画室|画会|一起画|共同.{0,4}画|画一幅|绘画主题|来画画")
STROKE_PREFIX_RE = re.compile(r"^(我希望这幅画上有|我想在这幅画上加上)")
STROKE_ANYWHERE_RE = re.compile(r"(我希望这幅画上有|我想在这幅画上加上)")
NO_STROKE_RE = re.compile(r"不知道|不会画|随便|你帮我想|你来画|跳过|想不出|画什么好|没想法")
STROKE_LINE_RE = re.compile(r"【([^】]+)】[^\n]*?((?:我希望这幅画上有|我想在这幅画上加上)[^【\n]{0,220})")

POOL = ("linzhiheng", "xunanzhi", "chenmo", "xiaoman", "wenyan", "guyifan")

# 开场团友A的代笔：每人一句固定笔触（脚本开场用，稳定不生成）
FIRST_STROKE = {
    "linzhiheng": "我想在这幅画上加上一盏亮到深夜的台灯，灯下摊着写满计划的草稿纸",
    "xunanzhi": "我想在这幅画上加上一杯还冒着热气的奶茶，放在占满桌面的小组作业旁边",
    "chenmo": "我想在这幅画上加上实验室窗外将亮未亮的天色，只有我一个人在的那条路",
    "xiaoman": "我想在这幅画上加上一只趴在快递箱上打盹的校园猫，什么都不用赶",
    "wenyan": "我想在这幅画上加上图书馆里刚好空着的靠窗座位，阳光斜斜落在桌上",
    "guyifan": "我想在这幅画上加上操场的傍晚跑道，跑完一圈的人慢慢走着回宿舍",
}


@dataclass
class StudioState:
    step: str = "user_stroke"  # user_stroke / reveal_ready / reflecting / done
    who: list[str] = field(default_factory=list)  # 已落笔者：p0/p1/p2/u
    img_token: str = ""
    topic_seed: str = ""


def marker(state: StudioState) -> str:
    return (
        f"<!--QXPA|step={state.step}|who={','.join(state.who)}"
        + (f"|img={state.img_token}" if state.img_token else "")
        + "-->"
    )


def reconstruct(messages: list[dict]) -> StudioState | None:
    found = None
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        hits = MARKER_RE.findall(extract_text(msg.get("content")))
        if hits:
            found = hits[-1]
    if found is None:
        return None
    step, who, img = found
    return StudioState(step=step, who=[x for x in who.split(",") if x], img_token=img or "")


def framing_from_seed(seed: str) -> str:
    core = re.sub(r"\s+", " ", (seed or "")).strip().rstrip("。！？!?，,")
    return f"这幅画关于「{core[:24] or '最近的我'}」"


def team_from_seed(seed: str) -> list[str]:
    """三位团友：从六人团友池按 seed 稳定取三位（hashlib，跨进程稳定）。"""
    digest = int(hashlib.sha256(f"studio|{seed}".encode("utf-8")).hexdigest(), 16)
    pool = list(POOL)
    picked: list[str] = []
    while pool and len(picked) < 3:
        picked.append(pool.pop(digest % len(pool)))
        digest = digest // 7 + 3
    return picked or ["linzhiheng", "xunanzhi", "chenmo"]


def member_names(team: list[str]) -> list[str]:
    cfg = load_group_v2_config()
    return [cfg["members"][mid]["name"] for mid in team if mid in cfg["members"]]


def opening_script(state: StudioState, team: list[str]) -> str:
    names = member_names(team)
    framing = framing_from_seed(state.topic_seed)
    stroke = FIRST_STROKE.get(team[0], FIRST_STROKE["linzhiheng"])
    return (
        f"【小晴】欢迎来到「圆桌画室」～今天这场很安静：我们不聊天，我们画一幅画。命题是——{framing}。"
        "规则很简单：每个人往画上添一笔，用一句话说出你想画什么，说出来的就算数。\n"
        f"【{names[0]}】{stroke}。\n"
        f"【小晴】{names[0]}已经落笔了。现在轮到你：你想在这幅画上加上什么？"
        "一句话就行；实在想不出，说「随便」也可以，我替你添。"
    )


def parse_strokes(messages: list[dict]) -> list[tuple[str, str]]:
    """从历史里收集全部已成形的笔触（说话人, 笔触句）。"""
    out: list[tuple[str, str]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for m in STROKE_LINE_RE.finditer(extract_text(msg.get("content"))):
            out.append((m.group(1), m.group(2).strip()))
    return out


def user_stroke_text(last_user: str) -> str:
    core = re.sub(r"\s+", " ", last_user).strip().rstrip("。！？!?，,")
    if not core:
        return "我想在这幅画上加上一小片安静的留白"
    if STROKE_PREFIX_RE.match(core):
        return core[:80]
    return f"我想在这幅画上加上{core[:60]}"


def next_state(last_user: str, current: StudioState) -> tuple[StudioState, str]:
    """返回 (新状态, 动作)。动作：strokes / reveal / reflect。"""
    if current.step == "user_stroke":
        # 用户落笔后团友B、C各添一笔：四位贡献者就此收齐
        who = list(dict.fromkeys(current.who + ["u", "p1", "p2"]))
        return StudioState("reveal_ready", who, current.img_token, current.topic_seed), "strokes"
    if current.step == "reveal_ready":
        return StudioState("reflecting", current.who, current.img_token, current.topic_seed), "reveal"
    if current.step == "reflecting":
        return StudioState("done", current.who, current.img_token, current.topic_seed), "reflect"
    # done 之后：小晴温和送客
    return StudioState("done", current.who, current.img_token, current.topic_seed), "reflect"


def build_system_prompt(state: StudioState, team: list[str], action: str,
                        user_stroke: str, strokes: list[tuple[str, str]]) -> str:
    cfg = load_group_v2_config()
    names = member_names(team)
    profiles = "\n\n".join(
        f"【{cfg['members'][mid]['name']}】{cfg['members'][mid]['profile']}"
        f"{cfg['members'][mid]['core']}表达：{cfg['members'][mid]['voice']}"
        for mid in team
    )
    framing = framing_from_seed(state.topic_seed)
    stroke_lines = "\n".join(f"- {n}：{t}" for n, t in strokes) or "（还没有笔触）"
    if action == "strokes":
        task = (
            f"用户刚刚落笔：“{user_stroke}”。{names[1]}和{names[2]}依次各添一笔："
            "先用半句轻轻接住用户那一笔，再落笔；笔触必须以“我想在这幅画上加上”或"
            "“我希望这幅画上有”开头、一句话说完、不超过60字、和自己的校园生活或心境有关。\n"
            "最后小晴用两句话收住：四笔已经收齐，把四笔连成一句画面预告"
            "（“我已经看到这幅画的样子了——…”），告诉用户回一声“好了”，画室就把这四笔合成一幅真正的画。"
        )
        fmt = "输出：团友B、小晴半句、团友C、小晴收束预告。不写旁白，不用markdown加粗，全场不超过240字。"
    elif action == "reveal":
        # generate_painting 三级降级后永不返回 None：本轮一定有真画（最差是暖色兜底图）。
        # 提示词构建时 img_token 尚未生成，故这里无条件按「已合成真画」来描述。
        img_note = (
            "系统已把这四笔合成一幅真实的画（本轮会作为附件送出）。小晴揭晓："
            "两三句描述画里有什么、四笔各自在哪里；两位团友各用一句说出看到画的第一感受，"
            "可以互相接半句——这是本场唯一可以多聊两句的时刻；"
            "然后小晴立即收住（“画我们先记在心里”），并顺势抛出一个反思问题，例如："
            "现在看着这幅画，你自己那一笔和你落笔时想的一样吗？"
        )
        task = f"本场已落下的笔触：\n{stroke_lines}\n\n{img_note}"
        fmt = "输出：小晴揭晓 → 两位团友各一句（可互相接半句）→ 小晴收住+一个反思问题。不用markdown加粗，全场不超过280字。"
    else:  # reflect
        task = (
            "用户刚刚回应了反思问题。三位团友各用一句话回应用户"
            "（只回应、不提问、不建议，35—70字，说自己的画和此刻的感觉）；"
            "然后小晴总结：这幅画由四笔组成、每一笔分别是谁的，再用一句温柔的话把画送给用户，"
            "并说明明信片已经整理好、就在下面的附件里。"
        )
        fmt = "输出：三位团友各一句 → 小晴总结送别。不提问、不用markdown加粗，全场不超过260字。"
    return f"""你是「圆桌画室」的导演。这是一场安静的轻团体：小晴问一次，每人对一次；
除画作揭晓那一刻的即席感受外，成员之间不多轮交谈。场景是洒满阳光的画室，圆桌上摊着一张大画纸。

【三位团友（虚构AI角色）】
{profiles}

【本场命题】{framing}
【已落下的笔触】
{stroke_lines}

【铁律】
- 只扮演小晴和三位团友，绝不替用户发言或落笔。
- 每个发言独占一行，行首必须是【角色名】的方括号格式，例如【小晴】…或【{names[0]}】…；禁止“小晴：”这类冒号格式，禁止markdown加粗。
- 团友笔触句必须以“我想在这幅画上加上”或“我希望这幅画上有”开头，一句话、不含问号。
- 不出现围炉、夜话等旧场景词；不说教、不诊断。
- 用户说"随便"时视为把落笔权交给小晴，小晴代笔并点明代笔，不算用户失误。

【本轮任务】
{task}

【输出格式】
{fmt}"""


def stroke_issues(text: str, team_names: list[str], action: str) -> list[str]:
    """轻团体输出校验：说话人、笔触格式、问句约束（先剥离markdown加粗星号）。"""
    text = re.sub(r"\*+", "", text)
    issues: list[str] = []
    speakers = re.findall(r"^【([^】]+)】", text, re.MULTILINE)
    allowed = {"小晴", *team_names}
    if not speakers:
        return ["no-speaker-lines"]
    issues.extend(f"unknown-speaker:{n}" for n in speakers if n not in allowed)
    if action == "strokes":
        for n in team_names[1:]:
            if f"【{n}】" not in text:
                issues.append(f"missing-stroke:{n}")
        for line in text.splitlines():
            lm = re.match(r"^【([^】]+)】\s*(.+)$", line.strip())
            if lm and lm.group(1) in team_names and not STROKE_ANYWHERE_RE.search(lm.group(2)):
                issues.append(f"bad-stroke-format:{lm.group(1)}")
    if action == "reflect":
        for line in text.splitlines():
            lm = re.match(r"^【([^】]+)】\s*(.+)$", line.strip())
            if lm and lm.group(1) in team_names and ("？" in lm.group(2) or "?" in lm.group(2)):
                issues.append(f"reflect-question:{lm.group(1)}")
    if action == "reveal" and "？" not in text and "?" not in text:
        issues.append("missing-reflection-question")
    if len(text) > 800:
        issues.append("too-long")
    return issues


def studio_fallback(action: str, team_names: list[str], user_stroke: str) -> str:
    """生成失败时的确定性兜底：固定笔触与收束，绝不卡流程。"""
    b, c = (team_names[1:3] + ["团友乙", "团友丙"])[:2]
    if action == "strokes":
        return (
            f"【小晴】你这一笔画下了：{user_stroke}。\n"
            f"【{b}】我想在这幅画上加上食堂傍晚亮起来的灯，暖黄的那种。\n"
            f"【{c}】我想在这幅画上加上一段慢慢走的路，不用赶去哪里。\n"
            "【小晴】四笔收齐了。回我一声“好了”，我就把这四笔合成一幅真正的画。"
        )
    if action == "reveal":
        return (
            "【小晴】画回来了。台灯、热气、傍晚的灯和慢慢走的路在同一张纸上——"
            "每一笔都还在它落下的位置。\n"
            f"【{b}】看到它变成一幅真的画，心里轻轻落了一下。\n"
            f"【{c}】我那一笔在角落里待着，刚刚好。\n"
            "【小晴】画我们先记在心里。现在看着它——你自己那一笔，和你落笔时想的一样吗？"
        )
    a = team_names[0] if team_names else "团友甲"
    return (
        f"【{a}】这幅画里有你的那一笔，也有我们各自的一角。\n"
        f"【{b}】把它带回去吧，累的时候看一眼。\n"
        f"【{c}】下次想画的时候，画室还开着。\n"
        "【小晴】这幅画由四笔组成：一笔台灯、一笔你、一笔灯、一笔路。明信片已经整理好，"
        "就在下面的附件里。谢谢你今天认真画完这一场。"
    )
