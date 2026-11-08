// Relit audio: the engine decides which sounds play and how loud; this plays
// them with Web Audio, placed in 3D around the player (HRTF), through a reverb
// that follows the size of the room, plus OPL music streamed from the engine.
// GPL-2.0-or-later.

export class GameAudio {
  constructor(wad) {
    this.wad = wad;
    this.buffers = new Map();
    this.channels = new Map();
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = AC ? new AC({ latencyHint: 'interactive' }) : null;
    if (!this.ctx) return;
    const c = this.ctx;
    this.master = c.createGain(); this.master.gain.value = 0.9;
    const comp = c.createDynamicsCompressor();
    comp.threshold.value = -14; comp.knee.value = 12; comp.ratio.value = 4; comp.attack.value = 0.004; comp.release.value = 0.2;
    this.master.connect(comp).connect(c.destination);
    // sound effects go dry plus a share through the room reverb
    this.sfxBus = c.createGain(); this.sfxBus.connect(this.master);
    this.reverb = c.createConvolver(); this.reverb.buffer = impulse(c, 2.2, 3.2);
    this.wet = c.createGain(); this.wet.gain.value = 0.18;
    this.sfxBus.connect(this.wet).connect(this.reverb).connect(this.master);
    this.musicGain = c.createGain(); this.musicGain.gain.value = 0.55; this.musicGain.connect(this.master);
    this.musicAt = 0;
    // browsers start audio only after a user gesture
    const resume = () => { if (c.state !== 'running') c.resume(); };
    ['pointerdown', 'keydown', 'touchend'].forEach((n) => addEventListener(n, resume, { passive: true }));
    resume();
  }

  // Imports for the engine (wasm module "relit").
  imports() {
    return {
      sfx_start: (ch, lump, vol, sep, haspos, x, y, z) => this.start(ch, lump, vol, haspos, x, y, z),
      sfx_update: (ch, vol, sep, haspos, x, y, z) => this.update(ch, vol, haspos, x, y, z),
      sfx_stop: (ch) => this.stop(ch),
      sfx_playing: (ch) => (this.channels.get(ch)?.playing ? 1 : 0),
    };
  }

  // Doom's DMX sound lumps: 8-bit unsigned PCM with 16 bytes of padding at each end.
  buffer(lump) {
    if (this.buffers.has(lump)) return this.buffers.get(lump);
    let buf = null;
    try {
      const d = this.wad.data(lump);
      if (d.byteLength > 8 && d.getUint16(0, true) === 3) {
        const rate = d.getUint16(2, true) || 11025, n = d.getUint32(4, true);
        const start = 8 + 16, len = Math.max(1, Math.min(n - 32, d.byteLength - start - 16));
        buf = this.ctx.createBuffer(1, len, rate);
        const ch = buf.getChannelData(0);
        for (let i = 0; i < len; i++) ch[i] = (d.getUint8(start + i) - 128) / 128;
      }
    } catch { buf = null; }
    this.buffers.set(lump, buf);
    return buf;
  }

  start(ch, lump, vol, haspos, x, y, z) {
    this.started = (this.started || 0) + 1;
    if (!this.ctx) return;
    this.stop(ch);
    const buf = this.buffer(lump); if (!buf) return;
    const c = this.ctx;
    const src = c.createBufferSource(); src.buffer = buf;
    const gain = c.createGain(); gain.gain.value = vol / 127;
    let panner = null;
    if (haspos) {
      panner = c.createPanner();
      panner.panningModel = 'HRTF';
      panner.distanceModel = 'linear'; panner.rolloffFactor = 0; // the engine already handles distance
      setPos(panner, x, z, -y);
      src.connect(gain).connect(panner).connect(this.sfxBus);
    } else {
      src.connect(gain).connect(this.sfxBus);
    }
    const entry = { src, gain, panner, playing: true };
    src.onended = () => { entry.playing = false; if (this.channels.get(ch) === entry) this.channels.delete(ch); };
    this.channels.set(ch, entry);
    src.start();
  }

