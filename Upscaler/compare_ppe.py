"""Compare PPE addon originals vs Content folder versions."""
import os, struct
from pathlib import Path
from collections import Counter

PPE = Path(r"C:\Users\ALEXAN~1\AppData\Local\Temp\gmpublisher\placeable_particle_effects_110\materials")
CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")

DXT = {13:'DXT1',14:'DXT3',15:'DXT5',0:'RGBA8888',2:'RGB888',12:'BGRA8888',11:'ARGB8888',
       3:'BGR888',4:'RGB565',5:'I8',6:'IA88',7:'P8',8:'A8',16:'BGRX8888',
       17:'BGR565',20:'DXT1_ONEBITALPHA',22:'UV88',24:'RGBA16161616F'}

def vtf_info(p):
    try:
        with open(p,'rb') as f:
            h = f.read(40)
            if len(h)<40 or h[:4]!=b'VTF\x00': return None
            w=struct.unpack_from('<H',h,16)[0]; ht=struct.unpack_from('<H',h,18)[0]
            fmt=struct.unpack_from('<I',h,36)[0]; mips=struct.unpack_from('B',h,28)[0]
            frames=struct.unpack_from('<H',h,24)[0]; flags=struct.unpack_from('<I',h,20)[0]
            sz=os.path.getsize(p)
            return {'w':w,'h':ht,'fmt':DXT.get(fmt,f'?{fmt}'),'fmt_id':fmt,
                    'mips':mips,'frames':frames,'flags':flags,'size':sz}
    except:
        return None


# Scan PPE
ppe_files = {}
for root,dirs,files in os.walk(PPE):
    for f in files:
        if f.endswith('.vtf'):
            rel = os.path.relpath(os.path.join(root,f), PPE).replace("\\","/")
            ppe_files[rel.lower()] = Path(root)/f

print(f"=== PPE ADDON: {len(ppe_files)} VTFs ===\n")

# Compare with Content
fmt_changes = Counter()
frame_changes = []
missing_in_content = []
issues = []

for rel, ppe_path in sorted(ppe_files.items()):
    content_path = CONTENT_MAT / rel.replace("/", os.sep)
    pi = vtf_info(ppe_path)
    if not pi:
        continue

    if not content_path.exists():
        missing_in_content.append(rel)
        continue

    ci = vtf_info(content_path)
    if not ci:
        continue

    changed = []
    if pi['fmt_id'] != ci['fmt_id']:
        changed.append(f"FMT:{pi['fmt']}->{ci['fmt']}")
        fmt_changes[f"{pi['fmt']}->{ci['fmt']}"] += 1
    if pi['frames'] != ci['frames']:
        changed.append(f"FRAMES:{pi['frames']}->{ci['frames']}")
        frame_changes.append((rel, pi['frames'], ci['frames']))
    if pi['flags'] != ci['flags']:
        changed.append(f"FLAGS:{pi['flags']:#x}->{ci['flags']:#x}")

    if changed:
        issues.append((rel, changed, pi, ci))


# Report
print("=== FORMAT CHANGES ===")
for key, count in fmt_changes.most_common():
    print(f"  {key}: {count} files")

print(f"\n=== FRAME/ANIMATION CHANGES ===")
for rel, orig, new in frame_changes:
    print(f"  {rel}: {orig} -> {new} frames")

print(f"\n=== SAMPLE COMPARISONS (first 20 with changes) ===")
for rel, changed, pi, ci in issues[:20]:
    print(f"  {rel}")
    print(f"    PPE:     {pi['w']}x{pi['h']} {pi['fmt']} flags={pi['flags']:#x} mips={pi['mips']} frames={pi['frames']} {pi['size']//1024}KB")
    print(f"    Content: {ci['w']}x{ci['h']} {ci['fmt']} flags={ci['flags']:#x} mips={ci['mips']} frames={ci['frames']} {ci['size']//1024}KB")

print(f"\n=== SUMMARY ===")
print(f"  PPE VTFs total: {len(ppe_files)}")
print(f"  Present in Content: {len(ppe_files) - len(missing_in_content)}")
print(f"  Missing from Content: {len(missing_in_content)}")
print(f"  With format/frame/flag changes: {len(issues)}")

if missing_in_content:
    print(f"\n=== MISSING FROM CONTENT ({len(missing_in_content)}) ===")
    for m in missing_in_content[:20]:
        print(f"  • {m}")
    if len(missing_in_content) > 20:
        print(f"  ... +{len(missing_in_content)-20} more")
