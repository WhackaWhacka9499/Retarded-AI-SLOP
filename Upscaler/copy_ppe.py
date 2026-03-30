"""Copy all PPE original VTFs and VMTs to Content, replacing upscaled versions."""
import os, shutil
from pathlib import Path

PPE = Path(r"C:\Users\Alexander Jarvis\AppData\Local\Temp\gmpublisher\placeable_particle_effects_110\materials")
CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")

copied_vtf = 0
copied_vmt = 0
errors = 0

for root, dirs, files in os.walk(PPE):
    for f in files:
        if f.endswith('.vtf') or f.endswith('.vmt'):
            src = Path(root) / f
            rel = src.relative_to(PPE)
            dst = CONTENT_MAT / rel
            
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                if f.endswith('.vtf'):
                    copied_vtf += 1
                else:
                    copied_vmt += 1
            except Exception as e:
                print(f"  ERROR: {rel}: {e}")
                errors += 1

print(f"Copied {copied_vtf} VTFs, {copied_vmt} VMTs, {errors} errors")

# Verify sizes
print("\nVerification sample:")
samples = [
    "pfx/fire_basic_nocolor.vtf",
    "particle/fireball.vtf",
    "effects/beam001.vtf",
    "sprites/fire.vtf",
]
for s in samples:
    ppe_f = PPE / s
    content_f = CONTENT_MAT / s
    if ppe_f.exists() and content_f.exists():
        ps = os.path.getsize(ppe_f) // 1024
        cs = os.path.getsize(content_f) // 1024
        match = "✅" if ps == cs else "❌"
        print(f"  {s}: PPE={ps}KB Content={cs}KB {match}")
    elif ppe_f.exists():
        print(f"  {s}: PPE exists but missing from Content!")
    else:
        print(f"  {s}: not in PPE")
