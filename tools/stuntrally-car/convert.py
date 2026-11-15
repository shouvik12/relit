"""Converts Stunt Rally 3 cars into Relit's small car format (.rcar).

Usage: python3 convert.py <stuntrally3/data> <out dir> [car ids...]
Reads data/cars/<ID>/*.mesh (Ogre-Next v2), the textures beside them and the
wheel positions in data/carsim/normal/cars/<ID>.car.

.rcar layout: 'RCAR', u32 length of the JSON header, the JSON, then 4-aligned
binary blocks the header points into: int16 positions (times 'scale'), int8
normals (padded to 4), int16 texture coordinates (/ 4096), uint16/uint32
indices, and JPEG or PNG textures.
"""
import io, json, os, re, struct, sys
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ogre_mesh

CARS = {
    # id: display name, body paint (sRGB hex), body metalness, roughness,
    # sag: how far the wheels sit above the .car file's fully dropped suspension (metres)
    'LK4': dict(name='Ironhide', paint='7d2f26', metal=0.55, rough=0.45, sag=0.2),
    'XZ': dict(name='Quicksilver', paint='b9c2cc', metal=0.8, rough=0.3, sag=0.04),
    'OT': dict(name='Old Thunder', paint='1d5c34', metal=0.4, rough=0.5, sag=0.08),
}
TEX_MAX = 512


def carsim(path):
    txt = open(path).read()
    sect, out = None, {}
    for line in txt.splitlines():
        line = line.split('#')[0].strip()
        m = re.match(r'\[\s*(.+?)\s*\]', line)
        if m: sect = m.group(1); continue
        if '=' in line and sect:
            k, v = [x.strip() for x in line.split('=', 1)]
            out.setdefault(sect, {})[k] = v
    return out


def vec(s): return [float(x) for x in s.split(',')]


class Blob:
    def __init__(self): self.b = bytearray()

    def add(self, data):
        while len(self.b) % 4: self.b.append(0)
        off = len(self.b); self.b += data; return off


def texture(path, blob, cache):
    if path in cache: return cache[path]
    im = Image.open(path)
    alpha = im.mode in ('RGBA', 'LA', 'P') and im.convert('RGBA').getextrema()[3][0] < 255
    im = im.convert('RGBA' if alpha else 'RGB')
    if max(im.size) > TEX_MAX: im.thumbnail((TEX_MAX, TEX_MAX), Image.LANCZOS)
    buf = io.BytesIO()
    if alpha: im.save(buf, 'PNG', optimize=True); mime = 'image/png'
    else: im.save(buf, 'JPEG', quality=82, optimize=True); mime = 'image/jpeg'
    cache[path] = len(cache['_list']); cache['_list'].append(dict(off=blob.add(buf.getvalue()), len=len(buf.getvalue()), mime=mime))
    return cache[path]


def part(sub, kind, tex, blob):
    pos, nrm, uv, idx = sub['pos'], sub['nrm'], sub['uv'], sub['indices']
    n = len(pos) // 3
    scale = max(1e-6, max(abs(x) for x in pos)) / 32767
    p = struct.pack('<%dh' % len(pos), *[round(x / scale) for x in pos])
    nn = bytearray()
    for i in range(n):
        x, y, z = (nrm[i * 3:i * 3 + 3] if nrm else (0, 1, 0))
        nn += struct.pack('<4b', round(max(-1, min(1, x)) * 127), round(max(-1, min(1, y)) * 127), round(max(-1, min(1, z)) * 127), 0)
    u = struct.pack('<%dh' % (n * 2), *[round(max(-7.99, min(7.99, v)) * 4096) for v in (uv or [0.0] * (n * 2))])
    big = n > 65535
    ib = struct.pack('<%d%s' % (len(idx), 'I' if big else 'H'), *idx)
    return dict(kind=kind, tex=tex, count=n, scale=scale, pos=blob.add(p), nrm=blob.add(bytes(nn)), uv=blob.add(u),
                idx=blob.add(ib), nidx=len(idx), big=big)


def convert(data, out, cid):
    cfg = CARS[cid]
    d = os.path.join(data, 'cars', cid)
    sim = carsim(os.path.join(data, 'carsim', 'normal', 'cars', cid + '.car'))
    blob, cache = Blob(), {'_list': []}
    texdir = os.path.join(d, 'textures')

    def tex_for(name):
        f = os.path.join(texdir, name)
        return texture(f, blob, cache) if os.path.exists(f) else None

    parts = []
    files = sorted(f for f in os.listdir(d) if f.endswith('.mesh'))
    for f in files:
        base = f[:-5][len(cid) + 1:]
        if base == 'brake': continue
        kind = 'wheelR' if base == 'wheel_rear' else 'wheel' if base.startswith('wheel') else base
        if kind not in ('body', 'glass', 'interior', 'wheel', 'wheelR'): continue
        tname = {'body': cid + '_body_red.png', 'glass': cid + '_glass.png', 'interior': cid + '_interior.png'}.get(kind)
        if kind in ('wheel', 'wheelR'):
            tname = next((t for t in os.listdir(texdir) if t.lower() in (cid.lower() + '_wheel.png', cid.lower() + '_tire.png')), None)
        for sub in ogre_mesh.read(os.path.join(d, f)):
            if not sub['pos'] or len(sub['indices']) < 3: continue
            parts.append(part(sub, kind, tex_for(tname) if tname else None, blob))

    tyre_f = sim.get('tire-front', sim.get('tire-both', {})).get('radius')
    tyre_r = sim.get('tire-rear', sim.get('tire-both', {})).get('radius')
    wheels = []
    for w in ('FR', 'FL', 'RR', 'RL'):
        x, y, z = vec(sim['wheel-' + w]['position'])
        wheels.append(dict(x=x, fwd=y, up=z + cfg['sag'], r=float(tyre_f if w[0] == 'F' else tyre_r), front=w[0] == 'F', left=w[1] == 'L'))
    about = open(os.path.join(d, 'about.txt')).read()
    author = re.search(r'Author[s]?:\s*(.+)', about); lic = re.search(r'License:\s*(.+)', about)
    head = dict(id=cid, name=cfg['name'], paint=cfg['paint'], metal=cfg['metal'], rough=cfg['rough'],
                author=author.group(1).strip() if author else '', license=lic.group(1).strip() if lic else '',
                parts=parts, wheels=wheels, textures=cache['_list'])
    hj = json.dumps(head, separators=(',', ':')).encode()
    hj += b' ' * ((4 - (8 + len(hj)) % 4) % 4)
    path = os.path.join(out, cid + '.rcar')
    with open(path, 'wb') as fh:
        fh.write(b'RCAR' + struct.pack('<I', len(hj)) + hj + bytes(blob.b))
    print(path, round(os.path.getsize(path) / 1024), 'KB', len(parts), 'parts')


if __name__ == '__main__':
    data, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    for cid in (sys.argv[3:] or list(CARS)): convert(data, out, cid)
