"""
upscale_particles.py — Properly upscale particle/effect VTFs.

Pipeline:
1. Read original VTF from .bak using srctools (gets RGBA pixel data + metadata)
2. Upscale with RealESRGAN AI
3. Save as DXT5-compressed VTF using VTFLib.dll (proper mipmaps, compression)

This avoids srctools' bugs (RGBA8888 output, 0 mipmaps) by using VTFLib for the write step.
"""
import ctypes
import os, sys, struct, math, time, tempfile, shutil, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# ===== CONFIG =====
CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")
VTFLIB_DLL = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\VTFLib\x64\VTFLib.dll"
UPSCALER_EXE = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\realesrgan\realesrgan-ncnn-vulkan.exe"
UPSCALER_MODEL = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\realesrgan\models"
SCALE = 4  # 4x upscale
BATCH_SIZE = 30  # Files per GPU batch (smaller = more reliable)
EFFECT_DIRS = ["particle", "particles", "effects", "pfx", "sprites"]
GPU_ID = 0

sys.path.insert(0, str(Path(r"C:\Users\Alexander Jarvis\Desktop\Upscaler")))

# ===== VTFLib DLL BINDINGS =====
class SVTFCreateOptions(ctypes.Structure):
    _fields_ = [
        ("uiVersion0", ctypes.c_uint),
        ("uiVersion1", ctypes.c_uint),
        ("ImageFormat", ctypes.c_int),
        ("uiFlags", ctypes.c_uint),
        ("uiStartFrame", ctypes.c_uint),
        ("sBumpScale", ctypes.c_float),
        ("bMipmaps", ctypes.c_bool),
        ("MipmapFilter", ctypes.c_int),
        ("MipmapSharpenFilter", ctypes.c_int),
        ("bThumbnail", ctypes.c_bool),
        ("bReflectivity", ctypes.c_bool),
        ("bResize", ctypes.c_bool),
        ("ResizeMethod", ctypes.c_int),
        ("ResizeFilter", ctypes.c_int),
        ("ResizeSharpenFilter", ctypes.c_int),
        ("uiResizeWidth", ctypes.c_uint),
        ("uiResizeHeight", ctypes.c_uint),
        ("bResizeClamp", ctypes.c_bool),
        ("uiResizeClampWidth", ctypes.c_uint),
        ("uiResizeClampHeight", ctypes.c_uint),
        ("bGammaCorrection", ctypes.c_bool),
        ("sGammaCorrection", ctypes.c_float),
        ("bNormalMap", ctypes.c_bool),
        ("KernelFilter", ctypes.c_int),
        ("HeightConversionMethod", ctypes.c_int),
        ("NormalAlphaResult", ctypes.c_int),
        ("bNormalMinimumZ", ctypes.c_uint),
        ("sNormalScale", ctypes.c_float),
        ("bNormalWrap", ctypes.c_bool),
        ("bNormalInvertX", ctypes.c_bool),
        ("bNormalInvertY", ctypes.c_bool),
        ("bNormalInvertZ", ctypes.c_bool),
        ("bSphereMap", ctypes.c_bool),
    ]

IMAGE_FORMAT_DXT1 = 13
IMAGE_FORMAT_DXT5 = 15
IMAGE_FORMAT_RGBA8888 = 0
MAX_SIZE = 4096  # Max VTF dimension


def init_vtflib():
    """Initialize VTFLib and return the DLL handle."""
    vtf = ctypes.cdll.LoadLibrary(VTFLIB_DLL)
    vtf.vlInitialize.restype = ctypes.c_bool
    vtf.vlShutdown.restype = None
    vtf.vlCreateImage.argtypes = [ctypes.POINTER(ctypes.c_uint)]
    vtf.vlCreateImage.restype = ctypes.c_bool
    vtf.vlBindImage.argtypes = [ctypes.c_uint]
    vtf.vlBindImage.restype = ctypes.c_bool
    vtf.vlDeleteImage.argtypes = [ctypes.c_uint]
    vtf.vlDeleteImage.restype = None
    vtf.vlImageCreateDefaultCreateStructure.argtypes = [ctypes.POINTER(SVTFCreateOptions)]
    vtf.vlImageCreateDefaultCreateStructure.restype = None
    vtf.vlImageCreateSingle.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p, ctypes.POINTER(SVTFCreateOptions)]
    vtf.vlImageCreateSingle.restype = ctypes.c_bool
    vtf.vlImageSave.argtypes = [ctypes.c_char_p]
    vtf.vlImageSave.restype = ctypes.c_bool
    vtf.vlGetLastError.restype = ctypes.c_char_p
    vtf.vlImageGetFormat.restype = ctypes.c_int
    vtf.vlImageGetMipmapCount.restype = ctypes.c_uint
    vtf.vlInitialize()
    return vtf


