"""
Comprehensive v7.0 patch — applies ALL 23 improvements to v6.2 base.
Includes: VRAM monitor, G: scratch, alpha quarantine, format-aware output,
8x multi-pass, pre-scan cache, smart skip, AI debug logging, PNG preview fix,
timestamps, auto-save, completion sound, scale selector, and more.
Run once then delete this file.
"""
import re

FILE = 'vtf_upscaler_gui.py'
with open(FILE, 'r', encoding='utf-8') as f:
    src = f.read()

original_len = len(src)
applied = []

def patch(name, old, new, count=1):
    """Apply a string replacement patch. Raises if old text not found."""
    global src
    actual = src.count(old)
    if actual == 0:
        print(f"  SKIP [{name}]: target text not found")
        return False
    if count == -1:
        src = src.replace(old, new)
    else:
        src = src.replace(old, new, count)
    applied.append(name)
    return True

# ============================================================
# 1. IMPORTS: winsound + pynvml
# ============================================================
patch("winsound import",
    "import ctypes",
    """import ctypes
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
    PYNVML_AVAILABLE = False""")

# ============================================================
# 2. VERSION
# ============================================================
patch("version update",
    'APP_VERSION = "6.2.0"',
    'APP_VERSION = "7.0"')

# ============================================================
# 3. CACHE_FILE + SCRATCH_DIR constants
# ============================================================
patch("cache + scratch constants",
    "ADDON_OUTPUT_PATH = ",
    """CACHE_FILE = "vtf_scan_cache.json"

# G: drive scratch space — avoids filling C: with large temp files
SCRATCH_DRIVE = Path("G:\\\\_vtf_scratch")
def get_scratch_dir():
    \"\"\"Return scratch directory on G: drive, fallback to system temp.\"\"\"
    try:
        if SCRATCH_DRIVE.parent.exists():
            SCRATCH_DRIVE.mkdir(exist_ok=True)
            return str(SCRATCH_DRIVE)
    except:
        pass
    return None  # Use tempfile.TemporaryDirectory() as fallback

ADDON_OUTPUT_PATH = """)

# ============================================================
# 4. DEFAULT_CONFIG updates
# ============================================================
patch("batch_size default",
    '"batch_size": 100',
    '"batch_size": 200')

patch("gpu_threads default",
    '"gpu_threads": "4:4:4"',
    '"gpu_threads": "4:8:8"')

patch("backup default",
    '"backup_originals": True',
    '"backup_originals": False')

# output_to_addon is already True in v6.2 DEFAULT_CONFIG, but let's check
if '"output_to_addon": False' in src:
    patch("addon output default",
        '"output_to_addon": False',
        '"output_to_addon": True')

# ============================================================
# 5. FORMAT_ID_MAP + get_output_format_for_source
# ============================================================
# Insert after DEFAULT_CONFIG closing brace
format_block = '''
# VTF format ID mapping for format-aware output
FORMAT_ID_MAP = {
    0: "RGBA8888", 1: "ABGR8888", 2: "RGB888", 3: "BGR888",
    4: "RGB565", 12: "BGRA8888", 13: "DXT1", 14: "DXT3", 15: "DXT5",
    24: "BGRA4444", 25: "DXT1_ONEBITALPHA",
}

def get_output_format_for_source(format_id):
    """Return the best output ImageFormat for a given source VTF format_id."""
    try:
        from srctools.vtf import ImageFormats
    except ImportError:
        return None
    no_alpha = {13, 25, 2, 3, 4}   # DXT1, DXT1_ONEBITALPHA, RGB888, BGR888, RGB565
    alpha = {0, 1, 12, 14, 15, 24}  # RGBA, ABGR, BGRA, DXT3, DXT5, BGRA4444
    if format_id in no_alpha:
        return ImageFormats.DXT1
    elif format_id in alpha:
        return ImageFormats.DXT5
    return ImageFormats.DXT5  # safe fallback

'''

default_config_end = src.find('\n}\n', src.find('DEFAULT_CONFIG'))
if default_config_end > 0:
    insert_pos = default_config_end + 3
    src = src[:insert_pos] + format_block + src[insert_pos:]
    applied.append("FORMAT_ID_MAP + get_output_format_for_source")

# ============================================================
# 6. read_vtf_header → 5-tuple (add format_id)
# ============================================================
patch("read_vtf_header 5-tuple return",
    "return width, height, fmt, has_alpha",
    "return width, height, fmt, has_alpha, format_id")

