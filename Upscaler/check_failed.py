import json, os
from srctools.vtf import VTF

cache_path = r'G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials\vtf_scan_cache.json'
with open(cache_path) as f:
    data = json.load(f)

# Check a sample of the "AI output missing or corrupted" type files
targets = ['slave1decal', 'slave1glass', 'slave1glass2', 'darkmask', 'fexp', 'visor']
found = []
for fp, info in data.get('files', {}).items():
    bn = os.path.basename(fp).lower().replace('.vtf', '')
    for t in targets:
        if bn == t.lower():
            try:
                sz = os.path.getsize(fp)
                with open(fp, 'rb') as f:
                    vtf = VTF.read(f)
                w, h = vtf.width, vtf.height
                fmt = str(vtf.format)
                print(f"  {os.path.basename(fp)}: {w}x{h} {fmt} ({sz} bytes)")
            except Exception as e:
                print(f"  {os.path.basename(fp)}: READ ERROR - {e} ({os.path.getsize(fp)} bytes)")
            found.append(fp)
            break

print(f"\nTotal found: {len(found)}")
