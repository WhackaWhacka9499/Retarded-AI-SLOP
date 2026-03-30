#!/usr/bin/env python3
"""
Benchmark script for RealESRGAN upscaler pipeline.
Tests: binary inference speed, I/O overhead, batch throughput.
"""
import os, sys, time, subprocess, tempfile, shutil
from pathlib import Path
from PIL import Image
import numpy as np

BINARY = Path(r"c:\Users\Alexander Jarvis\Desktop\Upscaler\realesrgan\realesrgan-ncnn-vulkan.exe")
MODEL_DIR = BINARY.parent / "models"
MODEL = "realesrgan-x4plus"
SCRATCH = r"G:\_vtf_scratch\_benchmark"

def generate_test_image(w, h, filepath):
    """Generate a random test image."""
    arr = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    Image.fromarray(arr, 'RGB').save(filepath)

def bench_single(w, h, tile, label=""):
    """Benchmark a single image upscale."""
    os.makedirs(SCRATCH, exist_ok=True)
    inp = os.path.join(SCRATCH, "bench_input.png")
    out = os.path.join(SCRATCH, "bench_output.png")
    
    generate_test_image(w, h, inp)
    
    cmd = [
        str(BINARY), "-i", inp, "-o", out,
        "-n", MODEL, "-s", "4", "-t", str(tile),
        "-g", "0", "-f", "png",
        "-m", str(MODEL_DIR),
    ]
    
    # Warm-up run (loads model into VRAM)
    subprocess.run(cmd, capture_output=True, timeout=120,
                   cwd=str(BINARY.parent), creationflags=0x08000000)
    
    # Timed runs
    times = []
    for _ in range(3):
        if os.path.exists(out):
            os.remove(out)
        t0 = time.perf_counter()
        result = subprocess.run(cmd, capture_output=True, timeout=120,
                               cwd=str(BINARY.parent), creationflags=0x08000000)
        t1 = time.perf_counter()
        elapsed = t1 - t0
        times.append(elapsed)
        
        if result.returncode != 0:
            stderr = result.stderr.decode('utf-8', errors='replace')[:200]
            print(f"  !! FAILED: {stderr}")
            return None
    
    avg = sum(times) / len(times)
    best = min(times)
    pixels = w * h
    mpx = pixels / 1_000_000
    mpx_per_sec = mpx / avg
    
    print(f"  {label or f'{w}x{h}':<20} tile={tile:<6} avg={avg:.2f}s  best={best:.2f}s  {mpx_per_sec:.2f} MP/s  ({mpx:.2f} MP)")
    
    # Cleanup
    for f in [inp, out]:
        if os.path.exists(f):
            os.remove(f)
    
    return {"w": w, "h": h, "tile": tile, "avg": avg, "best": best, "mpx_per_sec": mpx_per_sec}

def bench_batch(count, w, h, tile):
    """Benchmark batch processing of multiple images."""
    batch_in = os.path.join(SCRATCH, "batch_in")
    batch_out = os.path.join(SCRATCH, "batch_out")
    os.makedirs(batch_in, exist_ok=True)
    os.makedirs(batch_out, exist_ok=True)
    
    # Generate test images
    for i in range(count):
        generate_test_image(w, h, os.path.join(batch_in, f"img_{i:03d}.png"))
    
    cmd = [
        str(BINARY), "-i", batch_in, "-o", batch_out,
        "-n", MODEL, "-s", "4", "-t", str(tile),
        "-g", "0", "-f", "png", "-v",
        "-m", str(MODEL_DIR),
        "-j", "2:2:2",
    ]
    
    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, timeout=600,
                           cwd=str(BINARY.parent), creationflags=0x08000000)
    t1 = time.perf_counter()
    elapsed = t1 - t0
    
    if result.returncode != 0:
        stderr = result.stderr.decode('utf-8', errors='replace')[:300]
        print(f"  !! BATCH FAILED: {stderr}")
        return None
    
    # Count successful outputs
    outputs = [f for f in os.listdir(batch_out) if os.path.getsize(os.path.join(batch_out, f)) > 512]
    
    per_image = elapsed / max(len(outputs), 1)
    total_mpx = (w * h * len(outputs)) / 1_000_000
    mpx_per_sec = total_mpx / elapsed
    
    print(f"  Batch {count}x {w}x{h}  tile={tile}  total={elapsed:.2f}s  per_img={per_image:.2f}s  "
          f"{mpx_per_sec:.2f} MP/s  outputs={len(outputs)}/{count}")
    
    # Cleanup
    shutil.rmtree(batch_in, ignore_errors=True)
    shutil.rmtree(batch_out, ignore_errors=True)
    
    return {"count": count, "elapsed": elapsed, "per_image": per_image, "mpx_per_sec": mpx_per_sec, "outputs": len(outputs)}