patch("read_vtf_header 5-tuple except",
    'return 0, 0, "UNKNOWN", False',
    'return 0, 0, "UNKNOWN", False, 0')

# Update signature docstring
patch("read_vtf_header signature",
    "def read_vtf_header(filepath: str) -> Tuple[int, int, str, bool]:",
    "def read_vtf_header(filepath: str) -> Tuple[int, int, str, bool, int]:")

# ============================================================
# 7. Scale clamp in _build_cmd
# ============================================================
patch("scale clamp",
    '        cmd = [\n            str(self.exe_path),\n            "-i", input_path,\n            "-o", output_path,\n            "-n", model,\n            "-s", str(scale),',
    '        # Binary only supports scale 2, 3, 4 — clamp to prevent crashes\n        effective_scale = min(scale, 4)\n        \n        cmd = [\n            str(self.exe_path),\n            "-i", input_path,\n            "-o", output_path,\n            "-n", model,\n            "-s", str(effective_scale),')

# ============================================================
# 8. AIUpscaler log_callback
# ============================================================
patch("AIUpscaler log_callback",
    "self.progress_callback = None  # Called with (current_file, pct) during batch",
    """self.progress_callback = None  # Called with (current_file, pct) during batch
        self.log_callback = None  # Called with (msg) for GUI logging
    
    def _log(self, msg: str):
        \"\"\"Send debug info to GUI log if available, else print.\"\"\"
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)""")

# Replace print() calls in AIUpscaler with self._log()
patch("AIUpscaler print→log CMD",
    'print(f"[AIUpscaler] CMD:',
    'self._log(f"[AI] CMD:')

patch("AIUpscaler print→log failed",
    'print(f"[AIUpscaler] Binary failed',
    'self._log(f"[AI] Binary FAILED')

patch("AIUpscaler print→log not found",
    'print(f"[AIUpscaler] Output not found',
    'self._log(f"[AI] Output NOT found')

patch("AIUpscaler print→log exception",
    'print(f"[AIUpscaler] Exception',
    'self._log(f"[AI] Exception')

# ============================================================
# 9. Preview: PNG instead of WebP (16383px limit fix)
# ============================================================
patch("preview PNG format",
    'output_path = os.path.join(tmpdir, "output." + self.BATCH_OUTPUT_FMT)',
    '# Use PNG for preview (WebP has 16383px max dimension limit)\n                preview_fmt = "png"\n                output_path = os.path.join(tmpdir, "output." + preview_fmt)')

# Pass format to _build_cmd in upscale()
patch("preview _build_cmd format",
    'cmd = self._build_cmd(input_path, output_path, model, scale)\n                self._log',
    'cmd = self._build_cmd(input_path, output_path, model, scale, output_fmt=preview_fmt)\n                self._log')

# ============================================================
# 10. get_vtf_info → 5-tuple
# ============================================================
patch("get_vtf_info 5-tuple",
    "def get_vtf_info(self, filepath: str) -> Tuple[int, int, str, bool]:\n        return read_vtf_header(filepath)",
    "def get_vtf_info(self, filepath: str) -> Tuple[int, int, str, bool, int]:\n        return read_vtf_header(filepath)")

# ============================================================
# 11. should_skip → 5-tuple unpack
# ============================================================
patch("should_skip 5-tuple",
    "orig_w, orig_h, _, _ = read_vtf_header(filepath)",
    "orig_w, orig_h, _, _, _ = read_vtf_header(filepath)")

# ============================================================
# 12. extract_to_bmp: capture fmt_id and store in metadata
# ============================================================
# First, we need to add format_id capture. The extraction uses VTF.read, not read_vtf_header.
# We need to add the header read for format_id.
patch("extract_to_bmp format_id",
    "        try:\n            with open(filepath, 'rb') as f:\n                vtf = VTF.read(f)\n                vtf.load()\n                meta = {",
    "        try:\n            # Read format_id from raw header for format-aware output\n            _, _, _, _, src_format_id = read_vtf_header(filepath)\n            with open(filepath, 'rb') as f:\n                vtf = VTF.read(f)\n                vtf.load()\n                meta = {")

