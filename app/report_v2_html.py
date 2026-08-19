"""《圆桌留笺》HTML：暖房圆桌视觉，单文件、移动端优先。"""
from __future__ import annotations

import datetime as dt
import html


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _avatar(name: str, index: int) -> str:
    palettes = ("apricot", "sage", "lilac")
    initial = esc(name[-1:] or "友")
    return (
        f'<div class="seat seat-{index}"><div class="avatar {palettes[index % 3]}">'
        f'{initial}</div><span>{esc(name)}</span><small>AI团友</small></div>'
    )


def _temperature(before: object, after: object) -> str:
    if not isinstance(before, (int, float)) and not isinstance(after, (int, float)):
        return ""
    b = max(0, min(10, int(before))) if isinstance(before, (int, float)) else None
    a = max(0, min(10, int(after))) if isinstance(after, (int, float)) else None
    b_text, a_text = (f"{b}" if b is not None else "—"), (f"{a}" if a is not None else "—")
    b_width, a_width = (b or 0) * 10, (a or 0) * 10
    return f"""
    <section class="card temperature">
      <div class="eyebrow">SELF CHECK-IN</div><h2>压力温度</h2>
      <div class="meter-row"><b>入桌</b><div class="meter"><i style="width:{b_width}%"></i></div><strong>{b_text}</strong></div>
      <div class="meter-row"><b>离桌</b><div class="meter after"><i style="width:{a_width}%"></i></div><strong>{a_text}</strong></div>
      <p class="hint">这是一次自愿记录，不是效果评分。数字没有变化，也不代表这场圆桌没有意义。</p>
    </section>"""


def render(fields: dict, member_names: list[str]) -> bytes:
    names = (member_names + ["团友", "团友", "团友"])[:3]
    seats = "".join(_avatar(name, idx) for idx, name in enumerate(names))
    differences = [str(x) for x in (fields.get("differences") or []) if str(x).strip()][:2]
    while len(differences) < 2:
        differences.append("这次没有形成另一种需要记录的立场。")
    temperature = _temperature(fields.get("pressure_before"), fields.get("pressure_after"))
    today = dt.date.today().strftime("%Y · %m · %d")
    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>清心圆桌 · 圆桌留笺</title>
