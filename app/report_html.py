"""本场成长报告的 HTML 渲染：模板固定，LLM 只填字段（结构永不漂移）。

报告为单文件（内联 CSS、无外部依赖、无 JS 交互依赖），手机浏览器可直接打开。
"""
from __future__ import annotations

import datetime as _dt
import html as _html


def _esc(v: object) -> str:
    return _html.escape(str(v if v is not None else ""))


def _link(url: str, label: str) -> str:
    return f'<a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(label)}</a>'


_THREE_BRAINS_SVG = """
<svg viewBox="0 0 320 150" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="三脑示意">
  <g font-family="sans-serif" text-anchor="middle">
    <rect x="10" y="10" width="300" height="40" rx="12" fill="#ffe3c2"/>
    <text x="160" y="28" font-size="13" fill="#8a5a2b">认知脑（新皮质）· 思考、解题、计划</text>
    <text x="160" y="43" font-size="11" fill="#a97c4f">情绪安顿下来，它才能上线</text>
    <rect x="10" y="55" width="300" height="40" rx="12" fill="#ffd2d9"/>
    <text x="160" y="73" font-size="13" fill="#a33a52">情绪脑（边缘系统）· 恐慌、逃避、烦躁</text>
    <text x="160" y="88" font-size="11" fill="#b86275">压力一大，它会抢走方向盘</text>
    <rect x="10" y="100" width="300" height="40" rx="12" fill="#d9e8ff"/>
    <text x="160" y="118" font-size="13" fill="#2f5e9e">爬行脑（脑干）· 心跳加快、呼吸急促</text>
    <text x="160" y="133" font-size="11" fill="#5b82b3">身体最先拉响警报</text>
  </g>
</svg>
"""