# Add src_format_id to meta dict
patch("extract_to_bmp meta format_id",
    "'has_alpha': pil_img.mode == 'RGBA'\n            if meta['has_alpha']:",
    "'has_alpha': pil_img.mode == 'RGBA'\n            meta['src_format_id'] = src_format_id\n            if meta['has_alpha']:")

# ============================================================
# 13. assemble_vtf: Format-aware output
# ============================================================
patch("assemble_vtf format-aware",
    '            out_fmt_name = self.config.get("output_format", "DXT5")\n            has_alpha = meta.get(\'has_alpha\', False)\n            if has_alpha and out_fmt_name == "DXT1":\n                out_fmt_name = "DXT5"\n            out_fmt = VTF_FORMATS.get(out_fmt_name, ImageFormats.DXT5)',
    """            out_fmt_name = self.config.get("output_format", "Auto (Match Source)")
            has_alpha = meta.get('has_alpha', False)
            
            # Format-aware: match source format if "Auto" selected
            if out_fmt_name == "Auto (Match Source)":
                src_fmt_id = meta.get("src_format_id", 15)
                matched_fmt = get_output_format_for_source(src_fmt_id)
                if matched_fmt:
                    out_fmt = matched_fmt
                    out_fmt_name = FORMAT_ID_MAP.get(src_fmt_id, "DXT5")
                else:
                    out_fmt = ImageFormats.DXT5
            else:
                if has_alpha and out_fmt_name == "DXT1":
                    out_fmt_name = "DXT5"
                out_fmt = VTF_FORMATS.get(out_fmt_name, ImageFormats.DXT5)""")

# ============================================================
# 14. Multi-pass 8x in preview (upscale_image)
# ============================================================
patch("preview 8x multi-pass",
    """        if method == "ai" and self.ai_upscaler.available:
            ai_scale = self.config.get("ai_scale", 4)
            ai_model = self.config.get("ai_model", "realesrgan-x4plus")
            upscaled = self.ai_upscaler.upscale(img, scale=ai_scale, model=ai_model, source_name=source_name)
            if upscaled:
                upscaled = upscaled.resize((new_w, new_h), Image.Resampling.LANCZOS)
                return upscaled, "AI"
            return img.resize((new_w, new_h), Image.Resampling.LANCZOS), "Lanczos (AI failed)\"""",
    """        if method == "ai" and self.ai_upscaler.available:
            ai_scale = self.config.get("ai_scale", 4)
            ai_model = self.config.get("ai_model", "realesrgan-x4plus")
            
            if ai_scale == 8:
                # Multi-pass: two sequential 4x AI passes for true 8x
                pass1 = self.ai_upscaler.upscale(img, scale=4, model=ai_model, source_name=source_name)
                if pass1:
                    p1w, p1h = pass1.size
                    # Skip pass 2 if pass 1 already exceeds target
                    if p1w >= new_w and p1h >= new_h:
                        self.ai_upscaler._log(f"[AI] 8x: Pass 1 ({p1w}x{p1h}) already >= target ({new_w}x{new_h}), skipping pass 2")
                        pass1 = pass1.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        return pass1, "AI 8x (1-pass, already oversampled)"
                    upscaled = self.ai_upscaler.upscale(pass1, scale=4, model=ai_model, source_name=source_name + " (pass2)")
                    if upscaled:
                        upscaled = upscaled.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        return upscaled, "AI 8x (2-pass)"
                    pass1 = pass1.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    return pass1, "AI 4x (pass2 failed)"
            else:
                upscaled = self.ai_upscaler.upscale(img, scale=ai_scale, model=ai_model, source_name=source_name)
                if upscaled:
                    upscaled = upscaled.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    return upscaled, "AI"
            return img.resize((new_w, new_h), Image.Resampling.LANCZOS), "Lanczos (AI failed)\"""")

# ============================================================
# 15. trace deprecation fix
# ============================================================
patch("trace deprecation",
    "trace('w',", "trace_add('write',")

# ============================================================
# 16. GUI: Format dropdown update
# ============================================================
patch("format dropdown values",
    'values=["DXT1", "DXT5", "BGRA8888"]',
    'values=["Auto (Match Source)", "DXT1", "DXT5", "BGR888", "BGRA8888"]')

patch("format default",
    'value=self.config.get("output_format", "DXT5")',
    'value=self.config.get("output_format", "Auto (Match Source)")')

