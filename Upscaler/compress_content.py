"""
compress_content.py — Compress RGBA8888/BGR888 VTFs to DXT1/DXT5 using VTFLib.dll.
Also deletes .bak backup files to reclaim space.
"""
import ctypes
import os, sys, struct, time
from pathlib import Path
from PIL import Image

# ===== CONFIG =====
CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")
VTFLIB_DLL = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\VTFLib\x64\VTFLib.dll"

# VTF formats
IMAGE_FORMAT_RGBA8888 = 0
IMAGE_FORMAT_BGR888 = 3
IMAGE_FORMAT_DXT1 = 13
IMAGE_FORMAT_DXT5 = 15
UNCOMPRESSED_FORMATS = {0, 1, 2, 3}  # RGBA8888, ABGR8888, RGB888, BGR888


class SVTFCreateOptions(ctypes.Structure):
    _fields_ = [
        ("uiVersion0", ctypes.c_uint), ("uiVersion1", ctypes.c_uint),
        ("ImageFormat", ctypes.c_int), ("uiFlags", ctypes.c_uint),
        ("uiStartFrame", ctypes.c_uint), ("sBumpScale", ctypes.c_float),
        ("bMipmaps", ctypes.c_bool), ("MipmapFilter", ctypes.c_int),
        ("MipmapSharpenFilter", ctypes.c_int), ("bThumbnail", ctypes.c_bool),
        ("bReflectivity", ctypes.c_bool), ("bResize", ctypes.c_bool),
        ("ResizeMethod", ctypes.c_int), ("ResizeFilter", ctypes.c_int),
        ("ResizeSharpenFilter", ctypes.c_int), ("uiResizeWidth", ctypes.c_uint),
        ("uiResizeHeight", ctypes.c_uint), ("bResizeClamp", ctypes.c_bool),
        ("uiResizeClampWidth", ctypes.c_uint), ("uiResizeClampHeight", ctypes.c_uint),
        ("bGammaCorrection", ctypes.c_bool), ("sGammaCorrection", ctypes.c_float),
        ("bNormalMap", ctypes.c_bool), ("KernelFilter", ctypes.c_int),
        ("HeightConversionMethod", ctypes.c_int), ("NormalAlphaResult", ctypes.c_int),
        ("bNormalMinimumZ", ctypes.c_uint), ("sNormalScale", ctypes.c_float),
        ("bNormalWrap", ctypes.c_bool), ("bNormalInvertX", ctypes.c_bool),
        ("bNormalInvertY", ctypes.c_bool), ("bNormalInvertZ", ctypes.c_bool),
        ("bSphereMap", ctypes.c_bool),
    ]


def init_vtflib():
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
    vtf.vlInitialize()
    return vtf


def has_alpha(img):
    """Check if image has meaningful alpha channel."""
    alpha = img.split()[3]
    sample = list(alpha.getdata())[:2000]
    return any(a != 255 for a in sample)


def compress_vtf(vtflib, vtf_path, flags, width, height, rgba_data, use_alpha):
    """Compress RGBA data to DXT and save as VTF."""
    handle = ctypes.c_uint(0)
    vtflib.vlCreateImage(ctypes.byref(handle))
    vtflib.vlBindImage(handle)

    opts = SVTFCreateOptions()
    vtflib.vlImageCreateDefaultCreateStructure(ctypes.byref(opts))
    opts.ImageFormat = IMAGE_FORMAT_DXT5 if use_alpha else IMAGE_FORMAT_DXT1
    opts.uiFlags = flags
    opts.bMipmaps = True
    opts.MipmapFilter = 0
    opts.bThumbnail = True
    opts.bReflectivity = True
    opts.uiVersion0 = 7
    opts.uiVersion1 = 2

    ok = vtflib.vlImageCreateSingle(width, height, rgba_data, ctypes.byref(opts))
    if not ok:
        err = vtflib.vlGetLastError()
        vtflib.vlDeleteImage(handle)
        return False, f"create failed: {err}"

    tmp_path = str(vtf_path) + '.dxttmp'
    ok = vtflib.vlImageSave(tmp_path.encode('ascii'))
    vtflib.vlDeleteImage(handle)
    if not ok:
        return False, "save failed"

    os.replace(tmp_path, str(vtf_path))
    return True, "ok"


