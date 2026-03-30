#!/usr/bin/env python3
"""
VTF Upscaler v6.1 - AI-Powered VTF Texture Upscaling (Source-Optimized)
Uses srctools for VTF, RealESRGAN for AI enhancement.
Optimized: batch folder mode, BMP→WebP pipeline, source-tuned GPU params.
"""

import os
import sys
import json
import shutil
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    CTK_AVAILABLE = False
from pathlib import Path
from typing import Optional, List, Tuple
import struct
import glob as globmod
import time
import tempfile
import queue
import numpy as np

# Set high priority for this process
try:
    import psutil
    p = psutil.Process(os.getpid())
    pass  # Process priority left at normal (HIGH_PRIORITY can freeze Windows)
except:
    pass

try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False

try:
    import pynvml
    pynvml.nvmlInit()
    PYNVML_AVAILABLE = True
except Exception:
    PYNVML_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from PIL import Image, ImageFilter, ImageEnhance, ImageTk
    Image.MAX_IMAGE_PIXELS = 300000000
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from srctools.vtf import VTF, ImageFormats
    SRCTOOLS_AVAILABLE = True
    
    # Format ID → human name mapping (from VTF spec)
    FORMAT_ID_MAP = {
        0: "RGBA8888", 1: "ABGR8888", 2: "RGB888", 3: "BGR888",
        4: "RGB565", 5: "I8", 6: "IA88", 8: "A8",
        11: "ARGB8888", 12: "BGRA8888",
        13: "DXT1", 14: "DXT3", 15: "DXT5",
        16: "BGRX8888", 20: "DXT1_ONEBITALPHA",
        22: "UV88", 24: "RGBA16161616F",
    }
    
    # Human name → ImageFormats enum
    VTF_FORMATS = {
        "DXT1": ImageFormats.DXT1,
        "DXT3": ImageFormats.DXT3,
        "DXT5": ImageFormats.DXT5,
        "RGBA8888": ImageFormats.RGBA8888,
        "BGRA8888": ImageFormats.BGRA8888,
        "RGB888": ImageFormats.RGB888,
        "BGR888": ImageFormats.BGR888,
        "DXT1_ONEBITALPHA": ImageFormats.DXT1_ONEBITALPHA,
    }
    
    def get_output_format_for_source(src_fmt_id: int):
        """Map source VTF format ID to the correct output ImageFormats enum.
        DXT1 (no alpha) → DXT1, DXT3/DXT5 (alpha) → DXT5, uncompressed → DXT5."""
        fmt_map = {
            13: ImageFormats.DXT1,           # DXT1 → DXT1
            14: ImageFormats.DXT5,           # DXT3 → DXT5 (better quality)
            15: ImageFormats.DXT5,           # DXT5 → DXT5
            20: ImageFormats.DXT1_ONEBITALPHA,  # DXT1_ONEBITALPHA → same
            0:  ImageFormats.DXT5,           # RGBA8888 → DXT5 (compress it!)
            2:  ImageFormats.DXT1,           # RGB888 → DXT1 (no alpha)
            3:  ImageFormats.DXT1,           # BGR888 → DXT1
            12: ImageFormats.DXT5,           # BGRA8888 → DXT5
        }
        return fmt_map.get(src_fmt_id, ImageFormats.DXT5)  # Default: DXT5
    
except ImportError:
    SRCTOOLS_AVAILABLE = False
    FORMAT_ID_MAP = {}
    VTF_FORMATS = {}
    def get_output_format_for_source(src_fmt_id): return None

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

APP_NAME = "VTF AI Upscaler"
APP_VERSION = "7.0.0"  # Incremental rebuild from clean v6.2 base
CONFIG_FILE = "vtf_upscaler_config.json"
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
PREVIEW_SIZE = 480

ADDON_OUTPUT_PATH = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\Heinzy_Upscaled"

# Original (pre-upscale) source directories for alpha health comparison
ORIGINAL_SOURCE_DIRS = [
    r"C:\Users\Alexander Jarvis\Desktop\Extract Me\battalion5-3864532439\materials",
    r"C:\Users\Alexander Jarvis\Desktop\CWRP Installer\2026 SP Helper\materials",
    r"C:\Users\Alexander Jarvis\Desktop\Extract Me\battalion2-2582938939\materials",
    r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\maps\rp_liberator_extracted\materials",
]

SCRIPT_DIR = Path(__file__).parent if '__file__' in dir() else Path('.')
REALESRGAN_EXE = SCRIPT_DIR / "realesrgan" / "realesrgan-ncnn-vulkan.exe"

# Scratch space: RAM disk (fastest) > G: drive > system temp
SCRATCH_DRIVE = r"G:\_vtf_scratch"
RAM_DISK_LETTER = "R:"          # Drive letter for RAM disk
RAM_DISK_SIZE_MB = 5120         # 5 GB — room for large batch inputs
_ram_disk_ready = False

_ram_disk_failed = False  # Prevent noisy retries

def _create_ram_disk():
    """Try to create a RAM disk via ImDisk. Returns True on success."""
    global _ram_disk_ready, _ram_disk_failed
    if _ram_disk_ready:
        return True
    if _ram_disk_failed:
        return False  # Already failed — don't retry (prevents ImDisk spam)
    # Already mounted?
    if os.path.isdir(f"{RAM_DISK_LETTER}\\"):
        _ram_disk_ready = True
        return True
    try:
        import shutil
        imdisk = shutil.which("imdisk")
        if not imdisk:
            return False
        # Create RAM disk: NTFS, quick format, no drive letter prompt
        ret = subprocess.run(
            [imdisk, "-a", "-s", f"{RAM_DISK_SIZE_MB}M",
             "-m", RAM_DISK_LETTER, "-p", "/fs:ntfs /q /y"],
            capture_output=True, timeout=15,
            creationflags=0x08000000  # CREATE_NO_WINDOW
        )
        if ret.returncode == 0 and os.path.isdir(f"{RAM_DISK_LETTER}\\"):
            _ram_disk_ready = True
            print(f"[Scratch] RAM disk created: {RAM_DISK_LETTER}\\ ({RAM_DISK_SIZE_MB}MB)", file=sys.stderr)
            return True
        else:
            print(f"[Scratch] ImDisk returned {ret.returncode}: {ret.stderr.decode(errors='replace')}", file=sys.stderr)
    except Exception as e:
        print(f"[Scratch] RAM disk creation failed: {e}", file=sys.stderr)
    _ram_disk_failed = True
    return False

def get_scratch_dir():
    """Return scratch directory — G: drive > system temp. (RAM disk disabled for stability)"""
    # RAM disk DISABLED — ImDisk allocating 5GB can crash Windows when RAM is low
    # Priority 1: G: drive scratch
    try:
        os.makedirs(SCRATCH_DRIVE, exist_ok=True)
        return SCRATCH_DRIVE
    except Exception:
        pass
    # Priority 2: System temp
    return tempfile.gettempdir()

CACHE_FILE = "vtf_scan_cache.json"  # Per-folder scan cache
HISTORY_FILE = "vtf_process_history.json"  # Per-folder crash-resume tracking

DEFAULT_CONFIG = {
    "target_resolution": 4096,
    "upscale_method": "ai",
    "output_format": "Auto (Match Source)",
    "sharpen_strength": 0.0,
    "generate_mipmaps": True,
    "backup_originals": True,
    "recursive_search": True,
    "ai_scale": 4,
    "ai_model": "realesrgan-x4plus",
    "parallel_workers": 4,
    # gpu_threads: now auto-calculated in _build_cmd based on free VRAM
    "tile_size": 0,  # 0 = auto (binary selects based on VRAM)
    "gpu_id": 0,
    "tta_mode": False,
    "skip_small": 64,
    "denoise_strength": 0,
    "last_folder": "",
    "output_to_addon": False,
    # Performance tuning
    "batch_size": 100,  # Images per RealESRGAN call (auto-adjusted for texture size)
    "cpu_workers": 8,  # CPU threads for VTF load/save (matched to Ryzen 7 cores)
}

VTF_FORMATS = {
    "DXT1": ImageFormats.DXT1 if SRCTOOLS_AVAILABLE else None,
    "DXT5": ImageFormats.DXT5 if SRCTOOLS_AVAILABLE else None,
    "BGRA8888": ImageFormats.BGRA8888 if SRCTOOLS_AVAILABLE else None,
}

# Map VTF format IDs to srctools ImageFormats for format-aware output
FORMAT_ID_MAP = {
    0: "RGBA8888", 3: "BGR888", 12: "BGRA8888", 13: "DXT1", 14: "DXT3", 15: "DXT5",
}

def get_output_format_for_source(fmt_id: int):
    """Return the best srctools ImageFormat to match the source VTF format."""
    if not SRCTOOLS_AVAILABLE:
        return None
    mapping = {
        0: ImageFormats.RGBA8888,   # RGBA8888
        3: ImageFormats.BGR888,     # BGR888
        12: ImageFormats.BGRA8888,  # BGRA8888
        13: ImageFormats.DXT1,      # DXT1
        14: ImageFormats.DXT5,      # DXT3 → DXT5 (srctools doesn't support DXT3 write)
        15: ImageFormats.DXT5,      # DXT5
    }
    return mapping.get(fmt_id)


def next_power_of_2(n: int) -> int:
    if n <= 0:
        return 1
    n -= 1
    n |= n >> 1
    n |= n >> 2
    n |= n >> 4
    n |= n >> 8
    n |= n >> 16
    return n + 1


SKIP_TEXTURE_PATTERNS = [
    '_normal', '_n.', '_bump', '_b.', '_spec', '_s.',
    '_gloss', '_g.', '_ao', '_ambient', '_height', '_h.',
    '_mask', '_m.', '_detail', '_env', '_cube', '_dudv',
    '_nrm', '_nm', '_norm',  # Additional normal map patterns
]

def is_problematic_texture(filename: str) -> str:
    import re
    name_lower = filename.lower()
    
    # Automatically skip built-in map cubemaps (e.g. c8857_2121_1389.vtf)
    if re.match(r"^c-?\d+_-?\d+_-?\d+", name_lower) or "cubemap" in name_lower:
        return "cubemap"
        
    for pattern in SKIP_TEXTURE_PATTERNS:
        if pattern in name_lower:
            if 'normal' in pattern or pattern == '_n.' or 'nrm' in pattern or pattern == '_nm' or 'norm' in pattern: return "normal map"
            elif 'bump' in pattern or pattern == '_b.': return "bump map"
            elif 'spec' in pattern or pattern == '_s.': return "specular"
            elif 'gloss' in pattern or pattern == '_g.': return "gloss map"
            elif 'mask' in pattern or pattern == '_m.': return "mask texture"
            elif 'detail' in pattern: return "detail texture"
            elif 'env' in pattern or 'cube' in pattern: return "environment map"
            else: return "special texture"
    return ""

# ── Texture Classification ───────────────────────────────────────────
# 3-tier heuristic: path match (instant) → VMT shader (if companion exists) → content analysis
# Returns: 'ai' (full AI upscale), 'lanczos' (LANCZOS only), or 'skip'

# Path patterns that indicate gradient mask textures
# These should NOT be AI-upscaled — AI adds texture to smooth gradients
# Particle/effect/sprite textures are now handled per-frame by AI
LANCZOS_PATH_PATTERNS = [
    'lightsaber/',      # wOS beam materials — gradient masks
    'wos/lightsabers/blades/', # wOS blade textures — gradient masks
    'wos/blades/',      # wOS blade alternates (anzati, etc.)
]

# VMT shader types that indicate effect textures
# NOTE: No longer used for LANCZOS classification since per-frame AI handles these
LANCZOS_VMT_SHADERS = set()

# VMT properties (disabled — per-frame AI handles particle/effect textures)
LANCZOS_VMT_PROPERTIES = []

def classify_texture(filepath: str) -> str:
    """Classify a VTF texture for upscale method selection.
    
    Returns:
        'ai'      - Use AI upscaling (detailed texture with complex patterns)
        'lanczos' - Use LANCZOS only (gradient mask, particle sprite, effect)
        'skip'    - Should be skipped entirely
    """
    # Check if already flagged as problematic (normals, bumps, etc.)
    if is_problematic_texture(os.path.basename(filepath)):
        return 'skip'
    
    # --- TIER 1: Path-based classification (instant, most reliable) ---
    normalized = filepath.replace('\\', '/').lower()
    # Extract the path under materials/ for pattern matching
    mat_idx = normalized.find('materials/')
    rel_path = normalized[mat_idx + 10:] if mat_idx >= 0 else normalized
    
    for pattern in LANCZOS_PATH_PATTERNS:
        if pattern in rel_path:
            return 'lanczos'
    
    # --- TIER 2: VMT companion analysis ---
    # Look for a .vmt file next to the .vtf
    vmt_path = filepath.rsplit('.', 1)[0] + '.vmt'
    if os.path.exists(vmt_path):
        try:
            with open(vmt_path, 'r', errors='ignore') as f:
                vmt_content = f.read(2048)  # First 2KB is enough
            vmt_lower = vmt_content.lower()
            
            # Check shader name (first non-empty line)
            first_line = vmt_content.strip().split('\n')[0].strip().strip('"').lower()
            if first_line in LANCZOS_VMT_SHADERS:
                return 'lanczos'
            
            # Check for effect-indicating properties
            for prop in LANCZOS_VMT_PROPERTIES:
                if prop in vmt_lower:
                    return 'lanczos'
        except (OSError, IOError):
            pass
    
    # --- TIER 3: Content-based heuristic (fallback) ---
    # Only run if we can read the image cheaply
    if CV2_AVAILABLE:
        try:
            _, _, _, _, fmt_id, _ = read_vtf_header(filepath)
            # Tiny textures (≤32px) are almost always masks/gradients
            w, h, _, _, _, _ = read_vtf_header(filepath)
            if w > 0 and h > 0 and max(w, h) <= 32:
                return 'lanczos'
        except:
            pass
    
    # Default: AI upscale (most textures benefit from AI)
    return 'ai'


def _find_original_vtf(deployed_vtf_path: str) -> Optional[str]:
    """Find the original (pre-upscale) VTF for a deployed file by checking source dirs.
    Uses the relative path under 'materials/' to search all original source directories.
    Returns the path to the original file, or None if not found."""
    normalized = deployed_vtf_path.replace('\\', '/')
    # Find the 'materials/' marker in the path
    for marker in ['materials/']:
        idx = normalized.lower().find(marker)
        if idx >= 0:
            relative = normalized[idx + len(marker):]
            for src_dir in ORIGINAL_SOURCE_DIRS:
                candidate = os.path.join(src_dir, relative.replace('/', os.sep))
                if os.path.isfile(candidate):
                    return candidate
    return None


def _check_vtf_alpha_health(deployed_path: str, original_path: str = None) -> dict:
    """Check alpha channel health of a VTF file.
    Returns dict with: 'alpha_broken' (bool), 'alpha_fixable' (bool), 'alpha_mean' (float).
    Only meaningful for DXT5/DXT3 formats with alpha."""
    result = {'alpha_broken': False, 'alpha_fixable': False, 'alpha_false_positive': False, 'alpha_mean': -1}
    try:
        from srctools.vtf import VTF as VTFCheck
        with open(deployed_path, 'rb') as f:
            vtf = VTFCheck.read(f)
            vtf.load()
            img = vtf.get(frame=0, mipmap=0).to_PIL()
        if img.mode != 'RGBA':
            return result
        _, _, _, a = img.split()
        alpha_arr = np.array(a)
        result['alpha_mean'] = float(alpha_arr.mean())
        # Broken = all pixels near 255 (DXT allows tiny variance)
        result['alpha_broken'] = int(alpha_arr.min()) >= 250
        
        if result['alpha_broken'] and original_path:
            # Check if original has real alpha data
            try:
                with open(original_path, 'rb') as f:
                    orig_vtf = VTFCheck.read(f)
                    orig_vtf.load()
                    orig_img = orig_vtf.get(frame=0, mipmap=0).to_PIL()
                if orig_img.mode == 'RGBA':
                    _, _, _, orig_a = orig_img.split()
                    orig_alpha = np.array(orig_a)
                    # Original has real alpha if it has variation (not all white)
                    if int(orig_alpha.min()) < 250:
                        result['alpha_fixable'] = True
                    else:
                        # Original is also all-white — not actually broken, just DXT5 format
                        result['alpha_broken'] = False
                        result['alpha_false_positive'] = True
            except:
                pass
    except:
        pass
    return result


def read_vtf_header(filepath: str) -> Tuple[int, int, str, bool, int, int]:
    """Fast header-only read (64 bytes) - no full VTF parse. Returns (w, h, fmt_name, has_alpha, format_id, frame_count)."""
    try:
        with open(filepath, 'rb') as f:
            if f.read(4) != b'VTF\x00':
                return 0, 0, "UNKNOWN", False, -1, 0
            f.read(8)
            f.read(4)
            width = struct.unpack('<H', f.read(2))[0]
            height = struct.unpack('<H', f.read(2))[0]
            f.read(4)  # flags
            frame_count = struct.unpack('<H', f.read(2))[0]
            f.read(2)  # first frame
            f.read(4)  # padding
            f.read(12); f.read(4); f.read(4)  # reflectivity, padding, bumpscale
            format_id = struct.unpack('<I', f.read(4))[0]
            fmt_map = {0: "RGBA8888", 3: "BGR888", 12: "BGRA8888", 13: "DXT1", 14: "DXT3", 15: "DXT5"}
            fmt = fmt_map.get(format_id, f"FMT_{format_id}")
            has_alpha = fmt in ("RGBA8888", "BGRA8888", "DXT5", "DXT3")
            return width, height, fmt, has_alpha, format_id, frame_count
    except:
        return 0, 0, "UNKNOWN", False, -1, 0


