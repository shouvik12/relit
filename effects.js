// Relit atmosphere: sparks, smoke, explosion bursts, embers and dust, all as
// GPU point particles. GPL-2.0-or-later.

import { THREE } from './vendor/libs.js';

const VERT = `
attribute float aSize; attribute float aLife; attribute vec3 aColor;
varying float vLife; varying vec3 vColor;
uniform float uScale;
void main(){
  vLife = aLife; vColor = aColor;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = aLife > 0.0 ? aSize * uScale / max(1.0, -mv.z) : 0.0;
  gl_Position = projectionMatrix * mv;
}`;

const FRAG_GLOW = `
varying float vLife; varying vec3 vColor;
void main(){
  vec2 d = gl_PointCoord - 0.5; float r = dot(d, d) * 4.0;
  if (r > 1.0) discard;
  float a = (1.0 - r) * (1.0 - r) * clamp(vLife, 0.0, 1.0);
  gl_FragColor = vec4(vColor * a, a);
}`;

const FRAG_SMOKE = `
varying float vLife; varying vec3 vColor;
void main(){
  vec2 d = gl_PointCoord - 0.5; float r = dot(d, d) * 4.0;
  if (r > 1.0) discard;
  float a = (1.0 - r) * clamp(vLife, 0.0, 1.0) * 0.35;
  gl_FragColor = vec4(vColor, a);
}`;

class Pool {
  constructor(scene, n, frag, blending) {
    this.n = n; this.next = 0;
    this.pos = new Float32Array(n * 3); this.vel = new Float32Array(n * 3);
    this.size = new Float32Array(n); this.life = new Float32Array(n); this.decay = new Float32Array(n);
    this.color = new Float32Array(n * 3); this.grav = new Float32Array(n); this.grow = new Float32Array(n); this.drag = new Float32Array(n);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aSize', new THREE.BufferAttribute(this.size, 1).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aLife', new THREE.BufferAttribute(this.life, 1).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aColor', new THREE.BufferAttribute(this.color, 3).setUsage(THREE.DynamicDrawUsage));
    this.uniforms = { uScale: { value: 600 } };
    const m = new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: frag, uniforms: this.uniforms, transparent: true, depthWrite: false, blending });
    this.points = new THREE.Points(g, m); this.points.frustumCulled = false; this.points.layers.set(1); this.points.renderOrder = 5;
    scene.add(this.points);
    this.geo = g;
  }
  spawn(x, y, z, vx, vy, vz, size, life, r, g, b, grav = 0, grow = 0, drag = 0) {
    const i = this.next; this.next = (this.next + 1) % this.n;
    this.pos.set([x, y, z], i * 3); this.vel.set([vx, vy, vz], i * 3); this.color.set([r, g, b], i * 3);
    this.size[i] = size; this.life[i] = 1; this.decay[i] = 1 / life; this.grav[i] = grav; this.grow[i] = grow; this.drag[i] = drag;
  }
  update(dt) {
    const p = this.pos, v = this.vel;
    for (let i = 0; i < this.n; i++) {
      if (this.life[i] <= 0) continue;
      this.life[i] -= this.decay[i] * dt;
      const k = Math.max(0, 1 - this.drag[i] * dt);
      v[i * 3] *= k; v[i * 3 + 1] = v[i * 3 + 1] * k - this.grav[i] * dt; v[i * 3 + 2] *= k;
      p[i * 3] += v[i * 3] * dt; p[i * 3 + 1] += v[i * 3 + 1] * dt; p[i * 3 + 2] += v[i * 3 + 2] * dt;
      this.size[i] += this.grow[i] * dt;
    }
    for (const a of ['position', 'aSize', 'aLife', 'aColor']) this.geo.attributes[a].needsUpdate = true;
  }
}

const rnd = (a, b) => a + Math.random() * (b - a);
const dir = () => { const u = rnd(-1, 1), t = rnd(0, Math.PI * 2), s = Math.sqrt(1 - u * u); return [s * Math.cos(t), u, s * Math.sin(t)]; };

