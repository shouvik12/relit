// Relit car mode: the car you see, its lights, the chase camera and the engine note.
// The Doom engine does the driving (engine-src/.../relit_car.c); this only draws it.
// GPL-2.0-or-later.

import { THREE } from './vendor/libs.js';

const M = 24; // Doom units per metre on the car level
const lerpAng = (a, b, t) => { let d = b - a; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; return a + d * t; };

// A Stunt Rally 3 car converted by tools/stuntrally-car (.rcar): JSON header + binary blocks.
function parseRcar(bytes) {
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (String.fromCharCode(...u8.subarray(0, 4)) !== 'RCAR') return null;
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const hl = dv.getUint32(4, true);
  const head = JSON.parse(new TextDecoder().decode(u8.subarray(8, 8 + hl)));
  const base = 8 + hl;
  const buf = u8.slice(base).buffer; // aligned copy
  head.textures = head.textures.map((t) => {
    const tex = new THREE.Texture(); tex.colorSpace = THREE.SRGBColorSpace; tex.flipY = false;
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping; tex.anisotropy = 4;
    createImageBitmap(new Blob([new Uint8Array(buf, t.off, t.len)], { type: t.mime }), { imageOrientation: 'none' })
      .then((img) => { tex.image = img; tex.needsUpdate = true; }).catch(() => {});
    return tex;
  });
  head.parts = head.parts.map((p) => {
    const geo = new THREE.BufferGeometry();
    const pos = new Float32Array(p.count * 3), src = new Int16Array(buf, p.pos, p.count * 3);
    for (let i = 0; i < pos.length; i++) pos[i] = src[i] * p.scale * M;
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('normal', new THREE.BufferAttribute(new Int8Array(buf, p.nrm, p.count * 4), 4, true));
    const uv = new Float32Array(p.count * 2), us = new Int16Array(buf, p.uv, p.count * 2);
    for (let i = 0; i < uv.length; i++) uv[i] = us[i] / 4096;
    geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    geo.setIndex(new THREE.BufferAttribute(p.big ? new Uint32Array(buf, p.idx, p.nidx) : new Uint16Array(buf, p.idx, p.nidx), 1));
    return { ...p, geo };
  });
  return head;
}

