"""Scan Content VTFs and count files needing downscale."""
import os, struct
from pathlib import Path

CONTENT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")

over2k = 0; over2k_size = 0
at2k = 0; under2k = 0; animated = 0
total = 0; total_size = 0

for root, dirs, fnames in os.walk(CONTENT):
    for f in fnames:
        if not f.endswith('.vtf') or f.endswith('.bak'):
            continue
        fp = Path(root) / f
        sz = fp.stat().st_size
        total += 1
        total_size += sz
        try:
            with open(fp, 'rb') as fh:
                hdr = fh.read(28)
            if hdr[:4] != b'VTF\x00':
                continue
            w = struct.unpack_from('<H', hdr, 16)[0]
            h = struct.unpack_from('<H', hdr, 18)[0]
            frames = struct.unpack_from('<H', hdr, 24)[0]
            if frames > 1:
                animated += 1
                continue
            mx = max(w, h)
            if mx > 2048:
                over2k += 1
                over2k_size += sz
            elif mx == 2048:
                at2k += 1
            else:
                under2k += 1
        except:
            pass

print(f'Total: {total} VTFs, {total_size / 1024**3:.1f} GB')
print(f'Over 2048px: {over2k} files, {over2k_size / 1024**3:.1f} GB (WILL DOWNSCALE)')
print(f'At 2048px: {at2k} files (keep as-is)')
print(f'Under 2048px: {under2k} files (keep as-is)')
print(f'Animated: {animated} files (skip)')
est = (total_size - over2k_size * 0.75) / 1024**3
print(f'Estimated size after: ~{est:.0f} GB')