def bench_io_overhead():
    """Benchmark raw I/O: PNG encode/decode and BMP encode/decode."""
    print("\n--- I/O Overhead ---")
    arr = np.random.randint(0, 255, (512, 512, 3), dtype=np.uint8)
    img = Image.fromarray(arr, 'RGB')
    
    # PNG write
    t0 = time.perf_counter()
    for _ in range(20):
        fp = os.path.join(SCRATCH, "io_test.png")
        img.save(fp)
    png_write = (time.perf_counter() - t0) / 20
    
    # PNG read
    t0 = time.perf_counter()
    for _ in range(20):
        with Image.open(fp) as tmp:
            tmp.load()
    png_read = (time.perf_counter() - t0) / 20
    
    # BMP write
    t0 = time.perf_counter()
    for _ in range(20):
        fp2 = os.path.join(SCRATCH, "io_test.bmp")
        img.save(fp2)
    bmp_write = (time.perf_counter() - t0) / 20
    
    # BMP read
    t0 = time.perf_counter()
    for _ in range(20):
        with Image.open(fp2) as tmp:
            tmp.load()
    bmp_read = (time.perf_counter() - t0) / 20
    
    print(f"  PNG write: {png_write*1000:.1f}ms   PNG read: {png_read*1000:.1f}ms")
    print(f"  BMP write: {bmp_write*1000:.1f}ms   BMP read: {bmp_read*1000:.1f}ms")
    print(f"  PNG vs BMP write overhead: {png_write/bmp_write:.1f}x slower")
    
    for f in ["io_test.png", "io_test.bmp"]:
        fp = os.path.join(SCRATCH, f)
        if os.path.exists(fp):
            os.remove(fp)

def main():
    os.makedirs(SCRATCH, exist_ok=True)
    
    print(f"=== RealESRGAN Benchmark ===")
    print(f"Binary: {BINARY}")
    print(f"Model: {MODEL}")
    print(f"Scratch: {SCRATCH}")
    print()
    
    # Check binary
    if not BINARY.exists():
        print(f"ERROR: Binary not found at {BINARY}")
        return
    
    # --- Single image, varying sizes ---
    print("--- Single Image (varying sizes, tile=256) ---")
    for w, h in [(128, 128), (256, 256), (512, 512), (1024, 1024)]:
        bench_single(w, h, tile=256)
    
    # --- Tile size comparison at 512x512 ---
    print("\n--- Tile Size Comparison (512x512 image) ---")
    for tile in [128, 256, 384, 512]:
        bench_single(512, 512, tile=tile)
    
    # --- Batch throughput ---
    print("\n--- Batch Throughput (256x256 images) ---")
    for count in [5, 20]:
        bench_batch(count, 256, 256, tile=256)
    
    print("\n--- Batch Throughput (512x512 images) ---")
    bench_batch(10, 512, 512, tile=256)
    
    # --- I/O overhead ---
    bench_io_overhead()
    
    print("\n=== Benchmark Complete ===")
    
    # Cleanup
    try:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    except:
        pass

if __name__ == "__main__":
    main()
