// Relit renderer: the original Doom engine (compiled to WebAssembly)
// runs the game; this file draws its world in real 3D with Three.js.
// GPL-2.0-or-later, to match the engine it is built around.

import {
  THREE, EffectComposer, RenderPass, UnrealBloomPass, OutputPass, ShaderPass, GTAOPass, earcut,
  WASI, File, OpenFile, ConsoleStdout, PreopenDirectory,
} from './vendor/libs.js';
import { Upscaler, Remaster, wadFingerprint } from './ai.js';
import { GameAudio } from './audio.js';
import { Effects } from './effects.js';

const TIC_MS = 1000 / 35;
const FF_FULLBRIGHT = 0x8000, FF_FRAMEMASK = 0x7fff;
const MF_SHADOW = 0x40000, MF_MISSILE = 0x10000;
const ML_DONTPEGTOP = 8, ML_DONTPEGBOTTOM = 16, ML_TWOSIDED = 4;
const UPSCALE = 2; // each Scale2x pass doubles the texture size

// ---------------------------------------------------------------- WAD

class Wad {
  constructor(buf) {
    this.u8 = new Uint8Array(buf);
    this.dv = new DataView(buf);
    const n = this.dv.getInt32(4, true), off = this.dv.getInt32(8, true);
    this.lumps = [];
    for (let i = 0; i < n; i++) {
      const p = off + i * 16;
      this.lumps.push({ pos: this.dv.getInt32(p, true), size: this.dv.getInt32(p + 4, true), name: this.str(p + 8, 8) });
    }
  }
  str(p, len) {
    let s = '';
    for (let i = 0; i < len; i++) { const c = this.u8[p + i]; if (!c) break; s += String.fromCharCode(c); }
    return s.toUpperCase();
  }
  find(name) { for (let i = this.lumps.length - 1; i >= 0; i--) if (this.lumps[i].name === name) return i; return -1; }
  data(i) { const l = this.lumps[i]; return new DataView(this.u8.buffer, l.pos, l.size); }
  bytes(i) { const l = this.lumps[i]; return this.u8.subarray(l.pos, l.pos + l.size); }
}

// ---------------------------------------------------------------- pictures

function readPatch(wad, lump, pal) {
  const d = wad.data(lump);
  const w = d.getInt16(0, true), h = d.getInt16(2, true);
  const left = d.getInt16(4, true), top = d.getInt16(6, true);
  const px = new Uint32Array(w * h);
  for (let x = 0; x < w; x++) {
    let p = d.getInt32(8 + x * 4, true);
    for (;;) {
      const row = d.getUint8(p); if (row === 0xff) break;
      const len = d.getUint8(p + 1);
      for (let i = 0; i < len; i++) {
        const y = row + i;
        if (y < h) px[y * w + x] = pal[d.getUint8(p + 3 + i)];
      }
      p += len + 4;
    }
  }
  return { w, h, left, top, px };
}

// Scale2x (EPX): doubles pixel art while keeping edges crisp.
function scale2x(src, w, h) {
  const W = w * 2, dst = new Uint32Array(W * h * 2);
  for (let y = 0; y < h; y++) {
    const ym = y > 0 ? y - 1 : y, yp = y < h - 1 ? y + 1 : y;
    for (let x = 0; x < w; x++) {
      const xm = x > 0 ? x - 1 : x, xp = x < w - 1 ? x + 1 : x;
      const E = src[y * w + x], B = src[ym * w + x], H = src[yp * w + x], D = src[y * w + xm], F = src[y * w + xp];
      let e0 = E, e1 = E, e2 = E, e3 = E;
      if (B !== H && D !== F) {
        if (D === B) e0 = D;
        if (B === F) e1 = F;
        if (D === H) e2 = D;
        if (H === F) e3 = F;
      }
      const o = y * 2 * W + x * 2;
      dst[o] = e0; dst[o + 1] = e1; dst[o + W] = e2; dst[o + W + 1] = e3;
    }
  }
  return dst;
}

function upscale(px, w, h, times) {
  for (let i = 0; i < times; i++) { px = scale2x(px, w, h); w *= 2; h *= 2; }
  return { px, w, h };
}

// Height from brightness, then a tangent-space normal map from its slopes.
function normalMapFrom(px, w, h, strength, wrap) {
  const hgt = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const c = px[i];
    hgt[i] = (c >>> 24) < 128 ? 0 : ((c & 255) * 0.3 + ((c >> 8) & 255) * 0.59 + ((c >> 16) & 255) * 0.11) / 255;
  }
  const at = (x, y) => {
    if (wrap) { x = (x + w) % w; y = (y + h) % h; } else { x = Math.max(0, Math.min(w - 1, x)); y = Math.max(0, Math.min(h - 1, y)); }
    return hgt[y * w + x];
  };
  const out = new Uint8Array(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const dx = (at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x - 1, y) + at(x - 1, y + 1));
    const dy = (at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x, y - 1) + at(x + 1, y - 1));
    let nx = -dx * strength, ny = dy * strength, nz = 1;
    const l = Math.hypot(nx, ny, nz); nx /= l; ny /= l; nz /= l;
    const o = (y * w + x) * 4;
    out[o] = (nx * 0.5 + 0.5) * 255; out[o + 1] = (ny * 0.5 + 0.5) * 255; out[o + 2] = (nz * 0.5 + 0.5) * 255; out[o + 3] = 255;
  }
  return out;
}

let maxAniso = 1;
function makeTex(data, w, h, color) {
  const t = new THREE.DataTexture(data, w, h, THREE.RGBAFormat, THREE.UnsignedByteType);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.magFilter = THREE.LinearFilter;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.generateMipmaps = true;
  t.anisotropy = maxAniso;
  t.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.needsUpdate = true;
  return t;
}

// ---------------------------------------------------------------- shared lighting

// Every surface samples its sector's light level from this texture, so
// flickering and strobing sectors stay as cheap as one texture upload.
const shared = {
  uSectorTex: { value: null },
  uSectorCount: { value: 1 },
  uAmbient: { value: 1 },
};

function sectorLit(mat) {
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.uSectorTex = shared.uSectorTex;
    sh.uniforms.uSectorCount = shared.uSectorCount;
    sh.uniforms.uAmbient = shared.uAmbient;
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float aSector;\nuniform sampler2D uSectorTex;\nuniform float uSectorCount;\nvarying float vSectorLight;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvSectorLight = texture2D(uSectorTex, vec2((aSector + 0.5) / uSectorCount, 0.5)).r;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vSectorLight;\nuniform float uAmbient;')
      .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance += diffuseColor.rgb * vSectorLight * uAmbient;');
  };
  mat.customProgramCacheKey = () => 'sectorLit';
  return mat;
}

// Doom light 0..255 to a linear ambient amount.
const lightCurve = (l) => Math.pow(Math.max(0, Math.min(255, l)) / 255, 1.8) * 1.3 + 0.01;

// ---------------------------------------------------------------- assets

const LIQUID = /^(NUKAGE|FWATER|LAVA|SLIME|BLOOD|SWATER)/;

class Assets {
  constructor(wad) {
    this.wad = wad;
    const pp = wad.bytes(wad.find('PLAYPAL'));
    this.pal = new Uint32Array(256);
    for (let i = 0; i < 256; i++) this.pal[i] = (255 << 24) | (pp[i * 3 + 2] << 16) | (pp[i * 3 + 1] << 8) | pp[i * 3];
    // patches
    const pn = wad.data(wad.find('PNAMES'));
    this.pnames = [];
    for (let i = 0, n = pn.getInt32(0, true); i < n; i++) this.pnames.push(wad.str(pn.byteOffset + 4 + i * 8, 8));
    this.pstart = wad.find('P_START');
    // textures, in the engine's order (TEXTURE1 then TEXTURE2)
    this.textures = [];
    for (const name of ['TEXTURE1', 'TEXTURE2']) {
      const i = wad.find(name); if (i < 0) continue;
      const d = wad.data(i), n = d.getInt32(0, true);
      for (let t = 0; t < n; t++) {
        const o = d.getInt32(4 + t * 4, true);
        const tex = { name: wad.str(d.byteOffset + o, 8), w: d.getInt16(o + 12, true), h: d.getInt16(o + 14, true), patches: [] };
        const np = d.getInt16(o + 20, true);
        for (let p = 0; p < np; p++) {
          const q = o + 22 + p * 10;
          tex.patches.push({ x: d.getInt16(q, true), y: d.getInt16(q + 2, true), p: d.getInt16(q + 4, true) });
        }
        this.textures.push(tex);
      }
    }
    // flats: engine flat n is lump F_START + 1 + n
    this.fstart = wad.find('F_START') + 1;
    this.skyflat = wad.find('F_SKY1') - this.fstart;
    // sprites
    const s0 = wad.find('S_START'), s1 = wad.find('S_END');
    this.spriteLumps = new Map();
    for (let i = s0 + 1; i < s1; i++) {
      const n = wad.lumps[i].name; if (n.length < 6) continue;
      const key = n.slice(0, 4);
      if (!this.spriteLumps.has(key)) this.spriteLumps.set(key, []);
      this.spriteLumps.get(key).push(i);
    }
    this.spriteTables = new Map();
    this.wallMats = new Map(); this.flatMats = new Map(); this.spriteCache = new Map();
    this.patchCache = new Map();
    this.ai = () => {}; // set when the AI remaster is available
  }

