import struct, sys, json
d=open(sys.argv[1],'rb').read()
n,off=struct.unpack_from('<II',d,4)
lumps=[]
for i in range(n):
    p,s,nm=struct.unpack_from('<II8s',d,off+16*i); lumps.append((nm.rstrip(b'\0').decode('latin1'),p,s))
names=[l[0] for l in lumps]
def lump(nm): 
    for l in lumps:
        if l[0]==nm: return d[l[1]:l[1]+l[2]]
tx=lump('TEXTURE1'); cnt=struct.unpack_from('<I',tx,0)[0]
texs=[]
for i in range(cnt):
    o=struct.unpack_from('<I',tx,4+4*i)[0]; nm=tx[o:o+8].rstrip(b'\0').decode(); w,h=struct.unpack_from('<HH',tx,o+12); texs.append((nm,w,h))
a=names.index('F_START'); b=names.index('F_END')
flats=[x for x in names[a+1:b] if x not in('F1_START','F1_END','F2_START','F2_END','F3_START','F3_END')]
json.dump({'textures':texs,'flats':flats},open(sys.argv[2] if len(sys.argv)>2 else 'freedoom1_textures.json','w'))
print(len(texs),'textures',len(flats),'flats')
print(' '.join(t[0] for t in texs))
print(' '.join(flats))
