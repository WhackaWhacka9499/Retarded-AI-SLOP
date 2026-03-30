"""Quick end-to-end test: read .bak → upscale → save DXT5 VTF."""
import sys, struct, ctypes, os, subprocess, tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, r'C:\Users\Alexander Jarvis\Desktop\Upscaler')
from upscale_particles import init_vtflib, save_vtf_dxt, read_vtf_pixels, has_alpha

CONTENT_MAT = Path(r'G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Content\materials')
test_vtf = CONTENT_MAT / 'effects' / 'beam001.vtf'
test_bak = Path(str(test_vtf) + '.bak')

if not test_bak.exists():
    print(f'No .bak for {test_vtf}')
    sys.exit(1)

print(f'Testing with {test_vtf.name}')
img, flags, ver = read_vtf_pixels(test_bak)
print(f'  Read OK: {img.size[0]}x{img.size[1]}, flags={flags}, version={ver}')

# Upscale with RealESRGAN
tmp = Path(tempfile.mkdtemp())
img.save(tmp / 'test.png')
exe = r'C:\Users\Alexander Jarvis\Desktop\Upscaler\realesrgan\realesrgan-ncnn-vulkan.exe'
r = subprocess.run([exe, '-i', str(tmp / 'test.png'), '-o', str(tmp / 'out.png'),
    '-s', '4', '-n', 'realesrgan-x4plus', '-g', '0', '-f', 'png'], capture_output=True)
print(f'  Upscale return code: {r.returncode}')

up = Image.open(tmp / 'out.png').convert('RGBA')
print(f'  Upscaled: {up.size[0]}x{up.size[1]}')

# Save as DXT5
vtflib = init_vtflib()
alpha = has_alpha(up)
out_path = str(tmp / 'test_dxt5.vtf')
ok, msg = save_vtf_dxt(vtflib, up.tobytes(), up.size[0], up.size[1], out_path, flags, alpha)
print(f'  save_vtf_dxt: ok={ok}, msg={msg}')

if ok:
    sz = os.path.getsize(out_path) // 1024
    rgba_sz = up.size[0] * up.size[1] * 4 // 1024
    with open(out_path, 'rb') as f:
        hdr = f.read(40)
        mips = struct.unpack_from('B', hdr, 28)[0]
    print(f'  Output: {sz}KB (vs {rgba_sz}KB RGBA8888), mipmaps={mips}')
    compressed = sz < rgba_sz
    print(f'  DXT5 compression: {"YES" if compressed else "NO"}')

vtflib.vlShutdown()
print('Done!')
