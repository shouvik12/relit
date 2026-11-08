"""Load Real-ESRGAN SRVGGNetCompact weights from a .pth without PyTorch,
and run the network in NumPy (reference for the browser version)."""
import zipfile, pickle, numpy as np, sys

class _Storage:
    def __init__(self, key, dtype): self.key, self.dtype = key, dtype

def load_pth(path):
    zf = zipfile.ZipFile(path)
    prefix = zf.namelist()[0].split('/')[0]
    dtypes = {'FloatStorage': np.float32, 'HalfStorage': np.float16}
    class U(pickle.Unpickler):
        def find_class(self, mod, name):
            if name == '_rebuild_tensor_v2':
                def rebuild(storage, offset, size, stride, *a):
                    raw = zf.read(f'{prefix}/data/{storage.key}')
                    arr = np.frombuffer(raw, dtype=storage.dtype)
                    n = int(np.prod(size)) if size else 1
                    return np.lib.stride_tricks.as_strided(arr[offset:], shape=size, strides=[s * arr.itemsize for s in stride]).copy() if size else arr[offset:offset+1].copy()
                return rebuild
            if name in dtypes: return name
            if mod == 'collections' and name == 'OrderedDict':
                import collections; return collections.OrderedDict
            return super().find_class(mod, name)
        def persistent_load(self, pid):
            _, stype, key, loc, n = pid
            return _Storage(key, dtypes[stype if isinstance(stype, str) else stype.__name__])
    sd = U(zf.open(f'{prefix}/data.pkl')).load()
    if 'params' in sd: sd = sd['params']
    return {k: np.asarray(v, dtype=np.float32) for k, v in sd.items()}

def conv3x3(x, w, b):
    # x: (C,H,W)  w: (O,C,3,3)
    C, H, W = x.shape
    p = np.pad(x, ((0, 0), (1, 1), (1, 1)), mode='constant')
    cols = np.empty((C, 9, H, W), np.float32)
    for dy in range(3):
        for dx in range(3):
            cols[:, dy * 3 + dx] = p[:, dy:dy + H, dx:dx + W]
    out = w.reshape(w.shape[0], -1) @ cols.reshape(C * 9, H * W)
    return out.reshape(-1, H, W) + b[:, None, None]

def run(sd, img):
    """img: (H,W,3) float 0..1 -> (4H,4W,3)"""
    convs = sorted({int(k.split('.')[1]) for k in sd if k.endswith('.weight') and sd[k].ndim == 4})
    x = img.transpose(2, 0, 1).astype(np.float32)
    h = x
    for i, ci in enumerate(convs):
        h = conv3x3(h, sd[f'body.{ci}.weight'], sd[f'body.{ci}.bias'])
        if i < len(convs) - 1:
            a = sd[f'body.{ci + 1}.weight'][:, None, None]
            h = np.where(h >= 0, h, h * a)
    r = 4
    C, H, W = h.shape
    out = h.reshape(3, r, r, H, W).transpose(0, 3, 1, 4, 2).reshape(3, H * r, W * r)
    out += np.repeat(np.repeat(x, r, axis=1), r, axis=2)
    return np.clip(out.transpose(1, 2, 0), 0, 1)

if __name__ == '__main__':
    sd = load_pth(sys.argv[1])
    for k, v in list(sd.items())[:4]: print(k, v.shape)
    print(len(sd), 'tensors')
