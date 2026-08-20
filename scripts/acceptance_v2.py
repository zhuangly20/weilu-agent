"""减压安心之旅 v2 提交前真实链路验收。

使用当前 .env 中的应用密钥调用本地 OpenAI 兼容接口，并验证：
四人焦点顺序 → 共创 → 告别 → 实际 HTML 附件。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import group_v2
from app.config import load_settings


BASE = "http://127.0.0.1:8200"
KEY = load_settings().api_key


async def main() -> None:
    messages: list[dict[str, str]] = []
    steps = [
        ("我想参加减压安心之旅", 1, "", "main"),
        ("我叫凌云，心理学博六。白天实习，晚上写论文，秋招还没投，我最近特别慌。", 2, "linzhiheng", "story_first"),
        ("之衡，你师兄也是这么过来的，他应该能理解你。", 2, "linzhiheng", "story_second"),
        ("下一位", 2, "xunanzhi", "story_first"),
        ("南枝，真正的朋友不需要靠你一直熬夜来维持。", 2, "xunanzhi", "story_second"),
        ("下一位", 2, "chenmo", "story_first"),
        ("陈默，我觉得数据重要，但也可以先缓一晚。", 2, "chenmo", "story_second"),
        ("下一位", 2, "user", "story_first"),
        ("我同时做的事情太多，科研又不是一天两天能做完。我觉得自己很没用，情绪低落就更不想做。", 2, "user", "story_second"),
        ("这一段够了，进入下一项", 3, "", "main"),
        ("我愿意听听大家有哪些具体办法。", 3, "", "mutual"),
        ("收获与告别", 4, "", "main"),
        ("我带走的是：允许自己休息。", 4, "", "ask_end"),
        ("再见", 4, "", "report"),
    ]

    async with httpx.AsyncClient(base_url=BASE, timeout=180) as client:
        final_attachments: list[dict] = []
        for index, (user_text, phase, focus, mode) in enumerate(steps, 1):
            messages.append({"role": "user", "content": user_text})
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {KEY}"},
                json={"messages": messages, "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
            assistant_text = payload["choices"][0]["message"]["content"]
            final_attachments = payload.get("x_soda", {}).get("attachments", [])
            messages.append({"role": "assistant", "content": assistant_text})
            state = group_v2.reconstruct([{"role": "assistant", "content": assistant_text}])
            assert state is not None, f"第{index}轮缺少状态标记"
            assert (state.phase, state.focus, state.mode) == (phase, focus, mode), (
                f"第{index}轮状态错误：实际 {(state.phase, state.focus, state.mode)}，"
                f"预期 {(phase, focus, mode)}"
            )
            print(f"{index:02d} PASS  phase={phase} focus={focus or '-'} mode={mode}")
            if "--verbose" in sys.argv:
                visible = assistant_text.split("<!--QXG2", 1)[0].strip()
                print(visible)

        assert len(final_attachments) == 1, "结束后必须返回且只返回一份《圆桌留笺》"
        attachment = final_attachments[0]
        assert attachment["fileName"].endswith("圆桌留笺.html")
        file_response = await client.get(attachment["fileUrl"])
        file_response.raise_for_status()
        assert "text/html" in file_response.headers.get("content-type", "")
        assert "圆桌留笺" in file_response.text

    print(f"HTML PASS  {attachment['fileName']}  {len(file_response.content)} bytes")


if __name__ == "__main__":
    asyncio.run(main())
