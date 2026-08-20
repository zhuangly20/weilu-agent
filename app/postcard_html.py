"""圆桌画室·明信片：单文件 HTML，字段全部确定性来自对话（无 LLM JSON）。

画作图以临时 URL 内嵌（onerror 隐藏，回落到文字笔触排版），
因此图像生成失败时明信片依然完整成立。
"""
from __future__ import annotations

import datetime as dt
import html
import re


def _esc(v: object) -> str:
    return html.escape(str(v if v is not None else ""))


def _leader_note(body: str) -> str:
    """取小晴最后一段话作为寄语（确定性解析，无生成）。"""
    blocks = re.findall(r"【小晴】\s*([^【]+)", body)
    note = blocks[-1].strip() if blocks else ""
    return note or "这幅画由你们四笔组成——谢谢你认真画完这一场。"


def render(framing: str, strokes: list[tuple[str, str]], body: str,
           artwork_url: str = "") -> bytes:
    today = dt.date.today().strftime("%Y年%m月%d日")
    note = _esc(_leader_note(body))
    rows = "".join(
        f"<div class='stroke'><span class='who'>{_esc(n)}</span>"
        f"<span class='what'>{_esc(t)}</span></div>"
        for n, t in strokes
    ) or "<div class='stroke'><span class='who'>画室</span><span class='what'>今天的画先留在文字里。</span></div>"
    art = (
        f"<img src='{_esc(artwork_url)}' alt='共同画作' onerror=\"this.closest('.artwork').classList.add('noimg')\">"
        if artwork_url else ""
    )
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>圆桌画室 · 明信片</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}}
  body{{background:linear-gradient(180deg,#fff8ec,#ffedd6);color:#5a4636;padding:24px 16px 40px;}}
  .wrap{{max-width:600px;margin:0 auto;}}
  .card{{background:#fff;border-radius:18px;padding:20px;box-shadow:0 3px 14px rgba(160,110,60,.12);margin-bottom:14px;}}
  header{{text-align:center;padding:6px 0 16px;}}
  header .t{{font-size:21px;font-weight:700;color:#e0603c;}}
  header .s{{font-size:12px;color:#a6825e;margin-top:6px;}}
  .artwrap{{border:6px solid #fff3dd;border-radius:14px;overflow:hidden;background:#fff;box-shadow:0 2px 10px rgba(160,110,60,.10);}}
  .artwrap img{{width:100%;display:block;}}
  .artwrap.noimg img{{display:none;}}
  h2{{font-size:15px;color:#d95f36;margin-bottom:10px;}}
  .stroke{{display:flex;gap:10px;padding:8px 0;border-bottom:1px dashed #f3e3cf;font-size:14px;line-height:1.7;}}
  .stroke:last-child{{border-bottom:none;}}
  .who{{flex:none;font-weight:600;color:#9b7ede;min-width:4em;}}
  .say{{background:linear-gradient(135deg,#fff3d9,#ffe4ea);border:1px solid #ffd9b0;border-radius:14px;padding:16px;font-size:15px;line-height:1.9;color:#7a4a3a;}}
  footer{{text-align:center;font-size:11px;color:#b39a7d;padding:10px 0 0;line-height:1.8;}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="t">🎨 圆桌画室 · 明信片</div>
    <div class="s">{today}｜{_esc(framing)}</div>
  </header>

  <div class="card artwrap">{art}
    <div style="padding:14px 16px 6px"><h2>🖌 这幅画的四笔</h2></div>
    <div style="padding:0 16px 14px">{rows}</div>
  </div>

  <div class="card say">💌 {_esc(note[:180])}</div>

  <footer>清心圆桌 · 圆桌画室<br>画面由本场四笔共同完成，仅供留念</footer>
</div>
</body>
</html>"""
    return html_doc.encode("utf-8")
