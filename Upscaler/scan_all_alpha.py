#!/usr/bin/env python3
"""
Scan ALL DXT5 VTFs in Content for all-white alpha (broken transparency).
This catches ALL alpha-dependent materials, not just $blendtintbybasealpha.
Includes: $translucent, $alphatest, $blendtintbybasealpha, etc.

Uses header-only format check first (fast), then only does full alpha analysis on DXT5 files.
"""
import os, struct, sys, time

CONTENT = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials"
SOURCES = [
    r"C:\Users\Alexander Jarvis\Desktop\Extract Me\battalion5-3864532439\materials",
    r"C:\Users\Alexander Jarvis\Desktop\CWRP Installer\2026 SP Helper\materials",
    r"C:\Users\Alexander Jarvis\Desktop\Extract Me\battalion2-2582938939\materials",
]

def vtf_hdr(fp):
    try:
        with open(fp, 'rb') as f:
            d = f.read(80)
            if d[:4] != b'VTF\x00': return None
            w = struct.unpack_from('<H', d, 16)[0]
            h = struct.unpack_from('<H', d, 18)[0]
            fmt = struct.unpack_from('<I', d, 52)[0]
            return w, h, fmt
    except:
        return None

def find_original(rel_path):
    for src in SOURCES:
        candidate = os.path.join(src, rel_path)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(candidate)
        fname = os.path.basename(candidate).lower()
        if os.path.isdir(parent):
            for f in os.listdir(parent):
                if f.lower() == fname:
                    return os.path.join(parent, f)
    return None

# Scan ALL DXT5 VTFs
print("Scanning all DXT5 VTFs in Content folder...")
start = time.time()

all_dxt5 = []
total_vtf = 0

for root, _, files in os.walk(CONTENT):
    for f in files:
        if not f.lower().endswith('.vtf'):
            continue
        total_vtf += 1
        fp = os.path.join(root, f)
        info = vtf_hdr(fp)
        if info and info[2] == 15:  # DXT5
            all_dxt5.append(fp)

print(f"  Total VTFs: {total_vtf}")
print(f"  DXT5 VTFs: {len(all_dxt5)}")
print(f"  Header scan took {time.time()-start:.1f}s")

# Check each DXT5 VTF for all-white alpha
print("\nChecking alpha channels on DXT5 VTFs...")
from srctools.vtf import VTF
import numpy as np

broken = []
already_fixed = []
ok = []
errors = []

for i, fp in enumerate(all_dxt5):
    if (i+1) % 100 == 0:
        print(f"  Progress: {i+1}/{len(all_dxt5)}...")
    
    rel = os.path.relpath(fp, os.path.dirname(CONTENT))  # rel to Content dir
    
    # Skip backups
    if '_vtf_backups' in fp:
        continue
    
    try:
        v = VTF.read(open(fp, 'rb'))
        img = v.get().to_PIL()
        if img.mode != 'RGBA':
            continue
        _, _, _, a = img.split()
        arr = np.array(a)
        
        if arr.min() >= 254:  # All white alpha
            info = vtf_hdr(fp)
            w, h = info[0], info[1] if info else (0, 0)
            
            # Check if we already fixed this (exists in backup)
            backup_check = fp.replace(os.sep + 'materials' + os.sep, os.sep + '_vtf_backups' + os.sep + 'materials' + os.sep)
            
            # Check if original has non-white alpha
            orig_rel = os.path.relpath(fp, os.path.dirname(CONTENT))
            if orig_rel.startswith('materials\\'):
                orig_rel_clean = orig_rel
            else:
                orig_rel_clean = 'materials\\' + orig_rel
            
            orig = find_original(orig_rel)
            
            has_orig = orig is not None
            orig_has_alpha = False
            if has_orig:
                try:
                    ov = VTF.read(open(orig, 'rb'))
                    oi = ov.get().to_PIL()
                    if oi.mode == 'RGBA':
                        _, _, _, oa = oi.split()
                        oarr = np.array(oa)
                        orig_has_alpha = oarr.min() < 254
                except:
                    pass
            
            broken.append({
                'path': fp,
                'rel': rel,
                'size': f"{w}x{h}",
                'has_orig': has_orig,
                'orig_has_alpha': orig_has_alpha,
                'orig': orig,
            })
    except:
        errors.append(rel)

print(f"\nScan complete in {time.time()-start:.1f}s")
print(f"  DXT5 with all-white alpha: {len(broken)}")
print(f"  Errors: {len(errors)}")

# Categorize
fixable = [b for b in broken if b['orig_has_alpha']]
unfixable_no_orig = [b for b in broken if not b['has_orig']]
unfixable_orig_white = [b for b in broken if b['has_orig'] and not b['orig_has_alpha']]

print(f"\n=== RESULTS ===")
print(f"Fixable (original has alpha): {len(fixable)}")
print(f"Unfixable - no original found: {len(unfixable_no_orig)}")
print(f"Unfixable - original also all-white: {len(unfixable_orig_white)}")

if fixable:
    print(f"\n--- Fixable files ({len(fixable)}) ---")
    for b in fixable:
        print(f"  {b['rel']} ({b['size']})")

if unfixable_no_orig:
    print(f"\n--- No original found ({len(unfixable_no_orig)}) ---")
    for b in unfixable_no_orig[:30]:
        print(f"  {b['rel']} ({b['size']})")
    if len(unfixable_no_orig) > 30:
        print(f"  ... and {len(unfixable_no_orig)-30} more")
