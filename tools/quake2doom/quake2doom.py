"""Convert a Quake 1 .map (brush source) into a vanilla Doom map (Doom 1 format, ExMy).

Doom maps are 2.5D: a 2D floor plan of sectors, each with one floor and one ceiling
height. Quake maps are full 3D brushes. The conversion:

  1. Turn every brush into a convex solid; project it to a 2D footprint.
  2. Overlay all footprints (plus contour lines on sloped surfaces) into a planar
     arrangement of small faces. Inside one face, the vertical "column" of solid and
     empty space is the same everywhere.
  3. For each face, list the air gaps in its column (floor..ceiling).
  4. Walk from the player start (and other seeds) to adjacent faces, choosing the gap a
     Doom player could actually move into (step <= 24, headroom >= 56). Where Quake has a
     room above another room, the first one reached wins.
  5. Group faces with identical properties into sectors, emit linedefs where properties
     change, and convert entities (monsters, items, doors, lifts, exit, teleporter, lights).
"""
import json, math, os, re, struct, sys
from collections import defaultdict, deque

import numpy as np
from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

EPS = 0.05
STEP_UP = 24
HEADROOM = 56
WATER_JUMP = 72  # how far above the water a swimming Quake player can climb out


# ---------------------------------------------------------------- .map parsing
def parse_map(text):
    ents, i, n = [], 0, len(text)
    tok = re.compile(r'"[^"]*"|\(|\)|\{|\}|[^\s(){}"]+')
    toks = tok.findall(re.sub(r'//[^\n]*', '', text))
    p = 0

    def nxt():
        nonlocal p
        t = toks[p]; p += 1; return t

    while p < len(toks):
        assert nxt() == '{'
        ent = {'kv': {}, 'brushes': [], 'patches': []}
        while True:
            t = nxt()
            if t == '}':
                break
            if t.startswith('"'):
                ent['kv'][t[1:-1]] = nxt()[1:-1]
            elif t == '{':
                if toks[p] == 'patchDef2':
                    # Quake III curved surface: a grid of control points
                    p += 2
                    tex = nxt()
                    assert nxt() == '('
                    w, h = int(nxt()), int(nxt())
                    while nxt() != ')':
                        pass
                    assert nxt() == '('
                    grid = []
                    for _ in range(w):
                        assert nxt() == '('
                        row = []
                        for _ in range(h):
                            assert nxt() == '('
                            v = [float(nxt()) for _ in range(5)]
                            assert nxt() == ')'
                            row.append(v[:3])
                        assert nxt() == ')'
                        grid.append(row)
                    assert nxt() == ')'
                    assert nxt() == '}' and nxt() == '}'
                    ent['patches'].append((tex, np.array(grid)))
                    continue
                faces = []
                while True:
                    t = nxt()
                    if t == '}':
                        break
                    pts = []
                    for _ in range(3):
                        assert t == '('
                        pts.append([float(nxt()), float(nxt()), float(nxt())])
                        assert nxt() == ')'
                        t = nxt()
                    tex = t
                    # Quake 1 has 5 numbers after the texture, Quake III has 8
                    n0 = p
                    while toks[p] not in ('(', '}'):
                        p += 1
                    if p - n0 >= 8:
                        ent['q3'] = True
                    faces.append((np.array(pts), tex))
                ent['brushes'].append(faces)
        ents.append(ent)
    return ents


# ---------------------------------------------------------------- brushes
class Brush:
    def __init__(self, faces, sign):
        self.planes = []  # (n, d, tex) with solid where n.x <= d
        for pts, tex in faces:
            n = np.cross(pts[1] - pts[0], pts[2] - pts[0]) * sign
            L = np.linalg.norm(n)
            if L < 1e-9:
                continue
            n = n / L
            self.planes.append((n, float(n @ pts[0]), tex))
        self.verts = self._verts()
        self.texs = {t for _, _, t in self.planes}

    def _verts(self):
        P = self.planes; out = []
        for i in range(len(P)):
            for j in range(i + 1, len(P)):
                for k in range(j + 1, len(P)):
                    A = np.array([P[i][0], P[j][0], P[k][0]])
                    if abs(np.linalg.det(A)) < 1e-9:
                        continue
                    x = np.linalg.solve(A, np.array([P[i][1], P[j][1], P[k][1]]))
                    if all(n @ x <= d + 0.01 for n, d, _ in P):
                        out.append(x)
        return np.array(out) if out else np.zeros((0, 3))

    def footprint(self):
        if len(self.verts) < 3:
            return None
        from shapely.geometry import MultiPoint
        h = MultiPoint([(round(v[0]), round(v[1])) for v in self.verts]).convex_hull
        return h if h.geom_type == 'Polygon' and h.area > 0.5 else None

    def zrange_at(self, x, y):
        lo, hi = -1e9, 1e9
        lo_tex = hi_tex = None
        for n, d, tex in self.planes:
            r = d - n[0] * x - n[1] * y
            if abs(n[2]) < 1e-6:
                if r < -EPS:
                    return None
                continue
            z = r / n[2]
            if n[2] > 0:
                if z < hi:
                    hi, hi_tex = z, tex
            else:
                if z > lo:
                    lo, lo_tex = z, tex
        if hi - lo < 0.5:
            return None
        return lo, hi, lo_tex, hi_tex  # lo_tex = underside texture, hi_tex = top texture

    def slope_contours(self, step=8):
        """Lines across sloped faces every `step` units of height, so ramps become stairs."""
        segs = []
        if len(self.verts) < 3:
            return segs
        for n, d, tex in self.planes:
            if 0.05 < abs(n[2]) < 0.95:
                zs = self.verts[:, 2]
                for z in np.arange(math.floor(zs.min() / step) * step + step, zs.max(), step):
                    # line n.x*x + n.y*y = d - n.z*z clipped to footprint
                    fp = self.footprint()
                    if fp is None:
                        continue
                    c = d - n[2] * z
                    a, b = n[0], n[1]
                    big = 1e5
                    if abs(b) > abs(a):
                        p1 = (-big, (c + a * big) / b); p2 = (big, (c - a * big) / b)
                    else:
                        p1 = ((c + b * big) / a, -big); p2 = ((c - b * big) / a, big)
                    seg = LineString([p1, p2]).intersection(fp)
                    if seg.geom_type == 'LineString' and seg.length > 1:
                        segs.append(seg)
        return segs


class Block:
    """A plain box-like solid: a footprint with one bottom and one top height.
    Quake III's curved patches are flattened into these."""
    planes = ()

    def __init__(self, fp, lo, hi, tex):
        self.fp, self.lo, self.hi, self.tex = fp, lo, hi, tex
        self.texs = {tex}
        self.verts = np.zeros((8, 3))

    def footprint(self):
        return self.fp

    def zrange_at(self, x, y):
        return self.lo, self.hi, self.tex, self.tex

    def slope_contours(self, step=8):
        return []


def patch_blocks(tex, grid, cells=4, wall=8, slab=8, zstep=8):
    """Flatten a curved patch (a grid of quadratic Bezier control points) into blocks.
    The surface is cut into `cells` pieces per curve each way; each piece becomes a box:
    a thin upright wall segment where the surface is vertical, a flat slab elsewhere."""
    from shapely.geometry import MultiPoint

    def bez(c, t):
        return (1 - t) ** 2 * c[0] + 2 * t * (1 - t) * c[1] + t * t * c[2]

    W, H = grid.shape[:2]
    us = [(i, k / cells) for i in range(0, W - 2, 2) for k in range(cells)] + [(W - 3, 1.0)]
    vs = [(j, k / cells) for j in range(0, H - 2, 2) for k in range(cells)] + [(H - 3, 1.0)]
    pts = np.array([[bez([bez(grid[i + a, j:j + 3], v) for a in range(3)], u) for j, v in vs] for i, u in us])
    out = []
    for a in range(len(us) - 1):
        for b in range(len(vs) - 1):
            q = pts[a:a + 2, b:b + 2].reshape(4, 3)
            lo, hi = float(q[:, 2].min()), float(q[:, 2].max())
            xy = [(round(float(x)), round(float(y))) for x, y in q[:, :2]]
            hull = MultiPoint(xy).convex_hull
            if hull.geom_type == 'Polygon' and hull.area > 16:
                lo, hi = lo - slab, math.ceil(hi / zstep - 1e-6) * zstep  # a slab under the surface
            elif hull.geom_type in ('LineString', 'Polygon') and hi - lo >= 1:
                hull = hull.buffer(wall / 2, cap_style=2, join_style=2)  # an upright strip
                hull = Polygon([(round(x), round(y)) for x, y in hull.exterior.coords])
            else:
                continue
            if hull.is_valid and hull.area > 4:
                out.append(Block(orient(hull), lo, hi, tex))
    return out


def find_sign(ents):
    for e in ents:
        for b in e['brushes']:
            if len(Brush(b, 1).verts) >= 4:
                return 1
            if len(Brush(b, -1).verts) >= 4:
                return -1
    return 1


# ---------------------------------------------------------------- texture mapping
def load_names(path):
    j = json.load(open(path))
    return {t[0] for t in j['textures']}, set(j['flats'])


# Quake III brushes that are not walls: triggers, hints and clips (the map's only player
# clips are far above the play area, like Quake 1's)
Q3_NONSOLID = {'common/' + n for n in ('clip', 'botclip', 'trigger', 'clusterportal', 'missileclip', 'hint',
                                       'skip', 'areaportal', 'donotenter', 'origin', 'nodrop')}