def main():
    print("🔧 Content VTF Compressor (RGBA8888/BGR888 → DXT)")
    print(f"   Content: {CONTENT_MAT}")
    print()

    vtflib = init_vtflib()
    print("✅ VTFLib initialized")

    # Phase 1: Find all uncompressed VTFs
    from srctools.vtf import VTF as SrcVTF
    
    targets = []
    for root, dirs, fnames in os.walk(CONTENT_MAT):
        for f in fnames:
            if not f.endswith('.vtf') or f.endswith('.bak') or f.endswith('.tmp'):
                continue
            fp = Path(root) / f
            try:
                with open(fp, 'rb') as fh:
                    hdr = fh.read(60)
                if hdr[:4] != b'VTF\x00':
                    continue
                fmt = struct.unpack_from('<I', hdr, 52)[0]
                if fmt in UNCOMPRESSED_FORMATS:
                    frames = struct.unpack_from('<H', hdr, 24)[0]
                    flags = struct.unpack_from('<I', hdr, 20)[0]
                    targets.append((fp, fmt, frames, flags))
            except:
                pass

    print(f"   Found {len(targets)} uncompressed VTFs")
    
    # Phase 2: Compress each file
    success = 0
    skipped = 0
    failed = 0
    saved_bytes = 0
    t0 = time.time()

    for i, (fp, fmt, frames, flags) in enumerate(targets):
        rel = fp.relative_to(CONTENT_MAT)
        old_size = fp.stat().st_size

        # Skip animated VTFs
        if frames > 1:
            print(f"  ⚠ Skip {rel}: animated ({frames} frames)")
            skipped += 1
            continue

        try:
            # Read pixels via srctools
            with open(fp, 'rb') as fh:
                vtf = SrcVTF.read(fh)
                frame = vtf.get(frame=0, mipmap=0)
                w, h = vtf.width, vtf.height
                data = bytes(frame)
            
            img = Image.frombytes('RGBA', (w, h), data)
            alpha = has_alpha(img)
            rgba_data = img.tobytes()

            ok, msg = compress_vtf(vtflib, fp, flags, w, h, rgba_data, alpha)
            if ok:
                new_size = fp.stat().st_size
                saved = old_size - new_size
                saved_bytes += saved
                success += 1
                fmt_name = "DXT5" if alpha else "DXT1"
                if success <= 5 or success % 50 == 0:
                    print(f"  ✅ {rel}: {w}x{h} {fmt_name} ({old_size//1024}KB → {new_size//1024}KB, saved {saved//1024}KB)")
            else:
                failed += 1
                print(f"  ❌ {rel}: {msg}")
        except Exception as e:
            failed += 1
            print(f"  ❌ {rel}: {e}")

    elapsed = time.time() - t0
    print(f"\n✅ Compression done in {elapsed:.0f}s")
    print(f"   Success: {success}")
    print(f"   Skipped: {skipped}")
    print(f"   Failed: {failed}")
    print(f"   Space saved: {saved_bytes/1024**3:.2f} GB")

    # Phase 3: Delete .bak backups
    print(f"\n🗑️ Phase 3: Cleaning up .bak backups...")
    bak_count = 0
    bak_bytes = 0
    for root, dirs, fnames in os.walk(CONTENT_MAT):
        for f in fnames:
            if f.endswith('.vtf.bak'):
                bp = Path(root) / f
                sz = bp.stat().st_size
                bp.unlink()
                bak_count += 1
                bak_bytes += sz

    print(f"   Deleted {bak_count} backup files ({bak_bytes/1024**3:.2f} GB)")
    print(f"\n🎉 Total space saved: {(saved_bytes + bak_bytes)/1024**3:.2f} GB")

    vtflib.vlShutdown()


if __name__ == '__main__':
    main()
