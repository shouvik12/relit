"""Pack SRVGGNetCompact weights for the browser.
Layout (all float16, little endian):
  header: 'RLAI' u32 version, u32 nlayers, then per layer: u32 cin, u32 cout, u32 has_prelu
  then per layer: weights in GPU order:
     for each output group k (4 channels): bias(4), alpha(4),
        for each input group g, tap t (dy*3+dx): 4 columns x 4 = 16 values,
        column i = weights[4k+0..3, 4g+i, t]
"""
import sys, struct, numpy as np
sys.path.insert(0, 'tools')
from srvgg import load_pth
sd = load_pth(sys.argv[1])
convs = sorted({int(k.split('.')[1]) for k in sd if k.endswith('.weight') and sd[k].ndim == 4})
head = [b'RLAI', struct.pack('<II', 1, len(convs))]
blobs = []
for i, ci in enumerate(convs):
    W = sd[f'body.{ci}.weight']; B = sd[f'body.{ci}.bias']
    cout, cin = W.shape[:2]
    prelu = i < len(convs) - 1
    A = sd[f'body.{ci+1}.weight'] if prelu else np.zeros(cout, np.float32)
    gin, gout = (cin + 3) // 4, (cout + 3) // 4
    Wp = np.zeros((gout * 4, gin * 4, 3, 3), np.float32); Wp[:cout, :cin] = W
    Bp = np.zeros(gout * 4, np.float32); Bp[:cout] = B
    Ap = np.zeros(gout * 4, np.float32); Ap[:cout] = A
    out = []
    for k in range(gout):
        out.append(Bp[4*k:4*k+4]); out.append(Ap[4*k:4*k+4])
        for g in range(gin):
            for t in range(9):
                dy, dx = divmod(t, 3)
                for col in range(4):
                    out.append(Wp[4*k:4*k+4, 4*g+col, dy, dx])
    blobs.append(np.concatenate(out).astype(np.float16).tobytes())
    head.append(struct.pack('<III', cin, cout, int(prelu)))
data = b''.join(head) + b''.join(blobs)
open(sys.argv[2], 'wb').write(data)
print(sys.argv[2], len(data), 'bytes', len(convs), 'layers')
