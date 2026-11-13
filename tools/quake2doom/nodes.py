"""A small node builder for vanilla-format Doom maps (no outside tools needed).

Reads the raw map that quake2doom.py writes and adds what the engine needs to load it:
SEGS, SSECTORS, NODES (the BSP tree), REJECT (all zero: nothing hidden) and BLOCKMAP.

    python3 nodes.py arena.raw.wad arena.wad

It is a plain recursive splitter: pick the wall that cuts the fewest others while keeping
the two halves balanced, split, repeat until each piece is convex. ZDBSP makes tighter
trees; this one is here so a level can be rebuilt with nothing but Python.
"""
import math, struct, sys

import numpy as np

EPS = 0.25


def read_wad(path):
    d = open(path, 'rb').read()
    n, off = struct.unpack_from('<II', d, 4)
    lumps = []
    for i in range(n):
        p, s, nm = struct.unpack_from('<II8s', d, off + 16 * i)
        lumps.append((nm.rstrip(b'\0').decode('ascii'), d[p:p + s]))
    return lumps


def write_wad(path, lumps):
    data, dirs, off = b'', [], 12
    for nm, b in lumps:
        dirs.append(struct.pack('<II', off, len(b)) + nm.encode('ascii')[:8].ljust(8, b'\0'))
        data += b; off += len(b)
    open(path, 'wb').write(b'PWAD' + struct.pack('<II', len(lumps), off) + data + b''.join(dirs))


class Builder:
    def __init__(self, verts, lines):
        self.verts = [tuple(v) for v in verts]  # grows as walls are split
        self.vidx = {}
        self.lines = lines                      # (v1, v2, front side or -1, back side or -1)
        self.segs, self.ssectors, self.nodes = [], [], []

    def vertex(self, x, y):
        k = (int(round(x)), int(round(y)))
        if k not in self.vidx:
            self.vidx[k] = len(self.verts); self.verts.append(k)
        return self.vidx[k]

    def build(self):
        rows = []
        for li, (v1, v2, front, back) in enumerate(self.lines):
            a, b = self.verts[v1], self.verts[v2]
            if a == b:
                continue
            if front >= 0:
                rows.append((a[0], a[1], b[0], b[1], li, 0, v1, v2))
            if back >= 0:
                rows.append((b[0], b[1], a[0], a[1], li, 1, v2, v1))
        sys.setrecursionlimit(20000)
        return self.split(np.array(rows, dtype=np.float64))

    @staticmethod
    def sides(S, px, py, dx, dy):
        """Signed distance of every seg's two ends from a partition line (negative = right)."""
        L = math.hypot(dx, dy)
        a = (dx * (S[:, 1] - py) - dy * (S[:, 0] - px)) / L
        b = (dx * (S[:, 3] - py) - dy * (S[:, 2] - px)) / L
        return a, b

    def classify(self, S, part):
        px, py, qx, qy = part[:4]
        dx, dy = qx - px, qy - py
        a, b = self.sides(S, px, py, dx, dy)
        on = (np.abs(a) <= EPS) & (np.abs(b) <= EPS)
        same = ((S[:, 2] - S[:, 0]) * dx + (S[:, 3] - S[:, 1]) * dy) > 0
        right = ((a <= EPS) & (b <= EPS) & ~on) | (on & same)
        left = ((a >= -EPS) & (b >= -EPS) & ~on) | (on & ~same)
        return a, b, right, left, ~(right | left)

    def pick(self, S):
        n = len(S)
        best, bcost = None, None
        for cand in ([np.linspace(0, n - 1, min(n, 28)).astype(int)], [np.arange(n)]):
            for i in cand[0]:
                a, b, right, left, cut = self.classify(S, S[i])
                nr, nl, nc = int(right.sum()), int(left.sum()), int(cut.sum())
                if (nl + nc == 0) or (nr + nc == 0):
                    continue  # everything on one side: not a real split
                cost = nc * 12 + abs(nr - nl)
                if bcost is None or cost < bcost:
                    best, bcost = i, cost
            if best is not None:
                return best
        return None

    def leaf(self, S):
        first = len(self.segs)
        for x1, y1, x2, y2, li, side, va, vb in S:
            v1, v2 = self.lines[int(li)][:2]
            o = self.verts[v1 if side == 0 else v2]
            ang = int(round(math.atan2(y2 - y1, x2 - x1) / (2 * math.pi) * 65536)) & 0xFFFF
            self.segs.append((int(va), int(vb), ang, int(li), int(side), int(round(math.hypot(x1 - o[0], y1 - o[1])))))
        self.ssectors.append((len(S), first))
        return 0x8000 | (len(self.ssectors) - 1)

    @staticmethod
    def bbox(S):
        xs = np.concatenate([S[:, 0], S[:, 2]]); ys = np.concatenate([S[:, 1], S[:, 3]])
        return (int(math.ceil(ys.max())), int(math.floor(ys.min())), int(math.floor(xs.min())), int(math.ceil(xs.max())))

    def split(self, S):
        i = self.pick(S) if len(S) > 1 else None
        if i is None:
            return self.leaf(S)
        part = S[i].copy()
        a, b, right, left, cut = self.classify(S, part)
        R, Lf = [S[right]], [S[left]]
        for row, da, db in zip(S[cut], a[cut], b[cut]):
            t = da / (da - db)
            mx, my = row[0] + (row[2] - row[0]) * t, row[1] + (row[3] - row[1]) * t
            vm = self.vertex(mx, my)
            mx, my = self.verts[vm]
            first = np.array([[row[0], row[1], mx, my, row[4], row[5], row[6], vm]])
            second = np.array([[mx, my, row[2], row[3], row[4], row[5], vm, row[7]]])
            (R if da < 0 else Lf).append(first)
            (Lf if da < 0 else R).append(second)
        R, Lf = np.concatenate(R), np.concatenate(Lf)
        keep = lambda A: A[(A[:, 0] != A[:, 2]) | (A[:, 1] != A[:, 3])]
        R, Lf = keep(R), keep(Lf)
        if not len(R) or not len(Lf):
            return self.leaf(S)
        # the partition is stored from the whole wall it came from, in map units
        v1, v2 = self.lines[int(part[4])][:2]
        p, q = (self.verts[v1], self.verts[v2]) if part[5] == 0 else (self.verts[v2], self.verts[v1])
        rb, lb = self.bbox(R), self.bbox(Lf)
        rc, lc = self.split(R), self.split(Lf)
        self.nodes.append((p[0], p[1], q[0] - p[0], q[1] - p[1], rb, lb, rc, lc))
        return len(self.nodes) - 1