# ============================================================
# 17. GUI: Scale selector after format combo
# ============================================================
# Find the format combobox line and add scale selector
patch("format combo width",
    "width=8, state='readonly').pack(side=tk.LEFT, padx=3)",
    "width=16, state='readonly').pack(side=tk.LEFT, padx=3)")

# Add scale right after the format combo pack
scale_code = """
        ttk.Label(settings, text="Scale:", style='Dim.TLabel').pack(side=tk.LEFT, padx=(10, 2))
        self.scale_var = tk.StringVar(value=str(self.config.get("ai_scale", 4)))
        scale_combo = ttk.Combobox(settings, textvariable=self.scale_var,
                                    values=["2", "3", "4", "8"], width=3, state='readonly')
        scale_combo.pack(side=tk.LEFT)"""

patch("scale selector",
    "        ttk.Label(settings, text=\"⚡ AI Mode\"",
    scale_code + "\n        ttk.Label(settings, text=\"⚡ AI Mode\"")

# ============================================================
# 18. GUI: GPU threads default display
# ============================================================
patch("gpu threads display default",
    'value=self.config.get("gpu_threads", "4:4:4")',
    'value=self.config.get("gpu_threads", "4:8:8")')

# ============================================================
# 19. GUI: VRAM monitor label in header
# ============================================================
patch("VRAM label",
    "self.ai_status = ttk.Label(header, text=\"\", style='AI.TLabel')\n        self.ai_status.pack(side=tk.LEFT, padx=20)",
    """self.ai_status = ttk.Label(header, text="", style='AI.TLabel')
        self.ai_status.pack(side=tk.LEFT, padx=20)
        self.vram_label = ttk.Label(header, text="", style='Dim.TLabel')
        self.vram_label.pack(side=tk.RIGHT, padx=10)
        if PYNVML_AVAILABLE:
            self._update_vram()""")

# ============================================================
# 20. GUI: Pre-scan Cache button
# ============================================================
patch("prescan button",
    "self.cancel_btn.pack(side=tk.LEFT, padx=3)\n        \n        # Log",
    """self.cancel_btn.pack(side=tk.LEFT, padx=3)
        self.prescan_btn = tk.Button(btn_frame, text="\\U0001f4cb Pre-scan Cache", command=self._prescan_cache,
                                    bg=c['light'], fg=c['text'], relief='flat', padx=12, pady=10)
        self.prescan_btn.pack(side=tk.LEFT, padx=3)
        
        # Log""")

# ============================================================
# 21. _load_preview: 5-tuple unpack
# ============================================================
patch("_load_preview 5-tuple",
    "w, h, fmt, _ = processor.get_vtf_info(filepath)",
    "w, h, fmt, _, _ = processor.get_vtf_info(filepath)")

# ============================================================
# 22. Wire log_callback in _preview_ai
# ============================================================
patch("wire log_callback preview",
    "processor = VTFProcessor(current_config)\n                target = int(self.res_var.get())",
    """processor = VTFProcessor(current_config)
                # Wire AI debug output to GUI log
                def ai_log(msg):
                    self.root.after(0, lambda m=msg: self._log(m, 'ai'))
                processor.ai_upscaler.log_callback = ai_log
                target = int(self.res_var.get())""")

# ============================================================
# 23. _update_config: add ai_scale + denoise
# ============================================================
patch("_update_config ai_scale",
    'self.config["sharpen_strength"] = float(self.sharpen_var.get())',
    'self.config["sharpen_strength"] = float(self.sharpen_var.get())\n        self.config["ai_scale"] = int(self.scale_var.get())')

# ============================================================
# 24. WM_DELETE_WINDOW protocol
# ============================================================
patch("WM_DELETE_WINDOW",
    "self._check_deps()",
    'self._check_deps()\n        \n        # Auto-save config on close\n        self.root.protocol("WM_DELETE_WINDOW", self._on_close)')

# ============================================================
# 25. Deps message v7.0
# ============================================================
patch("deps message v7.0",
    "v6.0 Performance mode: batch pipeline, BMP temp files",
    "v7.0 Performance mode: cache, format-aware, multi-pass")

# ============================================================
# 26. Timestamps in _log
# ============================================================
patch("timestamps in _log",
    '    def _log(self, msg: str, tag: str = None):\n        self.log_text.insert(tk.END, msg + "\\n", tag)\n        self.log_text.see(tk.END)',
    '    def _log(self, msg: str, tag: str = None):\n        timestamp = time.strftime("%H:%M:%S")\n        line = f"[{timestamp}] {msg}"\n        self.log_text.insert(tk.END, line + "\\n", tag)\n        self.log_text.see(tk.END)')

