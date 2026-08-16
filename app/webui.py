"""内置测试页：小清心暖色明亮风（吉祥物头像 + 奶油底 + 珊瑚橘主色）。"""
from __future__ import annotations

from fastapi.responses import HTMLResponse

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>围炉夜话 · 小清心</title>
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
  body{margin:0;background:linear-gradient(180deg,var(--bg) 0%,var(--bg2) 100%);color:var(--ink);
       font:15px/1.75 "PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;}
  header{position:sticky;top:0;z-index:5;background:rgba(255,251,246,.92);backdrop-filter:blur(8px);
         border-bottom:1px solid var(--line);padding:10px 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
  header .logo{width:44px;height:44px;border-radius:14px;object-fit:cover;box-shadow:0 2px 8px rgba(240,96,60,.18);}
  header h1{font-size:18px;margin:0;color:var(--ink);letter-spacing:1px;}
  header h1 b{color:var(--accent-deep);}
  header .tag{font-size:12px;color:#fff;background:linear-gradient(120deg,var(--accent),var(--gold));
              padding:2px 10px;border-radius:12px;font-weight:600;}
  #key{margin-left:auto;background:#fff;border:1px solid var(--line);color:var(--ink);border-radius:10px;
       padding:5px 10px;font-size:12px;width:200px;box-shadow:0 1px 3px rgba(90,70,54,.05);}
  main{max-width:780px;margin:0 auto;padding:22px 16px 140px;}
  .welcome{background:var(--panel);border:1px solid var(--line);border-radius:22px;padding:22px;margin:14px 0;
           box-shadow:0 6px 24px rgba(240,96,60,.08);text-align:center;}
  .welcome img{width:min(420px,88%);border-radius:14px;margin-bottom:10px;}
  .welcome .t{font-weight:700;font-size:16px;}
  .welcome .d{color:var(--dim);font-size:13px;margin-top:4px;}
  .msg{margin:14px 0;display:flex;gap:10px;align-items:flex-start;}
  .msg .ava{flex:0 0 38px;width:38px;height:38px;border-radius:50%;background-image:url('/assets/img/xqx-mascots.jpg');
            background-size:400% 100%;border:2px solid #fff;box-shadow:0 2px 6px rgba(90,70,54,.15);margin-top:4px;}
  .msg .col{flex:1;min-width:0;}
  .msg .who{font-weight:700;font-size:13px;margin-bottom:3px;padding:1px 10px;border-radius:10px;display:inline-block;}
  .who.leader{background:#FFE3D6;color:var(--accent-deep);}
  .who.member{background:#EFE7FF;color:#7757c4;}
  .who.user{background:#DDF2E7;color:#3d8a68;}
  .msg .text{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:10px 14px;
             white-space:pre-wrap;box-shadow:0 2px 10px rgba(90,70,54,.05);}
  .msg.user{flex-direction:row-reverse;}
  .msg.user .text{background:#FFF0E4;border-color:#FFD9C2;}
  .msg .line{display:flex;gap:8px;align-items:flex-start;margin:7px 0;}
  .msg .line .spk{flex:0 0 auto;font-size:13px;font-weight:700;}
  .spk.leader{color:var(--accent-deep);}
  .spk.member{color:#7757c4;}
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
  <h1>围炉夜话 · <b>小清心</b></h1><span class="tag">AI团体支持空间</span>
  <input id="key" placeholder="API Key（sk-weilu-开头）" title="API Key">
</header>
<main id="log">
  <div class="welcome">
    <img src="/assets/img/xqx-mascots.jpg" alt="清清华华心心理理">
    <div class="t">和小晴一起，围炉开聊 🕯️</div>
    <div class="d">试试发「最近科研压力好大」，或「画会，聊学业压力」</div>
  </div>
</main>
<form onsubmit="return send(event)">
  <div class="hint">Enter 发送 · Shift+Enter 换行</div>
  <inner><textarea id="input" placeholder="说点什么……" autofocus></textarea><button id="btn">发送</button></inner>
</form>
<script>
const log = document.getElementById('log');
const input = document.getElementById('input');
const btn = document.getElementById('btn');
const keyBox = document.getElementById('key');
keyBox.value = localStorage.getItem('weilu_key') || 'sk-weilu-dev-key';
keyBox.addEventListener('change', () => localStorage.setItem('weilu_key', keyBox.value.trim()));
let messages = [];

// 吉祥物头像位置：心心(小晴,第3位66%)、清清(0%)、华华(33%)、理理(100%)
function avaStyle(name){
  if (name === '小晴') return "background-position:66% 50%";
  if (name === '同学') return "background-image:url('/assets/img/xqx-logo.jpg');background-size:cover";
  const members = ['清清','华华','理理'];
  let h = 0; for (const c of name) h = (h*31 + c.charCodeAt(0)) >>> 0;
  return "background-position:" + (h % 3) * 33 + "% 50%";
}

function userBubble(text){
  const m = document.createElement('div');
  m.className = 'msg user';
  m.innerHTML = '<div class="ava" style="background-image:url(\\'/assets/img/xqx-logo.jpg\\');background-size:cover"></div>'
    + '<div class="col"><div class="who user">同学</div><div class="text"></div></div>';
  m.querySelector('.text').textContent = text;
  log.appendChild(m);
}

function leaderBubble(){
  const m = document.createElement('div');
  m.className = 'msg';
  m.innerHTML = '<div class="ava" style="background-position:66% 50%"></div>'
    + '<div class="col"><div class="text"></div></div>';
  log.appendChild(m);
  return m.querySelector('.text');
}

function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

function renderBlock(text, el){
  el.innerHTML = '';
  const lines = text.split('\\n');
  for (const ln of lines){
    const m = ln.match(/^【([^】]+)】(.*)$/);
    if (m){
      const cls = m[1]==='小晴' ? 'leader' : 'member';
      const d = document.createElement('div');
      d.className = 'line';
      d.innerHTML = '<span class="spk ' + cls + '">【' + esc(m[1]) + '】</span><span class="body"></span>';
      d.querySelector('.body').textContent = m[2];
      el.appendChild(d);
    } else if (ln.trim().startsWith('（围炉进度')){
      const d = document.createElement('div');
      d.style.cssText = 'color:var(--dim);font-size:12px;margin:12px 0 2px';
      d.textContent = ln.trim();
      el.appendChild(d);
    } else if (ln.trim()){
      const d = document.createElement('div');
      d.textContent = ln;
      el.appendChild(d);
    }
  }
  window.scrollTo(0, document.body.scrollHeight);
}

function addAttachments(atts){
  for (const a of atts){
    const w = document.createElement('div');
    w.className = 'attach';
    if (a.fileType === 'image'){
      const img = new Image(); img.src = a.fileUrl; w.appendChild(img);
    }
    const link = document.createElement('a');
    link.href = a.fileUrl; link.textContent = '📎 ' + a.fileName + ' (' + Math.round((a.fileSize||0)/1024) + 'KB)';
    link.target = '_blank';
    w.appendChild(link);
    log.appendChild(w);
  }
  window.scrollTo(0, document.body.scrollHeight);
}

async function send(ev){
  ev.preventDefault();
  const text = input.value.trim();
  if (!text || btn.disabled) return false;
  input.value = '';
  messages.push({ role: 'user', content: text });
  userBubble(text);
  const welcome = document.querySelector('.welcome'); if (welcome) welcome.remove();
  btn.disabled = true;
  const out = leaderBubble();
  let acc = '';
  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + keyBox.value.trim() },
      body: JSON.stringify({ stream: true, messages })
    });
    if (!resp.ok){ out.textContent = 'HTTP ' + resp.status + ' — ' + (await resp.text()).slice(0,200); return false; }
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
          if (delta.content){ acc += delta.content; renderBlock(acc, out); }
          if (chunk.x_soda && chunk.x_soda.attachments) attachments = chunk.x_soda.attachments;
        } catch(e){}
      }
    }
    messages.push({ role: 'assistant', content: acc });
    if (attachments) addAttachments(attachments);
  } catch(e){
    out.textContent += '\\n[连接出错] ' + e.message;
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
