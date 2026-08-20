"""《圆桌留笺》HTML：内嵌圆桌插画、成员、议题、建议、寄语与校内资源。"""
from __future__ import annotations

import base64
import datetime as dt
import html
from pathlib import Path


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _hero_image() -> str:
    path = Path(__file__).resolve().parent.parent / "assets" / "img" / "qingxin-roundtable-bg.jpg"
    try:
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""


def _items(values: object, fallback: str, limit: int = 10) -> str:
    rows = [str(x).strip() for x in (values or []) if str(x).strip()][:limit]
    rows = rows or [fallback]
    return "".join(f"<li>{esc(row)}</li>" for row in rows)


def _member(name: str, role: str, index: int) -> str:
    colors = ("coral", "sage", "lilac", "gold", "blue")
    return (
        f'<article class="member"><div class="avatar {colors[index % len(colors)]}">{esc(name[-1:] or "友")}</div>'
        f'<div><b>{esc(name)}</b><small>{esc(role)}</small></div></article>'
    )


def _temperature(before: object, after: object) -> str:
    if not isinstance(before, (int, float)) and not isinstance(after, (int, float)):
        return ""
    b = max(0, min(10, int(before))) if isinstance(before, (int, float)) else None
    a = max(0, min(10, int(after))) if isinstance(after, (int, float)) else None
    return f'''<section class="panel temperature"><div class="kicker">SELF CHECK-IN</div><h2>压力温度</h2>
      <div class="meter-row"><span>入桌</span><i><em style="width:{(b or 0)*10}%"></em></i><b>{b if b is not None else "—"}</b></div>
      <div class="meter-row"><span>离桌</span><i><em class="after" style="width:{(a or 0)*10}%"></em></i><b>{a if a is not None else "—"}</b></div>
      <p class="note">这是自愿记录，不是效果评分；数字没有下降也不代表参与失败。</p></section>'''


def render(fields: dict, member_names: list[str]) -> bytes:
    ai_names = (member_names + ["团友", "团友", "团友"])[:3]
    user_name = str(fields.get("participant_name") or "你").strip()[:12]
    members = [_member("小晴", "AI带领者", 0)]
    members.extend(_member(name, "虚构AI团友", i + 1) for i, name in enumerate(ai_names))
    members.append(_member(user_name, "真人成员", 4))
    topics = _items(fields.get("discussion_topics"), "这次没有形成可确认的主要议题。", 6)
    suggestions = _items(
        fields.get("stress_suggestions") or fields.get("stress_checklist"),
        "这次没有勉强整理出建议；没有清单也可以离开。", 12,
    )
    hero = _hero_image()
    hero_style = f' style="background-image:linear-gradient(180deg,rgba(45,37,31,.03),rgba(45,37,31,.72)),url({hero})"' if hero else ""
    today = dt.date.today().strftime("%Y · %m · %d")
    temperature = _temperature(fields.get("pressure_before"), fields.get("pressure_after"))
    doc = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>清心圆桌 · 圆桌留笺</title><style>
