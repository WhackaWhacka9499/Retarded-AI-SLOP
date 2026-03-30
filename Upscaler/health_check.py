# health_check.py — Comprehensive scan of Content/materials for broken files
import os, sys, struct
from pathlib import Path
from collections import defaultdict

CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")

stats = defaultdict(int)
issues = defaultdict(list)

def check_vtf(path: Path):
    """Validate a VTF file for basic integrity."""
    size = path.stat().st_size

    if size == 0:
        issues['zero_byte'].append(str(path.relative_to(CONTENT_MAT)))
        return

    if size < 64:
        issues['tiny_file'].append(f"{path.relative_to(CONTENT_MAT)} ({size}B)")
        return

    try:
        with open(path, 'rb') as f:
            header = f.read(30)
            if len(header) < 30:
                issues['truncated'].append(f"{path.relative_to(CONTENT_MAT)} ({size}B)")
                return

            sig = header[0:4]
            if sig != b'VTF\x00':
                issues['bad_magic'].append(f"{path.relative_to(CONTENT_MAT)} (got {sig!r})")
                return

            ver_major = struct.unpack_from('<I', header, 4)[0]
            ver_minor = struct.unpack_from('<I', header, 8)[0]
            header_size = struct.unpack_from('<I', header, 12)[0]
            width = struct.unpack_from('<H', header, 16)[0]
            height = struct.unpack_from('<H', header, 18)[0]
            flags = struct.unpack_from('<I', header, 20)[0]
            frames = struct.unpack_from('<H', header, 24)[0]

            if width == 0 or height == 0:
                issues['zero_dimensions'].append(f"{path.relative_to(CONTENT_MAT)} ({width}x{height} v{ver_major}.{ver_minor})")
                return

            if width > 4096 or height > 4096:
                issues['oversized'].append(f"{path.relative_to(CONTENT_MAT)} ({width}x{height})")

            # Check backup comparison
            bak = Path(str(path) + '.bak')
            if bak.exists():
                bak_size = bak.stat().st_size
                if size < bak_size * 0.1 and bak_size > 1024:
                    issues['shrunk_suspiciously'].append(
                        f"{path.relative_to(CONTENT_MAT)} (bak={bak_size/1024:.0f}KB → now={size/1024:.0f}KB)")
                stats['has_backup'] += 1

            stats['vtf_ok'] += 1

    except Exception as e:
        issues['read_error'].append(f"{path.relative_to(CONTENT_MAT)}: {e}")


def check_vmt(path: Path):
    """Validate a VMT file for basic integrity."""
    size = path.stat().st_size
    if size == 0:
        issues['zero_byte_vmt'].append(str(path.relative_to(CONTENT_MAT)))
        return
    stats['vmt_ok'] += 1


print(f"🔍 Content Folder Health Check")
print(f"   Scanning: {CONTENT_MAT}\n")

total = 0
vtf_count = 0
vmt_count = 0

for root, dirs, files in os.walk(CONTENT_MAT):
    for f in files:
        path = Path(root) / f
        total += 1
        ext = f.lower().rsplit('.', 1)[-1] if '.' in f else ''

        if ext == 'vtf' and not f.endswith('.bak'):
            vtf_count += 1
            check_vtf(path)
        elif ext == 'vmt':
            vmt_count += 1
            check_vmt(path)
        elif ext == 'bak':
            stats['backups'] += 1
        else:
            stats['other'] += 1

print(f"📊 Summary")
print(f"   Total files: {total:,}")
print(f"   VTF files: {vtf_count:,} ({stats.get('vtf_ok', 0):,} valid)")
print(f"   VMT files: {vmt_count:,} ({stats.get('vmt_ok', 0):,} valid)")
print(f"   BAK files: {stats.get('backups', 0):,}")
print(f"   Other: {stats.get('other', 0):,}")
print(f"   VTFs with backups: {stats.get('has_backup', 0):,}")

print(f"\n{'='*60}")
has_issues = any(issues.values())
if not has_issues:
    print("✅ NO ISSUES FOUND — Content folder is healthy!")
else:
    total_issues = sum(len(v) for v in issues.values())
    print(f"⚠ Found {total_issues} issue(s):\n")

    for key in ['zero_byte', 'bad_magic', 'zero_dimensions', 'truncated', 'read_error',
                'tiny_file', 'shrunk_suspiciously', 'oversized',
                'zero_byte_vmt']:
        items = issues.get(key, [])
        if items:
            labels = {
                'zero_byte': '🔴 Zero-byte VTF files',
                'bad_magic': '🔴 Invalid VTF header',
                'zero_dimensions': '🔴 VTF with 0x0 dimensions',
                'truncated': '🔴 Truncated VTF files',
                'read_error': '🔴 VTF read errors',
                'tiny_file': '🟡 Suspiciously tiny VTFs (<64B)',
                'shrunk_suspiciously': '🟡 Upscaled file much smaller than backup',
                'oversized': '🟡 Oversized VTF (>4096 on a side)',
                'zero_byte_vmt': '🔴 Zero-byte VMT files',
            }
            print(f"  {labels.get(key, key)}: {len(items)}")
            for item in items[:20]:
                print(f"    • {item}")
            if len(items) > 20:
                print(f"    ... +{len(items)-20} more")
            print()
