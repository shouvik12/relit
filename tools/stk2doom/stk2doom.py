#!/usr/bin/env python3
"""stk2doom: turn a SuperTuxKart track's driveline (quads.xml) into a Relit race level.

Only the driveline geometry is used: each quad of the main driveline becomes a Doom sector at
that height, with a low snowbank either side and open sky above. The loop is closed by a wall
with the exit switch on it, so you finish by going all the way round. Monsters line the track,
aimed at the start.

    python3 stk2doom.py quads.xml out E1M1   ->  out.raw.wad  (then zdbsp)
"""
import math, random, re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'quake2doom'))
from quake2doom import write_wad

SCALE, BANK, BANKH = 40, 4.0, 40
HSCALE = 0.55   # squash heights so every step is a comfortable <= 16 units
SNOW, WALL = 'FLAT19', 'STONE2'
INDOOR = os.environ.get('STK_INDOOR', '1') == '1'   # frozen tunnel: low ceiling, no banks, torchlit


def read_quads(path):
    text = open(path).read()
    block = re.split(r'<!-- Driveline', text)[1]       # first (main) driveline only
    quads = []
    for m in re.finditer(r'<quad\s+p0="([^"]+)"\s+p1="([^"]+)"\s+p2="([^"]+)"\s+p3="([^"]+)"', block):
        pts = []
        for s in m.groups():
            if ':' in s:
                q, k = map(int, s.split(':')); pts.append(quads[q][k])
            else:
                x, y, z = map(float, s.split()); pts.append((x, y, z))
        quads.append(pts)
    return quads