def blockmap(verts, lines):
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]
    ox, oy = min(xs) - 8, min(ys) - 8
    cols, rows = (max(xs) - ox) // 128 + 1, (max(ys) - oy) // 128 + 1
    cells = [[] for _ in range(cols * rows)]
    for li, (v1, v2, _, _) in enumerate(lines):
        (x1, y1), (x2, y2) = verts[v1], verts[v2]
        for cx in range((min(x1, x2) - ox) // 128, (max(x1, x2) - ox) // 128 + 1):
            for cy in range((min(y1, y2) - oy) // 128, (max(y1, y2) - oy) // 128 + 1):
                bx, by = ox + cx * 128, oy + cy * 128
                # the line touches the block unless all four corners lie strictly on one side
                d = [(x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
                     for px, py in ((bx, by), (bx + 128, by), (bx, by + 128), (bx + 128, by + 128))]
                if min(d) > 0 or max(d) < 0:
                    continue
                cells[cy * cols + cx].append(li)
    out = [struct.pack('<hhHH', ox, oy, cols, rows)]
    body, offs, at = [], [], 4 + cols * rows
    for c in cells:
        offs.append(at)
        body.append(struct.pack('<%dH' % (len(c) + 2), 0, *c, 0xFFFF))
        at += len(c) + 2
    if at > 0xFFFF:
        raise SystemExit('blockmap too large for the vanilla format')
    return out[0] + struct.pack('<%dH' % len(offs), *offs) + b''.join(body)


def build(lumps):
    L = dict(lumps[1:])
    verts = list(struct.iter_unpack('<hh', L['VERTEXES']))
    lines, nsides = [], len(L['SIDEDEFS']) // 30
    for v1, v2, fl, sp, tag, front, back in struct.iter_unpack('<HHhhhHH', L['LINEDEFS']):
        lines.append((v1, v2, front if front != 0xFFFF else -1, back if back != 0xFFFF else -1))
    b = Builder(verts, lines)
    root = b.build()
    if max(len(b.segs), len(b.ssectors), len(b.nodes), len(b.verts)) > 32767:
        raise SystemExit('map too large for the vanilla format')
    nsec = len(L['SECTORS']) // 26
    child = lambda c: c
    L['VERTEXES'] = b''.join(struct.pack('<hh', *v) for v in b.verts)
    L['SEGS'] = b''.join(struct.pack('<HHHHHh', v1, v2, ang, li, side, off) for v1, v2, ang, li, side, off in b.segs)
    L['SSECTORS'] = b''.join(struct.pack('<HH', n, first) for n, first in b.ssectors)
    L['NODES'] = b''.join(struct.pack('<hhhh4h4hHH', x, y, dx, dy, *rb, *lb, child(rc), child(lc))
                          for x, y, dx, dy, rb, lb, rc, lc in b.nodes)
    L['REJECT'] = bytes((nsec * nsec + 7) // 8)
    L['BLOCKMAP'] = blockmap(verts, lines)
    order = ['THINGS', 'LINEDEFS', 'SIDEDEFS', 'VERTEXES', 'SEGS', 'SSECTORS', 'NODES', 'SECTORS', 'REJECT', 'BLOCKMAP']
    print(f'nodes: {len(b.segs)} segs, {len(b.ssectors)} subsectors, {len(b.nodes)} nodes, '
          f'{len(b.verts) - len(verts)} split vertices', file=sys.stderr)
    return [lumps[0]] + [(nm, L[nm]) for nm in order]


if __name__ == '__main__':
    write_wad(sys.argv[2], build(read_wad(sys.argv[1])))
