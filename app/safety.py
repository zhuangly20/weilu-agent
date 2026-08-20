"""危机检测：两级正则（迁移自心晴谷），high 走脚本化响应不经 LLM。"""
from __future__ import annotations

import re

from .config import load_crisis_config

_CACHE: dict[str, object] = {}


def _compiled() -> tuple[list[tuple[re.Pattern[str], str]], dict]:
    if "cache" not in _CACHE:
        cfg = load_crisis_config()
        rules = [
            (re.compile(rule["pattern"]), str(rule["severity"]))
            for rule in cfg.get("keywords", [])
        ]
        _CACHE["cache"] = (rules, cfg)
    return _CACHE["cache"]  # type: ignore[return-value]


def detect(text: str) -> str | None:
    """返回 "high" / "medium" / None。high 优先。"""
    rules, _ = _compiled()
    level: str | None = None
    for pattern, severity in rules:
        if pattern.search(text):
            if severity == "high":
                return "high"
            level = "medium"
    return level


def aid_reply() -> str:
    return "\n".join([
        "【小晴】我先暂停圆桌。你刚才说了想自杀／不想活，这比任何讨论都重要。",
        "【小晴】请先只回答一个问题：你现在有没有马上伤害自己的打算、计划，或已经拿在身边的东西？如果有，先不要一个人待着，立刻叫室友、同学、辅导员或宿管到你身边，并拨打 120 或 110。",
        "【小晴】你也可以立刻联系清华在校学生 7×24 小时心理热线 010-62785252；工作时间可联系学生心理发展指导中心 010-62782007。现在不需要解释得完整，只要回复“安全”或“不安全”。",
    ])


def safety_followup() -> str:
    return "\n".join([
        "【小晴】谢谢你继续告诉我。圆桌还先暂停着。请直接告诉我：你现在是“安全”，还是“有马上伤害自己的危险”？",
        "【小晴】如果有危险，请立刻联系身边的人陪你、远离可能伤害自己的物品，并拨打 120、110 或清华 7×24 小时心理热线 010-62785252。",
    ])


def safety_support() -> str:
    return "\n".join([
        "【小晴】谢谢你告诉我现在安全。为了让这一刻更稳一点，先做一件现实的小事：给一位可信任的人发消息请他陪你待会儿、去室友身边，或把可能伤害自己的物品先放远。",
        "【小晴】做完后回我“好了”或“继续”。如果那种冲动又回来，先联系 010-62785252，或在紧急时拨打 120、110。",
    ])


def medium_empathy_instruction() -> str:
    _, cfg = _compiled()
    return str(cfg.get("medium_empathy_instruction", "")).strip()
