"""
fix_mipmaps.py — Binary-patch VTF mipmap count header byte.

srctools writes mipmap image data but sets mipmap_count=0 in the header.
Source Engine reads the header byte to know how many mipmaps exist.
This script patches byte 28 of each VTF file to the correct mipmap count.

Additionally regenerates VTFs that were created by fix_pfx.py (which also
used srctools and thus have 0 mipmaps with NO mipmap data) — for these files
we rebuild the full VTF with mipmap data included.
"""
import os, sys, struct, math, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")
EFFECT_DIRS = [
    CONTENT_MAT / "particle",
    CONTENT_MAT / "particles",
    CONTENT_MAT / "effects",
    CONTENT_MAT / "pfx",
    CONTENT_MAT / "sprites",
]
MAX_WORKERS = 8

sys.path.insert(0, str(Path(r"C:\Users\Alexander Jarvis\Desktop\Upscaler")))
from srctools.vtf import VTF, ImageFormats
from PIL import Image


def get_mipmap_count(width, height):
    """Expected mipmap count for dimensions (including base level)."""
    return int(math.log2(max(width, height))) + 1


def get_vtf_info(path):
    """Read VTF header, return (width, height, mipmaps, filesize) or None."""
    try:
        with open(path, 'rb') as f:
            header = f.read(30)
            if len(header) < 30 or header[:4] != b'VTF\x00':
                return None
            width = struct.unpack_from('<H', header, 16)[0]
            height = struct.unpack_from('<H', header, 18)[0]
            mipmaps = struct.unpack_from('B', header, 28)[0]
            return (width, height, mipmaps, os.path.getsize(path))
    except:
        return None


def rebuild_vtf_with_mipmaps(path):
    """Full rebuild: read VTF, generate mipmaps, save, then patch header."""
    try:
        with open(path, 'rb') as f:
            vtf = VTF.read(f)
            frame = vtf.get(frame=0, mipmap=0)
            w, h = vtf.width, vtf.height
            data = bytes(frame)

        img = Image.frombytes('RGBA', (w, h), data)
        orig_flags = vtf.flags
        orig_version = vtf.version

        # Detect alpha
        alpha = list(img.split()[3].getdata())
        has_alpha = any(a != 255 for a in alpha[:2000])
        out_fmt = ImageFormats.DXT5 if has_alpha else ImageFormats.DXT1

        expected_mips = get_mipmap_count(w, h)

        # Create new VTF
        new_vtf = VTF(w, h, fmt=out_fmt, version=orig_version)
        new_vtf.get(frame=0, mipmap=0).copy_from(img.tobytes())

        # Generate mipmaps
        for mip in range(1, expected_mips):
            mw = max(w >> mip, 1)
            mh = max(h >> mip, 1)
            try:
                mip_img = img.resize((mw, mh), Image.LANCZOS)
                new_vtf.get(frame=0, mipmap=mip).copy_from(mip_img.tobytes())
            except:
                break

        new_vtf.flags = orig_flags
        if not hasattr(new_vtf, 'hotspot_info'):
            new_vtf.hotspot_info = None
        if not hasattr(new_vtf, 'hotspot_flags'):
            new_vtf.hotspot_flags = 0

        # Save
        tmp_path = str(path) + '.miptmp'
        with open(tmp_path, 'wb') as f:
            new_vtf.save(f)

        # Patch header byte 28 with correct mipmap count
        with open(tmp_path, 'r+b') as f:
            f.seek(28)
            f.write(struct.pack('B', expected_mips))

        # Verify
        info = get_vtf_info(tmp_path)
        if info and info[2] >= 2:
            os.replace(tmp_path, str(path))
            return True, f"rebuilt {w}x{h}, {info[2]} mips"
        else:
            os.remove(tmp_path)
            return False, "header patch failed"

    except Exception as e:
        tmp_path = str(path) + '.miptmp'
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False, str(e)


