import os, struct, collections

content = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content"

vtf_count = 0
total_size = 0
res_buckets = collections.Counter()
fmt_buckets = collections.Counter()
dir_stats = {}
too_big = 0
has_bak = 0

FORMAT_NAMES = {
    0: "RGBA8888", 1: "ABGR8888", 2: "RGB888", 3: "BGR888",
    4: "RGB565", 5: "I8", 6: "IA88", 8: "A8",
    11: "ARGB8888", 12: "BGRA8888",
    13: "DXT1", 14: "DXT3", 15: "DXT5",
    16: "BGRX8888", 20: "DXT1_ONEBITALPHA",
    22: "UV88", 24: "RGBA16161616F",
}

for root, dirs, files in os.walk(content):
    for f in files:
        fp = os.path.join(root, f)
        if f.lower().endswith(".bak"):
            has_bak += 1
            continue
        if not f.lower().endswith(".vtf"):
            continue
        vtf_count += 1
        sz = os.path.getsize(fp)
        total_size += sz
        rel_dir = os.path.relpath(root, content)
        parts = rel_dir.split(os.sep)
        top_dir = parts[1] if len(parts) > 1 else parts[0]
        if top_dir not in dir_stats:
            dir_stats[top_dir] = [0, 0]
        dir_stats[top_dir][0] += 1
        dir_stats[top_dir][1] += sz
        try:
            with open(fp, "rb") as vf:
                magic = vf.read(4)
                if magic != b"VTF\x00":
                    continue
                vf.read(8)  # version
                vf.read(4)  # header size
                w, h = struct.unpack("<HH", vf.read(4))
                vf.read(28)  # flags, frames, etc
                vf.read(4)  # bump scale
                fmt_id = struct.unpack("<I", vf.read(4))[0]
                mx = max(w, h)
                if mx >= 4096:
                    too_big += 1
                elif mx >= 2048:
                    res_buckets["2048"] += 1
                elif mx >= 1024:
                    res_buckets["1024"] += 1
                elif mx >= 512:
                    res_buckets["512"] += 1
                elif mx >= 256:
                    res_buckets["256"] += 1
                elif mx >= 128:
                    res_buckets["128"] += 1
                else:
                    res_buckets["<128"] += 1
                fmt_buckets[FORMAT_NAMES.get(fmt_id, "Unk(%d)" % fmt_id)] += 1
        except Exception:
            pass

to_upscale = vtf_count - too_big
print("=" * 50)
print("   CONTENT ADDON FULL SCAN")
print("=" * 50)
print("Total VTFs:       %d" % vtf_count)
print("Total size:       %.1f MB (%.2f GB)" % (total_size / 1048576, total_size / 1073741824))
print("Already >=4096:   %d (would skip)" % too_big)
print("To upscale:       %d" % to_upscale)
print("Existing .bak:    %d" % has_bak)
print()
print("--- Resolution Distribution ---")
for b in ["<128", "128", "256", "512", "1024", "2048"]:
    c = res_buckets.get(b, 0)
    pct = c / vtf_count * 100 if vtf_count else 0
    bar = "#" * int(pct / 2)
    print("  %6spx: %5d (%5.1f%%) %s" % (b, c, pct, bar))
print("  >=4096:  %5d (%5.1f%%) (skip)" % (too_big, too_big / vtf_count * 100 if vtf_count else 0))
print()
print("--- Format Distribution ---")
for fmt, c in fmt_buckets.most_common(10):
    pct = c / vtf_count * 100
    print("  %20s: %5d (%5.1f%%)" % (fmt, c, pct))
print()
print("--- Top 15 Directories ---")
sorted_dirs = sorted(dir_stats.items(), key=lambda x: -x[1][0])
for d, s in sorted_dirs[:15]:
    print("  %30s: %5d VTFs  (%6.1f MB)" % (d, s[0], s[1] / 1048576))

# ETA calculation based on observed logs
# From logs: 29 files in 27 seconds (23:03:55 to 23:04:22) = ~1.07 files/sec GPU
# Assembly: ~0.5 sec/file for DXT compressed
# With DXT fix: assembly should be faster (smaller writes)
gpu_rate = 1.0  # files/sec observed on RTX 4080 Super at tile 1024
assembly_rate = 2.0  # files/sec with cv2 + DXT compression
extract_rate = 20.0  # files/sec CPU parallel
batch_overhead = 8  # seconds per batch (model load + cooldown)
batch_size = 200
num_batches = (to_upscale + batch_size - 1) // batch_size

gpu_time = to_upscale / gpu_rate
extract_time = to_upscale / extract_rate
assembly_time = to_upscale / assembly_rate
overhead_time = num_batches * batch_overhead

total_batched = gpu_time + extract_time + assembly_time + overhead_time
total_bulk = gpu_time + extract_time + assembly_time + batch_overhead  # 1 batch

print()
print("=" * 50)
print("   ETA ESTIMATES (RTX 4080 Super)")
print("=" * 50)
print("GPU processing:   %.0f sec (%.1f min) @ %.1f files/sec" % (gpu_time, gpu_time / 60, gpu_rate))
print("CPU extraction:   %.0f sec" % extract_time)
print("CPU assembly:     %.0f sec (%.1f min)" % (assembly_time, assembly_time / 60))
print("Batch overhead:   %.0f sec (%d batches x %ds)" % (overhead_time, num_batches, batch_overhead))
print()
print("BATCHED approach: ~%.1f min total" % (total_batched / 60))
print("BULK approach:    ~%.1f min total (saves %.0f sec)" % (total_bulk / 60, total_batched - total_bulk))