def convert(path, mapname):
    Q = read_quads(path)[:int(os.environ.get('STK_QUADS', 106))]   # cut before the track crosses itself
    n = len(Q)
    # cross-section k: left = p0 of quad k, right = p1 (the last one is p3/p2 of the last quad)
    secs = [(q[0], q[1]) for q in Q] + [(Q[-1][3], Q[-1][2])]
    xy = lambda p: (round(p[0] * SCALE), round(-p[2] * SCALE))
    def outer(L, R):
        dx, dz = L[0] - R[0], L[2] - R[2]; d = math.hypot(dx, dz) or 1
        return ((L[0] + dx / d * BANK, L[1], L[2] + dz / d * BANK), (R[0] - dx / d * BANK, R[1], R[2] - dz / d * BANK))
    outs = [outer(*s) for s in secs]

    V, L, S, SEC, T = [], [], [], [], []
    vid = {}
    def v(p):
        p = xy(p)
        if p not in vid: vid[p] = len(V); V.append(p)
        return vid[p]
    def side(sec, mid='-'):
        S.append({'x': 0, 'y': 0, 'upper': WALL, 'lower': WALL, 'mid': mid, 'sector': sec}); return len(S) - 1
    def line(a, b, front, back=None, special=0, flags=None):
        L.append({'v1': v(a), 'v2': v(b), 'flags': flags if flags is not None else (1 if back is None else 4),
                  'special': special, 'tag': 0, 'front': front, 'back': 0xFFFF if back is None else back})

    for i, q in enumerate(Q):
        f = round(sum(p[1] for p in q) / 4 * SCALE * HSCALE)
        if INDOOR:
            SEC.append({'floor': f, 'ceil': f + 200, 'fflat': SNOW, 'cflat': 'CEIL5_1',
                        'light': (176, 160, 144)[i % 3], 'special': 0, 'tag': 0})
        else:
            SEC.append({'floor': f, 'ceil': f + 640, 'fflat': SNOW, 'cflat': 'F_SKY1', 'light': 255, 'special': 0, 'tag': 0})
    for i in range(0 if INDOOR else 2 * n):
        f = SEC[i % n]['floor']
        SEC.append({'floor': f + BANKH, 'ceil': f + BANKH + 8, 'fflat': SNOW, 'cflat': 'F_SKY1', 'light': 255, 'special': 0, 'tag': 0})

    for i in range(n):
        (l0, r0), (l1, r1) = secs[i], secs[i + 1]
        (ol0, or0), (ol1, or1) = outs[i], outs[i + 1]
        lb, rb = n + i, 2 * n + i
        # Doom sides face right of v1->v2; the driveline runs l->r across, forward along z
        if INDOOR:
            line(l1, l0, side(i, WALL)); line(r0, r1, side(i, WALL))
            if i < n - 1: line(r1, l1, side(i), side(i + 1))
            else: line(r1, l1, side(i, 'SW1COMP'), special=11)
            if i == 0: line(l0, r0, side(i, WALL))
            continue
        line(l1, l0, side(i), side(lb)); line(r0, r1, side(i), side(rb))
        line(ol0, ol1, side(lb, WALL)); line(or1, or0, side(rb, WALL))
        if i < n - 1:
            line(r1, l1, side(i), side(i + 1))
            line(l1, ol1, side(lb), side(lb + 1)); line(or1, r1, side(rb), side(rb + 1))
        else:   # end of the lap: a solid wall with the exit switch, banks closed off
            line(r1, l1, side(i, 'SW1COMP'), special=11)
            line(l1, ol1, side(lb, WALL)); line(or1, r1, side(rb, WALL))
        if i == 0:
            line(l0, r0, side(i, WALL)); line(ol0, l0, side(lb, WALL)); line(r0, or0, side(rb, WALL))

    rnd = random.Random(42)
    mid = lambda q, t=.5, s=.5: tuple(
        (q[0][k] * (1 - s) + q[1][k] * s) * (1 - t) + (q[3][k] * (1 - s) + q[2][k] * s) * t for k in range(3))
    def thing(p, t, a=0): x, y = xy(p); T.append({'x': x, 'y': y, 'angle': a, 'type': t, 'flags': 7})
    s0 = mid(Q[2]); ahead = mid(Q[4])
    thing(s0, 1, int(math.degrees(math.atan2(xy(ahead)[1] - xy(s0)[1], xy(ahead)[0] - xy(s0)[0]))) % 360)
    thing(mid(Q[3], .5, .3), 2001); thing(mid(Q[3], .5, .7), 2002)
    GORE = [10, 12, 15, 18, 19, 20, 21, 24, 24, 10, 12]   # corpses, gibs, blood (Doom 1 types only)
    STAKES = [25, 26, 27, 28]                                         # impaled bodies, heads on stakes
    for i in range(5, n - 1):
        prog = i / n
        pool = [3004, 3004, 9, 9, 3001, 3001, 3002] + ([3006, 3005, 3002] if prog > .25 else []) \
            + ([3003, 3005] if prog > .6 else [])
        for _ in range(1 if i % 2 else rnd.choice([0, 1, 1, 2])):   # a steady horde, light enough to run smoothly
            thing(mid(Q[i], rnd.random(), rnd.uniform(.12, .88)), rnd.choice(pool))
        for _ in range(rnd.choice([0, 1, 1, 2])):                     # the carnage of those who came before
            thing(mid(Q[i], rnd.random(), rnd.uniform(.05, .95)), rnd.choice(GORE), rnd.choice((0, 90, 180, 270)))
        if i % 3 == 1:
            thing(mid(Q[i], .5, rnd.uniform(.2, .8)), rnd.choice([2008, 2048, 2049, 2007, 2046, 2048]))
        if INDOOR and os.environ.get("STK_TORCHES") == "1" and i % 4 == 0:                                     # torches along both walls
            thing(mid(Q[i], .5, .06), 46); thing(mid(Q[i], .5, .94), 44 if i % 8 else 45)
        if i % 7 == 0:
            thing(mid(Q[i], .5, .5), rnd.choice([2011, 2012, 2012, 2014]))
        if i in (40, 75):
            thing(mid(Q[i], .5, .5), rnd.choice([2003, 2006]) if i == 75 else 2003)   # rocket launcher, then BFG
    px, py = T[0]['x'], T[0]['y']
    for t in T[1:]:
        if t['type'] in (3004, 9, 3001, 3002, 3006, 3005, 3003):
            t['angle'] = int(round(math.degrees(math.atan2(py - t['y'], px - t['x'])) % 360 / 45) * 45) % 360
    length = sum(math.hypot(secs[i + 1][0][0] - secs[i][0][0], secs[i + 1][0][2] - secs[i][0][2]) for i in range(n))
    print(f'{n} quads, ~{length:.0f} m lap, {len(T)} things, {sum(t["type"] in (3004,9,3001,3002,3006,3005,3003) for t in T)} monsters', file=sys.stderr)
    return {'mapname': mapname, 'vertexes': V, 'linedefs': L, 'sidedefs': S, 'sectors': SEC, 'things': T}


if __name__ == '__main__':
    write_wad(convert(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else 'E1M1'), sys.argv[2] + '.raw.wad')
