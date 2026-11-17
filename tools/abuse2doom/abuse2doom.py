#!/usr/bin/env python3
"""abuse2doom: turn an Abuse (1995, Crack dot Com, public domain) level into a Doom map that
wears Abuse's own art.

Abuse is a side-scroller, so its foreground tile grid is read as a floor plan: tiles with a
collision outline are solid, the rest is open floor. Every wall is textured with the actual Abuse
tile behind it (extracted from the game's art files, converted to the Doom palette and shipped in
the map file), so the tunnels look like Abuse's base. Freedoom monsters, weapons and items are
placed along the route, and the exit switch goes on the wall farthest from the start.

    python3 abuse2doom.py <abuse data dir> level01.spe out E1M1 <freedoom1.wad>
        ->  out.raw.wad, plus out.tex (texture lumps to add after building nodes)
    python3 abuse2doom.py --addtex built.wad out.tex
"""
import os, pickle, random, struct, sys
from collections import deque

CELL = 64                         # Doom units per Abuse tile
FLOOR, CEIL = 'FLOOR4_8', 'CEIL5_1'
ART = ['foregrnd', 'techno', 'techno2', 'techno3', 'techno4', 'cave', 'alien', 'trees', 'endgame', 'trees2']


def spe_entries(path):
    d = open(path, 'rb').read()
    if d[:7] != b'SPEC1.0': raise SystemExit(f'{path}: not an Abuse .spe file')
    n = struct.unpack_from('<H', d, 8)[0]; p = 10; out = []
    for _ in range(n):
        t, l = d[p], d[p + 1]; name = d[p + 2:p + 2 + l].rstrip(b'\0').decode('latin1'); p += 2 + l
        size, off = struct.unpack_from('<II', d, p + 1); p += 9
        out.append((t, name, d[off:off + size]))
    return out


def load_tiles(data):
    """Abuse foreground tiles: number -> (w, h, pixels, solid). Solid = has a collision outline."""
    pal = None
    for t, nm, b in spe_entries(os.path.join(data, 'art/back/backgrnd.spe')):
        if t == 2:
            n = struct.unpack_from('<H', b)[0]; pal = [tuple(b[2 + 3 * i:5 + 3 * i]) for i in range(n)]
    tiles = {}
    for f in ART:
        for t, nm, b in spe_entries(os.path.join(data, f'art/fore/{f}.spe')):
            if t != 5: continue
            try: num = int(nm)
            except ValueError: continue
            w, h = struct.unpack_from('<HH', b); p = 4 + w * h
            tiles[num] = (w, h, b[4:4 + w * h], b[p + 3] > 0)
    return pal, tiles


def read_fg(path):
    fg = [b for t, nm, b in spe_entries(path) if nm == 'fgmap'][0]
    w, h = struct.unpack_from('<II', fg)
    m = struct.unpack_from('<%dH' % (w * h), fg, 8)
    return w, h, [[m[y * w + x] & 0x7fff for x in range(w)] for y in range(h)]


def bfs(cells, start):
    dist = {start: 0}; q = deque([start])
    while q:
        x, y = q.popleft()
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n in cells and n not in dist:
                dist[n] = dist[(x, y)] + 1; q.append(n)
    return dist


