// Relit page logic: pick a game file, start the engine, drive the HUD.
// GPL-2.0-or-later.
import { run } from './remaster.js';

const $ = (id) => document.getElementById(id);
const stage = $('stage');
const hpEl = $('hp'), arEl = $('ar'), amEl = $('am'), msgEl = $('msg');
let lastHud = '', started = false;

// The game file stays in this browser: remembered in IndexedDB so the next
// visit is one click. Nothing is uploaded anywhere.
const DB = 'relit', STORE = 'files';
function idb() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
async function saveWad(name, bytes) {
  try {
    const db = await idb();
    await new Promise((res, rej) => { const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).put({ name, bytes }, 'wad'); tx.oncomplete = res; tx.onerror = () => rej(tx.error); });
  } catch { /* private mode or storage full: just ask again next time */ }
}
async function loadWad() {
  try {
    const db = await idb();
    return await new Promise((res) => { const r = db.transaction(STORE).objectStore(STORE).get('wad'); r.onsuccess = () => res(r.result || null); r.onerror = () => res(null); });
  } catch { return null; }
}
async function forgetWad() {
  try { const db = await idb(); db.transaction(STORE, 'readwrite').objectStore(STORE).delete('wad'); } catch { /* nothing to forget */ }
}

function b64(str) {
  const bin = atob(str), out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
async function aiBytes() {
  if (/[?&]noai\b/.test(location.search)) return null; // ?noai turns the AI remaster off
  if (window.RELIT_AI) return b64(window.RELIT_AI);
  try { const r = await fetch('relit-ai.bin'); return r.ok ? new Uint8Array(await r.arrayBuffer()) : null; } catch { return null; }
}

async function engineBytes() {
  if (window.RELIT_WASM) {
    const bin = atob(window.RELIT_WASM), out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  const r = await fetch('doom.wasm');
  if (!r.ok) throw new Error('Could not load the engine (doom.wasm)');
  return new Uint8Array(await r.arrayBuffer());
}

// ---- bundled extra levels ----------------------------------------------
// The Quake level ships as a PWAD (a Doom add-on file). Relit appends its lumps to
// the player's game file in memory; the engine and the 3D renderer both use the last
// copy of a lump name, so the bundled E1M1 replaces the game's own for this session.
async function gunzip(bytes) {
  const ds = new DecompressionStream('gzip');
  const buf = await new Response(new Blob([bytes]).stream().pipeThrough(ds)).arrayBuffer();
  return new Uint8Array(buf);
}
async function slipgateBytes() {
  if (window.RELIT_SLIPGATE) return gunzip(b64(window.RELIT_SLIPGATE));
  const r = await fetch('maps/slipgate.wad');
  if (!r.ok) throw new Error('Could not load the Quake level (maps/slipgate.wad)');
  return new Uint8Array(await r.arrayBuffer());
}
function appendPwad(base, pwad) {
  const bv = new DataView(base.buffer, base.byteOffset, base.byteLength);
  const pv = new DataView(pwad.buffer, pwad.byteOffset, pwad.byteLength);
  const bn = bv.getInt32(4, true), bdir = bv.getInt32(8, true);
  const pn = pv.getInt32(4, true), pdir = pv.getInt32(8, true);
  let dataLen = 0;
  for (let i = 0; i < pn; i++) dataLen += pv.getInt32(pdir + i * 16 + 4, true);
  const out = new Uint8Array(base.byteLength + dataLen + (bn + pn) * 16);
  out.set(base, 0);
  const ov = new DataView(out.buffer);
  let at = base.byteLength;
  const dirAt = base.byteLength + dataLen;
  out.set(base.subarray(bdir, bdir + bn * 16), dirAt);
  for (let i = 0; i < pn; i++) {
    const e = pdir + i * 16, pos = pv.getInt32(e, true), size = pv.getInt32(e + 4, true);
    out.set(pwad.subarray(pos, pos + size), at);
    const d = dirAt + (bn + i) * 16;
    ov.setInt32(d, size ? at : 0, true); ov.setInt32(d + 4, size, true);
    out.set(pwad.subarray(e + 8, e + 16), d + 8);
    at += size;
  }
  ov.setInt32(4, bn + pn, true); ov.setInt32(8, dirAt, true);
  return out;
}
function hasLump(bytes, name) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const n = v.getInt32(4, true), dir = v.getInt32(8, true);
  for (let i = 0; i < n; i++) {
    let s = '';
    for (let k = 0; k < 8; k++) { const c = bytes[dir + i * 16 + 8 + k]; if (!c) break; s += String.fromCharCode(c); }
    if (s.toUpperCase() === name) return true;
  }
  return false;
}
const levelNote = () => { $('levelnote').hidden = document.querySelector('input[name=level]:checked').value !== 'slipgate'; };
document.querySelectorAll('input[name=level]').forEach((r) => r.addEventListener('change', levelNote));

function showError(msg) {
  $('load').hidden = false;
  $('barfill').style.width = '0';
  $('loadmsg').innerHTML = '';
  const s = document.createElement('span'); s.className = 'err'; s.textContent = msg; $('loadmsg').append(s);
  $('pick').disabled = false; $('again').disabled = false;
}

async function start(name, wadBytes, remember) {
  if (started) return;
  started = true;
  $('pick').disabled = true; $('again').disabled = true; $('load').hidden = false;
  const skill = +document.querySelector('input[name=skill]:checked').value;
  const level = document.querySelector('input[name=level]:checked').value;
  const fileBytes = wadBytes;
  try {
    if (level === 'slipgate') {
      if (!hasLump(wadBytes, 'E1M1')) throw new Error('the Quake level needs a Doom 1-style file such as freedoom1.wad or doom.wad, not a Doom II one');
      $('loadmsg').textContent = 'Opening the Slipgate…';
      wadBytes = appendPwad(wadBytes, await slipgateBytes());
    }
    const wasmBytes = await engineBytes();
    const ai = await aiBytes();
    await run({
      stage, canvas: $('gl'), weaponCanvas: $('weapon'), fbCanvas: $('fb'),
      wasmBytes, wadBytes, skill, aiBytes: ai, noAO: /[?&]noao\b/.test(location.search),
      onAI: (done, total) => {
        const el = $('aistat');
        el.textContent = `AI remastering art · ${done} / ${total}`;
        el.classList.toggle('off', done >= total);
      },
      toggle: $('toggle'), torchButton: $('torch'),
      onTorch: (on) => $('torch').setAttribute('aria-pressed', on),
      buttons: document.querySelectorAll('.touch button, #menuBtn'),
      onProgress: (msg, f) => { $('loadmsg').textContent = msg; $('barfill').style.width = (f * 100) + '%'; },
      onView: (v) => stage.classList.toggle('view-fb', v === 'fb'),
      onMode: (m) => $('toggle').setAttribute('aria-pressed', m === '1993'),
      onLock: (on) => $('hint').classList.toggle('off', on),
      onMessage: (m) => { msgEl.textContent = m; msgEl.style.opacity = 1; },
      onCrash: (err, log) => { console.error(err, log); },
      status: (fps) => { $('fps').textContent = fps + ' fps'; },
      hud: (P, msgAge) => {
        const ammo = P[9] < 0 ? '–' : P[9] | 0;
        const key = `${P[6]}|${P[7]}|${ammo}|${P[24]}${P[25]}${P[26]}`;
        if (key !== lastHud) {
          lastHud = key;
          hpEl.querySelector('b').textContent = Math.max(0, P[6] | 0); hpEl.classList.toggle('low', P[6] < 26);
          arEl.querySelector('b').textContent = P[7] | 0;
          amEl.querySelector('b').textContent = ammo; amEl.classList.toggle('low', P[9] >= 0 && P[9] < 10);
          $('kb').classList.toggle('on', !!P[24]); $('ky').classList.toggle('on', !!P[25]); $('kr').classList.toggle('on', !!P[26]);
        }
        if (msgAge > 3500) msgEl.style.opacity = 0;
      },
    });
    if (remember) saveWad(name, fileBytes);
    $('start').hidden = true;
    setTimeout(() => $('thint').classList.add('off'), 6000);
  } catch (err) {
    started = false;
    showError('Couldn\'t start: ' + (err.message || err) + '.');
  }
}

async function fromFile(file) {
  if (!file) return;
  if (!/\.wad$/i.test(file.name)) { showError('Pick a .wad file, for example freedoom1.wad.'); return; }
  $('load').hidden = false; $('loadmsg').textContent = 'Reading ' + file.name + '…';
  start(file.name, new Uint8Array(await file.arrayBuffer()), true);
}

$('pick').addEventListener('click', () => $('wadfile').click());
$('wadfile').addEventListener('change', (e) => fromFile(e.target.files[0]));

// drag and drop anywhere on the start screen
const startEl = $('start');
['dragenter', 'dragover'].forEach((n) => startEl.addEventListener(n, (e) => { e.preventDefault(); startEl.classList.add('drop'); }));
['dragleave', 'drop'].forEach((n) => startEl.addEventListener(n, (e) => { e.preventDefault(); if (n === 'dragleave' && startEl.contains(e.relatedTarget)) return; startEl.classList.remove('drop'); }));
startEl.addEventListener('drop', (e) => fromFile(e.dataTransfer.files[0]));

// returning player: offer the remembered file
loadWad().then((saved) => {
  if (!saved || !saved.bytes) return;
  $('againName').textContent = saved.name;
  $('remembered').hidden = false;
  $('again').addEventListener('click', () => start(saved.name, saved.bytes, false));
  $('forget').addEventListener('click', () => { forgetWad(); $('remembered').hidden = true; });
});
