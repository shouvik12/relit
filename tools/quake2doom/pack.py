"""Join node-built single-map WADs into one PWAD, in the order given.

    python3 tools/quake2doom/pack.py maps/quake-e1.wad e1m1.wad e1m2.wad e1m3.wad e1m4.wad

Relit appends this PWAD to the player's game file, so these maps replace E1M1-E1M4
for the session and Doom's normal level order carries the player from one to the next.
"""
import struct
import sys


def lumps(path):
    d = open(path, 'rb').read()
    assert d[:4] in (b'PWAD', b'IWAD'), path
    n, off = struct.unpack_from('<II', d, 4)
    for i in range(n):
        pos, size, name = struct.unpack_from('<II8s', d, off + 16 * i)
        yield name, d[pos:pos + size]


def main(out, *inputs):
    allx = [l for p in inputs for l in lumps(p)]
    data, dirs, at = b'', b'', 12
    for name, b in allx:
        dirs += struct.pack('<II', at if b else 0, len(b)) + name
        data += b; at += len(b)
    open(out, 'wb').write(b'PWAD' + struct.pack('<II', len(allx), at) + data + dirs)
    maps = [n.rstrip(b'\0').decode() for n, _ in allx if n.startswith(b'E') and n[2:3] == b'M']
    print(out, len(data) // 1024, 'KB:', ' '.join(maps))


if __name__ == '__main__':
    main(*sys.argv[1:])