class AIUpscaler:
    """RealESRGAN AI Upscaler - source-optimized for ncnn-vulkan binary."""
    
    # Output format for intermediate files (binary output).
    # Binary only supports jpg/png/webp. PNG is lossless and has no dimension limit.
    # Note: Input extraction uses BMP (29x faster writes), but output must be PNG.
    BATCH_OUTPUT_FMT = "png"
    
    def __init__(self, exe_path_or_config, config: dict = None):
        # Guard: handle case where config dict is passed as first arg
        if isinstance(exe_path_or_config, dict):
            self.config = exe_path_or_config
            self.exe_path = REALESRGAN_EXE
        else:
            self.exe_path = exe_path_or_config
            self.config = config or {}
        self.available = self.exe_path.exists() if hasattr(self.exe_path, 'exists') else False
        self.progress_callback = None  # Called with (current_file, pct) during batch
    
    def _calc_vram_safe_tile(self, gpu_id: int = 0, vram_cap: float = 0.91) -> int:
        """Calculate a tile size that keeps VRAM usage under vram_cap (0-1).
        Returns 0 if pynvml is unavailable (lets binary auto-detect).
        
        Optimized for RTX 4080 SUPER 16GB:
        RealESRGAN x4plus VRAM per tile ~ tile^2 * 640 bytes.
        tile=256 ~ 40MB, tile=512 ~ 170MB, tile=768 ~ 380MB, tile=1024 ~ 680MB
        """
        if not PYNVML_AVAILABLE:
            return 0
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            total_mb = info.total / 1048576
            free_mb = info.free / 1048576
            # Budget = cap * total - currently_used
            budget_mb = total_mb * vram_cap - (total_mb - free_mb)
            budget_mb = max(budget_mb, 128)  # Floor at 128MB

            # Reserve VRAM for model + Vulkan overhead + proc thread buffers
            # Model: ~64MB GPU (32MB fp32 → fp16), Vulkan: ~500MB, pipeline: ~2.5GB
            MODEL_AND_OVERHEAD_MB = 3500
            tile_budget_mb = max(budget_mb - MODEL_AND_OVERHEAD_MB, 100)
            
            # Per-tile VRAM: ~640 bytes/pixel (input + 4x output + intermediates)
            budget_bytes = tile_budget_mb * 1048576
            max_tile = int((budget_bytes / 640) ** 0.5)

            # Snap to nearest standard tile size (more granularity for high-end GPUs)
            standard_tiles = [128, 192, 256, 384, 512, 640, 768, 896, 1024]
            best = 128
            for t in standard_tiles:
                if t <= max_tile:
                    best = t

            print(f"[AI] VRAM tile calc: {free_mb:.0f}/{total_mb:.0f}MB free, "
                  f"budget {budget_mb:.0f}MB (cap {vram_cap*100:.0f}%), "
                  f"tile_budget {tile_budget_mb:.0f}MB → tile={best}", file=sys.stderr)

            return best
        except Exception:
            return 0

    def _build_cmd(self, input_path: str, output_path: str, model: str, scale: int,
                   verbose: bool = False, output_fmt: str = None,
                   batch_file_count: int = 1, max_image_height: int = 0) -> list:
        """Build RealESRGAN command line args with VRAM-optimized threading.
        
        Key insight from C++ source analysis: the binary's proc threads each hold
        one full image in VRAM simultaneously (input + 4x output + tile buffers).
        VRAM usage = model_overhead + (proc_threads × per_image_vram).
        
        Strategy: keep tile size HIGH (fewer GPU iterations = faster per image),
        and dynamically maximize proc threads to fill available VRAM.
        
        Optimized for RTX 4080 SUPER 16GB VRAM:
        - Model overhead: ~3.5GB (model 64MB + Vulkan ~500MB + pipeline ~2.5GB)
        - Per proc thread: 320-2600MB depending on tile size
        - Safe to run 3 proc threads with 1024 tiles (11.3GB total)
        """
        tile_cfg = self.config.get("tile_size", 0)
        if tile_cfg == 0:
            # Auto: calculate safe tile from VRAM budget
            vram_cap = self.config.get("vram_cap", 0.85)
            gpu_id_int = self.config.get("gpu_id", 0)
            safe_tile = self._calc_vram_safe_tile(gpu_id_int, vram_cap)
            tile_size = str(safe_tile) if safe_tile > 0 else "0"
        else:
            tile_size = str(tile_cfg)
        
        # Adaptive tile cap: tall images (height >= 1024) cause VRAM exhaustion
        # at large tile sizes. Cap to 512 to prevent black/blank output.
        if max_image_height >= 1024 and tile_size != "0" and int(tile_size) > 512:
            print(f"[AI] Adaptive tile: capping {tile_size} -> 512 (max image height={max_image_height})", file=sys.stderr)
            tile_size = "512"
        
        # VRAM-aware dynamic threading (from C++ source analysis)
        # The binary uses a producer-consumer pipeline: load → proc → save
        # Only proc threads hold images in VRAM. Each proc thread needs:
        #   - Input buffer + output buffer (4x resolution) + tile intermediates
        #   - Staging allocators for upload/download to GPU
        # Validated per-proc-thread VRAM (RTX 4080 SUPER, corrected with safety margin):
        per_proc_mb = {
            1024: 4000, 896: 2600, 768: 2000, 640: 1500,
            512: 1000, 384: 600, 256: 320, 192: 200, 128: 120
        }
        MODEL_OVERHEAD_MB = 4500  # Model + Vulkan + pipeline + fragmentation
        
        # Optimized defaults: 2 load threads keep GPU pipeline fed,
        # 2 proc threads for single images, 2 save threads for I/O overlap
        gpu_threads = "2:2:2"
        
        if batch_file_count > 1 and PYNVML_AVAILABLE:
            try:
                gpu_id_int = self.config.get("gpu_id", 0)
                handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id_int)
                info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                free_mb = info.free / 1048576
                total_mb = info.total / 1048576
                
                # VRAM budget for proc threads (15% safety margin)
                tile_val = int(tile_size) if tile_size != "0" else 512
                per_thread_mb = per_proc_mb.get(tile_val, per_proc_mb.get(512, 1000))
                # Tall images have much larger output buffers (e.g. 512x1024 → 2048x4096)
                if max_image_height >= 1024:
                    per_thread_mb = int(per_thread_mb * 1.5)
                vram_for_procs = (free_mb - MODEL_OVERHEAD_MB) * 0.85
                
                if vram_for_procs > 0 and per_thread_mb > 0:
                    # Max proc threads that fit in VRAM
                    # Cap at 4 for 16GB+ cards, 2 for 8GB, 1 for <8GB
                    if total_mb >= 15000:    # 16GB cards
                        # Cap to 2 for large tiles OR tall images (big output buffers)
                        hard_cap = 2 if (tile_val >= 1024 or max_image_height >= 1024) else 4
                    elif total_mb >= 7500:   # 8GB cards  
                        hard_cap = 2
                    else:                    # <8GB
                        hard_cap = 1
                    
                    max_procs = max(1, min(hard_cap, int(vram_for_procs / per_thread_mb)))
                    optimal_procs = min(max_procs, batch_file_count)
                    
                    # Load threads: 2 for batch, keeps GPU pipeline fed
                    # Save threads: match proc threads for I/O balance
                    load_threads = min(2, batch_file_count)
                    save_threads = max(2, optimal_procs)
                    gpu_threads = f"{load_threads}:{optimal_procs}:{save_threads}"
                    
                    print(
                        f"[AI] VRAM budget: {free_mb:.0f}/{total_mb:.0f}MB, "
                        f"{per_thread_mb}MB/thread @ tile={tile_val} → "
                        f"threads={gpu_threads} ({optimal_procs} concurrent, "
                        f"headroom={vram_for_procs - optimal_procs*per_thread_mb:.0f}MB)",
                        file=sys.stderr)
            except Exception:
                pass
        gpu_id = str(self.config.get("gpu_id", 0))
        fmt = output_fmt or self.BATCH_OUTPUT_FMT
        effective_scale = min(scale, 4)  # Binary only supports up to 4x
        
        cmd = [
            str(self.exe_path),
            "-i", input_path,
            "-o", output_path,
            "-n", model,
            "-s", str(effective_scale),
            "-t", tile_size,
            "-j", gpu_threads,
            "-g", gpu_id,
            "-f", fmt,
            "-m", str(self.exe_path.parent / "models"),
        ]
        if verbose:
            cmd.append("-v")
        if self.config.get("tta_mode", False):
            cmd.append("-x")
        denoise = self.config.get("denoise_strength", 0)
        if denoise > 0 and ("denoise" in model or "animevideo" in model):
            cmd.extend(["-d", str(denoise)])
        return cmd
    
    def upscale_batch(self, input_dir: str, output_dir: str, scale: int = 4, 
                      model: str = "realesrgan-x4plus", timeout: int = 600,
                      progress_callback=None, log_callback=None) -> bool:
        """
        Batch upscale all images in input_dir to output_dir.
        Uses RealESRGAN folder mode - model loads ONCE for entire batch.
        Streams real-time progress via Popen + stderr pipe.
        Returns True on success.
        """
        if not self.available:
            return False
        
        # Scan images and split into normal vs tall groups for adaptive tile sizing
        # Tall images (height >= 1024) get processed separately at tile=512
        # to prevent VRAM exhaustion and black output
        normal_files = []
        tall_files = []
        tile_cfg = self.config.get("tile_size", 0)
        
        try:
            all_files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
            if tile_cfg > 512:
                # Only split if user tile > 512 — otherwise no need
                for f in all_files:
                    fp = os.path.join(input_dir, f)
                    try:
                        with Image.open(fp) as im:
                            if im.height >= 1024:
                                tall_files.append(f)
                            else:
                                normal_files.append(f)
                    except Exception:
                        normal_files.append(f)  # Fallback: treat as normal
            else:
                normal_files = all_files
        except Exception:
            normal_files = []
        
        success = True
        done_count = 0
        
        def _run_batch(src_dir, file_list, batch_label, max_h=0):
            """Run a single binary batch with optional staging directory."""
            nonlocal done_count, success
            
            if not file_list:
                return
            
            # Stage files into a temp dir so binary only processes this subset
            use_staging = (src_dir == input_dir and (len(normal_files) > 0 and len(tall_files) > 0))
            
            if use_staging:
                staging_dir = os.path.join(get_scratch_dir(), f"_batch_{batch_label}")
                os.makedirs(staging_dir, exist_ok=True)
                for f in file_list:
                    src = os.path.join(input_dir, f)
                    dst = os.path.join(staging_dir, f)
                    try:
                        # Hard link to avoid copy overhead
                        if os.path.exists(dst):
                            os.remove(dst)
                        os.link(src, dst)
                    except OSError:
                        # Fallback to copy if hard link fails (cross-drive)
                        import shutil
                        shutil.copy2(src, dst)
                batch_input = staging_dir
            else:
                batch_input = src_dir
            
            cmd = self._build_cmd(batch_input, output_dir, model, scale, verbose=True,
                                  batch_file_count=len(file_list), max_image_height=max_h)
            print(f"[AIUpscaler] {batch_label} BATCH ({len(file_list)} files): {' '.join(cmd)}", file=sys.stderr)
            
            try:
                flags = 0x08000000  # CREATE_NO_WINDOW only (no HIGH_PRIORITY — can freeze Windows)
                proc = subprocess.Popen(
                    cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    cwd=str(self.exe_path.parent),
                    creationflags=flags,
                    bufsize=65536
                )
                
                # Non-blocking stderr reader with per-file watchdog timeout.
                # Old code: blocking readline loop that hangs forever if binary stalls.
                # New code: background thread feeds a queue, main loop checks with timeout.
                import queue as _queue
                stderr_q = _queue.Queue()
                
                def _stderr_reader(pipe, q):
                    try:
                        for line in iter(pipe.readline, b''):
                            q.put(line)
                    except Exception:
                        pass
                    finally:
                        q.put(None)  # Sentinel
                
                reader_thread = threading.Thread(target=_stderr_reader, args=(proc.stderr, stderr_q), daemon=True)
                reader_thread.start()
                
                PER_FILE_TIMEOUT = 120  # seconds — kill if no output for this long
                
                while True:
                    try:
                        line = stderr_q.get(timeout=PER_FILE_TIMEOUT)
                    except _queue.Empty:
                        # No output for PER_FILE_TIMEOUT seconds — binary is hung
                        print(f"[AIUpscaler] WATCHDOG: No output for {PER_FILE_TIMEOUT}s — killing hung process", file=sys.stderr)
                        if log_callback:
                            log_callback(f"⚠ GPU process hung (no output for {PER_FILE_TIMEOUT}s) — killed")
                        proc.kill()
                        success = False
                        break
                    
                    if line is None:
                        break  # Process finished
                    
                    line_str = line.decode('utf-8', errors='replace').strip()
                    if not line_str:
                        continue
                    print(f"[GPU] {line_str}", file=sys.stderr)
                    if log_callback:
                        log_callback(line_str)
                    if 'done' in line_str:
                        done_count += 1
                        if progress_callback:
                            progress_callback(done_count, line_str)
                    elif 'vkAllocateMemory failed' in line_str or 'vkWaitForFences failed' in line_str:
                        if progress_callback:
                            progress_callback(done_count, line_str)
                        proc.kill()
                        success = False
                        break
                    elif '%' in line_str and progress_callback:
                        progress_callback(done_count, line_str)
                
                proc.wait(timeout=30)
                print(f"[AIUpscaler] {batch_label} RETURN CODE: {proc.returncode}", file=sys.stderr)
                if proc.returncode != 0:
                    success = False
            except subprocess.TimeoutExpired:
                proc.kill()
                success = False
            except Exception:
                success = False
            finally:
                # Clean up staging dir
                if use_staging:
                    try:
                        import shutil
                        shutil.rmtree(staging_dir, ignore_errors=True)
                    except Exception:
                        pass
        
        # Pass 1: Normal images at user's configured tile size
        if normal_files:
            if log_callback and tall_files:
                log_callback(f"[Split] Pass 1: {len(normal_files)} normal images (tile={tile_cfg})")
            _run_batch(input_dir, normal_files, "NORMAL", max_h=0)
        
        # VRAM cooldown: let Vulkan fully release GPU memory before Pass 2
        if normal_files and tall_files:
            time.sleep(5)
        
        # Pass 2: Tall images at capped tile=512
        if tall_files:
            if log_callback:
                log_callback(f"[Split] Pass 2: {len(tall_files)} tall images (tile=512, height>=1024)")
            _run_batch(input_dir, tall_files, "TALL", max_h=1024)
        
        return success
    
    def upscale(self, img: Image.Image, scale: int = 4, model: str = "realesrgan-x4plus", source_name: str = "") -> Optional[Image.Image]:
        """Single image upscale (for preview). Uses BMP input, WebP output for speed."""
        if not self.available:
            return None
        try:
            with tempfile.TemporaryDirectory(dir=get_scratch_dir()) as tmpdir:
                input_path = os.path.join(tmpdir, "input.bmp")
                output_path = os.path.join(tmpdir, "output.png")  # Always PNG for preview
                
                alpha = None
                if img.mode == 'RGBA':
                    alpha = img.split()[3]
                    img.convert('RGB').save(input_path)
                else:
                    img.save(input_path)
                
                # Pass image height for adaptive tile sizing
                cmd = self._build_cmd(input_path, output_path, model, scale,
                                      max_image_height=img.height)
                print(f"[AIUpscaler] CMD: {' '.join(cmd[:6])}... model={model}")
                # NORMAL priority for preview — HIGH_PRIORITY can freeze Windows
                flags = 0x08000000  # CREATE_NO_WINDOW only
                result = subprocess.run(cmd, capture_output=True, timeout=30,
                             cwd=str(self.exe_path.parent),
                             creationflags=flags)
                
                if result.returncode != 0:
                    stderr_msg = result.stderr.decode('utf-8', errors='replace')[:500] if result.stderr else 'no stderr'
                    print(f"[AIUpscaler] Binary failed (rc={result.returncode}): {stderr_msg}")
                
                # Find output file (binary may add extension)
                found = None
                for ext in [self.BATCH_OUTPUT_FMT, 'png', 'webp', 'jpg']:
                    candidate = os.path.join(tmpdir, f"output.{ext}")
                    if os.path.exists(candidate) and os.path.getsize(candidate) > 512:
                        found = candidate
                        break
                # Also check output dir for any image file
                if not found:
                    for f in os.listdir(tmpdir):
                        fp = os.path.join(tmpdir, f)
                        if os.path.isfile(fp) and os.path.getsize(fp) > 512 and f != "input.bmp":
                            found = fp
                            break
                
                if found:
                    time.sleep(0.02)
                    with Image.open(found) as tmp:
                        upscaled = tmp.copy()
                    if alpha is not None:
                        alpha_up = alpha.resize(upscaled.size, Image.Resampling.LANCZOS)
                        upscaled = upscaled.convert('RGBA')
                        upscaled.putalpha(alpha_up)
                    return upscaled
                else:
                    print(f"[AIUpscaler] Output not found. Dir contents: {os.listdir(tmpdir)}")
                return None
        except Exception as e:
            print(f"[AIUpscaler] Exception in upscale: {e}")
            return None


class VTFProcessor:
    """VTF processing engine with batch pipeline support."""
    
    def __init__(self, config: dict):
        self.config = config
        self.log_callback = None
        self.ai_upscaler = AIUpscaler(REALESRGAN_EXE, config)
    
    def set_logger(self, callback):
        self.log_callback = callback
    
    def _log(self, msg: str):
        if self.log_callback:
            self.log_callback(msg)
    
    def get_vtf_info(self, filepath: str) -> Tuple[int, int, str, bool, int, int]:
        return read_vtf_header(filepath)
    
    def load_vtf_image(self, filepath: str) -> Optional[Image.Image]:
        if not SRCTOOLS_AVAILABLE or not PIL_AVAILABLE:
            return None
        try:
            with open(filepath, 'rb') as f:
                vtf = VTF.read(f)
                vtf.load()
                try:
                    frame = vtf.get(frame=0, mipmap=0)
                except (TypeError, ValueError):
                    try:
                        frame = vtf.get(frame=0, mipmap=0, side=0)
                    except:
                        return None
                try:
                    img = frame.to_PIL()
                except:
                    try:
                        raw_data = bytes(frame)
                        w, h = vtf.width, vtf.height
                        if len(raw_data) == w * h * 3:
                            img = Image.frombytes('RGB', (w, h), raw_data)
                        elif len(raw_data) == w * h * 4:
                            img = Image.frombytes('RGBA', (w, h), raw_data)
                        else:
                            return None
                    except:
                        return None
                if img.mode == 'RGBA':
                    img = img.convert('RGB')
                elif img.mode not in ('RGB',):
                    img = img.convert('RGB')
                return img
        except:
            return None

    def load_vtf_frames(self, filepath: str) -> List[Image.Image]:
        """Loads all frames from a VTF into a list of PIL Images."""
        if not SRCTOOLS_AVAILABLE or not PIL_AVAILABLE:
            return []
        try:
            with open(filepath, 'rb') as f:
                vtf = VTF.read(f)
                vtf.load()
                
                frames = []
                count = max(1, getattr(vtf, 'frame_count', 1))
                for i in range(count):
                    try:
                        frame = vtf.get(frame=i, mipmap=0)
                    except (TypeError, ValueError):
                        try:
                            frame = vtf.get(frame=i, mipmap=0, side=0)
                        except:
                            continue
                    try:
                        img = frame.to_PIL()
                    except:
                        try:
                            raw_data = bytes(frame)
                            w, h = vtf.width, vtf.height
                            if len(raw_data) == w * h * 3:
                                img = Image.frombytes('RGB', (w, h), raw_data)
                            elif len(raw_data) == w * h * 4:
                                img = Image.frombytes('RGBA', (w, h), raw_data)
                            else:
                                continue
                        except:
                            continue
                    
                    if img.mode == 'RGBA':
                        img = img.convert('RGB')
                    elif img.mode not in ('RGB',):
                        img = img.convert('RGB')
                    frames.append(img)
                return frames
        except:
            return []
    
    def calc_target_dims(self, orig_w: int, orig_h: int, target: int) -> Tuple[int, int]:
        """Calculate target dimensions preserving aspect ratio with power-of-2."""
        if orig_w >= orig_h:
            new_w = target
            new_h = next_power_of_2(int(orig_h * (target / orig_w)))
            new_h = min(new_h, target)
        else:
            new_h = target
            new_w = next_power_of_2(int(orig_w * (target / orig_h)))
            new_w = min(new_w, target)
        return new_w, new_h
    
    def should_skip(self, filepath: str) -> Tuple[bool, str]:
        """Pre-flight check using header only. Returns (skip, reason)."""
        filename = os.path.basename(filepath)
        problem = is_problematic_texture(filename)
        if problem:
            return True, f"Skipped ({problem})"
        
        orig_w, orig_h, _, _, _, _ = read_vtf_header(filepath)
        if orig_w == 0:
            return True, "Failed to read VTF"
        
        target = self.config.get("target_resolution", 2048)
        new_w, new_h = self.calc_target_dims(orig_w, orig_h, target)
        
        if orig_w >= target and orig_h >= target:
            return True, f"Skipped (already {orig_w}x{orig_h})"
        if orig_w >= new_w and orig_h >= new_h:
            return True, f"Skipped ({orig_w}x{orig_h})"
        
        skip_small = self.config.get("skip_small", 64)
        if skip_small > 0 and orig_w < skip_small and orig_h < skip_small:
            return True, f"Skipped (too small {orig_w}x{orig_h})"
        
        return False, ""
    
    def extract_to_bmp(self, filepath: str, output_bmp: str) -> Tuple[bool, dict]:
        """Stage 1: Extract VTF to PNG for AI processing. Returns metadata needed for Stage 3.
        
        Uses PNG format for lossless color fidelity (BMP strips alpha, PNG preserves it).
        Alpha channel is saved as a separate file and tracked in meta['alpha_path'].
        """
        try:
            # Read format_id from raw header for format-aware output
            _, _, _, _, src_format_id, header_frames = read_vtf_header(filepath)
            
            with open(filepath, 'rb') as f:
                vtf = VTF.read(f)
                vtf.load()
                meta = {
                    'flags': vtf.flags,
                    'reflectivity': vtf.reflectivity,
                    'version': vtf.version,
                    'orig_w': vtf.width,
                    'orig_h': vtf.height,
                    'src_format_id': src_format_id,
                }
                if meta['version'][0] == 7:
                    minor = max(2, min(5, meta['version'][1]))
                    meta['version'] = (7, minor)
                
                # Detect frame count
                frame_count = 1
                if header_frames > 1:
                    for i in range(header_frames):
                        try:
                            vtf.get(frame=i, mipmap=0)
                            frame_count = i + 1
                        except:
                            break
                
                meta['frame_count'] = frame_count
                
                if frame_count > 1:
                    # ── Multi-frame extraction ──────────────────────────
                    self._log(f"  📽️ Animated VTF: {frame_count} frames")
                    base_name = os.path.splitext(output_bmp)[0]
                    ext = os.path.splitext(output_bmp)[1] or '.png'
                    frame_paths = []
                    alpha_paths = []
                    has_alpha = False
                    alpha_is_binary = False
                    
                    for i in range(frame_count):
                        try:
                            pil_img = vtf.get(frame=i, mipmap=0).to_PIL()
                        except (TypeError, ValueError):
                            try:
                                pil_img = vtf.get(frame=i, mipmap=0, side=0).to_PIL()
                            except:
                                return False, {'error': f'frame {i} read failed'}
                        
                        frame_path = f"{base_name}_frame{i}{ext}"
                        
                        # Handle alpha per-frame
                        if pil_img.mode == 'RGBA':
                            has_alpha = True
                            alpha = pil_img.split()[3]
                            alpha_path = f"{base_name}_frame{i}_alpha.png"
                            alpha.save(alpha_path)
                            alpha_paths.append(alpha_path)
                            if i == 0:
                                alpha_arr = np.array(alpha)
                                binary_pct = np.sum((alpha_arr < 16) | (alpha_arr > 240)) / alpha_arr.size
                                alpha_is_binary = binary_pct > 0.85
                            pil_img = pil_img.convert('RGB')
                        else:
                            alpha_paths.append(None)
                        
                        pil_img.save(frame_path)
                        frame_paths.append(frame_path)
                    
                    meta['has_alpha'] = has_alpha
                    meta['alpha_is_binary'] = alpha_is_binary
                    meta['frame_paths'] = frame_paths
                    meta['alpha_paths'] = alpha_paths
                    # Save frame 0 as main output for batch pipeline compatibility
                    if frame_paths and not os.path.exists(output_bmp):
                        shutil.copy2(frame_paths[0], output_bmp)
                    return True, meta
                
                # ── Single-frame extraction (original logic) ──────────
                try:
                    pil_img = vtf.get(frame=0, mipmap=0).to_PIL()
                except (TypeError, ValueError):
                    try:
                        pil_img = vtf.get(frame=0, mipmap=0, side=0).to_PIL()
                    except:
                        return False, {'error': 'cubemap'}
                except Exception as e:
                    if "data block" in str(e).lower():
                        return False, {'error': 'corrupted'}
                    raise
            
            # Check for alpha
            meta['has_alpha'] = pil_img.mode == 'RGBA'
            if meta['has_alpha']:
                alpha = pil_img.split()[3]
                # Save alpha as separate PNG for lossless restoration later
                alpha_path = output_bmp.replace('.bmp', '_alpha.png').replace('.png', '_alpha.png')
                if alpha_path == output_bmp:  # safety fallback
                    alpha_path = output_bmp + '_alpha.png'
                alpha.save(alpha_path)
                meta['alpha_path'] = alpha_path  # Track path for assembly stage
                
                # Check if alpha is binary (for $alphatest foliage cutouts)
                alpha_arr = np.array(alpha)
                binary_pct = np.sum((alpha_arr < 16) | (alpha_arr > 240)) / alpha_arr.size
                meta['alpha_is_binary'] = binary_pct > 0.85  # >85% pixels are near 0 or 255
                
                pil_img = pil_img.convert('RGB')
            
            pil_img.save(output_bmp)
            return True, meta
        except Exception as e:
            return False, {'error': str(e)}
    
    def assemble_vtf(self, upscaled_path: str, output_vtf: str, input_vtf: str, meta: dict) -> Tuple[bool, str]:
        """Stage 3: Assemble upscaled image back into VTF. Supports multi-frame animated VTFs."""
        try:
            frame_count = meta.get('frame_count', 1)
            
            # ── Multi-frame assembly ──────────────────────────────
            if frame_count > 1:
                frame_paths = meta.get('frame_paths', [])
                alpha_paths = meta.get('alpha_paths', [])
                target = self.config.get("target_resolution", 2048)
                new_w, new_h = self.calc_target_dims(meta['orig_w'], meta['orig_h'], target)
                
                # Determine output format
                out_fmt_name = self.config.get("output_format", "Auto (Match Source)")
                has_alpha = meta.get('has_alpha', False)
                if out_fmt_name == "Auto (Match Source)":
                    src_fmt_id = meta.get('src_format_id', 15)
                    matched_fmt = get_output_format_for_source(src_fmt_id)
                    if matched_fmt:
                        out_fmt = matched_fmt
                        out_fmt_name = FORMAT_ID_MAP.get(src_fmt_id, "DXT5")
                    else:
                        out_fmt = ImageFormats.DXT5
                        out_fmt_name = "DXT5"
                else:
                    if has_alpha and out_fmt_name == "DXT1":
                        out_fmt_name = "DXT5"
                    out_fmt = VTF_FORMATS.get(out_fmt_name, ImageFormats.DXT5)
                
                # Create multi-frame VTF
                new_vtf = VTF(new_w, new_h, frames=frame_count, fmt=out_fmt, version=meta['version'])
                new_vtf.flags = meta['flags']
                new_vtf.reflectivity = meta['reflectivity']
                
                # Find upscaled frame files (AI output uses same naming)
                # upscaled_path could be e.g. "0000_frame0.png" — strip the _frame0 to get base "0000"
                raw_base = os.path.splitext(upscaled_path)[0]
                ext = os.path.splitext(upscaled_path)[1] or '.png'
                out_dir = os.path.dirname(upscaled_path)
                
                # Strip trailing _frameN from base name if present
                import re
                base_name = re.sub(r'_frame\d+$', '', raw_base)
                
                for i in range(frame_count):
                    # Look for upscaled frame file in the same directory as the first frame
                    frame_file = os.path.join(out_dir, f"{os.path.basename(base_name)}_frame{i}{ext}")
                    if not os.path.exists(frame_file):
                        # Fallback: try the full path pattern (for single-file upscale)
                        frame_file = f"{base_name}_frame{i}{ext}"
                    if not os.path.exists(frame_file):
                        # Fallback: check original frame paths in input dir
                        if i < len(frame_paths):
                            orig_base = os.path.splitext(os.path.basename(frame_paths[i]))[0]
                            frame_file = os.path.join(out_dir, orig_base + ext)
                    
                    if not os.path.exists(frame_file):
                        return False, f"Missing upscaled frame {i}: {os.path.basename(base_name)}_frame{i}{ext}"
                    
                    # Load upscaled frame
                    if CV2_AVAILABLE:
                        bgr = cv2.imread(frame_file, cv2.IMREAD_UNCHANGED)
                        if bgr is None:
                            return False, f"Failed to decode frame {i}"
                        if len(bgr.shape) == 3 and bgr.shape[2] == 4:
                            rgba = cv2.cvtColor(bgr, cv2.COLOR_BGRA2RGBA)
                            frame_img = Image.fromarray(rgba, 'RGBA')
                        elif len(bgr.shape) == 3:
                            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                            frame_img = Image.fromarray(rgb, 'RGB')
                        else:
                            frame_img = Image.fromarray(bgr)
                    else:
                        with Image.open(frame_file) as tmp:
                            frame_img = tmp.copy()
                    
                    # Resize to target
                    frame_img = frame_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    
                    # Restore alpha for this frame
                    if has_alpha and i < len(alpha_paths) and alpha_paths[i] and os.path.exists(alpha_paths[i]):
                        _alpha_tmp = Image.open(alpha_paths[i])
                        alpha = _alpha_tmp.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        _alpha_tmp.close()
                        if meta.get('alpha_is_binary'):
                            alpha_arr = np.array(alpha)
                            alpha_arr = np.where(alpha_arr > 128, 255, 0).astype(np.uint8)
                            alpha = Image.fromarray(alpha_arr, 'L')
                        frame_img = frame_img.convert('RGBA')
                        frame_img.putalpha(alpha)
                    
                    # Apply sharpen if configured
                    sharpen = self.config.get("sharpen_strength", 0.0)
                    if sharpen > 0:
                        enhancer = ImageEnhance.Sharpness(frame_img)
                        frame_img = enhancer.enhance(1.0 + sharpen)
                    
                    if frame_img.mode != 'RGBA':
                        frame_img = frame_img.convert('RGBA')
                    new_vtf.get(frame=i, mipmap=0).copy_from(frame_img.tobytes())
                
                if self.config.get("generate_mipmaps", True):
                    new_vtf.compute_mipmaps()
                
                if self.config.get("backup_originals", True) and output_vtf == input_vtf:
                    backup = input_vtf + ".bak"
                    if not os.path.exists(backup):
                        shutil.copy2(input_vtf, backup)
                
                os.makedirs(os.path.dirname(output_vtf) or '.', exist_ok=True)
                if not hasattr(new_vtf, 'hotspot_info'):
                    new_vtf.hotspot_info = None
                if not hasattr(new_vtf, 'hotspot_flags'):
                    new_vtf.hotspot_flags = 0
                tmp_path = output_vtf + '.tmp'
                with open(tmp_path, 'wb') as f:
                    new_vtf.save(f)
                os.replace(tmp_path, output_vtf)
                
                file_size = os.path.getsize(output_vtf)
                if file_size >= 1024 * 1024:
                    size_str = f"{file_size / 1024 / 1024:.1f}MB"
                else:
                    size_str = f"{file_size / 1024:.0f}KB"
                
                return True, f"{meta['orig_w']}x{meta['orig_h']} → {new_w}x{new_h} [{frame_count}f] ({size_str})"
            
            # ── Single-frame assembly (original logic) ────────────
            if not os.path.exists(upscaled_path) or os.path.getsize(upscaled_path) < 1024:
                return False, "AI output missing or corrupted"
            
            # OPT: cv2.imread is ~2x faster than Pillow for PNG decode
            if CV2_AVAILABLE:
                bgr = cv2.imread(upscaled_path, cv2.IMREAD_UNCHANGED)
                if bgr is None:
                    return False, "Failed to decode upscaled image"
                if len(bgr.shape) == 3 and bgr.shape[2] == 4:
                    # BGRA → RGBA
                    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGRA2RGBA)
                    upscaled = Image.fromarray(rgba, 'RGBA')
                elif len(bgr.shape) == 3:
                    # BGR → RGB
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    upscaled = Image.fromarray(rgb, 'RGB')
                else:
                    upscaled = Image.fromarray(bgr)
            else:
                with Image.open(upscaled_path) as tmp:
                    upscaled = tmp.copy()
            
            # Corruption detection: check for black/solid textures
            warn_tag = ""
            try:
                # OPT: Use numpy directly (skip Pillow convert for speed)
                w, h = upscaled.size
                crop_size = min(256, w, h)
                cx, cy = w // 2, h // 2
                check_arr = np.array(upscaled)
                # Slice center region directly from numpy array
                y1, y2 = cy - crop_size//2, cy + crop_size//2
                x1, x2 = cx - crop_size//2, cx + crop_size//2
                sample = check_arr[y1:y2, x1:x2, :3].astype(np.float32)
                mean_val = sample.mean()
                std_val = sample.std()
                
                if mean_val < 5:
                    warn_tag = " ⚠️ DARK"
                elif std_val < 2:
                    warn_tag = " ⚠️ FLAT"
                elif mean_val < 15 and std_val < 10:
                    warn_tag = " ⚠️ SUSPECT"
            except:
                pass
            
            target = self.config.get("target_resolution", 2048)
            new_w, new_h = self.calc_target_dims(meta['orig_w'], meta['orig_h'], target)
            upscaled = upscaled.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            # Restore alpha if present
            if meta.get('has_alpha'):
                alpha_file = None
                
                # Primary: use tracked path from meta (most reliable)
                if meta.get('alpha_path') and os.path.exists(meta['alpha_path']):
                    alpha_file = meta['alpha_path']
                else:
                    # Fallback: search for alpha file near the input/output
                    base = os.path.splitext(os.path.basename(upscaled_path))[0]
                    for search_dir in [os.path.dirname(upscaled_path),
                                       os.path.join(os.path.dirname(upscaled_path), '..', 'input'),
                                       os.path.join(os.path.dirname(upscaled_path), '..', 'alpha_hold')]:
                        for ext in ['_alpha.png', '_alpha.bmp']:
                            candidate = os.path.join(search_dir, base + ext)
                            if os.path.exists(candidate):
                                alpha_file = candidate
                                break
                        if alpha_file:
                            break
                
                if alpha_file:
                    _alpha_tmp = Image.open(alpha_file)
                    alpha = _alpha_tmp.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    _alpha_tmp.close()
                    
                    # For binary alpha (foliage $alphatest cutouts), threshold to crisp edges
                    if meta.get('alpha_is_binary'):
                        alpha_arr = np.array(alpha)
                        alpha_arr = np.where(alpha_arr > 128, 255, 0).astype(np.uint8)
                        alpha = Image.fromarray(alpha_arr, 'L')
                    
                    upscaled = upscaled.convert('RGBA')
                    upscaled.putalpha(alpha)
            
            # Post-process: Apply sharpen if configured
            sharpen = self.config.get("sharpen_strength", 0.0)
            if sharpen > 0:
                enhancer = ImageEnhance.Sharpness(upscaled)
                upscaled = enhancer.enhance(1.0 + sharpen)
            
            # Output format — with Auto (Match Source) support
            out_fmt_name = self.config.get("output_format", "Auto (Match Source)")
            has_alpha = meta.get('has_alpha', False)
            
            if out_fmt_name == "Auto (Match Source)":
                src_fmt_id = meta.get('src_format_id', 15)  # Default to DXT5
                matched_fmt = get_output_format_for_source(src_fmt_id)
                if matched_fmt:
                    out_fmt = matched_fmt
                    out_fmt_name = FORMAT_ID_MAP.get(src_fmt_id, "DXT5")
                else:
                    out_fmt = ImageFormats.DXT5
                    out_fmt_name = "DXT5"
            else:
                if has_alpha and out_fmt_name == "DXT1":
                    out_fmt_name = "DXT5"
                out_fmt = VTF_FORMATS.get(out_fmt_name, ImageFormats.DXT5)
            
            new_vtf = VTF(upscaled.width, upscaled.height, fmt=out_fmt, version=meta['version'])
            new_vtf.flags = meta['flags']
            new_vtf.reflectivity = meta['reflectivity']
            
            if upscaled.mode != 'RGBA':
                upscaled = upscaled.convert('RGBA')
            new_vtf.get(frame=0, mipmap=0).copy_from(upscaled.tobytes())
            
            if self.config.get("generate_mipmaps", True):
                new_vtf.compute_mipmaps()
            
            if self.config.get("backup_originals", True) and output_vtf == input_vtf:
                backup = input_vtf + ".bak"
                if not os.path.exists(backup):
                    shutil.copy2(input_vtf, backup)
            
            os.makedirs(os.path.dirname(output_vtf) or '.', exist_ok=True)
            # Patch missing srctools attrs that save() expects
            if not hasattr(new_vtf, 'hotspot_info'):
                new_vtf.hotspot_info = None
            if not hasattr(new_vtf, 'hotspot_flags'):
                new_vtf.hotspot_flags = 0
            tmp_path = output_vtf + '.tmp'
            with open(tmp_path, 'wb') as f:
                new_vtf.save(f)
            os.replace(tmp_path, output_vtf)  # atomic rename
            
            file_size = os.path.getsize(output_vtf)
            if file_size >= 1024 * 1024 * 1024:
                size_str = f"{file_size / 1024 / 1024 / 1024:.1f}GB"
            elif file_size >= 1024 * 1024:
                size_str = f"{file_size / 1024 / 1024:.1f}MB"
            else:
                size_str = f"{file_size / 1024:.0f}KB"
            
            return True, f"{meta['orig_w']}x{meta['orig_h']} → {new_w}x{new_h} [AI] ({size_str}){warn_tag}"
        except Exception as e:
            return False, str(e)
    
    def upscale_image(self, img, target, source_name=""):
        """Single image upscale for preview."""
        method = self.config.get("upscale_method", "ai")
        orig_w, orig_h = img.size
        new_w, new_h = self.calc_target_dims(orig_w, orig_h, target)
        
        if method == "ai" and self.ai_upscaler.available:
            ai_scale = self.config.get("ai_scale", 4)
            ai_model = self.config.get("ai_model", "realesrgan-x4plus")
            
            if ai_scale > 4:
                # 8x preview: single 4x pass then Lanczos to target
                # (full 8x = 2 GPU passes, too slow for preview)
                upscaled = self.ai_upscaler.upscale(img, scale=4, model=ai_model, source_name=source_name)
                if upscaled:
                    upscaled = upscaled.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    method_used = f"AI (4x→resize, preview of {ai_scale}x)"
                else:
                    upscaled = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    method_used = "Lanczos (AI failed)"
            else:
                upscaled = self.ai_upscaler.upscale(img, scale=ai_scale, model=ai_model, source_name=source_name)
                if upscaled:
                    upscaled = upscaled.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    method_used = "AI"
                else:
                    upscaled = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    method_used = "Lanczos (AI failed)"
            
            # Apply sharpen to AI output preview so user can see it
            sharpen = self.config.get("sharpen_strength", 0.0)
            if sharpen > 0 and upscaled:
                upscaled = ImageEnhance.Sharpness(upscaled).enhance(1.0 + float(sharpen))
                
            return upscaled, method_used
        else:
            resample = {"lanczos": Image.Resampling.LANCZOS, "bicubic": Image.Resampling.BICUBIC}.get(method, Image.Resampling.LANCZOS)
            upscaled = img.resize((new_w, new_h), resample)
            sharpen = self.config.get("sharpen_strength", 0.0)
            if sharpen > 0:
                upscaled = ImageEnhance.Sharpness(upscaled).enhance(1.0 + float(sharpen))
            return upscaled, method.capitalize()
    
    def upscale_file(self, input_path, output_path):
        """Single file upscale (legacy, for double-click). Supports animated VTFs."""
        skip, reason = self.should_skip(input_path)
        if skip:
            return True, reason
        
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                in_dir = os.path.join(tmpdir, "input")
                out_dir = os.path.join(tmpdir, "output")
                os.makedirs(in_dir); os.makedirs(out_dir)
                
                bmp_path = os.path.join(in_dir, "img.bmp")
                ok, meta = self.extract_to_bmp(input_path, bmp_path)
                if not ok:
                    return True, f"Skipped ({meta.get('error', 'unknown')})"
                
                ai_scale = self.config.get("ai_scale", 4)
                ai_model = self.config.get("ai_model", "realesrgan-x4plus")
                frame_count = meta.get('frame_count', 1)
                
                # For animated VTFs: remove the duplicate main file (frame 0 is already extracted individually)
                if frame_count > 1 and os.path.exists(bmp_path):
                    os.remove(bmp_path)
                    self._log(f"  📽️ AI upscaling {frame_count} frames with {ai_model}...")
                
                # Quarantine alpha files — AI binary would waste VRAM upscaling them
                alpha_hold = os.path.join(tmpdir, "alpha_hold")
                os.makedirs(alpha_hold, exist_ok=True)
                for af in list(os.listdir(in_dir)):
                    if '_alpha.' in af:
                        shutil.move(os.path.join(in_dir, af), os.path.join(alpha_hold, af))
                
                # Increase timeout proportionally for animated VTFs
                timeout = max(300, frame_count * 60)
                success = self.ai_upscaler.upscale_batch(in_dir, out_dir, scale=ai_scale, model=ai_model, timeout=timeout)
                if not success:
                    return False, "AI upscale failed"
                
                # Restore alpha files for assembly
                for af in os.listdir(alpha_hold):
                    shutil.move(os.path.join(alpha_hold, af), os.path.join(in_dir, af))
                
                if frame_count > 1:
                    # For animated VTFs: find the first frame's AI output
                    out_file = None
                    base_name = os.path.splitext(os.path.basename(bmp_path))[0]  # "img"
                    for ext in ['png', 'bmp', 'webp', 'jpg']:
                        candidate = os.path.join(out_dir, f"{base_name}_frame0.{ext}")
                        if os.path.exists(candidate) and os.path.getsize(candidate) > 256:
                            out_file = candidate
                            break
                    if not out_file:
                        return False, f"AI output for animated frames not found in {out_dir}"
                    return self.assemble_vtf(out_file, output_path, input_path, meta)
                else:
                    # Single-frame: original logic
                    out_file = None
                    for ext in ['bmp', 'png', 'webp', 'jpg']:
                        candidate = os.path.join(out_dir, f"img.{ext}")
                        if os.path.exists(candidate) and os.path.getsize(candidate) > 256:
                            out_file = candidate
                            break
                    if not out_file:
                        for f in os.listdir(out_dir):
                            fp = os.path.join(out_dir, f)
                            if os.path.isfile(fp) and os.path.getsize(fp) > 256:
                                out_file = fp
                                break
                    if not out_file:
                        return False, "AI output missing or corrupted"
                    return self.assemble_vtf(out_file, output_path, input_path, meta)
        except Exception as e:
            return False, str(e)


