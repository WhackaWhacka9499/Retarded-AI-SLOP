# deep_particle_diag.py — Comprehensive particle effect diagnostic
# Checks: load order conflicts, mipmap chains, PCF references, download folder, all sources
import os, sys, struct, glob
from pathlib import Path
from collections import defaultdict

GMOD_ROOT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod")
CONTENT_MAT = GMOD_ROOT / "addons" / "Content" / "materials"
ADDONS_DIR = GMOD_ROOT / "addons"
DOWNLOAD_DIR = GMOD_ROOT / "download"
WORKSHOP = Path(r"G:\Program Files (x86)\Steam\steamapps\workshop\content\4000")
BASE_MAT = GMOD_ROOT / "materials"

EFFECT_KEYWORDS = {"particle/", "particles/", "effects/", "sprites/", "pfx/"}

print("=" * 70)
print("🔬 DEEP PARTICLE EFFECT DIAGNOSTIC")
print("=" * 70)

# ============================================================
# 1. Check if Content addon even has priority
# ============================================================
print("\n\n📋 SECTION 1: ADDON LOAD ORDER")
print("-" * 50)

# GMod loads addons alphabetically by folder name
loose_addons = []
if ADDONS_DIR.exists():
    for d in sorted(ADDONS_DIR.iterdir()):
        if d.is_dir() and d.name != "Content":
            mat = d / "materials"
            if mat.exists():
                loose_addons.append(d.name)

print(f"Loose addon folders with materials (sorted by load order):")
content_pos = None
for i, name in enumerate(sorted(loose_addons + ["Content"])):
    marker = " ← YOUR TEXTURES" if name == "Content" else ""
    print(f"  {i+1}. {name}{marker}")
    if name == "Content":
        content_pos = i + 1

# Check addons that come AFTER "Content" alphabetically (they override it)
overriders = [n for n in sorted(loose_addons + ["Content"]) if n > "Content" and n != "Content"]
if overriders:
    print(f"\n⚠ {len(overriders)} addon(s) load AFTER Content and can override its materials:")
    for name in overriders[:10]:
        mat_dir = ADDONS_DIR / name / "materials"
        count = sum(1 for _ in mat_dir.rglob("*.vtf")) if mat_dir.exists() else 0
        print(f"  • {name} ({count} VTF files)")


# ============================================================
# 2. Check download folder for conflicting materials
# ============================================================
print("\n\n📋 SECTION 2: DOWNLOAD FOLDER CONFLICTS")
print("-" * 50)

download_materials = DOWNLOAD_DIR / "materials"
download_conflicts = []
if download_materials.exists():
    for root, dirs, files in os.walk(download_materials):
        for f in files:
            if f.endswith('.vtf') or f.endswith('.vmt'):
                rel = os.path.relpath(os.path.join(root, f), download_materials).replace("\\", "/").lower()
                if any(kw in rel for kw in EFFECT_KEYWORDS):
                    # Check if this conflicts with Content
                    content_file = CONTENT_MAT / rel
                    if content_file.exists():
                        download_conflicts.append(rel)

    if download_conflicts:
        print(f"🔴 {len(download_conflicts)} particle materials in download/ conflict with Content:")
        for c in download_conflicts[:20]:
            print(f"  • {c}")
        if len(download_conflicts) > 20:
            print(f"  ... +{len(download_conflicts)-20} more")
    else:
        print("✅ No particle material conflicts in download folder")
else:
    print("✅ No download/materials folder exists")


# ============================================================
# 3. Check garrysmod/materials (base) for conflicts
# ============================================================
print("\n\n📋 SECTION 3: BASE MATERIALS CONFLICTS")
print("-" * 50)

