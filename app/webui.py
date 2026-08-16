"""内置本地测试页：浏览器直接体验围炉夜话（含逐字节奏与附件展示）。"""
from __future__ import annotations

from fastapi.responses import HTMLResponse

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>围炉夜话 · 本地测试</title>
<style>
  :root { --bg:#141c29; --panel:#1d2736; --line:#2c3a4e; --paper:#f5ead8; --gold:#e6be78; --dim:#96a0b0; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--paper); font:15px/1.7 "PingFang SC","Microsoft YaHei",sans-serif; }
  header { padding:14px 20px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; color:var(--gold); letter-spacing:2px; }
  header .tag { font-size:12px; color:var(--dim); border:1px solid var(--line); padding:1px 8px; border-radius:10px; }
  #key { margin-left:auto; background:var(--panel); border:1px solid var(--line); color:var(--paper); border-radius:8px; padding:4px 10px; font-size:12px; width:190px; }
  main { max-width:760px; margin:0 auto; padding:20px 16px 130px; }
  .msg { margin:14px 0; }
  .msg .who { font-weight:600; }
  .msg.user .who { color:#7ec2ff; }
  .msg.leader .who { color:var(--gold); }
  .msg.member .who { color:#9fd8a8; }
  .msg .text { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 14px; margin-top:4px; white-space:pre-wrap; }
  .msg.user .text { background:#20344a; }
  .marker { color:var(--dim); font-size:12px; text-align:center; margin:18px 0; }
  .attach img { max-width:70%; border-radius:12px; border:1px solid var(--line); display:block; margin:8px 0; }
  .attach a { color:#7ec2ff; font-size:13px; text-decoration:none; }
  form { position:fixed; bottom:0; left:0; right:0; background:rgba(20,28,41,.96); border-top:1px solid var(--line); padding:12px 16px; }
  form inner { max-width:760px; margin:0 auto; display:flex; gap:10px; }
  textarea { flex:1; background:var(--panel); color:var(--paper); border:1px solid var(--line); border-radius:10px; padding:10px 12px; font:inherit; resize:none; height:52px; }
  button { background:var(--gold); color:#241c0e; border:0; border-radius:10px; padding:0 22px; font-size:15px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .hint { max-width:760px; margin:0 auto; font-size:12px; color:var(--dim); padding:0 0 6px; }
</style>
</head>
<body>
<header>
  <h1>围炉夜话</h1><span class="tag">测试页</span>
  <input id="key" placeholder="API Key（sk-weilu-开头）" title="API Key">
</header>
<main id="log">
  <div class="marker">—— 直接发一句话开始，例如「最近科研压力好大」或「画会，聊学业压力」——</div>
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

function bubble(role, who, cls) {
  const m = document.createElement('div');
  m.className = 'msg ' + cls;
  m.innerHTML = '<div class="who">' + who + '</div><div class="text"></div>';
  log.appendChild(m);
  return m.querySelector('.text');
}

function renderBlock(text, el) {
  // 按【发言人】行渲染成多段
  el.innerHTML = '';
  const lines = text.split('\\n');
  for (const ln of lines) {
    const m = ln.match(/^【([^】]+)】(.*)$/);
    const d = document.createElement('div');
    if (m) {
      d.innerHTML = '<span style="color:' + (m[1]==='小晴' ? 'var(--gold)' : '#9fd8a8') + '">【' + m[1] + '】</span>';
      d.appendChild(document.createTextNode(m[2]));
      d.style.margin = '6px 0';
    } else if (ln.trim().startsWith('（围炉进度')) {
      d.className = 'marker-inline'; d.style.cssText = 'color:var(--dim);font-size:12px;margin:10px 0 2px';
      d.textContent = ln.trim();
    } else {
      d.textContent = ln;
    }
    el.appendChild(d);
  }
  window.scrollTo(0, document.body.scrollHeight);
}

function addAttachments(atts) {
  for (const a of atts) {
    const w = document.createElement('div');
    w.className = 'msg attach';
    if (a.fileType === 'image') {
      const img = new Image(); img.src = a.fileUrl; w.appendChild(img);
    }
    const link = document.createElement('a');
    link.href = a.fileUrl; link.textContent = '📎 ' + a.fileName + ' (' + Math.round((a.fileSize||0)/1024) + 'KB)'; link.target = '_blank';
    w.appendChild(link);
    log.appendChild(w);
  }
  window.scrollTo(0, document.body.scrollHeight);
}

async function send(ev) {
  ev.preventDefault();
  const text = input.value.trim();
  if (!text || btn.disabled) return false;
  input.value = '';
  messages.push({ role: 'user', content: text });
  bubble('user', '同学', 'user').textContent = text;
  btn.disabled = true;
  const out = bubble('assistant', '围炉', 'leader');
  let acc = '';
  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + document.getElementById('key').value },
      body: JSON.stringify({ stream: true, messages })
    });
    if (!resp.ok) { out.textContent = 'HTTP ' + resp.status + ' — ' + (await resp.text()).slice(0, 200); return false; }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '', attachments = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\\n\\n');
      buf = parts.pop();
      for (const p of parts) {
        const line = p.trim();
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') continue;
        try {
          const chunk = JSON.parse(payload);
          const delta = chunk.choices[0].delta || {};
          if (delta.content) { acc += delta.content; renderBlock(acc, out); }
          if (chunk.x_soda && chunk.x_soda.attachments) attachments = chunk.x_soda.attachments;
        } catch (e) {}
      }
    }
    messages.push({ role: 'assistant', content: acc });
    if (attachments) addAttachments(attachments);
  } catch (e) {
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