def render(
    fields: dict,
    theme_label: str,
    member_names: list[str],
    resources: dict,
    public_base: str,
) -> bytes:
    today = _dt.date.today().strftime("%Y年%m月%d日")
    review_items = "".join(
        f"<li><span class='dot'>●</span>{_esc(item)}</li>"
        for item in (fields.get("review") or [])[:8]
    ) or "<li><span class='dot'>●</span>完成了全部八个环节</li>"
    tips = fields.get("member_tips") or []
    tip_rows = "".join(
        f"<div class='tip'><span class='who'>{_esc(t.get('name', ''))}</span>"
        f"<span class='what'>{_esc(t.get('tip', ''))}</span></div>"
        for t in tips if isinstance(t, dict)
    ) or "".join(
        f"<div class='tip'><span class='who'>{_esc(n)}</span><span class='what'>谢谢你来这一场</span></div>"
        for n in member_names
    )
    takeaways = "".join(
        f"<div class='tk'><span class='n'>{i + 1}</span>{_esc(t)}</div>"
        for i, t in enumerate((fields.get("takeaways") or [])[:3])
    ) or "<div class='tk'><span class='n'>1</span>慢一点，也没关系。</div>"

    pb = fields.get("pressure_before")
    pressure_block = ""
    if isinstance(pb, (int, float)) and not isinstance(pb, bool):
        pct = max(0, min(100, int(pb) * 10))
        pressure_block = f"""
    <section>
      <h2>🌡 你的压力温度</h2>
      <p class="muted">开场时你给自己打了 <b>{int(pb)} 分</b>（0-10）。</p>
      <div class="meter"><span style="width:{pct}%"></span></div>
      <p class="muted">现在再给自己打个分——如果降了，恭喜你；如果没变，也没关系，觉察本身就是开始。</p>
    </section>"""

    links = []
    if resources.get("breathing_video"):
        links.append(f"<div class='lk'>{_link(resources['breathing_video'], '▶ 腹式呼吸教学视频')}</div>")
    if resources.get("mindfulness_audio"):
        links.append(f"<div class='lk'>{_link(resources['mindfulness_audio'], '🎧 一分钟正念音频')}</div>")
    if resources.get("three_brains_path"):
        three_label = "🧠 三脑图解（为什么压力会让人\u201c脑子空白\u201d）"
        links.append(f"<div class='lk'>{_link(public_base + resources['three_brains_path'], three_label)}</div>")
    books = "".join(f"<div class='bk'>📖 {_esc(b)}</div>" for b in (resources.get("books") or []))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>清心圆桌 · 成长报告</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}}
  body{{background:linear-gradient(180deg,#fff8ec,#ffedd6);color:#5a4636;padding:24px 16px 40px;}}
  .wrap{{max-width:640px;margin:0 auto;}}
  header{{text-align:center;padding:18px 12px 22px;}}
  header .t{{font-size:22px;font-weight:700;color:#e0603c;}}
  header .s{{font-size:13px;color:#a6825e;margin-top:6px;}}
  section{{background:#fff;border-radius:16px;padding:18px 18px 16px;margin-bottom:14px;
           box-shadow:0 2px 10px rgba(160,110,60,.08);}}
  h2{{font-size:15px;color:#d95f36;margin-bottom:10px;}}
  p{{font-size:14px;line-height:1.8;}}
  .muted{{color:#8a6f52;}}
  ul{{list-style:none;}}
  li{{font-size:14px;line-height:1.9;}}
  .dot{{color:#f0a04b;margin-right:8px;font-size:10px;vertical-align:2px;}}
  .tip{{display:flex;gap:10px;padding:8px 0;border-bottom:1px dashed #f3e3cf;font-size:14px;}}
  .tip:last-child{{border-bottom:none;}}
  .who{{flex:none;font-weight:600;color:#9b7ede;min-width:4.5em;}}
  .tk{{display:flex;gap:10px;align-items:flex-start;font-size:14px;line-height:1.7;padding:6px 0;}}
  .tk .n{{flex:none;width:22px;height:22px;border-radius:50%;background:#f0a04b;color:#fff;
         text-align:center;line-height:22px;font-size:12px;}}
  .meter{{height:10px;border-radius:5px;background:#f3e3cf;margin:10px 0;overflow:hidden;}}
  .meter span{{display:block;height:100%;border-radius:5px;background:linear-gradient(90deg,#ffc66b,#f0803c);}}
  .lk{{font-size:14px;line-height:2.1;}}
  .lk a{{color:#3d6fdb;text-decoration:none;border-bottom:1px dashed #9db8ea;}}
  .bk{{font-size:14px;line-height:2;color:#6d5741;}}
  .say{{background:linear-gradient(135deg,#fff3d9,#ffe4ea);border:1px solid #ffd9b0;}}
  .say p{{font-size:15px;color:#7a4a3a;}}
  svg{{width:100%;height:auto;display:block;margin:4px 0;}}
  footer{{text-align:center;font-size:11px;color:#b39a7d;padding:12px 0;line-height:1.8;}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="t">🌞 {_esc(theme_label)} · 成长报告</div>
    <div class="s">{today}｜清心圆桌（朋辈支持空间 · 非心理治疗）</div>
  </header>

  <section>
    <h2>🪑 这一场的圆桌</h2>
    <p>围桌而坐的有：{_esc("、".join(member_names) if member_names else "几位AI桌友")}，以及认真参与到底的你。</p>
    <p class="muted">{_esc(fields.get("pressure_note") or "")}</p>
  </section>

  <section>
    <h2>🗺 活动回顾</h2>
    <ul>{review_items}</ul>
  </section>

  {pressure_block}

  <section>
    <h2>🧘 今天练习过的</h2>
    <p>我们一起做了一分钟<b>腹式呼吸</b>：鼻子慢慢吸气（3秒）→ 停一下 → 嘴巴慢慢呼气（3秒）。想再练一次：</p>
    {''.join(links)}
    <p class="muted" style="margin-top:8px">为什么压力会让人"脑子空白"？——看这张图：</p>
    {_THREE_BRAINS_SVG}
  </section>

  <section>
    <h2>🎁 桌友们的减压一招</h2>
    {tip_rows}
  </section>

  <section>
    <h2>📋 你的减压清单</h2>
    {takeaways}
    <p class="muted" style="margin-top:6px">三条都来自你自己说过的话和这一场圆桌——不是标准答案，是你自己的答案。</p>
  </section>

  <section class="say">
    <h2>💌 想对你说</h2>
    <p>{_esc(fields.get("encouragement") or "把压力说出来的你，已经做了一件很了不起的事。")}</p>
  </section>

  <section>
    <h2>📚 还想了解更多</h2>
    {books}
    <p class="muted" style="margin-top:8px">如果压力持续影响到睡眠、饮食或日常生活，欢迎联系学校心理发展指导中心（预约方式见学校 info 门户），或拨打全国心理援助热线 400-161-9995。寻求专业帮助，是照顾自己的方式。</p>
  </section>

  <footer>清心圆桌 · 小清心的AI团体助手化身<br>本报告由本场对话生成，仅供自我觉察参考</footer>
</div>
</body>
</html>"""
    return html.encode("utf-8")