base_conflicts = []
if BASE_MAT.exists():
    for root, dirs, files in os.walk(BASE_MAT):
        for f in files:
            if f.endswith('.vtf') or f.endswith('.vmt'):
                rel = os.path.relpath(os.path.join(root, f), BASE_MAT).replace("\\", "/").lower()
                if any(kw in rel for kw in EFFECT_KEYWORDS):
                    content_file = CONTENT_MAT / rel
                    if content_file.exists():
                        base_conflicts.append(rel)

    if base_conflicts:
        print(f"⚠ {len(base_conflicts)} particle materials in garrysmod/materials/ conflict with Content:")
        for c in base_conflicts[:15]:
            print(f"  • {c}")
        if len(base_conflicts) > 15:
            print(f"  ... +{len(base_conflicts)-15} more")
        print("\n  Note: garrysmod/materials/ loads BEFORE addons, so Content should override these.")
    else:
        print("✅ No particle material conflicts in base materials")
else:
    print("✅ No garrysmod/materials folder")


# ============================================================
# 4. Check GMA workshop addons for conflicts with Content
# ============================================================
print("\n\n📋 SECTION 4: WORKSHOP GMA CONFLICTS")
print("-" * 50)

gma_conflicts = defaultdict(list)
scanned = 0

if WORKSHOP.exists():
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
                    f.read(1)  # version
                    f.read(8)  # steamid
                    f.read(8)  # timestamp
                    while f.read(1) != b"\x00": pass
                    name_bytes = b""
                    while True:
                        c = f.read(1)
                        if c == b"\x00": break
                        name_bytes += c
                    addon_name = name_bytes.decode("utf-8", errors="replace")
                    while f.read(1) != b"\x00": pass
                    while f.read(1) != b"\x00": pass
                    f.read(4)

                    while True:
                        num_data = f.read(4)
                        if len(num_data) < 4: break
                        num = struct.unpack("<I", num_data)[0]
                        if num == 0: break
                        fn_bytes = b""
                        while True:
                            c = f.read(1)
                            if c == b"\x00": break
                            fn_bytes += c
                        fn = fn_bytes.decode("utf-8", errors="replace")
                        f.read(12)  # size + crc

                        fn_lower = fn.lower().replace("\\", "/")
                        if fn_lower.startswith("materials/"):
                            rel = fn_lower[10:]  # strip "materials/"
                            if any(kw in rel for kw in EFFECT_KEYWORDS):
                                if (fn_lower.endswith(".vtf") or fn_lower.endswith(".vmt")):
                                    content_file = CONTENT_MAT / rel
                                    if content_file.exists():
                                        gma_conflicts[f"{wid} ({addon_name})"].append(rel)
            except:
                pass

total_gma_conflicts = sum(len(v) for v in gma_conflicts.values())
if gma_conflicts:
    print(f"⚠ {total_gma_conflicts} particle material conflicts across {len(gma_conflicts)} GMA addons:")
    print(f"  (These GMA files contain the SAME materials that are in Content)")
    for gma, files in sorted(gma_conflicts.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"\n  📦 {gma}: {len(files)} conflicts")
        for fp in files[:5]:
            print(f"    • {fp}")
        if len(files) > 5:
            print(f"    ... +{len(files)-5} more")
else:
    print("✅ No GMA conflicts found")


# ============================================================
# 5. Check VTF mipmap chain and resolution quality
# ============================================================
print("\n\n📋 SECTION 5: VTF MIPMAP CHAIN CHECK")
print("-" * 50)

# Check particle VTFs for mipmap issues
no_mipmaps = []
extreme_upscale = []
very_large = []

