# extract_vpk.py — Extract missing particle/effect materials from Source Engine VPK archives
import struct
import os

GMOD = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod"
CONTENT = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content"

VPK_FILES = [
    os.path.join(GMOD, "garrysmod", "garrysmod_dir.vpk"),
    os.path.join(GMOD, "garrysmod", "fallbacks_dir.vpk"),
    os.path.join(GMOD, "sourceengine", "hl2_textures_dir.vpk"),
    os.path.join(GMOD, "platform", "platform_misc_dir.vpk"),
]

EFFECT_KEYWORDS = {"particle/", "particles/", "effects/", "pfx/", "sprites/", "cst/"}

# Build existing set
existing = set()
mat_root = os.path.join(CONTENT, "materials")
for root, dirs, files in os.walk(mat_root):
    for f in files:
        rel = os.path.relpath(os.path.join(root, f), mat_root).replace("\\", "/").lower()
        existing.add(rel)

print(f"Content materials has {len(existing)} files")


def read_cstring(f):
    """Read a null-terminated string."""
    result = b""
    while True:
        c = f.read(1)
        if not c or c == b"\x00":
            break
        result += c
    return result.decode("utf-8", errors="replace")


def parse_vpk(dir_vpk_path):
    """Parse a VPK directory file and return list of (filepath, archive_index, offset, length, preload_data)."""
    entries = []
    with open(dir_vpk_path, "rb") as f:
        sig = struct.unpack("<I", f.read(4))[0]
        if sig != 0x55AA1234:
            print(f"  Not a valid VPK: {dir_vpk_path}")
            return []

        version = struct.unpack("<I", f.read(4))[0]
        tree_size = struct.unpack("<I", f.read(4))[0]

        if version == 2:
            f.read(4 * 4)  # skip v2 header fields

        # Parse tree
        while True:
            ext = read_cstring(f)
            if ext == "":
                break
            while True:
                path = read_cstring(f)
                if path == "":
                    break
                while True:
                    filename = read_cstring(f)
                    if filename == "":
                        break

                    crc = struct.unpack("<I", f.read(4))[0]
                    preload_bytes = struct.unpack("<H", f.read(2))[0]
                    archive_index = struct.unpack("<H", f.read(2))[0]
                    entry_offset = struct.unpack("<I", f.read(4))[0]
                    entry_length = struct.unpack("<I", f.read(4))[0]
                    terminator = struct.unpack("<H", f.read(2))[0]

                    preload_data = b""
                    if preload_bytes > 0:
                        preload_data = f.read(preload_bytes)

                    if path == " ":
                        filepath = f"{filename}.{ext}"
                    else:
                        filepath = f"{path}/{filename}.{ext}"

                    entries.append((filepath, archive_index, entry_offset, entry_length, preload_data))

    return entries


def extract_from_vpk(dir_vpk_path, entries_to_extract):
    """Extract specific entries from a VPK."""
    base_dir = os.path.dirname(dir_vpk_path)
    base_name = os.path.basename(dir_vpk_path).replace("_dir.vpk", "")

    extracted = 0
    for filepath, archive_index, offset, length, preload_data in entries_to_extract:
        try:
            if archive_index == 0x7FFF:
                # Data is in the directory VPK itself (after tree)
                archive_path = dir_vpk_path
            else:
                archive_path = os.path.join(base_dir, f"{base_name}_{archive_index:03d}.vpk")

            data = preload_data
            if length > 0:
                with open(archive_path, "rb") as f:
                    f.seek(offset)
                    data += f.read(length)

            dst_path = os.path.join(CONTENT, "materials", filepath.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            with open(dst_path, "wb") as out:
                out.write(data)
            extracted += 1
        except Exception as e:
            print(f"    Error extracting {filepath}: {e}")

    return extracted


total_extracted = 0

for vpk_path in VPK_FILES:
    if not os.path.exists(vpk_path):
        print(f"Skipping (not found): {vpk_path}")
        continue

    vpk_name = os.path.basename(vpk_path)
    print(f"\nParsing {vpk_name}...")

    entries = parse_vpk(vpk_path)
    print(f"  Total entries: {len(entries)}")

    # Filter for missing effect materials
    to_extract = []
    for filepath, archive_index, offset, length, preload_data in entries:
        fp_lower = filepath.lower()
        # Only materials (vtf/vmt) in effect paths
        if not (fp_lower.endswith(".vtf") or fp_lower.endswith(".vmt")):
            continue
        if not any(kw in fp_lower for kw in EFFECT_KEYWORDS):
            continue
        if fp_lower not in existing:
            to_extract.append((filepath, archive_index, offset, length, preload_data))
            existing.add(fp_lower)

    if to_extract:
        print(f"  Found {len(to_extract)} missing effect materials to extract")
        count = extract_from_vpk(vpk_path, to_extract)
        total_extracted += count
        print(f"  ✓ Extracted {count} files")

        # Show some examples
        for fp, *_ in to_extract[:8]:
            print(f"    {fp}")
        if len(to_extract) > 8:
            print(f"    ... +{len(to_extract)-8} more")
    else:
        print(f"  No missing effect materials found")

print(f"\n=== TOTAL: {total_extracted} files extracted from VPKs ===")
