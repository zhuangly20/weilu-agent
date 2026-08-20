"""生成新版《圆桌留笺》的本地样式预览。"""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.report_v2_html import render  # noqa: E402


fields = {
    "participant_name": "凌云",
    "discussion_topics": [
        "白天实习、晚上推进论文，同时秋招尚未开始带来的多线压力",
        "担心暴露“不知道”，需要向师兄求助却迟迟按不下发送键",
        "已经很累却仍然答应别人，难以说出自己的边界",
        "实验结果异常后独自承受，不知道该向谁开口",
    ],
    "stress_suggestions": [
        "先约一位熟悉的师姐聊一次简历，只收集反馈，不要求当天投递。",
        "把“开始秋招”拆成浏览岗位、收藏三个岗位、修改一段经历三个小动作。",
        "身体僵住时先离开屏幕走十分钟，再决定继续、求助还是休息。",
        "需要拒绝时给出边界和替代方案：今晚做不了，明天下午可以帮你看十分钟。",
        "异常数据先告诉一位可信任的同门，不必等到完全想清楚才开口。",
        "跑步、唱歌或找朋友聊天可以用来短暂换挡，不要求一次把压力全部消除。",
    ],
    "pressure_before": 8,
    "pressure_after": 6,
    "leader_note": (
        "凌云，谢谢你既让大家看见了你的慌，也认真接住了陈默的难过。"
        "你不只是在这里被照顾，也真实地照顾了别人。离开圆桌以后，不必立刻翻过整座山；"
        "先选一个今天愿意做的小动作，就已经是在为自己腾出一点路。"
    ),
}

target = PROJECT_ROOT / "deploy" / "圆桌留笺-新版预览.html"
target.write_bytes(render(fields, ["林之衡", "许南枝", "陈默"]))
print(target)
