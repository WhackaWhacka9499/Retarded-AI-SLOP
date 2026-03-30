"""Compare VTF headers: generated vs original."""
import struct, os, sys
from pathlib import Path

CONTENT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")
FMT_NAMES = {0:'RGBA8888', 2:'RGB888', 13:'DXT1', 15:'DXT5', 12:'DXT1_1BIT'}

def dump_vtf(path, label):
    with open(path, 'rb') as f:
        hdr = f.read(80)
    sz = os.path.getsize(path)
    print(f"\n=== {label}: {path.name} ({sz} bytes) ===")
    print(f"  Signature: {hdr[0:4]}")
    
    ver = struct.unpack_from('<II', hdr, 4)
    print(f"  Version: {ver[0]}.{ver[1]}")
    
    hdr_size = struct.unpack_from('<I', hdr, 12)[0]
    print(f"  Header size: {hdr_size}")
    
    w, h = struct.unpack_from('<HH', hdr, 16)
    print(f"  Dimensions: {w}x{h}")
    
    flags = struct.unpack_from('<I', hdr, 20)[0]
    print(f"  Flags: 0x{flags:08x}")
    
    frames, first = struct.unpack_from('<HH', hdr, 24)
    print(f"  Frames: {frames}, First: {first}")
    
    pad0 = struct.unpack_from('<I', hdr, 28)[0]
    print(f"  Padding0 [28-31]: 0x{pad0:08x} (raw bytes: {hdr[28]:02x} {hdr[29]:02x} {hdr[30]:02x} {hdr[31]:02x})")
    
    refl = struct.unpack_from('<fff', hdr, 32)
    print(f"  Reflectivity: ({refl[0]:.4f}, {refl[1]:.4f}, {refl[2]:.4f})")
    
    pad1 = struct.unpack_from('<I', hdr, 44)[0]
    print(f"  Padding1 [44-47]: 0x{pad1:08x}")
    
    bump = struct.unpack_from('<f', hdr, 48)[0]
    print(f"  Bump scale: {bump}")
    
    fmt = struct.unpack_from('<I', hdr, 52)[0]
    print(f"  Format [52-55]: {fmt} = {FMT_NAMES.get(fmt, 'UNKNOWN')}")
    
    mips = hdr[56]
    print(f"  Mipmap count [56]: {mips}")
    
    lo_fmt = struct.unpack_from('<I', hdr, 57)[0]  # Note: misaligned
    lo_w = hdr[61]
    lo_h = hdr[62]
    print(f"  Low-res format [57]: {lo_fmt}")
    print(f"  Low-res dims: {lo_w}x{lo_h}")
    
    # Raw hex of first 64 bytes
    for off in range(0, 64, 16):
        chunk = hdr[off:off+16]
        hex_str = ' '.join(f'{b:02x}' for b in chunk)
        print(f"  [{off:3d}] {hex_str}")

# Compare a few files
samples = [
    'particle/beam001.vtf',
    'effects/beam001.vtf',
    'pfx/fire_basic_nocolor.vtf',
    'sprites/fire.vtf',
]

for s in samples:
    cur = CONTENT / s
    bak = Path(str(cur) + '.bak')
    
    if not cur.exists():
        print(f"\n{s}: MISSING")
        continue
    
    if bak.exists():
        dump_vtf(bak, "ORIGINAL .bak")
    dump_vtf(cur, "GENERATED")
    print()
