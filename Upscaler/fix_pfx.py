"""
Targeted particle texture fixer with alpha preservation.
Re-upscales VTFs in particle/effects dirs using AI for RGB, LANCZOS for alpha.

OPTIMIZED: Batches AI calls (200 files per GPU invocation) and parallelizes VTF I/O.
"""
import os, sys, subprocess, tempfile, shutil, time
from pathlib import Path
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np

# Paths
REALESRGAN_EXE = Path(r"C:\Users\Alexander Jarvis\Desktop\Upscaler\realesrgan\realesrgan-ncnn-vulkan.exe")
MODELS_DIR = REALESRGAN_EXE.parent / "models"
CONTENT_MAT = Path(r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials")
EFFECT_DIRS = [
    CONTENT_MAT / "particle",
    CONTENT_MAT / "particles",
    CONTENT_MAT / "effects",
    CONTENT_MAT / "pfx",
    CONTENT_MAT / "sprites",
]
AI_MODEL = "realesrgan-x4plus-anime"
SCALE = 4
BATCH_SIZE = 200  # Files per GPU invocation
MAX_WORKERS = 6   # Parallel VTF read/write threads

sys.path.insert(0, str(Path(r"C:\Users\Alexander Jarvis\Desktop\Upscaler")))
try:
    from srctools.vtf import VTF, ImageFormats
except ImportError:
    print("ERROR: srctools not found. Run: pip install srctools")
    sys.exit(1)


def get_vtf_files(root: Path):
    """Find all VTF files that have .bak backups (meaning they were upscaled)."""
    files = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.endswith('.vtf') and not f.endswith('.bak'):
                bak = Path(dirpath) / (f + '.bak')
                if bak.exists():
                    files.append(Path(dirpath) / f)
    return files


def read_vtf_image(vtf_path: Path):
    """Read a VTF and return (PIL Image RGBA, VTF object)."""
    with open(vtf_path, 'rb') as f:
        vtf = VTF.read(f)
        frame = vtf.get(frame=0, mipmap=0)
        w, h = vtf.width, vtf.height
        data = bytes(frame)
    img = Image.frombytes('RGBA', (w, h), data)
    return img, vtf


def read_original(vtf_path: Path):
    """Read original .bak file and return metadata needed for processing."""
    bak_path = Path(str(vtf_path) + '.bak')
    orig_img, orig_vtf = read_vtf_image(bak_path)
    orig_w, orig_h = orig_vtf.width, orig_vtf.height
    new_w = min(orig_w * SCALE, 4096)
    new_h = min(orig_h * SCALE, 4096)

    # Skip if already at max
    if orig_w >= 4096 and orig_h >= 4096:
        return None  # Signal to skip

    # Extract alpha
    r, g, b, a = orig_img.split()
    alpha_arr = np.array(a)
    has_alpha = not (alpha_arr.min() == 255 and alpha_arr.max() == 255)

    return {
        'vtf_path': vtf_path,
        'orig_img': orig_img,
        'orig_vtf': orig_vtf,
        'orig_w': orig_w, 'orig_h': orig_h,
        'new_w': new_w, 'new_h': new_h,
        'alpha_channel': a,
        'has_alpha': has_alpha,
        'orig_format': orig_vtf.format,
        'orig_flags': orig_vtf.flags,
        'orig_version': orig_vtf.version,
    }


def batch_ai_upscale(items, in_dir: Path, out_dir: Path):
    """Save all RGB images to in_dir, run AI once, return mapping of index -> upscaled RGB."""
    in_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)

    # Save all BMPs in parallel
    def save_bmp(args):
        idx, item = args
        rgb = item['orig_img'].convert('RGB')
        bmp_path = in_dir / f"{idx:04d}.bmp"
        rgb.save(str(bmp_path), 'BMP')

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(save_bmp, enumerate(items)))

    # Single GPU invocation for the whole batch
    cmd = [
        str(REALESRGAN_EXE),
        "-i", str(in_dir),
        "-o", str(out_dir),
        "-n", AI_MODEL,
        "-s", str(SCALE),
        "-f", "png",
        "-g", "0",
        "-j", "2:2:2",
        "-t", "512",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    # Collect outputs
    outputs = {}
    for idx in range(len(items)):
        out_path = out_dir / f"{idx:04d}.png"
        if out_path.exists():
            outputs[idx] = Image.open(out_path).convert('RGB')
        else:
            outputs[idx] = None
    return outputs


def finalize_vtf(item, rgb_upscaled: Image.Image):
    """Combine AI RGB with LANCZOS alpha and write final VTF. Returns (path, success, error)."""
    vtf_path = item['vtf_path']
    new_w, new_h = item['new_w'], item['new_h']
    name = vtf_path.name

    try:
        # Resize to exact target
        if rgb_upscaled.size != (new_w, new_h):
            rgb_upscaled = rgb_upscaled.resize((new_w, new_h), Image.LANCZOS)

        # LANCZOS upscale alpha
        if item['has_alpha']:
            alpha_upscaled = item['alpha_channel'].resize((new_w, new_h), Image.LANCZOS)
        else:
            alpha_upscaled = Image.new('L', (new_w, new_h), 255)

        # Combine
        r2, g2, b2 = rgb_upscaled.split()
        combined = Image.merge('RGBA', (r2, g2, b2, alpha_upscaled))

        # Output format
        out_fmt = ImageFormats.DXT5 if item['has_alpha'] else ImageFormats.DXT1

        # Create VTF
        new_vtf = VTF(new_w, new_h, fmt=out_fmt, version=item['orig_version'])
        new_vtf.get(frame=0, mipmap=0).copy_from(combined.tobytes())
        new_vtf.flags = item['orig_flags']

        if hasattr(new_vtf, 'compute_mipmaps'):
            new_vtf.compute_mipmaps()
        if not hasattr(new_vtf, 'hotspot_info'):
            new_vtf.hotspot_info = None
        if not hasattr(new_vtf, 'hotspot_flags'):
            new_vtf.hotspot_flags = 0

        # Atomic write
        tmp_path = str(vtf_path) + '.tmp'
        with open(tmp_path, 'wb') as f:
            new_vtf.save(f)
        os.replace(tmp_path, str(vtf_path))

        size_mb = os.path.getsize(vtf_path) / (1024 * 1024)
        return vtf_path, True, f"{item['orig_w']}x{item['orig_h']} → {new_w}x{new_h} ({size_mb:.1f}MB)"
    except Exception as e:
        return vtf_path, False, str(e)


def main():
    print(f"🔧 Particle/Effect Alpha Fixer (Batch Optimized)")
    print(f"   AI Model: {AI_MODEL}")
    print(f"   Batch Size: {BATCH_SIZE} | Workers: {MAX_WORKERS}")
    print()

    if not REALESRGAN_EXE.exists():
        print(f"ERROR: RealESRGAN not found at {REALESRGAN_EXE}")
        return

    # Collect files
    all_files = []
    for d in EFFECT_DIRS:
        if not d.exists():
            print(f"  ⊖ {d.name}/ — not found, skipping")
            continue
        files = get_vtf_files(d)
        if files:
            print(f"  ✓ {d.name}/ — {len(files)} files")
            all_files.extend(files)
        else:
            print(f"  ⊖ {d.name}/ — no files to fix")

    if not all_files:
        print("\nNo VTF files with .bak backups found")
        return

    print(f"\n🔧 Total: {len(all_files)} VTF files to process\n")
    t0 = time.time()

    # Phase 1: Read all originals in parallel
    print("📖 Phase 1: Reading originals...")
    items = []
    skipped = 0

    def read_one(vtf_path):
        try:
            return vtf_path, read_original(vtf_path), None
        except Exception as e:
            return vtf_path, None, str(e)

    read_errors = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(read_one, f): f for f in all_files}
        for fut in as_completed(futures):
            path, result, err = fut.result()
            if err:
                print(f"   ⚠ {path.name}: {err}")
                read_errors += 1
            elif result is None:
                skipped += 1
            else:
                items.append(result)

    print(f"   {len(items)} to process, {skipped} skipped (already max res)")

    if not items:
        print("\n✅ Nothing to fix!")
        return

    # Phase 2: Batch AI upscale
    n_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\n🤖 Phase 2: AI upscaling in {n_batches} batch(es)...")

    success = 0
    failed = 0

    with tempfile.TemporaryDirectory(prefix="pfx_fix_") as tmp:
        work_dir = Path(tmp)

        for batch_idx in range(n_batches):
            start = batch_idx * BATCH_SIZE
            end = min(start + BATCH_SIZE, len(items))
            batch = items[start:end]

            print(f"\n📦 Batch {batch_idx+1}/{n_batches} ({len(batch)} files)")

            in_dir = work_dir / f"in_{batch_idx}"
            out_dir = work_dir / f"out_{batch_idx}"

            bt = time.time()
            outputs = batch_ai_upscale(batch, in_dir, out_dir)
            ai_time = time.time() - bt
            print(f"   GPU done in {ai_time:.1f}s")

            # Phase 3: Finalize VTFs in parallel
            print(f"   Writing VTFs...")
            wt = time.time()

            finalize_args = []
            for idx, item in enumerate(batch):
                rgb = outputs.get(idx)
                if rgb is not None:
                    finalize_args.append((item, rgb))
                else:
                    print(f"   ✗ {item['vtf_path'].name}: AI output missing")
                    failed += 1

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = [ex.submit(finalize_vtf, item, rgb) for item, rgb in finalize_args]
                for fut in as_completed(futures):
                    path, ok, msg = fut.result()
                    if ok:
                        success += 1
                        print(f"   ✅ {path.name}: {msg}")
                    else:
                        failed += 1
                        print(f"   ✗ {path.name}: {msg}")

            write_time = time.time() - wt
            print(f"   VTFs written in {write_time:.1f}s")

            # Cleanup batch temp files
            shutil.rmtree(in_dir, ignore_errors=True)
            shutil.rmtree(out_dir, ignore_errors=True)

    elapsed = time.time() - t0
    print(f"\n✅ Done: {success} fixed, {skipped} skipped, {failed} failed")
    print(f"⏱  Total time: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    if success > 0:
        print(f"   Average: {elapsed/success:.1f}s per file")


if __name__ == '__main__':
    main()