WALL_RULES = [
    (r'^SWITCH|^SW', 'SW1COMP'), (r'^\+\d?BUTN|^\+\dSW', 'SW1COMP'),
    (r'^EXIT', 'EXITDOOR'),
    # Quake's medieval set (episodes 1-4): swamp stone, metal trim, brick, rock, wood
    (r'^WSWAMP', 'AROCK2'), (r'^WIZMET|^WMET', 'METAL1'), (r'^WBRICK', 'BRICK7'),
    (r'^ROCK', 'ROCK3'), (r'^WIZWOOD|^WWOOD', 'WOOD9'), (r'^WGRND', 'BROWN144'), (r'^COP', 'MARBGRAY'),
    (r'DOOR', 'BIGDOOR2'), (r'^SLIP', 'SUPPORT2'), (r'LIGHT|LITE', 'LITE5'),
    (r'^COMP', 'COMPTALL'), (r'^CRATE', 'CRATE3'),
    (r'^TECH04_3', 'STARTAN3'), (r'^TECH04', 'STARTAN2'), (r'^TECH08', 'STARGR1'),
    (r'^TECH0[17]', 'STARG3'), (r'^TECH', 'TEKWALL4'),
    (r'^UWALL', 'BROWN1'), (r'^TWALL2', 'GRAY5'), (r'^TWALL', 'GRAY4'),
    (r'^ECOP', 'BROWNGRN'), (r'^GROUND', 'BROWN144'), (r'^METAL|^MET', 'METAL1'),
    (r'^WIZ|^CITY|^BRICK', 'STONE2'), (r'^\*', 'SLADWALL'),
]
FLOOR_RULES = [
    (r'^\*SLIME', 'NUKAGE1'), (r'^\*LAVA', 'LAVA1'), (r'^\*', 'FWATER1'), (r'^SKY', 'F_SKY1'),
    (r'LIGHT|LITE', 'TLITE6_4'), (r'^SLIP', 'FLOOR1_6'), (r'^SFLOOR', 'FLOOR4_8'),
    (r'^GROUND', 'FLOOR5_2'), (r'^ECOP', 'FLOOR5_1'), (r'^TECH', 'FLAT5_4'), (r'^CRATE', 'CRATOP1'),
]
CEIL_RULES = [
    (r'^SKY', 'F_SKY1'), (r'LIGHT|LITE', 'TLITE6_4'), (r'^TECH', 'CEIL5_1'),
    (r'^ECOP|^GROUND', 'CEIL3_5'),
]


# Quake III / OpenArena texture paths (matched upper-case) to Doom II names
Q3_WALL_RULES = [
    (r'SKY', 'STONE2'), (r'JUMPPAD|TELEPORT', 'SILVER1'), (r'GLOW|LIGHT', 'GRAY7'),
    (r'GRASS', 'ZIMMER2'), (r'MOSS', 'STONE3'), (r'WALL_STONE', 'STONE2'), (r'CONC', 'GRAYBIG'),
    (r'PEWTER', 'SHAWN2'), (r'METFLOOR_BLOCK', 'METAL2'), (r'METAL', 'METAL1'), (r'WHITE', 'GRAY1'),
]
Q3_FLOOR_RULES = [
    (r'SKY', 'F_SKY1'), (r'LAVA', 'LAVA1'), (r'SLIME', 'NUKAGE1'), (r'WATER', 'FWATER1'),
    (r'GRASS|MOSS', 'GRASS1'), (r'WALL_STONE', 'MFLR8_1'), (r'CONC', 'FLAT5_4'), (r'PEWTER', 'FLOOR0_3'),
    (r'METFLOOR_BLOCK', 'CEIL5_1'), (r'METAL', 'FLOOR4_8'), (r'WHITE', 'FLAT19'),
]
Q3_CEIL_RULES = [(r'SKY', 'F_SKY1'), (r'METAL|METFLOOR', 'CEIL5_1'), (r'PEWTER', 'FLAT23')]


# Quake III items to Doom II things. The shotgun becomes the super shotgun.
Q3_THING = {
    'info_player_start': 1,
    'weapon_shotgun': 82, 'weapon_rocketlauncher': 2003, 'weapon_grenadelauncher': 2003,
    'weapon_plasmagun': 2004, 'weapon_lightning': 2002, 'weapon_railgun': 2004, 'weapon_bfg': 2006,
    'weapon_machinegun': 2002,
    'ammo_shells': 2049, 'ammo_rockets': 2046, 'ammo_grenades': 2046, 'ammo_cells': 17,
    'ammo_lightning': 2048, 'ammo_bullets': 2048, 'ammo_slugs': 2047, 'ammo_bfg': 17,
    'item_health': 2012, 'item_health_large': 2012, 'item_health_small': 2011, 'item_health_mega': 2013,
    'item_armor_shard': 2015, 'item_armor_combat': 2018, 'item_armor_body': 2019,
    'item_quad': 2023, 'item_regen': 2013, 'item_haste': 2023, 'item_invis': 2024, 'item_enviro': 2025,
}

# The arena's monsters, per part of the map: (Doom II thing, radius, easy, medium, hard).
# 65 chaingunner, 66 revenant, 67 mancubus, 68 arachnotron, 64 arch-vile.
ARENA_ROSTER = {
    'pit':   [(68, 64, 1, 1, 2), (67, 48, 1, 1, 2), (66, 20, 1, 2, 3), (65, 20, 1, 2, 3)],
    'mid':   [(65, 20, 2, 4, 6), (66, 20, 2, 3, 5), (67, 48, 1, 3, 3), (64, 20, 1, 2, 2), (68, 64, 1, 2, 2)],
    'ledge': [(66, 20, 2, 3, 4), (65, 20, 2, 2, 3), (67, 48, 1, 1, 2), (64, 20, 1, 1, 2)],
}
# extra supplies per part: box of shells, 4 shells, medikit, box of rockets
ARENA_SUPPLY = {'pit': (4, 6, 3, 1), 'mid': (8, 8, 4, 2), 'ledge': (4, 4, 2, 1)}


def mapper(rules, default, valid):
    cache = {}

    def f(tex):
        if tex is None:
            return default
        k = tex.upper()
        if k not in cache:
            out = default
            for pat, name in rules:
                if re.search(pat, k):
                    out = name; break
            cache[k] = out if out in valid else default
        return cache[k]
    return f