def read_vtf_pixels(path):
    """Read VTF file and return (PIL Image, flags, version, frame_count)."""
    from srctools.vtf import VTF as SrcVTF
    # Also read frame count from binary header (srctools might not expose it)
    with open(path, 'rb') as f:
        raw_hdr = f.read(30)
    frame_count = struct.unpack_from('<H', raw_hdr, 24)[0] if len(raw_hdr) >= 26 else 1
    
    try:
        with open(path, 'rb') as f:
            vtf = SrcVTF.read(f)
            frame = vtf.get(frame=0, mipmap=0)
            w, h = vtf.width, vtf.height
            data = bytes(frame)
            flags = vtf.flags.value  # .value to get int from VTFFlags enum
            version = vtf.version
        img = Image.frombytes('RGBA', (w, h), data)
        return img, flags, version, frame_count
    except Exception:
        # Fallback: read flags from binary header
        flags = struct.unpack_from('<I', raw_hdr, 20)[0]
        # Re-try srctools read
        with open(path, 'rb') as f:
            vtf = SrcVTF.read(f)
            frame = vtf.get(frame=0, mipmap=0)
            w, h = vtf.width, vtf.height
            data = bytes(frame)
            version = vtf.version
        img = Image.frombytes('RGBA', (w, h), data)
        return img, flags, version, frame_count


def has_alpha(img):
    """Check if image has meaningful alpha channel."""
    alpha = img.split()[3]
    data = list(alpha.getdata())
    # Sample first 2000 pixels
    sample = data[:2000]
    return any(a != 255 for a in sample)


def save_vtf_dxt(vtflib, rgba_data, width, height, out_path, flags=0x2000, use_alpha=True):
    """Save RGBA pixel data as a DXT-compressed VTF using VTFLib."""
    handle = ctypes.c_uint(0)
    vtflib.vlCreateImage(ctypes.byref(handle))
    vtflib.vlBindImage(handle)

    opts = SVTFCreateOptions()
    vtflib.vlImageCreateDefaultCreateStructure(ctypes.byref(opts))
    opts.ImageFormat = IMAGE_FORMAT_DXT5 if use_alpha else IMAGE_FORMAT_DXT1
    opts.uiFlags = flags
    opts.bMipmaps = True
    opts.MipmapFilter = 0  # Box filter
    opts.bThumbnail = True
    opts.bReflectivity = True
    opts.uiVersion0 = 7
    opts.uiVersion1 = 2

    ok = vtflib.vlImageCreateSingle(width, height, rgba_data, ctypes.byref(opts))
    if not ok:
        err = vtflib.vlGetLastError()
        vtflib.vlDeleteImage(handle)
        return False, f"vlImageCreateSingle failed: {err}"

    ok = vtflib.vlImageSave(out_path.encode('ascii') if isinstance(out_path, str) else out_path)
    vtflib.vlDeleteImage(handle)
    if not ok:
        return False, f"vlImageSave failed"

    # VTFLib correctly writes mipmap count at byte 56 — no patching needed
    return True, "ok"