for d in [CONTENT_MAT / "particle", CONTENT_MAT / "effects", CONTENT_MAT / "sprites"]:
    if not d.exists():
        continue
    for root, dirs, files in os.walk(d):
        for f in files:
            if not f.endswith('.vtf') or f.endswith('.bak'):
                continue
            path = Path(root) / f
            bak = Path(str(path) + '.bak')

            try:
                with open(path, 'rb') as fh:
                    header = fh.read(30)
                    if len(header) < 30 or header[:4] != b'VTF\x00':
                        continue

                    width = struct.unpack_from('<H', header, 16)[0]
                    height = struct.unpack_from('<H', header, 18)[0]
                    # Mipmap count is at offset 28
                    mipmaps = struct.unpack_from('B', header, 28)[0]

                    rel = path.relative_to(CONTENT_MAT)

                    if mipmaps <= 1:
                        no_mipmaps.append(f"{rel} ({width}x{height}, {mipmaps} mipmaps)")

                    if width >= 4096 or height >= 4096:
                        very_large.append(f"{rel} ({width}x{height})")

                    # Check upscale ratio
                    if bak.exists():
                        with open(bak, 'rb') as bh:
                            bheader = bh.read(30)
                            if len(bheader) >= 24:
                                orig_w = struct.unpack_from('<H', bheader, 16)[0]
                                orig_h = struct.unpack_from('<H', bheader, 18)[0]
                                ratio = max(width / max(orig_w, 1), height / max(orig_h, 1))
                                if ratio >= 32 and (orig_w <= 64 or orig_h <= 64):
                                    extreme_upscale.append(
                                        f"{rel} ({orig_w}x{orig_h} → {width}x{height}, {ratio:.0f}x)")
            except:
                pass

if no_mipmaps:
    print(f"🟡 {len(no_mipmaps)} VTFs with 0-1 mipmaps (could cause rendering issues):")
    for item in no_mipmaps[:10]:
        print(f"  • {item}")
    if len(no_mipmaps) > 10:
        print(f"  ... +{len(no_mipmaps)-10} more")
else:
    print("✅ All particle VTFs have mipmap chains")

print()
if extreme_upscale:
    print(f"🟡 {len(extreme_upscale)} VTFs with extreme upscale ratios (32x+, tiny original):")
    for item in extreme_upscale[:10]:
        print(f"  • {item}")
    if len(extreme_upscale) > 10:
        print(f"  ... +{len(extreme_upscale)-10} more")
else:
    print("✅ No extreme upscale ratios detected")

print()
if very_large:
    print(f"ℹ {len(very_large)} particle VTFs at 4096+ resolution:")
    for item in very_large[:10]:
        print(f"  • {item}")
    if len(very_large) > 10:
        print(f"  ... +{len(very_large)-10} more")


# ============================================================
# 6. Check for PCF files and their material references
# ============================================================
print("\n\n📋 SECTION 6: PCF PARTICLE CONFIG FILES")
print("-" * 50)

pcf_locations = []
# Check particles/ folder in garrysmod
particles_dir = GMOD_ROOT / "particles"
if particles_dir.exists():
    for f in particles_dir.glob("*.pcf"):
        pcf_locations.append(("garrysmod/particles", f))

# Check addons for PCF files
for addon_dir in ADDONS_DIR.iterdir():
    if addon_dir.is_dir():
        pdir = addon_dir / "particles"
        if pdir.exists():
            for f in pdir.glob("*.pcf"):
                pcf_locations.append((f"addons/{addon_dir.name}/particles", f))

print(f"Found {len(pcf_locations)} PCF files:")
for loc, f in pcf_locations:
    size_kb = f.stat().st_size / 1024
    print(f"  • {loc}/{f.name} ({size_kb:.0f}KB)")


# ============================================================
# 7. Summary & Recommendations
# ============================================================
print("\n\n" + "=" * 70)
print("📊 DIAGNOSTIC SUMMARY")
print("=" * 70)

problems = []
if overriders:
    problems.append(f"• {len(overriders)} addon(s) load AFTER Content (alphabetically) and may override its textures")
if download_conflicts:
    problems.append(f"• {len(download_conflicts)} server-downloaded materials conflict with Content")
if base_conflicts:
    problems.append(f"• {len(base_conflicts)} base game materials conflict with Content")
if gma_conflicts:
    problems.append(f"• {total_gma_conflicts} GMA workshop addon materials conflict with Content")
if no_mipmaps:
    problems.append(f"• {len(no_mipmaps)} particle VTFs have missing mipmap chains")
if extreme_upscale:
    problems.append(f"• {len(extreme_upscale)} particle textures upscaled from tiny originals (32x+)")

if problems:
    print("\n⚠ POTENTIAL ISSUES:")
    for p in problems:
        print(f"  {p}")
else:
    print("\n✅ No obvious conflicts detected")
