"""
Test VTFLib.dll with proper ctypes prototypes for creating DXT5 VTFs.
"""
import ctypes
import ctypes.wintypes
import os, struct
from pathlib import Path
from PIL import Image

DLL_PATH = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\VTFLib\x64\VTFLib.dll"

# Load DLL
vtf = ctypes.cdll.LoadLibrary(DLL_PATH)

# Define function prototypes
vtf.vlInitialize.restype = ctypes.c_bool
vtf.vlShutdown.restype = None

vtf.vlCreateImage.argtypes = [ctypes.POINTER(ctypes.c_uint)]
vtf.vlCreateImage.restype = ctypes.c_bool

vtf.vlBindImage.argtypes = [ctypes.c_uint]
vtf.vlBindImage.restype = ctypes.c_bool

vtf.vlDeleteImage.argtypes = [ctypes.c_uint]
vtf.vlDeleteImage.restype = None

# SVTFCreateOptions structure
class SVTFCreateOptions(ctypes.Structure):
    _fields_ = [
        ("uiVersion0", ctypes.c_uint),      # Version major
        ("uiVersion1", ctypes.c_uint),      # Version minor
        ("ImageFormat", ctypes.c_int),       # Output format
        ("uiFlags", ctypes.c_uint),          # VTF flags
        ("uiStartFrame", ctypes.c_uint),
        ("sBumpScale", ctypes.c_float),
        ("bMipmaps", ctypes.c_bool),         # Generate mipmaps
        ("MipmapFilter", ctypes.c_int),      # Mipmap filter
        ("MipmapSharpenFilter", ctypes.c_int),
        ("bThumbnail", ctypes.c_bool),       # Generate thumbnail
        ("bReflectivity", ctypes.c_bool),
        ("bResize", ctypes.c_bool),
        ("ResizeMethod", ctypes.c_int),
        ("ResizeFilter", ctypes.c_int),
        ("ResizeSharpenFilter", ctypes.c_int),
        ("uiResizeWidth", ctypes.c_uint),
        ("uiResizeHeight", ctypes.c_uint),
        ("bResizeClamp", ctypes.c_bool),
        ("uiResizeClampWidth", ctypes.c_uint),
        ("uiResizeClampHeight", ctypes.c_uint),
        ("bGammaCorrection", ctypes.c_bool),
        ("sGammaCorrection", ctypes.c_float),
        ("bNormalMap", ctypes.c_bool),
        ("KernelFilter", ctypes.c_int),
        ("HeightConversionMethod", ctypes.c_int),
        ("NormalAlphaResult", ctypes.c_int),
        ("bNormalMinimumZ", ctypes.c_uint),
        ("sNormalScale", ctypes.c_float),
        ("bNormalWrap", ctypes.c_bool),
        ("bNormalInvertX", ctypes.c_bool),
        ("bNormalInvertY", ctypes.c_bool),
        ("bNormalInvertZ", ctypes.c_bool),
        ("bSphereMap", ctypes.c_bool),       # Generate sphere map (v7.1+)
    ]

vtf.vlImageCreateDefaultCreateStructure.argtypes = [ctypes.POINTER(SVTFCreateOptions)]
vtf.vlImageCreateDefaultCreateStructure.restype = None

vtf.vlImageCreateSingle.argtypes = [
    ctypes.c_uint,      # width
    ctypes.c_uint,      # height  
    ctypes.c_char_p,    # RGBA data
    ctypes.POINTER(SVTFCreateOptions)  # options
]
vtf.vlImageCreateSingle.restype = ctypes.c_bool

vtf.vlImageSave.argtypes = [ctypes.c_char_p]
vtf.vlImageSave.restype = ctypes.c_bool

vtf.vlGetLastError.restype = ctypes.c_char_p

vtf.vlImageGetWidth.restype = ctypes.c_uint
vtf.vlImageGetHeight.restype = ctypes.c_uint
vtf.vlImageGetFormat.restype = ctypes.c_int
vtf.vlImageGetMipmapCount.restype = ctypes.c_uint