  patchByName(name) {
    if (this.patchCache.has(name)) return this.patchCache.get(name);
    let lump = -1;
    for (let i = this.wad.lumps.length - 1; i >= 0; i--) if (this.wad.lumps[i].name === name) { lump = i; break; }
    const p = lump >= 0 ? readPatch(this.wad, lump, this.pal) : null;
    this.patchCache.set(name, p);
    return p;
  }

  textureImage(idx) {
    const t = this.textures[idx];
    const px = new Uint32Array(t.w * t.h);
    for (const pp of t.patches) {
      const p = this.patchByName(this.pnames[pp.p]); if (!p) continue;
      for (let y = 0; y < p.h; y++) {
        const ty = pp.y + y; if (ty < 0 || ty >= t.h) continue;
        for (let x = 0; x < p.w; x++) {
          const tx = pp.x + x; if (tx < 0 || tx >= t.w) continue;
          const c = p.px[y * p.w + x]; if (c >>> 24) px[ty * t.w + tx] = c;
        }
      }
    }
    return { px, w: t.w, h: t.h };
  }

  wallMaterial(idx) {
    if (this.wallMats.has(idx)) return this.wallMats.get(idx);
    const img = this.textureImage(idx);
    let masked = false;
    for (let i = 0; i < img.px.length; i++) if (!(img.px[i] >>> 24)) { masked = true; break; }
    const up = upscale(img.px, img.w, img.h, UPSCALE);
    const mat = sectorLit(new THREE.MeshStandardMaterial({
      map: makeTex(new Uint8Array(up.px.buffer), up.w, up.h, true),
      normalMap: makeTex(normalMapFrom(up.px, up.w, up.h, 2.2, true), up.w, up.h, false),
      normalScale: new THREE.Vector2(1, -1),
      roughness: 0.78, metalness: 0.0,
      alphaTest: masked ? 0.35 : 0, alphaToCoverage: masked,
      side: masked ? THREE.DoubleSide : THREE.FrontSide,
    }));
    this.ai('t', this.textures[idx].name, img.px, img.w, img.h, !masked, mat.map, 1);
    const entry = { mat, w: img.w, h: img.h, masked, name: this.textures[idx].name };
    this.wallMats.set(idx, entry);
    return entry;
  }

  flatMaterial(n) {
    if (this.flatMats.has(n)) return this.flatMats.get(n);
    const lump = this.fstart + n, src = this.wad.bytes(lump), name = this.wad.lumps[lump].name;
    const px = new Uint32Array(64 * 64);
    for (let i = 0; i < 4096 && i < src.length; i++) px[i] = this.pal[src[i]];
    const up = upscale(px, 64, 64, UPSCALE);
    const glow = LIQUID.test(name);
    const map = makeTex(new Uint8Array(up.px.buffer), up.w, up.h, true);
    const mat = sectorLit(new THREE.MeshStandardMaterial({
      map,
      normalMap: makeTex(normalMapFrom(up.px, up.w, up.h, glow ? 1.0 : 2.0, true), up.w, up.h, false),
      normalScale: new THREE.Vector2(1, -1),
      roughness: glow ? 0.25 : 0.72, metalness: 0.0,
      emissive: glow ? 0xffffff : 0x000000, emissiveMap: glow ? map : null, emissiveIntensity: glow ? 0.55 : 0,
    }));
    this.ai('f', name, px, 64, 64, true, map, 1);
    const entry = { mat, name, glow };
    this.flatMats.set(n, entry);
    return entry;
  }

  // Frame/rotation table for a 4-letter sprite name, Doom-style.
  spriteTable(name) {
    if (this.spriteTables.has(name)) return this.spriteTables.get(name);
    const frames = [];
    for (const i of this.spriteLumps.get(name) || []) {
      const n = this.wad.lumps[i].name;
      const put = (fc, rc, flip) => {
        const f = fc.charCodeAt(0) - 65, r = rc.charCodeAt(0) - 48;
        frames[f] = frames[f] || [];
        if (r === 0) for (let k = 0; k < 8; k++) frames[f][k] = frames[f][k] || { lump: i, flip };
        else frames[f][r - 1] = { lump: i, flip };
      };
      put(n[4], n[5], false);
      if (n.length >= 8) put(n[6], n[7], true);
    }
    this.spriteTables.set(name, frames);
    return frames;
  }

  // Remastered sprite: texture, size, offsets and a light colour.
  sprite(lump, priority = 0) {
    if (this.spriteCache.has(lump)) return this.spriteCache.get(lump);
    const p = readPatch(this.wad, lump, this.pal);
    const up = upscale(p.px, p.w, p.h, UPSCALE);
    let r = 0, g = 0, b = 0, wsum = 0;
    for (const c of p.px) {
      if (!(c >>> 24)) continue;
      const cr = c & 255, cg = (c >> 8) & 255, cb = (c >> 16) & 255, wgt = Math.pow((cr + cg + cb) / 765, 3);
      r += cr * wgt; g += cg * wgt; b += cb * wgt; wsum += wgt;
    }
    const color = wsum ? new THREE.Color(r / wsum / 255, g / wsum / 255, b / wsum / 255) : new THREE.Color(1, 0.8, 0.5);
    const tex = makeTex(new Uint8Array(up.px.buffer), up.w, up.h, true);
    tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
    // alpha-to-coverage turns soft sprite edges into smooth, multisampled outlines
    const lit = sectorLit(new THREE.MeshStandardMaterial({ map: tex, alphaTest: 0.35, alphaToCoverage: true, side: THREE.DoubleSide, roughness: 0.9 }));
    const bright = new THREE.MeshStandardMaterial({ map: tex, alphaTest: 0.35, alphaToCoverage: true, side: THREE.DoubleSide, emissive: 0xffffff, emissiveMap: tex, emissiveIntensity: 1.6, color: 0x000000 });
    const ghost = new THREE.MeshBasicMaterial({ map: tex, alphaTest: 0.5, transparent: true, opacity: 0.25, color: 0x202020, depthWrite: false });
    // a canvas copy for the weapon overlay
    let canvas = null;
    const getCanvas = () => {
      if (canvas) return canvas;
      const im = tex.image;
      canvas = document.createElement('canvas'); canvas.width = im.width; canvas.height = im.height;
      const id = new ImageData(new Uint8ClampedArray(im.data.buffer.slice(im.data.byteOffset, im.data.byteOffset + im.data.byteLength)), im.width, im.height);
      canvas.getContext('2d').putImageData(id, 0, 0);
      return canvas;
    };
    this.ai('s', this.wad.lumps[lump].name, p.px, p.w, p.h, false, tex, priority, () => { canvas = null; });
    const s = { w: p.w, h: p.h, left: p.left, top: p.top, lit, bright, ghost, color, getCanvas };
    this.spriteCache.set(lump, s);
    return s;
  }
}

// ---------------------------------------------------------------- level geometry

const SPRITE_LIGHTS = {
  // static light sources: colour boost, intensity, radius
  COLU: [1.0, 0.95, 0.8, 7, 520], TLMP: [0.8, 0.9, 1.0, 8, 560], TLP2: [0.8, 0.9, 1.0, 8, 560],
  CAND: [1.0, 0.6, 0.25, 2.5, 220], CBRA: [1.0, 0.6, 0.25, 5, 380], FCAN: [1.0, 0.5, 0.15, 8, 520],
  TRED: [1.0, 0.25, 0.15, 6, 460], SMRT: [1.0, 0.25, 0.15, 5, 380], TBLU: [0.3, 0.45, 1.0, 6, 460],
  SMBT: [0.3, 0.45, 1.0, 5, 380], TGRN: [0.3, 1.0, 0.35, 6, 460], SMGT: [0.3, 1.0, 0.35, 5, 380],
  POL3: [1.0, 0.55, 0.2, 3, 260],
  // projectiles and impacts
  BAL1: [1.0, 0.45, 0.15, 14, 520], BAL2: [1.0, 0.3, 0.6, 14, 520], BAL7: [0.4, 1.0, 0.3, 14, 520],
  MISL: [1.0, 0.55, 0.2, 16, 600], PLSS: [0.35, 0.55, 1.0, 12, 480], PLSE: [0.35, 0.55, 1.0, 12, 480],
  BFS1: [0.35, 1.0, 0.35, 22, 800], BFE1: [0.35, 1.0, 0.35, 22, 800], APLS: [0.5, 1.0, 0.3, 10, 420],
  APBX: [0.5, 1.0, 0.3, 10, 420], FATB: [1.0, 0.5, 0.15, 14, 520], MANF: [1.0, 0.5, 0.15, 14, 520],
  PUFF: [1.0, 0.85, 0.6, 3.5, 200], BEXP: [1.0, 0.55, 0.2, 18, 700],
};

