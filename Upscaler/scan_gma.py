# scan_gma.py — Scan all workshop GMA files for particle/effect materials missing from Content
import struct
import os
import glob

WORKSHOP = r"G:\Program Files (x86)\Steam\steamapps\workshop\content\4000"
CONTENT = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content"

# Build set of existing Content file paths (lowercase, forward slashes)
existing = set()
mat_root = os.path.join(CONTENT, "materials")
for root, dirs, files in os.walk(mat_root):
    for f in files:
        rel = os.path.relpath(os.path.join(root, f), CONTENT).replace("\\", "/").lower()
        existing.add(rel)

print(f"Content has {len(existing)} files")

# Keywords for particle/effect paths
effect_keywords = {"particle/", "particles/", "effects/", "pfx/", "sprites/", "cst/"}

missing_by_gma = {}
scanned = 0
errors = 0

for wid in os.listdir(WORKSHOP):
    wdir = os.path.join(WORKSHOP, wid)
    if not os.path.isdir(wdir):
        continue
    for gma_file in glob.glob(os.path.join(wdir, "*.gma")):
        scanned += 1
        try:
            with open(gma_file, "rb") as f:
                magic = f.read(4)
                if magic != b"GMAD":
                    continue
                ver = struct.unpack("b", f.read(1))[0]
                f.read(8)  # steamid
                f.read(8)  # timestamp
                # read null-terminated strings
                while f.read(1) != b"\x00":
                    pass  # required content
                # addon name
                name_bytes = b""
                while True:
                    c = f.read(1)
                    if c == b"\x00":
                        break
                    name_bytes += c
                addon_name = name_bytes.decode("utf-8", errors="replace")
                # skip desc, author
                while f.read(1) != b"\x00":
                    pass
                while f.read(1) != b"\x00":
                    pass
                f.read(4)  # version

                # Read file entries
                missing = []
                while True:
                    num_data = f.read(4)
                    if len(num_data) < 4:
                        break
                    num = struct.unpack("<I", num_data)[0]
                    if num == 0:
                        break
                    # filename (null-terminated)
                    fn_bytes = b""
                    while True:
                        c = f.read(1)
                        if c == b"\x00":
                            break
                        fn_bytes += c
                    fn = fn_bytes.decode("utf-8", errors="replace")
                    f.read(8)  # size
                    f.read(4)  # crc

                    fn_lower = fn.lower()
                    # Check if this is a particle/effect material
                    if fn_lower.startswith("materials/"):
                        is_effect = any(kw in fn_lower for kw in effect_keywords)
                        is_mat = fn_lower.endswith(".vtf") or fn_lower.endswith(".vmt")
                        if is_effect and is_mat and fn_lower not in existing:
                            missing.append(fn)

                if missing:
                    missing_by_gma[f"{wid} ({addon_name})"] = missing
        except Exception as e:
            errors += 1

print(f"Scanned {scanned} GMA files, {errors} errors")
total_missing = sum(len(v) for v in missing_by_gma.values())
print(f"Found {len(missing_by_gma)} addons with missing effect materials")
print(f"Total missing files: {total_missing}")
print()

for gma, files in sorted(missing_by_gma.items(), key=lambda x: -len(x[1])):
    print(f"  {gma}: {len(files)} missing")
    for f in files[:10]:
        print(f"    {f}")
    if len(files) > 10:
        print(f"    ... +{len(files)-10} more")
