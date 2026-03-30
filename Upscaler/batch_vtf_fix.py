#!/usr/bin/env python3
"""
Batch VTF Fix Script
Phase 1: Check coverage - how many broken files have originals available
Phase 2: Fix alpha channels using original alpha + deployed RGB
Phase 3: Restore original normal/bump maps
"""
import os, re, struct, sys, shutil, time
from pathlib import Path

CONTENT = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content"
SOURCES = [
    r"C:\Users\Alexander Jarvis\Desktop\Extract Me\battalion5-3864532439",
    r"C:\Users\Alexander Jarvis\Desktop\CWRP Installer\2026 SP Helper",
    r"C:\Users\Alexander Jarvis\Desktop\Extract Me\battalion2-2582938939",
]
BACKUP_DIR = os.path.join(CONTENT, "_vtf_backups")

# ---- Phase 0: Parse scan results ----
def parse_scan_results(filepath):
    broken_alpha = []
    upscaled_normals = []
    upscaled_bumps = []
    section = None
    with open(filepath, 'r', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if "SCAN 1:" in line: section = "alpha"
            elif "SCAN 2:" in line: section = "normals"
            elif "SCAN 3:" in line: section = "bumps"
            elif "SUMMARY" in line: section = None
            
            if section == "alpha" and "[BROKEN]" in line:
                # e.g. [BROKEN] materials\foo\bar.vtf (4096x4096) alpha_mean=255.0
                m = re.search(r'\[BROKEN\]\s+(.+?\.vtf)', line)
                if m:
                    rel = m.group(1).strip()
                    if rel not in broken_alpha:
                        broken_alpha.append(rel)
            elif section == "normals" and "[UPSCALED-NM]" in line:
                m = re.search(r'\[UPSCALED-NM\]\s+(.+?\.vtf)', line)
                if m:
                    rel = m.group(1).strip()
                    if rel not in upscaled_normals:
                        upscaled_normals.append(rel)
            elif section == "bumps" and "[UPSCALED-BUMP]" in line:
                m = re.search(r'\[UPSCALED-BUMP\]\s+(.+?\.vtf)', line)
                if m:
                    rel = m.group(1).strip()
                    if rel not in upscaled_bumps:
                        upscaled_bumps.append(rel)
    
    return broken_alpha, upscaled_normals, upscaled_bumps

def find_original(rel_path):
    """Find original VTF in source directories. Returns full path or None."""
    for src in SOURCES:
        candidate = os.path.join(src, rel_path)
        if os.path.isfile(candidate):
            return candidate
        # Try case-insensitive
        parent = os.path.dirname(candidate)
        fname = os.path.basename(candidate)
        if os.path.isdir(parent):
            for f in os.listdir(parent):
                if f.lower() == fname.lower():
                    return os.path.join(parent, f)
    return None

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

def backup_file(deployed_path):
    """Create backup in _vtf_backups preserving directory structure."""
    rel = os.path.relpath(deployed_path, CONTENT)
    backup_path = os.path.join(BACKUP_DIR, rel)
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    if not os.path.exists(backup_path):
        shutil.copy2(deployed_path, backup_path)
    return backup_path

def fix_alpha(deployed_path, original_path):
    """Fix alpha channel: keep deployed RGB + upscale original alpha."""
    from srctools.vtf import VTF, ImageFormats
    from PIL import Image
    import numpy as np
    
    # Load original (has correct alpha)
    orig_vtf = VTF.read(open(original_path, 'rb'))
    orig_img = orig_vtf.get().to_PIL()
    if orig_img.mode != 'RGBA':
        return False, "original not RGBA"
    _, _, _, orig_alpha = orig_img.split()
    
    # Check original alpha is actually meaningful
    a_arr = np.array(orig_alpha)
    if a_arr.min() >= 254:
        return False, "original alpha also all-white (not fixable)"
    
    # Load deployed (has good RGB, broken alpha)
    dep_vtf = VTF.read(open(deployed_path, 'rb'))
    dep_img = dep_vtf.get().to_PIL()
    dep_r, dep_g, dep_b, _ = dep_img.split()
    
    # Upscale original alpha to match deployed size
    alpha_upscaled = orig_alpha.resize(dep_img.size, Image.LANCZOS)
    
    # Composite
    fixed_img = Image.merge('RGBA', (dep_r, dep_g, dep_b, alpha_upscaled))
    
    # Save as new VTF
    new_vtf = VTF(fixed_img.width, fixed_img.height, fmt=ImageFormats.DXT5)
    new_vtf.get().copy_from(fixed_img.tobytes(), ImageFormats.RGBA8888)
    new_vtf.flags = dep_vtf.flags
    
    with open(deployed_path, 'wb') as f:
        new_vtf.save(f)
    
    return True, f"alpha restored ({orig_img.size[0]}->{dep_img.size[0]})"

def restore_original(deployed_path, original_path):
    """Replace deployed file with original for normal/bump maps."""
    shutil.copy2(original_path, deployed_path)
    return True, "restored original"

# ---- Main execution ----
if __name__ == '__main__':
    scan_file = os.path.join(os.path.dirname(__file__), "scan_results.txt")
    
    print("Parsing scan results...")
    broken_alpha, upscaled_normals, upscaled_bumps = parse_scan_results(scan_file)
    print(f"  Broken alpha: {len(broken_alpha)}")
    print(f"  Upscaled normals: {len(upscaled_normals)}")
    print(f"  Upscaled bumps: {len(upscaled_bumps)}")
    
    # Deduplicate
    broken_alpha = list(dict.fromkeys(broken_alpha))
    upscaled_normals = list(dict.fromkeys(upscaled_normals))
    upscaled_bumps = list(dict.fromkeys(upscaled_bumps))
    print(f"  After dedup: alpha={len(broken_alpha)}, normals={len(upscaled_normals)}, bumps={len(upscaled_bumps)}")
    
    # Phase 1: Check coverage
    print("\n=== PHASE 1: Checking source coverage ===")
    alpha_found = 0
    alpha_missing = []
    for rel in broken_alpha:
        orig = find_original(rel)
        if orig:
            alpha_found += 1
        else:
            alpha_missing.append(rel)
    
    nm_found = 0
    nm_missing = []
    for rel in upscaled_normals + upscaled_bumps:
        orig = find_original(rel)
        if orig:
            nm_found += 1
        else:
            nm_missing.append(rel)
    
    print(f"  Alpha: {alpha_found}/{len(broken_alpha)} have originals")
    print(f"  Normal/Bump: {nm_found}/{len(upscaled_normals) + len(upscaled_bumps)} have originals")
    
    if alpha_missing:
        print(f"\n  Missing alpha originals ({len(alpha_missing)}):")
        for m in alpha_missing[:20]:
            print(f"    - {m}")
        if len(alpha_missing) > 20:
            print(f"    ... and {len(alpha_missing) - 20} more")
    
    if nm_missing:
        print(f"\n  Missing normal/bump originals ({len(nm_missing)}):")
        for m in nm_missing[:20]:
            print(f"    - {m}")
        if len(nm_missing) > 20:
            print(f"    ... and {len(nm_missing) - 20} more")
    
    if '--check-only' in sys.argv:
        print("\n[CHECK ONLY MODE - no changes made]")
        sys.exit(0)
    
    # Phase 2: Fix alpha channels
    print("\n=== PHASE 2: Fixing broken alpha channels ===")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    fixed = 0
    failed = 0
    skipped = 0
    
    for i, rel in enumerate(broken_alpha):
        deployed = os.path.join(CONTENT, rel)
        original = find_original(rel)
        
        if not original:
            skipped += 1
            continue
        
        if not os.path.isfile(deployed):
            skipped += 1
            continue
        
        try:
            backup_file(deployed)
            success, msg = fix_alpha(deployed, original)
            if success:
                fixed += 1
                if (fixed % 10 == 0) or fixed <= 5:
                    print(f"  [{fixed}/{alpha_found}] FIXED: {os.path.basename(rel)} - {msg}")
            else:
                failed += 1
                print(f"  [SKIP] {os.path.basename(rel)} - {msg}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {os.path.basename(rel)} - {e}")
    
    print(f"\n  Alpha fix complete: {fixed} fixed, {failed} failed, {skipped} skipped (no original)")
    
    # Phase 3: Restore normal/bump maps
    print("\n=== PHASE 3: Restoring normal/bump maps ===")
    nm_fixed = 0
    nm_failed = 0
    nm_skipped = 0
    
    all_norms = list(dict.fromkeys(upscaled_normals + upscaled_bumps))
    for i, rel in enumerate(all_norms):
        deployed = os.path.join(CONTENT, rel)
        original = find_original(rel)
        
        if not original:
            nm_skipped += 1
            continue
        
        if not os.path.isfile(deployed):
            nm_skipped += 1
            continue
        
        try:
            backup_file(deployed)
            success, msg = restore_original(deployed, original)
            if success:
                nm_fixed += 1
                if (nm_fixed % 10 == 0) or nm_fixed <= 5:
                    print(f"  [{nm_fixed}] RESTORED: {os.path.basename(rel)}")
        except Exception as e:
            nm_failed += 1
            print(f"  [ERROR] {os.path.basename(rel)} - {e}")
    
    print(f"\n  Normal/bump restore: {nm_fixed} restored, {nm_failed} failed, {nm_skipped} skipped")
    
    # Final summary
    print("\n" + "=" * 60)
    print("BATCH FIX COMPLETE")
    print("=" * 60)
    print(f"Alpha channels fixed: {fixed}/{len(broken_alpha)}")
    print(f"Normal maps restored: {nm_fixed}/{len(all_norms)}")
    print(f"Backups saved to: {BACKUP_DIR}")
    print(f"Total issues remaining: {len(alpha_missing) + len(nm_missing)} (no originals found)")
