"""Turn a Stunt Rally 3 track into a Doom II level for Relit's car mode.

    python3 tools/stuntrally2doom/stuntrally2doom.py maps/src/vlc1-dark wreck.raw.wad
    python3 tools/quake2doom/nodes.py wreck.raw.wad maps/wrecklight.wad

A Stunt Rally track is a heightmap (heightmap.f32, 32-bit floats) and a road spline
(road.xml). Doom levels are flat-floored sectors, so the track becomes a ribbon of small
sectors that follow the road: the road itself, a kerb, two bands of roadside ground and a
cliff rim on each side. Each takes its height from the road spline or the heightmap, so
slopes become small steps, and bridges become raised road with a parapet over a drop.
Ground below the track's water level becomes lava.

The level goes in Doom II's MAP07 slot: when the last mancubus dies the gate before the
finish line sinks, as in the original game. Needs: pip install numpy shapely
The output is GPL-3.0-or-later (the track data is GPL v3); this script is GPL-2.0-or-later.
"""
import math, random, re, struct, sys
from collections import defaultdict

import numpy as np
from shapely.geometry import Polygon
from shapely.strtree import STRtree

S = 24.0                # Doom units per metre
LAVA = -2.0             # the track's water level, metres
KERB, NEAR, FAR, RIM = 1.5, 9.0, 20.0, 1.0   # roadside band widths out from the road edge, metres
OFFROAD_TAG = 900       # the engine gives these sectors less grip (see relit_car.h)


# ---------------------------------------------------------------- track
def load_track(folder):
    hm = np.fromfile(f'{folder}/heightmap.f32', dtype='<f4')
    n = int(round(math.sqrt(hm.size)))
    hm = hm.reshape(n, n)
    xml = open(f'{folder}/road.xml').read()
    scene = open(f'{folder}/scene.xml').read()
    tri = float(re.search(r'triangle="([^"]+)"', scene).group(1))
    pts = []
    for m in re.finditer(r'<P ([^>]*)/>', xml):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        x, y, z = map(float, a['pos'].split())
        pts.append((x, y, z, float(a['w']), int(a.get('onTer', '1'))))
    start = re.search(r'<start pos="([^"]+)"', scene).group(1).split()
    return hm, tri, pts, (float(start[0]), -float(start[1]))


class Terrain:
    def __init__(self, hm, tri):
        self.hm, self.tri, self.n = hm, tri, hm.shape[0]
        self.half = self.n * tri / 2

    def at(self, x, z):
        """Height in metres at track coordinates (x east, z south)."""
        c = min(max((x + self.half) / self.tri, 0), self.n - 1.001)
        r = min(max((self.half - z) / self.tri, 0), self.n - 1.001)
        c0, r0 = int(c), int(r); fc, fr = c - c0, r - r0
        h = self.hm
        return float(h[r0, c0] * (1 - fc) * (1 - fr) + h[r0, c0 + 1] * fc * (1 - fr)
                     + h[r0 + 1, c0] * (1 - fc) * fr + h[r0 + 1, c0 + 1] * fc * fr)


def spline(pts, ter):
    """Closed Catmull-Rom through the road points, about one sample every two metres."""
    n = len(pts)
    cy = [ter.at(p[0], p[2]) if p[4] else p[1] for p in pts]
    cr = lambda a, b, c, d, t: 0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (-a + 3 * b - 3 * c + d) * t ** 3)
    X, Z, Y, W, B = [], [], [], [], []
    for i in range(n):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        k = max(4, round(math.hypot(p2[0] - p1[0], p2[2] - p1[2]) / 2))
        for j in range(k):
            t = j / k; st = t * t * (3 - 2 * t)
            X.append(cr(p0[0], p1[0], p2[0], p3[0], t)); Z.append(cr(p0[2], p1[2], p2[2], p3[2], t))
            Y.append(cr(cy[i - 1], cy[i], cy[(i + 1) % n], cy[(i + 2) % n], t))
            W.append(p1[3] + (p2[3] - p1[3]) * st)
            B.append(0 if (p1[4] and p2[4]) else 1)
    return np.array(X), np.array(Z), np.array(Y), np.array(W), np.array(B)


