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
    _, cfg = _compiled()
    msg = cfg["aid_message"]
    lines = [f"【小晴】{msg['title']}", "", msg["body"].strip(), "", msg["continue_hint"].strip()]
    return "\n".join(lines).strip()


def medium_empathy_instruction() -> str:
    _, cfg = _compiled()
    return str(cfg.get("medium_empathy_instruction", "")).strip()