class VTFUpscalerGUI:
    """Main GUI with AI Upscaling - Performance Optimized v6.0"""
    
    def __init__(self, initial_folder=None, auto_start=False):
        if CTK_AVAILABLE and not DND_AVAILABLE:
            ctk.set_appearance_mode("dark")
            ctk.set_default_color_theme("dark-blue")
            self.root = ctk.CTk()
        elif DND_AVAILABLE:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1800x1000")
        self.root.configure(bg='#0d1117')
        
        self.config = self._load_config()
        self.file_list: List[str] = []
        self._scan_cache = {}  # Reset scan cache
        self.folder_list: List[str] = []
        self.displayed_files: List[str] = []
        self.displayed_folders: List[str] = []
        self.current_file_index = 0
        self.processing = False
        self.cancel_flag = False
        self.original_photo = None
        self._anim_timer = None
        self._anim_frames_orig = []
        self._anim_frames_ai = []
        self._anim_idx = 0
        self._is_playing = False
        self.preview_photo = None
        self.original_pil = None
        
        self._setup_styles()
        self._build_ui()
        self._setup_keyboard_shortcuts()
        self._setup_drag_drop()
        self._check_deps()
        
        # Prevent ThreadPoolExecutor from keeping Python alive after GUI is closed
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
    def _on_close(self):
        """Forcefully shut down, killing any hung batch threads instantly."""
        try:
            self._log("Shutting down... forcing process termination", "warning")
            self.root.destroy()
        except: pass
        import os
        os._exit(0)
        
        if initial_folder and os.path.isdir(initial_folder):
            self.input_entry.insert(0, initial_folder)
            self.root.after(100, self._refresh_files)
            if auto_start:
                self.root.after(500, self._start)
        else:
            last_folder = self.config.get("last_folder", "")
            if last_folder and os.path.isdir(last_folder):
                self.input_entry.insert(0, last_folder)
                self.root.after(100, self._refresh_files)
    
    def _load_config(self) -> dict:
        config = DEFAULT_CONFIG.copy()
        try:
            config_path = SCRIPT_DIR / CONFIG_FILE
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config.update(json.load(f))
        except:
            pass
        return config
    
    def _save_config(self):
        try:
            with open(SCRIPT_DIR / CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
        except:
            pass
    
    def _setup_keyboard_shortcuts(self):
        self.root.bind('<Up>', lambda e: self._prev_file())
        self.root.bind('<Down>', lambda e: self._next_file())
        self.root.bind('<Return>', lambda e: self._upscale_current())
        self.root.bind('<Escape>', lambda e: self._cancel())
        self.root.bind('<Control-a>', lambda e: self._select_all())
    
    def _upscale_current(self):
        if not self.processing:
            fp = self._get_file_from_listbox()
            if fp:
                self._upscale_single(fp)
    
    def _get_file_from_listbox(self, index=None):
        """Resolve a listbox index to the actual file path, accounting for search filter."""
        if index is None:
            selection = self.file_listbox.curselection()
            if not selection:
                return None
            index = selection[0]
        folder_count = len(self.displayed_folders)
        if index < folder_count:
            return None  # It's a folder, not a file
        file_idx = index - folder_count
        if 0 <= file_idx < len(self.displayed_files):
            return self.displayed_files[file_idx]
        return None
    
    def _select_all(self):
        folder_count = len(self.folder_list)
        self.file_listbox.selection_set(folder_count, tk.END)
    
    def _get_current_model(self) -> str:
        """Get the actual model name from the combobox (strips display text)."""
        display = self.model_var.get()
        if '(' in display:
            return display.split('(')[0].strip()
        return display
    
    def _on_model_select(self, event=None):
        display = self.model_var.get()
        if '(' in display:
            self.model_var.set(display.split('(')[0].strip())
    
    def _setup_drag_drop(self):
        if not DND_AVAILABLE:
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self._on_drop)
        except:
            pass
    
    def _on_drop(self, event):
        data = event.data
        if data.startswith('{'):
            paths = [p.strip('{}') for p in data.split('} {')]
        else:
            paths = data.split()
        if paths:
            first_path = paths[0]
            if os.path.isdir(first_path):
                self.input_entry.delete(0, tk.END)
                self.input_entry.insert(0, first_path)
                self._refresh_files()
            elif first_path.lower().endswith('.vtf'):
                self.input_entry.delete(0, tk.END)
                self.input_entry.insert(0, os.path.dirname(first_path))
                self._refresh_files()
    
    def _setup_styles(self):
        # ── Colors (GitHub Dark + Gaming accents) ───────────────────
        self.colors = {
            'bg':      '#0d1117',   # Base background
            'card':    '#161b22',   # Card/panel background
            'input':   '#21262d',   # Input fields
            'hover':   '#30363d',   # Hover state
            'border':  '#30363d',   # Borders
            'accent':  '#58a6ff',   # Primary (cyan-blue)
            'green':   '#3fb950',   # AI/success
            'purple':  '#bc8cff',   # Tools
            'red':     '#f85149',   # Danger
            'amber':   '#d29922',   # Warning
            'text':    '#e6edf3',   # Primary text
            'dim':     '#8b949e',   # Secondary text
            'muted':   '#484f58',   # Very dim
            # Legacy aliases
            'med': '#161b22', 'light': '#21262d',
        }
        
        # TTK styles for widgets that still use ttk
        style = ttk.Style()
        style.theme_use('clam')
        c = self.colors
        style.configure('Dark.TFrame', background=c['bg'])
        style.configure('Med.TFrame', background=c['card'])
        style.configure('Dark.TLabel', background=c['bg'], foreground=c['text'], font=('Segoe UI', 10))
        style.configure('Title.TLabel', background=c['bg'], foreground=c['text'], font=('Segoe UI', 16, 'bold'))
        style.configure('Preview.TLabel', background=c['card'], foreground=c['text'], font=('Segoe UI', 11, 'bold'))
        style.configure('Dim.TLabel', background=c['bg'], foreground=c['dim'], font=('Segoe UI', 9))
        style.configure('AI.TLabel', background=c['bg'], foreground=c['green'], font=('Segoe UI', 10, 'bold'))

    def _build_ui(self):
        c = self.colors
        self.root.configure(bg=c['bg'])
        
        # ════════════════════════════════════════════════════════════
        # TOP HEADER BAR
        # ════════════════════════════════════════════════════════════
        header = ctk.CTkFrame(self.root, fg_color=c['card'], height=48, corner_radius=0) if CTK_AVAILABLE else tk.Frame(self.root, bg=c['card'], height=48)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        # App title
        if CTK_AVAILABLE:
            ctk.CTkLabel(header, text="🚀 VTF AI Upscaler", font=('Segoe UI', 17, 'bold'),
                         text_color=c['text']).pack(side=tk.LEFT, padx=15)
        else:
            tk.Label(header, text="🚀 VTF AI Upscaler", bg=c['card'], fg=c['text'],
                     font=('Segoe UI', 15, 'bold')).pack(side=tk.LEFT, padx=15)
        
        # GPU + VRAM (centered in header)
        if PYNVML_AVAILABLE:
            center_frame = tk.Frame(header, bg=c['card'])
            center_frame.pack(side=tk.LEFT, expand=True)
            try:
                import pynvml as _pnv
                _pnv.nvmlInit()
                _h = _pnv.nvmlDeviceGetHandleByIndex(0)
                gpu_name = _pnv.nvmlDeviceGetName(_h)
                if isinstance(gpu_name, bytes): gpu_name = gpu_name.decode()
                tk.Label(center_frame, text=f"GPU  •  {gpu_name}", bg=c['card'], fg=c['dim'],
                         font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=(0, 15))
            except Exception:
                pass
            self.vram_label = tk.Label(center_frame, text="VRAM: ---", bg=c['card'], fg=c['accent'],
                                       font=('Consolas', 10, 'bold'))
            self.vram_label.pack(side=tk.LEFT, padx=5)
        
        # AI status (hidden in header, used internally for tracking)
        self.ai_status = tk.Label(header, text="", bg=c['card'], fg=c['green'],
                                  font=('Segoe UI', 10, 'bold'))
        # Not packed — status shown in log only
        
        # Thin separator
        tk.Frame(self.root, bg=c['border'], height=1).pack(fill=tk.X)
        
        # ════════════════════════════════════════════════════════════
        # MAIN CONTENT
        # ════════════════════════════════════════════════════════════
        content = tk.Frame(self.root, bg=c['bg'])
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        content.columnconfigure(0, weight=35, minsize=400)
        content.columnconfigure(1, weight=65, minsize=600)
        content.rowconfigure(0, weight=1)
        
        # ──────────────────────────────────────────────────────────
        # LEFT PANEL
        # ──────────────────────────────────────────────────────────
        left = tk.Frame(content, bg=c['bg'])
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 5))
        
        # ── Path Input (rounded card) ──
        if CTK_AVAILABLE:
            path_card = ctk.CTkFrame(left, fg_color=c['card'], corner_radius=10)
            path_card.pack(fill=tk.X, pady=(0, 6))
            path_inner = tk.Frame(path_card, bg=c['card'])
            path_inner.pack(fill=tk.X, padx=10, pady=8)
            ctk.CTkLabel(path_inner, text="📂", font=('Segoe UI', 11), text_color=c['dim']).pack(side=tk.LEFT)
            self.input_entry = ctk.CTkEntry(path_inner, fg_color=c['input'], text_color=c['text'],
                                            border_color=c['border'], corner_radius=6,
                                            font=('Segoe UI', 11), height=34)
            self.input_entry.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)
            ctk.CTkButton(path_inner, text="📄", command=self._browse_file, width=32, height=28,
                          fg_color=c['input'], hover_color=c['hover'], text_color=c['dim'],
                          corner_radius=6, font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=1)
            ctk.CTkButton(path_inner, text="📁", command=self._browse_folder, width=32, height=28,
                          fg_color=c['input'], hover_color=c['hover'], text_color=c['dim'],
                          corner_radius=6, font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=1)
        else:
            path_card = tk.Frame(left, bg=c['card'])
            path_card.pack(fill=tk.X, pady=(0, 6))
            self.input_entry = tk.Entry(path_card, bg=c['input'], fg=c['text'], relief='flat', font=('Segoe UI', 9))
            self.input_entry.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True, ipady=4)
            tk.Button(path_card, text="📄", command=self._browse_file, bg=c['input'], fg=c['dim'], relief='flat').pack(side=tk.LEFT, padx=1)
            tk.Button(path_card, text="📁", command=self._browse_folder, bg=c['input'], fg=c['dim'], relief='flat').pack(side=tk.LEFT, padx=1)
        
        # ── File Browser (rounded card) ──
        if CTK_AVAILABLE:
            browser_card = ctk.CTkFrame(left, fg_color=c['card'], corner_radius=10)
            browser_card.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        else:
            browser_card = tk.Frame(left, bg=c['card'])
            browser_card.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        
        browser_inner = tk.Frame(browser_card, bg=c['card'])
        browser_inner.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        
        # Search bar
        search_row = tk.Frame(browser_inner, bg=c['card'])
        search_row.pack(fill=tk.X, pady=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', self._on_search)
        self.anim_filter_var = tk.BooleanVar(value=False)
        if CTK_AVAILABLE:
            self.search_entry = ctk.CTkEntry(search_row, textvariable=self.search_var,
                                              fg_color=c['input'], text_color=c['text'],
                                              border_color=c['border'], corner_radius=6,
                                              placeholder_text="Search files...", height=30,
                                              font=('Segoe UI', 10))
            self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
            ctk.CTkButton(search_row, text="✕", command=lambda: self.search_var.set(""),
                          width=28, height=28, fg_color=c['input'], hover_color=c['hover'],
                          text_color=c['dim'], corner_radius=6).pack(side=tk.LEFT)
            ctk.CTkCheckBox(search_row, text="Anim Only", variable=self.anim_filter_var, 
                            command=self._on_search, text_color=c['text'], 
                            font=('Segoe UI', 10), width=60, checkbox_width=18, checkbox_height=18).pack(side=tk.LEFT, padx=(6, 0))
        else:
            tk.Label(search_row, text="🔍", bg=c['card'], fg=c['dim']).pack(side=tk.LEFT)
            self.search_entry = tk.Entry(search_row, textvariable=self.search_var, bg=c['input'],
                                         fg=c['text'], relief='flat', font=('Segoe UI', 9))
            self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, ipady=2)
            tk.Checkbutton(search_row, text="Anim Only", variable=self.anim_filter_var, 
                           command=self._on_search, bg=c['card'], fg=c['text'], selectcolor=c['input']).pack(side=tk.LEFT, padx=4)
        
        # Listbox (no CTk equivalent — use standard tk)
        listbox_container = tk.Frame(browser_inner, bg=c['input'])
        listbox_container.pack(fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(listbox_container, bg=c['input'], troughcolor=c['card'])
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_listbox = tk.Listbox(listbox_container, bg=c['input'], fg=c['text'],
                                       font=('Consolas', 10), relief='flat', height=10,
                                       selectbackground=c['accent'], selectforeground='white',
                                       selectmode=tk.EXTENDED, yscrollcommand=scrollbar.set,
                                       highlightthickness=0, borderwidth=0, activestyle='none')
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=self.file_listbox.yview)
        self.file_listbox.bind('<<ListboxSelect>>', self._on_file_select)
        self.file_listbox.bind('<ButtonRelease-1>', self._on_click_select)
        self.file_listbox.bind('<Button-3>', self._show_context_menu)
        self.file_listbox.bind('<Double-Button-1>', self._on_double_click)
        self._folder_color = '#e8a838'  # Orange for folder names
        self.context_menu = tk.Menu(self.root, tearoff=0, bg=c['card'], fg=c['text'])
        
        # ── Tabbed Settings (rounded card) ──
        if CTK_AVAILABLE:
            tabview = ctk.CTkTabview(left, fg_color=c['card'], corner_radius=10, height=140,
                                     segmented_button_fg_color=c['input'],
                                     segmented_button_selected_color=c['accent'],
                                     segmented_button_selected_hover_color='#4090d0',
                                     segmented_button_unselected_color=c['input'],
                                     segmented_button_unselected_hover_color=c['hover'])
            tabview.pack(fill=tk.X, pady=(0, 6))
            tab_settings = tabview.add("⚙ Settings")
            tab_advanced = tabview.add("🔧 Advanced")
            tab_settings.configure(fg_color=c['card'])
            tab_advanced.configure(fg_color=c['card'])
        else:
            tabview = ttk.Notebook(left)
            tabview.pack(fill=tk.X, pady=(0, 6))
            tab_settings = tk.Frame(tabview, bg=c['card'])
            tab_advanced = tk.Frame(tabview, bg=c['card'])
            tabview.add(tab_settings, text='  ⚙ Settings  ')
            tabview.add(tab_advanced, text='  🔧 Advanced  ')
        
        # --- Settings Tab ---
        # Row 1: Target + Format + Scale
        row1 = tk.Frame(tab_settings, bg=c['card'])
        row1.pack(fill=tk.X, pady=3, padx=5)
        lbl_s = {'bg': c['card'], 'fg': c['dim'], 'font': ('Segoe UI', 12)}
        tk.Label(row1, text="Target:", **lbl_s).pack(side=tk.LEFT)
        self.res_var = tk.StringVar(value=str(self.config.get("target_resolution", 2048)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(row1, variable=self.res_var, values=["1024", "2048", "4096"],
                              width=75, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=(4, 10))
        else:
            ttk.Combobox(row1, textvariable=self.res_var, values=["1024", "2048", "4096"], width=5, state='readonly').pack(side=tk.LEFT, padx=(4, 10))
        
        # Format always "Auto (Match Source)" — removed dropdown to prevent user error
        self.format_var = tk.StringVar(value="Auto (Match Source)")
        
        tk.Label(row1, text="Scale:", **lbl_s).pack(side=tk.LEFT)
        self.scale_var = tk.StringVar(value=str(self.config.get("ai_scale", 4)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(row1, variable=self.scale_var, values=["2", "3", "4", "8"],
                              width=60, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=4)
        else:
            ttk.Combobox(row1, textvariable=self.scale_var, values=["2", "3", "4", "8"], width=3, state='readonly').pack(side=tk.LEFT, padx=4)
        self.method_var = tk.StringVar(value="ai")
        
        # Row 2: Model
        row2 = tk.Frame(tab_settings, bg=c['card'])
        row2.pack(fill=tk.X, pady=3, padx=5)
        tk.Label(row2, text="Model:", **lbl_s).pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value=self.config.get("ai_model", "realesrgan-x4plus"))
        if CTK_AVAILABLE:
            model_menu = ctk.CTkOptionMenu(row2, variable=self.model_var,
                         values=["realesrgan-x4plus", "realesrgan-x4plus-anime",
                                 "realesr-animevideov3", "realesrnet-x4plus"],
                         width=200, height=32, fg_color=c['input'], button_color=c['hover'],
                         button_hover_color=c['accent'], corner_radius=6,
                         font=('Segoe UI', 11), command=lambda v: None)
            model_menu.pack(side=tk.LEFT, padx=4)
        else:
            model_combo = ttk.Combobox(row2, textvariable=self.model_var,
                         values=["realesrgan-x4plus (Default - Best Quality)",
                                 "realesrgan-x4plus-anime (Anime/Toon)",
                                 "realesr-animevideov3 (Fast)",
                                 "realesrnet-x4plus (Fastest)"], width=32, state='readonly')
            model_combo.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
            model_combo.bind('<<ComboboxSelected>>', self._on_model_select)
        
        # Row 3: Quality
        row3 = tk.Frame(tab_settings, bg=c['card'])
        row3.pack(fill=tk.X, pady=3, padx=5)
        tk.Label(row3, text="Sharpen:", **lbl_s).pack(side=tk.LEFT)
        self.sharpen_var = tk.StringVar(value=str(self.config.get("sharpen_strength", 0.0)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(row3, variable=self.sharpen_var,
                              values=["0.0", "0.25", "0.5", "0.75", "1.0", "1.5", "2.0"],
                              width=70, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=(4, 10))
        else:
            ttk.Combobox(row3, textvariable=self.sharpen_var, values=["0.0", "0.25", "0.5", "0.75", "1.0", "1.5", "2.0"], width=5, state='readonly').pack(side=tk.LEFT, padx=(4, 10))
        
        tk.Label(row3, text="Denoise (AnimeVideo only):", **lbl_s).pack(side=tk.LEFT)
        self.denoise_var = tk.StringVar(value=str(self.config.get("denoise_strength", 0)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(row3, variable=self.denoise_var, values=["0", "1", "2", "3"],
                              width=60, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=4)
        else:
            ttk.Combobox(row3, textvariable=self.denoise_var, values=["0", "1", "2", "3"], width=3, state='readonly').pack(side=tk.LEFT, padx=4)
        
        # Row 4: Toggles (CTk switches or tk checkbuttons)
        row4 = tk.Frame(tab_settings, bg=c['card'])
        row4.pack(fill=tk.X, pady=3, padx=5)
        self.mipmap_var = tk.BooleanVar(value=self.config.get("generate_mipmaps", True))
        self.recursive_var = tk.BooleanVar(value=self.config.get("recursive_search", True))
        self.backup_var = tk.BooleanVar(value=self.config.get("backup_originals", True))
        self.addon_output_var = tk.BooleanVar(value=self.config.get("output_to_addon", True))
        
        if CTK_AVAILABLE:
            sw_kw = {'width': 40, 'height': 20, 'fg_color': c['input'], 'progress_color': c['accent'],
                     'button_color': c['dim'], 'button_hover_color': c['text'], 'font': ('Segoe UI', 10)}
            ctk.CTkSwitch(row4, text="Mipmaps", variable=self.mipmap_var, text_color=c['text'], **sw_kw).pack(side=tk.LEFT, padx=(0, 8))
            ctk.CTkSwitch(row4, text="Subfolders", variable=self.recursive_var, text_color=c['text'], **sw_kw).pack(side=tk.LEFT, padx=8)
            ctk.CTkSwitch(row4, text="Backup", variable=self.backup_var, text_color=c['text'], **sw_kw).pack(side=tk.LEFT, padx=8)
            ctk.CTkSwitch(row4, text="Addon Output", variable=self.addon_output_var, text_color=c['green'],
                          progress_color=c['green'], **{k: v for k, v in sw_kw.items() if k != 'progress_color'}).pack(side=tk.LEFT, padx=8)
        else:
            cb_kw = {'bg': c['card'], 'fg': c['text'], 'selectcolor': c['input'], 'font': ('Segoe UI', 9), 'activebackground': c['card']}
            tk.Checkbutton(row4, text="Mipmaps", variable=self.mipmap_var, **cb_kw).pack(side=tk.LEFT, padx=(0, 6))
            tk.Checkbutton(row4, text="Subfolders", variable=self.recursive_var, **cb_kw).pack(side=tk.LEFT, padx=6)
            tk.Checkbutton(row4, text="Backup", variable=self.backup_var, **cb_kw).pack(side=tk.LEFT, padx=6)
            tk.Checkbutton(row4, text="Addon Output", variable=self.addon_output_var, bg=c['card'], fg=c['green'], selectcolor=c['input'], font=('Segoe UI', 9), activebackground=c['card']).pack(side=tk.LEFT, padx=6)
        
        # --- Advanced Tab ---
        adv_row1 = tk.Frame(tab_advanced, bg=c['card'])
        adv_row1.pack(fill=tk.X, pady=3, padx=5)
        lbl_a = {'bg': c['card'], 'fg': c['dim'], 'font': ('Segoe UI', 12)}
        tk.Label(adv_row1, text="Batch:", **lbl_a).pack(side=tk.LEFT)
        self.batch_var = tk.StringVar(value=str(self.config.get("batch_size", 100)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(adv_row1, variable=self.batch_var,
                              values=["10", "25", "50", "100", "150", "200"],
                              width=70, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=(4, 10))
        else:
            ttk.Combobox(adv_row1, textvariable=self.batch_var, values=["10", "25", "50", "100", "150", "200"], width=4, state='readonly').pack(side=tk.LEFT, padx=(4, 10))
        
        tk.Label(adv_row1, text="Tile:", **lbl_a).pack(side=tk.LEFT)
        self.tile_var = tk.StringVar(value=str(self.config.get("tile_size", 0)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(adv_row1, variable=self.tile_var,
                              values=["0 (Auto)", "128", "256", "512", "1024"],
                              width=90, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=(4, 10))
        else:
            ttk.Combobox(adv_row1, textvariable=self.tile_var, values=["0 (Auto)", "128", "256", "512", "1024"], width=8, state='readonly').pack(side=tk.LEFT, padx=(4, 10))
        
        tk.Label(adv_row1, text="CPU:", **lbl_a).pack(side=tk.LEFT)
        self.cpu_workers_var = tk.StringVar(value=str(self.config.get("cpu_workers", 4)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(adv_row1, variable=self.cpu_workers_var, values=["2", "4", "6", "8"],
                              width=55, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=4)
        else:
            ttk.Combobox(adv_row1, textvariable=self.cpu_workers_var, values=["2", "4", "6", "8"], width=3, state='readonly').pack(side=tk.LEFT, padx=4)
        
        adv_row2 = tk.Frame(tab_advanced, bg=c['card'])
        adv_row2.pack(fill=tk.X, pady=3, padx=5)
        tk.Label(adv_row2, text="GPU ID:", **lbl_a).pack(side=tk.LEFT)
        self.gpu_var = tk.StringVar(value=str(self.config.get("gpu_id", 0)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(adv_row2, variable=self.gpu_var, values=["0", "1", "2", "3"],
                              width=60, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=(4, 10))
        else:
            gpu_combo = ttk.Combobox(adv_row2, textvariable=self.gpu_var, values=["0 (Primary)", "1", "2", "3"], width=10, state='readonly')
            gpu_combo.pack(side=tk.LEFT, padx=(4, 10))
            gpu_combo.bind('<<ComboboxSelected>>', lambda e: self.gpu_var.set(self.gpu_var.get().split()[0]))
        
        tk.Label(adv_row2, text="Skip <px:", **lbl_a).pack(side=tk.LEFT)
        self.skip_small_var = tk.StringVar(value=str(self.config.get("skip_small", 64)))
        if CTK_AVAILABLE:
            ctk.CTkOptionMenu(adv_row2, variable=self.skip_small_var,
                              values=["0", "32", "64", "128", "256"],
                              width=70, height=28, fg_color=c['input'], button_color=c['hover'],
                              button_hover_color=c['accent'], corner_radius=6,
                              font=('Segoe UI', 12)).pack(side=tk.LEFT, padx=4)
        else:
            skip_combo = ttk.Combobox(adv_row2, textvariable=self.skip_small_var, values=["0 (None)", "32", "64", "128", "256"], width=8, state='readonly')
            skip_combo.pack(side=tk.LEFT, padx=4)
            skip_combo.bind('<<ComboboxSelected>>', lambda e: self.skip_small_var.set(self.skip_small_var.get().split()[0]))
        self.tta_var = tk.BooleanVar(value=False)
        
        # ── Action Buttons (rounded, single row) ──
        btn_frame = tk.Frame(left, bg=c['bg'])
        btn_frame.pack(fill=tk.X, pady=(0, 6))
        
        if CTK_AVAILABLE:
            self.start_btn = ctk.CTkButton(btn_frame, text="🚀 Start", command=self._start,
                                           fg_color=c['accent'], hover_color='#4090d0', text_color='white',
                                           font=('Segoe UI', 13, 'bold'), corner_radius=10,
                                           width=110, height=42)
            self.start_btn.pack(side=tk.LEFT, padx=(0, 3))
            self.cancel_btn = ctk.CTkButton(btn_frame, text="⏹", command=self._cancel,
                                            fg_color=c['red'], hover_color='#d03030', text_color='white',
                                            font=('Segoe UI', 13), corner_radius=10,
                                            width=42, height=42, state='disabled')
            self.cancel_btn.pack(side=tk.LEFT, padx=2)
            
            # Separator
            tk.Frame(btn_frame, bg=c['border'], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
            
            ubtn = {'height': 36, 'corner_radius': 8, 'fg_color': c['input'],
                    'hover_color': c['hover'], 'text_color': c['dim'], 'font': ('Segoe UI', 11)}
            self.cache_btn = ctk.CTkButton(btn_frame, text="🔍 Scan", command=self._prescan_cache, width=60, **ubtn)
            self.cache_btn.pack(side=tk.LEFT, padx=1)
            self.restore_btn = ctk.CTkButton(btn_frame, text="♻ Restore", command=self._restore_all_backups,
                                             fg_color=c['purple'], hover_color='#9070d0', text_color='white',
                                             font=('Segoe UI', 11, 'bold'), corner_radius=8, width=90, height=36)
            self.restore_btn.pack(side=tk.LEFT, padx=1)
            self.fix_alpha_btn = ctk.CTkButton(btn_frame, text="🩹 Alpha", command=self._fix_broken_alpha, width=60, **ubtn)
            self.fix_alpha_btn.pack(side=tk.LEFT, padx=1)
            self.reset_history_btn = ctk.CTkButton(btn_frame, text="🔄", command=self._reset_history, width=30, **ubtn)
            self.reset_history_btn.pack(side=tk.LEFT, padx=1)
            
            # Right-side cleanup
            self.flush_btn = ctk.CTkButton(btn_frame, text="⚡ Flush", command=self._flush_all,
                                           fg_color=c['red'], hover_color='#d03030', text_color='white',
                                           font=('Segoe UI', 11, 'bold'), corner_radius=8, width=75, height=36)
            self.flush_btn.pack(side=tk.RIGHT, padx=1)
            self.clear_vram_btn = ctk.CTkButton(btn_frame, text="VRAM", command=self._clear_vram, width=60, **ubtn)
            self.clear_vram_btn.pack(side=tk.RIGHT, padx=1)
            self.clear_ram_btn = ctk.CTkButton(btn_frame, text="RAM", command=self._clear_ram, width=55, **ubtn)
            self.clear_ram_btn.pack(side=tk.RIGHT, padx=1)
        else:
            btn_kw = {'relief': 'flat', 'padx': 8, 'pady': 4, 'cursor': 'hand2'}
            self.start_btn = tk.Button(btn_frame, text="🚀 Start", command=self._start, bg=c['accent'], fg='white', font=('Segoe UI', 10, 'bold'), **btn_kw)
            self.start_btn.pack(side=tk.LEFT, padx=2)
            self.cancel_btn = tk.Button(btn_frame, text="⏹", command=self._cancel, bg=c['red'], fg='white', state=tk.DISABLED, **btn_kw)
            self.cancel_btn.pack(side=tk.LEFT, padx=2)
            self.cache_btn = tk.Button(btn_frame, text="🔍 Scan", command=self._prescan_cache, bg=c['input'], fg=c['dim'], **btn_kw)
            self.cache_btn.pack(side=tk.LEFT, padx=1)
            self.restore_btn = tk.Button(btn_frame, text="♻ Restore", command=self._restore_all_backups, bg=c['purple'], fg='white', **btn_kw)
            self.restore_btn.pack(side=tk.LEFT, padx=1)
            self.fix_alpha_btn = tk.Button(btn_frame, text="🩹 Alpha", command=self._fix_broken_alpha, bg=c['input'], fg=c['dim'], **btn_kw)
            self.fix_alpha_btn.pack(side=tk.LEFT, padx=1)
            self.reset_history_btn = tk.Button(btn_frame, text="🔄", command=self._reset_history, bg=c['input'], fg=c['dim'], **btn_kw)
            self.reset_history_btn.pack(side=tk.LEFT, padx=1)
            self.flush_btn = tk.Button(btn_frame, text="⚡ Flush", command=self._flush_all, bg=c['red'], fg='white', **btn_kw)
            self.flush_btn.pack(side=tk.RIGHT, padx=1)
            self.clear_vram_btn = tk.Button(btn_frame, text="VRAM", command=self._clear_vram, bg=c['input'], fg=c['dim'], **btn_kw)
            self.clear_vram_btn.pack(side=tk.RIGHT, padx=1)
            self.clear_ram_btn = tk.Button(btn_frame, text="RAM", command=self._clear_ram, bg=c['input'], fg=c['dim'], **btn_kw)
            self.clear_ram_btn.pack(side=tk.RIGHT, padx=1)
        
        # ── Status (inline, compact) ──
        status_frame = tk.Frame(left, bg=c['bg'])
        status_frame.pack(fill=tk.X, pady=(0, 3))
        self.status_label = tk.Label(status_frame, text="Ready", bg=c['bg'], fg=c['dim'],
                                     font=('Segoe UI', 10), anchor='w')
        self.status_label.pack(side=tk.LEFT)
        self.eta_label = tk.Label(status_frame, text="", bg=c['bg'], fg=c['dim'],
                                  font=('Consolas', 10), anchor='e')
        self.eta_label.pack(side=tk.RIGHT)
        self.progress_var = tk.DoubleVar()
        
        # ── Log Panel (rounded card) ──
        if CTK_AVAILABLE:
            log_card = ctk.CTkFrame(left, fg_color=c['card'], corner_radius=10)
            log_card.pack(fill=tk.BOTH, expand=False)
        else:
            log_card = tk.Frame(left, bg=c['card'])
            log_card.pack(fill=tk.BOTH, expand=False)
        log_inner = tk.Frame(log_card, bg=c['card'])
        log_inner.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        log_scroll = tk.Scrollbar(log_inner, bg=c['card'], troughcolor=c['bg'])
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = tk.Text(log_inner, height=7, bg=c['card'], fg=c['text'],
                                font=('Consolas', 10), relief='flat', borderwidth=0,
                                highlightthickness=0, yscrollcommand=log_scroll.set, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        log_scroll.configure(command=self.log_text.yview)
        self.log_text.tag_configure('success', foreground=c['green'])
        self.log_text.tag_configure('error', foreground=c['red'])
        self.log_text.tag_configure('warning', foreground=c['amber'])
        self.log_text.tag_configure('ai', foreground=c['accent'])
        
        # ──────────────────────────────────────────────────────────
        # RIGHT PANEL — Preview (rounded card)
        # ──────────────────────────────────────────────────────────
        if CTK_AVAILABLE:
            right = ctk.CTkFrame(content, fg_color=c['card'], corner_radius=10)
        else:
            right = tk.Frame(content, bg=c['card'])
        right.grid(row=0, column=1, sticky='nsew', padx=(5, 0))
        
        # Preview header + nav
        preview_header = tk.Frame(right, bg=c['card'])
        preview_header.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(preview_header, text="📷 Preview Comparison", bg=c['card'], fg=c['text'],
                 font=('Segoe UI', 14, 'bold')).pack(side=tk.LEFT)
        
        nav_frame = tk.Frame(preview_header, bg=c['card'])
        nav_frame.pack(side=tk.RIGHT)
        if CTK_AVAILABLE:
            ctk.CTkButton(nav_frame, text="◀ Prev", command=self._prev_file, width=70, height=30,
                          fg_color=c['input'], hover_color=c['hover'], text_color=c['text'],
                          corner_radius=6, font=('Segoe UI', 11)).pack(side=tk.LEFT, padx=2)
            ctk.CTkButton(nav_frame, text="Next ▶", command=self._next_file, width=70, height=30,
                          fg_color=c['input'], hover_color=c['hover'], text_color=c['text'],
                          corner_radius=6, font=('Segoe UI', 11)).pack(side=tk.LEFT, padx=2)
            ctk.CTkButton(nav_frame, text="🔄 Preview AI", command=self._preview_ai, width=110, height=30,
                          fg_color=c['green'], hover_color='#2d8a3e', text_color='#0d1117',
                          corner_radius=6, font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT, padx=(8, 2))
            ctk.CTkButton(nav_frame, text="🔍 Compare", command=self._compare_mode, width=100, height=30,
                          fg_color=c['purple'], hover_color='#9070d0', text_color='white',
                          corner_radius=6, font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT, padx=2)
        else:
            nav_kw = {'bg': c['input'], 'fg': c['text'], 'relief': 'flat', 'font': ('Segoe UI', 9), 'cursor': 'hand2', 'padx': 8, 'pady': 3}
            tk.Button(nav_frame, text="◀ Prev", command=self._prev_file, **nav_kw).pack(side=tk.LEFT, padx=2)
            tk.Button(nav_frame, text="Next ▶", command=self._next_file, **nav_kw).pack(side=tk.LEFT, padx=2)
            tk.Button(nav_frame, text="🔄 Preview AI", command=self._preview_ai, bg=c['green'], fg='#0d1117', relief='flat', font=('Segoe UI', 9, 'bold'), cursor='hand2', padx=8, pady=3).pack(side=tk.LEFT, padx=(8, 2))
            tk.Button(nav_frame, text="🔍 Compare", command=self._compare_mode, bg=c['purple'], fg='white', relief='flat', font=('Segoe UI', 9, 'bold'), cursor='hand2', padx=8, pady=3).pack(side=tk.LEFT, padx=2)
        
        # Preview canvases (large, expandable)
        preview_container = tk.Frame(right, bg=c['card'])
        preview_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        preview_container.columnconfigure(0, weight=1)
        preview_container.columnconfigure(1, weight=1)
        preview_container.rowconfigure(1, weight=1)
        
        self.orig_info_label = tk.Label(preview_container, text="Original", bg=c['card'],
                                        fg=c['dim'], font=('Segoe UI', 10))
        self.orig_info_label.grid(row=0, column=0, pady=(0, 4))
        self.original_canvas = tk.Canvas(preview_container, width=PREVIEW_SIZE, height=PREVIEW_SIZE,
                                          bg=c['input'], highlightthickness=2, highlightbackground=c['border'])
        self.original_canvas.grid(row=1, column=0, padx=(0, 6), sticky='nsew')
        
        self.preview_info_label = tk.Label(preview_container, text="AI Enhanced", bg=c['card'],
                                           fg=c['green'], font=('Segoe UI', 10, 'bold'))
        self.preview_info_label.grid(row=0, column=1, pady=(0, 4))
        self.preview_canvas = tk.Canvas(preview_container, width=PREVIEW_SIZE, height=PREVIEW_SIZE,
                                         bg=c['input'], highlightthickness=2, highlightbackground=c['green'])
        self.preview_canvas.grid(row=1, column=1, padx=(6, 0), sticky='nsew')
        
        # ── Animation Controls (Hidden by default) ──
        self.anim_control_frame = tk.Frame(preview_container, bg=c['card'])
        self.anim_control_frame.grid(row=2, column=0, columnspan=2, pady=(8, 0), sticky='ew')
        self.anim_control_frame.grid_remove()  # Hide initially
        
        if CTK_AVAILABLE:
            self.anim_play_btn = ctk.CTkButton(self.anim_control_frame, text="⏸ Pause", width=70, height=28,
                                               fg_color=c['accent'], hover_color='#2c7ab2',
                                               command=self._toggle_animation)
            self.anim_play_btn.pack(side=tk.LEFT, padx=5)
            self.anim_slider = ctk.CTkSlider(self.anim_control_frame, from_=0, to=1, number_of_steps=1,
                                             command=self._on_anim_scrub, button_color=c['accent'])
            self.anim_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        else:
            self.anim_play_btn = tk.Button(self.anim_control_frame, text="⏸ Pause", command=self._toggle_animation,
                                           bg=c['accent'], fg='white', relief='flat')
            self.anim_play_btn.pack(side=tk.LEFT, padx=5)
            self.anim_slider = ttk.Scale(self.anim_control_frame, from_=0, to=1, command=self._on_anim_scrub)
            self.anim_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
            
        self.anim_frame_label = tk.Label(self.anim_control_frame, text="0 / 0", bg=c['card'], fg=c['dim'], font=('Consolas', 10))
        self.anim_frame_label.pack(side=tk.RIGHT, padx=5)
        
        # ════════════════════════════════════════════════════════════
        # BOTTOM PROGRESS BAR (full width, like concept)
        # ════════════════════════════════════════════════════════════
        if CTK_AVAILABLE:
            bottom_bar = ctk.CTkFrame(self.root, fg_color=c['card'], height=42, corner_radius=0)
            bottom_bar.pack(fill=tk.X, side=tk.BOTTOM)
            bottom_bar.pack_propagate(False)
            # Gradient-style progress bar (purple → cyan)
            self._progress_bar = ctk.CTkProgressBar(bottom_bar, variable=self.progress_var, height=14,
                                          fg_color=c['input'], progress_color='#7c3aed',
                                          corner_radius=6)
            self._progress_bar.pack(fill=tk.X, side=tk.TOP, padx=12, pady=(6, 2))
            self._progress_bar.set(0)
            # Bottom info: percentage | ETA | count | speed
            info_frame = tk.Frame(bottom_bar, bg=c['card'])
            info_frame.pack(fill=tk.X, expand=True, padx=10)
            self.bottom_pct = tk.Label(info_frame, text="", bg=c['card'], fg=c['green'],
                                       font=('Segoe UI', 11, 'bold'), anchor='w')
            self.bottom_pct.pack(side=tk.LEFT, padx=(0, 10))
            self.bottom_eta = tk.Label(info_frame, text="", bg=c['card'], fg=c['dim'],
                                       font=('Segoe UI', 10), anchor='w')
            self.bottom_eta.pack(side=tk.LEFT, padx=10)
            self.bottom_count = tk.Label(info_frame, text="", bg=c['card'], fg=c['dim'],
                                         font=('Segoe UI', 10))
            self.bottom_count.pack(side=tk.RIGHT, padx=10)
            self.bottom_speed = tk.Label(info_frame, text="", bg=c['card'], fg=c['accent'],
                                         font=('Segoe UI', 10))
            self.bottom_speed.pack(side=tk.RIGHT, padx=5)
        else:
            ttk.Progressbar(self.root, variable=self.progress_var, maximum=100).pack(fill=tk.X, side=tk.BOTTOM)
        
        # ── Magnifier Zoom Slider ──
        mag_row = tk.Frame(right, bg=c['card'])
        mag_row.pack(fill=tk.X, padx=12, pady=(0, 4))
        tk.Label(mag_row, text="🔍 Zoom:", bg=c['card'], fg=c['dim'],
                 font=('Segoe UI', 10)).pack(side=tk.LEFT)
        self._mag_zoom_var = tk.DoubleVar(value=2.0)
        self._mag_zoom_label = tk.Label(mag_row, text="2.0x", bg=c['card'], fg=c['accent'],
                                        font=('Consolas', 10, 'bold'))
        self._mag_zoom_label.pack(side=tk.RIGHT, padx=(6, 0))
        
        def _on_zoom_change(val):
            z = float(val)
            self._mag_zoom = z
            self._mag_zoom_label.configure(text=f"{z:.1f}x")
        
        if CTK_AVAILABLE:
            mag_slider = ctk.CTkSlider(mag_row, from_=1.0, to=5.0, number_of_steps=16,
                                        variable=self._mag_zoom_var, command=_on_zoom_change,
                                        width=200, height=16,
                                        fg_color=c['input'], progress_color=c['accent'],
                                        button_color=c['accent'], button_hover_color='#4090d0')
            mag_slider.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        else:
            tk.Scale(mag_row, from_=1.0, to=5.0, resolution=0.25, orient=tk.HORIZONTAL,
                     variable=self._mag_zoom_var, command=_on_zoom_change,
                     bg=c['card'], fg=c['text'], troughcolor=c['input'],
                     highlightthickness=0, showvalue=False).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        
        # ════════════════════════════════════════════════════════════
        # PREVIEW MAGNIFIER (circular zoom on hover)
        # ════════════════════════════════════════════════════════════
        self._mag_size = 150  # Magnifier diameter
        self._mag_zoom = 2.0  # Initial zoom factor
        self._mag_canvas = None
        self._mag_active = False
        
        def _setup_magnifier(canvas, ref_name):
            def on_enter(e):
                self._mag_active = True
            def on_leave(e):
                self._mag_active = False
                canvas.delete("magnifier")
                canvas.delete("mag_border")
            def on_motion(e):
                if not self._mag_active:
                    return
                # Get the source photo
                photo_ref = self.original_photo if ref_name == "original" else self.preview_photo
                if photo_ref is None:
                    return
                # Get source PIL image
                src_img = getattr(self, f'_mag_src_{ref_name}', None)
                if src_img is None:
                    return
                
                canvas.delete("magnifier")
                canvas.delete("mag_border")
                
                # Calculate position in image coordinates
                cw = canvas.winfo_width()
                ch = canvas.winfo_height()
                img_w, img_h = src_img.size
                
                # Image display scale and offset
                display_scale = min(cw / img_w, ch / img_h) * 0.95
                display_w = int(img_w * display_scale)
                display_h = int(img_h * display_scale)
                offset_x = (cw - display_w) // 2
                offset_y = (ch - display_h) // 2
                
                # Mouse position in image coordinates
                img_x = (e.x - offset_x) / display_scale
                img_y = (e.y - offset_y) / display_scale
                
                if img_x < 0 or img_y < 0 or img_x >= img_w or img_y >= img_h:
                    return
                
                # Extract region from source image
                # Normalize zoom based on display scale so 2x zoom looks the same on a 512x and 4096x image
                r = int(self._mag_size / (2 * self._mag_zoom * display_scale))
                x1 = max(0, int(img_x - r))
                y1 = max(0, int(img_y - r))
                x2 = min(img_w, int(img_x + r))
                y2 = min(img_h, int(img_y + r))
                
                if x2 <= x1 or y2 <= y1:
                    return
                
                crop = src_img.crop((x1, y1, x2, y2))
                zoomed = crop.resize((self._mag_size, self._mag_size), Image.Resampling.NEAREST)
                
                # Create circular mask
                from PIL import ImageDraw
                mask = Image.new('L', (self._mag_size, self._mag_size), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, self._mag_size-1, self._mag_size-1), fill=255)
                
                # Apply mask
                if zoomed.mode != 'RGBA':
                    zoomed = zoomed.convert('RGBA')
                zoomed.putalpha(mask)
                
                mag_photo = ImageTk.PhotoImage(zoomed)
                # Store reference to prevent GC
                if ref_name == "original":
                    self._mag_photo_orig = mag_photo
                else:
                    self._mag_photo_prev = mag_photo
                
                canvas.create_image(e.x, e.y, image=mag_photo, anchor=tk.CENTER, tags="magnifier")
                # Draw border circle
                half = self._mag_size // 2
                canvas.create_oval(e.x - half, e.y - half, e.x + half, e.y + half,
                                   outline=c['accent'], width=2, tags="mag_border")
            
            canvas.bind('<Enter>', on_enter)
            canvas.bind('<Leave>', on_leave)
            canvas.bind('<Motion>', on_motion)
        
        _setup_magnifier(self.original_canvas, "original")
        _setup_magnifier(self.preview_canvas, "preview")
    
    def _check_deps(self):
        if not PIL_AVAILABLE:
            self._log("⚠️ Pillow not installed", 'warning')
        if not SRCTOOLS_AVAILABLE:
            self._log("⚠️ srctools not installed", 'warning')
        if REALESRGAN_EXE.exists():
            self.ai_status.configure(text="✓ AI Ready (RealESRGAN)")
            self._log("✓ RealESRGAN AI upscaler loaded", 'ai')
        else:
            self.ai_status.configure(text="⚠ AI Not Found")
            self._log(f"⚠️ RealESRGAN not found at {REALESRGAN_EXE}", 'warning')
        if PIL_AVAILABLE and SRCTOOLS_AVAILABLE:
            self._log("✓ Core dependencies OK")
        self._log("✓ v6.0 Performance mode: batch pipeline, BMP temp files", 'ai')
    
    def _init_log_file(self):
        """Initialize log file for this session."""
        os.makedirs(LOG_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._log_file_path = os.path.join(LOG_DIR, f"vtf_upscaler_{timestamp}.log")
        self._log_file = open(self._log_file_path, 'w', encoding='utf-8', buffering=1)
        self._log_file.write(f"=== VTF Upscaler {APP_VERSION} Log — {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
    
    def _log(self, msg: str, tag: str = None):
        ts = time.strftime("[%H:%M:%S] ")
        self.log_text.insert(tk.END, ts + msg + "\n", tag)
        # Trim log widget to prevent unbounded memory growth
        line_count = int(self.log_text.index('end-1c').split('.')[0])
        if line_count > 500:
            self.log_text.delete('1.0', f'{line_count - 400}.0')
        self.log_text.see(tk.END)
        # Also write to log file
        if hasattr(self, '_log_file') and self._log_file and not self._log_file.closed:
            try:
                self._log_file.write(ts + msg + "\n")
            except:
                pass
    
    def _browse_file(self):
        path = filedialog.askopenfilename(title="Select VTF", filetypes=[("VTF Files", "*.vtf")])
        if path:
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, path)
            self._refresh_files()
    
    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select Folder")
        if path:
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, path)
            self._refresh_files()
    
    def _show_context_menu(self, event):
        try:
            index = self.file_listbox.nearest(event.y)
            self.file_listbox.selection_clear(0, tk.END)
            self.file_listbox.selection_set(index)
            self.current_file_index = index
            self.context_menu.delete(0, tk.END)
            folder_count = len(self.displayed_folders)
            if index < folder_count:
                folder_path = self.displayed_folders[index]
                self.context_menu.add_command(label="🚀 Upscale Contents", command=lambda: self._upscale_folder_contents(folder_path))
                self.context_menu.add_separator()
                self.context_menu.add_command(label="📂 Open Folder", command=lambda: os.startfile(folder_path))
            else:
                fp = self._get_file_from_listbox(index)
                if fp:
                    self.context_menu.add_command(label="🚀 Upscale This File", command=lambda: self._upscale_single(fp))
                    self.context_menu.add_separator()
                    self.context_menu.add_command(label="📂 Open Directory", command=lambda: os.startfile(os.path.dirname(fp)))
                    self.context_menu.add_command(label="📄 Open File", command=lambda: os.startfile(fp))
                    bak_path = f"{fp}.bak"
                    if os.path.exists(bak_path):
                        self.context_menu.add_separator()
                        self.context_menu.add_command(label="♻ Restore Backup", command=lambda: self._restore_single_file(fp))
                    self.context_menu.add_separator()
                    self.context_menu.add_command(label="🗑 Skip This File", command=self._skip_file)
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()
            
    def _restore_single_file(self, fp):
        bak_path = f"{fp}.bak"
        fname = os.path.basename(fp)
        if not os.path.exists(bak_path):
            self._log(f"No backup found for {fname}", 'warning')
            return
        try:
            if os.path.exists(fp):
                os.remove(fp)
            os.rename(bak_path, fp)
            self._log(f"♻ Restored backup for {fname}", 'success')
            if hasattr(self, '_scan_cache') and fp in self._scan_cache:
                self._scan_cache.pop(fp)
                self.root.after(0, self._update_listbox_with_cache)
            if self.current_file_index is not None and fp == self._get_file_from_listbox(self.current_file_index):
                self.original_canvas.delete("all")
                self.preview_canvas.delete("all")
                self.file_info.configure(text="")
        except Exception as e:
            self._log(f"❌ Failed to restore {fname}: {e}", 'error')
    
    def _open_directory(self):
        fp = self._get_file_from_listbox(self.current_file_index)
        if fp:
            os.startfile(os.path.dirname(fp))
    
    def _open_file(self):
        fp = self._get_file_from_listbox(self.current_file_index)
        if fp:
            os.startfile(fp)
    
    def _skip_file(self):
        fp = self._get_file_from_listbox(self.current_file_index)
        if fp:
            filename = os.path.basename(fp)
            self.file_list.remove(fp)
            if fp in self.displayed_files:
                self.displayed_files.remove(fp)
            self.file_listbox.delete(self.current_file_index)
            self._log(f"⊖ Removed: {filename}", 'warning')
            self.status_label.configure(text=f"{len(self.file_list)} VTF file(s)")
    
    def _upscale_folder_contents(self, folder_path):
        if self.processing:
            messagebox.showwarning("Busy", "Already processing files")
            return
        vtf_files = []
        for root, dirs, files in os.walk(folder_path):
            for f in files:
                if f.lower().endswith('.vtf'):
                    vtf_files.append(os.path.join(root, f))
        if not vtf_files:
            messagebox.showinfo("No VTFs", f"No VTF files found in:\n{folder_path}")
            return
        folder_name = os.path.basename(folder_path)
        if not messagebox.askyesno("Upscale Folder", f"Upscale {len(vtf_files)} VTF files in:\n{folder_name}?"):
            return
        self.file_list = vtf_files
        self._update_file_listbox()
        self._log(f"📁 Folder: {folder_name} ({len(vtf_files)} VTFs)", 'ai')
        self._start()
    
    def _update_file_listbox(self):
        folder_count = len(self.folder_list)
        self.file_listbox.delete(folder_count, tk.END)
        for f in self.file_list:
            self.file_listbox.insert(tk.END, f"  {os.path.basename(f)}")
        self.status_label.configure(text=f"{len(self.file_list)} VTF file(s)")
    
    def _refresh_files(self):
        self.file_list = []
        self.folder_list = []
        self.file_listbox.delete(0, tk.END)
        path = self.input_entry.get().strip()
        if not path:
            return
        if os.path.isfile(path) and path.lower().endswith('.vtf'):
            self.file_list = [path]
        elif os.path.isdir(path):
            parent = os.path.dirname(path)
            if parent and parent != path:
                self.file_listbox.insert(tk.END, "▸ ..")
                self.folder_list.append(parent)
            try:
                items = sorted(os.listdir(path), key=str.lower)
            except PermissionError:
                items = []
            for item in items:
                item_path = os.path.join(path, item)
                if os.path.isdir(item_path):
                    self.file_listbox.insert(tk.END, f"▸ {item}")
                    self.folder_list.append(item_path)
            if self.recursive_var.get():
                for root, _, files in os.walk(path):
                    for f in files:
                        if f.lower().endswith('.vtf'):
                            self.file_list.append(os.path.join(root, f))
            else:
                for item in items:
                    if item.lower().endswith('.vtf'):
                        self.file_list.append(os.path.join(path, item))
        for fp in self.file_list:
            self.file_listbox.insert(tk.END, os.path.basename(fp))
        self.displayed_folders = self.folder_list.copy()
        self.displayed_files = self.file_list.copy()
        folder_count = len(self.folder_list)
        self.status_label.configure(text=f"{folder_count} folders, {len(self.file_list)} VTF file(s)")
        if self.file_list:
            self.file_listbox.selection_set(folder_count)
            self.current_file_index = 0
            self._load_preview(self.file_list[0])
            # Auto-load existing cache for enriched display
            self._scan_cache = self._load_cache()
            if self._scan_cache:
                self._update_listbox_with_cache()
    
    def _colorize_folders(self):
        """Color folder items orange and skip/alpha items accordingly."""
        try:
            for i in range(self.file_listbox.size()):
                text = self.file_listbox.get(i)
                if text.startswith("▸"):
                    self.file_listbox.itemconfigure(i, fg=self._folder_color)
                elif text.startswith("  -") or "[skip" in text.lower() or "[already" in text.lower():
                    self.file_listbox.itemconfigure(i, fg='#484f58')  # Muted for skipped
                elif "[ALPHA" in text:
                    self.file_listbox.itemconfigure(i, fg='#d29922')  # Amber for alpha issues
        except Exception:
            pass
    
    def _on_search(self, *args):
        self._update_listbox_display()

    def _update_listbox_display(self):
        query = self.search_var.get().lower().strip()
        anim_only = getattr(self, 'anim_filter_var', None) and self.anim_filter_var.get()
        cache = getattr(self, '_scan_cache', {})
        
        self.displayed_folders = []
        self.displayed_files = []
        
        for folder_path in self.folder_list:
            folder_name = os.path.basename(folder_path) if folder_path != ".." else ".."
            if query in folder_name.lower():
                if not anim_only:
                    self.displayed_folders.append(folder_path)
                    
        for fp in self.file_list:
            filename = os.path.basename(fp)
            if query in filename.lower():
                if anim_only:
                    info = cache.get(fp, {})
                    frames = info.get('frame_count', 1)
                    if frames <= 1:
                        continue
                self.displayed_files.append(fp)
                
        self._update_listbox_with_cache()
    
    def _on_file_select(self, event):
        selection = self.file_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        folder_count = len(self.displayed_folders)
        if idx < folder_count:
            return
        file_idx = idx - folder_count
        if 0 <= file_idx < len(self.displayed_files):
            filepath = self.displayed_files[file_idx]
            if filepath in self.file_list:
                self.current_file_index = self.file_list.index(filepath)
            self._load_preview(filepath)
    
    def _on_click_select(self, event):
        idx = self.file_listbox.nearest(event.y)
        if idx < 0:
            return
        self.file_listbox.selection_clear(0, tk.END)
        self.file_listbox.selection_set(idx)
        self.file_listbox.activate(idx)
        folder_count = len(self.displayed_folders)
        if idx < folder_count:
            return
        file_idx = idx - folder_count
        if 0 <= file_idx < len(self.displayed_files):
            filepath = self.displayed_files[file_idx]
            if filepath in self.file_list:
                self.current_file_index = self.file_list.index(filepath)
            self._load_preview(filepath)
    
    def _on_double_click(self, event):
        selection = self.file_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        folder_count = len(self.folder_list)
        if idx < folder_count:
            folder_path = self.folder_list[idx]
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, folder_path)
            self._refresh_files()
        else:
            file_idx = idx - folder_count
            if 0 <= file_idx < len(self.file_list):
                self._upscale_single(self.file_list[file_idx])
    
    def _upscale_single(self, filepath: str):
        if self.processing:
            return
        filename = os.path.basename(filepath)
        og_size = os.path.getsize(filepath)
        og_size_str = f"{og_size/1024:.0f}KB" if og_size < 1024*1024 else f"{og_size/1024/1024:.1f}MB"
        self._log(f"⚡ Quick upscale: {filename} ({og_size_str})", 'ai')
        self.status_label.configure(text=f"Upscaling {filename}...")
        self.root.update()
        def do_upscale():
            try:
                t0 = time.time()
                self._update_config()
                processor = VTFProcessor(self.config)
                ok, msg = processor.upscale_file(filepath, filepath)
                elapsed = time.time() - t0
                if self.root.winfo_exists():
                    if ok:
                        new_size = os.path.getsize(filepath)
                        new_size_str = f"{new_size/1024:.0f}KB" if new_size < 1024*1024 else f"{new_size/1024/1024:.1f}MB"
                        self.root.after(0, lambda: self._log(f"✓ {filename}: {msg} | {og_size_str} → {new_size_str} ({elapsed:.1f}s)", 'success'))
                    else:
                        self.root.after(0, lambda: self._log(f"✗ {filename}: {msg} ({elapsed:.1f}s)", 'error'))
                    self.root.after(0, lambda: self.status_label.configure(text="Ready"))
                    self.root.after(0, lambda: self._load_preview(filepath))
            except Exception as e:
                if self.root.winfo_exists():
                    self.root.after(0, lambda: self._log(f"✗ Error: {e}", 'error'))
                    self.root.after(0, lambda: self.status_label.configure(text="Ready"))
        threading.Thread(target=do_upscale, daemon=True).start()
    
    def _prev_file(self):
        if self.file_list and self.current_file_index > 0:
            self.current_file_index -= 1
            folder_count = len(self.folder_list)
            self.file_listbox.selection_clear(0, tk.END)
            self.file_listbox.selection_set(folder_count + self.current_file_index)
            self._load_preview(self.file_list[self.current_file_index])
    
    def _next_file(self):
        if self.file_list and self.current_file_index < len(self.file_list) - 1:
            self.current_file_index += 1
            folder_count = len(self.folder_list)
            self.file_listbox.selection_clear(0, tk.END)
            self.file_listbox.selection_set(folder_count + self.current_file_index)
            self._load_preview(self.file_list[self.current_file_index])
    
    def _load_preview(self, filepath: str):
        if not SRCTOOLS_AVAILABLE or not PIL_AVAILABLE:
            self.orig_info_label.configure(text="Missing dependencies")
            return
        processor = VTFProcessor(self.config)
        w, h, fmt, _, _, frame_count = processor.get_vtf_info(filepath)
        anim_str = f" [{frame_count} frames]" if frame_count > 1 else ""
        self.orig_info_label.configure(text=f"Original: {w}x{h} ({fmt}){anim_str}")
        self._preview_filepath = filepath  # Store for classification lookup
        self._stop_animation()
        
        if frame_count > 1:
            self.root.update_idletasks()
            self._anim_frames_orig = processor.load_vtf_frames(filepath)
            self.original_pil = self._anim_frames_orig[0] if self._anim_frames_orig else None
            # Setup AI side if possible
            self._anim_frames_ai = []
            
            if self.anim_control_frame:
                self.anim_control_frame.grid()
                self._anim_idx = 0
                max_f = len(self._anim_frames_orig) - 1
                if max_f >= 0:
                    if CTK_AVAILABLE:
                        self.anim_slider.configure(to=max_f, number_of_steps=max_f)
                        self.anim_slider.set(0)
                    else:
                        self.anim_slider.configure(to=max_f)
                        self.anim_slider.set(0)
                self._update_anim_label()
                self._is_playing = True
                self._play_animation()
        else:
            self.original_pil = processor.load_vtf_image(filepath)
            if self.anim_control_frame:
                self.anim_control_frame.grid_remove()
                
        if self.original_pil is None:
            self.orig_info_label.configure(text=f"⚠ Failed to load: {w}x{h} ({fmt})")
            self.original_canvas.delete("all")
            self.preview_canvas.delete("all")
            return
        self._display_image(self.original_pil, self.original_canvas, "original")
        self._update_preview()
    
    def _display_image(self, img, canvas, ref_name):
        img_copy = img.copy()
        w, h = img_copy.size
        # Use actual canvas dimensions, with PREVIEW_SIZE as minimum
        try:
            canvas.update_idletasks()
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            # If canvas hasn't been laid out yet, use PREVIEW_SIZE
            if cw < 50 or ch < 50:
                cw = PREVIEW_SIZE
                ch = PREVIEW_SIZE
        except Exception:
            cw = PREVIEW_SIZE
            ch = PREVIEW_SIZE
        scale = min(cw / w, ch / h) * 0.95
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        img_copy = img_copy.resize((new_w, new_h), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(img_copy)
        if ref_name == "original":
            self.original_photo = photo
            self._mag_src_original = img  # Store full-res for magnifier
        else:
            self.preview_photo = photo
            self._mag_src_preview = img  # Store full-res for magnifier
        canvas.delete("all")
        canvas.create_image(cw // 2, ch // 2, anchor=tk.CENTER, image=photo)
        
    def _toggle_animation(self):
        if getattr(self, '_is_playing', False):
            self._stop_animation()
        else:
            self._is_playing = True
            if CTK_AVAILABLE and hasattr(self, 'anim_play_btn'):
                self.anim_play_btn.configure(text="⏸ Pause")
            elif hasattr(self, 'anim_play_btn'):
                self.anim_play_btn.configure(text="⏸ Pause")
            self._play_animation()

    def _play_animation(self):
        if not getattr(self, '_is_playing', False) or not getattr(self, '_anim_frames_orig', None):
            return
        
        self._anim_idx = (self._anim_idx + 1) % len(self._anim_frames_orig)
        self._show_current_frame()
        self._update_anim_label()
        
        # 15 FPS = ~66ms
        self._anim_timer = self.root.after(66, self._play_animation)

    def _stop_animation(self):
        self._is_playing = False
        if CTK_AVAILABLE and hasattr(self, 'anim_play_btn'):
            self.anim_play_btn.configure(text="▶ Play")
        elif hasattr(self, 'anim_play_btn'):
            self.anim_play_btn.configure(text="▶ Play")
        if getattr(self, '_anim_timer', None) is not None:
            self.root.after_cancel(self._anim_timer)
            self._anim_timer = None

    def _on_anim_scrub(self, val):
        self._stop_animation()
        try:
            self._anim_idx = int(round(float(val)))
        except:
            pass
        if getattr(self, '_anim_frames_orig', None) and 0 <= self._anim_idx < len(self._anim_frames_orig):
            self._show_current_frame()
            self._update_anim_label()

    def _update_anim_label(self):
        if not getattr(self, '_anim_frames_orig', None):
            return
        if hasattr(self, 'anim_frame_label'):
            self.anim_frame_label.configure(text=f"{self._anim_idx + 1} / {len(self._anim_frames_orig)}")
        if hasattr(self, 'anim_slider'):
            self.anim_slider.set(self._anim_idx)

    def _show_current_frame(self):
        if getattr(self, '_anim_frames_orig', None) and 0 <= self._anim_idx < len(self._anim_frames_orig):
            # Only update original_pil reference without triggering expensive _update_preview
            self.original_pil = self._anim_frames_orig[self._anim_idx]
            self._display_image(self.original_pil, self.original_canvas, "original")
        if getattr(self, '_anim_frames_ai', None) and 0 <= self._anim_idx < len(self._anim_frames_ai):
            self._display_image(self._anim_frames_ai[self._anim_idx], self.preview_canvas, "preview")
    
    def _update_preview(self):
        if self.original_pil is None:
            return
        target = int(self.res_var.get())
        w, h = self.original_pil.size
        processor = VTFProcessor(self.config)
        new_w, new_h = processor.calc_target_dims(w, h, target)
        preview = self.original_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
        model = self._get_current_model()
        self.preview_info_label.configure(text=f"Preview: {new_w}x{new_h} (click '🔄 Preview AI' for {model})")
        self._display_image(preview, self.preview_canvas, "preview")
    
    def _preview_ai(self):
        """Generate actual AI preview using the CURRENTLY SELECTED model.
        Classification-aware: uses LANCZOS for particle/effect/blade textures."""
        if self.original_pil is None:
            return
        
        # Check texture classification before deciding method
        filepath = getattr(self, '_preview_filepath', '')
        tex_class = ''
        if filepath:
            # Check scan cache first, then classify on-the-fly
            cache = getattr(self, '_scan_cache', None)
            if cache and filepath in cache:
                tex_class = cache[filepath].get('tex_class', '')
            if not tex_class:
                tex_class = classify_texture(filepath)
        
        if tex_class == 'lanczos':
            # LANCZOS preview — no GPU needed
            self._log(f"🔷 LANCZOS preview (particle/effect texture — AI skipped)", 'ai')
            self.preview_info_label.configure(text="⏳ LANCZOS upscale...")
            self.root.update()
            
            target = int(self.res_var.get())
            w, h = self.original_pil.size
            processor = VTFProcessor(self.config)
            new_w, new_h = processor.calc_target_dims(w, h, target)
            upscaled = self.original_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
            self._display_image(upscaled, self.preview_canvas, "preview")
            self.preview_info_label.configure(text=f"🔷 LANCZOS: {new_w}x{new_h} (particle/effect — AI bypassed)")
            self._log(f"✓ LANCZOS preview: {new_w}x{new_h}", 'success')
            return
        
        if not REALESRGAN_EXE.exists():
            messagebox.showwarning("AI Not Found", "RealESRGAN binary not found")
            return
        
        # Read current model selection BEFORE starting thread
        self._update_config()
        current_model = self._get_current_model()
        current_config = self.config.copy()
        current_config["ai_model"] = current_model
        
        self._log(f"Generating AI preview with {current_model}...", 'ai')
        self.preview_info_label.configure(text=f"⏳ Processing with {current_model}...")
        self.root.update()
        
        def do_ai():
            try:
                processor = VTFProcessor(current_config)
                target = int(self.res_var.get())
                w, h = self.original_pil.size
                new_w, new_h = processor.calc_target_dims(w, h, target)
                
                # Handling for ANIMATIONS: Force LANCZOS sequential preview to avoid UI lockup
                if getattr(self, '_anim_frames_orig', None) and len(self._anim_frames_orig) > 1:
                    self._anim_frames_ai = []
                    for f_img in self._anim_frames_orig:
                        up = f_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        self._anim_frames_ai.append(up)
                        
                    if self.root.winfo_exists():
                        self.root.after(0, lambda: self._show_current_frame())
                        self.root.after(0, lambda: self.preview_info_label.configure(text=f"AI Enhanced: {new_w}x{new_h} (LANCZOS seq fallback)"))
                        self.root.after(0, lambda: self._log(f"✓ AI preview: LANCZOS (fallback for anim)", 'success'))
                else:
                    # Normal Single-Frame AI
                    upscaled, method = processor.upscale_image(self.original_pil, target)
                    if self.root.winfo_exists():
                        self.root.after(0, lambda: self._display_image(upscaled, self.preview_canvas, "preview"))
                        self.root.after(0, lambda: self.preview_info_label.configure(text=f"AI Enhanced: {upscaled.width}x{upscaled.height} ({current_model})"))
                        self.root.after(0, lambda: self._log(f"✓ AI preview: {method} ({current_model})", 'success'))
            except Exception as e:
                if self.root.winfo_exists():
                    self.root.after(0, lambda: self._log(f"✗ AI preview failed: {e}", 'error'))
        threading.Thread(target=do_ai, daemon=True).start()
    
    def _update_config(self):
        self.config["target_resolution"] = int(self.res_var.get())
        self.config["upscale_method"] = self.method_var.get()
        self.config["output_format"] = self.format_var.get()
        self.config["generate_mipmaps"] = self.mipmap_var.get()
        self.config["recursive_search"] = self.recursive_var.get()
        self.config["backup_originals"] = self.backup_var.get()
        # gpu_threads: auto-calculated in _build_cmd, not stored in config
        self.config["tile_size"] = int(self.tile_var.get().split()[0])
        self.config["gpu_id"] = int(self.gpu_var.get())
        self.config["ai_model"] = self._get_current_model()
        self.config["skip_small"] = int(self.skip_small_var.get())
        self.config["tta_mode"] = self.tta_var.get()
        self.config["batch_size"] = int(self.batch_var.get())
        self.config["cpu_workers"] = int(self.cpu_workers_var.get())
        self.config["output_to_addon"] = self.addon_output_var.get()
        self.config["sharpen_strength"] = float(self.sharpen_var.get())
        self.config["denoise_strength"] = int(self.denoise_var.get())
        self.config["ai_scale"] = int(self.scale_var.get())
        folder = self.input_entry.get().strip()
        if os.path.isdir(folder):
            self.config["last_folder"] = folder
    
    def _start(self):
        # Use displayed_files if search is active, otherwise full file_list
        active_files = self.displayed_files if self.search_var.get().strip() else self.file_list
        if self.processing or not active_files:
            if not active_files:
                messagebox.showwarning("No Files", "No VTF files loaded (or no search results)")
            return
        if not SRCTOOLS_AVAILABLE or not PIL_AVAILABLE:
            messagebox.showerror("Dependencies", "pip install Pillow srctools")
            return
        self._update_config()
        self._save_config()
        
        # ── PRE-BATCH HEALTH CHECK ──────────────────────────────────
        warnings = []
        
        # Check VRAM
        if PYNVML_AVAILABLE:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(self.config.get('gpu_id', 0))
                vram_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                free_vram_mb = vram_info.free / 1048576
                total_vram_mb = vram_info.total / 1048576
                if free_vram_mb < 1024:
                    warnings.append(f"⚠ Low VRAM: {free_vram_mb:.0f} MB free / {total_vram_mb:.0f} MB total — may cause black outputs or crashes")
            except:
                pass
        
        # Check RAM
        try:
            import psutil
            ram = psutil.virtual_memory()
            free_ram_mb = ram.available / 1048576
            if free_ram_mb < 2048:
                warnings.append(f"⚠ Low RAM: {free_ram_mb:.0f} MB free — may freeze your PC. Close other apps first")
        except:
            pass
        
        # Check disk space on scratch drive
        try:
            scratch = get_scratch_dir()
            disk = shutil.disk_usage(os.path.dirname(scratch))
            free_disk_gb = disk.free / (1024**3)
            if free_disk_gb < 5:
                warnings.append(f"⚠ Low disk: {free_disk_gb:.1f} GB free on scratch drive — batch may fail")
        except:
            pass
        
        if warnings:
            warning_text = "\n".join(warnings)
            if not messagebox.askyesno("Health Check Warning",
                f"Pre-batch health check found issues:\n\n{warning_text}\n\nContinue anyway?"):
                return
        # ────────────────────────────────────────────────────────────
        
        # Store active file list for pipeline to use
        self._pipeline_files = list(active_files)
        
        total_size = sum(os.path.getsize(f) for f in self._pipeline_files if os.path.exists(f))
        size_mb = total_size / (1024 * 1024)
        batch_size = self.config.get("batch_size", 100)
        num_batches = (len(self._pipeline_files) + batch_size - 1) // batch_size
        est_time = num_batches * 15 + len(self._pipeline_files) * 0.5
        est_min = est_time / 60
        
        search_note = f" (filtered: '{self.search_var.get().strip()}')" if self.search_var.get().strip() else ""
        self._log(f"📊 Batch pipeline: {len(self._pipeline_files)} files{search_note} in {num_batches} batches (size {batch_size}), ~{est_min:.1f} min", 'ai')
        self._log(f"📊 Input: {size_mb:.1f} MB | Model: {self.config.get('ai_model', 'realesrgan-x4plus')}", 'ai')
        
        self.processing = True
        self.cancel_flag = False
        self._last_ui_update = 0  # Throttle: last time we updated status/ETA UI
        self.start_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self._init_log_file()
        threading.Thread(target=self._process_pipeline, daemon=True).start()
    
    def _cancel(self):
        self.cancel_flag = True
        self._log("Cancelling...", 'warning')
    
    def _get_cache_path(self):
        """Return the cache JSON path for the currently loaded folder."""
        path = self.input_entry.get().strip()
        if not path or not os.path.isdir(path):
            return None
        return os.path.join(path, CACHE_FILE)
    
    def _get_history_path(self):
        """Return the process history JSON path for the currently loaded folder."""
        path = self.input_entry.get().strip()
        if not path or not os.path.isdir(path):
            return None
        return os.path.join(path, HISTORY_FILE)
    
    def _load_history(self):
        """Load process history for crash-resume. Returns dict of filepath -> {status, timestamp, output}."""
        hist_path = self._get_history_path()
        if not hist_path or not os.path.exists(hist_path):
            return {}
        try:
            with open(hist_path, 'r') as f:
                data = json.load(f)
            # Validate history matches current settings
            if data.get('target_resolution') != self.config.get('target_resolution', 4096):
                return {}
            if data.get('ai_scale') != self.config.get('ai_scale', 4):
                return {}
            if data.get('ai_model') != self.config.get('ai_model', 'realesrgan-x4plus'):
                return {}
            return data.get('files', {})
        except Exception:
            return {}
    
    def _save_history(self, history: dict):
        """Persist process history to JSON."""
        hist_path = self._get_history_path()
        if not hist_path:
            return
        try:
            data = {
                'version': 1,
                'target_resolution': self.config.get('target_resolution', 4096),
                'ai_scale': self.config.get('ai_scale', 4),
                'ai_model': self.config.get('ai_model', 'realesrgan-x4plus'),
                'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'files': history
            }
            with open(hist_path, 'w') as f:
                json.dump(data, f, indent=1)
        except Exception:
            pass
    
    def _mark_history(self, history: dict, filepath: str, status: str, detail: str = ""):
        """Record a file's processing result in history."""
        history[filepath] = {
            'status': status,
            'detail': detail,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def _reset_history(self):
        """Clear the process history file for the current folder."""
        hist_path = self._get_history_path()
        if hist_path and os.path.exists(hist_path):
            try:
                os.remove(hist_path)
                self._log("🔄 Process history cleared — next run will process all files.", 'success')
            except Exception as e:
                self._log(f"✗ Failed to clear history: {e}", 'error')
        else:
            self._log("No process history found for this folder.", 'warning')
    
    def _load_cache(self):
        """Load and validate existing scan cache for the current folder.
        Returns dict of filepath -> {w, h, fmt, format_id, has_alpha, size, skip, skip_reason} or None."""
        cache_path = self._get_cache_path()
        if not cache_path or not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
            # Validate cache matches current settings
            if data.get('target_resolution') != self.config.get('target_resolution', 4096):
                return None
            if data.get('version') != 2:
                return None
            return data.get('files', {})
        except Exception:
            return None
    
    def _prescan_cache(self):
        """Scan all loaded VTFs and write a JSON cache to the folder."""
        if not self.file_list:
            self._log("No files loaded — open a folder first.", 'warning')
            return
        cache_path = self._get_cache_path()
        if not cache_path:
            self._log("Cannot cache — no folder selected.", 'warning')
            return
        
        self.cache_btn.configure(state=tk.DISABLED)
        self._log(f"\ud83d\udccb Pre-scanning {len(self.file_list)} files...", 'ai')
        
        def do_scan():
            from concurrent.futures import ThreadPoolExecutor
            processor = VTFProcessor(self.config)
            target = self.config.get("target_resolution", 4096)
            cache_data = {
                'version': 2,
                'target_resolution': target,
                'scanned_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'files': {}
            }
            
            def scan_one(fp):
                try:
                    w, h, fmt, has_alpha, format_id, frame_count = read_vtf_header(fp)
                    should_skip_file, reason = processor.should_skip(fp)
                    size = os.path.getsize(fp)
                    # Calculate target dimensions and estimated VRAM
                    scale = self.config.get('ai_scale', 4)
                    new_w, new_h = processor.calc_target_dims(w, h, target) if w > 0 and h > 0 else (0, 0)
                    # Pre-flight VRAM estimation: W * H * 3 channels * scale² (raw pixel budget)
                    estimated_vram_mb = (w * h * 3 * scale * scale) / (1024 * 1024) if w > 0 else 0
                    # Auto-classify texture for upscale method
                    tex_class = classify_texture(fp) if not should_skip_file else 'skip'
                    return fp, {
                        'w': w, 'h': h, 'fmt': fmt, 'format_id': format_id,
                        'has_alpha': has_alpha, 'size': size,
                        'skip': should_skip_file, 'skip_reason': reason,
                        'new_w': new_w, 'new_h': new_h,
                        'estimated_vram_mb': round(estimated_vram_mb, 1),
                        'alpha_broken': False, 'alpha_fixable': False,
                        'alpha_mean': -1, 'has_original': False,
                        'tex_class': tex_class,  # 'ai', 'lanczos', or 'skip'
                        'frame_count': frame_count,
                    }
                except Exception as e:
                    return fp, {'w': 0, 'h': 0, 'fmt': 'unknown', 'format_id': 0,
                               'has_alpha': False, 'size': 0,
                               'skip': True, 'skip_reason': f'Error: {e}',
                               'new_w': 0, 'new_h': 0, 'estimated_vram_mb': 0,
                               'alpha_broken': False, 'alpha_fixable': False,
                               'alpha_mean': -1, 'has_original': False,
                               'tex_class': 'skip', 'frame_count': 1}
            
            workers = min(16, len(self.file_list))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for fp, info in pool.map(scan_one, self.file_list):
                    cache_data['files'][fp] = info
            
            # ALPHA HEALTH CHECK: scan DXT5/DXT3 files for broken alpha
            alpha_candidates = {fp: info for fp, info in cache_data['files'].items()
                               if info.get('has_alpha') and info.get('fmt') in ('DXT5', 'DXT3')}
            if alpha_candidates:
                self.root.after(0, lambda n=len(alpha_candidates):
                    self._log(f"🔬 Alpha health check: scanning {n} DXT5/DXT3 files...", 'ai'))
                
                def check_alpha_one(fp_info):
                    fp, info = fp_info
                    original = _find_original_vtf(fp)
                    info['has_original'] = original is not None
                    health = _check_vtf_alpha_health(fp, original)
                    info['alpha_broken'] = health['alpha_broken']
                    info['alpha_fixable'] = health['alpha_fixable']
                    info['alpha_mean'] = health['alpha_mean']
                    return fp, info
                
                with ThreadPoolExecutor(max_workers=min(8, len(alpha_candidates))) as pool:
                    for fp, info in pool.map(check_alpha_one, alpha_candidates.items()):
                        cache_data['files'][fp] = info
            
            # Write cache
            try:
                with open(cache_path, 'w') as f:
                    json.dump(cache_data, f, indent=1)
            except Exception as e:
                self.root.after(0, lambda e=e: self._log(f"\u2717 Cache write failed: {e}", 'error'))
                self.root.after(0, lambda: self.cache_btn.configure(state=tk.NORMAL))
                return
            
            # Count stats
            total = len(cache_data['files'])
            processable = {k: v for k, v in cache_data['files'].items() if not v.get('skip')}
            skipped = total - len(processable)
            to_upscale = len(processable)
            total_size_mb = sum(v.get('size', 0) for v in cache_data['files'].values()) / (1024*1024)
            
            # VRAM analysis
            total_vram_mb = sum(v.get('estimated_vram_mb', 0) for v in processable.values())
            max_vram_file = max(processable.values(), key=lambda v: v.get('estimated_vram_mb', 0)) if processable else None
            max_vram_mb = max_vram_file.get('estimated_vram_mb', 0) if max_vram_file else 0
            
            # Alpha health summary
            broken_alpha = {k: v for k, v in cache_data['files'].items()
                               if v.get('alpha_broken')}
            fixable_alpha = {k: v for k, v in broken_alpha.items() if v.get('alpha_fixable')}
            unfixable_alpha = {k: v for k, v in broken_alpha.items() if not v.get('alpha_fixable')}
            no_original = {k: v for k, v in cache_data['files'].items()
                          if v.get('has_alpha') and not v.get('has_original')
                          and v.get('fmt') in ('DXT5', 'DXT3')}
            
            # Dimension distribution
            dim_tiers = {'tiny (<64px)': 0, 'small (64-256)': 0, 'medium (256-1024)': 0, 'large (1024+)': 0}
            for v in processable.values():
                max_dim = max(v.get('w', 0), v.get('h', 0))
                if max_dim < 64: dim_tiers['tiny (<64px)'] += 1
                elif max_dim < 256: dim_tiers['small (64-256)'] += 1
                elif max_dim < 1024: dim_tiers['medium (256-1024)'] += 1
                else: dim_tiers['large (1024+)'] += 1
            tier_str = ", ".join(f"{v}x {k}" for k, v in dim_tiers.items() if v > 0)
            
            # Classification distribution
            class_counts = {'ai': 0, 'lanczos': 0, 'skip': 0}
            for v in cache_data['files'].values():
                tc = v.get('tex_class', 'ai')
                class_counts[tc] = class_counts.get(tc, 0) + 1
            
            self.root.after(0, lambda t=total, s=skipped, u=to_upscale, sz=total_size_mb:
                self._log(f"\u2713 Scan: {t} files ({sz:.1f} MB), {u} to upscale, {s} will skip", 'success'))
            self.root.after(0, lambda tv=total_vram_mb, mv=max_vram_mb:
                self._log(f"\ud83d\udcca VRAM estimate: {tv:.0f} MB total, largest file: {mv:.0f} MB", 'ai'))
            self.root.after(0, lambda cc=class_counts:
                self._log(f"🏷️ Classification: {cc.get('ai',0)} AI, {cc.get('lanczos',0)} LANCZOS, {cc.get('skip',0)} skip", 'ai'))
            if tier_str:
                self.root.after(0, lambda ts=tier_str:
                    self._log(f"📊 Distribution: {ts}", 'ai'))
            
            # Alpha health report
            if fixable_alpha:
                self.root.after(0, lambda fa=len(fixable_alpha), no=len(no_original):
                    self._log(f"⚠ Alpha: {fa} fixable broken" + (f", {no} missing originals" if no else ""), 'warning'))
            elif no_original:
                self.root.after(0, lambda no=len(no_original):
                    self._log(f"❌ {no} DXT5 files have no original source found", 'warning'))
            # VRAM warning for very large files
            if PYNVML_AVAILABLE and max_vram_mb > 0:
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(self.config.get('gpu_id', 0))
                    info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    free_mb = info.free / 1048576
                    if max_vram_mb > free_mb * 0.5:
                        self.root.after(0, lambda mv=max_vram_mb, fm=free_mb:
                            self._log(f"\u26a0 Largest file needs ~{mv:.0f}MB VRAM (free: {fm:.0f}MB) \u2014 may need smaller tile", 'warning'))
                except Exception:
                    pass
            
            self.root.after(0, lambda: self.cache_btn.configure(state=tk.NORMAL))
            self.root.after(0, self._update_listbox_with_cache)
            self._scan_cache = cache_data.get('files', {})
        
        threading.Thread(target=do_scan, daemon=True).start()
    
    def _update_listbox_with_cache(self):
        """Re-render the file listbox with cached dimension/format info."""
        cache = getattr(self, '_scan_cache', None) or self._load_cache() or {}
        self._scan_cache = cache
        
        self.file_listbox.delete(0, tk.END)
        
        # Determine UI matching label
        query = self.search_var.get().lower().strip() if hasattr(self, 'search_var') else ""
        anim_only = self.anim_filter_var.get() if hasattr(self, 'anim_filter_var') else False
        
        # Re-add mapped folders dynamically
        parent = os.path.dirname(self.input_entry.get().strip())
        for folder_path in self.displayed_folders:
            folder_name = os.path.basename(folder_path)
            if folder_path == parent:
                self.file_listbox.insert(tk.END, "▸ ..")
            else:
                self.file_listbox.insert(tk.END, f"▸ {folder_name}")
        
        # Add files with cached info
        for fp in self.displayed_files:
            info = cache.get(fp)
            fn = os.path.basename(fp)
            if info:
                w, h = info.get('w', 0), info.get('h', 0)
                new_w, new_h = info.get('new_w', 0), info.get('new_h', 0)
                fmt = info.get('fmt', '???')
                frames = info.get('frame_count', 1)
                anim_str = f" [{frames} frames]" if frames > 1 else ""
                
                if info.get('skip'):
                    reason = info.get('skip_reason', 'skip')
                    # Compact reason: extract the part in parentheses
                    short = reason.split('(')[1].rstrip(')') if '(' in reason else reason
                    label = f"  - {fn} ({w}x{h} {fmt}){anim_str} [{short}]"
                elif info.get('alpha_broken') and info.get('alpha_fixable'):
                    label = f"  ! {fn} ({w}x{h} > {new_w}x{new_h} {fmt}){anim_str} [ALPHA fixable]"
                elif info.get('alpha_broken'):
                    label = f"  ! {fn} ({w}x{h} > {new_w}x{new_h} {fmt}){anim_str} [ALPHA broken]"
                elif new_w > 0 and new_h > 0:
                    tc = info.get('tex_class', 'ai')
                    tc_tag = ''
                    label = f"{tc_tag} {fn} ({w}x{h} > {new_w}x{new_h} {fmt}){anim_str}"
                else:
                    label = f"  {fn} ({w}x{h} {fmt}){anim_str}"
            else:
                label = fn
            self.file_listbox.insert(tk.END, label)
        self._colorize_folders()
        
        if query or anim_only:
            self.status_label.configure(text=f"🔍 {len(self.displayed_folders)} folders, {len(self.displayed_files)} VTF(s) matching")
        else:
            self.status_label.configure(text=f"{len(self.folder_list)} folders, {len(self.file_list)} VTF file(s)")
    
    def _fix_broken_alpha(self):
        """Fix all broken alpha channels detected by the scan.
        Restores alpha from original source files using LANCZOS upscaling.
        For binary alpha (foliage), applies threshold for crisp edges."""
        cache = getattr(self, '_scan_cache', None) or self._load_cache()
        if not cache:
            self._log("No scan cache — run Pre-scan first.", 'warning')
            return
        
        # Find fixable files
        fixable = {fp: info for fp, info in cache.items()
                   if info.get('alpha_broken') and info.get('alpha_fixable')}
        
        if not fixable:
            self._log("No fixable alpha issues found. Run Pre-scan first to detect them.", 'warning')
            return
        
        self.fix_alpha_btn.configure(state=tk.DISABLED)
        self._log(f"\U0001fa79 Fixing {len(fixable)} broken alpha channels...", 'ai')
        
        def do_fix():
            from srctools.vtf import VTF as VTFWrite
            fixed = 0
            failed = 0
            backup_dir = os.path.join(os.path.dirname(list(fixable.keys())[0]).split('addons')[0],
                                      'addons', 'Content', '_vtf_backups')
            
            for fp, info in fixable.items():
                try:
                    original = _find_original_vtf(fp)
                    if not original:
                        failed += 1
                        self.root.after(0, lambda f=os.path.basename(fp):
                            self._log(f"\u2717 {f}: original not found", 'error'))
                        continue
                    
                    # Read deployed VTF
                    with open(fp, 'rb') as f:
                        vtf = VTFWrite.read(f)
                        vtf.load()
                    deployed_img = vtf.get(frame=0, mipmap=0).to_PIL()
                    deployed_w, deployed_h = deployed_img.size
                    
                    # Read original VTF for alpha
                    with open(original, 'rb') as f:
                        orig_vtf = VTFWrite.read(f)
                        orig_vtf.load()
                    orig_img = orig_vtf.get(frame=0, mipmap=0).to_PIL()
                    
                    if orig_img.mode != 'RGBA':
                        failed += 1
                        self.root.after(0, lambda f=os.path.basename(fp):
                            self._log(f"\u2717 {f}: original has no alpha", 'error'))
                        continue
                    
                    # Extract and upscale alpha from original
                    _, _, _, orig_alpha = orig_img.split()
                    alpha_up = orig_alpha.resize((deployed_w, deployed_h), Image.Resampling.LANCZOS)
                    
                    # Check if binary alpha (foliage) — threshold for crisp edges
                    alpha_arr = np.array(orig_alpha)
                    binary_pct = np.sum((alpha_arr < 16) | (alpha_arr > 240)) / alpha_arr.size
                    if binary_pct > 0.85:
                        alpha_up_arr = np.array(alpha_up)
                        alpha_up_arr = np.where(alpha_up_arr > 128, 255, 0).astype(np.uint8)
                        alpha_up = Image.fromarray(alpha_up_arr, 'L')
                    
                    # Combine deployed RGB + restored alpha
                    deployed_rgb = deployed_img.convert('RGB')
                    combined = deployed_rgb.convert('RGBA')
                    combined.putalpha(alpha_up)
                    
                    # Backup
                    normalized = fp.replace('\\', '/')
                    idx = normalized.lower().find('materials/')
                    if idx >= 0:
                        rel = normalized[idx:]
                        bk_path = os.path.join(backup_dir, rel.replace('/', os.sep))
                        os.makedirs(os.path.dirname(bk_path), exist_ok=True)
                        if not os.path.exists(bk_path):
                            shutil.copy2(fp, bk_path)
                    
                    # Write fixed VTF back (atomic: write to temp, rename on success)
                    vtf.get(frame=0, mipmap=0).copy_from(combined.tobytes())
                    if hasattr(vtf, 'compute_mipmaps'):
                        vtf.compute_mipmaps()
                    # Patch missing srctools attrs that save() expects
                    if not hasattr(vtf, 'hotspot_info'):
                        vtf.hotspot_info = None
                    if not hasattr(vtf, 'hotspot_flags'):
                        vtf.hotspot_flags = 0
                    tmp_path = fp + '.tmp'
                    with open(tmp_path, 'wb') as f:
                        vtf.save(f)
                    os.replace(tmp_path, fp)  # atomic rename
                    
                    fixed += 1
                    # Update cache to reflect fix
                    info['alpha_broken'] = False
                    info['alpha_fixable'] = False
                    
                    self.root.after(0, lambda f=os.path.basename(fp):
                        self._log(f"\u2713 {f}: alpha restored", 'success'))
                    
                except Exception as e:
                    failed += 1
                    self.root.after(0, lambda f=os.path.basename(fp), e=str(e):
                        self._log(f"\u2717 {f}: {e}", 'error'))
            
            # Update cache file
            cache_path = self._get_cache_path()
            if cache_path:
                try:
                    full_cache = {'version': 2, 'target_resolution': self.config.get('target_resolution', 4096),
                                  'scanned_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'files': cache}
                    with open(cache_path, 'w') as f:
                        json.dump(full_cache, f, indent=1)
                except:
                    pass
            
            self.root.after(0, lambda fx=fixed, fa=failed:
                self._log(f"\u2705 Alpha fix complete: {fx} fixed, {fa} failed", 'success'))
            self.root.after(0, lambda: self.fix_alpha_btn.configure(state=tk.NORMAL))
            self.root.after(0, self._update_listbox_with_cache)
        
        threading.Thread(target=do_fix, daemon=True).start()
    def _get_addon_output_path(self, input_path):
        normalized = input_path.replace('\\', '/')
        for marker in ['materials/', 'models/', 'sound/', 'lua/']:
            if marker in normalized.lower():
                idx = normalized.lower().find(marker)
                relative = normalized[idx:]
                return os.path.join(ADDON_OUTPUT_PATH, relative)
        return os.path.join(ADDON_OUTPUT_PATH, "materials", os.path.basename(input_path))
    
    def _process_pipeline(self):
        """
        3-stage pipeline batch processor:
        Stage 1 (CPU parallel): Extract VTFs to BMP
        Stage 2 (GPU):          RealESRGAN batch folder mode
        Stage 3 (CPU parallel): Assemble upscaled BMPs back to VTF
        """
        from concurrent.futures import ThreadPoolExecutor
        
        processor = VTFProcessor(self.config)
        total = len(self._pipeline_files)
        success = skip = fail = 0
        completed = 0
        flagged_files = []  # Track potentially corrupt textures
        batch_size = self.config.get("batch_size", 100)
        cpu_workers = self.config.get("cpu_workers", 4)
        output_to_addon = self.config.get("output_to_addon", True)
        ai_scale = self.config.get("ai_scale", 4)
        ai_model = self.config.get("ai_model", "realesrgan-x4plus")
        
        if output_to_addon:
            os.makedirs(ADDON_OUTPUT_PATH, exist_ok=True)
            addon_json = os.path.join(ADDON_OUTPUT_PATH, "addon.json")
            if not os.path.exists(addon_json):
                with open(addon_json, 'w') as f:
                    json.dump({"title": "Heinzy Upscaled Textures", "type": "effects", "tags": ["materials"], "ignore": []}, f, indent=2)
        
        start_time = time.time()
        self._batch_stats = {
            'input_size_bytes': 0,
            'output_size_bytes': 0,
            'per_file_times': [],
            'start_time': start_time,
        }
        
        # Load process history for crash-resume
        process_history = self._load_history()
        history_resumed = 0
        
        
        # OPT 4: Addon output deduplication — skip already-upscaled files
        addon_skip_count = 0
        if output_to_addon:
            target_res = self.config.get('target_resolution', 4096)
            pre_dedup = []
            for fp in self._pipeline_files:
                addon_path = self._get_addon_output_path(fp)
                if os.path.exists(addon_path):
                    try:
                        aw, ah, _, _, _, _ = read_vtf_header(addon_path)
                        if aw >= target_res or ah >= target_res:
                            skip += 1
                            completed += 1
                            addon_skip_count += 1
                            continue
                    except:
                        pass
                pre_dedup.append(fp)
            if addon_skip_count > 0:
                self.root.after(0, lambda c=addon_skip_count: self._log(f"⊖ Skipped {c} already upscaled in addon output", 'warning'))
            self._pipeline_files = pre_dedup
            total = len(self._pipeline_files) + skip
        
        # Pre-flight: Use cache if available, otherwise scan all files
        scan_cache = getattr(self, '_scan_cache', None) or self._load_cache()
        to_process = []
        skip_reasons = {}
        
        if scan_cache and len(scan_cache) > 0:
            # FAST PATH: Use cached skip decisions
            self.root.after(0, lambda: self._log(f"⚡ Using cached scan for {total} files (instant)...", 'ai'))
            for fp in self._pipeline_files:
                info = scan_cache.get(fp)
                if info and info.get('skip'):
                    skip += 1
                    completed += 1
                    skip_reasons[os.path.basename(fp)] = info.get('skip_reason', 'Cached skip')
                else:
                    to_process.append(fp)
        else:
            # SLOW PATH: Scan all files (no cache)
            self.root.after(0, lambda: self._log(f"🔍 Pre-flight scanning {total} files...", 'ai'))
            
            def check_one(fp):
                """Check a single file — runs in thread pool."""
                return fp, processor.should_skip(fp)
            
            scan_workers = min(cpu_workers * 2, 16)
            with ThreadPoolExecutor(max_workers=scan_workers) as scan_pool:
                futures = [scan_pool.submit(check_one, fp) for fp in self._pipeline_files]
                for future in futures:
                    if self.cancel_flag:
                        break
                    fp, (should_skip_file, reason) = future.result()
                    if should_skip_file:
                        skip += 1
                        completed += 1
                        skip_reasons[os.path.basename(fp)] = reason
                    else:
                        to_process.append(fp)
        
        # Crash-resume: filter out already-completed files from previous runs
        if process_history:
            pre_resume = len(to_process)
            to_process = [fp for fp in to_process if process_history.get(fp, {}).get('status') != 'completed']
            history_resumed = pre_resume - len(to_process)
            if history_resumed > 0:
                skip += history_resumed
                completed += history_resumed
                self.root.after(0, lambda hr=history_resumed: self._log(f"⚡ Resumed: skipped {hr} already-completed files from previous run", 'ai'))
        
        # Smart skip: files with .bak backups were already processed in a previous session
        bak_skipped = 0
        bak_filtered = []
        for fp in to_process:
            if os.path.exists(fp + ".bak"):
                bak_skipped += 1
                skip += 1
                completed += 1
                skip_reasons[os.path.basename(fp)] = "already processed (.bak exists)"
            else:
                bak_filtered.append(fp)
        if bak_skipped > 0:
            self.root.after(0, lambda bs=bak_skipped: self._log(f"⊖ Smart skip: {bs} files already processed (.bak backup found)", 'ai'))
        to_process = bak_filtered
        
        if self.cancel_flag:
            self._save_history(process_history)
            self.root.after(0, self._done)
            return
        
        # Log skip summary (compact — don't spam 300+ individual lines)
        if skip > 0:
            # Group skip reasons
            reason_counts = {}
            for fn, reason in skip_reasons.items():
                key = reason.split('(')[1].rstrip(')') if '(' in reason else reason
                reason_counts[key] = reason_counts.get(key, 0) + 1
            reason_summary = ", ".join(f"{v}x {k}" for k, v in sorted(reason_counts.items(), key=lambda x: -x[1]))
            self.root.after(0, lambda s=skip, rs=reason_summary: self._log(f"⊖ Skipped {s} files: {rs}", 'warning'))
        
        # Dynamic batch sizing: prefer VRAM-aware if scan cache has estimates
        if to_process and scan_cache:
            # VRAM-aware batch sizing: use per-file estimates from scan cache
            vram_estimates = [scan_cache.get(fp, {}).get('estimated_vram_mb', 0) for fp in to_process]
            avg_vram = sum(vram_estimates) / len(vram_estimates) if vram_estimates else 0
            if avg_vram > 0 and PYNVML_AVAILABLE:
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(self.config.get('gpu_id', 0))
                    info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    free_mb = info.free / 1048576
                    # Budget: 70% of free VRAM (leave room for model + overhead)
                    vram_budget = free_mb * 0.7
                    vram_batch = max(5, int(vram_budget / avg_vram)) if avg_vram > 0 else batch_size
                    if vram_batch < batch_size:
                        self.root.after(0, lambda vb=vram_batch, ab=avg_vram, fb=free_mb:
                            self._log(f"\ud83d\udcca VRAM-aware batching: avg {ab:.0f}MB/file, free {fb:.0f}MB > batch={vb}", 'ai'))
                        batch_size = vram_batch
                except Exception:
                    pass
        elif to_process:
            # Fallback: average file-size heuristic
            sample = to_process[:min(20, len(to_process))]
            avg_size = sum(os.path.getsize(f) for f in sample) / len(sample)
            if avg_size < 8192:     # < 8KB = tiny textures
                batch_size = max(batch_size, 200)
            elif avg_size < 32768:  # < 32KB = small textures
                batch_size = max(batch_size, 150)
            elif avg_size < 131072: # < 128KB = medium textures
                pass  # Keep user setting (100)
            elif avg_size > 524288: # > 512KB = large textures
                batch_size = min(batch_size, 50)
            elif avg_size > 2097152: # > 2MB = very large textures
                batch_size = min(batch_size, 25)
        
        # OPT 6: Sort files by size for uniform GPU loading
        if to_process:
            to_process.sort(key=lambda fp: os.path.getsize(fp) if os.path.exists(fp) else 0)
        
        total_to_process_size = sum(os.path.getsize(f) for f in to_process if os.path.exists(f)) / (1024*1024)
        self._batch_stats['input_size_bytes'] = int(total_to_process_size * 1024 * 1024)
        self.root.after(0, lambda tp=len(to_process), s=skip, bs=batch_size, sz=total_to_process_size, hr=history_resumed:
            self._log(f"🔍 Scan done: {tp} to upscale ({sz:.1f} MB), {s} skipped" + (f", {hr} resumed" if hr > 0 else "") + f" | batch size: {bs}", 'ai'))
        self.root.after(0, lambda p=completed/total*100: self.progress_var.set(p / 100 if CTK_AVAILABLE else p))
        
        # Process in batches — with pipeline overlap (pre-extract N+1 while GPU runs N)
        num_batches = (len(to_process) + batch_size - 1) // batch_size
        
        # Pre-extraction state for pipeline overlap
        pre_extract_thread = None
        pre_extract_result = {}  # Shared: {bmp_name: (orig_path, meta)}
        pre_extract_dirs = [None, None]  # [tmpdir, in_dir] for pre-extracted batch
        
        def _do_pre_extract(batch_files_pe, in_dir_pe, result_dict, pe_processor, pe_cpu_workers):
            """Background extraction: runs while GPU processes the current batch."""
            from concurrent.futures import ThreadPoolExecutor
            def extract_one_pe(idx_fp):
                idx, fp = idx_fp
                bmp_name = f"{idx:04d}.bmp"
                bmp_path = os.path.join(in_dir_pe, bmp_name)
                ok, meta = pe_processor.extract_to_bmp(fp, bmp_path)
                return bmp_name, fp, ok, meta
            
            with ThreadPoolExecutor(max_workers=pe_cpu_workers) as pool:
                for bmp_name, fp, ok, meta in pool.map(extract_one_pe, 
                                                        [(i, fp) for i, fp in enumerate(batch_files_pe)]):
                    if ok:
                        result_dict[bmp_name] = (fp, meta)
        
        for batch_idx in range(num_batches):
            if self.cancel_flag:
                break
            
            batch_start = batch_idx * batch_size
            batch_end = min(batch_start + batch_size, len(to_process))
            batch_files = to_process[batch_start:batch_end]
            batch_num = batch_idx + 1
            
            self.root.after(0, lambda b=batch_num, n=num_batches, c=len(batch_files): 
                self._log(f"📦 Batch {b}/{n} ({c} files)...", 'ai'))
            
            # OPT 2: Split I/O — inputs on RAM disk (fast reads), outputs on SSD (space)
            # RAM disk is only 1GB; upscaled PNGs are 15-50MB each → overflow at ~30 files.
            scratch = get_scratch_dir()
            ssd_scratch = SCRATCH_DRIVE  # G:\_vtf_scratch — plenty of space for outputs
            os.makedirs(ssd_scratch, exist_ok=True)
            tmpdir = os.path.join(scratch, f"vtf_batch_{batch_num}_{int(time.time())}")
            out_tmpdir = os.path.join(ssd_scratch, f"vtf_out_{batch_num}_{int(time.time())}")
            try:
                os.makedirs(tmpdir, exist_ok=True)
                in_dir = os.path.join(tmpdir, "input")
                out_dir = os.path.join(out_tmpdir, "output")
                os.makedirs(in_dir)
                os.makedirs(out_dir)
                
                # STAGE 1: Extract VTFs to BMP (CPU parallel)
                # Check if we have pre-extracted data from pipeline overlap
                if pre_extract_thread is not None:
                    pre_extract_thread.join()
                    file_meta = dict(pre_extract_result)
                    pre_extract_result.clear()
                    # Use the pre-extracted dirs instead
                    if pre_extract_dirs[0]:
                        shutil.rmtree(tmpdir, ignore_errors=True)
                        tmpdir = pre_extract_dirs[0]
                        in_dir = pre_extract_dirs[1]
                        out_dir = os.path.join(out_tmpdir, "output")
                        os.makedirs(out_dir, exist_ok=True)
                    pre_extract_thread = None
                    pre_extract_dirs[0] = pre_extract_dirs[1] = None
                    # Count failures from pre-extraction
                    extracted_count = len(file_meta)
                    fail_count = len(batch_files) - extracted_count
                    if fail_count > 0:
                        fail += fail_count
                        completed += fail_count
                else:
                    file_meta = {}  # Maps bmp_name -> (original_path, metadata)
                
                # Only run synchronous extraction if we don't have pre-extracted data
                if not file_meta:
                    def extract_one(idx_fp):
                        idx, fp = idx_fp
                        bmp_name = f"{idx:04d}.bmp"
                        bmp_path = os.path.join(in_dir, bmp_name)
                        ok, meta = processor.extract_to_bmp(fp, bmp_path)
                        return bmp_name, fp, ok, meta
                    
                    with ThreadPoolExecutor(max_workers=cpu_workers) as cpu_pool:
                        extract_tasks = [(i, fp) for i, fp in enumerate(batch_files)]
                        for bmp_name, fp, ok, meta in cpu_pool.map(extract_one, extract_tasks):
                            if ok:
                                file_meta[bmp_name] = (fp, meta)
                            else:
                                fail += 1
                                completed += 1
                                fn = os.path.basename(fp)
                                err = meta.get('error', 'unknown')
                                self.root.after(0, lambda f=fn, e=err: self._log(f"✗ {f}: {e}", 'error'))
                                self.root.after(0, lambda p=completed/total*100: self.progress_var.set(p / 100 if CTK_AVAILABLE else p))
                
                if self.cancel_flag or not file_meta:
                    continue
                
                # ── LANCZOS BYPASS: Divert classified files before GPU ──
                # Files classified as 'lanczos' get upscaled via PIL.resize()
                # directly, skipping the GPU binary entirely. This preserves
                # gradient masks, particle sprites, and beam textures.
                lanczos_meta = {}  # Files to upscale with LANCZOS (bypass GPU)
                ai_meta = {}      # Files to send through GPU binary
                
                for bmp_name, (orig_path, meta) in file_meta.items():
                    # Check classification from scan cache
                    cached_info = scan_cache.get(orig_path, {}) if scan_cache else {}
                    tex_class = cached_info.get('tex_class', '')
                    if not tex_class:
                        # Fallback: classify on-the-fly if no cache
                        tex_class = classify_texture(orig_path)
                    
                    if tex_class == 'lanczos':
                        lanczos_meta[bmp_name] = (orig_path, meta)
                    else:
                        ai_meta[bmp_name] = (orig_path, meta)
                
                # Clean up duplicate main BMPs for animated VTFs going through AI
                # (frame 0 already exists as 0000_frame0.bmp — no need for 0000.bmp)
                anim_frame_total = 0
                for bmp_name, (orig_path, meta) in ai_meta.items():
                    frame_count = meta.get('frame_count', 1)
                    if frame_count > 1:
                        anim_frame_total += frame_count
                        main_bmp = os.path.join(in_dir, bmp_name)
                        if os.path.exists(main_bmp):
                            os.remove(main_bmp)
                if anim_frame_total > 0:
                    self.root.after(0, lambda af=anim_frame_total:
                        self._log(f"📽️ Animated VTFs: {af} total frames queued for AI upscaling", 'ai'))
                
                # Process LANCZOS files immediately (CPU-only, no GPU needed)
                if lanczos_meta:
                    lanczos_count = len(lanczos_meta)
                    self.root.after(0, lambda lc=lanczos_count:
                        self._log(f"🔷 LANCZOS bypass: {lc} files (particle/blade/effect textures)", 'ai'))
                    
                    target_res = self.config.get('target_resolution', 4096)
                    for bmp_name, (orig_path, meta) in lanczos_meta.items():
                        try:
                            bmp_path = os.path.join(in_dir, bmp_name)
                            if not os.path.exists(bmp_path):
                                continue
                            
                            orig_w, orig_h = meta.get('orig_w', 0), meta.get('orig_h', 0)
                            new_w, new_h = processor.calc_target_dims(orig_w, orig_h, target_res) if orig_w > 0 else (0, 0)
                            base_name = os.path.splitext(bmp_name)[0]
                            frame_count = meta.get('frame_count', 1)
                            
                            if frame_count > 1:
                                # Multi-frame LANCZOS: upscale each frame PNG
                                frame_paths = meta.get('frame_paths', [])
                                alpha_paths = meta.get('alpha_paths', [])
                                for fi in range(frame_count):
                                    frame_in = frame_paths[fi] if fi < len(frame_paths) else None
                                    if not frame_in or not os.path.exists(frame_in):
                                        continue
                                    frame_img = Image.open(frame_in)
                                    if new_w > 0 and new_h > 0:
                                        frame_img = frame_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                                    # Save upscaled frame
                                    frame_out = os.path.join(out_dir, f"{base_name}_frame{fi}.{AIUpscaler.BATCH_OUTPUT_FMT}")
                                    frame_img.save(frame_out)
                                    frame_img.close()
                                    # Upscale alpha for this frame
                                    if fi < len(alpha_paths) and alpha_paths[fi] and os.path.exists(alpha_paths[fi]):
                                        alpha_img = Image.open(alpha_paths[fi])
                                        alpha_up = alpha_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                                        if meta.get('alpha_is_binary'):
                                            alpha_arr = np.array(alpha_up)
                                            alpha_arr = np.where(alpha_arr > 128, 255, 0).astype(np.uint8)
                                            alpha_up = Image.fromarray(alpha_arr, 'L')
                                        alpha_up.save(alpha_paths[fi])
                                        alpha_img.close()
                                        alpha_up.close()
                                    # Remove from in_dir
                                    os.remove(frame_in)
                                # Also save frame 0 as the main output
                                frame0_out = os.path.join(out_dir, f"{base_name}_frame0.{AIUpscaler.BATCH_OUTPUT_FMT}")
                                main_out = os.path.join(out_dir, f"{base_name}.{AIUpscaler.BATCH_OUTPUT_FMT}")
                                if os.path.exists(frame0_out) and not os.path.exists(main_out):
                                    shutil.copy2(frame0_out, main_out)
                            else:
                                # Single-frame LANCZOS (original logic)
                                img = Image.open(bmp_path)
                                if new_w == 0:
                                    new_w, new_h = processor.calc_target_dims(img.width, img.height, target_res)
                                upscaled = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                                
                                # Upscale alpha if present
                                alpha_path = meta.get('alpha_path')
                                if alpha_path and os.path.exists(alpha_path):
                                    alpha_img = Image.open(alpha_path)
                                    alpha_up = alpha_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                                    alpha_img.close()
                                    if meta.get('alpha_is_binary'):
                                        alpha_arr = np.array(alpha_up)
                                        alpha_arr = np.where(alpha_arr > 128, 255, 0).astype(np.uint8)
                                        alpha_up = Image.fromarray(alpha_arr, 'L')
                                    alpha_up.save(alpha_path)
                                
                                out_path = os.path.join(out_dir, f"{base_name}.{AIUpscaler.BATCH_OUTPUT_FMT}")
                                upscaled.save(out_path)
                                img.close()
                                upscaled.close()
                            
                            # Remove main input file so GPU binary doesn't re-process
                            if os.path.exists(bmp_path):
                                os.remove(bmp_path)
                        except Exception as e:
                            fn = os.path.basename(orig_path)
                            self.root.after(0, lambda f=fn, err=str(e):
                                self._log(f"⚠ LANCZOS {f}: {err}", 'warning'))
                    
                    # Merge LANCZOS results back into file_meta for Stage 3 assembly
                    # (file_meta already contains both — Stage 3 will find outputs in current_out)
                
                # Update file_meta count for GPU progress tracking
                # (only AI files go through GPU)
                gpu_ok = True  # Default: true (skipped GPU = success for stage 3)
                vram_failed = [False]
                
                if not ai_meta:
                    # All files were LANCZOS — skip GPU entirely for this batch  
                    self.root.after(0, lambda b=batch_num:
                        self._log(f"⚡ Batch {b}: all LANCZOS — skipping GPU entirely!", 'success'))
                
                run_gpu = bool(ai_meta)  # Flag: skip entire GPU Stage 2 if no AI files
                
                # ALPHA QUARANTINE: Move alpha files out of input dir
                # The AI binary will try to upscale them, doubling VRAM usage
                # and potentially causing vkAllocateMemory failures
                alpha_dir = os.path.join(tmpdir, "alpha_hold")
                os.makedirs(alpha_dir, exist_ok=True)
                for af in list(os.listdir(in_dir)):
                    if '_alpha.' in af:
                        shutil.move(os.path.join(in_dir, af), os.path.join(alpha_dir, af))
                
                # PIPELINE OVERLAP: Pre-extract NEXT batch while GPU runs THIS batch
                if run_gpu and batch_idx + 1 < num_batches and not self.cancel_flag:
                    next_start = (batch_idx + 1) * batch_size
                    next_end = min(next_start + batch_size, len(to_process))
                    next_batch_files = to_process[next_start:next_end]
                    next_batch_num = batch_idx + 2
                    next_scratch = get_scratch_dir()
                    next_tmpdir = os.path.join(next_scratch, f"vtf_batch_{next_batch_num}_{int(time.time())}")
                    os.makedirs(next_tmpdir, exist_ok=True)
                    next_in_dir = os.path.join(next_tmpdir, "input")
                    os.makedirs(next_in_dir, exist_ok=True)
                    pre_extract_result.clear()
                    pre_extract_dirs[0] = next_tmpdir
                    pre_extract_dirs[1] = next_in_dir
                    pre_extract_thread = threading.Thread(
                        target=_do_pre_extract,
                        args=(next_batch_files, next_in_dir, pre_extract_result, processor, cpu_workers),
                        daemon=True)
                    pre_extract_thread.start()
                
                # STAGE 2: RealESRGAN batch folder (GPU)
                # For 8x: two 4x passes. For 4x or less: single pass.
                num_gpu_passes = 2 if ai_scale > 4 else 1
                gpu_files_done = [0]
                
                def gpu_progress(done_count, line_str):
                    gpu_files_done[0] = done_count
                    if 'vkAllocateMemory failed' in line_str or 'vkWaitForFences failed' in line_str or 'vkQueueSubmit failed' in line_str:
                        vram_failed[0] = True
                    batch_total = len(file_meta)
                    pct = min(done_count / batch_total * 100, 100) if batch_total > 0 else 0
                    self.root.after(0, lambda d=done_count, bt=batch_total, p=pct, bn=batch_num, gp=current_pass[0], tp=num_gpu_passes:
                        self.status_label.configure(text=f"🔥 GPU batch {bn} pass {gp}/{tp}: {d}/{bt} ({p:.0f}%)"))
                
                # GPU log with deduplication to prevent error spam
                gpu_error_counts = {}
                def gpu_log(line_str):
                    """Forward GPU binary output to GUI log with dedup."""
                    # Skip noisy tile percentage lines
                    if '%' in line_str and 'done' not in line_str and 'failed' not in line_str:
                        return
                    # Deduplicate repeated error lines
                    for err_key in ['vkAllocateMemory failed', 'vkWaitForFences failed', 'vkQueueSubmit failed']:
                        if err_key in line_str:
                            gpu_error_counts[err_key] = gpu_error_counts.get(err_key, 0) + 1
                            if gpu_error_counts[err_key] == 1:
                                self.root.after(0, lambda ls=line_str: self._log(f"⚠ GPU: {ls}", 'warning'))
                            return  # Don't spam repeats
                    # Skip GPU capability dump lines (repeated 8x per batch, UI noise)
                    if any(s in line_str for s in ['queueC=', 'fp16-', 'int8-', 'subgroup=', 'fp16-cm=', 'bf16-', 'fp8-']):
                        return
                    self.root.after(0, lambda ls=line_str: self._log(f"[GPU] {ls}", 'ai'))
                
                current_pass = [0]
                current_in = in_dir
                current_out = out_dir
                
                for pass_num in range(1, num_gpu_passes + 1):
                    if not run_gpu:
                        break  # Skip GPU passes entirely for LANCZOS-only batches
                    if self.cancel_flag:
                        gpu_ok = False
                        break
                    
                    current_pass[0] = pass_num
                    gpu_files_done[0] = 0
                    vram_failed[0] = False
                    
                    if pass_num > 1:
                        # OPT 3: Smart 8x pass-2 skip — only re-process files not yet at target
                        target_res = self.config.get('target_resolution', 4096)
                        pass2_needed = []
                        pass2_skip = 0
                        pass2_out = os.path.join(tmpdir, f"output_pass{pass_num}")
                        os.makedirs(pass2_out, exist_ok=True)
                        
                        for out_file in os.listdir(current_out):
                            out_path = os.path.join(current_out, out_file)
                            try:
                                with Image.open(out_path) as img:
                                    w, h = img.size
                                if w >= target_res or h >= target_res:
                                    pass2_skip += 1
                                    # Hard link (zero-copy) to final output
                                    dst = os.path.join(pass2_out, out_file)
                                    try:
                                        os.link(out_path, dst)
                                    except OSError:
                                        shutil.copy2(out_path, dst)
                                else:
                                    pass2_needed.append(out_file)
                            except:
                                pass2_needed.append(out_file)
                        
                        if pass2_skip > 0:
                            self.root.after(0, lambda s=pass2_skip, n=len(pass2_needed):
                                self._log(f"⚡ 8x skip: {s} already at target, {n} need pass 2", 'ai'))
                        
                        if not pass2_needed:
                            # All files already at target, skip pass 2 entirely
                            current_out = pass2_out
                            self.root.after(0, lambda b=batch_num:
                                self._log(f"⚡ GPU batch {b}: all files at target after pass 1, skipping pass 2!", 'success'))
                            continue
                        
                        # KEY FIX: Create a pass 2 input dir with ONLY files that need processing
                        # This prevents the GPU from re-upscaling already-at-target files (4096→16384)
                        pass2_in = os.path.join(tmpdir, f"input_pass{pass_num}")
                        os.makedirs(pass2_in, exist_ok=True)
                        for needed_file in pass2_needed:
                            src = os.path.join(current_out, needed_file)
                            dst = os.path.join(pass2_in, needed_file)
                            try:
                                os.link(src, dst)  # Zero-copy hard link
                            except OSError:
                                shutil.copy2(src, dst)
                        
                        # Feed ONLY needed files as input for pass 2
                        self.root.after(0, lambda b=batch_num, p=pass_num: 
                            self._log(f"🔄 GPU batch {b} pass {p}/{num_gpu_passes} (8x multi-pass)...", 'ai'))
                        current_in = pass2_in
                        current_out = pass2_out
                    else:
                        self.root.after(0, lambda b=batch_num, np=num_gpu_passes:
                            self.status_label.configure(text=f"🔥 GPU batch {b} pass 1/{np}..."))
                    
                    timeout = max(300, len(file_meta) * 30)
                    gpu_ok = processor.ai_upscaler.upscale_batch(
                        current_in, current_out, scale=min(ai_scale, 4), model=ai_model,
                        timeout=timeout, progress_callback=gpu_progress, log_callback=gpu_log)
                
                if vram_failed[0] and gpu_ok:
                    corrupt_files = []
                    for out_file in os.listdir(current_out):
                        out_path = os.path.join(current_out, out_file)
                        try:
                            with Image.open(out_path) as img:
                                check = img.convert('RGB')
                                w, h = check.size
                                crop_size = min(256, w, h)
                                cx, cy = w // 2, h // 2
                                sample = check.crop((cx - crop_size//2, cy - crop_size//2,
                                                     cx + crop_size//2, cy + crop_size//2))
                                pixels = np.array(sample, dtype=np.float32)
                                mean_val = pixels.mean()
                                std_val = pixels.std()
                                # Same thresholds as assembly-stage dark detection
                                if mean_val < 5 or std_val < 2:
                                    corrupt_files.append(out_file)
                                    os.remove(out_path)
                        except:
                            corrupt_files.append(out_file)
                            try: os.remove(out_path)
                            except: pass
                    
                    if corrupt_files:
                        self.root.after(0, lambda n=len(corrupt_files), b=batch_num:
                            self._log(f"⚠ GPU batch {b}: {n} corrupted outputs detected — re-processing with safe settings...", 'warning'))
                        # Re-process ONLY the corrupted files with safe settings
                        safe_in = os.path.join(tmpdir, 'safe_input')
                        os.makedirs(safe_in, exist_ok=True)
                        for cf in corrupt_files:
                            # Find corresponding input file
                            base = os.path.splitext(cf)[0]
                            for ext in ['bmp', 'png']:
                                src = os.path.join(current_in, f"{base}.{ext}")
                                if os.path.exists(src):
                                    shutil.copy2(src, os.path.join(safe_in, f"{base}.{ext}"))
                                    break
                        if os.listdir(safe_in):
                            time.sleep(2)  # Let GPU recover from error state
                            safe_config = dict(self.config)
                            safe_config['tile_size'] = 256  # Fixed safe tile (not auto)
                            # gpu_threads: auto-calculated in _build_cmd (will be 1:1:1 for small batch)
                            safe_upscaler = AIUpscaler(REALESRGAN_EXE, safe_config)
                            safe_upscaler.upscale_batch(
                                safe_in, current_out, scale=min(ai_scale, 4), model=ai_model,
                                timeout=max(300, len(corrupt_files) * 60), log_callback=gpu_log)
                            # Verify safe retry outputs
                            still_corrupt = 0
                            for cf in corrupt_files:
                                base = os.path.splitext(cf)[0]
                                out_path = os.path.join(current_out, f"{base}.{AIUpscaler.BATCH_OUTPUT_FMT}")
                                if os.path.exists(out_path):
                                    try:
                                        with Image.open(out_path) as vimg:
                                            varr = np.array(vimg.convert('RGB'), dtype=np.float32)
                                            if varr.mean() < 5:
                                                still_corrupt += 1
                                    except:
                                        still_corrupt += 1
                            if still_corrupt > 0:
                                self.root.after(0, lambda n=still_corrupt, b=batch_num:
                                    self._log(f"⚠ GPU batch {b}: {n} files still dark after safe retry", 'warning'))
                            else:
                                self.root.after(0, lambda n=len(corrupt_files), b=batch_num:
                                    self._log(f"✓ GPU batch {b}: {n} files re-processed with safe settings", 'success'))
                    else:
                        self.root.after(0, lambda b=batch_num:
                            self._log(f"⚡ GPU batch {b}: VRAM warning but all outputs valid", 'ai'))
                
                if not gpu_ok:
                    if vram_failed[0]:
                        self.root.after(0, lambda b=batch_num: self._log(f"⚠ GPU batch {b}: VRAM failed — retrying with safe settings (threads=1:1:1)...", 'warning'))
                        time.sleep(2)  # Let GPU recover
                        # Clean corrupted output and retry with safe GPU settings
                        for f in os.listdir(current_out):
                            os.remove(os.path.join(current_out, f))
                        safe_config = dict(self.config)
                        safe_config['tile_size'] = 256  # Fixed safe tile
                        # gpu_threads: auto-calculated in _build_cmd (will be 1:1:1 for small batch)
                        safe_upscaler = AIUpscaler(REALESRGAN_EXE, safe_config)
                        vram_failed[0] = False
                        gpu_ok = safe_upscaler.upscale_batch(
                            current_in, current_out, scale=min(ai_scale, 4), model=ai_model,
                            timeout=timeout * 2, progress_callback=gpu_progress, log_callback=gpu_log)
                        if not gpu_ok:
                            self.root.after(0, lambda b=batch_num: self._log(f"✗ GPU batch {b}: failed even with safe settings!", 'error'))
                            fail += len(file_meta)
                            completed += len(file_meta)
                            self.root.after(0, lambda p=completed/total*100: self.progress_var.set(p / 100 if CTK_AVAILABLE else p))
                            continue
                        else:
                            self.root.after(0, lambda b=batch_num: self._log(f"✓ GPU batch {b}: safe mode succeeded!", 'success'))
                    else:
                        # Check for partial success: if most output files exist, treat as partial success
                        outputs_present = 0
                        for bmp_name in ai_meta:
                            base = os.path.splitext(bmp_name)[0]
                            for ext in ['png', 'bmp', 'webp', 'jpg']:
                                if os.path.exists(os.path.join(current_out, f"{base}.{ext}")):
                                    outputs_present += 1
                                    break
                        if outputs_present > 0:
                            # Partial success: GPU had errors but produced some outputs
                            missing = len(ai_meta) - outputs_present
                            self.root.after(0, lambda b=batch_num, ok=outputs_present, m=missing:
                                self._log(f"⚠ GPU batch {b}: partial success ({ok} done, {m} failed)", 'warning'))
                            # Don't skip assembly — let it handle what's available
                        else:
                            self.root.after(0, lambda b=batch_num: self._log(f"✗ GPU batch {b} failed!", 'error'))
                            fail += len(file_meta)
                            completed += len(file_meta)
                            self.root.after(0, lambda p=completed/total*100: self.progress_var.set(p / 100 if CTK_AVAILABLE else p))
                            continue
                
                # Restore quarantined alpha files for assembly to find
                for af in os.listdir(alpha_dir):
                    shutil.move(os.path.join(alpha_dir, af), os.path.join(in_dir, af))
                
                # STAGE 3: Assemble VTFs (CPU parallel)
                def assemble_one(item):
                    bmp_name, (orig_path, meta) = item
                    base_name = os.path.splitext(bmp_name)[0]
                    frame_count = meta.get('frame_count', 1)
                    
                    # Resilient output file matching - try all supported formats
                    # Use current_out (final pass dir for 8x multi-pass)
                    upscaled_path = None
                    final_out = current_out
                    
                    if frame_count > 1:
                        # For animated VTFs: find frame 0 output (main BMP was removed as duplicate)
                        for ext in ['png', 'bmp', 'webp', 'jpg']:
                            candidate = os.path.join(final_out, f"{base_name}_frame0.{ext}")
                            if os.path.exists(candidate) and os.path.getsize(candidate) > 256:
                                upscaled_path = candidate
                                break
                    else:
                        for ext in ['bmp', 'png', 'webp', 'jpg']:
                            candidate = os.path.join(final_out, f"{base_name}.{ext}")
                            if os.path.exists(candidate) and os.path.getsize(candidate) > 256:
                                upscaled_path = candidate
                                break
                    
                    if not upscaled_path:
                        return orig_path, False, "Upscaled output not found"
                    
                    if output_to_addon:
                        output_vtf = self._get_addon_output_path(orig_path)
                        os.makedirs(os.path.dirname(output_vtf), exist_ok=True)
                    else:
                        output_vtf = orig_path
                    
                    ok, msg = processor.assemble_vtf(upscaled_path, output_vtf, orig_path, meta)
                    return orig_path, ok, msg
                
                with ThreadPoolExecutor(max_workers=cpu_workers) as cpu_pool:
                    for orig_path, ok, msg in cpu_pool.map(assemble_one, file_meta.items()):
                        completed += 1
                        fn = os.path.basename(orig_path)
                        if ok:
                            success += 1
                            self._mark_history(process_history, orig_path, 'completed', msg)
                            # Track flagged files for corruption report
                            if '⚠️' in msg:
                                flagged_files.append(fn)
                                self.root.after(0, lambda f=fn, m=msg: self._log(f"⚠ {f}: {m}", 'warning'))
                            else:
                                self.root.after(0, lambda f=fn, m=msg: self._log(f"✓ {f}: {m}", 'success'))
                        else:
                            fail += 1
                            self._mark_history(process_history, orig_path, 'failed', msg)
                            self.root.after(0, lambda f=fn, m=msg: self._log(f"✗ {f}: {m}", 'error'))
                        
                        elapsed = time.time() - start_time
                        rate = completed / elapsed if elapsed > 0 else 0
                        remaining = (total - completed) / rate if rate > 0 else 0
                        # Throttle UI updates: max once/sec to prevent GDI exhaustion
                        _now = time.time()
                        if _now - getattr(self, "_last_ui_update", 0) >= 1.0:
                            self._last_ui_update = _now
                            if remaining > 3600:
                                eta = f"{remaining/3600:.1f}h"
                            elif remaining > 60:
                                eta = f"{remaining/60:.0f}m {remaining%60:.0f}s"
                            else:
                                eta = f"{remaining:.0f}s"
                            elapsed_str = f"{elapsed/60:.0f}m" if elapsed > 60 else f"{elapsed:.0f}s"
                            rate_str = f"{rate*60:.1f}/min" if rate > 0 else "--"
                            self.root.after(0, lambda p=completed/total*100: self.progress_var.set(p / 100 if CTK_AVAILABLE else p))
                            self.root.after(0, lambda c=completed, t=total, e=eta, el=elapsed_str, r=rate_str:
                                self.status_label.configure(text=f"{c}/{t} | ETA: {e} | Elapsed: {el} | {r}"))
                            self.root.after(0, lambda c=completed, t=total, e=eta, el=elapsed_str, r=rate_str:
                                self.eta_label.configure(text=f"\u23f1 {c}/{t} processed \u2014 {e} remaining \u2014 {r}"))
                            # Update bottom bar stats
                            def _update_bottom(c=completed, t=total, e=eta, r=rate_str):
                                try:
                                    if hasattr(self, 'bottom_pct'):
                                        pct_val = c/t*100 if t > 0 else 0
                                        self.bottom_pct.configure(text=f"{pct_val:.0f}%")
                                        self.bottom_eta.configure(text=f"ETA: {e}")
                                        self.bottom_count.configure(text=f"{c}/{t}")
                                        self.bottom_speed.configure(text=f"{r}")
                                except Exception:
                                    pass
                            self.root.after(0, _update_bottom)
                        
                        # Track per-file stats
                        if ok and os.path.exists(orig_path):
                            try:
                                out_path = orig_path
                                if output_to_addon:
                                    out_path = self._get_addon_output_path(orig_path)
                                if os.path.exists(out_path):
                                    self._batch_stats['output_size_bytes'] += os.path.getsize(out_path)
                            except:
                                pass
            finally:
                # OPT 2: Cleanup scratch dirs (RAM disk input + SSD output)
                try:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    shutil.rmtree(out_tmpdir, ignore_errors=True)
                except:
                    pass
            # Save history after each batch for crash resilience
            self._save_history(process_history)
        
        # Final history save
        self._save_history(process_history)
        
        elapsed = time.time() - start_time
        rate_str = f"{success / (elapsed/60):.1f}/min" if elapsed > 60 else f"{success / elapsed:.1f}/sec" if elapsed > 0 else "--"
        self.root.after(0, lambda: self._log(f"✅ Done in {elapsed/60:.1f}min ({rate_str}) | ✓{success} ⊖{skip} ✗{fail}", 'success'))
        
        # ── Batch Stats Summary ──────────────────────────────────
        stats = getattr(self, '_batch_stats', {})
        if stats and success > 0:
            in_mb = stats.get('input_size_bytes', 0) / (1024 * 1024)
            out_mb = stats.get('output_size_bytes', 0) / (1024 * 1024)
            avg_time = elapsed / success if success > 0 else 0
            
            self.root.after(0, lambda: self._log("─" * 50, 'ai'))
            self.root.after(0, lambda: self._log("📊 BATCH STATS", 'ai'))
            self.root.after(0, lambda el=elapsed: self._log(f"  ⏱ Total Time:      {el/60:.1f} min ({el:.0f}s)", 'ai'))
            self.root.after(0, lambda s=success, sk=skip, f_=fail: self._log(f"  📁 Files:           {s} upscaled, {sk} skipped, {f_} failed", 'ai'))
            if in_mb > 0:
                self.root.after(0, lambda i=in_mb: self._log(f"  📥 Input Size:      {i:.1f} MB", 'ai'))
            if out_mb > 0:
                ratio = out_mb / in_mb if in_mb > 0 else 0
                self.root.after(0, lambda o=out_mb, r=ratio: self._log(f"  📤 Output Size:     {o:.1f} MB ({r:.1f}x)", 'ai'))
            self.root.after(0, lambda a=avg_time: self._log(f"  ⚡ Avg Per File:    {a:.1f}s", 'ai'))
            self.root.after(0, lambda r=rate_str: self._log(f"  🚀 Processing Rate: {r}", 'ai'))
            self.root.after(0, lambda: self._log("─" * 50, 'ai'))
        
        # Clear ETA label
        self.root.after(0, lambda: self.eta_label.configure(text=""))
        
        # Corruption report
        if flagged_files:
            self.root.after(0, lambda: self._log(f"⚠️ CORRUPTION REPORT: {len(flagged_files)} file(s) flagged for review:", 'warning'))
            for fn in flagged_files:
                self.root.after(0, lambda f=fn: self._log(f"  → {f}", 'warning'))
            self.root.after(0, lambda: self._log("Check these files in-game. Restore from .bak if needed.", 'warning'))
        
        # Close log file and report
        if hasattr(self, '_log_file') and self._log_file and not self._log_file.closed:
            self._log_file.write(f"\n=== Completed: {success} success, {skip} skipped, {fail} failed in {elapsed/60:.1f}min ===\n")
            self._log_file.close()
            log_path = getattr(self, '_log_file_path', '')
            self.root.after(0, lambda p=log_path: self._log(f"📝 Log saved: {p}", 'ai'))
        
        self.root.after(0, self._done)
    
    def _compare_mode(self):
        """Compare backup (.bak) original vs current upscaled file side-by-side."""
        if not self.file_list or not PIL_AVAILABLE or not SRCTOOLS_AVAILABLE:
            self._log("⚠ No files loaded or missing dependencies", 'warning')
            return
        
        # Get currently selected file
        filepath = None
        if 0 <= self.current_file_index < len(self.file_list):
            filepath = self.file_list[self.current_file_index]
        
        if not filepath:
            self._log("⚠ Select a file first", 'warning')
            return
        
        filename = os.path.basename(filepath)
        bak_path = filepath + ".bak"
        
        # Also check addon output path
        addon_path = None
        try:
            addon_path = self._get_addon_output_path(filepath)
        except:
            pass
        
        processor = VTFProcessor(self.config)
        
        if os.path.exists(bak_path):
            # Compare: backup (original) vs current file (upscaled)
            self._log(f"🔍 Compare: {filename} — backup vs upscaled", 'ai')
            
            # Left pane: backup (original)
            original_img = processor.load_vtf_image(bak_path)
            if original_img:
                w_bak, h_bak, fmt_bak, _, _, _ = read_vtf_header(bak_path)
                self.orig_info_label.configure(text=f"Backup: {w_bak}x{h_bak} ({fmt_bak})")
                self._display_image(original_img, self.original_canvas, "original")
            else:
                self.orig_info_label.configure(text="⚠ Failed to load backup")
            
            # Right pane: current (upscaled)
            upscaled_img = processor.load_vtf_image(filepath)
            if upscaled_img:
                w_up, h_up, fmt_up, _, _, _ = read_vtf_header(filepath)
                bak_size = os.path.getsize(bak_path)
                cur_size = os.path.getsize(filepath)
                bak_str = f"{bak_size/1024:.0f}KB" if bak_size < 1048576 else f"{bak_size/1048576:.1f}MB"
                cur_str = f"{cur_size/1024:.0f}KB" if cur_size < 1048576 else f"{cur_size/1048576:.1f}MB"
                self.preview_info_label.configure(text=f"Upscaled: {w_up}x{h_up} ({fmt_up}) | {bak_str} → {cur_str}")
                self._display_image(upscaled_img, self.preview_canvas, "preview")
            else:
                self.preview_info_label.configure(text="⚠ Failed to load upscaled")
        
        elif addon_path and os.path.exists(addon_path):
            # Compare: source file vs addon output
            self._log(f"🔍 Compare: {filename} — source vs addon output", 'ai')
            
            # Left pane: source
            source_img = processor.load_vtf_image(filepath)
            if source_img:
                w_src, h_src, fmt_src, _, _, _ = read_vtf_header(filepath)
                self.orig_info_label.configure(text=f"Source: {w_src}x{h_src} ({fmt_src})")
                self._display_image(source_img, self.original_canvas, "original")
            
            # Right pane: addon output
            addon_img = processor.load_vtf_image(addon_path)
            if addon_img:
                w_out, h_out, fmt_out, _, _, _ = read_vtf_header(addon_path)
                self.preview_info_label.configure(text=f"Addon Output: {w_out}x{h_out} ({fmt_out})")
                self._display_image(addon_img, self.preview_canvas, "preview")
        else:
            self._log(f"⚠ No backup (.bak) or addon output found for {filename}", 'warning')
            self._log("  Run upscaling first, or check backup files exist", 'warning')
    
    def _restore_all_backups(self):
        """Restore all .bak files in the currently opened folder back to their originals."""
        path = self.input_entry.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showwarning("No Folder", "Open a folder first before restoring.")
            return
        
        # Find all .bak files
        bak_files = []
        for root, _, files in os.walk(path):
            for f in files:
                if f.lower().endswith('.vtf.bak'):
                    bak_files.append(os.path.join(root, f))
        
        if not bak_files:
            messagebox.showinfo("No Backups", f"No .vtf.bak backup files found in:\n{path}")
            return
        
        if not messagebox.askyesno("Restore Backups",
                                   f"Found {len(bak_files)} backup files.\n\n"
                                   f"This will overwrite current VTFs with their original backups.\n\n"
                                   f"Continue?"):
            return
        
        restored = 0
        failed = 0
        for bak_path in bak_files:
            original_path = bak_path[:-4]  # Remove .bak extension
            try:
                shutil.copy2(bak_path, original_path)
                restored += 1
            except Exception as e:
                self._log(f"✗ Failed to restore: {os.path.basename(original_path)} — {e}", 'error')
                failed += 1
        
        self._log(f"♻ Restored {restored} files from backups ({failed} failed)", 'success')
        messagebox.showinfo("Restore Complete", f"Restored {restored} files.\n{failed} failed.")
        self._refresh_files()
    
    def _clear_ram(self):
        """Force Python garbage collection and trim OS working set to free RAM."""
        import gc
        
        # Get baseline
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            before_mb = proc.memory_info().rss / 1048576
        except:
            before_mb = 0
        
        # Release loaded images (biggest RAM consumers)
        if hasattr(self, 'original_pil') and self.original_pil:
            self.original_pil.close()
            self.original_pil = None
        if hasattr(self, 'original_photo'):
            self.original_photo = None  # Release GDI handle
        if hasattr(self, 'preview_photo'):
            self.preview_photo = None  # Release GDI handle
        # Clear canvases to release tkinter image references
        if hasattr(self, 'original_canvas'):
            self.original_canvas.delete("all")
        if hasattr(self, 'preview_canvas'):
            self.preview_canvas.delete("all")
        
        # Clear scan cache (can hold metadata for thousands of files)
        if hasattr(self, '_scan_cache'):
            self._scan_cache = {}
        
        # Clear batch stats
        if hasattr(self, '_batch_stats'):
            self._batch_stats = {}
        
        # Clear Python internals
        gc.collect()
        gc.collect()  # Double collect for cyclic refs
        
        # Clear PIL image cache
        if PIL_AVAILABLE:
            try:
                Image.core.reset()
            except:
                pass
        
        # Trim Windows working set (release pages back to OS)
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetCurrentProcess()
            kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except:
            pass
        
        # Get after
        try:
            after_mb = proc.memory_info().rss / 1048576
            freed = before_mb - after_mb
            self._log(f"🧹 RAM cleared: {before_mb:.0f}MB → {after_mb:.0f}MB (freed {freed:.0f}MB)", 'success')
        except:
            self._log("🧹 RAM cleared (GC + working set trim)", 'success')
    
    def _clear_vram(self):
        """Release GPU VRAM by clearing caches and forcing context reset."""
        if not PYNVML_AVAILABLE:
            self._log("⚠ pynvml not available — cannot monitor VRAM", 'warning')
            return
        
        try:
            gpu_id = self.config.get("gpu_id", 0)
            handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
            before = pynvml.nvmlDeviceGetMemoryInfo(handle)
            before_used = before.used / 1048576
        except:
            before_used = 0
        
        # Force Vulkan/GPU cleanup via subprocess
        # Kill any lingering RealESRGAN processes that might hold VRAM
        try:
            import psutil
            for proc in psutil.process_iter(['name', 'pid']):
                if proc.info['name'] and 'realesrgan' in proc.info['name'].lower():
                    proc.kill()
                    self._log(f"  Killed lingering GPU process: {proc.info['name']} (PID {proc.info['pid']})", 'warning')
        except:
            pass
        
        # Small delay for GPU driver to reclaim memory
        time.sleep(1)
        
        try:
            after = pynvml.nvmlDeviceGetMemoryInfo(handle)
            after_used = after.used / 1048576
            total_mb = after.total / 1048576
            freed = before_used - after_used
            self._log(f"🎮 VRAM cleared: {before_used:.0f}MB → {after_used:.0f}MB / {total_mb:.0f}MB (freed {freed:.0f}MB)", 'success')
        except:
            self._log("🎮 VRAM cleanup attempted", 'success')
    
    def _flush_all(self):
        """Full system cleanup: clear RAM + VRAM + temp files + caches."""
        self._log("⚡ Flushing all resources...", 'ai')
        
        # 1. Clear ALL temp/scratch files and subdirectories
        scratch = get_scratch_dir()
        cleaned_files = 0
        cleaned_bytes = 0
        try:
            if os.path.isdir(scratch):
                for item in os.listdir(scratch):
                    item_path = os.path.join(scratch, item)
                    try:
                        if os.path.isfile(item_path):
                            cleaned_bytes += os.path.getsize(item_path)
                            os.remove(item_path)
                            cleaned_files += 1
                        elif os.path.isdir(item_path):
                            # Clean ALL subdirs (vtf_batch_*, vtf_out_*, _batch_*, etc.)
                            for root, dirs, files in os.walk(item_path):
                                for f in files:
                                    try:
                                        cleaned_bytes += os.path.getsize(os.path.join(root, f))
                                    except:
                                        pass
                            shutil.rmtree(item_path, ignore_errors=True)
                            cleaned_files += 1
                    except:
                        pass
            cleaned_mb = cleaned_bytes / (1024 * 1024)
            self._log(f"  Cleaned {cleaned_files} items ({cleaned_mb:.0f} MB) from {scratch}", 'success')
        except:
            pass
        
        # 2. Clear query cache
        self._scan_cache = {}
        self._log("  Scan cache cleared", 'success')
        
        # 3. Clear RAM
        self._clear_ram()
        
        # 4. Clear VRAM
        self._clear_vram()
        
        # 5. Force Windows system file cache flush
        try:
            import ctypes
            # Empty system working set (requires admin, will silently fail if not)
            ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
        except:
            pass
        
        # 6. Trim log widget
        line_count = int(self.log_text.index('end-1c').split('.')[0])
        if line_count > 100:
            self.log_text.delete('1.0', f'{line_count - 50}.0')
        
        self._log("⚡ Flush complete — PC should be at full capacity", 'success')
    
    def _done(self):
        self.processing = False
        self.start_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        self.status_label.configure(text="Complete!")
        # Auto-save config on completion
        self._save_config()
        # Auto-cleanup: release batch memory and scratch files
        import gc
        self._batch_stats = {}
        # Clean scratch dirs from this batch
        try:
            scratch = get_scratch_dir()
            if os.path.isdir(scratch):
                for item in os.listdir(scratch):
                    item_path = os.path.join(scratch, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path, ignore_errors=True)
        except:
            pass
        gc.collect()
        gc.collect()
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetCurrentProcess()
            kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except:
            pass
        # Play completion sound
        if WINSOUND_AVAILABLE:
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except:
                pass
    
    def _on_close(self):
        """Auto-save config and cleanup when window closes."""
        self._save_config()
        # Close log file handle
        if hasattr(self, '_log_file') and self._log_file and not self._log_file.closed:
            try:
                self._log_file.close()
            except:
                pass
        # Final scratch cleanup
        try:
            scratch = get_scratch_dir()
            if os.path.isdir(scratch):
                for item in os.listdir(scratch):
                    item_path = os.path.join(scratch, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path, ignore_errors=True)
        except:
            pass
        self.root.destroy()
    
    def _update_vram(self):
        """Periodically update VRAM usage display."""
        if PYNVML_AVAILABLE and hasattr(self, 'vram_label'):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(self.config.get("gpu_id", 0))
                info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                used_mb = info.used / 1048576
                total_mb = info.total / 1048576
                pct = (info.used / info.total) * 100
                self.vram_label.configure(text=f"VRAM: {used_mb:.0f}/{total_mb:.0f} MB ({pct:.0f}%)")
            except:
                self.vram_label.configure(text="VRAM: N/A")
        # Slower polling during batch processing to reduce GDI pressure
        interval = 5000 if self.processing else 2000
        self.root.after(interval, self._update_vram)
    
    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Start VRAM monitor
        if PYNVML_AVAILABLE:
            self.root.after(1000, self._update_vram)
        self.root.mainloop()


def _write_crash_report(exc_type, exc_value, exc_tb):
    """Write a detailed crash debug report to crash_debug.txt."""
    import traceback as tb_mod
    crash_path = os.path.join(SCRIPT_DIR, "crash_debug.txt")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(crash_path, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*70}\n")
            f.write(f"CRASH REPORT — {timestamp}\n")
            f.write(f"VTF AI Upscaler v{APP_VERSION}\n")
            f.write(f"Python {sys.version}\n")
            f.write(f"{'='*70}\n\n")
            f.write("TRACEBACK:\n")
            tb_mod.print_exception(exc_type, exc_value, exc_tb, file=f)
            f.write("\nSYSTEM INFO:\n")
            f.write(f"  OS: {os.name} / {sys.platform}\n")
            f.write(f"  CWD: {os.getcwd()}\n")
            f.write(f"  Script: {SCRIPT_DIR}\n")
            f.write(f"  RealESRGAN: {REALESRGAN_EXE} (exists={REALESRGAN_EXE.exists()})\n")
            if PYNVML_AVAILABLE:
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    f.write(f"  VRAM: {info.used/1048576:.0f}/{info.total/1048576:.0f} MB\n")
                except:
                    f.write("  VRAM: unavailable\n")
            try:
                import psutil
                proc = psutil.Process(os.getpid())
                f.write(f"  RAM: {proc.memory_info().rss/1048576:.0f} MB\n")
            except:
                pass
            # Dump config if available
            config_path = os.path.join(SCRIPT_DIR, CONFIG_FILE)
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r') as cf:
                        f.write(f"\nCONFIG ({config_path}):\n{cf.read()}\n")
                except:
                    pass
            f.write(f"\n{'='*70}\n")
        print(f"\n💀 CRASH — Debug report saved to: {crash_path}")
        print(f"   Send this file for debugging.\n")
    except Exception as report_err:
        print(f"Failed to write crash report: {report_err}")
        tb_mod.print_exception(exc_type, exc_value, exc_tb)


def main():
    initial_folder = None
    auto_start = False
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if os.path.isdir(arg):
            initial_folder = arg
            auto_start = True
    try:
        app = VTFUpscalerGUI(initial_folder=initial_folder, auto_start=auto_start)
        app.run()
    except Exception:
        _write_crash_report(*sys.exc_info())
        raise


if __name__ == "__main__":
    # Install global exception handler for unhandled crashes
    _original_excepthook = sys.excepthook
    def _crash_handler(exc_type, exc_value, exc_tb):
        if exc_type != KeyboardInterrupt:
            _write_crash_report(exc_type, exc_value, exc_tb)
        _original_excepthook(exc_type, exc_value, exc_tb)
    sys.excepthook = _crash_handler
    main()