const mapName = (commercial, epi, map) => commercial ? `MAP${String(map).padStart(2, '0')}` : `E${epi}M${map}`;

function buildLevel(wad, assets, epi, map, scene, commercial) {
  const marker = wad.find(mapName(commercial, epi, map));
  if (marker < 0) throw new Error(`Map ${mapName(commercial, epi, map)} not found`);
  const L = (k) => wad.data(marker + k);
  const vd = L(4), V = [];
  for (let i = 0; i < vd.byteLength / 4; i++) V.push([vd.getInt16(i * 4, true), vd.getInt16(i * 4 + 2, true)]);
  const sd = L(3), sides = [];
  for (let i = 0; i < sd.byteLength / 30; i++) {
    const o = i * 30;
    sides.push({ xoff: sd.getInt16(o, true), yoff: sd.getInt16(o + 2, true), sector: sd.getInt16(o + 28, true) });
  }
  const ld = L(2), lines = [];
  for (let i = 0; i < ld.byteLength / 14; i++) {
    const o = i * 14;
    const r = ld.getUint16(o + 10, true), l = ld.getUint16(o + 12, true);
    lines.push({ v1: ld.getUint16(o, true), v2: ld.getUint16(o + 2, true), flags: ld.getInt16(o + 4, true),
      front: r === 0xffff ? -1 : r, back: l === 0xffff ? -1 : l });
  }
  const secd = L(8), nsec = secd.byteLength / 26;

  // --- floors and ceilings: trace each sector's boundary loops, then triangulate
  const edgesBySector = Array.from({ length: nsec }, () => []);
  for (const ln of lines) {
    const fs = ln.front >= 0 ? sides[ln.front].sector : -1, bs = ln.back >= 0 ? sides[ln.back].sector : -1;
    if (fs === bs) continue; // lines inside one sector do not bound it
    if (fs >= 0) edgesBySector[fs].push([ln.v1, ln.v2]);
    if (bs >= 0) edgesBySector[bs].push([ln.v2, ln.v1]);
  }
  const sectorMeshes = [];
  const group = new THREE.Group(); scene.add(group);
  for (let s = 0; s < nsec; s++) {
    const tris = triangulateSector(edgesBySector[s], V);
    sectorMeshes.push(tris ? makeFlatMeshes(tris, s, group) : null);
  }

  return { V, sides, lines, nsec, sectorMeshes, group, wallMeshes: new Map(), dynWallMeshes: new Map(), dynSec: new Set(), lastKey: '' };
}

function triangulateSector(edges, V) {
  if (edges.length < 3) return null;
  const out = new Map();
  for (const e of edges) { if (!out.has(e[0])) out.set(e[0], []); out.get(e[0]).push(e); }
  const used = new Set(), loops = [];
  for (const start of edges) {
    if (used.has(start)) continue;
    const loop = []; let e = start, guard = 0;
    while (e && !used.has(e) && guard++ < 10000) {
      used.add(e); loop.push(e[0]);
      const cand = (out.get(e[1]) || []).filter((c) => !used.has(c));
      if (!cand.length) { e = null; break; }
      if (cand.length === 1) { e = cand[0]; continue; }
      // several ways out: keep the region on our right by taking the
      // first edge counter-clockwise from the way we came in
      const [ax, ay] = V[e[0]], [bx, by] = V[e[1]];
      const back = Math.atan2(ay - by, ax - bx);
      let best = null, bestA = 1e9;
      for (const c of cand) {
        const [cx, cy] = V[c[1]];
        let a = Math.atan2(cy - by, cx - bx) - back;
        while (a <= 1e-9) a += Math.PI * 2;
        if (a < bestA) { bestA = a; best = c; }
      }
      e = best;
    }
    if (loop.length >= 3 && e === start) loops.push(loop);
    else if (loop.length >= 3) loops.push(loop); // tolerate small gaps in old maps
  }
  if (!loops.length) return null;
  const area = (lp) => { let a = 0; for (let i = 0; i < lp.length; i++) { const [x1, y1] = V[lp[i]], [x2, y2] = V[lp[(i + 1) % lp.length]]; a += x1 * y2 - x2 * y1; } return a / 2; };
  const inside = (pt, lp) => {
    let c = false;
    for (let i = 0, j = lp.length - 1; i < lp.length; j = i++) {
      const [xi, yi] = V[lp[i]], [xj, yj] = V[lp[j]];
      if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) c = !c;
    }
    return c;
  };
  // sector on the right means outer boundaries run clockwise (negative area)
  const outers = loops.filter((l) => area(l) < 0), holes = loops.filter((l) => area(l) >= 0);
  const polys = (outers.length ? outers : loops).map((o) => ({ outer: o, holes: [] }));
  if (outers.length) for (const h of holes) {
    const pt = V[h[0]];
    let best = null, bestA = Infinity;
    for (const p of polys) { const a = Math.abs(area(p.outer)); if (a < bestA && inside(pt, p.outer)) { bestA = a; best = p; } }
    if (best) best.holes.push(h);
  }
  const tris = [];
  for (const p of polys) {
    const flat = [], holeIdx = [];
    for (const i of p.outer) flat.push(V[i][0], V[i][1]);
    for (const h of p.holes) { holeIdx.push(flat.length / 2); for (const i of h) flat.push(V[i][0], V[i][1]); }
    const idx = earcut(flat, holeIdx);
    for (let i = 0; i < idx.length; i += 3) tris.push([flat[idx[i] * 2], flat[idx[i] * 2 + 1]], [flat[idx[i + 1] * 2], flat[idx[i + 1] * 2 + 1]], [flat[idx[i + 2] * 2], flat[idx[i + 2] * 2 + 1]]);
  }
  return tris;
}

// Split big triangles until no edge is longer than `max` units. Huge triangles
// that reach behind the camera lose precision on some GPUs (flat smears).
function subdivide(tris, max) {
  let out = tris;
  for (let pass = 0; pass < 8; pass++) {
    const next = []; let split = false;
    for (let i = 0; i < out.length; i += 3) {
      const a = out[i], b = out[i + 1], c = out[i + 2];
      const big = Math.max(Math.hypot(a[0] - b[0], a[1] - b[1]), Math.hypot(b[0] - c[0], b[1] - c[1]), Math.hypot(c[0] - a[0], c[1] - a[1])) > max;
      if (!big) { next.push(a, b, c); continue; }
      split = true;
      const m = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
      const ab = m(a, b), bc = m(b, c), ca = m(c, a);
      next.push(a, ab, ca, ab, b, bc, ca, bc, c, ab, bc, ca);
    }
    out = next;
    if (!split) break;
  }
  return out;
}