:root{{--paper:#fffaf2;--ink:#493b34;--muted:#8a7468;--coral:#db7359;--sage:#829e83;--gold:#dda34f;--line:#ead9c5}}
*{{box-sizing:border-box}}body{{margin:0;padding:20px 12px 42px;color:var(--ink);font:15px/1.72 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:radial-gradient(circle at 10% 3%,#fff2cf,transparent 28%),radial-gradient(circle at 92% 9%,#f4d9d0,transparent 25%),#efe9e1}}
main{{max-width:760px;margin:auto;background:var(--paper);border-radius:28px;overflow:hidden;box-shadow:0 20px 60px #6e513629}}
.hero{{height:390px;padding:28px;display:flex;flex-direction:column;justify-content:flex-end;color:#fff;background-size:cover;background-position:center;position:relative}}
.hero:after{{content:"";position:absolute;inset:16px;border:1px solid #ffffff66;border-radius:20px}}.hero>*{{z-index:1}}.brand{{font-size:11px;letter-spacing:3px}}h1{{font:700 34px/1.2 Georgia,"Songti SC",serif;margin:5px 0}}.date{{opacity:.85}}
.content{{padding:22px}}.panel{{background:#fff;border:1px solid var(--line);border-radius:20px;padding:20px;margin:15px 0;box-shadow:0 6px 20px #6f4f2c0c}}.kicker{{font-size:10px;letter-spacing:2px;color:#b6794c;font-weight:700}}h2{{margin:4px 0 12px;color:#ad543f;font:700 19px/1.35 Georgia,"Songti SC",serif}}
.members{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}.member{{text-align:center;padding:12px 5px;background:#faf5ed;border-radius:15px}}.avatar{{width:48px;height:48px;border-radius:50%;display:grid;place-items:center;margin:0 auto 7px;color:white;font:700 19px Georgia;border:3px solid #fff;box-shadow:0 4px 12px #5e46352b}}.member b,.member small{{display:block}}.member small{{font-size:10px;color:var(--muted)}}.coral{{background:#dc795f}}.sage{{background:#829e83}}.lilac{{background:#9b8fc0}}.gold{{background:#d9a14e}}.blue{{background:#658ca6}}
ul{{list-style:none;padding:0;margin:8px 0 0}}li{{position:relative;margin:9px 0;padding:11px 13px 11px 37px;border-radius:13px;background:#f8f3eb}}li:before{{content:"✦";position:absolute;left:14px;color:#d4894e}}.suggestions li{{background:#edf5ed}}.suggestions li:before{{content:"□";color:#6f9874;font-weight:700}}
.message{{background:linear-gradient(135deg,#fff2d4,#fff9eb);border-color:#e8ce92;position:relative;padding:25px 23px}}.message:before{{content:"“";position:absolute;right:18px;top:-4px;font:58px Georgia;color:#e5b76966}}.message p{{font:17px/1.8 Georgia,"Songti SC",serif;margin:0;color:#684d36}}
.resources{{background:linear-gradient(145deg,#f3f0fa,#fff)}}.resource{{padding:13px 0;border-bottom:1px dashed #d9d0e5}}.resource:last-child{{border:0}}.resource b{{display:block;color:#665586}}.resource a{{color:#9e553e;text-decoration:none;overflow-wrap:anywhere}}.books{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.book{{padding:14px;border-radius:14px;background:#fff;border:1px solid #e9dfd1}}.book b,.book small{{display:block}}.book small{{color:var(--muted);margin:2px 0 6px}}
.meter-row{{display:grid;grid-template-columns:38px 1fr 28px;align-items:center;gap:10px;margin:10px 0}}.meter-row i{{height:9px;background:#eee2d5;border-radius:9px;overflow:hidden}}.meter-row em{{display:block;height:100%;background:linear-gradient(90deg,#efb55d,#d96e57);border-radius:9px}}.meter-row em.after{{background:linear-gradient(90deg,#aec8a5,#6f9d7e)}}.note{{font-size:12px;color:var(--muted)}}footer{{padding:18px;text-align:center;background:#efe3d5;color:#8c7466;font-size:11px}}
@media(max-width:620px){{.hero{{height:300px;padding:22px}}h1{{font-size:29px}}.content{{padding:14px 12px 24px}}.members{{grid-template-columns:repeat(3,1fr)}}.books{{grid-template-columns:1fr}}}}
@media print{{body{{padding:0;background:#fff}}main{{box-shadow:none}}}}
</style></head><body><main><header class="hero"{hero_style}><div class="brand">QINGXIN ROUNDTABLE</div><h1>圆桌留笺</h1><div>{today} · 这一场，我们认真听过彼此</div></header><div class="content">
<section class="panel"><div class="kicker">WHO SAT AT THE TABLE</div><h2>今天围桌的人</h2><div class="members">{''.join(members)}</div></section>
<section class="panel"><div class="kicker">WHAT WE TALKED ABOUT</div><h2>这次主要讨论了什么</h2><ul>{topics}</ul></section>
<section class="panel suggestions"><div class="kicker">IDEAS FROM THE GROUP</div><h2>团体共同提炼的减压建议</h2><ul>{suggestions}</ul><p class="note">这些是圆桌给出的备选，不是任务；留下适合你的，其他的可以划掉。</p></section>
{temperature}
<section class="panel message"><div class="kicker">A NOTE FROM XIAOQING</div><h2>小晴的暖心寄语</h2><p>{esc(fields.get('leader_note') or '谢谢你把真实的一部分带到桌上。你不必一次解决所有压力，也不必独自把每件事想明白。')}</p></section>
<section class="panel resources"><div class="kicker">SUPPORT & READING</div><h2>需要时，可以去这些地方</h2>
  <div class="resource"><b>清华小清心微信公众号</b>微信搜索“清华小清心”，查看校内心理支持资源并使用预约入口。</div>
  <div class="resource"><b>清华大学在校学生 7×24 小时心理热线</b><a href="tel:01062785252">010-62785252</a></div>
  <div class="resource"><b>学生心理发展指导中心前台（工作时间）</b><a href="tel:01062782007">010-62782007</a></div>
  <p class="note">紧急危险时请优先联系校内保卫部门、120或110。联系方式依据清华大学公开信息整理。</p>
  <h2 style="margin-top:20px">慢慢读，也是一种照顾</h2><div class="books">
    <article class="book"><b>《为什么斑马不得胃溃疡》</b><small>罗伯特·萨波尔斯基</small>用生动的生理学解释压力怎样进入身体，以及我们能如何理解它。</article>
    <article class="book"><b>《正念：此刻是一枝花》</b><small>乔·卡巴金</small>用短小练习帮助人回到当下，不要求立刻消除所有念头。</article>
    <article class="book"><b>《自我关怀的力量》</b><small>克里斯廷·内夫</small>练习在困难时少一点自我攻击，多一点对自己的支持。</article>
    <article class="book"><b>《伯恩斯新情绪疗法》</b><small>戴维·伯恩斯</small>通过具体练习识别压力中的自动想法，寻找更灵活的解释。</article>
  </div>
</section></div><footer>三位团友和小晴均为虚构AI角色，不对应真实学生。<br>本留笺不是心理评估或医疗建议；资源信息可能更新，请以学校最新公布为准。</footer></main></body></html>'''
    return doc.encode("utf-8")
