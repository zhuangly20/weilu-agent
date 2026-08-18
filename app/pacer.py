"""流式节奏器：把 LLM 增量重放成"逐字发言 + 发言间停顿"的群聊节奏。

协议约束下一次回复只能是一条消息，但实时流式观看时，
逐字打字 + 【角色】换行前的自然停顿，能呈现"成员一个个发言"的真实感。

- 发言行（以【开头的行）：行内逐字释放；新发言行出现前停顿（首行不停）
- 普通行（空行、括号行、标记行）：立即释放
- 上游生成慢于打字节奏时自动透传等待，不阻塞
- 环境变量：WEILU_PACING=false 可整体关闭；
  WEILU_PACE_CHAR_MS / WEILU_PACE_PAUSE_MS（LLM路径，默认28/1200）；
  WEILU_SCRIPT_PACE_CHAR_MS / WEILU_SCRIPT_PACE_PAUSE_MS（脚本路径，默认12/500）
内容与顺序严格保持不变（只是重新分块与延时）。
"""
from __future__ import annotations

import asyncio
import os
import random
from collections.abc import AsyncIterator


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def pacing_enabled() -> bool:
    return os.environ.get("WEILU_PACING", "true").strip().lower() != "false"


async def paced(
    source: AsyncIterator[str],
    scripted: bool = False,
    char_ms: float | None = None,
    pause_ms: float | None = None,
) -> AsyncIterator[str]:
    if not pacing_enabled():
        async for chunk in source:
            if chunk:
                yield chunk
        return

    if char_ms is None:
        char_ms = _env_float("WEILU_SCRIPT_PACE_CHAR_MS", 12.0) if scripted else _env_float("WEILU_PACE_CHAR_MS", 28.0)
    if pause_ms is None:
        pause_ms = _env_float("WEILU_SCRIPT_PACE_PAUSE_MS", 500.0) if scripted else _env_float("WEILU_PACE_PAUSE_MS", 1200.0)

    acc = ""                 # 已接收文本
    i = 0                    # 已释放位置
    at_line_start = True     # 当前是否处于行首
    in_speaker_line = False  # 当前是否在发言行内
    first_speaker_done = False

    async for chunk in source:
        if not chunk:
            continue
        acc += chunk
        while i < len(acc):
            ch = acc[i]
            if at_line_start:
                if ch == "【":
                    in_speaker_line = True
                    if first_speaker_done:  # 新发言人开口前停一拍
                        jitter = random.uniform(0.8, 1.25)
                        await asyncio.sleep(pause_ms * jitter / 1000.0)
                    first_speaker_done = True
                else:
                    in_speaker_line = False
                at_line_start = False
            if in_speaker_line:
                await asyncio.sleep(char_ms / 1000.0)
            yield ch
            i += 1
            if ch == "\n":
                at_line_start = True
                in_speaker_line = False
