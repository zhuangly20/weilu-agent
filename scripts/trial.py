"""本地试聊脚本：模拟完整会话流程（8轮制：相邀→正式团体环节）。

用法：
  .venv/Scripts/python scripts/trial.py            # 完整减压安心之旅主题
  .venv/Scripts/python scripts/trial.py self 5     # 指定主题与轮数
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8200"
KEY = load_settings().api_key

# [开场倾诉, 确认参与, 破冰答案, 主活动答案, 收尾答案, 视角回应, 真心话回应, 报告前回应]
THEME_OPENERS = {
    "self": [
        "最近感觉自己特别迷茫，不知道自己想要什么，每天忙但不知道在忙什么",
        "好的，就和这几位桌友一起聊吧",
        "大概像一台一直开着但没人用的电视机吧",
        "大概是一年前吧，我一个人在实验室阳台看完日出，那一刻觉得特别平静",
        "我想对自己说：你不用急着变成谁",
        "嗯，我想我会记住庄子说的那个'无用之用'",
        "谢谢大家",
        "嗯，期待",
    ],
    "academic": [
        "最近压力很大，想参加减压安心之旅",
        "好的，就和这几位桌友一起聊吧",
        "最近导师连续催进度，看到同门发论文我就觉得自己很差，压力大概8分。",
        "肩颈一直绷着，胸口也闷；心里又焦虑又烦躁，晚上躺下还会反复想没做完的事。",
        "最沉的是一直被比较、又怕让导师失望的感觉，好像只要停下来就会落后。",
        "刚才呼气时肩膀松了一点，现在大概6分，但想到明天还是会紧张。",
        "听到大家也有狼狈和停下来的时候，我没那么像一个失败者了。",
        "我想先把明天最重要的一件事写下来，做完就允许自己休息。",
    ],
    "connection": [
        "我是今年刚入学的新生，第一次离家这么远，室友们都各自玩各自的，有点孤独",
        "好的，就和这几位桌友一起聊吧",
        "大概是室友们一起出去吃饭却没叫我，我一个人在宿舍吃泡面的时候",
        "我家楼下早餐店豆浆的味道，每天早上五点半就飘出来",
        "我会说：别怕，你会遇到很好的人的",
        "嗯，谢谢圆桌上的各位",
        "（点头）",
        "好的，谢谢",
    ],
    "career": [
        "马上要毕业了，秋招一个offer都没有，不知道留北京还是回家，很迷茫",
        "好的，就和这几位桌友一起聊吧",
        "反复刷招聘软件，改第无数版简历的时候",
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