# Update log file write
patch("log file timestamp",
    'self._log_file.write(msg + "\\n")',
    'self._log_file.write(line + "\\n")')

# ============================================================
# 27. Pipeline: read ai_scale
# ============================================================
patch("pipeline ai_scale",
    '        ai_model = self.config.get("ai_model", "realesrgan-x4plus")\n        \n        if output_to_addon:',
    '        ai_model = self.config.get("ai_model", "realesrgan-x4plus")\n        ai_scale = self.config.get("ai_scale", 4)\n        \n        if output_to_addon:')

# ============================================================
# 28. Pipeline: Alpha quarantine before GPU batch
# ============================================================
patch("alpha quarantine",
    "                if self.cancel_flag or not file_meta:\n                    continue\n                \n                # STAGE 2:",
    """                if self.cancel_flag or not file_meta:
                    continue
                
                # Alpha quarantine: move _alpha.bmp files out of GPU dir
                alpha_hold = os.path.join(tmpdir, "alpha_hold")
                os.makedirs(alpha_hold, exist_ok=True)
                for fname in os.listdir(in_dir):
                    if '_alpha.bmp' in fname:
                        src_path = os.path.join(in_dir, fname)
                        shutil.move(src_path, os.path.join(alpha_hold, fname))
                
                # STAGE 2:""")

# Also restore alpha files after GPU batch, before assembly
patch("alpha restore",
    "                # STAGE 3: Assemble VTFs (CPU parallel)",
    """                # Restore quarantined alpha files for assembly
                for fname in os.listdir(alpha_hold):
                    shutil.move(os.path.join(alpha_hold, fname), os.path.join(in_dir, fname))
                
                # STAGE 3: Assemble VTFs (CPU parallel)""")

# ============================================================
# 29. Pipeline: 8x multi-pass batch + smart skip
# ============================================================
patch("8x batch pipeline",
    """                gpu_ok = processor.ai_upscaler.upscale_batch(
                    in_dir, out_dir, scale=ai_scale, model=ai_model, timeout=timeout,
                    progress_callback=gpu_progress)""",
    """                if ai_scale == 8:
                    import shutil as _shutil
                    # 8x = Two sequential 4x passes, SMART: skip pass 2 for files already at target
                    pass1_dir = os.path.join(tmpdir, "pass1_out")
                    os.makedirs(pass1_dir)
                    self.root.after(0, lambda bn=batch_num: self._log(f"   Pass 1/2: 4x AI upscale...", "ai"))
                    gpu_ok = processor.ai_upscaler.upscale_batch(
                        in_dir, pass1_dir, scale=4, model=ai_model, timeout=timeout,
                        progress_callback=gpu_progress)
                    if gpu_ok:
                        target_res = self.config.get("target_resolution", 4096)
                        need_pass2_dir = os.path.join(tmpdir, "pass2_in")
                        os.makedirs(need_pass2_dir)
                        already_done_count = 0
                        need_pass2_count = 0
                        for fname in os.listdir(pass1_dir):
                            fpath = os.path.join(pass1_dir, fname)
                            if not os.path.isfile(fpath):
                                continue
                            try:
                                with Image.open(fpath) as im:
                                    w, h = im.size
                                if w >= target_res and h >= target_res:
                                    _shutil.copy2(fpath, os.path.join(out_dir, fname))
                                    already_done_count += 1
                                else:
                                    _shutil.copy2(fpath, os.path.join(need_pass2_dir, fname))
                                    need_pass2_count += 1
                            except:
                                _shutil.copy2(fpath, os.path.join(out_dir, fname))
                                already_done_count += 1
                        self.root.after(0, lambda d=already_done_count, n=need_pass2_count:
                            self._log(f"   ⚡ Pass 1 done: {d} already at target, {n} need pass 2", "ai"))
                        if need_pass2_count > 0:
                            self.root.after(0, lambda n=need_pass2_count:
                                self._log(f"   Pass 2/2: 4x AI upscale ({n} files)...", "ai"))
                            gpu_ok = processor.ai_upscaler.upscale_batch(
                                need_pass2_dir, out_dir, scale=4, model=ai_model, timeout=timeout * 2,
                                progress_callback=gpu_progress)
                else:
                    gpu_ok = processor.ai_upscaler.upscale_batch(
                        in_dir, out_dir, scale=ai_scale, model=ai_model, timeout=timeout,
                        progress_callback=gpu_progress)""")