function makeFlatMeshes(tris, s, group) {
  tris = subdivide(tris, 192);
  const make = (up) => {
    const pos = [], uv = [], nrm = [], sec = [];
    for (let i = 0; i < tris.length; i += 3) {
      let t = [tris[i], tris[i + 1], tris[i + 2]];
      // three.js x = doom x, z = -doom y; wind so the face points the right way
      const cross = (t[1][0] - t[0][0]) * (t[2][1] - t[0][1]) - (t[1][1] - t[0][1]) * (t[2][0] - t[0][0]);
      if ((cross > 0) !== up) t = [t[0], t[2], t[1]];
      for (const [x, y] of t) { pos.push(x, 0, -y); uv.push(x / 64, -y / 64); nrm.push(0, up ? 1 : -1, 0); sec.push(s); }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setAttribute('normal', new THREE.Float32BufferAttribute(nrm, 3));
    g.setAttribute('aSector', new THREE.Float32BufferAttribute(sec, 1));
    const m = new THREE.Mesh(g); m.visible = false; m.receiveShadow = true; m.castShadow = true;
    group.add(m);
    return m;
  };
  const fl = make(true), ce = make(false);
  fl.userData = { kind: 'floor', sector: s }; ce.userData = { kind: 'ceiling', sector: s };
  return { floor: fl, ceil: ce, floorPic: -1, ceilPic: -1 };
}

// Walls depend on live sector heights and switch textures, so they are
// rebuilt whenever any of those change, batched into one mesh per texture.
// Walls are built in two layers. Lines touching a sector that has ever moved (doors,
// lifts, crushers, switches) go in the small "moving" layer, which is the only one
// rebuilt while something moves; everything else is built once.
function buildWalls(lv, assets, H, sideTex, layer) {
  const meshes = layer === 'moving' ? lv.dynWallMeshes : lv.wallMeshes;
  const dyn = lv.dynSec;
  const batches = new Map();
  const add = (tex, sector, x1, y1, x2, y2, zb, zt, u0, u1, vTop, h) => {
    if (zt - zb < 0.01) return;
    let b = batches.get(tex); if (!b) batches.set(tex, (b = { pos: [], uv: [], nrm: [], sec: [] }));
    const nx = y2 - y1, ny = -(x2 - x1), l = Math.hypot(nx, ny) || 1; // doom-space right normal
    const n3 = [nx / l, 0, -ny / l];
    // split long or tall walls into pieces of at most 256 units (see subdivide)
    const len = Math.hypot(x2 - x1, y2 - y1), nx_ = Math.max(1, Math.ceil(len / 256)), ny_ = Math.max(1, Math.ceil((zt - zb) / 256));
    for (let i = 0; i < nx_; i++) {
      const f0 = i / nx_, f1 = (i + 1) / nx_;
      const ax = x1 + (x2 - x1) * f0, ay = y1 + (y2 - y1) * f0, bx = x1 + (x2 - x1) * f1, by = y1 + (y2 - y1) * f1;
      const ua = u0 + (u1 - u0) * f0, ub = u0 + (u1 - u0) * f1;
      for (let j = 0; j < ny_; j++) {
        const z0 = zb + (zt - zb) * j / ny_, z1 = zb + (zt - zb) * (j + 1) / ny_;
        const v0 = (vTop - z0) / h, v1 = (vTop - z1) / h;
        const P = [[ax, z0, -ay, ua, v0], [bx, z0, -by, ub, v0], [bx, z1, -by, ub, v1], [ax, z1, -ay, ua, v1]];
        for (const k of [0, 1, 2, 0, 2, 3]) { const p = P[k]; b.pos.push(p[0], p[1], p[2]); b.uv.push(p[3], p[4]); b.nrm.push(...n3); b.sec.push(sector); }
      }
    }
  };
  const sky = assets.skyflat;
  for (const ln of lv.lines) {
    const moving = (ln.front >= 0 && dyn.has(lv.sides[ln.front].sector)) || (ln.back >= 0 && dyn.has(lv.sides[ln.back].sector));
    if (moving !== (layer === 'moving')) continue;
    for (const side of [0, 1]) {
      const si = side === 0 ? ln.front : ln.back; if (si < 0) continue;
      const oi = side === 0 ? ln.back : ln.front;
      const sd = lv.sides[si], fsec = sd.sector, bsec = oi >= 0 ? lv.sides[oi].sector : -1;
      let [x1, y1] = lv.V[ln.v1], [x2, y2] = lv.V[ln.v2];
      if (side === 1) [x1, y1, x2, y2] = [x2, y2, x1, y1];
      const len = Math.hypot(x2 - x1, y2 - y1);
      const ff = H[fsec * 5], fc = H[fsec * 5 + 1];
      const [top, bottom, mid, xoff] = [sideTex[si * 4], sideTex[si * 4 + 1], sideTex[si * 4 + 2], sideTex[si * 4 + 3]];
      const yoff = sd.yoff;
      const U = (t) => [xoff / assets.wallMaterial(t).w, (xoff + len) / assets.wallMaterial(t).w];
      if (bsec < 0) {
        if (!mid) continue;
        const t = assets.wallMaterial(mid);
        const vTop = (ln.flags & ML_DONTPEGBOTTOM) ? ff + t.h : fc;
        add(mid, fsec, x1, y1, x2, y2, ff, fc, ...U(mid), vTop + yoff, t.h);
        continue;
      }
      const bf = H[bsec * 5], bc = H[bsec * 5 + 1];
      const fsky = H[fsec * 5 + 4] === sky, bsky = H[bsec * 5 + 4] === sky;
      if (bc < fc && top && !(fsky && bsky)) {
        const t = assets.wallMaterial(top);
        const vTop = (ln.flags & ML_DONTPEGTOP) ? fc : bc + t.h;
        add(top, fsec, x1, y1, x2, y2, bc, fc, ...U(top), vTop + yoff, t.h);
      }
      if (bf > ff && bottom) {
        const t = assets.wallMaterial(bottom);
        const vTop = (ln.flags & ML_DONTPEGBOTTOM) ? fc : bf;
        add(bottom, fsec, x1, y1, x2, y2, ff, bf, ...U(bottom), vTop + yoff, t.h);
      }
      if (mid) {
        const t = assets.wallMaterial(mid);
        const lo = Math.max(ff, bf), hi = Math.min(fc, bc);
        const vTop = ((ln.flags & ML_DONTPEGBOTTOM) ? lo + t.h : hi) + yoff;
        const zt = Math.min(hi, vTop), zb = Math.max(lo, vTop - t.h);
        add(mid, fsec, x1, y1, x2, y2, zb, zt, ...U(mid), vTop, t.h);
      }
    }
  }
  for (const [tex, m] of meshes) if (!batches.has(tex)) { lv.group.remove(m); m.geometry.dispose(); meshes.delete(tex); }
  for (const [tex, b] of batches) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(b.pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(b.uv, 2));
    g.setAttribute('normal', new THREE.Float32BufferAttribute(b.nrm, 3));
    g.setAttribute('aSector', new THREE.Float32BufferAttribute(b.sec, 1));
    let m = meshes.get(tex);
    if (m) { m.geometry.dispose(); m.geometry = g; }
    else { m = new THREE.Mesh(g, assets.wallMaterial(tex).mat); m.castShadow = m.receiveShadow = true; m.userData = { kind: 'wall', tex: assets.textures[tex]?.name }; meshes.set(tex, m); lv.group.add(m); }
  }
}

// ---------------------------------------------------------------- engine

async function startEngine(wasmBytes, wadBytes, epi, map, skill, commercial, relitImports) {
  let log = [];
  const fds = [new OpenFile(new File([])),
    ConsoleStdout.lineBuffered((m) => { log.push(m); if (log.length > 60) log.shift(); }),
    ConsoleStdout.lineBuffered((m) => { log.push(m); }),
    new PreopenDirectory('.', new Map([[commercial ? 'doom2.wad' : 'doom.wad', new File(wadBytes)]])),
    new PreopenDirectory('/tmp', new Map())]; // the music player writes each song here as MIDI
  const wasi = new WASI(['doom'], [], fds, { debug: false });
  const { instance } = await WebAssembly.instantiate(wasmBytes, { wasi_snapshot_preview1: wasi.wasiImport, relit: relitImports });
  wasi.initialize(instance);
  const e = instance.exports;
  e.dgx_start(epi, map, skill, commercial ? 1 : 0);
  const mem = () => e.memory.buffer;
  const str = (p) => { const b = new Uint8Array(mem(), p, Math.min(128, mem().byteLength - p)); let s = ''; for (const c of b) { if (!c) break; s += String.fromCharCode(c); } return s; };
  const sprnames = []; for (let i = 0; i < e.dgx_numsprites(); i++) sprnames.push(str(e.dgx_sprname(i)));
  return {
    e, log, sprnames, str,
    info: () => { const p = e.dgx_info(); return Int32Array.from(new Int32Array(mem(), p, 8)); },
    player: () => { const p = e.dgx_player(); return Float32Array.from(new Float32Array(mem(), p, 48)); },
    sectors: (n) => { const p = e.dgx_sectors(); return Float32Array.from(new Float32Array(mem(), p, n * 5)); },
    sides: (n) => { const p = e.dgx_sides(); return Int32Array.from(new Int32Array(mem(), p, n * 4)); },
    things: () => { const p = e.dgx_things(); return Int32Array.from(new Int32Array(mem(), p, e.dgx_thing_count() * 12)); },
    screen: () => { const p = e.dgx_screen(); return new Uint8Array(mem(), p, 640 * 400 * 4); },
  };
}

// ---------------------------------------------------------------- the game

