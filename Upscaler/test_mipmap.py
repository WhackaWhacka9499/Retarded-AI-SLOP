from srctools.vtf import VTF, ImageFormats
from PIL import Image
import struct, io

w, h = 256, 256
img = Image.new('RGBA', (w, h), (255, 0, 0, 255))

vtf = VTF(w, h, fmt=ImageFormats.DXT5)
attrs = [a for a in dir(vtf) if 'mip' in a.lower()]
print(f"VTF mipmap attrs: {attrs}")
print(f"mipmap_count: {vtf.mipmap_count}")

vtf.get(frame=0, mipmap=0).copy_from(img.tobytes())
print(f"After set mip0: count={vtf.mipmap_count}")

# Try writing mipmap levels
for mip in range(1, 9):
    mw = max(w >> mip, 1)
    mh = max(h >> mip, 1)
    try:
        mip_img = img.resize((mw, mh), Image.LANCZOS)
        vtf.get(frame=0, mipmap=mip).copy_from(mip_img.tobytes())
        print(f"  mip {mip}: {mw}x{mh} OK, count={vtf.mipmap_count}")
    except Exception as e:
        print(f"  mip {mip}: FAILED: {e}")
        break

# Save and inspect
buf = io.BytesIO()
vtf.save(buf)
buf.seek(28)
mips_in_file = struct.unpack('B', buf.read(1))[0]
print(f"\nMipmaps in saved file: {mips_in_file}")
print(f"File size: {len(buf.getvalue())} bytes")

# Try setting mipmap_count directly
try:
    vtf.mipmap_count = 9
    print(f"Set mipmap_count=9: {vtf.mipmap_count}")
except Exception as e:
    print(f"Cannot set mipmap_count: {e}")

# Check if it's a property
print(f"mipmap_count type: {type(type(vtf).__dict__.get('mipmap_count', 'not in dict'))}")