# ---------------------------------------------------------------- level
class Level:
    def __init__(self):
        self.verts, self.vidx = [], {}
        self.sectors, self.sidx = [], {}
        self.faces = []       # (vertex loop, sector)
        self.things = []
        self.line_props = {}  # (va, vb) sorted -> dict(special, flags)

    def vertex(self, x, y):
        k = (int(round(x)), int(round(y)))
        if k not in self.vidx:
            self.vidx[k] = len(self.verts); self.verts.append(k)
        return self.vidx[k]

    def sector(self, floor, ceil, fflat, cflat, light, special=0, tag=0, zone=0, wall='ASHWALL2'):
        k = (int(floor), int(ceil), fflat, cflat, light, special, tag, zone, wall)
        if k not in self.sidx:
            self.sidx[k] = len(self.sectors); self.sectors.append(k)
        return self.sidx[k]

    def face(self, loop, sec):
        loop = [v for i, v in enumerate(loop) if v != loop[i - 1]]
        if len(loop) < 3:
            return
        a = sum(self.verts[loop[i - 1]][0] * self.verts[v][1] - self.verts[v][0] * self.verts[loop[i - 1]][1] for i, v in enumerate(loop))
        if abs(a) < 2:
            return
        if a < 0:
            loop = loop[::-1]          # keep every face counter-clockwise
        self.faces.append((loop, sec))

    def mark(self, va, vb, **props):
        self.line_props.setdefault((min(va, vb), max(va, vb)), {}).update(props)

    def thing(self, x, y, angle, kind, flags=7):
        self.things.append((int(round(x)), int(round(y)), int(angle) % 360, kind, flags))

    def write(self, path, mapname):
        edges = defaultdict(list)   # undirected edge -> [(face sector, runs low-to-high vertex?)]
        for loop, sec in self.faces:
            for i, b in enumerate(loop):
                a = loop[i - 1]
                edges[(min(a, b), max(a, b))].append((sec, a < b))
        lines, sides = [], []

        def side(sec, lower='-', mid='-'):
            sides.append(struct.pack('<hh8s8s8sH', 0, 0, b'-', lower.encode().ljust(8, b'\0'), mid.encode().ljust(8, b'\0'), sec))
            return len(sides) - 1

        bad = 0
        for (a, b), fs in sorted(edges.items()):
            pr = self.line_props.get((a, b), {})
            if len(fs) > 2:
                bad += 1; continue
            if len(fs) == 1:
                # one face: an outer wall. The face is counter-clockwise, so it lies on the
                # left of its own edge direction; Doom wants the front side on the right.
                sec, fwd = fs[0]
                v1, v2 = (b, a) if fwd else (a, b)
                lines.append(struct.pack('<HHhhhHH', v1, v2, 1, 0, 0, side(sec, mid=self.sectors[sec][8]), 0xFFFF))
                continue
            (s1, f1), (s2, f2) = fs
            if s1 == s2 and not pr:
                continue             # inside one sector: no line needed
            if f1 == f2:
                bad += 1; continue
            # front = the sector on the right of v1->v2
            v1, v2 = (b, a) if f1 else (a, b)
            k1, k2 = self.sectors[s1], self.sectors[s2]
            low = k1[8] if k1[0] < k2[0] else k2[8]   # a step shows the lower side's wall texture
            tex = low if k1[0] != k2[0] or k1[6] == 666 or k2[6] == 666 else '-'
            lines.append(struct.pack('<HHhhhHH', v1, v2, 4 | pr.get('flags', 0), pr.get('special', 0), 0,
                                     side(s1, lower=tex), side(s2, lower=tex)))
        if bad:
            print(f'warning: {bad} edges shared by overlapping faces were skipped', file=sys.stderr)
        lumps = [
            (mapname, b''),
            ('THINGS', b''.join(struct.pack('<hhhhh', *t) for t in self.things)),
            ('LINEDEFS', b''.join(lines)),
            ('SIDEDEFS', b''.join(sides)),
            ('VERTEXES', b''.join(struct.pack('<hh', *v) for v in self.verts)),
            ('SECTORS', b''.join(struct.pack('<hh8s8shhh', k[0], k[1], k[2].encode().ljust(8, b'\0'), k[3].encode().ljust(8, b'\0'), k[4], k[5], k[6]) for k in self.sectors)),
        ]
        data, dirs, off = b'', [], 12
        for nm, b in lumps:
            dirs.append(struct.pack('<II', off, len(b)) + nm.encode().ljust(8, b'\0')); data += b; off += len(b)
        open(path, 'wb').write(b'PWAD' + struct.pack('<II', len(lumps), off) + data + b''.join(dirs))
        print(f'{path}: {len(self.verts)} vertices, {len(lines)} lines, {len(sides)} sides, {len(self.sectors)} sectors, {len(self.things)} things')


