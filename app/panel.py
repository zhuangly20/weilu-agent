"""时空对话：史记人物面板（用户一问、四位各答、辩一轮）。

形态是对话面板而非团体辅导：没有关系完成条件，状态机为
intro（问话题/想找谁）→ invite（介绍四人+满意确认+预告报告）
→ ask（提问）→ await（追问/换题/换人重分配）→ farewell（HTML报告）
五个可枚举状态。
人物 skill 直接来自史记项目 registry（config/shiji_registry.json）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .config import load_shiji_figures
from .session import extract_text

MARKER_RE = re.compile(
    r"<!--QXSD\|figs=([^|>]*)\|asked=(\d+)\|stage=([a-z_]+)(?:\|focus=([^|>]*))?-->"
)
PANEL_NAME_RE = re.compile(r"时空对话|历史人物对话|跟古人.*对话|和古人.*聊")
NEXT_Q_RE = re.compile(r"换一题|下一个问题|再问一个问题|新问题|换.*问题")
END_RE = re.compile(r"结束|再见|告别|散场|到这里|留笺|谢谢.*们.*辛苦|先聊到这")
FOLLOW_RE = re.compile(r"追问|继续问|再问(?P<name>[\u4e00-\u9fa5]{2,4})|(?P<name2>[\u4e00-\u9fa5]{2,4}).*(?:你怎么看|你说说|聊聊你的看法|回答一下)")
# intro 阶段视为有效话题/点名：有一定长度，或点名了人物
TOPIC_ENOUGH_RE = re.compile(r"^.{0,4}$")
# 换人意图（换题语义在 NEXT_Q_RE，先于本表判断）
REJECT_RE = re.compile(r"换人|换一批|换几位|换.*先生|换.*人物|不满意|不喜欢|不要这|重新配|重新分配|再配")
# invite 阶段：仅裸确认（没顺带说出问题）时才回「请讲」
BARE_CONFIRM_RE = re.compile(
    r"^(?:好|好的|好呀|好嘞|好哒|好的呢|行|行的|可以|可|没问题|没意见|满意|就这样|都行|"
    r"随便|开始|开始吧|讲吧|问吧|问|嗯|对|对的|ok|OK|Okay|Okay)\s*[。！!～~、]?\s*$"
)

# 预设主题组合：题目关键词 → 四位人物 id 的稳定组合
PRESET_TOPICS: list[tuple[str, tuple[str, ...]]] = [
    (r"失败|挫折|坚持|不甘|低谷|重来", ("xiangyu", "yuewanggoujian", "suqin", "hanxin")),
    (r"选择|抉择|代价|转行|放弃|路口", ("fanju", "lisi", "shangyang", "wuzixu")),
    (r"孤独|不被理解|坚守|委屈|误解", ("quyuan", "boyi", "shuqi", "simaqian")),
    (r"进退|处世|分寸|职场|人际|上下级", ("laozi", "kongzi", "liuhou", "luzhonglian")),
    (r"权力|野心|竞争|对手|胜负", ("liubang", "lvzhi", "qinshihuang", "liuche")),
    (r"才华|际遇|怀才不遇|被埋没|赏识", ("liguang", "simaxiangru", "bianque", "canggong")),
    (r"勇气|冒险|孤注一掷|义气|拼命", ("jingke", "niezheng", "yurang", "zhuanzhu")),
    (r"治国|管理|带团队|组织|领袖", ("guanzhong", "xiaohexiangguo", "caocanxiangguo", "weiwenhou")),
]


@dataclass
class PanelState:
    figure_ids: list[str] = field(default_factory=list)
    asked: int = 0
    stage: str = "intro"  # intro / invite / ask / await / farewell
    focus: str = ""  # 追问指定的人物 id
    topic: str = ""  # intro 阶段收集到的话题/点名（跨轮保留，用于重分配与报告）


def marker(state: PanelState) -> str:
    return (
        f"<!--QXSD|figs={','.join(state.figure_ids)}|asked={state.asked}"
        f"|stage={state.stage}" + (f"|focus={state.focus}" if state.focus else "") + "-->"
    )


def reconstruct(messages: list[dict]) -> PanelState | None:
    found = None
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        hits = MARKER_RE.findall(extract_text(msg.get("content")))
        if hits:
            found = hits[-1]
    if found is None:
        return None
    figs, asked, stage, focus = found
    return PanelState(
        figure_ids=[x for x in figs.split(",") if x],
        asked=int(asked), stage=stage, focus=focus or "",
    )


def figure_by_id() -> dict[str, dict]:
    return {p["id"]: p for p in load_shiji_figures()}


def figure_names(figs: list[str]) -> list[str]:
    by_id = figure_by_id()
    return [by_id[i]["name"] for i in figs if i in by_id]


def named_figures(text: str) -> list[str]:
    """用户点名的人物 id。多字名直接匹配；单字名（汤/禹）要求独立成词，避免嵌在常用词里。"""
    hits: list[str] = []
    for p in load_shiji_figures():
        name = p["name"]
        if len(name) >= 2:
            matched = name in text
        else:
            matched = re.search(rf"(?<![一-龥]){re.escape(name)}(?![一-龥])", text) is not None
        if matched and p["id"] not in hits:
            hits.append(p["id"])
    return hits


def match_figures(text: str) -> list[str]:
    """预设主题组合优先；用户点名次之；关键词计分兜底；恒定返回四位。"""
    lowered = text.strip()
    for pattern, ids in PRESET_TOPICS:
        if re.search(pattern, lowered):
            return list(ids)
    by_id = figure_by_id()
    hits = [i for i in named_figures(text) if i in by_id]
    if len(hits) >= 4:
        return hits[:4]
    if hits:
        pool = [p["id"] for p in load_shiji_figures() if p["id"] not in hits]
        digest = hashlib.sha256(lowered.encode("utf-8")).hexdigest()
        while len(hits) < 4 and pool:
            hits.append(pool.pop(int(digest, 16) % len(pool)))
        return hits
    scored: list[tuple[int, str]] = []
    for p in load_shiji_figures():
        blob = p["name"] + "".join(p.get("personality") or []) + p.get("quote", "")
        score = sum(1 for kw in re.split(r"[\s,，。、]", lowered) if kw and kw in blob)
        scored.append((score, p["id"]))
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored[:4]]


def detect_panel_entry(text: str) -> bool:
    if PANEL_NAME_RE.search(text):
        return True
    return len(named_figures(text)) >= 2


def next_state(last_user: str, current: PanelState) -> tuple[PanelState, str]:
    """返回 (新状态, 动作)：intro / invite / ask / answer / farewell。"""
    if current.stage == "intro":
        # 用户给出话题或点名 → 分配四人并介绍（invite）
        figs = match_figures(last_user) if last_user.strip() and not TOPIC_ENOUGH_RE.match(last_user.strip()) \
            else list(current.figure_ids)
        return PanelState(figs, 0, "invite", "", last_user.strip()[:200]), "invite"
    if current.stage == "invite":
        if REJECT_RE.search(last_user):
            # 不满意 → 换一批重新分配（换人意图融入话题，避免原班人马）
            base = current.topic or last_user
            figs = reshuffle_figures(base, current.figure_ids)
            return PanelState(figs, 0, "invite", "", current.topic), "invite"
        if BARE_CONFIRM_RE.match(last_user.strip()):
            # 只说了「满意/好/开始」，没带问题 → 请讲
            return PanelState(current.figure_ids, 0, "ask", "", current.topic), "ask"
        # 满意并顺带说出了问题 → 直接把这句话当作第一问，进入四人作答
        return PanelState(current.figure_ids, 1, "await", "", current.topic), "answer"
    # ask / await
    if END_RE.search(last_user):
        return PanelState(current.figure_ids, current.asked, "farewell", "", current.topic), "farewell"
    m = FOLLOW_RE.search(last_user)
    if NEXT_Q_RE.search(last_user) and not m:
        return PanelState(current.figure_ids, current.asked, "ask", "", current.topic), "ask"
    if REJECT_RE.search(last_user) and current.stage == "await":
        # 作答后想换人：重新分配并回到介绍确认
        base = current.topic or last_user
        figs = reshuffle_figures(base, current.figure_ids)
        return PanelState(figs, current.asked, "invite", "", current.topic), "invite"
    focus = ""
    if m:
        by_id = figure_by_id()
        target = m.group("name") or m.group("name2") or ""
        for fid in current.figure_ids:
            name = by_id.get(fid, {}).get("name", "")
            if name and (name == target or name in last_user):
                focus = fid
                break
    asked = current.asked + 1
    return PanelState(current.figure_ids, asked, "await", focus, current.topic), "answer"


def reshuffle_figures(text: str, previous: list[str]) -> list[str]:
    """换一批：优先用户点名的；否则按话题重新匹配并尽量避开上一批。"""
    named = [i for i in named_figures(text) if i in figure_by_id()]
    if len(named) >= 4:
        return named[:4]
    fresh = match_figures(text)
    keep = [i for i in fresh if i not in previous]
    if len(keep) >= 4:
        return keep[:4]
    # 匹配结果与上一批重叠过多时，从未上榜人物里补齐
    pool = [p["id"] for p in load_shiji_figures() if i_not_in(p["id"], previous + keep)]
    digest = hashlib.sha256((text + "|" + ",".join(previous)).encode("utf-8")).hexdigest()
    out = list(keep)
    while len(out) < 4 and pool:
        out.append(pool.pop(int(digest, 16) % len(pool)))
    return out[:4]


def i_not_in(item: str, seq: list[str]) -> bool:
    return item not in seq


def intro_script() -> str:
    """第一步：先问用户想聊什么、想找谁聊。"""
    return (
        "【小晴】欢迎来到「时空对话」～这里可以把问题放到千年前的桌上：\n"
        "【小晴】先跟我说说——你今天想聊什么？可以是正在烦着你的一件事（比如失败、选择、迷茫），"
        "也可以直接点名想找谁聊（司马迁、项羽、张良……库里有一百多位）。\n"
        "【小晴】我会照你的话题为你配四位先生，配好之后你可以确认，不满意就换一批。"
        "活动结束时，还会送你一份《时空留笺》——四位先生各留一句送给你的话。"
    )


def _intro_of(fig_id: str) -> str:
    """人物的一句话身份背景（确定性，取自 registry）。"""
    p = figure_by_id()[fig_id]
    persona = "，".join((p.get("personality") or [])[:2]) or "史册中人"
    return f"{p.get('era', '')}·{persona}"


def opening_script(state: PanelState) -> str:
    """invite 阶段：介绍四位姓名+身份背景，询问是否满意，预告报告。"""
    by_id = figure_by_id()
    lines = [
        f"【{by_id[i]['name']}】{_intro_of(i)}"
        for i in state.figure_ids if i in by_id
    ]
    listed = "\n".join(lines)
    return (
        "【小晴】照你的话题，我为你请到了四位先生——\n"
        f"{listed}\n"
        "【小晴】这四位合你眼缘吗？满意就说出你的问题，我们马上开始；"
        "不满意就说「换一批」，我重新为你配人。\n"
        "【小晴】玩法：你问一个问题，四位先生各自作答，然后当着你的面辩一轮——"
        "可以随时追问任何一位、换一题或换人。\n"
        "【小晴】本场结束时你会收到一份《时空留笺》：四位先生各留一句送给你的话，收进一份报告里。"
    )


def _figure_block(fig_id: str) -> str:
    p = figure_by_id()[fig_id]
    return f"【{p['name']}｜{p.get('era', '')}】\n{p['systemPrompt'].strip()}"


def build_system_prompt(state: PanelState, question: str, is_farewell: bool = False) -> str:
    by_id = figure_by_id()
    blocks = "\n\n".join(_figure_block(f) for f in state.figure_ids if f in by_id)
    names = figure_names(state.figure_ids)
    first = names[0] if names else "先生"
    if is_farewell:
        task = (
            f"用户要结束了。每位先生用一句话向用户道别（{first}先说，依次到末位），"
            "只留一句从自己一生里掏出来的、送给这位用户的话，不再展开论述；"
            "这句话会原文收进《时空留笺》报告，要写得值得被收藏。"
            "最后小晴两句收束并送别。"
        )
        fmt = "输出依次为：四位先生的赠言（每人不超过40字）+ 小晴收束（不超过60字）。"
    else:
        focus_name = by_id.get(state.focus, {}).get("name", "")
        task = (
            f"用户的问题是：“{question}”。"
            + (f"这是对{focus_name}的追问：{focus_name}必须第一个作答且直接回应追问；" if focus_name else "")
            + "四位先生依次作答，每人80—120字：先亮明自己时代的立场，再落到对这一问的回答；"
            "四人的切入点必须明显不同，不得互相客套附和。\n"
            "作答之后，同一次输出里进行一轮辩论：至少两位先生在回应里点名另一位并温和反驳一处"
            "（基于时代立场分歧，不人身攻击、每位最多反驳一处、不抬杠）。\n"
            "最后由小晴收束：一句话点出分歧所在，并邀请用户追问某位、换一题、换人或结束。"
        )
        fmt = (
            "输出依次为：【小晴】一句接住问题（不超过40字）→ 四位先生作答 → 辩论 → 【小晴】收束邀请。"
            "全场不超过900字，不写旁白和标题。"
        )
    return f"""你是「时空对话」的导演：一位用户向四位史记人物提问，你组织他们作答与辩论。

