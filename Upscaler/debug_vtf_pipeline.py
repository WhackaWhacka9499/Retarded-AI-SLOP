"""
VTF Pipeline Diagnostic Script
Tests the full upscale pipeline on a single file to find where black areas are introduced.
"""
import os, sys, tempfile, shutil, time
import numpy as np
from PIL import Image
from srctools.vtf import VTF, ImageFormats

TEST_FILE = r'G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\sups_content\materials\models\cac\32nd\cdr\enlisted.vtf'
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

def analyze_image(img, label):
    """Analyze an image for black regions."""
    arr = np.array(img.convert('RGB'), dtype=np.float32)
    h, w = arr.shape[:2]
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Size: {img.size}, Mode: {img.mode}")
    print(f"  Mean: {arr.mean():.1f}, Std: {arr.std():.1f}")
    
    # Check quadrants
    mid_h, mid_w = h // 2, w // 2
    regions = {
        'Top-left':     arr[:mid_h, :mid_w],
        'Top-right':    arr[:mid_h, mid_w:],
        'Bottom-left':  arr[mid_h:, :mid_w],
        'Bottom-right': arr[mid_h:, mid_w:],
    }
    for name, region in regions.items():
        m = region.mean()
        black_pct = (region.mean(axis=2) < 5).sum() / (region.shape[0] * region.shape[1]) * 100
        print(f"  {name}: mean={m:.1f}, {black_pct:.1f}% black pixels")
    
    # Check alpha if RGBA
    if img.mode == 'RGBA':
        alpha = np.array(img.split()[3], dtype=np.float32)
        print(f"\n  Alpha: min={alpha.min():.0f}, max={alpha.max():.0f}, mean={alpha.mean():.1f}")
        low_alpha_pct = (alpha < 128).sum() / alpha.size * 100
        zero_alpha_pct = (alpha == 0).sum() / alpha.size * 100
        print(f"  Alpha<128: {low_alpha_pct:.1f}%, Alpha==0: {zero_alpha_pct:.1f}%")
        
        # Check if RGB is black WHERE alpha is low
        rgb = np.array(img.convert('RGB'), dtype=np.float32)
        alpha_mask = alpha < 128
        if alpha_mask.any():
            black_under_alpha = (rgb[alpha_mask].mean(axis=1) < 5).sum()
            total_alpha = alpha_mask.sum()
            print(f"  Black pixels under alpha<128: {black_under_alpha}/{total_alpha} ({black_under_alpha/total_alpha*100:.1f}%)")
    print(f"{'='*60}")