  update(ch, vol, haspos, x, y, z) {
    const e = this.channels.get(ch); if (!e) return;
    e.gain.gain.setTargetAtTime(vol / 127, this.ctx.currentTime, 0.02);
    if (e.panner && haspos) setPos(e.panner, x, z, -y);
  }

  stop(ch) {
    const e = this.channels.get(ch); if (!e) return;
    e.playing = false;
    try { e.src.stop(); } catch { /* already stopped */ }
    this.channels.delete(ch);
  }

  // Once per frame: listener follows the camera, the reverb follows the room,
  // and music is rendered a quarter second ahead.
  frame(camera, roomHeight, outdoors, engine) {
    if (!this.ctx || this.ctx.state !== 'running') return;
    const c = this.ctx, l = c.listener, t = c.currentTime;
    const p = camera.position;
    const f = { x: 0, y: 0, z: -1 }, u = { x: 0, y: 1, z: 0 };
    const q = camera.quaternion;
    const rot = (v) => {
      const ix = q.w * v.x + q.y * v.z - q.z * v.y, iy = q.w * v.y + q.z * v.x - q.x * v.z, iz = q.w * v.z + q.x * v.y - q.y * v.x, iw = -q.x * v.x - q.y * v.y - q.z * v.z;
      return { x: ix * q.w + iw * -q.x + iy * -q.z - iz * -q.y, y: iy * q.w + iw * -q.y + iz * -q.x - ix * -q.z, z: iz * q.w + iw * -q.z + ix * -q.y - iy * -q.x };
    };
    const F = rot(f), U = rot(u);
    if (l.positionX) {
      l.positionX.value = p.x; l.positionY.value = p.y; l.positionZ.value = p.z;
      l.forwardX.value = F.x; l.forwardY.value = F.y; l.forwardZ.value = F.z;
      l.upX.value = U.x; l.upY.value = U.y; l.upZ.value = U.z;
    } else {
      l.setPosition(p.x, p.y, p.z); l.setOrientation(F.x, F.y, F.z, U.x, U.y, U.z);
    }
    // taller rooms ring longer; open sky barely echoes
    const wet = outdoors ? 0.07 : Math.min(0.42, Math.max(0.08, (roomHeight - 56) / 420));
    this.wet.gain.setTargetAtTime(wet, t, 0.4);
    // music
    if (engine) {
      const rate = engine.e.dgx_music_rate();
      if (this.musicAt < t + 0.02) this.musicAt = t + 0.06;
      while (this.musicAt < t + 0.25) {
        const n = 2048;
        const ptr = engine.e.dgx_music_render(n);
        const data = new Float32Array(engine.e.memory.buffer, ptr, n * 2);
        const b = c.createBuffer(2, n, rate), L = b.getChannelData(0), R = b.getChannelData(1);
        for (let i = 0; i < n; i++) { L[i] = data[i * 2]; R[i] = data[i * 2 + 1]; }
        const s = c.createBufferSource(); s.buffer = b; s.connect(this.musicGain); s.start(this.musicAt);
        this.musicAt += n / rate;
      }
    }
  }
}

function setPos(panner, x, y, z) {
  if (panner.positionX) { panner.positionX.value = x; panner.positionY.value = y; panner.positionZ.value = z; }
  else panner.setPosition(x, y, z);
}

// A generated room impulse: decaying stereo noise with early reflections.
function impulse(ctx, seconds, decay) {
  const rate = ctx.sampleRate, n = Math.floor(rate * seconds), b = ctx.createBuffer(2, n, rate);
  for (let c = 0; c < 2; c++) {
    const d = b.getChannelData(c);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, decay);
    for (const ms of [11, 19, 29, 41]) { const k = Math.floor(rate * ms / 1000) + c * 7; if (k < n) d[k] += 0.6; }
  }
  return b;
}
