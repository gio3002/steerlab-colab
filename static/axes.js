/* axes.js — le 9 barre, le avanzate e l'equalizzatore per layer. */

'use strict';

const AX = {
  list: [], values: {}, profiles: {}, extremes: {}, nLayers: 0,
  current: null, drag: false,
};

const FAMILY_LABEL = { style: 'stile', emotion: 'emozioni', mode: 'modalità' };
const $a = id => document.getElementById(id);
const f2 = v => (v >= 0 ? '+' : '') + Number(v).toFixed(2);

async function loadAxes() {
  const r = await fetch('/api/axes').then(x => x.json());
  if (!r.ok) throw new Error(r.error || 'axes');
  AX.list = r.axes;
  AX.nLayers = r.n_layers || 28;
  for (const a of r.axes) {
    AX.values[a.key] = AX.values[a.key] || 0;
    AX.profiles[a.key] = AX.profiles[a.key] || a.profile.slice();
    AX.extremes[a.key] = {
      left: a.extreme_left, right: a.extreme_right,
      calibrated: a.calibrated, metrics: a.metrics || {},
    };
  }
  $a('calInfo').textContent = 'calibrazione: ' + (r.calibration || 'none');
  renderAxes();
}

function alphaOf(key, v) {
  const e = AX.extremes[key];
  const ext = v >= 0 ? e.right : e.left;
  return v * Math.abs(ext);
}

function renderAxes() {
  const box = $a('families');
  const fams = {};
  for (const a of AX.list) (fams[a.family] = fams[a.family] || []).push(a);
  box.innerHTML = Object.keys(fams).map(fam => `
    <div class="fam">
      <div class="fam-h">${FAMILY_LABEL[fam] || fam}</div>
      ${fams[fam].map(a => axisRow(a)).join('')}
    </div>`).join('');
  box.querySelectorAll('.axis input[type=range]').forEach(inp => {
    inp.oninput = () => {
      const k = inp.dataset.k;
      AX.values[k] = parseFloat(inp.value);
      paintAxis(k);
      if (window.onAxesChanged) window.onAxesChanged();
    };
  });
  box.querySelectorAll('.axis .sig').forEach(b => {
    b.onclick = () => openModal(b.dataset.k);
  });
  for (const a of AX.list) paintAxis(a.key);
}

function axisRow(a) {
  const v = AX.values[a.key] || 0;
  return `<div class="axis ${v ? 'on' : ''}" data-k="${a.key}">
    <button class="sig" data-k="${a.key}" title="avanzate: estremi ed equalizzatore">◈</button>
    <div class="row1">
      <span class="lbl">${a.left}</span>
      <span class="lbl r">${a.right}</span>
    </div>
    <span class="val" id="val-${a.key}">0.00</span>
    <input type="range" min="-1" max="1" step="0.01" value="${v}" data-k="${a.key}">
  </div>`;
}

function paintAxis(k) {
  const v = AX.values[k] || 0;
  const el = document.querySelector(`.axis[data-k="${k}"]`);
  if (el) el.classList.toggle('on', Math.abs(v) > 1e-6);
  const val = $a(`val-${k}`);
  if (val) val.textContent = Math.abs(v) < 1e-6 ? '0.00' : f2(alphaOf(k, v));
}

function axesPayload() {
  const out = {};
  for (const k of Object.keys(AX.values)) {
    if (Math.abs(AX.values[k]) > 1e-6) out[k] = AX.values[k];
  }
  return out;
}
function profilesPayload() {
  const out = {};
  for (const k of Object.keys(AX.profiles)) out[k] = AX.profiles[k];
  return out;
}
function resetAxes() {
  for (const k of Object.keys(AX.values)) AX.values[k] = 0;
  renderAxes();
  if (window.onAxesChanged) window.onAxesChanged();
}

