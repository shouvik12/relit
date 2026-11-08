// Relit AI remaster: runs the Real-ESRGAN compact network (realesr-general-x4v3,
// BSD-3-Clause, by Xintao Wang et al.) on the player's GPU with plain WebGL2,
// turning each 1993 texture or sprite into a 4x larger, cleaner image.
// GPL-2.0-or-later (this file); the model weights are BSD-3-Clause.

const VS = `#version 300 es
in vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;

// One 3x3 convolution layer. Activations are stored as RGBA tiles side by
// side: tile g holds channels 4g..4g+3 of a W x H image.
const CONV = `#version 300 es
precision highp float; precision highp int; precision highp sampler2D;
uniform sampler2D uIn, uWt;
uniform int uW, uH, uGin, uBase, uPrelu, uWrap;
out vec4 o;
vec4 wt(int i){ i += uBase; return texelFetch(uWt, ivec2(i & 4095, i >> 12), 0); }
void main(){
  ivec2 p = ivec2(gl_FragCoord.xy);
  int k = p.x / uW, x = p.x - k * uW, y = p.y;
  int base = k * (2 + uGin * 36);
  vec4 acc = wt(base), alpha = wt(base + 1);
  int wi = base + 2;
  for (int g = 0; g < uGin; g++) {
    for (int t = 0; t < 9; t++) {
      int sy = y + t / 3 - 1, sx = x + t - (t / 3) * 3 - 1;
      vec4 v = vec4(0.0);
      if (uWrap == 1) { sx = (sx + uW) % uW; sy = (sy + uH) % uH; v = texelFetch(uIn, ivec2(g * uW + sx, sy), 0); }
      else if (sx >= 0 && sy >= 0 && sx < uW && sy < uH) v = texelFetch(uIn, ivec2(g * uW + sx, sy), 0);
      acc += mat4(wt(wi), wt(wi + 1), wt(wi + 2), wt(wi + 3)) * v;
      wi += 4;
    }
  }
  if (uPrelu == 1) acc = max(acc, 0.0) + alpha * min(acc, 0.0);
  o = acc;
}`;

// Pixel shuffle x4 plus the nearest-neighbour base image, to 8-bit RGB.
const SHUFFLE = `#version 300 es
precision highp float; precision highp int; precision highp sampler2D;
uniform sampler2D uIn, uSrc;
uniform int uW;
out vec4 o;
void main(){
  ivec2 P = ivec2(gl_FragCoord.xy);
  int x = P.x / 4, y = P.y / 4, j = P.x - x * 4, i = P.y - y * 4;
  vec3 src = texelFetch(uSrc, ivec2(x, y), 0).rgb, c;
  for (int ch = 0; ch < 3; ch++) {
    int n = ch * 16 + i * 4 + j, g = n / 4, comp = n - g * 4;
    c[ch] = texelFetch(uIn, ivec2(g * uW + x, y), 0)[comp] + src[ch];
  }
  o = vec4(clamp(c, 0.0, 1.0), 1.0);
}`;

export class Upscaler {
  static create(weightBytes) {
    try { return new Upscaler(weightBytes); } catch (e) { console.warn('AI remaster unavailable:', e.message); return null; }
  }

  constructor(bytes) {
    const canvas = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(1, 1) : document.createElement('canvas');
    const gl = canvas.getContext('webgl2', { antialias: false, depth: false, stencil: false, premultipliedAlpha: false, preserveDrawingBuffer: false });
    if (!gl) throw new Error('no WebGL2');
    if (!gl.getExtension('EXT_color_buffer_float')) throw new Error('no float render targets');
    this.gl = gl;
    this.maxTex = gl.getParameter(gl.MAX_TEXTURE_SIZE);

    // --- weights
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]) !== 'RLAI') throw new Error('bad weights');
    const n = dv.getUint32(8, true);
    this.layers = [];
    let off = 12, texel = 0;
    for (let i = 0; i < n; i++) {
      const cin = dv.getUint32(off, true), cout = dv.getUint32(off + 4, true), prelu = dv.getUint32(off + 8, true);
      const gin = (cin + 3) >> 2, gout = (cout + 3) >> 2;
      this.layers.push({ gin, gout, prelu, base: texel });
      texel += gout * (2 + gin * 36);
      off += 12;
    }
    const halfs = new Uint16Array(bytes.buffer.slice(bytes.byteOffset + off, bytes.byteOffset + off + texel * 8));
    const H = Math.ceil(texel / 4096);
    const padded = new Uint16Array(4096 * H * 4); padded.set(halfs);
    this.wt = this.tex(4096, H, gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT, padded);

    // --- programs
    const prog = (fs) => {
      const p = gl.createProgram();
      for (const [type, src] of [[gl.VERTEX_SHADER, VS], [gl.FRAGMENT_SHADER, fs]]) {
        const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
        gl.attachShader(p, s);
      }
      gl.bindAttribLocation(p, 0, 'p'); gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      const u = {}; for (let i = 0, c = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); i < c; i++) { const n = gl.getActiveUniform(p, i).name; u[n] = gl.getUniformLocation(p, n); }
      return { p, u };
    };
    this.conv = prog(CONV);
    this.shuffle = prog(SHUFFLE);
    const vb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vb);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    this.fb = gl.createFramebuffer();
    this.buffers = new Map();
  }

  tex(w, h, internal, format, type, data) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, type, data || null);
    return t;
  }

  // Ping-pong activation buffers, reused for images of the same size.
  bufs(w, h) {
    const key = w + 'x' + h;
    if (this.buffers.has(key)) return this.buffers.get(key);
    const gl = this.gl;
    if (this.buffers.size > 6) { for (const b of this.buffers.values()) { gl.deleteTexture(b.a); gl.deleteTexture(b.b); gl.deleteTexture(b.out); } this.buffers.clear(); }
    const b = {
      a: this.tex(w * 16, h, gl.RGBA32F, gl.RGBA, gl.FLOAT),
      b: this.tex(w * 16, h, gl.RGBA32F, gl.RGBA, gl.FLOAT),
      out: this.tex(w * 4, h * 4, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE),
    };
    this.buffers.set(key, b);
    return b;
  }

  canRun(w, h) { return w * 16 <= this.maxTex && h * 4 <= this.maxTex && w >= 1 && h >= 1; }

  // Starts a job. Call step(job) once per frame until it returns true; the
  // result is then in job.result (Uint8Array, (4w)*(4h)*4). Work is spread
  // over frames and read back asynchronously, so the game never waits on it.
  begin(rgba, w, h, wrap) {
    const gl = this.gl;
    const { rgb, opaque } = prepare(rgba, w, h);
    return { rgba, w, h, wrap, opaque, src: this.tex(w, h, gl.RGBA32F, gl.RGBA, gl.FLOAT, rgb), b: this.bufs(w, h), li: 0, input: null, phase: 'conv', result: null };
  }

  step(job) {
    const gl = this.gl, { w, h, b } = job;
    if (job.phase === 'conv') {
      // cap the GPU work per frame, in multiply-adds (a few ms on a laptop GPU)
      let budget = 4e8;
      gl.bindFramebuffer(gl.FRAMEBUFFER, this.fb);
      gl.useProgram(this.conv.p);
      gl.uniform1i(this.conv.u.uIn, 0); gl.uniform1i(this.conv.u.uWt, 1);
      gl.uniform1i(this.conv.u.uW, w); gl.uniform1i(this.conv.u.uH, h); gl.uniform1i(this.conv.u.uWrap, job.wrap ? 1 : 0);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this.wt);
      while (job.li < this.layers.length && budget > 0) {
        const L = this.layers[job.li];
        const input = job.li === 0 ? job.src : (job.li % 2 ? b.a : b.b);
        const output = job.li % 2 ? b.b : b.a;
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, output, 0);
        gl.viewport(0, 0, w * L.gout, h);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, input);
        gl.uniform1i(this.conv.u.uGin, L.gin); gl.uniform1i(this.conv.u.uBase, L.base); gl.uniform1i(this.conv.u.uPrelu, L.prelu);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        job.input = output;
        budget -= w * h * L.gout * L.gin * 9 * 16;
        job.li++;
      }
      if (job.li < this.layers.length) { gl.flush(); return false; }
      // pixel shuffle, then queue an asynchronous read-back
      gl.useProgram(this.shuffle.p);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, b.out, 0);
      gl.viewport(0, 0, w * 4, h * 4);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, job.input);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, job.src);
      gl.uniform1i(this.shuffle.u.uIn, 0); gl.uniform1i(this.shuffle.u.uSrc, 1); gl.uniform1i(this.shuffle.u.uW, w);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      job.pbo = gl.createBuffer();
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, job.pbo);
      gl.bufferData(gl.PIXEL_PACK_BUFFER, w * 4 * h * 4 * 4, gl.STREAM_READ);
      gl.readPixels(0, 0, w * 4, h * 4, gl.RGBA, gl.UNSIGNED_BYTE, 0);
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
      job.sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
      gl.flush();
      job.phase = 'read';
      return false;
    }
    if (job.phase === 'read') {
      if (gl.getSyncParameter(job.sync, gl.SYNC_STATUS) !== gl.SIGNALED) return false;
      gl.deleteSync(job.sync);
      const out = new Uint8Array(w * 4 * h * 4 * 4);
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, job.pbo);
      gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, out);
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
      gl.deleteBuffer(job.pbo); gl.deleteTexture(job.src);
      if (!job.opaque) applyAlpha(out, job.rgba, w, h);
      job.result = out; job.phase = 'done';
      return true;
    }
    return job.phase === 'done';
  }

  // Whole job in one go (used for testing). WebGL only reports a finished
  // fence after the page yields, so this waits between checks.
  async run(rgba, w, h, wrap) {
    const job = this.begin(rgba, w, h, wrap);
    while (!this.step(job)) await new Promise((r) => setTimeout(r, 0));
    return job.result;
  }
}

// RGB as floats; transparent pixels are filled from their neighbours so the
// network doesn't see dark halos around sprites.
function prepare(rgba, w, h) {
  const n = w * h, rgb = new Float32Array(n * 4);
  let known = new Uint8Array(n), opaque = true;
  for (let i = 0; i < n; i++) {
    if (rgba[i * 4 + 3] > 0) { known[i] = 1; rgb[i * 4] = rgba[i * 4] / 255; rgb[i * 4 + 1] = rgba[i * 4 + 1] / 255; rgb[i * 4 + 2] = rgba[i * 4 + 2] / 255; }
    else opaque = false;
  }
  if (!opaque) {
    for (let pass = 0; pass < 6; pass++) {
      const next = known.slice();
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const i = y * w + x; if (known[i]) continue;
        let r = 0, g = 0, b = 0, c = 0;
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const X = x + dx, Y = y + dy; if (X < 0 || Y < 0 || X >= w || Y >= h) continue;
          const j = Y * w + X; if (!known[j]) continue;
          r += rgb[j * 4]; g += rgb[j * 4 + 1]; b += rgb[j * 4 + 2]; c++;
        }
        if (c) { rgb[i * 4] = r / c; rgb[i * 4 + 1] = g / c; rgb[i * 4 + 2] = b / c; next[i] = 1; }
      }
      known = next;
    }
  }
  return { rgb, opaque };
}

// Alpha for the 4x image: bilinear from the original, then a soft edge.
function applyAlpha(out, rgba, w, h) {
  const W = w * 4, H = h * 4;
  const a = (x, y) => rgba[(Math.min(h - 1, Math.max(0, y)) * w + Math.min(w - 1, Math.max(0, x))) * 4 + 3];
  for (let Y = 0; Y < H; Y++) {
    const fy = (Y + 0.5) / 4 - 0.5, y0 = Math.floor(fy), ty = fy - y0;
    for (let X = 0; X < W; X++) {
      const fx = (X + 0.5) / 4 - 0.5, x0 = Math.floor(fx), tx = fx - x0;
      const v = (a(x0, y0) * (1 - tx) + a(x0 + 1, y0) * tx) * (1 - ty) + (a(x0, y0 + 1) * (1 - tx) + a(x0 + 1, y0 + 1) * tx) * ty;
      // soft edge: smooth step around the old 50% cut, so outlines aren't stair-stepped
      const e = Math.min(1, Math.max(0, (v - 72) / 112));
      out[(Y * W + X) * 4 + 3] = Math.round(e * e * (3 - 2 * e) * 255);
    }
  }
}

// ---------------------------------------------------------------- scheduler + cache

// Queues remaster jobs, runs one per frame on the GPU, swaps the result into
// the live textures, and keeps results in IndexedDB so the next launch is
// remastered from the first frame.
export class Remaster {
  constructor(upscaler, wadId, onStatus) {
    this.up = upscaler; this.wadId = wadId; this.onStatus = onStatus || (() => {});
    this.jobs = new Map(); this.queue = []; this.gpuQueue = []; this.current = null; this.lookups = 0; this.done = 0; this.total = 0;
    this.db = openDb();
  }

  // kind: 't' wall, 'f' flat, 's' sprite, 'k' sky. px: Uint32Array RGBA.
  request(kind, name, px, w, h, wrap, tex, priority, after) {
    const key = `${this.wadId}|${kind}|${name}|v3`;
    let job = this.jobs.get(key);
    if (job) { if (!job.done) { job.texs.push(tex); if (after) job.after.push(after); job.priority = Math.max(job.priority, priority); } else if (job.result) { applyTo(tex, job.result); after?.(job.result); } return; }
    if (!this.up.canRun(w, h)) return;
    job = { key, px, w, h, wrap, texs: [tex], after: after ? [after] : [], priority, done: false, result: null };
    this.jobs.set(key, job); this.queue.push(job); this.total++;
    this.onStatus(this.done, this.total);
  }

  // Call once per frame. Cache lookups run several at a time; new art is
  // computed on the GPU one image at a time, spread over frames.
  pump() {
    const pick = (q) => {
      let bi = 0;
      for (let i = 1; i < q.length; i++) if (q[i].priority > q[bi].priority) bi = i;
      return q.splice(bi, 1)[0];
    };
    while (this.lookups < 24 && this.queue.length) {
      const job = pick(this.queue);
      this.lookups++;
      this.cacheGet(job.key, job.w * 4, job.h * 4).then((hit) => {
        this.lookups--;
        if (hit) this.finish(job, hit); else this.gpuQueue.push(job);
      });
    }
    const cur = this.current;
    if (!cur) {
      if (!this.gpuQueue.length) return;
      const job = pick(this.gpuQueue);
      try { job.gpu = this.up.begin(new Uint8Array(job.px.buffer, job.px.byteOffset, job.px.byteLength), job.w, job.h, job.wrap); this.current = job; }
      catch (e) { console.warn('remaster failed', job.key, e); this.finish(job, null); }
      return;
    }
    let done = false;
    try { done = this.up.step(cur.gpu); } catch (e) { console.warn('remaster failed', cur.key, e); this.current = null; this.finish(cur, null); return; }
    if (done) {
      this.current = null;
      this.cachePut(cur.key, cur.gpu.result, cur.w * 4, cur.h * 4);
      this.finish(cur, cur.gpu.result);
    }
  }

  finish(job, data) {
    if (data) {
      const result = { data, width: job.w * 4, height: job.h * 4 };
      job.result = result;
      for (const t of job.texs) applyTo(t, result);
      for (const f of job.after) f(result);
    }
    job.done = true; job.px = null; job.gpu = null; job.texs = []; job.after = [];
    this.done++; this.onStatus(this.done, this.total);
  }

  async cacheGet(key, W, H) {
    try {
      const db = await this.db; if (!db) return null;
      const blob = await new Promise((res) => { const r = db.transaction('img').objectStore('img').get(key); r.onsuccess = () => res(r.result); r.onerror = () => res(null); });
      if (!blob) return null;
      const bmp = await createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
      if (bmp.width !== W || bmp.height !== H) return null;
      const c = new OffscreenCanvas(W, H), g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(bmp, 0, 0);
      return new Uint8Array(g.getImageData(0, 0, W, H, { colorSpace: 'srgb' }).data.buffer);
    } catch { return null; }
  }

  async cachePut(key, data, W, H) {
    try {
      const db = await this.db; if (!db || typeof OffscreenCanvas === 'undefined') return;
      const c = new OffscreenCanvas(W, H);
      c.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(data.buffer.slice(0)), W, H), 0, 0);
      const blob = await c.convertToBlob({ type: 'image/png' }); // lossless, so cached art matches exactly
      db.transaction('img', 'readwrite').objectStore('img').put(blob, key);
    } catch { /* storage full or unavailable: it just recomputes next time */ }
  }
}

function applyTo(tex, r) {
  tex.image = { data: r.data, width: r.width, height: r.height };
  tex.needsUpdate = true;
}

function openDb() {
  return new Promise((resolve) => {
    try {
      const r = indexedDB.open('relit-ai', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('img');
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => resolve(null);
    } catch { resolve(null); }
  });
}

// A short fingerprint of a WAD file, so cached art never mixes between files.
export function wadFingerprint(bytes) {
  let h = 0x811c9dc5;
  const step = Math.max(1, Math.floor(bytes.length / 65536));
  for (let i = 0; i < bytes.length; i += step) { h ^= bytes[i]; h = Math.imul(h, 0x01000193); }
  return (h >>> 0).toString(16) + '-' + bytes.length.toString(16);
}