def build(folder, out, mapname='MAP07', seed=7):
    rnd = random.Random(seed)
    hm, tri, pts, start_xz = load_track(folder)
    ter = Terrain(hm, tri)
    X, Z, Y, W, B = spline(pts, ter)
    M = len(X)
    idx = lambda i: i % M

    # road height: the spline on bridges, the ground elsewhere; then smoothed and slope-limited
    rh = np.array([Y[i] if B[i] else ter.at(X[i], Z[i]) for i in range(M)])
    rh = np.array([(rh[idx(i - 1)] + 2 * rh[i] + rh[idx(i + 1)]) / 4 for i in range(M)])
    rh = np.maximum(rh, LAVA + 0.35)
    for _ in range(6):
        for order in (range(M), range(M - 1, -1, -1)):
            for i in order:
                j = idx(i + 1)
                d = rh[j] - rh[i]
                if abs(d) > 0.5:
                    rh[j] = rh[i] + math.copysign(0.5, d)

    # Doom coordinates: x east, y north (track z points south)
    PX, PY = X * S, -Z * S
    T = np.array([(PX[idx(i + 1)] - PX[idx(i - 1)], PY[idx(i + 1)] - PY[idx(i - 1)]) for i in range(M)])
    T /= np.linalg.norm(T, axis=1)[:, None]
    NL = np.stack([-T[:, 1], T[:, 0]], axis=1)             # left of the direction of travel
    arc = np.zeros(M)
    for i in range(1, M):
        arc[i] = arc[i - 1] + math.hypot(X[i] - X[i - 1], Z[i] - Z[i - 1])
    length = arc[-1] + math.hypot(X[0] - X[-1], Z[0] - Z[-1])
    hw = W / 2

    # how far the roadside may reach on each side before it would fold over itself on the
    # inside of a bend or run into another part of the track
    full = KERB + NEAR + FAR
    lim = np.full((M, 2), 0.0)
    for i in range(M):
        for s, sg in enumerate((1, -1)):
            reach = hw[i] + full
            for j in range(0, M, 2):                        # another stretch of road nearby
                da = abs(arc[i] - arc[j]); da = min(da, length - da)
                if da < 70: continue
                d = math.hypot(X[i] - X[j], Z[i] - Z[j])
                reach = min(reach, d / 2 - RIM - 0.5)
            a, b = idx(i - 3), idx(i + 3)                   # the inside of a bend
            cross = T[a][0] * T[b][1] - T[a][1] * T[b][0]
            turn = math.asin(max(-1, min(1, cross)))
            if turn * sg > 0.02:
                chord = (arc[b] - arc[a]) % length
                reach = min(reach, 0.75 * chord / abs(turn) - RIM)
            lim[i, s] = max(reach, hw[i] + 0.2)
    for _ in range(3):   # no sudden changes from one cross-section to the next
        for order in (range(M), range(M - 1, -1, -1)):
            for i in order:
                j = idx(i + 1)
                for s in (0, 1):
                    lim[j, s] = min(lim[j, s], lim[i, s] + 0.8)

    def stations(i):
        """Offsets (metres, + = left) of the cross-section's points, left rim to right rim."""
        out = []
        for s, sg in ((0, 1), (1, -1)):
            room = lim[i, s] - hw[i]
            o = [hw[i], hw[i] + min(KERB, room), hw[i] + min(KERB + NEAR, room), hw[i] + min(full, room)]
            o.append(o[-1] + RIM)
            out.append([sg * v for v in o])
        return out[0][::-1] + out[1]          # 10 points: Lrim Lfar Lnear Lkerb Ledge | Redge Rkerb Rnear Rfar Rrim

    BANDS = ['rim', 'far', 'near', 'kerb', 'road', 'kerb', 'near', 'far', 'rim']

    def points():
        st = [stations(i) for i in range(M)]
        return [[(round(PX[i] + NL[i][0] * o * S), round(PY[i] + NL[i][1] * o * S)) for o in st[i]] for i in range(M)]

    # shrink the roadside wherever two pieces of it would still overlap
    for attempt in range(80):
        P = points()
        polys, owner = [], []
        for i in range(M):
            j = idx(i + 1)
            for b in range(9):
                q = [P[i][b], P[i][b + 1], P[j][b + 1], P[j][b]]
                if len(set(q)) < 3: continue
                polys.append(Polygon(q)); owner.append((i, b))
        tree = STRtree(polys)
        badsec = set()

        def blame(i, b):
            if b != 4: badsec.add((i, 0 if b < 4 else 1))
            else: badsec.update({(i, 0), (i, 1)})

        for k, pg in enumerate(polys):
            if not pg.is_valid:
                blame(*owner[k]); continue
            for m in tree.query(pg):
                if m <= k or not polys[m].is_valid: continue
                if pg.intersection(polys[m]).area > 1.5:
                    blame(*owner[k]); blame(*owner[m])
        if not badsec:
            break
        for (i, s) in badsec:
            for d in range(-3, 4):
                j = idx(i + d)
                lim[j, s] = max(hw[j] + 0.2, hw[j] + (lim[j, s] - hw[j]) * 0.85 - 0.15)
    else:
        print('warning: some roadside pieces still overlap', file=sys.stderr)
    print(f'roadside settled after {attempt} passes; narrowest side reaches '
          f'{min((lim[i, s] - hw[i]) for i in range(M) for s in (0, 1)):.1f} m beyond the road edge', file=sys.stderr)

    # ------------------------------------------------------------ start, gate and finish
    s0 = int(np.argmin((X - start_xz[0]) ** 2 + (Z - start_xz[1]) ** 2))
    rel = lambda i: (i - s0) % M                       # sections counted from the start line
    GA, EX, GB = (M - 22, M - 19), M - 14, (M - 9, M - 6)   # gate, finish line, gate
    flat_zone = lambda r: M - 26 <= r <= M - 2
    gate_h = float(np.mean([rh[idx(s0 + r)] for r in range(M - 26, M - 1)]))

    # ------------------------------------------------------------ sectors
    lv = Level()
    V = [[lv.vertex(*p) for p in row] for row in P]
    hi = lambda v: int(round(v * S))
    seg_road = [hi((rh[i] + rh[idx(i + 1)]) / 2) for i in range(M)]
    for i in range(M):
        if flat_zone(rel(i)): seg_road[i] = hi(gate_h)
    top = max(seg_road) + 900
    wander = [rnd.uniform(0, 1) for _ in range(M)]      # the cliff rim's height drifts along the road
    wander = [sum(wander[idx(i + d)] for d in range(-6, 7)) / 13 for i in range(M)]

    floors = {}
    for i in range(M):
        j = idx(i + 1)
        r = rel(i)
        zone = i // 20
        bridge = bool(B[i] or B[j])
        gate = GA[0] <= r < GA[1] or GB[0] <= r < GB[1]
        for b, kind in enumerate(BANDS):
            corners = [P[i][b], P[i][b + 1], P[j][b + 1], P[j][b]]
            cx = sum(c[0] for c in corners) / 4 / S; cz = -sum(c[1] for c in corners) / 4 / S
            g = ter.at(cx, cz)
            road = seg_road[i]
            sec = None
            if kind != 'rim' and gate:
                h = road + 112
                sec = lv.sector(h, top, 'FLAT23', 'F_SKY1', 160, 0, 666, 0, 'METAL2')
            elif kind == 'road':
                h = road
                sec = lv.sector(h, top, 'RROCK03', 'F_SKY1', 128, 0, 0, zone, 'SUPPORT3' if bridge else 'ASHWALL2')
            elif kind == 'kerb':
                h = road + 40 if bridge else road
                sec = lv.sector(h, top, 'FLAT19' if bridge else 'FLAT5_4', 'F_SKY1', 144, 0, 0, zone, 'METAL2' if bridge else 'ASHWALL2')
            else:
                if flat_zone(r): g = gate_h
                lava = g < LAVA + 0.1
                h = hi(LAVA) if lava else int(round(g * S / 8)) * 8
                if not bridge and not lava and abs(h - road) <= 8: h = road
                if kind == 'rim':
                    pass
                elif lava:
                    sec = lv.sector(h, top, 'LAVA1', 'F_SKY1', 208, 16, OFFROAD_TAG, zone, 'ROCKRED1')
                else:
                    sec = lv.sector(h, top, 'MFLR8_4' if kind == 'near' else 'FLAT5_7', 'F_SKY1', 104 if kind == 'near' else 88,
                                    0, OFFROAD_TAG, zone, 'ASHWALL2')
            floors[(i, b)] = h
            if sec is not None:
                lv.face([V[i][b], V[i][b + 1], V[j][b + 1], V[j][b]], sec)
    # rims: closed sky-ceilinged sectors, so the cliff ends against the sky instead of a wall
    for i in range(M):
        j = idx(i + 1)
        for b, fb in ((0, 1), (8, 7)):
            near_max = max(floors[(idx(i + d), fb)] for d in range(-2, 3))
            h = int(round((near_max + 150 + 170 * wander[i]) / 8)) * 8
            sec = lv.sector(h, h, 'FLAT5_7', 'F_SKY1', 88, 0, 0, 10000 + i * 2 + (b > 0), 'ASHWALL2')
            lv.face([V[i][b], V[i][b + 1], V[j][b + 1], V[j][b]], sec)

    # lines that do something
    for i in range(M):
        r = rel(i)
        for b in range(1, 8):
            if r == EX:
                lv.mark(V[i][b], V[i][b + 1], special=52)   # finish line: ends the level
            elif r % 75 == 40:
                lv.mark(V[i][b], V[i][b + 1], flags=64)     # gunfire carries a few hundred metres, not the whole lap

    # ------------------------------------------------------------ things
    def section(metres):
        a = (arc[s0] + metres) % length
        return int(np.searchsorted(arc, a, side='right') - 1) % M, a

    def place(metres, lat, kind, flags=7, facing=180):
        """Put a thing `metres` along the lap from the start line, `lat` metres left of the middle."""
        i, a = section(metres)
        j = idx(i + 1)
        t = min(1.0, (a - arc[i]) / max(1e-6, ((arc[j] - arc[i]) % length) or 1e-6))
        x = PX[i] + (PX[j] - PX[i]) * t + NL[i][0] * lat * S
        y = PY[i] + (PY[j] - PY[i]) * t + NL[i][1] * lat * S
        lv.thing(x, y, math.degrees(math.atan2(T[i][1], T[i][0])) + facing, kind, flags)

    half_width = lambda metres: hw[section(metres)[0]]
    on_bridge = lambda metres: bool(B[section(metres)[0]])

    place(0, 0, 1, 7, facing=0)                                           # the car
    for k in range(2, 5): place(-3 * (k - 1), 0, k, 7, facing=0)         # co-op starts, unused

    ZOMBIE, SHOTGUN, CHAIN, IMP, DEMON, SPECTRE, SOUL, CACO, KNIGHT, BARON, REV, FAT, PAIN, CYBER = \
        3004, 9, 65, 3001, 3002, 58, 3006, 3005, 69, 3003, 66, 67, 71, 16
    BARREL, MEDI, STIM, CLIPBOX, ROCKBOX, ARMOR1, ARMOR2, SOUL_SPHERE = 2035, 2012, 2011, 2048, 2046, 2018, 2019, 2013
    LAMP, TORCH_RED, FIRE_BARREL = 85, 46, 70
    radius = {DEMON: 30, SPECTRE: 30, SOUL: 16, CACO: 31, KNIGHT: 24, BARON: 24, FAT: 48, PAIN: 31, CYBER: 40, BARREL: 10}

    def group(metres, kinds, flags=7, spread=5.0):
        """A pack of monsters across the road, facing the oncoming car."""
        n = len(kinds)
        for k, kind in enumerate(kinds):
            room = half_width(metres) - (radius.get(kind, 20) + 10) / S
            lat = 0 if n == 1 else (-room + 2 * room * k / (n - 1)) * 0.9
            place(metres + (rnd.uniform(-spread, spread) if n > 1 else 0), lat, kind, flags)

    skills = lambda p: 7 if p < 0.55 else 6 if p < 0.82 else 4          # everyone, not on easy, hard only

    # mancubi hold the gate shut: spread round the lap, on the bridges where they fit
    fat_at = [0.13, 0.27, 0.41, 0.55, 0.69, 0.80, 0.90]
    fat_m = []
    for f, fl in zip(fat_at, [7, 6, 7, 7, 6, 7, 4]):
        m = f * length
        for d in range(0, 60, 4):                                         # nudge onto a bridge if one is close
            if on_bridge(m + d): m += d + 8; break
            if on_bridge(m - d): m -= d + 8; break
        fat_m.append(m)
        group(m, [FAT], fl)
        place(m - 14, half_width(m - 14) - 1.2, BARREL); place(m - 14, -(half_width(m - 14) - 1.2), BARREL)

    packs = [
        ([ZOMBIE, ZOMBIE, SHOTGUN], 1), ([IMP, IMP, IMP], 1), ([DEMON, DEMON], 1), ([SHOTGUN, ZOMBIE, SHOTGUN, ZOMBIE], 1),
        ([IMP, SHOTGUN, IMP], 1), ([DEMON, DEMON, DEMON], 2), ([CHAIN, CHAIN], 2), ([SOUL, SOUL, SOUL], 2), ([CACO], 2),
        ([IMP, IMP, IMP, IMP], 2), ([KNIGHT], 2), ([REV], 3), ([CACO, CACO], 3), ([CHAIN, SHOTGUN, CHAIN], 3),
        ([KNIGHT, IMP, KNIGHT], 3), ([REV, REV], 3), ([SPECTRE, DEMON, SPECTRE], 2), ([BARON], 3), ([PAIN], 3),
    ]
    m, n = 55.0, 0
    while m < length - 95:
        prog = m / length
        tier = 1 if prog < 0.22 else 2 if prog < 0.55 else 3
        kinds, _ = rnd.choice([p for p in packs if tier - 1 <= p[1] <= tier])
        if not any(abs(m - f) < 22 for f in fat_m):
            group(m, kinds, skills(rnd.random() if n > 1 else 0))
            if rnd.random() < 0.35:
                place(m + 6, rnd.choice((-1, 1)) * (half_width(m + 6) - 1.0), BARREL)
            n += 1
        m += rnd.uniform(17, 30) if tier > 1 else rnd.uniform(24, 36)
    group(length * 0.955, [CYBER], 4)                                     # hard only: the last straight is guarded

    # supplies in the middle of the road, where a car can run over them
    m, k = 40.0, 0
    while m < length - 60:
        place(m, rnd.uniform(-1.2, 1.2), [CLIPBOX, MEDI, ROCKBOX, CLIPBOX, MEDI, ROCKBOX, STIM][k % 7]); k += 1
        m += rnd.uniform(38, 55)
    place(length * 0.33, 0, ARMOR1); place(length * 0.66, 0, ARMOR2); place(length * 0.5, 0, SOUL_SPHERE)

    # lamps on the kerb, alternating sides; torches on the bridges
    m, side = 12.0, 1
    while m < length - 8:
        i = section(m)[0]
        if lim[i, 0 if side > 0 else 1] - hw[i] > 1.4:
            place(m, side * (hw[i] + 0.75), TORCH_RED if on_bridge(m) else FIRE_BARREL if int(m / 46) % 3 == 0 else LAMP)
        side = -side; m += 46
    lv.write(out, mapname)


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2], *(sys.argv[3:4]))