// ------------------------------------------------------------- avanzate
function openModal(k) {
  AX.current = k;
  const a = AX.list.find(x => x.key === k);
  $a('mtitle').textContent = `${a.left} ↔ ${a.right}   ·   ${k}`;
  $a('mleft').value = AX.extremes[k].left.toFixed(2);
  $a('mright').value = AX.extremes[k].right.toFixed(2);
  const m = AX.extremes[k].metrics || {};
  const gain = m.gain_calibrated, flat = m.gain_flat;
  $a('mmetrics').textContent =
    `metodo: ${m.method || '—'}\n` +
    `guadagno con profilo calibrato: ${gain != null ? Number(gain).toFixed(3) : '—'}` +
    `   ·   profilo piatto: ${flat != null ? Number(flat).toFixed(3) : '—'}\n` +
    `estremi misurati: sinistro ${AX.extremes[k].left.toFixed(2)} · destro ${AX.extremes[k].right.toFixed(2)}` +
    (AX.extremes[k].calibrated ? '' : '\nATTENZIONE: valori di fallback, non misurati su questo modello.');
  $a('modal').classList.remove('hidden');
  drawEq();
}

function closeModal() { $a('modal').classList.add('hidden'); AX.current = null; }

function drawEq() {
  const c = $a('eq');
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = 110;
  c.width = Math.max(10, Math.floor(w * dpr));
  c.height = Math.floor(h * dpr);
  const ctx = c.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const k = AX.current; if (!k) return;
  const prof = AX.profiles[k];
  const n = prof.length || AX.nLayers;
  const bw = w / n;
  // griglia
  ctx.strokeStyle = '#1b2a3b'; ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) {
    const y = (h - 14) * i / 4;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  for (let i = 0; i < n; i++) {
    const v = prof[i] || 0;
    const bh = v * (h - 16);
    const x = i * bw + 1;
    ctx.fillStyle = v > 0.66 ? '#4fd1c5' : (v > 0.2 ? '#3d9aa8' : '#2a4a58');
    ctx.fillRect(x, h - 14 - bh, Math.max(1, bw - 2), bh);
  }
  ctx.fillStyle = '#54687f'; ctx.font = '9px ui-monospace,monospace';
  ctx.fillText('layer 0', 2, h - 3);
  const t = 'layer ' + (n - 1); ctx.fillText(t, w - ctx.measureText(t).width - 2, h - 3);
}

function eqSet(ev) {
  const c = $a('eq'), r = c.getBoundingClientRect();
  const k = AX.current; if (!k) return;
  const n = AX.profiles[k].length;
  const x = Math.max(0, Math.min(r.width - 1, ev.clientX - r.left));
  const y = Math.max(0, Math.min(r.height - 1, ev.clientY - r.top));
  const i = Math.floor(x / (r.width / n));
  const v = Math.max(0, Math.min(1, 1 - (y / (r.height - 16))));
  if (i >= 0 && i < n) { AX.profiles[k][i] = Math.round(v * 100) / 100; drawEq(); }
}

function wireModal() {
  $a('mclose').onclick = closeModal;
  $a('modal').onclick = e => { if (e.target.id === 'modal') closeModal(); };
  const c = $a('eq');
  c.onmousedown = e => { AX.drag = true; eqSet(e); };
  window.addEventListener('mousemove', e => { if (AX.drag) eqSet(e); });
  window.addEventListener('mouseup', () => { AX.drag = false; });
  $a('eqCal').onclick = () => {
    const a = AX.list.find(x => x.key === AX.current);
    AX.profiles[AX.current] = a.profile.slice();
    drawEq();
  };
  $a('eqFlat').onclick = () => {
    AX.profiles[AX.current] = new Array(AX.nLayers).fill(1);
    drawEq();
  };
  $a('eqZero').onclick = () => {
    AX.profiles[AX.current] = new Array(AX.nLayers).fill(0);
    drawEq();
  };
  $a('eqApply').onclick = async () => {
    const k = AX.current;
    AX.extremes[k].left = Math.abs(parseFloat($a('mleft').value) || 0.35);
    AX.extremes[k].right = Math.abs(parseFloat($a('mright').value) || 0.35);
    await fetch('/api/axes/profile', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: k, profile: AX.profiles[k] }),
    });
    await fetch('/api/axes/extremes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: k, extreme_left: AX.extremes[k].left,
                             extreme_right: AX.extremes[k].right }),
    });
    paintAxis(k); closeModal();
  };
  window.addEventListener('resize', () => { if (AX.current) drawEq(); });
}

document.addEventListener('DOMContentLoaded', () => { wireModal(); });