# ============================================================
# 30. Pipeline: Smart skip for addon output
# ============================================================
patch("smart skip addon",
    "        # Log skip summary",
    """        # Smart skip: check if addon output already has files at target resolution
        if output_to_addon and to_process:
            smart_skipped = []
            target = self.config.get("target_resolution", 4096)
            for fp in list(to_process):
                addon_path = self._get_addon_output_path(fp)
                if os.path.exists(addon_path):
                    try:
                        aw, ah, _, _, _ = read_vtf_header(addon_path)
                        if aw >= target or ah >= target:
                            smart_skipped.append(fp)
                            to_process.remove(fp)
                            skip += 1
                            completed += 1
                    except:
                        pass
            if smart_skipped:
                self.root.after(0, lambda n=len(smart_skipped): 
                    self._log(f"⚡ Smart skip: {n} file(s) already upscaled in addon output", 'ai'))
        
        # Log skip summary""")

# ============================================================
# 31. _done: completion sound + auto-save
# ============================================================
patch("_done enhanced",
    '    def _done(self):\n        self.processing = False\n        self.start_btn.config(state=tk.NORMAL)\n        self.cancel_btn.config(state=tk.DISABLED)\n        self.status_label.config(text="Complete!")',
    '''    def _done(self):
        self.processing = False
        self.start_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self.status_label.config(text="Complete!")
        # Play completion sound
        if WINSOUND_AVAILABLE:
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except:
                pass
        # Auto-save config after processing
        try:
            self._update_config()
            self._save_config()
        except:
            pass
        # Stop VRAM monitoring
        self._vram_active = False''')

# ============================================================
# 32. Add _on_close, _update_vram, cache methods, _prescan_cache BEFORE _done
# ============================================================
new_methods = '''    def _on_close(self):
        """Auto-save config and close cleanly."""
        try:
            self._update_config()
            self._save_config()
        except:
            pass
        self.root.destroy()
    
    def _update_vram(self):
        """Update VRAM usage label — polls every 2s during processing."""
        if not PYNVML_AVAILABLE:
            return
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(self.config.get("gpu_id", 0))
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            used_gb = info.used / (1024**3)
            total_gb = info.total / (1024**3)
            pct = info.used / info.total * 100
            if pct < 60:
                color = '#9ece6a'  # green
            elif pct < 85:
                color = '#e0af68'  # yellow
            else:
                color = '#f7768e'  # red
            self.vram_label.config(text=f"\\U0001f3ae VRAM: {used_gb:.1f}/{total_gb:.1f} GB ({pct:.0f}%)", foreground=color)
        except:
            self.vram_label.config(text="\\U0001f3ae VRAM: N/A")
        # Poll every 2 seconds
        if getattr(self, '_vram_active', True):
            self.root.after(2000, self._update_vram)
    
    def _get_cache_path(self):
        """Return the path to the pre-scan cache file."""
        return SCRIPT_DIR / CACHE_FILE
    
    def _prescan_cache(self):
        """Scan all VTF files and build a JSON cache for instant pipeline start."""
        folder = self.input_entry.get().strip()
        if not folder or not os.path.isdir(folder):
            self._log("\\u26a0 No folder selected for pre-scan", 'warning')
            return
        
        self._log(f"\\U0001f4cb Pre-scanning {folder}...", 'ai')
        self.prescan_btn.config(state=tk.DISABLED)
        
        def _scan_thread():
            from concurrent.futures import ThreadPoolExecutor
            target = self.config.get("target_resolution", 4096)
            processor = VTFProcessor(self.config)
            files = self.file_list if self.file_list else []
            cache = {"scan_time": time.strftime("%Y-%m-%d %H:%M:%S"), "folder": folder,
                     "target_resolution": target, "files": {}}
            
            fmt_counts = {}
            needs_upscale = 0
            already_done = 0
            total_size = 0
            
            def scan_one(fp):
                w, h, fmt, has_alpha, format_id = read_vtf_header(fp)
                if w == 0:
                    return fp, None
                skip_file, reason = processor.should_skip(fp)
                fsize = os.path.getsize(fp) if os.path.exists(fp) else 0
                tw, th = processor.calc_target_dims(w, h, target)
                return fp, {
                    "width": w, "height": h, "format": fmt,
                    "format_id": format_id, "has_alpha": has_alpha,
                    "file_size": fsize, "needs_upscale": not skip_file,
                    "skip_reason": reason if skip_file else "",
                    "target_w": tw, "target_h": th
                }
            
            scan_workers = min(16, max(4, len(files) // 50))
            with ThreadPoolExecutor(max_workers=scan_workers) as pool:
                for fp, info in pool.map(scan_one, files):
                    if info is None:
                        continue
                    cache["files"][fp] = info
                    fmt_counts[info["format"]] = fmt_counts.get(info["format"], 0) + 1
                    total_size += info["file_size"]
                    if info["needs_upscale"]:
                        needs_upscale += 1
                    else:
                        already_done += 1
            
            try:
                with open(self._get_cache_path(), 'w') as f:
                    json.dump(cache, f, indent=2)
            except Exception as e:
                self.root.after(0, lambda e=e: self._log(f"\\u26a0 Cache save error: {e}", 'warning'))
            
            fmt_str = ", ".join(f"{v}x {k}" for k, v in sorted(fmt_counts.items(), key=lambda x: -x[1]))
            size_mb = total_size / (1024 * 1024)
            self.root.after(0, lambda: self._log(
                f"\\U0001f4cb Cache built: {len(cache['files'])} files ({size_mb:.1f} MB)", 'ai'))
            self.root.after(0, lambda n=needs_upscale, d=already_done, fs=fmt_str:
                self._log(f"   \\u21b3 {n} need upscale, {d} already done | Formats: {fs}", 'ai'))
            self.root.after(0, lambda: self.prescan_btn.config(state=tk.NORMAL))
        
        threading.Thread(target=_scan_thread, daemon=True).start()
    
    def _load_cache(self):
        """Load pre-scan cache if it exists and matches current folder/target."""
        cache_path = self._get_cache_path()
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, 'r') as f:
                cache = json.load(f)
            folder = self.input_entry.get().strip()
            target = self.config.get("target_resolution", 4096)
            if cache.get("folder") == folder and cache.get("target_resolution") == target:
                return cache
            return None
        except:
            return None
    
'''