<style>
  :root{{--paper:#fffaf2;--cream:#f8ead4;--ink:#4b3c34;--muted:#92796a;--coral:#df6b4f;
    --gold:#e7a84c;--sage:#9cb79d;--lilac:#a99acb;--line:#ead8c2;}}
  *{{box-sizing:border-box}}html{{background:#efe8df}}body{{margin:0;color:var(--ink);font:15px/1.72 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    background:radial-gradient(circle at 14% 4%,#fff5d7 0 11%,transparent 30%),radial-gradient(circle at 88% 9%,#f9ded1 0 8%,transparent 27%),#f4eee6;padding:18px 12px 38px}}
  main{{max-width:720px;margin:auto;background:var(--paper);border:1px solid #fff;border-radius:26px;overflow:hidden;box-shadow:0 18px 55px rgba(91,63,42,.14)}}
  .hero{{position:relative;padding:34px 22px 18px;text-align:center;background:linear-gradient(155deg,#fffaf0,#ffefd8 58%,#f9dfd3);overflow:hidden}}
  .hero:before,.hero:after{{content:"";position:absolute;border-radius:50%;border:1px solid rgba(221,155,83,.23)}}
  .hero:before{{width:220px;height:220px;left:-110px;top:-120px}}.hero:after{{width:160px;height:160px;right:-70px;top:-70px}}
  .brand{{font-size:12px;letter-spacing:3px;color:#b77745;font-weight:700}}h1{{font-family:Georgia,"Songti SC",serif;font-size:28px;line-height:1.25;margin:7px 0 5px;color:#b9503c}}
  .date{{color:var(--muted);font-size:12px}}.leaf{{display:inline-block;color:#8ea88d;margin:0 5px}}
  .roundtable{{position:relative;width:330px;height:238px;max-width:100%;margin:22px auto 0}}
  .table{{position:absolute;width:190px;height:112px;border-radius:50%;left:50%;top:65px;transform:translateX(-50%);background:linear-gradient(145deg,#e6ab67,#c98248);box-shadow:inset 0 7px 15px #f5ce91,0 13px 24px rgba(132,78,43,.2)}}
  .table:after{{content:"今天，我们在这里相遇";position:absolute;inset:0;display:grid;place-items:center;color:#fff8e8;font:13px/1.4 Georgia,"Songti SC",serif;padding:0 36px}}
  .seat{{position:absolute;width:88px;text-align:center;font-size:13px;font-weight:650;color:#604b40}}.seat small{{display:block;color:#a38c7e;font-size:10px;font-weight:400}}
  .seat-0{{left:2px;top:70px}}.seat-1{{left:50%;top:0;transform:translateX(-50%)}}.seat-2{{right:2px;top:70px}}
  .user-seat{{position:absolute;bottom:0;left:50%;transform:translateX(-50%);font-size:13px;font-weight:700;color:#b9503c}}
  .avatar{{width:46px;height:46px;margin:auto;border:3px solid rgba(255,255,255,.9);border-radius:50%;display:grid;place-items:center;color:white;font:700 18px Georgia,serif;box-shadow:0 5px 12px rgba(80,50,30,.15)}}
  .avatar.apricot{{background:#e58a65}}.avatar.sage{{background:#8eac94}}.avatar.lilac{{background:#9f90c4}}.avatar.you{{background:#d85d45;width:50px;height:50px}}
  .content{{padding:18px 20px 30px}}.card{{background:#fff;border:1px solid #f0e1d0;border-radius:18px;padding:19px 18px;margin:14px 0;box-shadow:0 5px 16px rgba(111,77,50,.055)}}
  .eyebrow{{font-size:10px;letter-spacing:2px;color:#bf8b5a;font-weight:700}}h2{{font:700 17px/1.35 Georgia,"Songti SC",serif;color:#b95740;margin:4px 0 10px}}p{{margin:3px 0}}.hint{{font-size:12px;color:var(--muted);margin-top:10px}}
  .moment{{background:linear-gradient(145deg,#fff,#fff5e5);position:relative;padding-left:27px}}.moment:before{{content:"“";position:absolute;left:7px;top:2px;font:48px Georgia;color:#edbf7f}}
  .impact-grid{{display:grid;grid-template-columns:1fr 36px 1fr;align-items:stretch;gap:5px;margin:14px 0}}
  .impact{{border-radius:18px;padding:17px 15px;min-width:0}}.impact.you-gave{{background:#fff0e7;border:1px solid #f3d4c2}}.impact.you-received{{background:#edf5ed;border:1px solid #d5e5d5}}
  .impact h3{{font-size:13px;margin:0 0 8px;color:#8b5543}}.impact p{{font-size:14px}}.arrows{{display:grid;place-items:center;color:#cc8b61;font-size:24px}}
  .differences{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.voice{{padding:14px;border-radius:14px;background:#faf5ee;border:1px dashed #d8bea3}}
  .voice b{{display:block;color:#a86a42;margin-bottom:5px}}.need{{background:linear-gradient(135deg,#f1edf9,#fff);border-color:#ded5ed}}
  .need .quote{{font:17px/1.75 Georgia,"Songti SC",serif;color:#675584}}
  .takeaway{{position:relative;background:#fff6d9;border:1px solid #ead18b;box-shadow:0 8px 18px rgba(141,102,41,.08);transform:rotate(-.35deg);padding:23px 20px 19px}}
  .takeaway:before{{content:"";position:absolute;width:86px;height:24px;background:rgba(232,186,107,.38);left:50%;top:-11px;transform:translateX(-50%) rotate(1deg)}}
  .takeaway .phrase{{font:700 18px/1.75 Georgia,"Songti SC",serif;color:#704f31}}
  .meter-row{{display:grid;grid-template-columns:34px 1fr 28px;gap:9px;align-items:center;margin:10px 0;font-size:12px}}.meter{{height:9px;background:#f0e4d6;border-radius:9px;overflow:hidden}}.meter i{{display:block;height:100%;border-radius:9px;background:linear-gradient(90deg,#f1bd65,#df6b4f)}}.meter.after i{{background:linear-gradient(90deg,#b7cfad,#74a98a)}}
  .closing{{text-align:center;padding:22px 22px 26px;color:#6b5548}}.closing .sun{{width:34px;height:34px;border-radius:50%;margin:0 auto 12px;background:#edb24f;box-shadow:0 0 0 8px #f9e7b9,0 0 0 15px #fff5da}}
  footer{{padding:17px 20px;background:#f3e8da;text-align:center;color:#937b6b;font-size:11px;line-height:1.7}}
  @media(max-width:520px){{main{{border-radius:19px}}.hero{{padding-left:14px;padding-right:14px}}.roundtable{{width:300px}}.content{{padding:14px 12px 24px}}.impact-grid{{grid-template-columns:1fr}}.arrows{{height:26px;transform:rotate(90deg)}}.differences{{grid-template-columns:1fr}}}}
  @media print{{body{{background:#fff;padding:0}}main{{box-shadow:none;border:0}}}}
</style></head><body><main>
  <header class="hero"><div class="brand">QINGXIN ROUNDTABLE</div><h1><span class="leaf">❧</span>圆桌留笺<span class="leaf">❧</span></h1><div class="date">{today}</div>
    <div class="roundtable"><div class="table"></div>{seats}<div class="user-seat"><div class="avatar you">你</div>真人成员</div></div>
  </header>
  <div class="content">
    <section class="card moment"><div class="eyebrow">A MOMENT OF CONNECTION</div><h2>我们真正靠近的时刻</h2><p>{esc(fields.get('approach_moment') or '这次还没有形成被确认的靠近时刻。')}</p></section>
    <div class="impact-grid"><article class="impact you-gave"><h3>你的话影响了谁</h3><p>{esc(fields.get('user_impact') or '这次还没有形成可确认的影响。')}</p></article><div class="arrows">⇄</div><article class="impact you-received"><h3>谁影响了你</h3><p>{esc(fields.get('member_impact') or '这次还没有得到你的确认。')}</p></article></div>
    <section class="card"><div class="eyebrow">DIFFERENT, TOGETHER</div><h2>圆桌上的不同</h2><div class="differences"><div class="voice"><b>一种声音</b>{esc(differences[0])}</div><div class="voice"><b>另一种声音</b>{esc(differences[1])}</div></div><p class="hint">团体不需要把不同变成同一个答案。</p></section>
    <section class="card need"><div class="eyebrow">HOW I WISH TO BE MET</div><h2>你希望被怎样回应</h2><p class="quote">“{esc(fields.get('response_need') or '这次还没有明确说出来。')}”</p></section>
    <section class="card takeaway"><div class="eyebrow">TAKE IT WITH YOU</div><h2>带回现实的一句话</h2><p class="phrase">{esc(fields.get('real_world_phrase') or '这次先不带行动离开。')}</p></section>
    {temperature}
    <section class="closing"><div class="sun"></div><h2>小晴收桌</h2><p>{esc(fields.get('leader_note') or '谢谢你认真参与这场圆桌。')}</p></section>
  </div>
  <footer>三位团友均为虚构合成AI角色，不对应真实学生。<br>本留笺只记录本场团体过程，不是心理评估或医疗建议。</footer>
</main></body></html>"""
    return doc.encode("utf-8")
