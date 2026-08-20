"""时空对话·时空留笺：单文件 HTML 报告（确定性渲染，无 LLM JSON）。

内容来源：
- 话题：PanelState.topic（intro 阶段用户原话）
- 四位人物：姓名+era+身份（registry），来自 marker 重建
- 赠言：farewell 轮文本里每位【人物名】后的第一句（正则提取，无则用人物 quote）
"""
from __future__ import annotations

import datetime as dt
import html
import re


def _esc(v: object) -> str:
    return html.escape(str(v if v is not None else ""))


def _gift_lines(body: str, names: list[str], quotes: dict[str, str]) -> list[tuple[str, str]]:
    """从告别文本提取每人赠言；提取不到时回退到 registry 里的 quote。"""
    gifts: list[tuple[str, str]] = []
    for name in names:
        m = re.search(rf"【{re.escape(name)}】\s*([^【\n]+)", body)
        text = m.group(1).strip().rstrip("。") if m else ""
        if not text:
            text = (quotes.get(name) or "各自珍重。").strip().rstrip("。")
        gifts.append((name, text[:60]))
    return gifts


def render(topic: str, figure_rows: list[dict], body: str,
           gifts_fallback: dict[str, str] | None = None) -> bytes:
    """figure_rows: [{id,name,era,persona}]；body: farewell 轮全文。"""
    today = dt.date.today().strftime("%Y年%m月%d日")
    quotes = gifts_fallback or {}
    names = [f["name"] for f in figure_rows]
    gifts = _gift_lines(body, names, quotes)
    gift_html = "".join(
        f"<div class='gift'><div class='who'>—— {_esc(n)}</div>"
        f"<div class='say'>{_esc(t)}。</div></div>"
        for n, t in gifts
    )
    roster = "、".join(names)
    topic_line = _esc(topic[:80]) if topic else "一场跨越时空的对话"
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>时空留笺 · 清心圆桌</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,"PingFang SC","Microsoft YaHei",serif;}}
  body{{background:linear-gradient(180deg,#f6f1e4,#efe6d2);color:#4a4032;padding:24px 16px 40px;}}
  .wrap{{max-width:600px;margin:0 auto;}}
  .card{{background:#fffdf6;border-radius:16px;padding:22px;box-shadow:0 3px 14px rgba(120,90,50,.14);margin-bottom:14px;
        border:1px solid #e8dcc2;}}
  header{{text-align:center;padding:4px 0 14px;}}
  header .t{{font-size:22px;font-weight:700;color:#8a6d3b;letter-spacing:2px;}}
  header .s{{font-size:12px;color:#a89877;margin-top:6px;}}
  .topic{{text-align:center;font-size:14px;color:#6b5d48;background:#f4ecd8;border-radius:10px;
         padding:10px 12px;margin-bottom:14px;line-height:1.8;}}
  h2{{font-size:15px;color:#8a6d3b;margin-bottom:12px;letter-spacing:1px;}}
  .fig{{display:flex;gap:8px;padding:7px 0;border-bottom:1px dashed #e8dcc2;font-size:13.5px;line-height:1.7;}}
  .fig:last-child{{border-bottom:none;}}
  .fig .nm{{flex:none;font-weight:700;color:#4a4032;min-width:4em;}}
  .fig .id2{{color:#8a7a5f;}}
  .gift{{margin:14px 0;padding:14px 16px;background:linear-gradient(135deg,#fbf6e9,#f3ead3);
        border:1px solid #e3d5b5;border-radius:12px;}}
  .gift .say{{font-size:15.5px;line-height:1.9;color:#5a4a32;}}
  .gift .who{{text-align:right;font-size:12.5px;color:#9a8a6a;margin-top:6px;}}
  footer{{text-align:center;font-size:11px;color:#b0a184;padding:8px 0 0;line-height:1.8;}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="t">⏳ 时空留笺</div>
    <div class="s">{today}｜清心圆桌 · 时空对话</div>
  </header>

  <div class="card">
    <div class="topic">本场话题：{topic_line}</div>
    <h2>👤 今日同桌</h2>
    {''.join(f"<div class='fig'><span class='nm'>{_esc(f['name'])}</span>"
             f"<span class='id2'>{_esc(f.get('era', ''))}·{_esc(f.get('persona', ''))}</span></div>"
             for f in figure_rows)}
    <div style="font-size:12px;color:#a89877;margin-top:10px">（{roster}，就此别过）</div>
  </div>

  <div class="card">
    <h2>💌 四位先生送给你的话</h2>
    {gift_html}
  </div>

  <footer>清心圆桌 · 时空对话<br>先生们只讲自己时代里的事，赠言仅供留念</footer>
</div>
</body>
</html>"""
    return html_doc.encode("utf-8")