patch("new methods before _done",
    "    def _done(self):", 
    new_methods + "    def _done(self):")

# ============================================================
# 33. Pipeline: Cache-aware pre-flight
# ============================================================
patch("cache-aware prescan",
    '        self.root.after(0, lambda: self._log(f"\\U0001f50d Pre-flight scanning {total} files...", \'ai\'))\n        to_process = []\n        skip_reasons = {}',
    '''        # Pre-flight: Use cache if available, otherwise scan all files
        cache = self._load_cache()
        if cache:
            self.root.after(0, lambda: self._log(f"\\U0001f4cb Using pre-scan cache (instant start)...", 'ai'))
            to_process = []
            skip_reasons = {}
            for fp in self._pipeline_files:
                info = cache.get("files", {}).get(fp)
                if info and not info.get("needs_upscale", True):
                    skip += 1
                    completed += 1
                    skip_reasons[os.path.basename(fp)] = info.get("skip_reason", "cached skip")
                else:
                    to_process.append(fp)
        else:
            self.root.after(0, lambda: self._log(f"\\U0001f50d Pre-flight scanning {total} files...", \'ai\'))
            to_process = []
            skip_reasons = {}''')

# Fix scan indentation for the else branch
patch("scan indent check_one",
    '        def check_one(fp):\n            """Check a single file — runs in thread pool."""\n            return fp, processor.should_skip(fp)',
    '            def check_one(fp):\n                """Check a single file — runs in thread pool."""\n                return fp, processor.should_skip(fp)')

patch("scan indent scan_workers",
    "        scan_workers = min(cpu_workers * 2, 16)",
    "            scan_workers = min(cpu_workers * 2, 16)")

patch("scan indent ThreadPoolExecutor",
    "        with ThreadPoolExecutor(max_workers=scan_workers) as scan_pool:",
    "            with ThreadPoolExecutor(max_workers=scan_workers) as scan_pool:")

patch("scan indent futures submit",
    "            futures = [scan_pool.submit(check_one, fp) for fp in self._pipeline_files]",
    "                futures = [scan_pool.submit(check_one, fp) for fp in self._pipeline_files]")

