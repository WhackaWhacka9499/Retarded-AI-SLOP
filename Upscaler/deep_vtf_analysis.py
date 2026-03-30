"""
Deep VTF/VMT analysis — Compare originals vs upscaled versions.
Check DXT format, alpha, and VMT blend settings.
"""
import os, sys, struct
from pathlib import Path
from collections import defaultdict

CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")

DXT_NAMES = {
    0: "RGBA8888", 1: "ABGR8888", 2: "RGB888", 3: "BGR888",
    4: "RGB565", 5: "I8", 6: "IA88", 7: "P8", 8: "A8",
    9: "RGB888_BLUESCREEN", 10: "BGR888_BLUESCREEN",
    11: "ARGB8888", 12: "BGRA8888", 13: "DXT1", 14: "DXT3", 15: "DXT5",
    16: "BGRX8888", 17: "BGR565", 18: "BGRX5551", 19: "BGRA4444",
    20: "DXT1_ONEBITALPHA", 21: "BGRA5551", 22: "UV88", 23: "UVWQ8888",
    24: "RGBA16161616F", 25: "RGBA16161616", 26: "UVLX8888",
}

PARTICLE_DIRS = ["particle", "particles", "effects", "pfx", "sprites"]

def read_vtf_header(path):
    """Read VTF header and return info dict."""
    try:
        with open(path, 'rb') as f:
            header = f.read(40)
            if len(header) < 30 or header[:4] != b'VTF\x00':
                return None
            return {
                'ver_major': struct.unpack_from('<I', header, 4)[0],
                'ver_minor': struct.unpack_from('<I', header, 8)[0],
                'header_size': struct.unpack_from('<I', header, 12)[0],
                'width': struct.unpack_from('<H', header, 16)[0],
                'height': struct.unpack_from('<H', header, 18)[0],
                'flags': struct.unpack_from('<I', header, 20)[0],
                'frames': struct.unpack_from('<H', header, 24)[0],
                'mipmaps': struct.unpack_from('B', header, 28)[0],
                'format': struct.unpack_from('<I', header, 36)[0] if len(header) >= 40 else -1,
                'size': os.path.getsize(path),
            }
    except:
        return None


