"""Reads Ogre-Next v2 meshes ([MeshSerializer_v2.1 R2]), as used by Stunt Rally 3.

Returns, for each submesh, its material name and the first LOD's triangles:
positions, normals and first texture coordinates as float lists, plus indices.
Only what Relit needs; skeletons, poses and animations are skipped.
"""
import struct

# vertex element types: (struct code, count, normalise divisor or None)
TYPES = {
    0: ('f', 1, None), 1: ('f', 2, None), 2: ('f', 3, None), 3: ('f', 4, None),
    4: ('B', 4, None), 6: ('h', 2, None), 8: ('h', 4, None), 9: ('B', 4, None),
    10: ('B', 4, None), 11: ('B', 4, None), 17: ('H', 2, None), 19: ('H', 4, None),
    20: ('i', 1, None), 21: ('i', 2, None), 22: ('i', 3, None), 23: ('i', 4, None),
    24: ('I', 1, None), 25: ('I', 2, None), 26: ('I', 3, None), 27: ('I', 4, None),
    28: ('b', 4, None), 29: ('b', 4, 127.0), 30: ('B', 4, 255.0),
    31: ('h', 2, 32767.0), 32: ('h', 4, 32767.0), 33: ('H', 2, 65535.0), 34: ('H', 4, 65535.0),
    35: ('e', 2, None), 36: ('e', 4, None),
}
POSITION, NORMAL, TEXCOORD, TANGENT = 1, 4, 7, 9


class Reader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def u8(self):
        v = self.d[self.p]; self.p += 1; return v

    def u16(self):
        v = struct.unpack_from('<H', self.d, self.p)[0]; self.p += 2; return v

    def u32(self):
        v = struct.unpack_from('<I', self.d, self.p)[0]; self.p += 4; return v

    def string(self):
        e = self.d.index(b'\n', self.p); s = self.d[self.p:e].decode('latin-1'); self.p = e + 1; return s

    def chunk(self):
        if self.p + 6 > len(self.d): return None
        cid = self.u16(); self.u32(); return cid

    def back(self):
        self.p -= 6


def read(path):
    r = Reader(open(path, 'rb').read())
    assert r.u16() == 0x1000
    version = r.string()
    assert 'v2.1' in version, version
    assert r.chunk() == 0x3000
    r.string()                    # LOD strategy
    passes = r.u8()
    subs = []
    while True:
        cid = r.chunk()
        if cid is None: break
        if cid == 0x4000:
            subs.append(read_submesh(r, passes))
        elif cid in (0x9000, 0xA000, 0x3200, 0x6000, 0x8000):
            # chunks after the submeshes: bounds, name table, hashes, skeleton, lod levels
            break
        else:
            break
    return subs


def read_submesh(r, passes):
    material = r.string()
    nb = r.u8()
    r.p += 2 * nb
    nlod = r.u8()
    lods = []
    for _ in range(passes):
        for _ in range(nlod):
            assert r.chunk() == 0x4300
            lods.append(read_lod(r))
    return dict(material=material, **lods[0])


def read_lod(r):
    n = r.u32(); idx = []
    if n:
        big = r.u8()
        fmt = '<%d%s' % (n, 'I' if big else 'H')
        idx = list(struct.unpack_from(fmt, r.d, r.p)); r.p += n * (4 if big else 2)
    out = dict(indices=idx, op=4, decl=None, verts=None, count=0, source=None)
    while True:
        cid = r.chunk()
        if cid == 0x4330:
            read_geometry(r, out)
        elif cid == 0x4340:
            out['source'] = r.u8()
        elif cid == 0x4310:
            out['op'] = r.u16()
        else:
            if cid is not None: r.back()
            break
    return out


def read_geometry(r, out):
    count = r.u32(); nsrc = r.u8()
    decls, bufs = [None] * nsrc, [None] * nsrc
    while True:
        cid = r.chunk()
        if cid == 0x4331:
            for s in range(nsrc):
                ne = r.u8(); decls[s] = [(r.u8(), r.u8()) for _ in range(ne)]
        elif cid == 0x4332:
            src = r.u8(); size = r.u8()
            bufs[src] = (size, r.d[r.p:r.p + size * count]); r.p += size * count
        else:
            if cid is not None: r.back()
            break
    out['count'] = count
    out.update(decode(decls[0], bufs[0], count))


def decode(decl, buf, count):
    size, data = buf
    attrs = {POSITION: [], NORMAL: [], TEXCOORD: []}
    off, layout = 0, []
    for t, sem in decl:
        code, n, norm = TYPES[t]
        layout.append((off, code, n, norm, sem))
        off += struct.calcsize('<' + code) * n
    assert off == size, (off, size, decl)
    seen = set()
    for i in range(count):
        base = i * size
        for o, code, n, norm, sem in layout:
            if sem not in attrs: continue
            if sem == TEXCOORD and (sem, 'done') in seen: continue
            v = struct.unpack_from('<%d%s' % (n, code), data, base + o)
            if norm: v = [x / norm for x in v]
            want = 3 if sem in (POSITION, NORMAL) else 2
            attrs[sem].extend(list(v[:want]) + [0.0] * (want - len(v[:want])))
        if i == 0:
            # use only the first texture-coordinate set
            pass
    # if several texture coordinate sets exist, the loop above took all; keep the first per vertex
    tc = [l for l in layout if l[4] == TEXCOORD]
    if len(tc) > 1:
        first = tc[0]
        attrs[TEXCOORD] = []
        for i in range(count):
            v = struct.unpack_from('<%d%s' % (first[2], first[1]), data, i * size + first[0])
            if first[3]: v = [x / first[3] for x in v]
            attrs[TEXCOORD].extend(v[:2])
    return dict(pos=attrs[POSITION], nrm=attrs[NORMAL], uv=attrs[TEXCOORD])