def main():
    if not os.path.exists(TEST_FILE):
        print(f"File not found: {TEST_FILE}")
        return
    
    print("="*60)
    print("  VTF PIPELINE DIAGNOSTIC")
    print("="*60)
    
    # Step 1: Load VTF
    print("\n[STEP 1] Loading VTF...")
    with open(TEST_FILE, 'rb') as f:
        vtf = VTF.read(f)
        vtf.load()
        frame = vtf.get(frame=0, mipmap=0)
        original = frame.to_PIL()
    
    print(f"  VTF: {vtf.width}x{vtf.height}, Format: {vtf.format}")
    analyze_image(original, "ORIGINAL (from VTF)")
    original.save(os.path.join(OUT_DIR, 'diag_01_original.png'))
    
    # Step 2: Convert to RGB BMP (same as extract_to_bmp)
    print("\n[STEP 2] Converting RGBA to RGB (extract_to_bmp)...")
    has_alpha = original.mode == 'RGBA'
    if has_alpha:
        alpha_channel = original.split()[3]
        rgb_img = original.convert('RGB')
    else:
        alpha_channel = None
        rgb_img = original.convert('RGB')
    
    analyze_image(rgb_img, "RGB BMP (after stripping alpha)")
    rgb_img.save(os.path.join(OUT_DIR, 'diag_02_rgb_bmp.png'))
    
    if alpha_channel:
        alpha_img = alpha_channel.convert('RGB')  # For visualization
        analyze_image(Image.merge('RGB', [alpha_channel, alpha_channel, alpha_channel]), "ALPHA CHANNEL")
        alpha_channel.save(os.path.join(OUT_DIR, 'diag_02b_alpha.png'))
    
    # Step 3: Save as BMP and run through AI
    print("\n[STEP 3] Running through RealESRGAN...")
    
    # Find the exe
    script_dir = os.path.dirname(os.path.abspath(__file__))
    exe = os.path.join(script_dir, 'realesrgan', 'realesrgan-ncnn-vulkan.exe')
    if not os.path.exists(exe):
        print(f"  RealESRGAN not found: {exe}")
        return
    
    with tempfile.TemporaryDirectory() as tmpdir:
        input_bmp = os.path.join(tmpdir, 'input.bmp')
        output_path = os.path.join(tmpdir, 'output.webp')
        
        rgb_img.save(input_bmp)
        print(f"  Input BMP saved: {os.path.getsize(input_bmp)} bytes")
        
        import subprocess
        cmd = [
            exe,
            '-i', input_bmp,
            '-o', output_path,
            '-n', 'realesrgan-x4plus',
            '-s', '4',
            '-f', 'webp',
        ]
        print(f"  Running: {' '.join(cmd[:6])}...")
        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, timeout=300,
                               cwd=os.path.dirname(exe))
        elapsed = time.time() - t0
        print(f"  Exit code: {result.returncode}, Time: {elapsed:.1f}s")
        
        if result.stderr:
            print(f"  stderr: {result.stderr.decode('utf-8', errors='replace')[:500]}")
        
        # Find output
        found = None
        for ext in ['webp', 'png', 'jpg', 'bmp']:
            candidate = os.path.join(tmpdir, f'output.{ext}')
            if os.path.exists(candidate) and os.path.getsize(candidate) > 512:
                found = candidate
                break
        if not found:
            for f in os.listdir(tmpdir):
                fp = os.path.join(tmpdir, f)
                if os.path.isfile(fp) and os.path.getsize(fp) > 512 and f != 'input.bmp':
                    found = fp
                    break
        
        if not found:
            print(f"  ERROR: No output found! Files: {os.listdir(tmpdir)}")
            return
        
        print(f"  Output found: {found} ({os.path.getsize(found)} bytes)")
        
        with Image.open(found) as tmp:
            ai_output = tmp.copy()
        
        analyze_image(ai_output, "AI OUTPUT (from RealESRGAN)")
        ai_output.save(os.path.join(OUT_DIR, 'diag_03_ai_output.png'))
        
        # Step 4: Resize to target
        print("\n[STEP 4] Resizing to target dimensions...")
        target = 4096
        orig_w, orig_h = vtf.width, vtf.height
        # Same logic as calc_target_dims
        if orig_w >= orig_h:
            new_w = target
            raw_h = int(orig_h * (target / orig_w))
            new_h = 1
            while new_h < raw_h:
                new_h *= 2
            new_h = min(new_h, target)
        else:
            new_h = target
            raw_w = int(orig_w * (target / orig_h))
            new_w = 1
            while new_w < raw_w:
                new_w *= 2
            new_w = min(new_w, target)
        
        print(f"  Target dims: {orig_w}x{orig_h} -> {new_w}x{new_h}")
        resized = ai_output.resize((new_w, new_h), Image.Resampling.LANCZOS)
        analyze_image(resized, "RESIZED (to target)")
        resized.save(os.path.join(OUT_DIR, 'diag_04_resized.png'))
        
        # Step 5: Restore alpha if applicable
        if has_alpha and alpha_channel:
            print("\n[STEP 5] Restoring alpha...")
            alpha_resized = alpha_channel.resize((new_w, new_h), Image.Resampling.LANCZOS)
            resized = resized.convert('RGBA')
            resized.putalpha(alpha_resized)
            analyze_image(resized, "ALPHA RESTORED")
            resized.save(os.path.join(OUT_DIR, 'diag_05_alpha_restored.png'))
        
        # Step 6: Write VTF
        print("\n[STEP 6] Assembling VTF...")
        if resized.mode != 'RGBA':
            resized = resized.convert('RGBA')
        
        out_vtf = VTF(new_w, new_h, fmt=ImageFormats.DXT5, version=vtf.version)
        out_vtf.flags = vtf.flags
        out_vtf.reflectivity = vtf.reflectivity
        out_vtf.get(frame=0, mipmap=0).copy_from(resized.tobytes())
        out_vtf.compute_mipmaps()
        
        test_output = os.path.join(OUT_DIR, 'diag_06_output.vtf')
        with open(test_output, 'wb') as f:
            out_vtf.save(f)
        print(f"  VTF saved: {os.path.getsize(test_output)} bytes")
        
        # Step 7: Read back the VTF and verify
        print("\n[STEP 7] Verifying written VTF...")
        with open(test_output, 'rb') as f:
            vtf_check = VTF.read(f)
            vtf_check.load()
            readback = vtf_check.get(frame=0, mipmap=0).to_PIL()
        analyze_image(readback, "VTF READBACK (verification)")
        readback.save(os.path.join(OUT_DIR, 'diag_07_readback.png'))
    
    print("\n" + "="*60)
    print("  DIAGNOSTIC COMPLETE!")
    print(f"  Check diag_*.png files in: {OUT_DIR}")
    print("="*60)

if __name__ == '__main__':
    main()
