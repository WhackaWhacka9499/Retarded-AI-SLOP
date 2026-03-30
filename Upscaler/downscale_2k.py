"""
downscale_2k.py — Downscale all >2048px VTFs to max 2048px.
Reads pixels via srctools, resizes with Pillow, saves as DXT via VTFLib.dll.
Processes files in-place (overwrites the existing VTF).
"""
import ctypes
import os, sys, struct, time, math
from pathlib import Path
from PIL import Image

# ===== CONFIG =====
CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")
VTFLIB_DLL = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\VTFLib\x64\VTFLib.dll"
MAX_DIM = 2048
UNCOMPRESSED_FMTS = {0, 1, 2, 3}  # RGBA8888, ABGR8888, RGB888, BGR888

IMAGE_FORMAT_DXT1 = 13
IMAGE_FORMAT_DXT5 = 15


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
    """Quick alpha check — sample first 2000 pixels."""
    alpha = img.split()[3]
    return any(a != 255 for a in list(alpha.getdata())[:2000])


def save_vtf_dxt(vtflib, rgba_data, w, h, out_path, flags, use_alpha):
    """Save RGBA as DXT VTF."""
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

    ok = vtflib.vlImageCreateSingle(w, h, rgba_data, ctypes.byref(opts))
    if not ok:
        vtflib.vlDeleteImage(handle)
        return False

    tmp = str(out_path) + '.dxttmp'
    ok = vtflib.vlImageSave(tmp.encode('ascii'))
    vtflib.vlDeleteImage(handle)
    if not ok:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False

    os.replace(tmp, str(out_path))
    return True


def main():
    from srctools.vtf import VTF as SrcVTF

    print(f"📐 Content VTF Downscaler (>2048 → {MAX_DIM})")
    print(f"   Content: {CONTENT_MAT}")
    print()

    vtflib = init_vtflib()
    print("✅ VTFLib initialized")

    # Phase 1: Collect files > 2048px
    targets = []
    for root, dirs, fnames in os.walk(CONTENT_MAT):
        for f in fnames:
            if not f.endswith('.vtf') or f.endswith('.bak') or f.endswith('.tmp'):
                continue
            fp = Path(root) / f
            try:
                with open(fp, 'rb') as fh:
                    hdr = fh.read(56)
                if hdr[:4] != b'VTF\x00':
                    continue
                w = struct.unpack_from('<H', hdr, 16)[0]
                h = struct.unpack_from('<H', hdr, 18)[0]
                flags = struct.unpack_from('<I', hdr, 20)[0]
                frames = struct.unpack_from('<H', hdr, 24)[0]
                fmt = struct.unpack_from('<I', hdr, 52)[0]
                if frames > 1:
                    continue  # skip animated
                if flags & 0x4000:
                    continue  # skip cubemaps (ENVMAP flag)
                if max(w, h) <= MAX_DIM:
                    continue  # already small enough
                targets.append((fp, w, h, flags, fmt))
            except:
                pass

    print(f"   Found {len(targets)} files >2048px to downscale")
    total_old = sum(fp.stat().st_size for fp, *_ in targets)
    print(f"   Current size: {total_old / 1024**3:.1f} GB")

    # Phase 2: Downscale each file
    success = 0
    failed = 0
    saved_bytes = 0
    t0 = time.time()

    for i, (fp, orig_w, orig_h, flags, fmt) in enumerate(targets):
        rel = fp.relative_to(CONTENT_MAT)
        old_size = fp.stat().st_size

        # Calculate new dimensions (maintain aspect ratio, cap at MAX_DIM)
        scale = MAX_DIM / max(orig_w, orig_h)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        # Ensure power of 2
        new_w = 1 << (new_w - 1).bit_length() if new_w > 1 else 1
        new_h = 1 << (new_h - 1).bit_length() if new_h > 1 else 1
        new_w = min(new_w, MAX_DIM)
        new_h = min(new_h, MAX_DIM)

        try:
            with open(fp, 'rb') as fh:
                vtf = SrcVTF.read(fh)
                frame = vtf.get(frame=0, mipmap=0)
                data = bytes(frame)

            img = Image.frombytes('RGBA', (orig_w, orig_h), data)

            # Resize
            img = img.resize((new_w, new_h), Image.LANCZOS)

            # Determine DXT format
            if fmt in UNCOMPRESSED_FMTS:
                use_alpha = has_alpha(img)
            else:
                use_alpha = (fmt == 15)  # DXT5 = alpha, DXT1 = no alpha

            rgba = img.tobytes()
            ok = save_vtf_dxt(vtflib, rgba, new_w, new_h, fp, flags, use_alpha)

            if ok:
                new_size = fp.stat().st_size
                saved = old_size - new_size
                saved_bytes += saved
                success += 1

                if success <= 5 or success % 500 == 0:
                    fmt_name = "DXT5" if use_alpha else "DXT1"
                    print(f"  ✅ [{success}/{len(targets)}] {rel}: "
                          f"{orig_w}x{orig_h} → {new_w}x{new_h} {fmt_name} "
                          f"({old_size//1024}KB → {new_size//1024}KB)")
            else:
                failed += 1
                if failed <= 10:
                    print(f"  ❌ {rel}: VTFLib save failed")

        except Exception as e:
            failed += 1
            if failed <= 10:
                print(f"  ❌ {rel}: {e}")

        # Progress every 200 files
        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (len(targets) - i - 1) / rate
            print(f"  ... {i+1}/{len(targets)} done ({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining, "
                  f"saved {saved_bytes/1024**3:.1f} GB so far)")

    elapsed = time.time() - t0
    print(f"\n✅ Done in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"   Success: {success}")
    print(f"   Failed: {failed}")
    print(f"   Space saved: {saved_bytes / 1024**3:.1f} GB")

    vtflib.vlShutdown()


if __name__ == '__main__':
    main()
