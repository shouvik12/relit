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
  try {
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
    if (remember) saveWad(name, wadBytes);
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