# IMAGE_FORMAT constants
IMAGE_FORMAT_DXT1 = 13
IMAGE_FORMAT_DXT5 = 15
IMAGE_FORMAT_RGBA8888 = 0

# Mipmap filter constants
MIPMAP_FILTER_BOX = 0

# Initialize
print("Initializing VTFLib...")
ok = vtf.vlInitialize()
print(f"  vlInitialize: {ok}")

# Create image handle
handle = ctypes.c_uint(0)
ok = vtf.vlCreateImage(ctypes.byref(handle))
print(f"  vlCreateImage: {ok}, handle={handle.value}")

ok = vtf.vlBindImage(handle)
print(f"  vlBindImage: {ok}")

# Create default options
opts = SVTFCreateOptions()
vtf.vlImageCreateDefaultCreateStructure(ctypes.byref(opts))
print(f"\n  Default options:")
print(f"    Version: {opts.uiVersion0}.{opts.uiVersion1}")
print(f"    Format: {opts.ImageFormat}")
print(f"    Mipmaps: {opts.bMipmaps}")
print(f"    Thumbnail: {opts.bThumbnail}")

# Set our desired options
opts.ImageFormat = IMAGE_FORMAT_DXT5
opts.bMipmaps = True
opts.MipmapFilter = MIPMAP_FILTER_BOX
opts.bThumbnail = True
opts.bReflectivity = True
opts.uiVersion0 = 7
opts.uiVersion1 = 2

print(f"\n  Custom options: DXT5, mipmaps=True, v7.2")

# Create test image
w, h = 256, 256
img = Image.new('RGBA', (w, h), (255, 128, 0, 200))
# Add some variation
from PIL import ImageDraw
draw = ImageDraw.Draw(img)
draw.ellipse([50,50,200,200], fill=(255,255,0,128))
rgba_data = img.tobytes()

# Create VTF
ok = vtf.vlImageCreateSingle(w, h, rgba_data, ctypes.byref(opts))
print(f"\n  vlImageCreateSingle({w}x{h}): {ok}")

if ok:
    # Check result
    fw = vtf.vlImageGetWidth()
    fh = vtf.vlImageGetHeight()
    ffmt = vtf.vlImageGetFormat()
    fmips = vtf.vlImageGetMipmapCount()
    print(f"  Result: {fw}x{fh}, format={ffmt}, mipmaps={fmips}")

    # Save
    out_path = r"C:\Users\Alexander Jarvis\Desktop\Upscaler\test_dxt5.vtf"
    ok = vtf.vlImageSave(out_path.encode('ascii'))
    print(f"  vlImageSave: {ok}")
    
    if ok and os.path.exists(out_path):
        sz = os.path.getsize(out_path)
        with open(out_path, 'rb') as f:
            hdr = f.read(40)
            fmt_id = struct.unpack_from('<I', hdr, 36)[0]
            mips = struct.unpack_from('B', hdr, 28)[0]
            ow = struct.unpack_from('<H', hdr, 16)[0]
            oh = struct.unpack_from('<H', hdr, 18)[0]
        print(f"  Saved: {sz} bytes ({sz//1024}KB)")
        print(f"  Header: {ow}x{oh}, format_id={fmt_id}, mipmaps={mips}")
        
        # Compare: RGBA8888 would be 256*256*4 = 262144 bytes
        # DXT5 should be 256*256*1 = 65536 bytes + mipmaps + header ≈ 90KB
        expected_rgba = w * h * 4
        print(f"  RGBA8888 would be: {expected_rgba//1024}KB")
        print(f"  Actual: {sz//1024}KB — {'✅ COMPRESSED!' if sz < expected_rgba else '❌ not compressed'}")
    elif not ok:
        print(f"  Save error: {vtf.vlGetLastError()}")
else:
    err = vtf.vlGetLastError()
    print(f"  Error: {err}")

vtf.vlDeleteImage(handle)
vtf.vlShutdown()
print("\nDone!")
