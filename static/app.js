/* app.js — chat in streaming, ispezione dei token, stato. */

'use strict';

const S = { history: [], busy: false, lastFormatted: null };

const $ = id => document.getElementById(id);
const nz = v => (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toFixed(3);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function welcome() {
  $('chatlog').innerHTML = `<div class="msg sys"><div class="bubble">
    <b>Chat con steering.</b> Le barre a sinistra cambiano il comportamento del modello
    mentre parli: 0 significa nessuno steering, il valore mostrato è l'intensità α.
    Il simbolo ◈ apre estremi ed equalizzatore per layer.<br>
    Passa il mouse su una parola della risposta per evidenziarla; clicca per vedere
    i 5 token più probabili in quel punto.
  </div></div>`;
}

async function api(path, body) {
  const opt = body === undefined ? { method: 'GET' } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json();
  if (j.ok === false) throw new Error(j.error || 'errore');
  return j;
}

async function refreshState() {
  try {
    const st = await api('/api/state');
    const name = String(st.effective_model || st.model_id).split(/[\\/]/).pop();
    $('status').textContent =
      `${name} · ${st.device}${st.quantized ? ' · 4bit' : ''} · ` +
      `${st.n_layers || '—'} layer · ${st.hidden || '—'} dim · ` +
      `calibrazione ${st.calibration}` + (st.calibration_matches === false ? ' (MISMATCH)' : '');
    $('btnLoad').textContent = st.loaded ? 'MODELLO CARICATO' : 'CARICA MODELLO';
    $('btnLoad').disabled = !!st.loaded;
  } catch (e) { $('status').textContent = 'server non raggiungibile'; }
}

async function loadModel() {
  $('btnLoad').disabled = true;
  $('btnLoad').textContent = 'CARICO… (1-2 min)';
  try {
    await api('/api/model/load', {});
    await refreshState();
    await loadAxes();
    footer('modello pronto. Scrivi qualcosa.', 'ok');
  } catch (e) {
    footer('caricamento: ' + e.message, 'err');
    $('btnLoad').disabled = false;
    $('btnLoad').textContent = 'CARICA MODELLO';
  }
}

function footer(msg, cls) {
  const el = $('strip');
  el.innerHTML = `<span class="st" style="color:${cls === 'err' ? '#ff5f6d' : '#9fc4dd'}">${esc(msg)}</span>`;
}

function strip(r) {
  const axes = Object.entries(AX.values).filter(([k, v]) => Math.abs(v) > 1e-6);
  $('strip').innerHTML = [
    `<span class="st"><b>KL</b> ${nz(r.kl)}</span>`,
    `<span class="st"><b>token</b> ${(r.tokens_meta || []).length}</span>`,
    `<span class="st"><b>ms</b> ${r.compute_ms || 0}</span>`,
    ...axes.map(([k, v]) => `<span class="st"><b>${esc(k)}</b> ${f2(alphaOf(k, v))}</span>`),
  ].join('');
}

// ------------------------------------------------------------- popover
let pop = null;
function closePop() { if (pop) { pop.remove(); pop = null; } }
function showPop(span, meta) {
  closePop();
  const d = document.createElement('div');
  d.className = 'tokpop';
  d.style.cssText = 'position:absolute;z-index:60;background:#0c1622f2;border:1px solid #2d4055;' +
    'border-radius:6px;padding:8px 9px;min-width:230px;box-shadow:0 10px 30px #000a;' +
    'font-family:ui-monospace,monospace;font-size:10px';
  d.innerHTML = `<div style="color:#9fc4dd;font-size:9px;margin-bottom:6px">distribuzione in questo punto</div>`
    + meta.top.map((t, i) => `<div style="display:grid;grid-template-columns:70px 1fr 44px;gap:6px;align-items:center;margin:3px 0">
        <span style="white-space:pre;overflow:hidden;text-overflow:ellipsis;color:${i === 0 ? '#4fd1c5' : '#bcd3e6'}">${esc(t.token || '∅')}</span>
        <span style="height:6px;background:#162230;border-radius:3px;overflow:hidden"><i style="display:block;height:100%;width:${Math.max(2, t.p * 100)}%;background:#4fd1c5"></i></span>
        <span style="text-align:right;color:#7f95ad">${(t.p * 100).toFixed(1)}%</span></div>`).join('')
    + `<div style="margin-top:6px;padding-top:5px;border-top:1px solid #22303f;color:#7f95ad">
        scelto <b style="color:#dbe7f3">${esc(meta.text || '')}</b> · p=${(meta.p * 100).toFixed(2)}% · rango ${meta.rank}</div>`;
  document.body.appendChild(d);
  const r = span.getBoundingClientRect();
  let left = r.left + window.scrollX, top = r.bottom + window.scrollY + 6;
  if (left + d.offsetWidth > innerWidth - 8) left = innerWidth - d.offsetWidth - 8;
  if (top + d.offsetHeight > innerHeight - 8) top = r.top + window.scrollY - d.offsetHeight - 6;
  d.style.left = Math.max(8, left) + 'px';
  d.style.top = Math.max(8, top) + 'px';
  pop = d;
}
document.addEventListener('click', e => {
  if (pop && !pop.contains(e.target) && !e.target.classList.contains('tk')) closePop();
});

// -------------------------------------------------------------- streaming
async function streamReply(body, onEvent) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  if (!res.ok || !res.body) throw new Error('stream HTTP ' + res.status);
  const rd = res.body.getReader(), dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { value, done } = await rd.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data:')) continue;
        const ev = JSON.parse(line.slice(5).trim());
        onEvent(ev);
        if (ev.event === 'error') throw new Error(ev.error || 'errore stream');
      }
    }
  }
}

