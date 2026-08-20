"""三形态真实链路验收：时空对话 / 圆桌画室 / 新主题深度团体。"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import load_settings  # noqa: E402

BASE = "http://127.0.0.1:8202"
KEY = load_settings().api_key
FORBIDDEN = ("围炉", "炉火", "火炉", "火焰", "夜话", "深夜", "夜里", "小屋", "🔥", "🪵")


async def turn(client: httpx.AsyncClient, messages: list[dict]) -> tuple[str, list]:
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"messages": messages, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"], data.get("x_soda", {}).get("attachments") or data.get("attachments") or []


def check(name: str, text: str, attachments: list) -> None:
    bad = [w for w in FORBIDDEN if w in text]
    assert not bad, f"{name} 出现禁词: {bad}"
    print(f"\n===== {name} =====")
    print(text[:900])
    if attachments:
        print("附件:", [(a.get("fileName"), a.get("mimeType")) for a in attachments])


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE) as client:
        # 1) 时空对话：入口→报话题→介绍四人确认→提问(四答+辩论)→追问→告别(HTML留笺)
        msgs: list[dict] = [{"role": "user", "content": "我想参加时空对话"}]
        for step in ["最近失败了一次很受挫",
                     "满意，我想问怎么看待失败？我考研失败了一次，不敢跟家里说",
                     "追问项羽，你最后悔的是什么",
                     "今天就到这里，谢谢各位"]:
            text, att = await turn(client, msgs)
            check(f"面板·{step[:10]}", text, att)
            msgs.append({"role": "assistant", "content": text})
            msgs.append({"role": "user", "content": step})
        # 告别轮：验证《时空留笺》HTML 报告
        text, att = await turn(client, msgs)
        check("面板·告别留笺", text, att)
        assert any(a.get("fileName") == "时空留笺.html" for a in att), "告别轮应产出时空留笺报告"

        # 2) 圆桌画室：入口→落笔→合成→揭晓(真画)→反思(明信片HTML)
        msgs = [{"role": "user", "content": "来圆桌画室，画一幅关于最近的我的画"}]
        for step in ["我想在这幅画上加上一盏宿舍的小夜灯",
                     "好了",
                     "和我落笔时想的一样，很安心"]:
            text, att = await turn(client, msgs)
            check(f"画室·{step[:10]}", text, att)
            msgs.append({"role": "assistant", "content": text})
            msgs.append({"role": "user", "content": step})
        # 反思轮：验证明信片 HTML 附件（含画）
        text, att = await turn(client, msgs)
        check("画室·明信片", text, att)
        assert any(a.get("fileName") == "圆桌画室·明信片.html" for a in att), "反思轮应产出明信片HTML"

        # 3) 新生适应：开场→自我介绍→第一段聚焦
        msgs = [{"role": "user", "content": "我是大一新生，想家，室友都说方言我插不上话"}]
        text, att = await turn(client, msgs)
        check("新生·开场", text, att)
        msgs.append({"role": "assistant", "content": text})
        msgs.append({"role": "user", "content": "我叫小林，大一，新闻系。最想家的是周日晚上和妈妈打完电话那会儿。"})
        text, att = await turn(client, msgs)
        check("新生·入桌总结+进入故事", text, att)

        # 4) 爱情探索：开场+自我介绍+棱镜前段
        msgs = [{"role": "user", "content": "暗恋一个人两年了不敢表白，想参加爱情探索"}]
        text, att = await turn(client, msgs)
        check("爱情·开场", text, att)
        msgs.append({"role": "assistant", "content": text})
        msgs.append({"role": "user", "content": "叫我阿哲，大三。喜欢社团里一个女生，快两年了，一直没敢说。"})
        text, att = await turn(client, msgs)
        check("爱情·入桌+进入心动圆桌", text, att)

    print("\nALL FORMS ACCEPTED ✔")


if __name__ == "__main__":
    asyncio.run(main())