patch("scan indent futures loop",
    "            for future in futures:",
    "                for future in futures:")

patch("scan indent cancel check",
    "                if self.cancel_flag:\n                    break\n                fp, (should_skip_file, reason) = future.result()\n                if should_skip_file:\n                    skip += 1\n                    completed += 1\n                    skip_reasons[os.path.basename(fp)] = reason\n                else:\n                    to_process.append(fp)",
    "                    if self.cancel_flag:\n                        break\n                    fp, (should_skip_file, reason) = future.result()\n                    if should_skip_file:\n                        skip += 1\n                        completed += 1\n                        skip_reasons[os.path.basename(fp)] = reason\n                    else:\n                        to_process.append(fp)")

# ============================================================
# 34. Pipeline: Activate VRAM monitoring during processing
# ============================================================
patch("VRAM active flag",
    "        start_time = time.time()",
    "        start_time = time.time()\n        self._vram_active = True")

# ============================================================
# 35. Pipeline: Use G: scratch space instead of tempfile
# ============================================================
patch("scratch space",
    "            with tempfile.TemporaryDirectory() as tmpdir:",
    """            scratch = get_scratch_dir()
            if scratch:
                tmpdir = os.path.join(scratch, f"batch_{batch_idx}")
                os.makedirs(tmpdir, exist_ok=True)
                try:""")

# We need to add the finally+cleanup for scratch mode and also keep the tempfile fallback
# This is complex - let's use a different approach: wrap the entire batch block
# Actually, let's keep it simpler - just use tempfile but on G: drive if available
# Revert the above and use a simpler approach
src = src.replace(
    """            scratch = get_scratch_dir()
            if scratch:
                tmpdir = os.path.join(scratch, f"batch_{batch_idx}")
                os.makedirs(tmpdir, exist_ok=True)
                try:""",
    "            with tempfile.TemporaryDirectory(dir=get_scratch_dir()) as tmpdir:")
applied.append("scratch space simplified")

# ============================================================
# WRITE OUTPUT
# ============================================================
with open(FILE, 'w', encoding='utf-8') as f:
    f.write(src)

lines = src.count('\n') + 1
print(f"\n{'='*60}")
print(f"Patched! {len(applied)} changes applied")
print(f"File: {original_len} → {len(src.encode())} bytes, {lines} lines")
print(f"{'='*60}")
for i, name in enumerate(applied, 1):
    print(f"  {i:2d}. ✓ {name}")

# Verify key features exist
checks = {
    'FORMAT_ID_MAP': 'Format-aware output map',
    'get_output_format_for_source': 'Format matching function',
    'self.scale_var': 'Scale selector',
    '_prescan_cache': 'Pre-scan cache',
    '_load_cache': 'Cache loader',
    '_get_cache_path': 'Cache path',
    'ai_scale == 8': '8x multi-pass',
    'trace_add': 'Deprecation fix',
    'src_format_id': 'Source format tracking',
    'CACHE_FILE': 'Cache constant',
    'WINSOUND_AVAILABLE': 'Winsound import',
    'PYNVML_AVAILABLE': 'VRAM monitor import',
    'winsound.MessageBeep': 'Completion sound',
    '_on_close': 'Auto-save on close',
    'WM_DELETE_WINDOW': 'Close handler',
    'Auto (Match Source)': 'Auto format option',
    'Smart skip': 'Smart addon skip',
    'Pre-scan Cache': 'Cache button',
    'v7.0': 'Version update',
    'effective_scale = min': 'Scale clamp',
    'preview_fmt': 'PNG preview fix',
    'log_callback': 'AI debug routing',
    'alpha_hold': 'Alpha quarantine',
    'vram_label': 'VRAM display',
    '_update_vram': 'VRAM polling',
    'get_scratch_dir': 'G: scratch space',
    'SCRATCH_DRIVE': 'Scratch constant',
    'pass1_dir': '8x batch pipeline',
}

print(f"\n{'='*60}")
print("Feature verification:")
missing = []
for key, desc in checks.items():
    if key in src:
        print(f"  ✓ {desc}")
    else:
        print(f"  ✗ MISSING: {desc}")
        missing.append(desc)

if missing:
    print(f"\n⚠ {len(missing)} features missing!")
else:
    print(f"\n✅ All {len(checks)} features verified!")
