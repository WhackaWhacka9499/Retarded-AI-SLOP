"""Restore all particle/effect VTFs from .bak files (pre-upscale originals).
Only restores files where a .bak exists and that were NOT already restored from PPE.
"""
import os, shutil, struct
from pathlib import Path

CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")
EFFECT_DIRS = ["particle", "particles", "effects", "pfx", "sprites"]

restored = 0
already_ok = 0
no_backup = 0

for d_name in EFFECT_DIRS:
    d = CONTENT_MAT / d_name
    if not d.exists():
        continue
    for root, dirs, files in os.walk(d):
        for f in files:
            if not f.endswith('.vtf') or f.endswith('.bak') or f.endswith('.tmp'):
                continue
            
            vtf_path = Path(root) / f
            bak_path = Path(str(vtf_path) + '.bak')
            
            if not bak_path.exists():
                no_backup += 1
                continue
            
            # Check if current file is already small/DXT (restored from PPE)
            vtf_size = os.path.getsize(vtf_path)
            bak_size = os.path.getsize(bak_path)
            
            # If current VTF is same size or smaller than backup, it was already restored
            if vtf_size <= bak_size:
                already_ok += 1
                continue
            
            # Restore from backup
            try:
                shutil.copy2(str(bak_path), str(vtf_path))
                restored += 1
                if restored <= 10:
                    rel = vtf_path.relative_to(CONTENT_MAT)
                    print(f"  ✅ {rel}: {vtf_size//1024}KB → {bak_size//1024}KB")
            except Exception as e:
                print(f"  ❌ {vtf_path.relative_to(CONTENT_MAT)}: {e}")

print(f"\nDone!")
print(f"  Restored from .bak: {restored}")
print(f"  Already OK (PPE or smaller): {already_ok}")
print(f"  No backup available: {no_backup}")
