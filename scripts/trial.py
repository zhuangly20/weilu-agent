"""本地试聊脚本：模拟完整会话流程（8轮制：相邀→开炉→6轮正式）。

用法：
  .venv/Scripts/python scripts/trial.py            # 完整学业压力主题
  .venv/Scripts/python scripts/trial.py self 5     # 指定主题与轮数
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8200"
KEY = "sk-weilu-dev-key"

# [开场倾诉, 确认开炉, 破冰答案, 主活动答案, 收尾答案, 视角回应, 真心话回应, 收夜回应]
THEME_OPENERS = {
    "self": [
        "最近感觉自己特别迷茫，不知道自己想要什么，每天忙但不知道在忙什么",
        "开炉吧，就他们几位",
        "大概像一台一直开着但没人用的电视机吧",
        "大概是一年前吧，我一个人在实验室阳台看完日出，那一刻觉得特别平静",
        "我想对自己说：你不用急着变成谁",
        "嗯，我想我会记住庄子说的那个'无用之用'",
        "谢谢大家",
        "嗯，期待",
    ],
    "academic": [
        "最近科研压力好大，导师一直催进度，同门都比我有产出，感觉整个人被抽干了",
        "好的，开炉吧",
        "大概是在实验室改论文到凌晨三点，看着窗外天一点点亮起来的时候",
        "是一块黑色的、棱角很多的石头，从开题被毙那天就开始背了，越来越重",
        "我想带走一句话：慢一点也没关系",
        "谢谢大家，今晚好受多了",
        "（点头）我明白",
        "嗯，谢谢",
    ],
    "connection": [
        "我是今年刚入学的新生，第一次离家这么远，室友们都各自玩各自的，有点孤独",
        "开炉吧～",
        "大概是室友们一起出去吃饭却没叫我，我一个人在宿舍吃泡面那晚",
        "我家楼下早餐店豆浆的味道，每天早上五点半就飘出来",
        "我会说：别怕，你会遇到很好的人的",
        "嗯，谢谢炉边的各位",
        "（点头）",
        "好的，谢谢",
    ],
    "career": [
        "马上要毕业了，秋招一个offer都没有，不知道留北京还是回家，很迷茫",
        "开炉吧",
        "深夜刷招聘软件，改第无数版简历的时候",
        "理想的一天是睡到自然醒，下午在一家小书店工作，晚上和朋友吃饭",
        "我想问：一年后的我，你过得还好吗？",
        "嗯，谢谢大家",
        "（点头）",
        "好的",
    ],
}

FILLERS = ["好的", "（点点头）", "嗯，我在听", "确实……", "谢谢"]


async def turn(client: httpx.AsyncClient, messages: list[dict]) -> str:
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"messages": messages, "stream": False},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def main(theme_id: str = "academic", rounds: int = 9) -> None:
    opener = THEME_OPENERS[theme_id]
    messages: list[dict] = [{"role": "user", "content": opener[0]}]
    async with httpx.AsyncClient(base_url=BASE) as client:
        for i in range(rounds):
            text = await turn(client, messages)
            print(f"\n{'='*18} 第{i+1}次回复 {'='*18}")
            print(text)
            messages.append({"role": "assistant", "content": text})
            user_next = opener[i + 1] if i + 1 < len(opener) else FILLERS[i % len(FILLERS)]
            messages.append({"role": "user", "content": user_next})
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    theme = sys.argv[1] if len(sys.argv) > 1 else "academic"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 9
    asyncio.run(main(theme, n))
