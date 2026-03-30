import os, struct
import numpy as np

CONTENT = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials"
BASE = r"starwars\syphadias\props\sw_tor\bioware_ea\props\nar_shadda"

# Read VMTs
vmts = [
    "nar_redlight_02.vmt",
    "nar_redlight_04.vmt",
    "nar_signage_01.vmt",
    "nar_signage_02.vmt",
]

for vmt_name in vmts:
    for sub in ["", "buildings"]:
        vmt_path = os.path.join(CONTENT, BASE, sub, vmt_name)
        if os.path.isfile(vmt_path):
            prefix = sub + "/" if sub else ""
            print(f"\n=== {prefix}{vmt_name} ===")
            with open(vmt_path, 'r', errors='ignore') as f:
                print(f.read())
            break

# Now check VTFs for alpha
print("\n" + "=" * 60)
print("VTF Alpha Analysis")
print("=" * 60)

from srctools.vtf import VTF

# Find all VTFs in the nar_shadda folder
nar_dir = os.path.join(CONTENT, BASE)
vtfs = []
for root, _, files in os.walk(nar_dir):
    for f in files:
        if f.lower().endswith('.vtf'):
            vtfs.append(os.path.join(root, f))

for vtf_path in sorted(vtfs):
    try:
        with open(vtf_path, 'rb') as f:
            d = f.read(80)
            w = struct.unpack_from('<H', d, 16)[0]
            h = struct.unpack_from('<H', d, 18)[0]
            fmt = struct.unpack_from('<I', d, 52)[0]
        
        fmt_names = {0:'RGBA8888', 2:'RGB888', 3:'BGR888', 12:'BGRA8888', 13:'DXT1', 14:'DXT3', 15:'DXT5'}
        fmt_name = fmt_names.get(fmt, str(fmt))
        
        # Only check alpha for formats that support it
        has_alpha_fmt = fmt in (0, 12, 14, 15)  # RGBA, BGRA, DXT3, DXT5
        
        rel = os.path.relpath(vtf_path, os.path.join(CONTENT, BASE))
        
        if has_alpha_fmt:
            v = VTF.read(open(vtf_path, 'rb'))
            img = v.get().to_PIL()
            if img.mode == 'RGBA':
                _, _, _, a = img.split()
                arr = np.array(a)
                is_all_white = arr.min() >= 254
                status = "ALL-WHITE ALPHA" if is_all_white else "OK"
                print(f"  [{status}] {rel} ({w}x{h} {fmt_name}) alpha: min={arr.min()} max={arr.max()} mean={arr.mean():.1f}")
            else:
                print(f"  [NO ALPHA] {rel} ({w}x{h} {fmt_name}) mode={img.mode}")
        else:
            print(f"  [NO ALPHA FMT] {rel} ({w}x{h} {fmt_name})")
    except Exception as e:
        print(f"  [ERROR] {rel} - {e}")
