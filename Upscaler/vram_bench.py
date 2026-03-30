"""VRAM benchmark for BATCH processing (realistic workload)."""
import subprocess, threading, time, pynvml, os
from PIL import Image

pynvml.nvmlInit()
h = pynvml.nvmlDeviceGetHandleByIndex(0)
EXE = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\realesrgan\realesrgan-ncnn-vulkan.exe"
MODELS = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\realesrgan\models"

# Create batch of 11 test images (matching real workload sizes)
IN_DIR = r"G:\_vtf_scratch\vram_batch_test\input"
OUT_DIR = r"G:\_vtf_scratch\vram_batch_test\output"
os.makedirs(IN_DIR, exist_ok=True)

# Mix of sizes like the real batch: 1024x1024, 512x512, 256x128
sizes = [(1024,1024), (1024,1024), (512,512), (512,512), (512,512),
         (512,512), (512,512), (512,512), (256,128), (256,128), (512,512)]

for i, (w, h_) in enumerate(sizes):
    Image.new("RGB", (w, h_), (128 + i*10, 64, 32)).save(os.path.join(IN_DIR, f"{i:04d}.bmp"))

print(f"Created {len(sizes)} test images")

total = pynvml.nvmlDeviceGetMemoryInfo(h).total / 1048576
base = pynvml.nvmlDeviceGetMemoryInfo(h).used / 1048576
print(f"BASELINE: {base:.0f}MB / {total:.0f}MB ({base/total*100:.1f}%)")

# Test batch with tile=1024, threads=4:8:8
time.sleep(2)
os.makedirs(OUT_DIR, exist_ok=True)
# Clear output dir
for f in os.listdir(OUT_DIR):
    os.remove(os.path.join(OUT_DIR, f))

peak_val = [0.0]
timeline = []
stop_flag = threading.Event()

def monitor():
    while not stop_flag.is_set():
        used = pynvml.nvmlDeviceGetMemoryInfo(h).used / 1048576
        if used > peak_val[0]:
            peak_val[0] = used
        timeline.append((time.time(), used))
        time.sleep(0.02)

t = threading.Thread(target=monitor, daemon=True)
t.start()

print("\nRunning BATCH: tile=1024 threads=4:8:8 (-v mode)...")
proc = subprocess.run(
    [EXE, "-i", IN_DIR, "-o", OUT_DIR, "-n", "realesrgan-x4plus",
     "-s", "4", "-g", "0", "-t", "1024", "-j", "4:8:8", "-f", "png",
     "-m", MODELS, "-v"],
    capture_output=True, timeout=120
)

stop_flag.set()
t.join(timeout=1)

peak = peak_val[0]
delta = peak - base
pct = peak / total * 100

print(f"\nBATCH RESULT: PEAK={peak:.0f}MB ({pct:.1f}%) DELTA=+{delta:.0f}MB RC={proc.returncode}")

# Check stderr for VRAM errors
stderr = proc.stderr.decode("utf-8", errors="replace")
vram_errors = [l for l in stderr.splitlines() if "vkAllocate" in l or "vkWaitFor" in l]
if vram_errors:
    print(f"\nVRAM ERRORS ({len(vram_errors)}):")
    for e in vram_errors[:5]:
        print(f"  {e}")
else:
    print("\nNo VRAM errors!")

# Timeline summary
if timeline:
    above_90 = sum(1 for _, v in timeline if v/total > 0.90)
    above_80 = sum(1 for _, v in timeline if v/total > 0.80)
    print(f"\nTimeline: {len(timeline)} samples, {above_80} above 80%, {above_90} above 90%")

print("\nDone!")