【四位人物（skill 全文，人格与史实边界以此为准）】
{blocks}

【铁律】
- 每人只能讲自己时代内的事实与观念，不使用自己身后时代的事物，不虚构 skill 未写的事迹。
- 不评论当代具体政治人物与事件；涉及当下话题时，先生们只从各自时代经验引申，小晴负责把话拉回历史。
- 辩论基于立场分歧，就事论事；不得出现围炉、夜话等与场景无关的词。
- 你只能扮演小晴和上面四位人物，绝不输出【用户】【我】或替用户说话。

【本轮任务】
{task}

【输出格式】
{fmt}
发言行只能以【小晴】或四位先生之名开头；不写「辩论」「作答」之类的小节标签，不用markdown加粗，不加序号。"""


def build_user_content(messages: list[dict], last_user: str, cap: int = 3600) -> str:
    entries: list[str] = []
    for msg in messages[:-1]:
        role = msg.get("role")
        text = MARKER_RE.sub("", extract_text(msg.get("content"))).strip()
        if not text or role not in ("user", "assistant"):
            continue
        entries.append(("用户" if role == "user" else "面板") + "：" + text[:600])
    history = "\n".join(entries)[-cap:]
    return f"【对话历史】\n{history}\n\n【用户刚才说】\n{last_user}\n\n请完成本轮面板输出。"