const KEY = { up: 0xad, down: 0xaf, left: 0xac, right: 0xae, sl: 0xa0, sr: 0xa1, use: 0xa2, fire: 0xa3, run: 0x80 + 0x36, strafe: 0x80 + 0x38 };
function doomKey(ev) {
  switch (ev.code) {
    case 'KeyW': case 'ArrowUp': return KEY.up;
    case 'KeyS': case 'ArrowDown': return KEY.down;
    case 'KeyA': return KEY.sl;
    case 'KeyD': return KEY.sr;
    case 'ArrowLeft': return KEY.left;
    case 'ArrowRight': return KEY.right;
    case 'Space': case 'KeyE': return KEY.use;
    case 'ControlLeft': case 'ControlRight': case 'KeyF': return KEY.fire;
    case 'ShiftLeft': case 'ShiftRight': return KEY.run;
    case 'AltLeft': case 'AltRight': return KEY.strafe;
    case 'Escape': return 27;
    case 'Enter': case 'NumpadEnter': return 13;
    case 'Tab': return 9;
    case 'Backspace': return 0x7f;
    case 'Minus': return 0x2d;
    case 'Equal': return 0x3d;
    case 'Pause': return 0xff;
  }
  if (/^F([1-9]|1[0-2])$/.test(ev.key)) { const n = +ev.key.slice(1); return n <= 10 ? 0x80 + 0x3a + n : 0x80 + 0x4c + n; }
  if (ev.key.length === 1) { const c = ev.key.toLowerCase().charCodeAt(0); if (c >= 32 && c < 127) return c; }
  return 0;
}