export class Car {
  constructor(scene, audio, modelBytes) {
    const g = new THREE.Group(); this.group = g; g.visible = false;
    const model = modelBytes ? parseRcar(modelBytes) : null;
    const paint = new THREE.MeshStandardMaterial({ color: 0x8a9196, roughness: 0.38, metalness: 0.7, emissive: 0x0c0f14 });
    const steel = new THREE.MeshStandardMaterial({ color: 0x2a2725, roughness: 0.5, metalness: 0.8, emissive: 0x050506 });
    const glass = new THREE.MeshStandardMaterial({ color: 0x07090c, roughness: 0.15, metalness: 0.9 });
    const tyre = new THREE.MeshStandardMaterial({ color: 0x0b0b0b, roughness: 0.95 });
    const glowMat = (r, gr, b) => { const m = new THREE.MeshBasicMaterial(); m.color.setRGB(r, gr, b); return m; };
    this.headMat = glowMat(6, 5.2, 3.6); this.tailMat = glowMat(5, 0.35, 0.15); this.flashMat = glowMat(9, 6, 2.5);
    this.paint = paint; this.steel = steel; this.paintColor = 0x8a9196;
    const box = (w, h, l, mat, x, y, z) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(w * M, h * M, l * M), mat);
      m.position.set(x * M, y * M, z * M); g.add(m); return m;
    };
    // the car points along +z here; update() turns it to the engine's heading.
    // Each wheel: a pivot that steers, holding a mesh that spins (sign flips it on mirrored wheels).
    this.wheels = [];
    // where the weapons and lights go, in metres: half width, roof height, front and back
    let hw = 0.95, roof = 1.4, front = 2.18, back = -2.14, lampY = 0.74;
    if (model) {
      this.paintColor = parseInt(model.paint, 16);
      paint.color.setHex(this.paintColor); paint.metalness = model.metal; paint.roughness = model.rough; paint.emissive.setHex(0x050608);
      const wheels = model.wheels;
      const lift = -Math.min(...wheels.map((w) => w.up - w.r)); // wheels stand on y = 0
      const mats = {
        body: (t) => { paint.map = t; return paint; },
        interior: (t) => new THREE.MeshStandardMaterial({ map: t, color: 0xb3b3b3, roughness: 0.6, metalness: 0.2 }),
        glass: (t) => new THREE.MeshStandardMaterial({ color: 0x0b0e12, roughness: 0.05, metalness: 0.9, transparent: true, opacity: 0.55, depthWrite: false }),
        wheel: (t) => new THREE.MeshStandardMaterial({ map: t, color: 0xb3b3b3, roughness: 0.8, metalness: 0.2 }),
      };
      mats.wheelR = mats.wheel;
      const box3 = new THREE.Box3();
      const wheelGeo = {};
      for (const p of model.parts) {
        const tex = p.tex != null ? model.textures[p.tex] : null;
        if (p.kind === 'wheel' || p.kind === 'wheelR') { wheelGeo[p.kind] = { geo: p.geo, mat: mats.wheel(tex) }; continue; }
        const m = new THREE.Mesh(p.geo, mats[p.kind](tex));
        m.position.y = lift * M; g.add(m);
        if (p.kind === 'body') { p.geo.computeBoundingBox(); box3.copy(p.geo.boundingBox); }
      }
      for (const w of wheels) {
        const src = (!w.front && wheelGeo.wheelR) || wheelGeo.wheel; if (!src) continue;
        const pivot = new THREE.Group(); pivot.position.set(w.x * M, (w.up + lift) * M, w.fwd * M);
        const spin = new THREE.Mesh(src.geo, src.mat);
        // Stunt Rally's wheel meshes face +x (right side); turn the left ones round
        if (w.left) spin.rotation.y = Math.PI;
        pivot.add(spin); g.add(pivot);
        this.wheels.push({ pivot, spin, sign: 1, front: w.front, r: w.r * M });
      }
      if (!box3.isEmpty()) {
        hw = Math.min(box3.max.x, -box3.min.x) / M; roof = box3.max.y / M + lift; front = box3.max.z / M; back = box3.min.z / M;
        lampY = box3.min.y / M + lift + 0.35;
      }
      // keep the weapons: a roof gun, rocket pods and steel ram bars
      box(0.42, 0.26, 0.55, steel, 0, roof + 0.13, -0.2); box(0.12, 0.12, 1.4, steel, 0, roof + 0.2, 0.45);
      box(0.3, 0.26, 0.9, steel, hw * 0.62, roof + 0.1, -0.5); box(0.3, 0.26, 0.9, steel, -hw * 0.62, roof + 0.1, -0.5);
      box(hw * 2.1, 0.22, 0.16, steel, 0, lampY - 0.12, front + 0.06); box(hw * 2.1, 0.22, 0.16, steel, 0, lampY - 0.1, back - 0.06);
      this.flash = box(0.34, 0.34, 0.5, this.flashMat, 0, roof + 0.2, 1.35); this.flash.visible = false;
    } else {
      box(1.9, 0.5, 4.2, paint, 0, 0.66, 0);
      box(1.72, 0.26, 1.5, paint, 0, 0.98, 1.2).rotation.x = 0.12;
      box(1.5, 0.5, 1.7, glass, 0, 1.14, -0.35);
      box(2.15, 0.34, 0.22, steel, 0, 0.56, 2.18); box(2.15, 0.34, 0.22, steel, 0, 0.6, -2.14);   // ram bars
      box(0.08, 0.4, 1.0, steel, 0.7, 1.2, -1.6); box(0.08, 0.4, 1.0, steel, -0.7, 1.2, -1.6);       // fins
      box(0.42, 0.26, 0.55, steel, 0, 1.5, -0.2); box(0.12, 0.12, 1.4, steel, 0, 1.6, 0.45);         // roof gun
      box(0.34, 0.3, 1.05, steel, 1.1, 0.98, -0.2); box(0.34, 0.3, 1.05, steel, -1.1, 0.98, -0.2);   // rocket pods
      this.flash = box(0.34, 0.34, 0.5, this.flashMat, 0, 1.6, 1.35); this.flash.visible = false;
      const wg = new THREE.CylinderGeometry(0.46 * M, 0.46 * M, 0.4 * M, 12); wg.rotateZ(Math.PI / 2);
      for (const [x, z] of [[1, 1.4], [-1, 1.4], [1, -1.4], [-1, -1.4]]) {
        const pivot = new THREE.Group(); pivot.position.set(x * M, 0.46 * M, z * M);
        const spin = new THREE.Mesh(wg, tyre); pivot.add(spin); g.add(pivot);
        this.wheels.push({ pivot, spin, sign: 1, front: z > 0, r: 0.46 * M });
      }
    }
    const lx = Math.min(0.7, hw * 0.72);
    this.heads = [box(0.3, 0.12, 0.05, this.headMat, lx, lampY, front + 0.03), box(0.3, 0.12, 0.05, this.headMat, -lx, lampY, front + 0.03)];
    box(0.3, 0.12, 0.05, this.tailMat, lx, lampY + 0.04, back - 0.03); box(0.3, 0.12, 0.05, this.tailMat, -lx, lampY + 0.04, back - 0.03);
    this.front = front; this.camUp = Math.max(0, roof - 1.4) * M * 1.1; this.camBack = Math.max(0, front - back - 4.3) * M * 0.6; this.seatUp = (roof - 1.4) * M * 0.8;
    g.traverse((o) => { if (o.isMesh) o.receiveShadow = false; });
    scene.add(g);
    this.view = 0; this.lookYaw = 0; this.lookPitch = 0; this.lookIdle = 9;
    this.z = null; this.camAng = null; this.pitch = 0; this.roll = 0; this.spin = 0; this.lastAng = 0; this.wreck = false;

    // engine note: one sawtooth through a low-pass, pitched by speed
    this.audio = audio;
    if (audio?.ctx) {
      const c = audio.ctx;
      this.osc = c.createOscillator(); this.osc.type = 'sawtooth'; this.osc.frequency.value = 50;
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 380;
      this.gain = c.createGain(); this.gain.gain.value = 0;
      this.osc.connect(lp).connect(this.gain).connect(audio.master); this.osc.start();
    }
  }

  look(dx, dy) {
    this.lookIdle = 0;
    this.lookYaw = Math.max(-Math.PI, Math.min(Math.PI, this.lookYaw - dx * 0.0025));
    this.lookPitch = Math.max(-0.5, Math.min(0.45, this.lookPitch - dy * 0.0022));
  }

  nextView() { this.view = (this.view + 1) % 3; }

  quiet() { if (this.gain) this.gain.gain.value = 0; }

  // s: x, y (Doom), z (floor under the car), ang, speed (units per tic), grounded, flash, dead, lights, gas
  update(dt, s, camera) {
    if (this.z === null || Math.abs(s.z - this.z) > 200) { this.z = s.z; this.camAng = s.ang; this.lastAng = s.ang; }
    const vz = (s.z - this.z) * Math.min(1, dt * 14); this.z += vz;   // steps in the road become a slope
    const g = this.group;
    g.position.set(s.x, this.z, -s.y);
    // nose up on climbs and over crests, lean into turns
    const run = Math.max(40, s.speed * 35) * dt;
    this.pitch += (Math.max(-0.35, Math.min(0.35, Math.atan2(vz, run))) - this.pitch) * Math.min(1, dt * 8);
    let turn = s.ang - this.lastAng; while (turn > Math.PI) turn -= 2 * Math.PI; while (turn < -Math.PI) turn += 2 * Math.PI;
    this.lastAng = s.ang;
    this.roll += (Math.max(-0.12, Math.min(0.12, -(turn / dt) * s.speed * 0.004)) - this.roll) * Math.min(1, dt * 6);
    g.rotation.set(-this.pitch, s.ang + Math.PI / 2, this.roll, 'YXZ');
    this.spin += s.speed * 35 * dt;
    const steer = Math.max(-0.4, Math.min(0.4, (turn / dt) * 0.25));
    for (const w of this.wheels) { w.spin.rotation.x = w.sign * this.spin / w.r; if (w.front) w.pivot.rotation.y = steer; }
    this.flash.visible = s.flash && !s.dead;
    for (const h of this.heads) h.visible = s.lights && !s.dead;
    if (s.dead !== this.wreck) {
      this.wreck = s.dead;
      this.paint.color.set(s.dead ? 0x0a0908 : this.paintColor); this.tailMat.color.setRGB(s.dead ? 0.02 : 5, s.dead ? 0.01 : 0.35, s.dead ? 0.01 : 0.15);
    }

    // camera: chase (close), chase (far) or the driver's seat, as C cycles. It turns with the car
    // straight away, like Doom's view; the mouse swings it round and it drifts back after a moment.
    this.camAng = lerpAng(this.camAng, s.ang, 1 - Math.exp(-(this.view === 2 ? 40 : 14) * dt));
    this.lookIdle += dt;
    if (this.lookIdle > 1.2) { const k = 1 - Math.exp(-2.5 * dt); this.lookYaw -= this.lookYaw * k; this.lookPitch -= this.lookPitch * k; }
    const yaw = this.camAng + this.lookYaw;
    let cx, cy, cz, tilt;
    if (this.view === 2 && !s.dead) {
      const seat = this.front * 0.1 * M;
      cx = s.x + Math.cos(s.ang) * seat; cy = s.y + Math.sin(s.ang) * seat; cz = this.z + 1.55 * M + this.seatUp; tilt = -0.04;
    } else {
      const far = this.view === 1;
      const back = (s.dead ? 250 : far ? 230 : 138) + this.camBack + s.speed * 1.7, up = (s.dead ? 150 : far ? 95 : 56) + this.camUp;
      cx = s.x - Math.cos(yaw) * back; cy = s.y - Math.sin(yaw) * back; cz = this.z + up; tilt = s.dead ? -0.42 : far ? -0.2 : -0.11;
    }
    camera.position.set(cx, cz, -cy);
    camera.rotation.set(tilt + this.lookPitch, yaw - Math.PI / 2, 0, 'YXZ');
    this.group.visible = this.view !== 2 || s.dead;

    if (this.gain) {
      const t = this.audio.ctx.currentTime;
      this.osc.frequency.setTargetAtTime(46 + s.speed * 4.6 + (s.gas ? 10 : 0), t, 0.05);
      this.gain.gain.setTargetAtTime(s.dead ? 0 : 0.045 + Math.min(0.05, s.speed / 500) + (s.gas ? 0.02 : 0), t, 0.08);
    }
    return { x: cx, y: cy, ang: yaw };
  }

  // headlights: Relit's shadow-casting flashlight, moved to the nose of the car
  aimLights(torch, on, s) {
    const fx = Math.cos(s.ang), fy = Math.sin(s.ang);
    torch.intensity = on && !s.dead ? 9000 : 0;
    torch.angle = 0.62; torch.distance = 1900;
    torch.position.set(s.x + fx * 58, this.z + 26, -(s.y + fy * 58));
    torch.target.position.set(s.x + fx * 520, this.z + 4 + Math.sin(this.pitch) * 460, -(s.y + fy * 520));
    torch.target.updateMatrixWorld();
  }
}
