"""
fix_all_mipmaps.py — Patch mipmap count byte for ALL VTFs in Content.
srctools writes mipmap data but sets the header count to 0.
This patches byte 28 of every affected VTF.
"""
import os, struct, math, time
from pathlib import Path

CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")


def get_expected_mips(w, h):
    return int(math.log2(max(w, h))) + 1


def main():
    print("🔧 ALL-VTF Mipmap Header Patcher")
    print(f"   Target: {CONTENT_MAT}\n")

    t0 = time.time()
    total = 0
    patched = 0
    already_ok = 0
    failed = 0

    for root, dirs, files in os.walk(CONTENT_MAT):
        for f in files:
            if not f.endswith('.vtf') or f.endswith('.bak') or f.endswith('.tmp'):
                continue

            total += 1
            path = os.path.join(root, f)

            try:
                with open(path, 'r+b') as fh:
                    header = fh.read(30)
                    if len(header) < 30 or header[:4] != b'VTF\x00':
                        continue

                    w = struct.unpack_from('<H', header, 16)[0]
                    h = struct.unpack_from('<H', header, 18)[0]
                    cur_mips = struct.unpack_from('B', header, 28)[0]

                    if w == 0 or h == 0:
                        continue

                    expected = get_expected_mips(w, h)

                    if cur_mips >= 2:
                        already_ok += 1
                        continue

                    # Patch byte 28
                    fh.seek(28)
                    fh.write(struct.pack('B', expected))
                    patched += 1

            except Exception as e:
                failed += 1

            if total % 2000 == 0:
                print(f"   ... {total} scanned, {patched} patched")

    elapsed = time.time() - t0
    print(f"\n✅ Done in {elapsed:.1f}s")
    print(f"   Total VTFs: {total:,}")
    print(f"   Patched: {patched:,}")
    print(f"   Already OK: {already_ok:,}")
    print(f"   Failed: {failed}")


if __name__ == '__main__':
    main()