// Which frames of a projectile are its impact.
const IMPACT = { MISL: 1, BAL1: 2, BAL2: 2, BAL7: 2, PLSE: 0, BFE1: 0, BEXP: 2, APBX: 0, MANF: 2, FATB: 2 };
const IMPACT_COLOR = { MISL: [1, 0.55, 0.2], BEXP: [1, 0.55, 0.2], BAL1: [1, 0.45, 0.15], BAL2: [1, 0.3, 0.6], BAL7: [0.4, 1, 0.3],
  PLSE: [0.35, 0.55, 1], BFE1: [0.35, 1, 0.35], APBX: [0.5, 1, 0.3], MANF: [1, 0.5, 0.15], FATB: [1, 0.5, 0.15] };
const BIG = { MISL: 1, BEXP: 1, BFE1: 1 };
const FIRE = { FCAN: [1, 0.5, 0.15], TRED: [1, 0.35, 0.1], SMRT: [1, 0.35, 0.1], TBLU: [0.35, 0.5, 1], SMBT: [0.35, 0.5, 1],
  TGRN: [0.4, 1, 0.35], SMGT: [0.4, 1, 0.35], CAND: [1, 0.6, 0.25], CBRA: [1, 0.6, 0.25], POL3: [1, 0.55, 0.2] };