export async function run(opts) {
  const { canvas, fbCanvas, hud, status, onProgress } = opts;

  // --- the player's game file, read in their own browser
  onProgress('Reading your game file…', 0.3);
  const wadBytes = opts.wadBytes;
  const magic = String.fromCharCode(...wadBytes.subarray(0, 4));
  if (magic !== 'IWAD') throw new Error(magic === 'PWAD'
    ? 'That is a mod (PWAD), not a full game file. Pick freedoom1.wad, freedoom2.wad or your own doom.wad / doom2.wad'
    : 'That file is not a Doom game file (.wad)');
  const wad = new Wad(wadBytes.buffer.slice(wadBytes.byteOffset, wadBytes.byteOffset + wadBytes.byteLength));
  const commercial = wad.find('MAP01') >= 0 && wad.find('E1M1') < 0;
  if (!commercial && wad.find('E1M1') < 0) throw new Error('No playable maps found in that file');
  const assets = new Assets(wad);
  // AI remaster: upscales art on this computer's GPU and caches it in the browser
  let remaster = null;
  if (opts.aiBytes && opts.aiEnabled !== false) {
    const up = Upscaler.create(opts.aiBytes);
    if (up) {
      remaster = new Remaster(up, wadFingerprint(wadBytes), opts.onAI);
      assets.ai = (...a) => remaster.request(...a);
    }
  }
  onProgress('Starting the engine…', 0.8);
  const audio = new GameAudio(wad);
  const eng = await startEngine(opts.wasmBytes, wadBytes, 1, opts.map || 1, opts.skill, commercial, audio.imports());

  // --- three.js setup
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  const coarse = matchMedia('(pointer:coarse)').matches;
  renderer.setPixelRatio(Math.min(devicePixelRatio, coarse ? 1.5 : 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.35;
  maxAniso = renderer.capabilities.getMaxAnisotropy();
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x07060a, 0.0006);
  scene.background = new THREE.Color(0x07060a);
  const camera = new THREE.PerspectiveCamera(72, 1, 1, 8000);
  camera.rotation.order = 'YXZ';
  camera.layers.enable(1); // layer 1: sprites and sky, which skip ambient occlusion
  const aoCamera = new THREE.PerspectiveCamera(72, 1, 1, 8000);
  // post-processing runs in multisampled buffers so edges stay smooth
  const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: coarse ? 0 : 4 }));
  composer.addPass(new RenderPass(scene, camera));
  let gtao = null;
  if (!coarse && !opts.noAO) {
    // contact shadows in corners and along floors
    gtao = new GTAOPass(scene, aoCamera, 512, 512);
    gtao.updateGtaoMaterial({ radius: 48, distanceExponent: 1.5, thickness: 6, scale: 1.2, samples: 12 });
    gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 12 });
    gtao.blendIntensity = 0.9;
    composer.addPass(gtao);
  }
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.45, 0.3, 0.85);
  composer.addPass(bloom);
  const grade = new ShaderPass({
    uniforms: { tDiffuse: { value: null }, uTexel: { value: new THREE.Vector2(1 / 1024, 1 / 1024) }, uSharp: { value: 0.55 },
      uHurt: { value: 0 }, uBonus: { value: 0 }, uSuit: { value: 0 } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: `uniform sampler2D tDiffuse; uniform vec2 uTexel; uniform float uSharp, uHurt, uBonus, uSuit; varying vec2 vUv;
      void main(){
        vec2 d = vUv - 0.5;
        vec3 c = texture2D(tDiffuse, vUv).rgb;
        // contrast-adaptive sharpening: crisper detail without halos
        vec3 n = texture2D(tDiffuse, vUv + vec2(0.0, uTexel.y)).rgb, s = texture2D(tDiffuse, vUv - vec2(0.0, uTexel.y)).rgb;
        vec3 e = texture2D(tDiffuse, vUv + vec2(uTexel.x, 0.0)).rgb, w = texture2D(tDiffuse, vUv - vec2(uTexel.x, 0.0)).rgb;
        vec3 mn = min(c, min(min(n, s), min(e, w))), mx = max(c, max(max(n, s), max(e, w)));
        vec3 amt = sqrt(clamp(min(mn, 2.0 - mx) / max(mx, 1e-4), 0.0, 1.0)) * uSharp;
        c = max(vec3(0.0), c + (4.0 * c - n - s - e - w) * amt * 0.25);
        // colour fringing only when hurt
        if (uHurt > 0.0) {
          float ab = uHurt * 0.006;
          c = mix(c, vec3(texture2D(tDiffuse, vUv + d*ab).r, c.g, texture2D(tDiffuse, vUv - d*ab).b), uHurt);
        }
        c *= 1.0 - dot(d, d) * 0.55;                      // light vignette
        c = mix(c, vec3(1.0, 0.05, 0.02) * (0.4 + c.r), uHurt * 0.55 * smoothstep(0.1, 0.75, length(d) + 0.25));
        c = mix(c, c + vec3(0.45, 0.38, 0.05), uBonus);
        c = mix(c, c * vec3(0.4, 1.0, 0.4), uSuit * 0.5);
        gl_FragColor = vec4(c, 1.0);
      }`,
  });
  composer.addPass(grade);
  composer.addPass(new OutputPass());

  // sector light texture
  let sectorData = new Float32Array(1), sectorTex = null;
  const setSectorCount = (n) => {
    sectorData = new Float32Array(n);
    sectorTex = new THREE.DataTexture(sectorData, n, 1, THREE.RedFormat, THREE.FloatType);
    sectorTex.magFilter = sectorTex.minFilter = THREE.NearestFilter; sectorTex.needsUpdate = true;
    shared.uSectorTex.value = sectorTex; shared.uSectorCount.value = n;
  };
  setSectorCount(1);

  // dynamic lights: a fixed pool, given each frame to the nearest emitters
  const LIGHTS = coarse ? 8 : 14;
  const lights = [];
  for (let i = 0; i < LIGHTS; i++) { const l = new THREE.PointLight(0xffffff, 0, 400, 1.6); scene.add(l); lights.push(l); }
  const muzzle = new THREE.PointLight(0xffc27a, 0, 900, 1.4); scene.add(muzzle);
  const fill = new THREE.PointLight(0xc8d2ff, 0, 260, 1.8); scene.add(fill); // faint lantern so dark rooms keep shape
  // flashlight: the one light that casts real shadows, from walls and monsters alike
  const torch = new THREE.SpotLight(0xfff1dc, 0, 1400, 0.72, 0.9, 1.45); // wide, very soft edge
  torch.castShadow = !coarse; // shadows cost a second pass; phones skip them
  torch.shadow.mapSize.set(coarse ? 512 : 1024, coarse ? 512 : 1024);
  torch.shadow.camera.near = 4; torch.shadow.camera.far = 1400;
  torch.shadow.bias = -0.0004; torch.shadow.normalBias = 0.6;
  torch.shadow.camera.layers.enable(1);
  scene.add(torch, torch.target);
  let torchOn = true;
  const effects = new Effects(scene, coarse);

  // sky: a cylinder around the camera, wrapped with the level's sky texture
  let skyMesh = null;
  const setSky = (epi, map) => {
    const name = commercial ? (map < 12 ? 'SKY1' : map < 21 ? 'SKY2' : 'SKY3') : `SKY${epi}`;
    let ti = assets.textures.findIndex((t) => t.name === name);
    if (ti < 0) ti = assets.textures.findIndex((t) => t.name === 'SKY1');
    if (skyMesh) { scene.remove(skyMesh); skyMesh.geometry.dispose(); skyMesh.material.map.dispose(); skyMesh.material.dispose(); skyMesh = null; }
    if (ti < 0) return;
    const img = assets.textureImage(ti), up = upscale(img.px, img.w, img.h, UPSCALE);
    const tex = makeTex(new Uint8Array(up.px.buffer), up.w, up.h, true); tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.repeat.set(4, 1);
    assets.ai('k', name, img.px, img.w, img.h, true, tex, 2);
    // a full dome: the picture covers a band above the horizon and its top
    // row stretches up to the zenith, so looking straight up still shows sky
    const geo = new THREE.SphereGeometry(3000, 64, 32);
    const pos = geo.attributes.position, uv = geo.attributes.uv;
    const latMin = -0.35, latMax = 0.95; // radians
    for (let i = 0; i < pos.count; i++) {
      const lat = Math.asin(Math.max(-1, Math.min(1, pos.getY(i) / 3000)));
      uv.setY(i, Math.min(0.995, Math.max(0.005, (latMax - lat) / (latMax - latMin))));
    }
    const mat = new THREE.MeshBasicMaterial({ map: tex, side: THREE.BackSide, fog: false, depthWrite: false, color: 0xbbbbbb });
    skyMesh = new THREE.Mesh(geo, mat); skyMesh.renderOrder = -10; skyMesh.layers.set(1); scene.add(skyMesh);
    const top = img.px[2 * img.w + 5];
    scene.background = new THREE.Color(((top & 255) / 255) * 0.6, (((top >> 8) & 255) / 255) * 0.6, (((top >> 16) & 255) / 255) * 0.6).convertSRGBToLinear();
  };

  // sprites: one small mesh per visible thing, reused across frames
  const spriteMeshes = new Map();
  const spriteMesh = (id) => {
    let m = spriteMeshes.get(id);
    if (m) return m;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(12), 3));
    g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(8), 2));
    g.setAttribute('normal', new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]), 3));
    g.setAttribute('aSector', new THREE.BufferAttribute(new Float32Array(4), 1));
    g.setIndex([0, 1, 2, 0, 2, 3]);
    m = new THREE.Mesh(g); m.frustumCulled = false; m.layers.set(1); m.castShadow = true; m.receiveShadow = true; m.userData = { kind: 'sprite', id }; scene.add(m);
    spriteMeshes.set(id, m);
    return m;
  };

  // --- state snapshots for smooth 60+ fps between the engine's 35 Hz tics
  let lv = null, levelKey = '', nsec = 0, nsides = 0;
  let prev = null, cur = null, ticTime = performance.now();
  let pendingTurn = 0, pitch = 0;
  const TURN_PER_PX = (Math.PI * 2) / 8192; // engine: angleturn -= dx*8, in 1/65536 turns

  const snapshot = () => {
    const info = eng.info();
    const s = { info, inLevel: (info[0] & 1) === 1, overlay: (info[0] & 6) !== 0, player: eng.player() };
    if (s.inLevel) {
      nsec = info[5]; nsides = info[6];
      s.sectors = eng.sectors(nsec);
      s.sides = eng.sides(nsides);
      const t = eng.things(), map = new Map();
      for (let i = 0; i < t.length; i += 12) map.set(t[i], t.subarray(i, i + 12));
      s.things = map;
    }
    return s;
  };

  const ensureLevel = (s) => {
    const key = `${s.info[1]}-${s.info[2]}`;
    if (key === levelKey) return;
    levelKey = key;
    if (lv) { scene.remove(lv.group); lv.group.traverse((o) => o.geometry && o.geometry.dispose()); }
    for (const m of spriteMeshes.values()) { scene.remove(m); m.geometry.dispose(); }
    spriteMeshes.clear();
    lv = buildLevel(wad, assets, s.info[1], s.info[2], scene, commercial);
    setSky(s.info[1], s.info[2]);
    setSectorCount(lv.nsec);
    lv.builtH = null; lv.builtSides = null;
  };

  // --- input
  let mode = 'remaster', locked = false;
  const held = new Set();
  const keyEvent = (down, k) => { if (k) eng.e.dgx_key(down ? 1 : 0, k); };
  const onKey = (down) => (ev) => {
    if (ev.code === 'KeyV' && down && !ev.repeat) { toggleMode(); ev.preventDefault(); return; }
    if (ev.code === 'KeyL' && down && !ev.repeat) { torchOn = !torchOn; opts.onTorch?.(torchOn); ev.preventDefault(); return; }
    const k = doomKey(ev); if (!k) return;
    ev.preventDefault();
    if (down) { if (held.has(k)) return; held.add(k); } else held.delete(k);
    keyEvent(down, k);
  };
  addEventListener('keydown', onKey(true));
  addEventListener('keyup', onKey(false));
  addEventListener('blur', () => { for (const k of held) keyEvent(false, k); held.clear(); });

  let mouseDx = 0, mouseButtons = 0;
  const stage = opts.stage;
  stage.addEventListener('click', () => { if (!coarse && document.pointerLockElement !== stage) stage.requestPointerLock?.(); });
  document.addEventListener('pointerlockchange', () => { locked = document.pointerLockElement === stage; opts.onLock?.(locked); });
  addEventListener('mousemove', (ev) => {
    if (!locked) return;
    mouseDx += ev.movementX;
    pitch = Math.max(-1.1, Math.min(1.1, pitch - ev.movementY * 0.0022));
  });
  stage.addEventListener('mousedown', (ev) => {
    if (!locked) return; ev.preventDefault();
    if (ev.button === 0) mouseButtons |= 1;
    if (ev.button === 2) keyEvent(true, KEY.use);
  });
  addEventListener('mouseup', (ev) => {
    if (ev.button === 0) mouseButtons &= ~1;
    if (ev.button === 2) keyEvent(false, KEY.use);
  });
  stage.addEventListener('contextmenu', (ev) => ev.preventDefault());
  stage.addEventListener('wheel', (ev) => {
    if (!locked) return; ev.preventDefault();
    const p = cur?.player; if (!p) return;
    const w = (p[8] + (ev.deltaY > 0 ? 1 : 6)) % 7; keyEvent(true, 49 + w); keyEvent(false, 49 + w);
  }, { passive: false });

  // touch: left half is a move stick, right half drags to look
  if (coarse) {
    let stick = null, look = null; const stickKeys = new Set();
    const setStick = (want) => {
      for (const k of [KEY.up, KEY.down, KEY.sl, KEY.sr]) {
        const on = want.has(k);
        if (on && !stickKeys.has(k)) { stickKeys.add(k); keyEvent(true, k); }
        if (!on && stickKeys.has(k)) { stickKeys.delete(k); keyEvent(false, k); }
      }
    };
    stage.addEventListener('pointerdown', (ev) => {
      if (ev.pointerType !== 'touch') return;
      const r = stage.getBoundingClientRect();
      if (ev.clientX < r.left + r.width / 2 && !stick) stick = { id: ev.pointerId, x: ev.clientX, y: ev.clientY };
      else if (!look) look = { id: ev.pointerId, x: ev.clientX, y: ev.clientY };
      stage.setPointerCapture(ev.pointerId);
    });
    stage.addEventListener('pointermove', (ev) => {
      if (stick && ev.pointerId === stick.id) {
        const dx = ev.clientX - stick.x, dy = ev.clientY - stick.y, want = new Set();
        if (dy < -18) want.add(KEY.up); if (dy > 18) want.add(KEY.down);
        if (dx < -22) want.add(KEY.sl); if (dx > 22) want.add(KEY.sr);
        setStick(want);
      } else if (look && ev.pointerId === look.id) {
        mouseDx += (ev.clientX - look.x) * 2.2;
        pitch = Math.max(-1.1, Math.min(1.1, pitch - (ev.clientY - look.y) * 0.006));
        look.x = ev.clientX; look.y = ev.clientY;
      }
    });
    const end = (ev) => {
      if (stick && ev.pointerId === stick.id) { stick = null; setStick(new Set()); }
      if (look && ev.pointerId === look.id) look = null;
    };
    stage.addEventListener('pointerup', end); stage.addEventListener('pointercancel', end);
  }
  opts.buttons?.forEach((b) => {
    const name = b.dataset.k;
    if (name === 'next') {
      b.addEventListener('pointerdown', (ev) => {
        ev.preventDefault(); ev.stopPropagation();
        const p = cur?.player; if (!p) return;
        const w = (p[8] + 1) % 7; keyEvent(true, 49 + w); keyEvent(false, 49 + w);
      });
      return;
    }
    const k = name === 'fire' ? KEY.fire : name === 'use' ? KEY.use : +name;
    const on = (ev) => { ev.preventDefault(); ev.stopPropagation(); b.classList.add('on'); keyEvent(true, k); };
    const off = (ev) => { ev.preventDefault(); if (!b.classList.contains('on')) return; b.classList.remove('on'); keyEvent(false, k); };
    b.addEventListener('pointerdown', on); ['pointerup', 'pointercancel', 'pointerleave'].forEach((n) => b.addEventListener(n, off));
  });

  const toggleMode = () => {
    mode = mode === 'remaster' ? '1993' : 'remaster';
    opts.onMode?.(mode);
  };
  opts.toggle?.addEventListener('click', (ev) => { ev.stopPropagation(); toggleMode(); });

  // --- 1993 framebuffer view
  const fbCtx = fbCanvas.getContext('2d'); const fbImg = fbCtx.createImageData(640, 400);
  const drawFramebuffer = () => {
    const src = eng.screen(), dst = fbImg.data;
    for (let i = 0; i < dst.length; i += 4) { dst[i] = src[i + 2]; dst[i + 1] = src[i + 1]; dst[i + 2] = src[i]; dst[i + 3] = 255; }
    fbCtx.putImageData(fbImg, 0, 0);
  };

  // --- weapon overlay and HUD
  const wctx = opts.weaponCanvas.getContext('2d');
  let lastMsgCount = eng.e.dgx_message_count(), msgTimer = 0;

  const resize = () => {
    const r = stage.getBoundingClientRect();
    renderer.setSize(r.width, r.height, false); composer.setSize(r.width, r.height);
    grade.uniforms.uTexel.value.set(1 / (r.width * renderer.getPixelRatio()), 1 / (r.height * renderer.getPixelRatio()));
    camera.aspect = r.width / r.height;
    camera.fov = camera.aspect < 1 ? 85 : 72;
    camera.updateProjectionMatrix();
    aoCamera.fov = camera.fov; aoCamera.aspect = camera.aspect; aoCamera.updateProjectionMatrix();
    effects.setScale((r.height * renderer.getPixelRatio()) / 2 / Math.tan((camera.fov * Math.PI) / 360));
    const pr = renderer.getPixelRatio();
    opts.weaponCanvas.width = Math.round(r.width * pr); opts.weaponCanvas.height = Math.round(r.height * pr);
  };
  new ResizeObserver(resize).observe(stage); resize();

  const lerp = (a, b, t) => a + (b - a) * t;
  const lerpAng = (a, b, t) => { let d = b - a; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; return a + d * t; };
  const flickers = new Map();
  // weapon feel: sway follows turning, recoil kicks on each shot
  let lastAng = null, lastPitch = 0, swayX = 0, swayY = 0, kick = 0, lastFlash = false, lastDamage = 0, lastNow = performance.now();
  let fps = 0, frames = 0, fpsT = performance.now();

  let quit = false;
  const frame = (now) => {
    if (quit) return;
    requestAnimationFrame(frame);
    remaster?.pump();
    // feed input and step the engine when a tic is due
    if (mouseDx || mouseButtons !== (frame.lastButtons || 0)) {
      const dx = Math.round(mouseDx); mouseDx -= dx; pendingTurn += dx;
      eng.e.dgx_mouse(dx, mouseButtons); frame.lastButtons = mouseButtons;
    }
    let ran = false;
    try { ran = eng.e.dgx_frame() === 1; } catch (err) {
      // Quit Game from Doom's menu ends the engine with exit code 0
      if (err && err.code === 0 && /exit/.test(err.message)) { quit = true; opts.onQuit?.(); return; }
      opts.onCrash(err, eng.log); throw err;
    }
    if (ran || !cur) {
      prev = cur; cur = snapshot(); ticTime = now; pendingTurn = 0;
      if (cur.inLevel) ensureLevel(cur);
      const mc = eng.e.dgx_message_count();
      if (mc !== lastMsgCount) { lastMsgCount = mc; opts.onMessage(eng.str(eng.e.dgx_message())); msgTimer = now; }
    }
    if (!prev) prev = cur;
    const t = Math.min(1, (now - ticTime) / TIC_MS);
    const showFB = mode === '1993' || !cur.inLevel || cur.overlay;
    opts.onView(showFB ? 'fb' : '3d', cur);
    if (showFB) { drawFramebuffer(); audio.frame(camera, 128, false, eng); return; }
    if (!prev.inLevel || prev.info[2] !== cur.info[2]) prev = cur;

    // --- interpolated world state
    const P = cur.player, Pp = prev.player;
    const H = new Float32Array(cur.sectors.length);
    for (let i = 0; i < H.length; i++) H[i] = (i % 5 < 2) ? lerp(prev.sectors[i] ?? cur.sectors[i], cur.sectors[i], t) : cur.sectors[i];
    for (let s = 0; s < nsec; s++) sectorData[s] = lightCurve(H[s * 5 + 2]);
    sectorTex.needsUpdate = true;

    // walls: rebuild only when heights or textures moved, and then only the moving layer
    // (a sector joins it the first time it moves, which rebuilds both layers once)
    const first = !lv.builtH || !lv.builtSides;
    let moved = false, grew = false;
    if (!first) {
      for (let i = 0; i < H.length; i++) {
        if (i % 5 !== 2 && H[i] !== lv.builtH[i]) {
          moved = true;
          const s = (i / 5) | 0;
          if (!lv.dynSec.has(s)) { lv.dynSec.add(s); grew = true; }
        }
      }
      const sd = cur.sides;
      for (let i = 0; i < sd.length; i++) {
        if (sd[i] !== lv.builtSides[i]) {
          moved = true;
          const s = lv.sides[(i / 4) | 0].sector;
          if (!lv.dynSec.has(s)) { lv.dynSec.add(s); grew = true; }
        }
      }
    }
    if (first || grew) buildWalls(lv, assets, H, cur.sides, 'still');
    if (first || moved) { buildWalls(lv, assets, H, cur.sides, 'moving'); lv.builtH = H; lv.builtSides = cur.sides; }

    // floors and ceilings
    for (let s = 0; s < nsec; s++) {
      const fm = lv.sectorMeshes[s]; if (!fm) continue;
      const fp = H[s * 5 + 3], cp = H[s * 5 + 4];
      fm.floor.position.y = H[s * 5]; fm.ceil.position.y = H[s * 5 + 1];
      if (fp !== fm.floorPic) { fm.floorPic = fp; fm.floor.visible = fp !== assets.skyflat; if (fm.floor.visible) fm.floor.material = assets.flatMaterial(fp).mat; }
      if (cp !== fm.ceilPic) { fm.ceilPic = cp; fm.ceil.visible = cp !== assets.skyflat; if (fm.ceil.visible) fm.ceil.material = assets.flatMaterial(cp).mat; }
    }

    // camera: engine position, plus turning the engine hasn't seen yet
    const ang = lerpAng(Pp[4], P[4], t) - pendingTurn * TURN_PER_PX;
    const cx = lerp(Pp[0], P[0], t), cy = lerp(Pp[1], P[1], t), cz = lerp(Pp[3], P[3], t);
    camera.position.set(cx, cz, -cy);
    camera.rotation.set(pitch, ang - Math.PI / 2, 0);
    const dt = Math.min(0.1, Math.max(0.001, (now - lastNow) / 1000)); lastNow = now;
    if (lastAng !== null) {
      let d = ang - lastAng; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI;
      const k = Math.min(1, dt * 9);
      swayX += (Math.max(-1, Math.min(1, (d / dt) * 0.12)) - swayX) * k;
      swayY += (Math.max(-1, Math.min(1, ((pitch - lastPitch) / dt) * 0.12)) - swayY) * k;
    }
    lastAng = ang; lastPitch = pitch;
    const flashNow = P[17] >= 0;
    if (flashNow && !lastFlash) kick = 1;
    lastFlash = flashNow; kick = Math.max(0, kick - dt * 7);
    if (P[10] > lastDamage) effects.shake = Math.max(effects.shake, 0.3);
    lastDamage = P[10];
    camera.rotation.x += kick * 0.014;
    if (effects.shake > 0) {
      const sh = effects.shake;
      camera.rotation.x += (Math.random() - 0.5) * sh * 0.03; camera.rotation.y += (Math.random() - 0.5) * sh * 0.03;
      camera.position.x += (Math.random() - 0.5) * sh * 3; camera.position.y += (Math.random() - 0.5) * sh * 3;
    }
    if (skyMesh) skyMesh.position.set(cx, cz, -cy);

    // --- things
    const emitters = [];
    const seen = new Set();
    const yaw = ang - Math.PI / 2;
    const rx = Math.cos(yaw), rz = -Math.sin(yaw); // camera right, three.js space
    for (const [id, th] of cur.things) {
      const pv = prev.things?.get(id);
      let x = th[1] / 65536, y = th[2] / 65536, z = th[3] / 65536;
      if (pv) {
        const px = pv[1] / 65536, py = pv[2] / 65536;
        if (Math.abs(px - x) + Math.abs(py - y) < 96) { x = lerp(px, x, t); y = lerp(py, y, t); z = lerp(pv[3] / 65536, z, t); }
      }
      const name = eng.sprnames[th[5]], table = assets.spriteTable(name);
      const fr = table[th[6] & FF_FRAMEMASK]; if (!fr) continue;
      const tang = (th[4] >>> 0) * (Math.PI * 2 / 4294967296);
      const a = Math.atan2(y - cy, x - cx);
      let rot = Math.floor((((a - tang + Math.PI * 9 / 8) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2) / (Math.PI / 4)) & 7;
      const pick = fr[rot] || fr[0]; if (!pick) continue;
      const sp = assets.sprite(pick.lump);
      const m = spriteMesh(id); seen.add(id); m.visible = true;
      const bright = (th[6] & FF_FULLBRIGHT) !== 0, shadow = (th[7] & MF_SHADOW) !== 0;
      m.material = shadow ? sp.ghost : bright ? sp.bright : sp.lit;
      const pos = m.geometry.attributes.position.array, uv = m.geometry.attributes.uv.array;
      const l = -sp.left, r = sp.w - sp.left, top = sp.top, bot = sp.top - sp.h;
      const corners = [[l, bot], [r, bot], [r, top], [l, top]];
      for (let k = 0; k < 4; k++) { pos[k * 3] = rx * corners[k][0]; pos[k * 3 + 1] = corners[k][1]; pos[k * 3 + 2] = rz * corners[k][0]; }
      const u0 = pick.flip ? 1 : 0, u1 = pick.flip ? 0 : 1;
      uv.set([u0, 1, u1, 1, u1, 0, u0, 0]);
      m.geometry.attributes.position.needsUpdate = true; m.geometry.attributes.uv.needsUpdate = true;
      const sa = m.geometry.attributes.aSector; if (sa.array[0] !== th[11]) { sa.array.fill(th[11]); sa.needsUpdate = true; }
      m.position.set(x, z, -y);
      // light sources
      const def = SPRITE_LIGHTS[name];
      if (def || bright) {
        const c = def ? new THREE.Color(def[0], def[1], def[2]) : sp.color;
        const isMissile = (th[7] & MF_MISSILE) !== 0;
        emitters.push({ id, x, y: z + Math.min(sp.top * 0.7, 48), z: -y, color: c,
          power: def ? def[3] : isMissile ? 10 : 2.5, range: def ? def[4] : isMissile ? 480 : 220, flicker: !isMissile });
      }
    }
    for (const [id, m] of spriteMeshes) if (!seen.has(id)) { if (cur.things.has(id)) m.visible = false; else { scene.remove(m); m.geometry.dispose(); spriteMeshes.delete(id); } }

    // --- give the light pool to the nearest emitters
    for (const em of emitters) em.d = (em.x - cx) ** 2 + (em.z + cy) ** 2;
    emitters.sort((a, b) => a.d - b.d);
    const tt = now / 1000;
    for (let i = 0; i < lights.length; i++) {
      const l = lights[i], em = emitters[i];
      if (!em || em.d > 1800 * 1800) { l.intensity = 0; continue; }
      let f = 1;
      if (em.flicker) { let ph = flickers.get(em.id); if (ph === undefined) flickers.set(em.id, (ph = Math.random() * 100)); f = 0.85 + 0.15 * Math.sin(tt * 9 + ph) * Math.sin(tt * 3.7 + ph * 2); }
      l.color.copy(em.color); l.position.set(em.x, em.y, em.z); l.distance = em.range; l.intensity = em.power * 1500 * f;
    }
    // muzzle flash: the engine's extralight plus the flash sprite
    const flash = P[5] > 0 || P[17] >= 0;
    muzzle.intensity = flash ? (P[5] + 1) * 7000 : 0;
    const fx = Math.cos(ang), fy = Math.sin(ang);
    muzzle.position.set(cx + fx * 24, cz - 6, -(cy + fy * 24));
    const here = sectorData[P[44] | 0] ?? 0.2;
    fill.intensity = 1600 * (1 - Math.min(1, here * 2.2));
    fill.position.set(cx, cz + 8, -cy);
    shared.uAmbient.value = P[22] > 0 ? 3.0 : 1.0; // light amplification visor
    camera.updateMatrixWorld();
    const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
    torch.intensity = torchOn ? 5200 : 0;
    torch.position.copy(camera.position).addScaledVector(right, 7).y -= 7;
    torch.target.position.copy(camera.position).addScaledVector(fwd, 200).y -= 18; // aimed a touch low, like a held light
    torch.target.updateMatrixWorld();
    aoCamera.position.copy(camera.position); aoCamera.quaternion.copy(camera.quaternion); aoCamera.updateMatrixWorld();
    effects.update(dt, cur.things, eng.sprnames, camera.position, { position: torch.position, dir: fwd, on: torchOn }, here, tt);
    if (skyMesh) skyMesh.material.color.setScalar(0.73);

    // --- sound: listener on the camera, reverb sized to the room
    {
      const ps = P[44] | 0;
      audio.frame(camera, H[ps * 5 + 1] - H[ps * 5], H[ps * 5 + 4] === assets.skyflat, eng);
    }

    // --- post effects
    grade.uniforms.uHurt.value = Math.min(1, P[10] / 40);
    grade.uniforms.uBonus.value = Math.min(0.35, P[11] / 30);
    grade.uniforms.uSuit.value = P[23] > 0 ? 1 : 0;
    composer.render();

    // --- weapon
    const W = opts.weaponCanvas.width, Hh = opts.weaponCanvas.height;
    wctx.clearRect(0, 0, W, Hh);
    const scale = Hh / 200, x0 = (W - 320 * scale) / 2;
    const wlight = Math.min(1.4, 0.35 + here * 1.6 + (flash ? 0.6 : 0));
    for (let k = 0; k < 2; k++) {
      const spr = P[13 + k * 4]; if (spr < 0) continue;
      const frm = P[14 + k * 4];
      const fr = assets.spriteTable(eng.sprnames[spr])[frm & FF_FRAMEMASK]; if (!fr || !fr[0]) continue;
      const sp = assets.sprite(fr[0].lump, 2);
      const sx = lerp(Pp[15 + k * 4], P[15 + k * 4], t), sy = lerp(Pp[16 + k * 4], P[16 + k * 4], t);
      wctx.filter = (frm & FF_FULLBRIGHT) ? 'brightness(1.25)' : `brightness(${wlight.toFixed(2)})`;
      const ox = -swayX * 14, oy = swayY * 8 + kick * 9 + 2;
      wctx.drawImage(sp.getCanvas(), x0 + (sx - sp.left + ox) * scale, (sy - sp.top + oy) * scale, sp.w * scale, sp.h * scale);
    }
    wctx.filter = 'none';
    if (P[17] >= 0) {
      // muzzle glow over the weapon
      const gx = x0 + (160 - swayX * 14) * scale, gy = Hh * 0.6, gr = 80 * scale;
      const g = wctx.createRadialGradient(gx, gy, 0, gx, gy, gr);
      g.addColorStop(0, 'rgba(255,220,150,0.55)'); g.addColorStop(0.35, 'rgba(255,150,60,0.22)'); g.addColorStop(1, 'rgba(255,120,40,0)');
      wctx.globalCompositeOperation = 'lighter'; wctx.fillStyle = g; wctx.fillRect(gx - gr, gy - gr, gr * 2, gr * 2);
      wctx.globalCompositeOperation = 'source-over';
    }

    hud(P, now - msgTimer);
    frames++; if (now - fpsT > 1000) { fps = frames; frames = 0; fpsT = now; status(fps); }
  };
  window.__relitDebug = { audio, eng, scene, camera, THREE, sprname: (id) => { const th = cur?.things?.get(id); return th ? eng.sprnames[th[5]] + ' type ' + th[10] + ' z ' + th[3] / 65536 : null; } }; // used by automated tests
  requestAnimationFrame(frame);
  opts.torchButton?.addEventListener('click', (ev) => { ev.stopPropagation(); torchOn = !torchOn; opts.onTorch?.(torchOn); });
  return { toggleMode };
}