def check_vmt(vmt_path):
    """Check VMT for blend mode settings."""
    try:
        with open(vmt_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read().lower()
        
        info = {
            'shader': 'unknown',
            'translucent': '$translucent' in content,
            'additive': '$additive' in content,
            'alphatest': '$alphatest' in content,
            'spritecard': 'spritecard' in content,
            'unlitgeneric': 'unlitgeneric' in content,
        }
        
        # Extract shader name (first non-comment word)
        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('//') and not line.startswith('{'):
                shader = line.strip('"').strip("'").strip()
                if shader:
                    info['shader'] = shader
                    break
        
        return info
    except:
        return None


print("=" * 70)
print("🔬 VTF/VMT DEEP ANALYSIS")
print("=" * 70)

# === SECTION 1: Format comparison — originals vs upscaled ===
print("\n📋 SECTION 1: DXT FORMAT — ORIGINAL (.bak) vs UPSCALED")
print("-" * 50)

format_changes = defaultdict(int)
format_mismatches = []

for d_name in PARTICLE_DIRS:
    d = CONTENT_MAT / d_name
    if not d.exists():
        continue
    for root, dirs, files in os.walk(d):
        for f in files:
            if not f.endswith('.vtf') or f.endswith('.bak'):
                continue
            vtf_path = Path(root) / f
            bak_path = Path(str(vtf_path) + '.bak')
            if not bak_path.exists():
                continue

            vtf_info = read_vtf_header(vtf_path)
            bak_info = read_vtf_header(bak_path)
            if not vtf_info or not bak_info:
                continue

            orig_fmt = DXT_NAMES.get(bak_info['format'], f"?{bak_info['format']}")
            new_fmt = DXT_NAMES.get(vtf_info['format'], f"?{vtf_info['format']}")

            key = f"{orig_fmt} → {new_fmt}"
            format_changes[key] += 1

            # Flag mismatches where alpha might be lost
            if bak_info['format'] in (15, 14, 0, 1, 11, 12) and vtf_info['format'] == 13:
                # Had alpha (DXT5/3/RGBA) → lost alpha (DXT1)
                rel = vtf_path.relative_to(CONTENT_MAT)
                format_mismatches.append(f"{rel}: {orig_fmt} → {new_fmt}")

print("Format transitions:")
for key, count in sorted(format_changes.items(), key=lambda x: -x[1]):
    marker = " ⚠ ALPHA LOST" if "DXT5" in key.split("→")[0] and "DXT1" in key.split("→")[1] else ""
    print(f"  {key}: {count}{marker}")

if format_mismatches:
    print(f"\n🔴 {len(format_mismatches)} files LOST alpha channel (DXT5→DXT1):")
    for m in format_mismatches[:15]:
        print(f"  • {m}")
    if len(format_mismatches) > 15:
        print(f"  ... +{len(format_mismatches)-15} more")


# === SECTION 2: VMT blend mode analysis ===
print(f"\n\n📋 SECTION 2: VMT BLEND MODE ANALYSIS")
print("-" * 50)

blend_stats = defaultdict(int)
missing_blend = []

for d_name in PARTICLE_DIRS:
    d = CONTENT_MAT / d_name
    if not d.exists():
        continue
    for root, dirs, files in os.walk(d):
        for f in files:
            if not f.endswith('.vmt'):
                continue
            vmt_path = Path(root) / f
            info = check_vmt(vmt_path)
            if not info:
                continue

            blend_stats[info['shader']] += 1

            has_blend = info['translucent'] or info['additive'] or info['alphatest']
            if not has_blend and info['shader'] not in ('patch', 'subrect'):
                # Check if corresponding VTF has alpha
                vtf_name = f.replace('.vmt', '.vtf')
                vtf_path_check = Path(root) / vtf_name
                if vtf_path_check.exists():
                    vtf_info = read_vtf_header(vtf_path_check)
                    if vtf_info and vtf_info['format'] in (15, 14):  # DXT5/3
                        rel = vmt_path.relative_to(CONTENT_MAT)
                        missing_blend.append(f"{rel} (shader={info['shader']})")

print("VMT Shaders used in particle/effect materials:")
for shader, count in sorted(blend_stats.items(), key=lambda x: -x[1]):
    print(f"  {shader}: {count}")

if missing_blend:
    print(f"\n🟡 {len(missing_blend)} VMTs with DXT5 texture but no blend mode set:")
    for m in missing_blend[:10]:
        print(f"  • {m}")


# === SECTION 3: VTF Flags analysis ===
print(f"\n\n📋 SECTION 3: VTF FLAGS — ORIGINAL vs UPSCALED")
print("-" * 50)

flag_diffs = []
for d_name in PARTICLE_DIRS:
    d = CONTENT_MAT / d_name
    if not d.exists():
        continue
    for root, dirs, files in os.walk(d):
        for f in files:
            if not f.endswith('.vtf') or f.endswith('.bak'):
                continue
            vtf_path = Path(root) / f
            bak_path = Path(str(vtf_path) + '.bak')
            if not bak_path.exists():
                continue
            vtf_info = read_vtf_header(vtf_path)
            bak_info = read_vtf_header(bak_path)
            if not vtf_info or not bak_info:
                continue

            if vtf_info['flags'] != bak_info['flags']:
                rel = vtf_path.relative_to(CONTENT_MAT)
                flag_diffs.append(f"{rel}: {bak_info['flags']:#010x} → {vtf_info['flags']:#010x}")

if flag_diffs:
    print(f"🟡 {len(flag_diffs)} files with changed VTF flags:")
    for d in flag_diffs[:15]:
        print(f"  • {d}")
    if len(flag_diffs) > 15:
        print(f"  ... +{len(flag_diffs)-15} more")
else:
    print("✅ All VTF flags match originals")


# === SECTION 4: Frame count check (animated sprites) ===
print(f"\n\n📋 SECTION 4: ANIMATED SPRITE SHEETS")
print("-" * 50)

animated = []
frame_lost = []
for d_name in PARTICLE_DIRS:
    d = CONTENT_MAT / d_name
    if not d.exists():
        continue
    for root, dirs, files in os.walk(d):
        for f in files:
            if not f.endswith('.vtf') or f.endswith('.bak'):
                continue
            vtf_path = Path(root) / f
            bak_path = Path(str(vtf_path) + '.bak')
            
            vtf_info = read_vtf_header(vtf_path)
            if not vtf_info:
                continue
            
            if bak_path.exists():
                bak_info = read_vtf_header(bak_path)
                if bak_info and bak_info['frames'] > 1:
                    rel = vtf_path.relative_to(CONTENT_MAT)
                    if vtf_info['frames'] != bak_info['frames']:
                        frame_lost.append(f"{rel}: {bak_info['frames']}→{vtf_info['frames']} frames")
                    else:
                        animated.append(f"{rel}: {bak_info['frames']} frames")

if animated:
    print(f"ℹ {len(animated)} multi-frame (animated) VTFs:")
    for a in animated[:10]:
        print(f"  • {a}")
if frame_lost:
    print(f"\n🔴 {len(frame_lost)} VTFs LOST animation frames:")
    for fl in frame_lost[:15]:
        print(f"  • {fl}")
if not animated and not frame_lost:
    print("✅ No animated sprite sheets found (all single-frame)")


# === SECTION 5: Key fire texture details ===
print(f"\n\n📋 SECTION 5: KEY FIRE/EXPLOSION TEXTURES")
print("-" * 50)

fire_names = ["fire_burning_character", "fire_particle_3", "fireball", "flamethrower", 
              "fire_basic", "fire1", "tinyfiresprites", "rainbow_fire"]
for d_name in PARTICLE_DIRS:
    d = CONTENT_MAT / d_name
    if not d.exists():
        continue
    for root, dirs, files in os.walk(d):
        for f in files:
            if not f.endswith('.vtf') or f.endswith('.bak'):
                continue
            name_lower = f.lower().replace('.vtf', '')
            if any(fn in name_lower for fn in fire_names):
                vtf_path = Path(root) / f
                bak_path = Path(str(vtf_path) + '.bak')
                vtf_info = read_vtf_header(vtf_path)
                rel = vtf_path.relative_to(CONTENT_MAT)
                if vtf_info:
                    fmt_name = DXT_NAMES.get(vtf_info['format'], '?')
                    print(f"  {rel}:")
                    print(f"    Size: {vtf_info['width']}x{vtf_info['height']}, {fmt_name}, {vtf_info['mipmaps']} mips, {vtf_info['frames']} frames")
                    print(f"    Flags: {vtf_info['flags']:#010x}, FileSize: {vtf_info['size']/1024:.0f}KB")
                    if bak_path.exists():
                        bak_info = read_vtf_header(bak_path)
                        if bak_info:
                            bak_fmt = DXT_NAMES.get(bak_info['format'], '?')
                            print(f"    Original: {bak_info['width']}x{bak_info['height']}, {bak_fmt}, {bak_info['mipmaps']} mips, {bak_info['frames']} frames")
                            print(f"    Orig flags: {bak_info['flags']:#010x}, FileSize: {bak_info['size']/1024:.0f}KB")