export class Effects {
  constructor(scene, coarse) {
    this.glow = new Pool(scene, coarse ? 900 : 2400, FRAG_GLOW, THREE.AdditiveBlending);
    this.smoke = new Pool(scene, coarse ? 300 : 800, FRAG_SMOKE, THREE.NormalBlending);
    this.seen = new Map(); // thing id -> impact handled
    this.shake = 0; this.kick = 0;
    // dust that drifts around the player and catches the flashlight
    const N = coarse ? 250 : 700;
    this.dust = { n: N, pos: new Float32Array(N * 3), seed: new Float32Array(N) };
    for (let i = 0; i < N; i++) { this.dust.pos.set([rnd(-300, 300), rnd(-120, 160), rnd(-300, 300)], i * 3); this.dust.seed[i] = Math.random() * 100; }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.dust.pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aSeed', new THREE.BufferAttribute(this.dust.seed, 1));
    this.dustUniforms = { uTorchPos: { value: new THREE.Vector3() }, uTorchDir: { value: new THREE.Vector3(0, 0, -1) }, uTorch: { value: 1 }, uAmbient: { value: 0.2 }, uTime: { value: 0 }, uScale: { value: 600 } };
    const m = new THREE.ShaderMaterial({
      uniforms: this.dustUniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: `attribute float aSeed; uniform vec3 uTorchPos, uTorchDir; uniform float uTorch, uAmbient, uTime, uScale; varying float vA;
        void main(){
          vec3 p = position + vec3(sin(uTime * 0.21 + aSeed) * 6.0, sin(uTime * 0.13 + aSeed * 1.7) * 4.0, cos(uTime * 0.17 + aSeed * 2.3) * 6.0);
          vec4 wp = modelMatrix * vec4(p, 1.0);
          vec3 d = wp.xyz - uTorchPos; float dist = length(d);
          float cone = smoothstep(0.86, 0.97, dot(d / max(dist, 1.0), uTorchDir)) * uTorch * clamp(1.0 - dist / 700.0, 0.0, 1.0);
          vA = (cone * 0.9 + uAmbient * 0.12) * (0.5 + 0.5 * sin(aSeed * 13.0));
          vec4 mv = viewMatrix * wp;
          gl_PointSize = 2.2 * uScale / max(1.0, -mv.z);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `varying float vA; void main(){ vec2 d = gl_PointCoord - 0.5; float r = dot(d, d) * 4.0; if (r > 1.0) discard; float a = (1.0 - r) * vA; gl_FragColor = vec4(vec3(1.0, 0.95, 0.85) * a, a); }`,
    });
    this.dustPoints = new THREE.Points(g, m); this.dustPoints.frustumCulled = false; this.dustPoints.layers.set(1);
    scene.add(this.dustPoints);
    this.emberClock = 0;
  }

  setScale(px) { this.glow.uniforms.uScale.value = this.smoke.uniforms.uScale.value = this.dustUniforms.uScale.value = px; }

  sparks(x, y, z, n, color = [1, 0.75, 0.4]) {
    for (let i = 0; i < n; i++) {
      const [dx, dy, dz] = dir(), s = rnd(80, 260);
      this.glow.spawn(x, y, z, dx * s, Math.abs(dy) * s * 0.8 + 40, dz * s, rnd(0.7, 1.4), rnd(0.2, 0.5), color[0] * 5, color[1] * 5, color[2] * 5, 520, 0, 2);
    }
    for (let i = 0; i < 2; i++) this.smoke.spawn(x + rnd(-3, 3), y, z + rnd(-3, 3), rnd(-8, 8), rnd(14, 28), rnd(-8, 8), rnd(5, 9), rnd(0.9, 1.6), 0.16, 0.15, 0.14, -6, 16, 1.5);
  }

  burst(x, y, z, color, big) {
    const n = big ? 70 : 32;
    for (let i = 0; i < n; i++) {
      const [dx, dy, dz] = dir(), s = rnd(60, big ? 420 : 260);
      this.glow.spawn(x, y, z, dx * s, dy * s + 60, dz * s, rnd(3, big ? 9 : 6), rnd(0.35, big ? 1.1 : 0.7), color[0] * 3, color[1] * 3, color[2] * 3, 300, 0, 2.2);
    }
    for (let i = 0; i < (big ? 14 : 5); i++) {
      const [dx, dy, dz] = dir();
      this.smoke.spawn(x + dx * 10, y + dy * 10, z + dz * 10, dx * 30, 30 + Math.abs(dy) * 30, dz * 30, rnd(18, 30), rnd(1.4, 2.6), 0.22, 0.2, 0.19, -10, 34, 1.2);
    }
  }

  // things: Map id -> int32 record; names: sprite names; camPos: THREE.Vector3
  update(dt, things, names, camPos, torch, ambient, time) {
    const now = new Set();
    this.emberClock += dt;
    const emberTick = this.emberClock > 0.06; if (emberTick) this.emberClock = 0;
    for (const [id, th] of things) {
      const name = names[th[5]], frame = th[6] & 0x7fff;
      const x = th[1] / 65536, z = -th[2] / 65536, y = th[3] / 65536;
      now.add(id);
      if (name === 'PUFF' && !this.seen.has(id)) { this.seen.set(id, 1); this.sparks(x, y + 2, z, 10); continue; }
      if (name in IMPACT && frame >= IMPACT[name] && !this.seen.has(id)) {
        this.seen.set(id, 1);
        this.burst(x, y + 16, z, IMPACT_COLOR[name], !!BIG[name]);
        const d = Math.hypot(x - camPos.x, z - camPos.z);
        this.shake = Math.max(this.shake, (BIG[name] ? 1 : 0.35) * Math.max(0, 1 - d / 900));
        continue;
      }
      if (emberTick && FIRE[name] && Math.random() < 0.55) {
        const c = FIRE[name], h = name.startsWith('C') ? 16 : name === 'FCAN' ? 40 : 56;
        if ((x - camPos.x) ** 2 + (z - camPos.z) ** 2 < 1600 * 1600)
          this.glow.spawn(x + rnd(-6, 6), y + h, z + rnd(-6, 6), rnd(-8, 8), rnd(30, 70), rnd(-8, 8), rnd(1.5, 3), rnd(0.8, 1.8), c[0] * 2.2, c[1] * 2.2, c[2] * 2.2, -10, -0.8, 0.6);
      }
    }
    for (const id of this.seen.keys()) if (!now.has(id)) this.seen.delete(id);
    this.glow.update(dt); this.smoke.update(dt);
    // dust box follows the camera, wrapping around it
    const p = this.dust.pos;
    for (let i = 0; i < this.dust.n; i++) {
      for (let a = 0; a < 3; a++) {
        const c = a === 0 ? camPos.x : a === 1 ? camPos.y : camPos.z, half = a === 1 ? 140 : 300;
        let v = p[i * 3 + a];
        if (v < c - half) v += half * 2; else if (v > c + half) v -= half * 2;
        p[i * 3 + a] = v;
      }
    }
    this.dustPoints.geometry.attributes.position.needsUpdate = true;
    const u = this.dustUniforms;
    u.uTorchPos.value.copy(torch.position); u.uTorchDir.value.copy(torch.dir); u.uTorch.value = torch.on ? 1 : 0; u.uAmbient.value = ambient; u.uTime.value = time;
    this.shake = Math.max(0, this.shake - dt * 2.2);
  }
}
