import hashlib, os
from pathlib import Path
from collections import defaultdict

SP = Path(r"C:\Users\Alexander Jarvis\Desktop\CWRP Installer\2026 SP Helper")
by_hash = defaultdict(list)

for f in SP.rglob("*"):
    if not f.is_file():
        continue
    sz = f.stat().st_size
    with open(f, "rb") as fh:
        head = fh.read(4096)
    key = (sz, hashlib.md5(head).hexdigest())
    by_hash[key].append(f)

dupes = {k: v for k, v in by_hash.items() if len(v) > 1}
dupe_files = sum(len(v) - 1 for v in dupes.values())
dupe_bytes = sum(k[0] * (len(v) - 1) for k, v in dupes.items())

print(f"Total unique: {len(by_hash)}")
print(f"Duplicate files: {dupe_files} ({dupe_bytes // 1024 // 1024} MB wasted)")

if dupes:
    print(f"\nTop 10 largest duplicate groups:")
    for k, v in sorted(dupes.items(), key=lambda x: -x[0][0] * (len(x[1]) - 1))[:10]:
        sz_kb = k[0] / 1024
        print(f"  {sz_kb:.0f}KB x{len(v)} copies: {v[0].relative_to(SP)}")
        for dup in v[1:3]:
            print(f"    = {dup.relative_to(SP)}")