# ---------------------------------------------------------------- arena population
def populate_arena(things, faces, face_sector, sectors, face_pt, ground_faces, scenery, limit=None, floor_ceil=None):
    """Fill a deathmatch arena with a Doom II fight: monsters spread over every part of the
    map (well away from the player start), plus shells, rockets and health.
    `limit` caps the monster count (for small test builds)."""
    start = next(t for t in things if t['type'] == 1)
    sp = Point(start['x'], start['y'])
    # flat open floor, by height
    groups, fheight = defaultdict(list), {}
    for fi, si in face_sector.items():
        if sectors[si]['kind'] is None and fi not in scenery:
            groups[(sectors[si]['floor'], fi in ground_faces)].append(faces[fi])
    start_floor = None
    regions = {}
    for (z, gr), ps in groups.items():
        regions[(z, gr)] = unary_union(ps)
        if start_floor is None and regions[(z, gr)].buffer(1).contains(sp):
            start_floor = z
    top = max(z for z, _ in regions)

    def part(z, gr):
        if z <= start_floor + 96:
            return 'pit'
        return 'ledge' if z >= top - 96 else 'mid'

    def spots(part_name, clear):
        out = []
        for (z, gr), reg in regions.items():
            if part(z, gr) != part_name:
                continue
            inner = reg.buffer(-(clear + 8))
            if inner.is_empty:
                continue
            x0, y0, x1, y1 = inner.bounds
            for x in range(int(x0 // 48) * 48, int(x1) + 48, 48):
                for y in range(int(y0 // 48) * 48, int(y1) + 48, 48):
                    if inner.contains(Point(x, y)):
                        out.append((x, y))
        return out

    eye = floor_ceil(start['x'], start['y'])[0] + 41 if floor_ceil else 0

    def sees_start(c):
        """Could a monster standing at c see the player at the start? Follows the straight
        line between them over the floors and under the ceilings in between (head and feet)."""
        if floor_ceil is None:
            return True
        here = floor_ceil(c[0], c[1])
        if here is None:
            return True
        n = max(2, int(math.hypot(c[0] - start['x'], c[1] - start['y']) / 6))
        for tz in (here[0] + 8, here[0] + 56):
            clear = True
            for k in range(1, n):
                t = k / n
                fc = floor_ceil(start['x'] + (c[0] - start['x']) * t, start['y'] + (c[1] - start['y']) * t)
                z = eye + (tz - eye) * t
                if fc is None or not (fc[0] <= z <= fc[1]):
                    clear = False; break
            if clear:
                return True
        return False

    taken = [(t['x'], t['y'], 20) for t in things]
    new = []

    def place(part_name, radius, count, far, make, hidden=False):
        """Put `count` things on free spots, each as far as possible from everything placed.
        With `hidden`, spots out of sight of the player start are used while they last."""
        cand = [c for c in spots(part_name, radius) if sp.distance(Point(c)) >= far]
        seen = []
        if hidden:
            seen = [c for c in cand if sees_start(c)]
            cand = [c for c in cand if c not in set(seen)]
        for k in range(count):
            best, bd = None, -1
            for c in cand:
                d = min((math.hypot(c[0] - x, c[1] - y) - r - radius for x, y, r in taken), default=1e9)
                if d > bd:
                    best, bd = c, d
            if (best is None or bd < 8) and seen:
                cand, seen = seen, []  # no hidden room left: stand in view, back turned
                for c in cand:
                    d = min((math.hypot(c[0] - x, c[1] - y) - r - radius for x, y, r in taken), default=1e9)
                    if d > bd:
                        best, bd = c, d
            if best is None or bd < 8:
                return k
            taken.append((best[0], best[1], radius))
            new.append(make(best, k))
        return count

    def facing(c):
        # A monster notices the player only in front of it, or on hearing a shot. Those that
        # could see the start stand with their backs to it, so the fight begins with the
        # first shot; the rest face the start, like guards looking into the arena.
        dx, dy = c[0] - start['x'], c[1] - start['y']
        if not sees_start(c):
            dx, dy = -dx, -dy
        return int(round(math.degrees(math.atan2(dy, dx)) / 45.0) * 45) % 360

    placed = defaultdict(int)
    if limit is not None:
        # a handful of monsters for a test build, wherever there is room
        order = [(65, 20), (66, 20), (67, 48), (65, 20), (66, 20), (68, 64), (64, 20)]
        for typ, rad in (order * 8)[:limit]:
            for pn in ('mid', 'pit', 'ledge', 'yard'):
                if place(pn, rad, 1, 320, lambda c, k, typ=typ: {'x': c[0], 'y': c[1], 'angle': facing(c), 'type': typ, 'flags': 7}, hidden=True):
                    placed[typ] += 1
                    break
    else:
        for pn, roster in ARENA_ROSTER.items():
            for typ, rad, easy, med, hard in sorted(roster, key=lambda r: -r[1]):
                def make(c, k, typ=typ, easy=easy, med=med):
                    return {'x': c[0], 'y': c[1], 'angle': facing(c), 'type': typ, 'flags': 7 if k < easy else (6 if k < med else 4)}
                placed[typ] += place(pn, rad, hard, 448, make, hidden=True)
    for pn, (boxes, shells, medikits, rockets) in ARENA_SUPPLY.items():
        for typ, n in ((2049, boxes), (2008, shells), (2012, medikits), (2046, rockets)):
            if limit is not None:
                n = min(n, 1)
            place(pn, 20, n, 96, lambda c, k, typ=typ: {'x': c[0], 'y': c[1], 'angle': 0, 'type': typ, 'flags': 7})
    # by the player start: the super shotgun, shells and a backpack
    a = math.radians(start['angle'])
    for typ, fwd, side in ((82, 72, 0), (2049, 72, 40), (8, 72, -40)):
        x = start['x'] + fwd * math.cos(a) - side * math.sin(a)
        y = start['y'] + fwd * math.sin(a) + side * math.cos(a)
        if any(reg.contains(Point(x, y)) for reg in regions.values()):
            new.append({'x': int(round(x)), 'y': int(round(y)), 'angle': 0, 'type': typ, 'flags': 7})
    print('arena monsters', dict(placed), 'things added', len(new), file=sys.stderr)
    return new


def simplify_lines(m):
    """Join runs of short collinear linedefs that share everything but their length, and
    drop the vertices no line uses any more."""
    V, L, S = m['vertexes'], m['linedefs'], m['sidedefs']

    def sig(l):
        f = S[l['front']]
        b = S[l['back']] if l['back'] != 0xFFFF else None
        t = lambda d: None if d is None else (d['upper'], d['lower'], d['mid'], d['sector'])
        return (l['flags'], l['special'], l['tag'], t(f), t(b))

    changed = True
    while changed:
        changed = False
        at = defaultdict(list)
        for i, l in enumerate(L):
            if l is not None:
                at[l['v1']].append(i); at[l['v2']].append(i)
        for v, ls in at.items():
            if len(ls) != 2:
                continue
            a, b = L[ls[0]], L[ls[1]]
            if a is None or b is None or a is b or sig(a) != sig(b) or a['special']:
                continue
            if a['v2'] == v and b['v1'] == v:
                first, second = a, b
            elif b['v2'] == v and a['v1'] == v:
                first, second = b, a
            else:
                continue
            p, q, r = V[first['v1']], V[v], V[second['v2']]
            if first['v1'] == second['v2'] or (q[0] - p[0]) * (r[1] - q[1]) != (q[1] - p[1]) * (r[0] - q[0]):
                continue
            if (q[0] - p[0]) * (r[0] - q[0]) + (q[1] - p[1]) * (r[1] - q[1]) <= 0:
                continue
            first['v2'] = second['v2']
            L[L.index(second)] = None
            changed = True
    L = [l for l in L if l is not None]
    used = sorted({l['v1'] for l in L} | {l['v2'] for l in L})
    remap = {v: i for i, v in enumerate(used)}
    for l in L:
        l['v1'], l['v2'] = remap[l['v1']], remap[l['v2']]
    usedS = []
    for l in L:
        for k in ('front', 'back'):
            if l[k] != 0xFFFF:
                usedS.append(S[l[k]]); l[k] = len(usedS) - 1
    m['vertexes'], m['linedefs'], m['sidedefs'] = [V[v] for v in used], L, usedS


# ---------------------------------------------------------------- main conversion
def convert(map_path, names_path, out_json, mapname='E1M1', crop=None, monsters=None, exit_class='team_CTF_redflag',
            water_jump=False):
    ents = parse_map(open(map_path).read())
    sign = find_sign(ents)
    tex_ok, flat_ok = load_names(names_path)
    q3 = any(e.get('q3') for e in ents)  # Quake III dialect (OpenArena)
    if q3:
        wall_tex = mapper(Q3_WALL_RULES, 'STONE2', tex_ok)
        floor_flat = mapper(Q3_FLOOR_RULES, 'FLAT5_4', flat_ok)
        ceil_flat = mapper(Q3_CEIL_RULES, 'FLAT5_4', flat_ok)
    else:
        wall_tex = mapper(WALL_RULES, 'STARTAN3', tex_ok)
        floor_flat = mapper(FLOOR_RULES, 'FLOOR4_8', flat_ok)
        ceil_flat = mapper(CEIL_RULES, 'CEIL3_5', flat_ok)

    world = ents[0]
    solids, liquids, doors, plats = [], [], [], []
    triggers = {'exit': [], 'teleport': []}
    warps = []  # teleporters (and Quake III jump pads): {'ent', 'kind', 'target'}
    for ei, e in enumerate(ents):
        cls = e['kv'].get('classname', '')
        if cls == 'trigger_teleport' or (q3 and cls == 'trigger_push'):
            warps.append({'ent': ei, 'kind': 'tele' if cls == 'trigger_teleport' else 'push',
                          'target': e['kv'].get('target')})
        if cls in ('worldspawn', 'func_group', 'func_static'):
            for tex, grid in e['patches']:
                if tex.lower() in Q3_NONSOLID:
                    continue
                for blk in patch_blocks(tex, grid):
                    blk.ent = ei
                    solids.append(blk)
        for bf in e['brushes']:
            b = Brush(bf, sign)
            if len(b.verts) < 4:
                continue
            b.fp = b.footprint()
            if b.fp is None:
                continue
            b.ent = ei
            if cls in ('worldspawn', 'func_wall', 'func_episodegate', 'func_bossgate', 'func_group', 'func_static'):
                if all(t.upper() in ('CLIP', 'TRIGGER') for t in b.texs):
                    continue
                if q3 and any(t.lower() in Q3_NONSOLID for t in b.texs):
                    continue
                if all(t.startswith('*') for t in b.texs) or (q3 and all(re.search(r'water|slime|lava', t) for t in b.texs)):
                    liquids.append(b)
                else:
                    solids.append(b)
            elif cls in ('func_door', 'func_door_secret'):
                doors.append(b)
            elif cls == 'func_plat':
                plats.append(b)
            elif cls == 'trigger_changelevel':
                triggers['exit'].append(b)
            elif cls in ('trigger_teleport', 'trigger_push'):
                if q3:
                    # Doom fires a teleporter when the player's centre crosses its edge, Quake
                    # when the player touches it: widen thin wall-mounted triggers to match
                    mr = b.fp.minimum_rotated_rectangle.exterior.coords
                    if min(math.dist(mr[0], mr[1]), math.dist(mr[1], mr[2])) < 48:
                        g = b.fp.buffer(24, join_style=2)
                        b.fp = orient(Polygon([(round(x), round(y)) for x, y in g.exterior.coords]))
                        b.zr = (float(b.verts[:, 2].min()), float(b.verts[:, 2].max()), None, None)
                triggers['teleport'].append(b)
    print(f'brushes: {len(solids)} solid, {len(liquids)} liquid, {len(doors)} door, {len(plats)} plat', file=sys.stderr)

    # 2. arrangement
    lines = []
    for b in solids + liquids + doors + plats + triggers['exit'] + triggers['teleport']:
        lines.append(LineString(list(b.fp.exterior.coords)))
    for b in solids:
        lines.extend(b.slope_contours())
    from shapely import set_precision
    if q3:
        # snap-rounded noding: every crossing gets a vertex on the 1-unit grid, so no face
        # of the plan is lost where many angled and curved pieces meet
        import shapely
        noded = shapely.unary_union(shapely.GeometryCollection(lines), grid_size=1.0)
    else:
        noded = set_precision(unary_union(lines), 1.0)
    # rebuild without the fixed 1-unit precision set_precision attaches (it would snap the
    # small buffers used for neighbour tests away)
    faces = [orient(Polygon(list(f.exterior.coords), [list(r.coords) for r in f.interiors]))
             for f in polygonize(noded) if f.area >= 1.0]
    print(f'faces: {len(faces)}', file=sys.stderr)

    def tree_of(bs):
        return (STRtree([b.fp for b in bs]), bs) if bs else (None, bs)
    T_sol, T_liq, T_door, T_plat = tree_of(solids), tree_of(liquids), tree_of(doors), tree_of(plats)
    T_exit, T_tele = tree_of(triggers['exit']), tree_of(triggers['teleport'])

    def hits(T, pt):
        tree, bs = T
        if tree is None:
            return []
        return [bs[i] for i in tree.query(pt, predicate='intersects') if bs[i].fp.buffer(-0.01).contains(pt)]

    # 3. columns
    face_pt, face_gaps, face_flags = [], [], []
    pool_depth = {}
    for f in faces:
        pt = f.representative_point()
        x, y = pt.x, pt.y
        iv = []
        for b in hits(T_sol, pt):
            r = b.zrange_at(x, y)
            if r:
                iv.append(r)
        iv.sort()
        merged = []
        for lo, hi, lt, ht in iv:
            if merged and lo <= merged[-1][1] + 0.5:
                if hi > merged[-1][1]:
                    merged[-1] = [merged[-1][0], hi, merged[-1][2], ht]
            else:
                merged.append([lo, hi, lt, ht])
        gaps = []
        for a, b in zip(merged, merged[1:]):
            if q3 and a[3] and 'sky' in a[3].lower():
                continue  # a sky "floor" is the edge of the world, not somewhere to stand
            gaps.append({'f': a[1], 'c': b[0], 'ftex': a[3], 'ctex': b[2]})
        liq = []
        for lb in hits(T_liq, pt):
            r = lb.zrange_at(x, y)
            if r:
                liq.append((id(lb), r))
        for g in gaps:
            for lid, (lo, hi, lt, ht) in liq:
                if lo <= g['f'] + 1 and g['f'] < hi < g['c']:
                    # Doom can't swim: the liquid surface becomes the floor. Each pool is
                    # lowered evenly (below) by whatever its lowest passage needs.
                    g['liq'] = (lid, hi, g['f'])
                    g['f'], g['ftex'] = hi, ht
                    need = HEADROOM - (g['c'] - hi)
                    # only real passages count (a gap of at least 40 above the surface);
                    # crawl spaces under slabs would otherwise sink whole pools
                    if 0 < need <= HEADROOM - 40:
                        pool_depth[lid] = max(pool_depth.get(lid, 0), min(need, hi - g['liq'][2]))
        flags = {}
        dh = hits(T_door, pt)
        if dh:
            ent = dh[0].ent
            if ents[ent]['kv'].get('angle') == '-2':
                # a Quake door that moves down is a platform/elevator: make it a Doom lift
                tops = [r[1] for r in (b.zrange_at(x, y) for b in dh if b.ent == ent) if r]
                if tops:
                    flags['plat'] = (ent, max(tops))
            else:
                flags['door'] = ent
        ph = hits(T_plat, pt)
        if ph:
            r = ph[0].zrange_at(x, y)
            if r:
                flags['plat'] = (ph[0].ent, r[1])
        if any(b.fp.buffer(72).contains(pt) for b in triggers['exit']):
            flags['exit'] = True  # includes the floor just in front of the slipgate
        th = [getattr(b, 'zr', None) or b.zrange_at(x, y) for b in hits(T_tele, pt)]
        th = [r for r in th if r]
        if th:
            flags['tele'] = (min(r[0] for r in th), max(r[1] for r in th))  # its height range
            flags['warp'] = hits(T_tele, pt)[0].ent
        face_pt.append((x, y)); face_gaps.append(gaps); face_flags.append(flags)

    for gaps in face_gaps:
        for g in gaps:
            if 'liq' in g:
                lid, top, bottom = g['liq']
                g['f'] = max(bottom, top - pool_depth.get(lid, 0))
    print('pool depths', sorted(pool_depth.values()), file=sys.stderr)

    # adjacency
    def key(p):
        return (int(round(p[0])), int(round(p[1])))
    edge_faces = defaultdict(list)
    for fi, f in enumerate(faces):
        rings = [f.exterior] + list(f.interiors)
        for ring in rings:
            cs = list(ring.coords)
            for a, b in zip(cs, cs[1:]):
                ka, kb = key(a), key(b)
                if ka != kb:
                    edge_faces[frozenset((ka, kb))].append(fi)
    # neighbours: faces sharing a stretch of boundary (robust to T-junctions)
    nbrs = defaultdict(set)
    _ft = STRtree(faces)
    for i, f in enumerate(faces):
        fb_buf = f.buffer(0.6)
        for j in _ft.query(fb_buf):
            j = int(j)
            if j != i and fb_buf.intersection(faces[j]).area > 0.6:
                nbrs[i].add(j); nbrs[j].add(i)

    # Thin strips (narrower than a player) between two floors at the same level are walked
    # straight over in both engines; give them that floor so the walk can cross them.
    added = 0
    for t, f in enumerate(faces):
        if not f.buffer(-12).is_empty:
            continue
        levels = defaultdict(list)
        for nb in nbrs[t]:
            for g in face_gaps[nb]:
                levels[int(round(g['f'] / 8.0))].append(g)
        for lv, gs in levels.items():
            if len(gs) < 2:
                continue
            F = max(g['f'] for g in gs)
            if any(abs(g['f'] - F) <= 8 for g in face_gaps[t]):
                continue
            for g in face_gaps[t]:
                if g['f'] < F and g['c'] - F >= HEADROOM:
                    src = max(gs, key=lambda q: q['f'])
                    face_gaps[t].append({'f': F, 'c': min(g['c'], min(q['c'] for q in gs)), 'ftex': src['ftex'], 'ctex': g['ctex']})
                    added += 1
                    break
    print('thin bridges', added, file=sys.stderr)

    # Quake players swim and hop out of water onto a ledge a little above the surface. Doom
    # can't, so a pool whose bank is up to WATER_JUMP above its surface is filled up until
    # that bank is a normal step out.
    if water_jump and not q3:
        lift = {}
        for a in range(len(faces)):
            for ga in face_gaps[a]:
                if 'liq' not in ga:
                    continue
                lid = ga['liq'][0]
                for b in nbrs[a]:
                    for gb in face_gaps[b]:
                        if 'liq' in gb:
                            continue
                        rise = gb['f'] - ga['f']
                        if STEP_UP < rise <= WATER_JUMP and min(ga['c'], gb['c']) - gb['f'] >= HEADROOM:
                            lift[lid] = max(lift.get(lid, -1e9), gb['f'] - STEP_UP)
        raised = 0
        for gaps in face_gaps:
            for g in gaps:
                if 'liq' in g and g['liq'][0] in lift and g['f'] < lift[g['liq'][0]]:
                    z = lift[g['liq'][0]]
                    if g['c'] - z >= HEADROOM:
                        g['f'] = z; raised += 1
        print('pools raised for water jumps', len(lift), raised, file=sys.stderr)

    face_tree = STRtree(faces)

    def face_at(x, y):
        pt = Point(x, y)
        for i in face_tree.query(pt, predicate='intersects'):
            if faces[i].covers(pt):
                return i
        return None

    # 4. reachability
    chosen = {}  # face -> gap

    def gap_for(fi, z):
        best = None
        for gi, g in enumerate(face_gaps[fi]):
            if g['f'] - 40 <= z <= g['c'] + 8 and g['c'] - g['f'] >= 32:
                if best is None or abs(g['f'] - z) < abs(face_gaps[fi][best]['f'] - z):
                    best = gi
        return best

    import heapq

    def moves(a, ga_i):
        """Doom-walkable moves from state (face a, gap ga_i): yields (b, gb, cost)."""
        ga = face_gaps[a][ga_i]
        up = STEP_UP
        if 'plat' in face_flags[a]:
            up = max(up, face_flags[a]['plat'][1] - ga['f'] + STEP_UP)
        for b in nbrs[a]:
            for gj, gb in enumerate(face_gaps[b]):
                rise = gb['f'] - ga['f']
                room = min(ga['c'], gb['c']) - max(ga['f'], gb['f'])
                if 'plat' in face_flags[b]:
                    rise = min(rise, 0)
                if rise <= up and room >= HEADROOM - 0.5:
                    yield b, gj, 1.0 + max(0.0, -rise) / 4.0 + max(0.0, rise) / 48.0

    def claim_from(heap):
        """Cheapest-path claim: each face keeps the floor reached by the cheapest route.
        Dropping down is expensive, so walkways and bridges win over the pits below them."""
        heapq.heapify(heap)
        while heap:
            cost, a, ga_i = heapq.heappop(heap)
            if a in chosen and chosen[a] != ga_i:
                continue
            if a in done_faces:
                continue
            done_faces.add(a)
            chosen[a] = ga_i
            for b, gj, st in moves(a, ga_i):
                if b not in chosen:
                    heapq.heappush(heap, (cost + st, b, gj))

    done_faces = set()
    seeds = []
    for e in ents:
        c = e['kv'].get('classname', '')
        if 'origin' in e['kv']:
            o = [float(v) for v in e['kv']['origin'].split()]
            if c == 'info_player_start':
                seeds.insert(0, o)
            elif c.startswith(('info_teleport_destination', 'monster_', 'item_', 'weapon_', 'info_player', 'ammo_', 'team_')):
                seeds.append(o)
    start_ent = None
    if q3:
        # no single-player start in an arena: use the lowest deathmatch spawn (the pit)
        dm = [e for e in ents if e['kv'].get('classname') == 'info_player_deathmatch']
        start_ent = min(dm, key=lambda e: float(e['kv']['origin'].split()[2]))
        seeds.insert(0, [float(v) for v in start_ent['kv']['origin'].split()])

    # ---- Quake III teleporters and jump pads: where each one puts the player down
    by_name = {e['kv']['targetname']: e for e in ents if 'targetname' in e['kv']}

    def resolve_warp(w, pick):
        """Where a teleporter or jump pad puts the player: (face, gap, x, y, angle) or None.
        `pick(face)` lists the floors that may be landed on."""
        t = by_name.get(w['target'])
        bs = [b for b in triggers['teleport'] if b.ent == w['ent']]
        if t is None or not bs:
            return None
        to = np.array([float(v) for v in t['kv']['origin'].split()])
        V = np.concatenate([b.verts for b in bs])
        o = (V.min(0) + V.max(0)) / 2
        if w['kind'] == 'tele':
            zmax, zmin, reach = to[2] + 8, -1e9, 96  # down to whatever floor was kept there
            ang = float(t['kv'].get('angle', 0))
        else:
            # A jump pad throws the player so the top of the arc is at its target. In the air
            # the player steers, so the landing is the highest floor near the top of the arc.
            zmax, zmin, reach = to[2] + 8, o[2] + 24, 128
            ang = math.degrees(math.atan2(to[1] - o[1], to[0] - o[0]))
        best = None
        here = Point(to[0], to[1])
        for fi in face_tree.query(here.buffer(reach)):
            fi = int(fi)
            if not q3 and any(k in face_flags[fi] for k in ('door', 'plat', 'exit', 'warp')):
                continue  # land on plain floor: a door, lift or another teleporter can't carry the tag
            for gi in pick(fi):
                g = face_gaps[fi][gi]
                if not (zmin <= g['f'] <= zmax) or g['c'] - g['f'] < HEADROOM:
                    continue
                inner = faces[fi].buffer(-20)
                d = faces[fi].distance(here) + (0 if not inner.is_empty else 40)
                if d > reach:
                    continue
                score = (round(g['f'] / 16), -d)
                if best is None or score > best[0]:
                    best = (score, fi, gi)
        if best is None:
            return None
        _, fi, gi = best
        # stand well inside the landing floor: the middle of the same-height floor nearby
        level = [faces[j] for j in map(int, face_tree.query(here.buffer(reach + 64)))
                 if any(abs(face_gaps[j][k]['f'] - face_gaps[fi][gi]['f']) < 1 for k in pick(j))]
        room = unary_union(level).buffer(-24)
        if room.is_empty:
            pt = faces[fi].representative_point()
        else:
            from shapely.ops import nearest_points
            pt = nearest_points(room, here)[0]
        return (fi, gi, float(round(pt.x)), float(round(pt.y)), ang)

    def walkable(fi):
        return [gi for gi, g in enumerate(face_gaps[fi]) if g['c'] - g['f'] >= HEADROOM]

    for w in warps:
        w['dest'] = resolve_warp(w, walkable)
    warp_of = {w['ent']: w for w in warps}

    def all_reachable(start_states):
        """Every (face, floor) a Doom player could get to: walking, dropping, and through
        teleporters and jump pads."""
        seen, stack = set(start_states), list(start_states)
        while stack:
            a, ga_i = stack.pop()
            nxts = [(b, gj) for b, gj, _ in moves(a, ga_i)]
            fl, g = face_flags[a], face_gaps[a][ga_i]
            if 'warp' in fl and g['f'] < fl['tele'][1] and g['f'] + HEADROOM > fl['tele'][0]:
                d = warp_of[fl['warp']]['dest']
                if d is not None:
                    nxts.append((d[0], d[1]))
            for st in nxts:
                if st not in seen:
                    seen.add(st); stack.append(st)
        return seen

    if q3:
        # Arena maps stack walkways. Doom can't, so per spot one floor is kept:
        #   - the open ground the whole structure stands on gives way to anything built over it;
        #   - among stacked walkways the lower one is kept, and the space above it is opened
        #     up, so what is left of the upper walkway becomes a raised ledge overlooking it.
        s0 = seeds[0]
        sf = face_at(s0[0], s0[1]); sg = gap_for(sf, s0[2] - 24)
        scenery = set()
        R = all_reachable([(sf, sg)])
        low = min(face_gaps[a][g]['f'] for a, g in R)
        ground = {(a, g) for a, g in R if face_gaps[a][g]['f'] <= low + 8}  # the flat bottom of the world
        per_face = defaultdict(list)
        for a, g in R:
            per_face[a].append(g)
        ledges = 0
        for a, gs in per_face.items():
            gs.sort(key=lambda g: face_gaps[a][g]['f'])
            built = [g for g in gs if (a, g) not in ground]
            keep = built[0] if built else gs[0]
            top = face_gaps[a][gs[-1]]
            if gs[-1] != keep:
                kept = dict(face_gaps[a][keep]); kept['c'], kept['ctex'] = top['c'], top['ctex']
                face_gaps[a].append(kept); keep = len(face_gaps[a]) - 1
                ledges += 1
            chosen[a] = keep
        # teleporters and jump pads aim at the floors that were kept
        for w in warps:
            w['dest'] = resolve_warp(w, lambda fi: [chosen[fi]] if fi in chosen else [])
        # Keep only what a Doom player can actually get to from the start, on the kept floors
        # (hollow space behind the arena's walls is reachable in the 3D map but not here).
        live, stack = {sf}, [sf]
        chosen[sf] = chosen.get(sf, sg)
        while stack:
            a = stack.pop()
            ga = face_gaps[a][chosen[a]]
            nxts = [b for b in nbrs[a] if b in chosen
                    and face_gaps[b][chosen[b]]['f'] - ga['f'] <= STEP_UP
                    and min(ga['c'], face_gaps[b][chosen[b]]['c']) - max(ga['f'], face_gaps[b][chosen[b]]['f']) >= HEADROOM - 0.5]
            fl = face_flags[a]
            if 'warp' in fl and ga['f'] < fl['tele'][1] and ga['f'] + HEADROOM > fl['tele'][0]:
                d = warp_of[fl['warp']]['dest']
                if d is not None:
                    nxts.append(d[0])
            for b in nxts:
                if b not in live:
                    live.add(b); stack.append(b)
        # Small pockets the player could drop into but never leave are filled up to their rim.
        back = defaultdict(set)
        for a in live:
            ga = face_gaps[a][chosen[a]]
            for b in nbrs[a]:
                if b in live and face_gaps[b][chosen[b]]['f'] - ga['f'] <= STEP_UP \
                        and min(ga['c'], face_gaps[b][chosen[b]]['c']) - max(ga['f'], face_gaps[b][chosen[b]]['f']) >= HEADROOM - 0.5:
                    back[b].add(a)
            fl = face_flags[a]
            if 'warp' in fl and ga['f'] < fl['tele'][1] and ga['f'] + HEADROOM > fl['tele'][0] and warp_of[fl['warp']]['dest']:
                back[warp_of[fl['warp']]['dest'][0]].add(a)
        home, stack = {sf}, [sf]
        while stack:
            for b in back[stack.pop()]:
                if b not in home:
                    home.add(b); stack.append(b)
        pockets = live - home
        for _ in range(64):
            if not pockets:
                break
            for a in list(pockets):
                rim = [face_gaps[b][chosen[b]]['f'] for b in nbrs[a] if b in home]
                if rim:
                    g = dict(face_gaps[a][chosen[a]]); g['f'] = max(g['f'], min(rim))
                    face_gaps[a].append(g); chosen[a] = len(face_gaps[a]) - 1
                    pockets.discard(a); home.add(a)
        chosen = {a: g for a, g in chosen.items() if a in live}
        # Scenery: wall tops, parapets and roofs nobody can walk onto still show under the
        # open sky, so the arena keeps its low walls and its view instead of sky-high ones.
        for a in range(len(faces)):
            sky = [gi for gi, g in enumerate(face_gaps[a]) if g['ctex'] and 'sky' in g['ctex'].lower()]
            if sky and a not in chosen:
                chosen[a] = sky[-1]; scenery.add(a)
        if crop:
            chosen = {a: g for a, g in chosen.items()
                      if crop[0] <= face_pt[a][0] <= crop[2] and crop[1] <= face_pt[a][1] <= crop[3]}
        route = [(sf, sg)]
        print(f'stacked spots resolved: {ledges}, walkable faces {len(live)}, scenery {len(scenery)}', file=sys.stderr)
        for w in warps:
            d = w['dest']
            print('warp', w['kind'], w['target'], '->', None if d is None else (round(d[2]), round(d[3]), round(face_gaps[d[0]][d[1]]['f'])), file=sys.stderr)
    else:
        # The critical route: cheapest walk from the player start to the exit, over all floor
        # levels. Its faces are locked to the floor the route uses (plus the walkway around it),
        # so the exit can't be cut off by a pit claiming the same spot.
        s0 = seeds[0]
        sf = face_at(s0[0], s0[1]); sg = gap_for(sf, s0[2] - 24)
        exit_states = {(i, k) for i in range(len(faces)) if face_flags[i].get('exit') for k in range(len(face_gaps[i]))}
        dist = {(sf, sg): 0.0}; prev = {(sf, sg): None}; heap = [(0.0, sf, sg)]; goal = None
        while heap:
            cst, a, ga_i = heapq.heappop(heap)
            if cst > dist.get((a, ga_i), 1e18):
                continue
            if (a, ga_i) in exit_states:
                goal = (a, ga_i); break
            steps = list(moves(a, ga_i))
            fl = face_flags[a]; g = face_gaps[a][ga_i]
            if 'warp' in fl and g['f'] < fl['tele'][1] and g['f'] + HEADROOM > fl['tele'][0]:
                d = warp_of[fl['warp']]['dest']
                if d is not None:
                    steps.append((d[0], d[1], 8.0))
            for b, gj, st in steps:
                nc = cst + st
                if nc < dist.get((b, gj), 1e18):
                    dist[(b, gj)] = nc; prev[(b, gj)] = (a, ga_i); heapq.heappush(heap, (nc, b, gj))
        route = [] if goal else [(sf, sg)]
        while goal:
            route.append(goal); goal = prev[goal]
        print(f'route to exit: {len(route)} faces', file=sys.stderr)
        for a, ga_i in route:
            chosen[a] = ga_i
        # widen the route to the full walkway: neighbours on the same level join it
        for a, ga_i in list(route):
            fa = face_gaps[a][ga_i]['f']
            for b, gj, st in moves(a, ga_i):
                if b not in chosen and abs(face_gaps[b][gj]['f'] - fa) <= 8:
                    chosen[b] = gj
        claim_from([(0.0, a, g) for a, g in chosen.items()])
        for o in seeds[1:]:
            fi = face_at(o[0], o[1])
            if fi is None or fi in chosen:
                continue
            gi = gap_for(fi, o[2] - 24)
            if gi is not None:
                claim_from([(0.0, fi, gi)])
        for w in warps:
            w['dest'] = resolve_warp(w, lambda fi: [chosen[fi]] if fi in chosen else [])
    print(f'reachable faces: {len(chosen)} / {len(faces)}', file=sys.stderr)

    # lights
    lights = []
    for e in ents:
        if e['kv'].get('classname', '').startswith('light') and 'origin' in e['kv']:
            o = [float(v) for v in e['kv']['origin'].split()]
            lights.append((o, float(e['kv'].get('light', 300))))
    L = np.array([l[0] for l in lights]) if lights else np.zeros((0, 3))
    Lv = np.array([l[1] for l in lights]) if lights else np.zeros(0)

    def light_raw(x, y, z):
        if not len(L):
            return 0.0
        d = np.linalg.norm(L - np.array([x, y, z]), axis=1)
        return float(np.clip(Lv - d, 0, None).sum())

    # Map Quake's light sum to Doom light levels by rank, so the level keeps its contrast:
    # the darkest spots end near 112, the brightest at 240.
    _raw = []
    for fi, gi in chosen.items():
        g = face_gaps[fi][gi]; x, y = face_pt[fi]
        _raw.append(light_raw(x, y, (g['f'] + g['c']) / 2))
    _sorted = np.sort(np.array(_raw)) if _raw else np.zeros(1)

    def light_at(x, y, z):
        r = light_raw(x, y, z)
        p = np.searchsorted(_sorted, r) / max(1, len(_sorted) - 1)
        if q3:
            return 160 + 32 * int(min(2, p * 3))  # three light levels keep the open arena cheap to draw
        return int(np.clip(round((112 + 128 * p) / 16) * 16, 112, 240))

    # Touching door pieces (Quake double doors are often two entities) become one Doom
    # door: a Doom door opens to the lowest ceiling around it, so two closed halves side by
    # side would hold each other shut.
    door_group = {}
    for fi in chosen:
        if 'door' not in face_flags[fi] or fi in door_group:
            continue
        gid = face_flags[fi]['door']; stack = [fi]; door_group[fi] = gid
        while stack:
            a = stack.pop()
            for b in nbrs[a]:
                if b in chosen and 'door' in face_flags[b] and b not in door_group:
                    door_group[b] = gid; stack.append(b)
    for fi, gid in door_group.items():
        face_flags[fi]['door'] = gid

    # one floor height per door / lift: the highest walkable floor among its pieces
    unit_floor = {}
    for fi, gi in chosen.items():
        fl = face_flags[fi]; g = face_gaps[fi][gi]
        if 'door' in fl:
            k = ('door', fl['door']); z = int(round(g['f']))
        elif 'plat' in fl:
            k = ('plat', fl['plat'][0]); z = int(round(fl['plat'][1]))
        else:
            continue
        unit_floor[k] = max(unit_floor.get(k, -32768), z)

    # 5. sectors
    tele_tag, door_tag = 1, None
    sec_key_index, sectors = {}, []
    face_sector = {}
    warp_tag, dest_faces, exit_faces = {}, {}, set()
    if q3:
        top_floor = max(face_gaps[fi][gi]['f'] for fi, gi in chosen.items() if fi not in scenery)
        sky_z = int(math.ceil((top_floor + 320) / 64.0) * 64)  # one sky height over the whole arena
    # each teleporter gets its own tag, shared only by teleporters landing on the same floor
    if True:
        for w in warps:
            if w['dest'] is None or w['dest'][0] not in chosen:
                continue
            dfi, dgi, dx, dy, _ = w['dest']
            if dfi in dest_faces:
                # two pads landing on the same piece of floor share its landing spot
                warp_tag[w['ent']] = warp_tag[dest_faces[dfi]]
                w['shared'] = True
                continue
            warp_tag[w['ent']] = 10 + len(set(warp_tag.values()))
            dest_faces[dfi] = w['ent']
            for j in map(int, face_tree.query(Point(dx, dy).buffer(20))):
                if j in chosen and abs(face_gaps[j][chosen[j]]['f'] - face_gaps[dfi][dgi]['f']) < 1 \
                        and 'warp' not in face_flags[j] and faces[j].distance(Point(dx, dy)) < 20:
                    dest_faces.setdefault(j, w['ent'])
    if q3:
        # The way out: a pad at the map's marked spot. On MAP07 Doom II lowers every sector
        # tagged 666 once the last mancubus dies, so the pad starts raised out of reach.
        for e in ents:
            if e['kv'].get('classname') == exit_class and 'origin' in e['kv']:
                o = [float(v) for v in e['kv']['origin'].split()]
                for j in map(int, face_tree.query(Point(o[0], o[1]).buffer(40))):
                    if j in chosen and j not in dest_faces and 'warp' not in face_flags[j] \
                            and faces[j].representative_point().distance(Point(o[0], o[1])) < 40:
                        exit_faces.add(j)
                break
    for fi, gi in chosen.items():
        g = face_gaps[fi][gi]; fl = face_flags[fi]
        fz, cz = int(round(g['f'])), int(round(g['c']))
        x, y = face_pt[fi]
        ff, cf = floor_flat(g['ftex']), ceil_flat(g['ctex'])
        light = light_at(x, y, (fz + cz) / 2)
        special, tag, kind = 0, 0, None
        if q3:
            fz = int(round(g['f'] / 8.0) * 8)
            cz = sky_z if cf == 'F_SKY1' else min(sky_z, int(math.ceil(g['c'] / 8.0) * 8))
            if fi in scenery and fz > sky_z - 64:
                continue  # taller than the sky: stays a plain wall
            if cz - fz < HEADROOM:
                cz = fz + HEADROOM
        if ff == 'NUKAGE1':
            special = 7
        elif ff == 'LAVA1':
            special = 5
        if 'door' in fl:
            kind = ('door', fl['door']); fz = cz = unit_floor[kind]
        elif 'plat' in fl:
            kind = ('plat', fl['plat'][0]); fz = unit_floor[kind]
        if 'exit' in fl:
            kind = ('exit',)
        if fl.get('warp') in warp_tag and g['f'] < fl['tele'][1] and g['f'] + HEADROOM > fl['tele'][0]:
            kind = ('tele', fl['warp'])  # only where the floor we kept is at the teleporter's height
        elif fi in dest_faces and not kind:
            kind = ('teledest', dest_faces[fi])
        if q3:
            kind = None
            if fl.get('warp') in warp_tag and g['f'] < fl['tele'][1] and g['f'] + HEADROOM > fl['tele'][0]:
                kind = ('tele', fl['warp'])
                ff = 'GATE4' if warp_of[fl['warp']]['kind'] == 'push' else 'GATE1'
                light = 255
            elif fi in dest_faces:
                kind = ('teledest', dest_faces[fi])
            elif fi in exit_faces:
                kind = ('exit',)
                ff, light = 'GATE3', 255
                if mapname == 'MAP07':
                    fz += 64
        # each door and lift is exactly one sector (one floor, flat and light), so it moves
        # as a unit and no closed half holds the other shut
        k = ('unit', kind) if kind and kind[0] in ('door', 'plat') else (fz, cz, ff, cf, light, special, kind)
        if k not in sec_key_index:
            sec_key_index[k] = len(sectors)
            sectors.append({'floor': fz, 'ceil': cz, 'fflat': ff, 'cflat': cf, 'light': light,
                            'special': special, 'tag': 0, 'kind': kind})
            if kind:
                sec = sectors[-1]
                if kind[0] == 'tele':
                    sec['warp_tag'] = warp_tag[kind[1]]
                elif kind[0] == 'teledest':
                    sec['tag'] = warp_tag[kind[1]]
                elif kind[0] == 'exit' and mapname == 'MAP07':
                    sec['tag'] = 666
        face_sector[fi] = sec_key_index[k]

    # Lifts find their sector by tag (tag 0 would mean "every untagged sector" in Doom),
    # so each Quake func_plat gets its own tag. Tag 1 is the teleport destination.
    plat_tags = {}
    for sec in sectors:
        if sec['kind'] is not None and sec['kind'][0] == 'plat':
            ent = sec['kind'][1]
            if ent not in plat_tags:
                plat_tags[ent] = 2 + len(plat_tags)
            sec['tag'] = plat_tags[ent]

    # teleport destination sector tag
    for e in ents if False else []:
        if e['kv'].get('classname') == 'info_teleport_destination':
            o = [float(v) for v in e['kv']['origin'].split()]
            fi = face_at(o[0], o[1])
            if fi in face_sector:
                s = sectors[face_sector[fi]]
                # give the destination its own sector so the tag is unique
                ns = dict(s); ns['tag'] = tele_tag; ns['kind'] = ('teledest',)
                sectors.append(ns); face_sector[fi] = len(sectors) - 1

    # Quake 1: the landing spot must stand in a sector carrying its teleporter's tag
    if not q3:
        for w in warps:
            if w['ent'] not in warp_tag or w.get('shared'):
                continue
            fi = face_at(w['dest'][2], w['dest'][3])
            if fi not in face_sector:
                continue
            s = sectors[face_sector[fi]]
            if s['kind'] is not None and s['kind'][0] != 'teledest':
                # the spot fell on a door or lift next to the landing floor: stand in the landing floor itself
                dfi = w['dest'][0]
                if dfi in face_sector and sectors[face_sector[dfi]]['tag'] == warp_tag[w['ent']]:
                    from shapely.ops import polylabel
                    p = polylabel(faces[dfi], 1.0)
                    w['dest'] = (dfi, w['dest'][1], float(round(p.x)), float(round(p.y)), w['dest'][4])
                continue
            if s['tag'] == warp_tag[w['ent']]:
                continue
            ns = dict(s); ns['tag'] = warp_tag[w['ent']]; ns['kind'] = ('teledest', w['ent'])
            sectors.append(ns); face_sector[fi] = len(sectors) - 1

    # edges
    verts, vidx = [], {}

    def V(p):
        k = key(p)
        if k not in vidx:
            vidx[k] = len(verts); verts.append(k)
        return vidx[k]

    edges = {}  # (va,vb) directed with face on the left
    for fi, si in face_sector.items():
        f = faces[fi]
        for ring in [f.exterior] + list(f.interiors):
            cs = list(ring.coords)
            # exterior is CCW (face on left); interiors are CW (face on left too)
            for a, b in zip(cs, cs[1:]):
                va, vb = V(a), V(b)
                if va != vb:
                    edges[(va, vb)] = si

    # wall texture lookup: vertical brush faces indexed by plane
    vfaces = defaultdict(list)
    for b in solids:
        for n, d, tex in b.planes:
            if abs(n[2]) < 0.1:
                ang = round(math.degrees(math.atan2(n[1], n[0]))) % 360
                vfaces[(ang, round(d))].append((b, n, d, tex))

    # edges with no matching brush face use the level's own most common wall texture
    _wall_counts = defaultdict(int)
    for _lst in vfaces.values():
        for _b, _n, _d, _tex in _lst:
            if wall_tex(_tex) not in ('METAL1', 'EXITDOOR', 'BIGDOOR2', 'LITE5', 'SW1COMP'):
                _wall_counts[wall_tex(_tex)] += 1
    fallback_tex = max(_wall_counts, key=_wall_counts.get) if _wall_counts else wall_tex(None)

    def tex_for_edge(p1, p2, outward_from_face):
        # find a brush face whose plane contains the edge, with normal pointing into the face
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        Ln = math.hypot(dx, dy) or 1
        # normal pointing into the face (face on the left of p1->p2): (-dy, dx)
        nx, ny = -dy / Ln, dx / Ln
        ang = round(math.degrees(math.atan2(ny, nx))) % 360
        d = nx * mx + ny * my
        for a in (ang, (ang + 1) % 360, (ang - 1) % 360):
            for dd in (round(d), round(d) + 1, round(d) - 1):
                for b, n, pd, tex in vfaces.get((a, dd), []):
                    if b.fp.buffer(1).contains(Point(mx - nx * 0.5, my - ny * 0.5)):
                        return wall_tex(tex)
        return fallback_tex

    linedefs, sidedefs = [], []

    def side(sec, upper='-', lower='-', mid='-'):
        sidedefs.append({'x': 0, 'y': 0, 'upper': upper, 'lower': lower, 'mid': mid, 'sector': sec})
        return len(sidedefs) - 1

    done = set()
    for (va, vb), sl in edges.items():
        if (va, vb) in done:
            continue
        done.add((va, vb))
        sr = edges.get((vb, va))
        if sr is not None:
            done.add((vb, va))
            if sr == sl:
                continue
        pa, pb = verts[va], verts[vb]
        S_l = sectors[sl]
        if sr is None:
            # one-sided wall, face on the left of va->vb; Doom front is on the right of v1->v2
            t = tex_for_edge(pa, pb, True)
            linedefs.append({'v1': vb, 'v2': va, 'flags': 1, 'special': 0, 'tag': 0,
                             'front': side(sl, mid=t), 'back': 0xFFFF})
            continue
        S_r = sectors[sr]
        t = tex_for_edge(pa, pb, True)
        # decide orientation: specials need the "outside" in front
        kl, kr = S_l['kind'], S_r['kind']
        special, tag = 0, 0
        front, back, v1, v2 = sr, sl, va, vb  # default: right sector (sr) is in front of va->vb
        def is_kind(k, name): return k is not None and k[0] == name
        for name, sp in (('door', 1), ('plat', 62), ('tele', 97), ('exit', 52)):
            if is_kind(kl, name) and not is_kind(kr, name):
                front, back, v1, v2 = sr, sl, va, vb; special = sp; break
            if is_kind(kr, name) and not is_kind(kl, name):
                front, back, v1, v2 = sl, sr, vb, va; special = sp; break
        if special == 97:
            tag = sectors[back].get('warp_tag', tele_tag)
        elif special == 62:
            tag = sectors[back]['tag']
            # from the lift's upper level you walk onto it (walk-over lift, 88);
            # from below you press use on its side (switch lift, 62)
            if sectors[front]['floor'] >= sectors[back]['floor'] - 8:
                special = 88
        flags = 4
        if special == 1:
            flags |= 8  # upper unpegged so door texture moves with the door
        fs, bs = sectors[front], sectors[back]
        def sd(sec, other):
            up = t if sectors[sec]['ceil'] > sectors[other]['ceil'] else '-'
            lo = t if sectors[sec]['floor'] < sectors[other]['floor'] else '-'
            if sectors[sec]['kind'] is not None and sectors[sec]['kind'][0] == 'door':
                up = lo = 'DOORTRAK'
            if special == 1 and sec == front:
                up = 'BIGDOOR2'
            if lo != '-' and sectors[other]['floor'] - sectors[sec]['floor'] <= 24 and not q3:
                lo = 'STEP1'
            return side(sec, upper=up, lower=lo)
        linedefs.append({'v1': v1, 'v2': v2, 'flags': flags, 'special': special, 'tag': tag,
                         'front': sd(front, back), 'back': sd(back, front)})

    # 6. things
    THING = {
        'info_player_start': 1, 'info_player_coop': 2, 'info_player_deathmatch': 11,
        # info_teleport_destination: its landing spot is placed with the teleporter's tag
        'monster_dog': 3002, 'monster_ogre': 3001, 'monster_knight': 3001, 'monster_wizard': 3005,
        'monster_demon1': 3003, 'monster_shambler': 3003, 'monster_zombie': 3004,
        'monster_boss': 16,  # Chthon: a Cyberdemon guards the lava pit
        'item_health': 2012, 'item_shells': 2008, 'item_spikes': 2007, 'item_rockets': 2010,
        'item_cells': 2047, 'item_armor1': 2018, 'item_armor2': 2019, 'item_armorInv': 2019,
        'item_artifact_super_damage': 2023, 'item_artifact_invulnerability': 2022,
        'item_artifact_envirosuit': 2025, 'item_artifact_invisibility': 2024,
        'weapon_supershotgun': 2001, 'weapon_nailgun': 2002, 'weapon_supernailgun': 2002,
        'weapon_grenadelauncher': 2003, 'weapon_rocketlauncher': 2003, 'weapon_lightning': 2004,
        'misc_explobox': 2035, 'misc_explobox2': 2035,
    }
    if q3:
        THING = dict(Q3_THING)
    things, coop = [], 0
    army = 0
    for e in ents:
        c = e['kv'].get('classname', '')
        if 'origin' not in e['kv']:
            continue
        o = [float(v) for v in e['kv']['origin'].split()]
        if q3 and e is start_ent:
            c = 'info_player_start'
        if c == 'monster_army':
            army += 1
            t = 3001 if army % 2 == 0 else (9 if army % 3 == 0 else 3004)  # imps, shotgun guys, troopers
        elif c == 'info_player_coop':
            coop += 1
            if coop > 3:
                continue
            t = 1 + coop
        else:
            t = THING.get(c)
        if t is None:
            continue
        if c == 'item_health':
            sf = int(e['kv'].get('spawnflags', 0))
            t = 2011 if sf & 1 else (2013 if sf & 2 else 2012)
        fi = face_at(o[0], o[1])
        if fi not in face_sector:
            continue
        if q3 and sectors[face_sector[fi]]['kind'] is not None and c != 'info_player_start':
            # nothing sits on a pad: step the item off it, onto the same floor
            for d in (64, 96, 128, 176):
                for ax, ay in ((d, 0), (-d, 0), (0, -d), (0, d)):
                    fj = face_at(o[0] + ax, o[1] + ay)
                    if fj in face_sector and sectors[face_sector[fj]]['kind'] is None \
                            and sectors[face_sector[fj]]['floor'] == sectors[face_sector[fi]]['floor'] - (64 if fi in exit_faces and mapname == 'MAP07' else 0):
                        o = [o[0] + ax, o[1] + ay, o[2]]; fi = fj
                        break
                else:
                    continue
                break
            else:
                continue
        sf = int(e['kv'].get('spawnflags', 0))
        flags = 7
        if q3:
            sf = 0  # Quake III spawnflags mean other things
        if c.startswith(('monster_', 'item_', 'weapon_')):
            if sf & 256: flags &= ~1
            if sf & 512: flags &= ~2
            if sf & 1024: flags &= ~4
            if c.startswith('monster_') and sf & 1: flags |= 8
        if c == 'info_player_deathmatch' or c == 'info_player_coop':
            flags = 7
        things.append({'x': int(round(o[0])), 'y': int(round(o[1])), 'angle': int(float(e['kv'].get('angle', 0))) % 360,
                       'type': t, 'flags': flags})

    # where a teleporter or jump pad sets the player down
    for w in warps:
        if w['ent'] in warp_tag and not w.get('shared'):
            _, _, dx, dy, ang = w['dest']
            things.append({'x': int(dx), 'y': int(dy), 'angle': int(round(ang / 45.0) * 45) % 360, 'type': 14, 'flags': 7})
    if q3:
        def floor_ceil(x, y):
            fi = face_at(x, y)
            if fi not in face_sector:
                return None
            sec = sectors[face_sector[fi]]
            return sec['floor'], sec['ceil']
        things.extend(populate_arena(things, faces, face_sector, sectors, face_pt,
                                     {fi for fi, gi in chosen.items() if (fi, gi) in ground}, scenery, monsters, floor_ceil))

    route_pts = [[round(face_pt[a][0]), round(face_pt[a][1]), round(face_gaps[a][g]['f'])] for a, g in reversed(route)]
    route_specials = []
    for a, _ in reversed(route):
        fl = face_flags[a]
        for k in ('door', 'plat'):
            if k in fl and (k, fl[k] if k == 'door' else fl[k][0]) not in [r[:2] for r in route_specials]:
                route_specials.append((k, fl[k] if k == 'door' else fl[k][0], round(face_pt[a][0]), round(face_pt[a][1])))
    print('route passes through', route_specials, file=sys.stderr)
    extra = {}
    if q3:
        # for tests: where each pad is and where it lands
        extra['warps'] = []
        for w in warps:
            if w['ent'] in warp_tag:
                Vb = np.concatenate([b.verts for b in triggers['teleport'] if b.ent == w['ent']])
                c = (Vb.min(0) + Vb.max(0)) / 2
                tagd = next(x for x in warps if warp_tag.get(x['ent']) == warp_tag[w['ent']] and not x.get('shared'))['dest']
                extra['warps'].append({'kind': w['kind'], 'target': w['target'], 'pad': [round(float(c[0])), round(float(c[1]))],
                                       'dest': [tagd[2], tagd[3], round(face_gaps[tagd[0]][tagd[1]]['f'] / 8) * 8]})
        extra['exit'] = [[round(faces[j].representative_point().x), round(faces[j].representative_point().y)] for j in sorted(exit_faces)][:1]
    out = {**extra, 'route': route_pts, 'mapname': mapname, 'vertexes': verts, 'linedefs': linedefs, 'sidedefs': sidedefs,
           'sectors': sectors, 'things': things}
    if q3:
        simplify_lines(out)
        verts, linedefs, sidedefs = out['vertexes'], out['linedefs'], out['sidedefs']
    json.dump(out, open(out_json, 'w'))
    print(f'verts {len(verts)} lines {len(linedefs)} sides {len(sidedefs)} sectors {len(sectors)} things {len(things)}', file=sys.stderr)
    return out


# ---------------------------------------------------------------- simplification
def simplify(m, tol=0.75):
    """Merge chains of collinear linedefs that have identical sides and specials.

    The planar overlay cuts long walls into many short pieces wherever another brush
    outline touches them. Vanilla Doom indexes vertexes and sidedefs with signed 16-bit
    numbers (max 32767), so big Quake levels only fit once those pieces are joined.
    """
    V, L, S = m['vertexes'], m['linedefs'], m['sidedefs']

    def skey(i):
        if i is None:
            return None
        s = S[i]
        return (s['sector'], s['upper'], s['lower'], s['mid'])

    # a one-sided line (no two-sided flag) has no back side; 0xFFFF can also be a real
    # side number here, since the unmerged level may have more than 65535 sides
    def back(l):
        return l['back'] if l['flags'] & 4 else None

    def lkey(l):
        return (l['flags'], l['special'], l['tag'], skey(l['front']), skey(back(l)))

    alive = [True] * len(L)
    starts, ends = defaultdict(set), defaultdict(set)
    for i, l in enumerate(L):
        starts[l['v1']].add(i); ends[l['v2']].add(i)

    def off_line(a, b, c):
        (ax, ay), (bx, by), (cx, cy) = V[a], V[b], V[c]
        Ln = math.hypot(cx - ax, cy - ay)
        if Ln < 1e-9:
            return 1e9
        return abs((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / Ln

    merged = 0
    changed = True
    while changed:
        changed = False
        for v in range(len(V)):
            if len(starts[v]) != 1 or len(ends[v]) != 1:
                continue
            (i,), (j,) = ends[v], starts[v]  # L[i]: a -> v, L[j]: v -> c
            if i == j or not (alive[i] and alive[j]):
                continue
            a, c = L[i]['v1'], L[j]['v2']
            if a == c or lkey(L[i]) != lkey(L[j]) or off_line(a, v, c) > tol:
                continue
            # join: L[i] becomes a -> c, L[j] goes away
            ends[v].discard(i); starts[v].discard(j); ends[c].discard(j); ends[c].add(i)
            L[i]['v2'] = c; alive[j] = False
            merged += 1; changed = True

    # rebuild compact arrays
    newL, usedV, usedS = [], {}, {}
    def remap_v(v):
        if v not in usedV:
            usedV[v] = len(usedV)
        return usedV[v]
    def remap_s(s):
        if s is None:
            return 0xFFFF
        if s not in usedS:
            usedS[s] = len(usedS)
        return usedS[s]
    for i, l in enumerate(L):
        if alive[i]:
            nl = dict(l); nl['v1'] = remap_v(l['v1']); nl['v2'] = remap_v(l['v2'])
            nl['front'] = remap_s(l['front']); nl['back'] = remap_s(back(l))
            newL.append(nl)
    newV = [None] * len(usedV)
    for old, new in usedV.items():
        newV[new] = V[old]
    newS = [None] * len(usedS)
    for old, new in usedS.items():
        newS[new] = S[old]
    m['vertexes'], m['linedefs'], m['sidedefs'] = newV, newL, newS
    print(f'simplified: merged {merged} pieces -> verts {len(newV)} lines {len(newL)} sides {len(newS)}', file=sys.stderr)
    for what, n in (('vertexes', len(newV)), ('sidedefs', len(newS)), ('linedefs', len(newL)), ('sectors', len(m['sectors']))):
        if n > 65535:
            raise SystemExit(f'{n} {what}: over the 65535 limit, even for Relit')
        if n > 32767:
            print(f'note: {n} {what} needs Relit\'s engine (unsigned indices); vanilla Doom stops at 32767', file=sys.stderr)
    return m


# ---------------------------------------------------------------- WAD writing
def write_wad(m, path):
    def s8(s):
        return s.encode('ascii')[:8].ljust(8, b'\0')
    lumps = [(m['mapname'], b'')]
    lumps.append(('THINGS', b''.join(struct.pack('<hhhhh', t['x'], t['y'], t['angle'], t['type'], t['flags']) for t in m['things'])))
    lumps.append(('LINEDEFS', b''.join(struct.pack('<HHhhhHH', l['v1'], l['v2'], l['flags'], l['special'], l['tag'], l['front'], l['back']) for l in m['linedefs'])))
    lumps.append(('SIDEDEFS', b''.join(struct.pack('<hh', s['x'], s['y']) + s8(s['upper']) + s8(s['lower']) + s8(s['mid']) + struct.pack('<h', s['sector']) for s in m['sidedefs'])))
    lumps.append(('VERTEXES', b''.join(struct.pack('<hh', v[0], v[1]) for v in m['vertexes'])))
    for nm in ('SEGS', 'SSECTORS', 'NODES'):
        lumps.append((nm, b''))
    lumps.append(('SECTORS', b''.join(struct.pack('<hh', s['floor'], s['ceil']) + s8(s['fflat']) + s8(s['cflat']) + struct.pack('<hhh', s['light'], s['special'], s['tag']) for s in m['sectors'])))
    lumps.append(('REJECT', b'')); lumps.append(('BLOCKMAP', b''))
    data = b''; dirs = []
    off = 12
    for nm, b in lumps:
        dirs.append(struct.pack('<II', off, len(b)) + s8(nm)); data += b; off += len(b)
    open(path, 'wb').write(b'PWAD' + struct.pack('<II', len(lumps), off) + data + b''.join(dirs))


if __name__ == '__main__':
    # quake2doom.py in.map textures.json outbase [MAPNAME] [--crop x0,y0,x1,y1] [--monsters N] [--water-jump]
    args, opts = [], {}
    it = iter(sys.argv[1:])
    for a in it:
        if a == '--crop':
            opts['crop'] = [float(v) for v in next(it).split(',')]
        elif a == '--monsters':
            opts['monsters'] = int(next(it))
        elif a == '--water-jump':
            opts['water_jump'] = True
        else:
            args.append(a)
    m = convert(args[0], args[1], args[2] + '.json', args[3] if len(args) > 3 else 'E1M1', **opts)
    mn = args[3] if len(args) > 3 else 'E1M1'
    write_wad(m if mn.startswith('MAP') else simplify(m), args[2] + '.raw.wad')