def upscale_batch(input_dir, output_dir, scale=4):
    """Run RealESRGAN on a batch of PNG files."""
    cmd = [
        UPSCALER_EXE,
        "-i", str(input_dir),
        "-o", str(output_dir),
        "-s", str(scale),
        "-n", "realesrgan-x4plus",
        "-m", UPSCALER_MODEL,
        "-g", str(GPU_ID),
        "-f", "png",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return result.returncode == 0


def main():
    print("🔧 Particle VTF Upscaler (DXT5 Pipeline)")
    print(f"   Content: {CONTENT_MAT}")
    print(f"   Scale: {SCALE}x")
    print(f"   VTFLib: {VTFLIB_DLL}")
    print()

    # Initialize VTFLib
    vtflib = init_vtflib()
    print("✅ VTFLib initialized")

    # Collect files to process
    # Use .bak files as source (pre-upscale originals)
    files = []
    for d_name in EFFECT_DIRS:
        d = CONTENT_MAT / d_name
        if not d.exists():
            continue
        for root, dirs, fnames in os.walk(d):
            for f in fnames:
                if not f.endswith('.vtf') or f.endswith('.bak') or f.endswith('.tmp'):
                    continue
                vtf_path = Path(root) / f
                bak_path = Path(str(vtf_path) + '.bak')
                
                # Use .bak as source if available, otherwise skip
                # (files without .bak were either never upscaled or are PPE originals from the extracted GMA)
                source = bak_path if bak_path.exists() else vtf_path
                files.append((vtf_path, source))

    print(f"   Found {len(files)} particle VTFs to process")
    
    # Check which have backups
    with_bak = sum(1 for _, s in files if str(s).endswith('.bak'))
    print(f"   With .bak source: {with_bak}")
    print(f"   Direct (PPE originals or no backup): {len(files) - with_bak}")

    # Phase 1: Read all source VTFs and save as PNGs for AI upscaling
    print(f"\n📖 Phase 1: Reading VTFs and saving PNGs...")
    tmp_in = Path(tempfile.mkdtemp(prefix="pfx_in_"))
    tmp_out = Path(tempfile.mkdtemp(prefix="pfx_out_"))
    
    file_map = {}  # idx -> (vtf_path, source_path, flags, has_alpha, orig_w, orig_h)
    skipped = 0
    
    for i, (vtf_path, source_path) in enumerate(files):
        try:
            img, flags, version, frame_count = read_vtf_pixels(source_path)
            w, h = img.size
            
            # Skip animated VTFs (multi-frame) — VTFLib single-frame only
            if frame_count > 1:
                print(f"  ⚠ Skip {vtf_path.relative_to(CONTENT_MAT)}: animated ({frame_count} frames)")
                skipped += 1
                continue
            
            # Skip if already at max size (no room to upscale)
            new_w = min(w * SCALE, MAX_SIZE)
            new_h = min(h * SCALE, MAX_SIZE)
            if new_w <= w and new_h <= h:
                skipped += 1
                continue
            
            # Cap source to 1024px max dimension to prevent GPU hangs
            # (4x upscale of 1024 = 4096 = MAX_SIZE)
            max_dim = max(w, h)
            if max_dim > 1024:
                scale_down = 1024 / max_dim
                new_w_src = int(w * scale_down)
                new_h_src = int(h * scale_down)
                # Round to nearest power of 2
                new_w_src = 1 << (new_w_src - 1).bit_length() if new_w_src > 1 else 1
                new_h_src = 1 << (new_h_src - 1).bit_length() if new_h_src > 1 else 1
                img = img.resize((new_w_src, new_h_src), Image.LANCZOS)
                w, h = new_w_src, new_h_src
            
            alpha = has_alpha(img)
            
            # Save as PNG for AI
            png_name = f"{i:04d}.png"
            img.save(tmp_in / png_name, "PNG")
            file_map[i] = (vtf_path, source_path, flags, alpha, w, h)
            
        except Exception as e:
            print(f"  ⚠ Skip {vtf_path.relative_to(CONTENT_MAT)}: {e}")
            skipped += 1

    print(f"   Prepared {len(file_map)} PNGs ({skipped} skipped)")

    if not file_map:
        print("Nothing to upscale!")
        vtflib.vlShutdown()
        return

    # Phase 2: AI Upscale in batches
    print(f"\n🤖 Phase 2: AI Upscaling ({len(file_map)} files)...")
    t0 = time.time()
    
    # Split into batches
    indices = sorted(file_map.keys())
    for batch_start in range(0, len(indices), BATCH_SIZE):
        batch = indices[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(indices) + BATCH_SIZE - 1) // BATCH_SIZE
        
        # Create batch temp dirs
        batch_in = Path(tempfile.mkdtemp(prefix=f"pfx_b{batch_num}_in_"))
        batch_out = Path(tempfile.mkdtemp(prefix=f"pfx_b{batch_num}_out_"))
        
        # Copy batch PNGs
        for idx in batch:
            png_name = f"{idx:04d}.png"
            shutil.copy2(str(tmp_in / png_name), str(batch_in / png_name))
        
        print(f"  Batch {batch_num}/{total_batches}: {len(batch)} files...", end=" ", flush=True)
        ok = upscale_batch(batch_in, batch_out, SCALE)
        
        if ok:
            # Copy results back
            for idx in batch:
                png_name = f"{idx:04d}.png"
                result = batch_out / png_name
                if result.exists():
                    shutil.copy2(str(result), str(tmp_out / png_name))
            print("✅")
        else:
            # Retry failed batch one file at a time
            print("❌ batch failed, retrying individually...")
            for idx in batch:
                png_name = f"{idx:04d}.png"
                solo_in = Path(tempfile.mkdtemp(prefix="pfx_solo_in_"))
                solo_out = Path(tempfile.mkdtemp(prefix="pfx_solo_out_"))
                shutil.copy2(str(tmp_in / png_name), str(solo_in / png_name))
                try:
                    solo_ok = upscale_batch(solo_in, solo_out, SCALE)
                    if solo_ok and (solo_out / png_name).exists():
                        shutil.copy2(str(solo_out / png_name), str(tmp_out / png_name))
                except Exception:
                    pass
                shutil.rmtree(solo_in, ignore_errors=True)
                shutil.rmtree(solo_out, ignore_errors=True)
        
        # Cleanup batch dirs
        shutil.rmtree(batch_in, ignore_errors=True)
        shutil.rmtree(batch_out, ignore_errors=True)

    t_upscale = time.time() - t0
    print(f"   AI upscaling done in {t_upscale:.0f}s")

    # Phase 3: Convert upscaled PNGs to DXT5 VTFs using VTFLib
    print(f"\n💾 Phase 3: Creating DXT5 VTFs...")
    success = 0
    failed = 0

    for idx in sorted(file_map.keys()):
        vtf_path, source_path, flags, alpha, orig_w, orig_h = file_map[idx]
        png_name = f"{idx:04d}.png"
        upscaled_png = tmp_out / png_name
        rel = vtf_path.relative_to(CONTENT_MAT)

        if not upscaled_png.exists():
            print(f"  ⚠ {rel}: upscaled PNG missing")
            failed += 1
            continue

        try:
            img = Image.open(upscaled_png).convert('RGBA')
            w, h = img.size
            
            # Clamp to MAX_SIZE
            if w > MAX_SIZE or h > MAX_SIZE:
                ratio = min(MAX_SIZE / w, MAX_SIZE / h)
                w = int(w * ratio)
                h = int(h * ratio)
                img = img.resize((w, h), Image.LANCZOS)
            
            # Ensure power of 2
            w2 = 1 << (w - 1).bit_length()
            h2 = 1 << (h - 1).bit_length()
            if w2 != w or h2 != h:
                img = img.resize((w2, h2), Image.LANCZOS)
                w, h = w2, h2

            rgba_data = img.tobytes()
            
            # Save with VTFLib
            tmp_vtf = str(vtf_path) + '.dxttmp'
            ok, msg = save_vtf_dxt(vtflib, rgba_data, w, h, tmp_vtf, int(flags), alpha)
            
            if ok:
                os.replace(tmp_vtf, str(vtf_path))
                success += 1
                if success % 50 == 1 or success <= 5:
                    new_size = os.path.getsize(vtf_path) // 1024
                    print(f"  ✅ {rel}: {orig_w}x{orig_h} → {w}x{h} DXT{'5' if alpha else '1'} ({new_size}KB)")
            else:
                if os.path.exists(tmp_vtf):
                    os.remove(tmp_vtf)
                failed += 1
                print(f"  ❌ {rel}: {msg}")

        except Exception as e:
            failed += 1
            print(f"  ❌ {rel}: {e}")

    # Cleanup
    shutil.rmtree(tmp_in, ignore_errors=True)
    shutil.rmtree(tmp_out, ignore_errors=True)
    vtflib.vlShutdown()

    elapsed = time.time() - t0
    print(f"\n✅ Done in {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"   Success: {success}")
    print(f"   Failed: {failed}")
    print(f"   Skipped: {skipped}")


if __name__ == '__main__':
    main()