function bubble(role) {
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  const b = document.createElement('div'); b.className = 'bubble';
  wrap.appendChild(b);
  $('chatlog').appendChild(wrap);
  $('chatlog').scrollTop = $('chatlog').scrollHeight;
  return { wrap, b };
}

async function send() {
  if (S.busy) return;
  const text = $('input').value.trim();
  if (!text) return;
  $('input').value = '';
  const { b } = bubble('user'); b.textContent = text;
  const { wrap, b: bot } = bubble('bot');
  const caret = document.createElement('span'); caret.className = 'caret';
  bot.appendChild(caret);
  const metas = []; let acc = '';
  S.busy = true; $('send').disabled = true;
  try {
    await streamReply({
      history: S.history, message: text,
      axes: axesPayload(), profiles: profilesPayload(),
      max_new: parseInt($('maxnew').value) || 96, temperature: 0,
    }, ev => {
      if (ev.event === 'start') { S.lastFormatted = ev.formatted_prompt; }
      else if (ev.event === 'token') {
        metas.push(ev);
        if (ev.special) return;
        const s = document.createElement('span');
        s.className = 'tk'; s.dataset.i = String(metas.length - 1);
        s.textContent = ev.text; acc += ev.text;
        bot.insertBefore(s, caret);
        $('chatlog').scrollTop = $('chatlog').scrollHeight;
      } else if (ev.event === 'done') {
        S.history = ev.history;
        caret.remove();
        if (acc.trim() !== String(ev.reply || '').trim()) {
          bot.textContent = ev.reply;
        } else {
          bot.querySelectorAll('.tk').forEach(s => {
            const m = metas[parseInt(s.dataset.i, 10)];
            if (m) s.onclick = e => { e.stopPropagation(); showPop(s, m); };
          });
        }
        const meta = document.createElement('div'); meta.className = 'msgmeta';
        meta.textContent = `KL ${nz(ev.kl)} · ${(ev.tokens_meta || []).length} token · ${ev.compute_ms} ms`;
        wrap.appendChild(meta);
        strip(ev);
        $('chatlog').scrollTop = $('chatlog').scrollHeight;
      }
    });
  } catch (e) {
    caret.remove();
    footer('chat: ' + e.message, 'err');
  } finally {
    S.busy = false; $('send').disabled = false;
  }
}

window.onAxesChanged = () => { /* aggiorna solo la strip se c'è un ultimo turno */ };

document.addEventListener('DOMContentLoaded', async () => {
  welcome();
  $('btnLoad').onclick = loadModel;
  $('btnReset').onclick = resetAxes;
  $('send').onclick = send;
  $('input').onkeydown = e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };
  await refreshState();
  try { await loadAxes(); } catch (e) { footer('assi: ' + e.message, 'err'); }
  setInterval(refreshState, 8000);
});
