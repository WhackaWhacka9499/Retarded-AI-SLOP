# extract_gma.py — Extract missing particle/effect materials from ALL workshop GMAs into Content
import struct
import os
import glob
import shutil

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

effect_keywords = {"particle/", "particles/", "effects/", "pfx/", "sprites/", "cst/"}

total_extracted = 0
total_skipped = 0
errors = 0
scanned = 0

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
                while f.read(1) != b"\x00":
                    pass
                name_bytes = b""
                while True:
                    c = f.read(1)
                    if c == b"\x00":
                        break
                    name_bytes += c
                addon_name = name_bytes.decode("utf-8", errors="replace")
                while f.read(1) != b"\x00":
                    pass
                while f.read(1) != b"\x00":
                    pass
                f.read(4)

                # Read file entry table
                file_entries = []
                while True:
                    num_data = f.read(4)
                    if len(num_data) < 4:
                        break
                    num = struct.unpack("<I", num_data)[0]
                    if num == 0:
                        break
                    fn_bytes = b""
                    while True:
                        c = f.read(1)
                        if c == b"\x00":
                            break
                        fn_bytes += c
                    fn = fn_bytes.decode("utf-8", errors="replace")
                    size = struct.unpack("<q", f.read(8))[0]
                    crc = struct.unpack("<I", f.read(4))[0]
                    file_entries.append((fn, size))

                # Now positioned at start of file data
                data_start = f.tell()
                offset = 0

                extracted_from_this = 0
                for fn, size in file_entries:
                    fn_lower = fn.lower()
                    if fn_lower.startswith("materials/"):
                        is_effect = any(kw in fn_lower for kw in effect_keywords)
                        is_mat = fn_lower.endswith(".vtf") or fn_lower.endswith(".vmt")
                        if is_effect and is_mat and fn_lower not in existing:
                            # Extract this file
                            f.seek(data_start + offset)
                            data = f.read(size)

                            dst_path = os.path.join(CONTENT, fn.replace("/", os.sep))
                            dst_dir = os.path.dirname(dst_path)
                            os.makedirs(dst_dir, exist_ok=True)
                            with open(dst_path, "wb") as out:
                                out.write(data)

                            existing.add(fn_lower)
                            extracted_from_this += 1
                            total_extracted += 1
                        else:
                            total_skipped += 1

                    offset += size

                if extracted_from_this > 0:
                    print(f"  ✓ {wid} ({addon_name}): extracted {extracted_from_this} files")

        except Exception as e:
            errors += 1

print(f"\nScanned {scanned} GMA files, {errors} errors")
print(f"Total extracted: {total_extracted}")
print(f"Total skipped (already existed): {total_skipped}")
