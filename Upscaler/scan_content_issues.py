#!/usr/bin/env python3
"""Content Folder Scanner - Detect broken upscaled VTFs."""
import os, re, struct, sys

# Force ASCII-safe output
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(errors='replace')

CONTENT = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content"
NORMAL_PAT = ['_normal', '_n.', '_bump', '_b.', '_nrm', '_nm', '_nm.']
FMT = {0:'RGBA8888', 2:'RGB888', 3:'BGR888', 12:'BGRA8888', 13:'DXT1', 14:'DXT3', 15:'DXT5'}

def vtf_hdr(fp):
    try:
        with open(fp, 'rb') as f:
            d = f.read(80)
            if d[:4] != b'VTF\x00': return None
            w = struct.unpack_from('<H', d, 16)[0]
            h = struct.unpack_from('<H', d, 18)[0]
            fmt = struct.unpack_from('<I', d, 52)[0]
            return w, h, fmt, os.path.getsize(fp)
    except:
        return None

def alpha_check(fp):
    try:
        from srctools.vtf import VTF
        import numpy as np
        v = VTF.read(open(fp, 'rb'))
        img = v.get().to_PIL()
        if img.mode != 'RGBA': return False, -1
        _, _, _, a = img.split()
        arr = np.array(a)
        return arr.min() >= 254, float(arr.mean())
    except:
        return False, -1

mat = os.path.join(CONTENT, 'materials')

# === SCAN 1: blendtintbybasealpha ===
print("=== SCAN 1: blendtintbybasealpha alpha issues ===")
broken = []
checked = 0
for root, _, files in os.walk(mat):
    for f in files:
        if not f.lower().endswith('.vmt'): continue
        p = os.path.join(root, f)
        try:
            c = open(p, 'r', errors='ignore').read().lower()
        except:
            continue
        if '$blendtintbybasealpha' not in c: continue
        m = re.search(r'"\$basetexture"\s*"([^"]+)"', c)
        if not m: continue
        vp = os.path.join(mat, m.group(1).replace('/', os.sep) + '.vtf')
        if not os.path.isfile(vp): continue
        info = vtf_hdr(vp)
        if not info or info[2] != 15: continue
        checked += 1
        bad, ma = alpha_check(vp)
        rel = os.path.relpath(vp, CONTENT)
        if bad:
            broken.append(rel)
            print(f"  [BROKEN] {rel} ({info[0]}x{info[1]}) alpha_mean={ma:.1f}")
        else:
            print(f"  [OK] {rel} ({info[0]}x{info[1]}) alpha_mean={ma:.1f}")

print(f"\n  Checked {checked}, broken: {len(broken)}")

# === SCAN 2: Upscaled normal maps ===
print("\n=== SCAN 2: Upscaled normal maps (lighting bugs) ===")
norms = []
for root, _, files in os.walk(mat):
    for f in files:
        if not f.lower().endswith('.vtf'): continue
        lo = f.lower()
        is_nm = any(p in lo for p in NORMAL_PAT)
        if not is_nm: continue
        vp = os.path.join(root, f)
        info = vtf_hdr(vp)
        if not info: continue
        w, h, fmt_id, sz = info
        if w > 1024 or h > 1024:
            rel = os.path.relpath(vp, CONTENT)
            norms.append(rel)
            print(f"  [UPSCALED-NM] {rel} ({w}x{h} {FMT.get(fmt_id, str(fmt_id))} {sz/(1024*1024):.1f}MB)")

print(f"\n  Total upscaled normals: {len(norms)}")

# === SCAN 3: VMT $bumpmap refs ===
print("\n=== SCAN 3: VMT $bumpmap refs to upscaled VTFs ===")
bumps = []
for root, _, files in os.walk(mat):
    for f in files:
        if not f.lower().endswith('.vmt'): continue
        p = os.path.join(root, f)
        try:
            c = open(p, 'r', errors='ignore').read()
        except:
            continue
        m = re.search(r'"\$bumpmap"\s*"([^"]+)"', c, re.IGNORECASE)
        if not m: continue
        bp = os.path.join(mat, m.group(1).replace('/', os.sep) + '.vtf')
        if not os.path.isfile(bp): continue
        info = vtf_hdr(bp)
        if not info: continue
        if info[0] > 1024 or info[1] > 1024:
            rel = os.path.relpath(bp, CONTENT)
            if rel not in norms:
                bumps.append(rel)
                print(f"  [UPSCALED-BUMP] {rel} ({info[0]}x{info[1]} {FMT.get(info[2], str(info[2]))})")

print(f"\n  Additional upscaled bumpmaps: {len(bumps)}")

# === SUMMARY ===
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Broken alpha (blendtint): {len(broken)}")
for b in broken:
    print(f"  - {b}")
print(f"Upscaled normal maps: {len(norms)}")
print(f"Upscaled bumpmaps (by VMT ref): {len(bumps)}")
print(f"Total lighting-affecting issues: {len(norms) + len(bumps)}")