def simple_patch(path, expected_mips):
    """Simple binary patch: just change byte 28 to the expected mipmap count.
    This works if the VTF was created by the upscaler GUI which DOES write mipmap data
    but srctools set the count to 0."""
    try:
        with open(path, 'r+b') as f:
            f.seek(28)
            f.write(struct.pack('B', expected_mips))
        return True, f"patched to {expected_mips} mips"
    except Exception as e:
        return False, str(e)


def fix_vtf(path):
    """Fix a VTF with broken mipmaps."""
    info = get_vtf_info(path)
    if info is None:
        return path, False, "unreadable"

    w, h, cur_mips, filesize = info
    if w == 0 or h == 0:
        return path, False, "zero dimensions"

    expected_mips = get_mipmap_count(w, h)

    if cur_mips >= 2:
        return path, None, "already OK"

    # Check if file was created by fix_pfx.py (has .bak backup)
    bak = Path(str(path) + '.bak')

    # Heuristic: if the file is "large enough" it likely has mipmap data
    # already written by srctools (just header is wrong).
    # DXT5 at WxH = W*H bytes, DXT1 = W*H/2. Full mipmap chain adds ~33%.
    # If filesize >= base_size * 1.2, likely has mipmap data.
    base_dxt5 = w * h  # DXT5 bytes for base level
    base_dxt1 = w * h // 2
    min_with_mips = min(base_dxt5, base_dxt1) * 1.2

    if filesize >= min_with_mips:
        # Likely has mipmap data, just patch the header
        ok, msg = simple_patch(path, expected_mips)
        return path, ok, f"quick-{msg}"
    else:
        # Need full rebuild
        ok, msg = rebuild_vtf_with_mipmaps(path)
        return path, ok, f"full-{msg}"


def main():
    print("🔧 Particle VTF Mipmap Fixer (Header Patcher)")
    print(f"   Scanning: {CONTENT_MAT}")
    print()

    # Collect all particle VTFs
    all_vtfs = []
    for d in EFFECT_DIRS:
        if not d.exists():
            continue
        for root, dirs, files in os.walk(d):
            for f in files:
                if f.endswith('.vtf') and not f.endswith('.bak') and not f.endswith('.tmp'):
                    all_vtfs.append(Path(root) / f)

    print(f"   Found {len(all_vtfs)} particle VTFs")

    # Check which need fixing
    broken = []
    ok_count = 0
    for p in all_vtfs:
        info = get_vtf_info(p)
        if info and info[2] < 2:
            broken.append(p)
        else:
            ok_count += 1

    print(f"   Already OK: {ok_count}")
    print(f"   Need fixing: {len(broken)}")

    if not broken:
        print("\n✅ All particle VTFs have proper mipmaps!")
        return

    print(f"\n🔧 Fixing {len(broken)} VTFs...")
    t0 = time.time()

    success = 0
    failed = 0
    quick = 0
    full = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fix_vtf, p): p for p in broken}
        for fut in as_completed(futures):
            path, ok, msg = fut.result()
            rel = path.relative_to(CONTENT_MAT)
            if ok is None:
                ok_count += 1
            elif ok:
                success += 1
                if msg.startswith("quick"):
                    quick += 1
                else:
                    full += 1
                # Only print every 50th or failures
                if success % 50 == 1 or success <= 5:
                    print(f"  ✅ {rel}: {msg}")
            else:
                failed += 1
                print(f"  ✗ {rel}: {msg}")

    elapsed = time.time() - t0
    print(f"\n✅ Done in {elapsed:.1f}s")
    print(f"   Fixed: {success} ({quick} quick-patched, {full} rebuilt)")
    print(f"   Failed: {failed}")

    # Verify a few
    print(f"\n🔍 Verification sample:")
    for p in broken[:5]:
        info = get_vtf_info(p)
        if info:
            print(f"  {p.relative_to(CONTENT_MAT)}: {info[0]}x{info[1]}, {info[2]} mipmaps")


if __name__ == '__main__':
    main()
