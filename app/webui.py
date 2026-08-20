"""公开体验页：免key直接聊（走 /public/chat/completions，服务端限流）。"""
from __future__ import annotations

from fastapi.responses import HTMLResponse

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>清心圆桌 · 小清心</title>
<link rel="icon" href="/assets/img/xqx-logo.jpg">
<style>
  :root{
    --bg:#FFF6EC; --bg2:#FFEBD6;
    --panel:#FFFFFF; --line:#F3DEC6;
    --ink:#5A4636; --dim:#A6896E;
    --accent:#FF7A59; --accent-deep:#F0603C;
    --gold:#F2A93B; --purple:#9B7EDE; --pink:#F49CBB; --green:#69B899;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:linear-gradient(rgba(255,250,242,.58),rgba(255,250,242,.72)),
       url('/assets/img/qingxin-roundtable-bg.jpg') center top / cover fixed no-repeat;
       color:var(--ink);font:15px/1.75 "PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;}
  body::before{content:"";position:fixed;inset:0;z-index:-1;background:rgba(255,255,255,.12);pointer-events:none;}
  header{position:sticky;top:0;z-index:5;background:rgba(255,251,246,.92);backdrop-filter:blur(8px);
         border-bottom:1px solid var(--line);padding:10px 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
  header .logo{width:44px;height:44px;border-radius:14px;object-fit:cover;box-shadow:0 2px 8px rgba(240,96,60,.18);}
  header h1{font-size:18px;margin:0;color:var(--ink);letter-spacing:1px;}
  header h1 b{color:var(--accent-deep);}
  header .tag{font-size:12px;color:#fff;background:linear-gradient(120deg,var(--accent),var(--gold));
              padding:2px 10px;border-radius:12px;font-weight:600;}
  .tools{display:flex;gap:8px;margin-left:auto;}
  .tools button{background:#fff;border:1px solid var(--line);color:var(--dim);border-radius:10px;
                padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer;box-shadow:none;}
  .tools button:hover{border-color:var(--accent);color:var(--accent-deep);transform:none;}
  main{max-width:780px;margin:0 auto;padding:22px 16px 140px;}
  .welcome{background:rgba(255,255,255,.78);border:1px solid rgba(243,222,198,.9);border-radius:22px;padding:22px;margin:14px 0;
           box-shadow:0 6px 24px rgba(240,96,60,.08);text-align:center;backdrop-filter:blur(3px);}
  .welcome img{width:min(420px,88%);border-radius:14px;margin-bottom:10px;}
  .welcome .t{font-weight:700;font-size:16px;}
  .welcome .d{color:var(--dim);font-size:13px;margin-top:6px;line-height:1.7;}
  .how{background:rgba(255,240,228,.8);border:1px dashed #FFD9C2;border-radius:14px;padding:10px 14px;
       margin-top:12px;font-size:13px;color:var(--ink);text-align:left;line-height:1.8;}
  .grp{margin:16px 0 8px;font-size:13px;font-weight:700;color:var(--accent-deep);text-align:left;}
  .quick{display:flex;flex-direction:column;gap:8px;margin-top:8px;}
  .quick.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
  .quick button{background:#fff;border:1.5px solid #FFD9C2;border-radius:16px;padding:9px 16px;font-size:14px;
                font-weight:600;color:var(--ink);cursor:pointer;text-align:left;box-shadow:0 2px 8px rgba(240,96,60,.10);
                transition:transform .08s ease,border-color .08s ease;}
  .quick button:hover{border-color:var(--accent);transform:translateY(-1px);}
  .quick button small{display:block;font-weight:400;color:var(--dim);font-size:12px;margin-top:1px;}
  .msg{margin:14px 0;display:flex;gap:10px;align-items:flex-start;}
  .msg .ava{flex:0 0 38px;width:38px;height:38px;border-radius:50%;background-image:url('/assets/img/xqx-mascots.jpg');
            background-size:400% 100%;border:2px solid #fff;box-shadow:0 2px 6px rgba(90,70,54,.15);margin-top:4px;}
  .msg .col{flex:1;min-width:0;}
  .msg .who{font-weight:700;font-size:13px;margin-bottom:3px;padding:1px 10px;border-radius:10px;display:inline-block;}
  .who.leader{background:#FFE3D6;color:var(--accent-deep);}
  .who.member{background:#EFE7FF;color:#7757c4;}
  .who.user{background:#DDF2E7;color:#3d8a68;}
  .msg .text{background:rgba(255,255,255,.82);border:1px solid rgba(243,222,198,.9);border-radius:16px;padding:10px 14px;
             white-space:pre-wrap;box-shadow:0 2px 10px rgba(90,70,54,.05);backdrop-filter:blur(3px);}
  .msg.user{flex-direction:row-reverse;}
  .msg.user .text{background:rgba(255,240,228,.88);border-color:#FFD9C2;}
  .marker{color:var(--dim);font-size:12px;text-align:center;margin:20px 0;}
  .attach{margin:10px 0 10px 48px;}
  .attach img{max-width:70%;border-radius:14px;border:1px solid var(--line);display:block;margin:8px 0;
              box-shadow:0 4px 16px rgba(90,70,54,.12);}
  .attach a{color:var(--accent-deep);font-size:13px;text-decoration:none;font-weight:600;}
  form{position:fixed;bottom:0;left:0;right:0;background:rgba(255,251,246,.95);backdrop-filter:blur(8px);
       border-top:1px solid var(--line);padding:12px 16px;}
  form inner{max-width:780px;margin:0 auto;display:flex;gap:10px;}
  textarea{flex:1;background:#fff;color:var(--ink);border:1.5px solid var(--line);border-radius:14px;
           padding:11px 14px;font:inherit;resize:none;height:54px;}
  textarea:focus{outline:none;border-color:var(--accent);}
  button{background:linear-gradient(120deg,var(--accent),var(--gold));color:#fff;border:0;border-radius:14px;
         padding:0 24px;font-size:15px;font-weight:700;cursor:pointer;box-shadow:0 4px 12px rgba(240,96,60,.25);}
  button:disabled{opacity:.5;cursor:default;box-shadow:none;}
  .hint{max-width:780px;margin:0 auto 6px;font-size:12px;color:var(--dim);}
</style>
</head>
<body>
<header>
  <img class="logo" src="/assets/img/xqx-logo.jpg" alt="小清心">
  <h1>清心圆桌</h1><span class="tag">AI团体支持空间</span>
  <div class="tools">
    <button onclick="copyChat()" id="copyBtn" title="复制完整对话记录">📋 复制对话</button>
    <button onclick="resetChat()" title="清空记录，重新开始">🔄 重开</button>
  </div>
</header>
<main id="log">
  <div class="welcome">
    <img src="/assets/img/xqx-mascots.jpg" alt="清清华华心心理理">
    <div class="t">和小伙伴们一起，圆桌开聊 ☀️</div>
    <div class="d">这不是一对一问答，而是一场 <b>AI 团体活动</b>：小晴会照你的话题，召集一桌刚刚好的 AI 同伴——三类形态、六个节目，总有一场适合此刻的你。</div>
    <div class="how"><b>怎么玩</b>：回复数字或直接说最近的困惑 → 小晴帮你开桌 → 深度团体四个活动走完（约20分钟）→ 结束带走一份《圆桌留笺》；画室结束送一张明信片 ✨</div>
    <div class="grp">① 深度团体（约20分钟，聊透一件事）</div>
    <div class="quick grid">
      <button onclick="quickSend('我想参加减压安心之旅')">🫧 减压安心之旅<small>学业、科研、生活，来松一松</small></button>
      <button onclick="quickSend('我是大一新生，想家，想参加新生适应')">🌱 新生适应<small>想家、宿舍、新环境</small></button>
      <button onclick="quickSend('暗恋一个人两年了不敢表白，想参加爱情探索')">💞 爱情探索<small>心动、异地、表白与错过</small></button>
      <button onclick="quickSend('秋招投了很多简历都没回音，想参加就业迷茫')">🛤 就业迷茫<small>秋招、考研、Gap、路口</small></button>
    </div>
    <div class="grp">② 轻团体（约10分钟，安静共创）</div>
    <div class="quick">
      <button onclick="quickSend('来圆桌画室，画一幅关于最近的我的画')">🎨 圆桌画室<small>不用会画画——大家用文字各添一笔，合成一幅真正的画，送你一张明信片</small></button>
    </div>
    <div class="grp">③ 对话面板</div>
    <div class="quick">
      <button onclick="quickSend('我想参加时空对话')">⏳ 时空对话<small>司马迁、项羽、张良……点四位史记人物，一问四答，看他们隔世辩论</small></button>
    </div>
    <div class="d" style="margin-top:12px">也可以直接输入最近想说的话，小晴帮你挑一场</div>
  </div>
</main>
<form onsubmit="return send(event)">
  <div class="hint">不知道说什么时，可以直接说“我先听一轮” · Enter 发送 · Shift+Enter 换行</div>
  <inner><textarea id="input" placeholder="说点什么……" autofocus></textarea><button id="btn">发送</button></inner>
</form>
<script>
const log = document.getElementById('log');
const input = document.getElementById('input');
const btn = document.getElementById('btn');
let messages = [];
try { messages = JSON.parse(localStorage.getItem('weilu_msgs') || '[]'); } catch(e){ messages = []; }

function persist(){ localStorage.setItem('weilu_msgs', JSON.stringify(messages)); }

function copyChat(){
  const clean = s => s.replace(/<!--QX(?:G2|SD|PA)\\|[\\s\\S]*?-->/g, '').trim();
  const text = messages.map(m => m.role === 'user' ? '【我】' + m.content : clean(m.content)).join('\\n\\n');
  const done = () => { const b = document.getElementById('copyBtn'); b.textContent = '✅ 已复制';
                       setTimeout(() => b.textContent = '📋 复制对话', 1500); };
  if (navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(done);
  } else {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove(); done();
  }
}

function resetChat(){
  if (!confirm('清空当前对话，重新开始？')) return;
  localStorage.removeItem('weilu_msgs');
  location.reload();
}

// 刷新后恢复历史对话
if (messages.length){
  const welcome = document.querySelector('.welcome'); if (welcome) welcome.remove();
  for (const m of messages){
    if (m.role === 'user'){ userBubble(m.content); }
    else { const box = document.createElement('div'); log.appendChild(box); renderRound(m.content, box); }
  }
}

// 吉祥物头像位置：心心(小晴,第3位66%)、清清(0%)、华华(33%)、理理(100%)
function avaStyle(name){
  if (name === '小晴') return "background-position:66% 50%";
  const members = ['清清','华华','理理'];
  let h = 0; for (const c of name) h = (h*31 + c.charCodeAt(0)) >>> 0;
  return "background-position:" + (h % 3) * 33 + "% 50%";
}

function userBubble(text){
  const m = document.createElement('div');
  m.className = 'msg user';
  m.innerHTML = '<div class="ava" style="background-image:url(\\'/assets/img/xqx-logo.jpg\\');background-size:cover"></div>'
    + '<div class="col"><div class="who user">我</div><div class="text"></div></div>';
  m.querySelector('.text').textContent = text;
  log.appendChild(m);
}

function makeBubble(name, box){
  const m = document.createElement('div');
  m.className = 'msg';
  m.innerHTML = '<div class="ava" style="' + avaStyle(name) + '"></div>'
    + '<div class="col"><div class="who ' + (name === '小晴' ? 'leader' : 'member') + '">' + esc(name) + '</div><div class="text"></div></div>';
  box.appendChild(m);
  return m.querySelector('.text');
}

function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// 每个【发言人】一行 = 一个独立气泡，模拟"一条条消息分别发出"
function renderRound(acc, box){
  box.innerHTML = '';
  let textEl = null;
  for (const ln of acc.split('\\n')){
    if (ln.trim().startsWith('<!--QX')) continue;
    const m = ln.match(/^【([^】]+)】(.*)$/);
    if (m){
      textEl = makeBubble(m[1].trim(), box);
      textEl.textContent = m[2];
    } else if (ln.trim().startsWith('（圆桌进度') || /^[📍⏳🎨✨]/.test(ln.trim())){
      const d = document.createElement('div');
      d.style.cssText = 'color:var(--dim);font-size:12px;text-align:center;margin:16px 0 4px';
      d.textContent = ln.trim();
      box.appendChild(d);
      textEl = null;
    } else if (ln.trim()){
      if (!textEl) textEl = makeBubble('小晴', box);
      const d = document.createElement('div');
      d.textContent = ln;
      textEl.appendChild(d);
    }
  }
  window.scrollTo(0, document.body.scrollHeight);
}

input.addEventListener('keydown', ev => {
  if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    send(ev);
  }
});

function addAttachments(atts){
  for (const a of atts){
    // 附件链接改写为同源相对路径：测试服务端口与 PUBLIC_BASE_URL 不一致时也能显示
    let url = a.fileUrl;
    try {
      const u = new URL(a.fileUrl, location.origin);
      if (u.pathname.startsWith('/files/')) url = u.pathname;
    } catch(e){}
    const w = document.createElement('div');
    w.className = 'attach';
    if (a.fileType === 'image'){
      const img = new Image(); img.src = url; w.appendChild(img);
    }
    const link = document.createElement('a');
    link.href = url; link.textContent = '📎 ' + a.fileName + ' (' + Math.round((a.fileSize||0)/1024) + 'KB)';
    link.target = '_blank';
    w.appendChild(link);
    log.appendChild(w);
  }
  window.scrollTo(0, document.body.scrollHeight);
}

function quickSend(text){
  if (btn.disabled) return;
  input.value = text;
  send(new Event('submit'));
}

async function send(ev){
  ev.preventDefault();
  const text = input.value.trim();
  if (!text || btn.disabled) return false;
  input.value = '';
  messages.push({ role: 'user', content: text });
  persist();
  userBubble(text);
  const welcome = document.querySelector('.welcome'); if (welcome) welcome.remove();
  btn.disabled = true;
  const box = document.createElement('div');
  log.appendChild(box);
  let acc = '';
  try {
    const resp = await fetch('/public/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stream: true, messages })
    });
    if (!resp.ok){ makeBubble('小晴', box).textContent = 'HTTP ' + resp.status + ' — ' + (await resp.text()).slice(0,200); return false; }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '', attachments = null;
    while (true){
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\\n\\n');
      buf = parts.pop();
      for (const p of parts){
        const line = p.trim();
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') continue;
        try {
          const chunk = JSON.parse(payload);
          const delta = chunk.choices[0].delta || {};
          if (delta.content){ acc += delta.content; renderRound(acc, box); }
          if (chunk.x_soda && chunk.x_soda.attachments) attachments = chunk.x_soda.attachments;
        } catch(e){}
      }
    }
    messages.push({ role: 'assistant', content: acc });
    persist();
    if (attachments) addAttachments(attachments);
  } catch(e){
    makeBubble('小晴', box).textContent = '\\n[连接出错] ' + e.message;
  } finally {
    btn.disabled = false;
    input.focus();
  }
  return false;
}
</script>
</body>
</html>
"""


def register_webui(app) -> None:  # noqa: ANN001 - FastAPI实例
    @app.get("/", include_in_schema=False)
    async def index():
        return HTMLResponse(PAGE)
