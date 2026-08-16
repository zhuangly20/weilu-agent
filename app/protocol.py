"""OpenAI 兼容协议帧构造（严格对齐清小搭接入指南 §3）。"""
from __future__ import annotations

import json
import time
import uuid


def _cid() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:12]}"


def sse_frame(delta: dict, finish: str | None = None, usage: dict | None = None,
              extra: dict | None = None) -> str:
    chunk: dict = {
        "id": _cid(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        chunk["usage"] = usage
    if extra:
        chunk.update(extra)
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"


def full_response(
    text: str, usage: dict, finish_reason: str = "stop", attachments: list | None = None
) -> dict:
    resp = {
        "id": _cid(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "weilu-agent",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
    if attachments:
        resp["x_soda"] = {"attachments": attachments}
    return resp


def stream_frames(text: str, usage: dict) -> list[str]:
    """整段文本按块转成标准帧序列（role → content* → stop+usage → DONE）。"""
    frames = [sse_frame({"role": "assistant"})]
    step = 48
    if text:
        for i in range(0, len(text), step):
            frames.append(sse_frame({"content": text[i : i + step]}))
    else:
        frames.append(sse_frame({"content": ""}))
    frames.append(sse_frame({}, finish="stop", usage=usage))
    frames.append(sse_done())
    return frames