def convert(data, level, mapname):
    pal, tiles = load_tiles(data)
    w, h, grid = read_fg(level)
    solid = lambda x, y: not (0 <= x < w and 0 <= y < h) or tiles.get(grid[y][x], (0, 0, b'', False))[3]
    # the biggest connected open area is the playable base
    seen, best = set(), set()
    for y in range(h):
        for x in range(w):
            if solid(x, y) or (x, y) in seen: continue
            comp = set(bfs({(i, j) for j in range(h) for i in range(w) if not solid(i, j)}, (x, y)))
            seen |= comp
            if len(comp) > len(best): best = comp
    cells = best
    P = lambda x, y: (x * CELL, -y * CELL)

    V, L, S, T = [], [], [], []
    vid = {}
    def v(p):
        if p not in vid: vid[p] = len(V); V.append(p)
        return vid[p]
    used = {}
    def tex(tile):
        name = f'AB{tile:04d}'[:8]
        used[name] = tile
        return name
    segs = []  # (a, b, cell faced, solid tile behind)
    for (x, y) in cells:
        if (x, y - 1) not in cells: segs.append((P(x, y), P(x + 1, y), (x, y), (x, y - 1)))
        if (x, y + 1) not in cells: segs.append((P(x + 1, y + 1), P(x, y + 1), (x, y), (x, y + 1)))
        if (x - 1, y) not in cells: segs.append((P(x, y + 1), P(x, y), (x, y), (x - 1, y)))
        if (x + 1, y) not in cells: segs.append((P(x + 1, y), P(x + 1, y + 1), (x, y), (x + 1, y)))
    start = min(cells, key=lambda c: (c[0], -c[1]))
    dist = bfs(cells, start)
    far = max(range(len(segs)), key=lambda i: dist.get(segs[i][2], 0))
    for i, (a, b, _, behind) in enumerate(segs):
        bx, by = behind
        t = grid[by][bx] if 0 <= bx < w and 0 <= by < h else 28
        mid = 'SW1COMP' if i == far else tex(t if t in tiles and tiles[t][0] else 28)
        S.append({'x': 0, 'y': 0, 'upper': '-', 'lower': '-', 'mid': mid, 'sector': 0})
        L.append({'v1': v(a), 'v2': v(b), 'flags': 1, 'special': 11 if i == far else 0, 'tag': 0,
                  'front': len(S) - 1, 'back': 0xFFFF})
    sectors = [{'floor': 0, 'ceil': 128, 'fflat': FLOOR, 'cflat': CEIL, 'light': 192, 'special': 0, 'tag': 0}]

    rnd = random.Random(1995)
    centre = lambda c: (c[0] * CELL + CELL // 2, -c[1] * CELL - CELL // 2)
    T.append(dict(zip(('x', 'y'), centre(start)), angle=0, type=1, flags=7))
    maxd = max(dist.values())
    order = sorted(cells - {start}, key=lambda c: dist[c])
    early = [c for c in order if dist[c] > 3][:2]
    for c, t in zip(early, (2001, 2002)):
        T.append(dict(zip(('x', 'y'), centre(c)), angle=0, type=t, flags=7))
    taken = set(early)
    for c in order:
        if c in taken or dist[c] < 6: continue
        r = rnd.random(); frac = dist[c] / maxd
        if 10 <= dist[c] <= 18 and r < 0.08: t = rnd.choice([3004, 3004, 9])
        elif r < 0.05: t = rnd.choice([3004, 3004, 9, 3001] + ([3002, 3001, 3006] if frac > .4 else []) + ([3005] if frac > .75 else []))
        elif r < 0.065: t = rnd.choice([2007, 2008, 2008, 2049])
        elif r < 0.075: t = rnd.choice([2011, 2012])
        elif r < 0.09: t = rnd.choice([10, 12, 15, 18, 19, 20, 24])   # Doom 1 gore only
        elif r < 0.10: t = 2028                                         # lamps light the tunnels
        else: continue
        x, y = c   # face back along the route, toward where the player comes from
        back = min(((x + 1, y, 0), (x - 1, y, 180), (x, y - 1, 90), (x, y + 1, 270)),
                   key=lambda n: dist.get((n[0], n[1]), 1 << 30))
        T.append(dict(zip(('x', 'y'), centre(c)), angle=back[2], type=t, flags=7))
        taken.add(c)
    mons = sum(t['type'] in (3004, 9, 3001, 3002, 3006, 3005) for t in T)
    print(f'{len(cells)} open tiles, {len(L)} walls, {len(used)} Abuse wall textures, {mons} monsters, route {maxd} tiles', file=sys.stderr)
    m = {'mapname': mapname, 'vertexes': V, 'linedefs': L, 'sidedefs': S, 'sectors': sectors, 'things': T}
    return m, {n: tiles[t][:3] for n, t in used.items()}, pal


def tex_lumps(textures, abuse_pal, iwad_path):
    """Patches for each Abuse tile (64x64: the tile scaled to 64x32, stacked twice), plus PNAMES and
    TEXTURE2 extended from the IWAD's so every stock texture still works."""
    d = open(iwad_path, 'rb').read(); n, o = struct.unpack_from('<II', d, 4); iw = {}
    for i in range(n):
        off, sz = struct.unpack_from('<II', d, o + 16 * i)
        iw.setdefault(d[o + 16 * i + 8:o + 16 * i + 16].rstrip(b'\0').decode('latin1'), d[off:off + sz])
    pp = iw['PLAYPAL']; dpal = [tuple(pp[i * 3:i * 3 + 3]) for i in range(256)]
    remap = []
    for c in abuse_pal:   # nearest Doom colour for each Abuse colour
        remap.append(min(range(256), key=lambda k: sum((dpal[k][j] - c[j]) ** 2 for j in range(3))))
    pn = iw['PNAMES']; count = struct.unpack_from('<i', pn)[0]
    names = [pn[4 + i * 8:12 + i * 8] for i in range(count)]
    t2 = iw['TEXTURE2']; tn = struct.unpack_from('<i', t2)[0]
    offs = struct.unpack_from(f'<{tn}i', t2, 4)
    bodies = [t2[a:b] for a, b in zip(offs, list(offs[1:]) + [len(t2)])]
    patches = []
    for name, (tw, th, px) in sorted(textures.items()):
        col_px = []
        for x in range(64):
            sx = x * tw // 64
            col = [remap[px[(min(th - 1, (y % 32) * 15 // 32)) * tw + sx]] for y in range(64)]
            col_px.append(col)
        cols = [bytes([0, 64, 0]) + bytes(c) + bytes([0, 255]) for c in col_px]
        head = struct.pack('<hhhh', 64, 64, 0, 0); at = 8 + 4 * 64; table = []
        for c in cols: table.append(at); at += len(c)
        patches.append((name, head + struct.pack('<64I', *table) + b''.join(cols)))
        idx = len(names); names.append(name.encode().ljust(8, b'\0'))
        bodies.append(name.encode().ljust(8, b'\0') + struct.pack('<ihhih', 0, 64, 64, 0, 1) + struct.pack('<hhhhh', 0, 0, idx, 1, 0))
    pnames = struct.pack('<i', len(names)) + b''.join(names)
    k = len(bodies); at = 4 + 4 * k; table = []
    for b in bodies: table.append(at); at += len(b)
    tex2 = struct.pack('<i', k) + struct.pack(f'<{k}i', *table) + b''.join(bodies)
    return patches + [('PNAMES', pnames), ('TEXTURE2', tex2)]


def add_lumps(wad_path, extra):
    d = open(wad_path, 'rb').read(); n, o = struct.unpack_from('<II', d, 4)
    dirs = [d[o + 16 * i:o + 16 * i + 16] for i in range(n)]
    body = bytearray(d[:o])
    for name, data in extra:
        dirs.append(struct.pack('<II', len(body), len(data)) + name.encode().ljust(8, b'\0')); body += data
    struct.pack_into('<II', body, 4, len(dirs), len(body))
    open(wad_path, 'wb').write(bytes(body) + b''.join(dirs))


if __name__ == '__main__':
    if sys.argv[1] == '--addtex':
        add_lumps(sys.argv[2], pickle.load(open(sys.argv[3], 'rb')))
    else:
        data, level, out = sys.argv[1], sys.argv[2], sys.argv[3]
        mapname = sys.argv[4] if len(sys.argv) > 4 else 'E1M1'
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'quake2doom'))
        from quake2doom import write_wad
        m, textures, pal = convert(data, level, mapname)
        write_wad(m, out + '.raw.wad')
        pickle.dump(tex_lumps(textures, pal, sys.argv[5]), open(out + '.tex', 'wb'))
