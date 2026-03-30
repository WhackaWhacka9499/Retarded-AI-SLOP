"""
NovaCompress — Maximum Compression Tool
Wraps 7-Zip with LZMA2 ultra settings for maximum compression.
Compatible output with WinRAR, 7-Zip, and standard archivers.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import threading
import os
import re
import time
import json
from pathlib import Path
from datetime import timedelta

# ─── Configuration ────────────────────────────────────────────────────────────

SEVEN_ZIP_PATH = r"C:\Program Files\7-Zip\7z.exe"

PRESETS = {
    "Maximum": {
        "desc": "Smallest file size  •  Slowest",
        "args": ["-mx=9", "-md=1g", "-mfb=273", "-ms=on"],
        "icon": "🔥",
    },
    "Balanced": {
        "desc": "Good ratio  •  ~2× faster",
        "args": ["-mx=7", "-md=256m", "-mfb=128", "-ms=on"],
        "icon": "⚖️",
    },
    "Fast": {
        "desc": "Quick compression  •  Decent ratio",
        "args": ["-mx=5", "-md=64m", "-mfb=64", "-ms=on"],
        "icon": "⚡",
    },
}

# Volume split options (value for -v switch)
SPLIT_OPTIONS = {
    "No Split": None,
    "700 MB (CD)": "700m",
    "4 GB (FAT32 / DVD)": "4g",
    "8 GB": "8g",
    "25 GB (Blu-ray)": "25g",
    "50 GB": "50g",
    "Custom...": "custom",
}

# ─── Colors ───────────────────────────────────────────────────────────────────

COLORS = {
    "bg": "#0d1117",
    "bg_secondary": "#161b22",
    "bg_tertiary": "#1c2333",
    "surface": "#21262d",
    "border": "#30363d",
    "text": "#e6edf3",
    "text_dim": "#8b949e",
    "accent": "#58a6ff",
    "accent_hover": "#79c0ff",
    "green": "#3fb950",
    "green_dim": "#238636",
    "orange": "#d29922",
    "red": "#f85149",
    "purple": "#bc8cff",
    "cyan": "#39d2c0",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def format_size(size_bytes):
    """Format bytes to human-readable string."""
    if size_bytes < 0:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def format_time(seconds):
    """Format seconds to human-readable time string."""
    if seconds < 0 or seconds > 999999:
        return "calculating..."
    td = timedelta(seconds=int(seconds))
    parts = []
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def scan_directory_fast(path):
    """Quick scan to count files and total size."""
    total_size = 0
    file_count = 0
    ext_stats = {}
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                file_count += 1
                try:
                    sz = entry.stat(follow_symlinks=False).st_size
                    total_size += sz
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in ext_stats:
                        ext_stats[ext][0] += 1
                        ext_stats[ext][1] += sz
                    else:
                        ext_stats[ext] = [1, sz]
                except OSError:
                    pass
            elif entry.is_dir(follow_symlinks=False):
                sub_count, sub_size, sub_ext = scan_directory_fast(entry.path)
                file_count += sub_count
                total_size += sub_size
                for e, (c, s) in sub_ext.items():
                    if e in ext_stats:
                        ext_stats[e][0] += c
                        ext_stats[e][1] += s
                    else:
                        ext_stats[e] = [c, s]
    except PermissionError:
        pass
    return file_count, total_size, ext_stats


# ─── Main Application ────────────────────────────────────────────────────────

class NovaCompress:
    def __init__(self, root):
        self.root = root
        self.root.title("NovaCompress")
        self.root.geometry("900x720")
        self.root.minsize(800, 650)
        self.root.configure(bg=COLORS["bg"])

        # State
        self.process = None
        self.is_compressing = False
        self.cancel_flag = False
        self.start_time = None
        self.selected_preset = tk.StringVar(value="Maximum")
        self.input_path = tk.StringVar(value=r"G:\MegaMind")
        self.output_path = tk.StringVar()
        self.password_var = tk.StringVar()
        self.split_var = tk.StringVar(value="No Split")
        self.custom_split_var = tk.StringVar(value="4g")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.scanned_size = 0
        self.scanned_count = 0

        # Set default output path
        self._update_default_output()

        # Style
        self._setup_styles()

        # Build UI
        self._build_ui()

        # Icon (set title bar)
        self.root.after(100, self._center_window)

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Progress bar
        style.configure(
            "Nova.Horizontal.TProgressbar",
            troughcolor=COLORS["bg_tertiary"],
            background=COLORS["accent"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["accent"],
            darkcolor=COLORS["accent"],
            thickness=22,
        )

        # Custom entry style
        style.configure(
            "Nova.TEntry",
            fieldbackground=COLORS["bg_tertiary"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            insertcolor=COLORS["text"],
        )

    def _update_default_output(self, *_):
        inp = self.input_path.get()
        if inp:
            parent = str(Path(inp).parent)
            name = Path(inp).name
            self.output_path.set(os.path.join(parent, f"{name}_compressed.7z"))

    def _build_ui(self):
        # Main container with padding
        main = tk.Frame(self.root, bg=COLORS["bg"])
        main.pack(fill="both", expand=True, padx=20, pady=15)

        # ── Header ──
        header_frame = tk.Frame(main, bg=COLORS["bg"])
        header_frame.pack(fill="x", pady=(0, 15))

        title_label = tk.Label(
            header_frame,
            text="🗜️  NovaCompress",
            font=("Segoe UI", 22, "bold"),
            fg=COLORS["accent"],
            bg=COLORS["bg"],
        )
        title_label.pack(side="left")

        subtitle = tk.Label(
            header_frame,
            text="Maximum Compression  •  7-Zip LZMA2",
            font=("Segoe UI", 10),
            fg=COLORS["text_dim"],
            bg=COLORS["bg"],
        )
        subtitle.pack(side="left", padx=(12, 0), pady=(8, 0))

        # ── Input / Output Section ──
        io_frame = tk.Frame(main, bg=COLORS["surface"], highlightbackground=COLORS["border"], highlightthickness=1)
        io_frame.pack(fill="x", pady=(0, 10))

        io_inner = tk.Frame(io_frame, bg=COLORS["surface"])
        io_inner.pack(fill="x", padx=15, pady=12)

        # Input path
        self._make_path_row(io_inner, "Source", self.input_path, self._browse_input, row=0)
        # Output path
        self._make_path_row(io_inner, "Output", self.output_path, self._browse_output, row=1)

        # ── Settings Row ──
        settings_frame = tk.Frame(main, bg=COLORS["bg"])
        settings_frame.pack(fill="x", pady=(0, 10))

        # Preset selector
        preset_frame = tk.Frame(settings_frame, bg=COLORS["surface"], highlightbackground=COLORS["border"], highlightthickness=1)
        preset_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        preset_inner = tk.Frame(preset_frame, bg=COLORS["surface"])
        preset_inner.pack(fill="both", padx=12, pady=10)

        tk.Label(preset_inner, text="COMPRESSION PRESET", font=("Segoe UI", 8, "bold"),
                 fg=COLORS["text_dim"], bg=COLORS["surface"]).pack(anchor="w")

        for name, cfg in PRESETS.items():
            f = tk.Frame(preset_inner, bg=COLORS["surface"], cursor="hand2")
            f.pack(fill="x", pady=2)

            rb = tk.Radiobutton(
                f,
                text=f"  {cfg['icon']}  {name}",
                variable=self.selected_preset,
                value=name,
                font=("Segoe UI", 11),
                fg=COLORS["text"],
                bg=COLORS["surface"],
                selectcolor=COLORS["bg_tertiary"],
                activebackground=COLORS["surface"],
                activeforeground=COLORS["accent"],
                indicatoron=True,
                relief="flat",
                highlightthickness=0,
                bd=0,
            )
            rb.pack(side="left")

            desc_lbl = tk.Label(
                f,
                text=cfg["desc"],
                font=("Segoe UI", 9),
                fg=COLORS["text_dim"],
                bg=COLORS["surface"],
            )
            desc_lbl.pack(side="left", padx=(5, 0))

        # Options panel
        opt_frame = tk.Frame(settings_frame, bg=COLORS["surface"], highlightbackground=COLORS["border"], highlightthickness=1)
        opt_frame.pack(side="left", fill="both", expand=True, padx=(5, 0))

        opt_inner = tk.Frame(opt_frame, bg=COLORS["surface"])
        opt_inner.pack(fill="both", padx=12, pady=10)

        tk.Label(opt_inner, text="OPTIONS", font=("Segoe UI", 8, "bold"),
                 fg=COLORS["text_dim"], bg=COLORS["surface"]).pack(anchor="w", pady=(0, 5))

        # Password
        pw_row = tk.Frame(opt_inner, bg=COLORS["surface"])
        pw_row.pack(fill="x", pady=2)
        tk.Label(pw_row, text="Password:", font=("Segoe UI", 10),
                 fg=COLORS["text"], bg=COLORS["surface"], width=10, anchor="w").pack(side="left")
        pw_entry = tk.Entry(
            pw_row, textvariable=self.password_var, show="●",
            font=("Segoe UI", 10), bg=COLORS["bg_tertiary"],
            fg=COLORS["text"], insertbackground=COLORS["text"],
            relief="flat", highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"],
        )
        pw_entry.pack(side="left", fill="x", expand=True, ipady=3)

        # Volume split
        split_row = tk.Frame(opt_inner, bg=COLORS["surface"])
        split_row.pack(fill="x", pady=(8, 2))
        tk.Label(split_row, text="Split Into:", font=("Segoe UI", 10),
                 fg=COLORS["text"], bg=COLORS["surface"], width=10, anchor="w").pack(side="left")

        split_menu = tk.OptionMenu(split_row, self.split_var, *SPLIT_OPTIONS.keys())
        split_menu.configure(
            font=("Segoe UI", 10), bg=COLORS["bg_tertiary"],
            fg=COLORS["text"], activebackground=COLORS["surface"],
            activeforeground=COLORS["accent"], highlightthickness=0,
            relief="flat", bd=0,
        )
        split_menu["menu"].configure(
            bg=COLORS["bg_tertiary"], fg=COLORS["text"],
            activebackground=COLORS["accent"], activeforeground=COLORS["bg"],
            font=("Segoe UI", 10),
        )
        split_menu.pack(side="left", fill="x", expand=True)

        # Custom split entry (hidden by default)
        self.custom_split_frame = tk.Frame(opt_inner, bg=COLORS["surface"])
        tk.Label(self.custom_split_frame, text="Size:", font=("Segoe UI", 10),
                 fg=COLORS["text"], bg=COLORS["surface"], width=10, anchor="w").pack(side="left")
        custom_entry = tk.Entry(
            self.custom_split_frame, textvariable=self.custom_split_var,
            font=("Segoe UI", 10), bg=COLORS["bg_tertiary"],
            fg=COLORS["text"], insertbackground=COLORS["text"],
            relief="flat", highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"], width=12,
        )
        custom_entry.pack(side="left", ipady=3)
        tk.Label(self.custom_split_frame, text="(e.g. 4g, 700m, 100m)",
                 font=("Segoe UI", 9), fg=COLORS["text_dim"], bg=COLORS["surface"]).pack(side="left", padx=5)

        self.split_var.trace_add("write", self._on_split_change)

        # ── Progress Section ──
        prog_frame = tk.Frame(main, bg=COLORS["surface"], highlightbackground=COLORS["border"], highlightthickness=1)
        prog_frame.pack(fill="x", pady=(0, 10))

        prog_inner = tk.Frame(prog_frame, bg=COLORS["surface"])
        prog_inner.pack(fill="x", padx=15, pady=12)

        # Stats row
        stats_row = tk.Frame(prog_inner, bg=COLORS["surface"])
        stats_row.pack(fill="x", pady=(0, 8))

        self.status_label = tk.Label(
            stats_row, text="Ready", font=("Segoe UI", 11, "bold"),
            fg=COLORS["green"], bg=COLORS["surface"], anchor="w",
        )
        self.status_label.pack(side="left")

        self.percent_label = tk.Label(
            stats_row, text="0%", font=("Segoe UI", 14, "bold"),
            fg=COLORS["accent"], bg=COLORS["surface"],
        )
        self.percent_label.pack(side="right")

        # Progress bar
        self.progress_bar = ttk.Progressbar(
            prog_inner, variable=self.progress_var,
            maximum=100, style="Nova.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill="x", pady=(0, 8))

        # Detail stats
        detail_row = tk.Frame(prog_inner, bg=COLORS["surface"])
        detail_row.pack(fill="x")

        self.elapsed_label = self._make_stat(detail_row, "Elapsed", "—", 0)
        self.eta_label = self._make_stat(detail_row, "ETA", "—", 1)
        self.ratio_label = self._make_stat(detail_row, "Ratio", "—", 2)
        self.size_label = self._make_stat(detail_row, "Source", "—", 3)

        detail_row.columnconfigure(0, weight=1)
        detail_row.columnconfigure(1, weight=1)
        detail_row.columnconfigure(2, weight=1)
        detail_row.columnconfigure(3, weight=1)

        # ── Log Panel ──
        log_frame = tk.Frame(main, bg=COLORS["surface"], highlightbackground=COLORS["border"], highlightthickness=1)
        log_frame.pack(fill="both", expand=True, pady=(0, 10))

        log_header = tk.Frame(log_frame, bg=COLORS["surface"])
        log_header.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(log_header, text="LOG", font=("Segoe UI", 8, "bold"),
                 fg=COLORS["text_dim"], bg=COLORS["surface"]).pack(side="left")

        self.log_text = tk.Text(
            log_frame, font=("Cascadia Code", 9), bg=COLORS["bg_tertiary"],
            fg=COLORS["text_dim"], relief="flat", wrap="word",
            highlightthickness=0, padx=10, pady=8, height=8,
            insertbackground=COLORS["text"],
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        log_scroll = tk.Scrollbar(self.log_text, command=self.log_text.yview, bg=COLORS["surface"])
        self.log_text.configure(yscrollcommand=log_scroll.set)

        # ── Button Row ──
        btn_row = tk.Frame(main, bg=COLORS["bg"])
        btn_row.pack(fill="x")

        self.scan_btn = tk.Button(
            btn_row, text="📊  Scan", font=("Segoe UI", 11, "bold"),
            fg=COLORS["text"], bg=COLORS["surface"],
            activebackground=COLORS["bg_tertiary"], activeforeground=COLORS["text"],
            relief="flat", bd=0, padx=20, pady=8, cursor="hand2",
            command=self._start_scan,
        )
        self.scan_btn.pack(side="left", padx=(0, 8))

        self.cancel_btn = tk.Button(
            btn_row, text="✖  Cancel", font=("Segoe UI", 11, "bold"),
            fg=COLORS["text"], bg=COLORS["red"],
            activebackground="#da3633", activeforeground=COLORS["text"],
            relief="flat", bd=0, padx=20, pady=8, cursor="hand2",
            command=self._cancel_compression, state="disabled",
        )
        self.cancel_btn.pack(side="right")

        self.start_btn = tk.Button(
            btn_row, text="🚀  Compress", font=("Segoe UI", 12, "bold"),
            fg=COLORS["bg"], bg=COLORS["green"],
            activebackground=COLORS["green_dim"], activeforeground=COLORS["text"],
            relief="flat", bd=0, padx=30, pady=8, cursor="hand2",
            command=self._start_compression,
        )
        self.start_btn.pack(side="right", padx=(0, 8))

    def _make_path_row(self, parent, label, var, browse_cmd, row):
        f = tk.Frame(parent, bg=COLORS["surface"])
        f.pack(fill="x", pady=3)

        tk.Label(f, text=f"{label}:", font=("Segoe UI", 10, "bold"),
                 fg=COLORS["text_dim"], bg=COLORS["surface"], width=7, anchor="w").pack(side="left")

        entry = tk.Entry(
            f, textvariable=var, font=("Segoe UI", 10),
            bg=COLORS["bg_tertiary"], fg=COLORS["text"],
            insertbackground=COLORS["text"], relief="flat",
            highlightthickness=1, highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 8))

        if label == "Source":
            var.trace_add("write", self._update_default_output)

        btn = tk.Button(
            f, text="Browse", font=("Segoe UI", 9),
            fg=COLORS["text"], bg=COLORS["bg_tertiary"],
            activebackground=COLORS["surface"], activeforeground=COLORS["accent"],
            relief="flat", bd=0, padx=12, pady=2, cursor="hand2",
            command=browse_cmd,
        )
        btn.pack(side="left")

    def _make_stat(self, parent, label, initial, col):
        f = tk.Frame(parent, bg=COLORS["surface"])
        f.grid(row=0, column=col, sticky="ew")

        tk.Label(f, text=label.upper(), font=("Segoe UI", 7, "bold"),
                 fg=COLORS["text_dim"], bg=COLORS["surface"]).pack(anchor="w")
        val = tk.Label(f, text=initial, font=("Segoe UI", 11, "bold"),
                       fg=COLORS["text"], bg=COLORS["surface"], anchor="w")
        val.pack(anchor="w")
        return val

    def _on_split_change(self, *_):
        if self.split_var.get() == "Custom...":
            self.custom_split_frame.pack(fill="x", pady=(4, 0))
        else:
            self.custom_split_frame.pack_forget()

    def _browse_input(self):
        path = filedialog.askdirectory(title="Select folder to compress", initialdir=self.input_path.get() or "G:\\")
        if path:
            self.input_path.set(path)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save compressed archive as",
            defaultextension=".7z",
            filetypes=[("7z Archive", "*.7z"), ("All Files", "*.*")],
            initialdir=os.path.dirname(self.output_path.get()) or "G:\\",
        )
        if path:
            self.output_path.set(path)

    def _log(self, msg, color=None):
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")

    def _set_status(self, text, color=None):
        self.status_label.config(text=text, fg=color or COLORS["text"])

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _start_scan(self):
        inp = self.input_path.get()
        if not inp or not os.path.isdir(inp):
            messagebox.showerror("Error", "Please select a valid source folder.")
            return

        self.scan_btn.config(state="disabled", text="Scanning...")
        self._set_status("Scanning...", COLORS["orange"])
        self._log(f"Scanning {inp}...")

        def do_scan():
            count, size, ext_stats = scan_directory_fast(inp)
            self.scanned_count = count
            self.scanned_size = size

            self.root.after(0, lambda: self._finish_scan(count, size, ext_stats))

        threading.Thread(target=do_scan, daemon=True).start()

    def _finish_scan(self, count, size, ext_stats):
        self.scan_btn.config(state="normal", text="📊  Scan")
        self._set_status("Ready", COLORS["green"])
        self.size_label.config(text=format_size(size))

        self._log(f"─── Scan Complete ───")
        self._log(f"  Files:  {count:,}")
        self._log(f"  Size:   {format_size(size)}")
        self._log("")

        # Top extensions by size
        sorted_ext = sorted(ext_stats.items(), key=lambda x: x[1][1], reverse=True)[:10]
        self._log("  Top file types by size:")
        for ext, (c, s) in sorted_ext:
            ext_name = ext if ext else "(no ext)"
            self._log(f"    {ext_name:<10} {c:>8,} files   {format_size(s):>10}")
        self._log("")

    # ── Compression ───────────────────────────────────────────────────────────

    def _start_compression(self):
        inp = self.input_path.get()
        out = self.output_path.get()

        if not inp or not os.path.isdir(inp):
            messagebox.showerror("Error", "Please select a valid source folder.")
            return
        if not out:
            messagebox.showerror("Error", "Please specify an output file path.")
            return
        if os.path.exists(out):
            if not messagebox.askyesno("Overwrite?", f"Output file already exists:\n{out}\n\nOverwrite?"):
                return

        # Verify 7-Zip exists
        if not os.path.isfile(SEVEN_ZIP_PATH):
            messagebox.showerror("Error", f"7-Zip not found at:\n{SEVEN_ZIP_PATH}\n\nPlease install 7-Zip.")
            return

        self.is_compressing = True
        self.cancel_flag = False
        self.start_time = time.time()

        self.start_btn.config(state="disabled")
        self.scan_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.progress_var.set(0)
        self.percent_label.config(text="0%")
        self._set_status("Compressing...", COLORS["accent"])

        # Build command
        preset = PRESETS[self.selected_preset.get()]
        cmd = [
            SEVEN_ZIP_PATH, "a",
            "-t7z",
            "-m0=lzma2",
            "-mmt=on",
            "-bsp1",       # progress to stdout
            "-bb1",        # log level
        ]
        cmd.extend(preset["args"])

        # Password
        pw = self.password_var.get().strip()
        if pw:
            cmd.append(f"-p{pw}")
            cmd.append("-mhe=on")  # encrypt headers too

        # Volume split
        split_key = self.split_var.get()
        split_val = SPLIT_OPTIONS.get(split_key)
        if split_val == "custom":
            split_val = self.custom_split_var.get().strip()
        if split_val:
            cmd.append(f"-v{split_val}")

        # Remove existing output if overwrite confirmed
        if os.path.exists(out):
            try:
                os.remove(out)
            except OSError:
                pass

        cmd.append(out)    # output archive
        cmd.append(os.path.join(inp, "*"))  # input files

        self._log(f"─── Compression Started ───")
        self._log(f"  Preset:  {self.selected_preset.get()}")
        self._log(f"  Source:  {inp}")
        self._log(f"  Output:  {out}")
        if pw:
            self._log(f"  Password: ****")
        if split_val:
            self._log(f"  Split:   {split_val}")
        self._log(f"  Command: {' '.join(cmd[:6])}...")
        self._log("")

        # Launch in thread
        threading.Thread(target=self._run_compression, args=(cmd, out), daemon=True).start()
        self._update_timer()

    def _run_compression(self, cmd, output_path):
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )

            percent_re = re.compile(r'(\d{1,3})%')
            last_pct = -1

            for line in self.process.stdout:
                if self.cancel_flag:
                    self.process.terminate()
                    self.root.after(0, lambda: self._compression_cancelled())
                    return

                line = line.strip()
                if not line:
                    continue

                # Parse progress percentage
                match = percent_re.search(line)
                if match:
                    pct = int(match.group(1))
                    if pct != last_pct:
                        last_pct = pct
                        self.root.after(0, lambda p=pct: self._update_progress(p))
                elif line and not line.startswith('+'):
                    # Log non-progress lines (but throttle)
                    if len(line) < 200:
                        self.root.after(0, lambda l=line: self._log(f"  {l}"))

            self.process.wait()
            rc = self.process.returncode

            if rc == 0 and not self.cancel_flag:
                self.root.after(0, lambda: self._compression_complete(output_path))
            elif not self.cancel_flag:
                self.root.after(0, lambda: self._compression_error(rc))

        except Exception as e:
            self.root.after(0, lambda: self._compression_error(str(e)))

    def _update_progress(self, pct):
        self.progress_var.set(pct)
        self.percent_label.config(text=f"{pct}%")

        # Update ETA
        elapsed = time.time() - self.start_time
        if pct > 0:
            eta = elapsed / pct * (100 - pct)
            self.eta_label.config(text=format_time(eta))

    def _update_timer(self):
        if not self.is_compressing:
            return
        elapsed = time.time() - self.start_time
        self.elapsed_label.config(text=format_time(elapsed))
        self.root.after(1000, self._update_timer)

    def _compression_complete(self, output_path):
        self.is_compressing = False
        elapsed = time.time() - self.start_time

        self.start_btn.config(state="normal")
        self.scan_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.progress_var.set(100)
        self.percent_label.config(text="100%")
        self._set_status("Complete!", COLORS["green"])
        self.eta_label.config(text="0s")

        # Get output size
        out_size = 0
        try:
            # Could be split volumes, sum them all
            base = output_path
            if os.path.exists(base):
                out_size = os.path.getsize(base)
            else:
                # Check for split volumes (.7z.001, .7z.002, etc.)
                vol_dir = os.path.dirname(base)
                vol_base = os.path.basename(base)
                for f in os.listdir(vol_dir):
                    if f.startswith(vol_base):
                        out_size += os.path.getsize(os.path.join(vol_dir, f))
        except Exception:
            pass

        self._log("")
        self._log(f"─── Compression Complete ───")
        self._log(f"  Time:      {format_time(elapsed)}")
        if out_size > 0:
            self._log(f"  Output:    {format_size(out_size)}")
            if self.scanned_size > 0:
                ratio = (1 - out_size / self.scanned_size) * 100
                self.ratio_label.config(text=f"{ratio:.1f}%")
                self._log(f"  Saved:     {ratio:.1f}%")
                self._log(f"  Ratio:     {format_size(self.scanned_size)} → {format_size(out_size)}")
        self._log(f"  Path:      {output_path}")
        self._log("")

        messagebox.showinfo("Done!", f"Compression complete!\n\nTime: {format_time(elapsed)}\nOutput: {format_size(out_size)}")

    def _compression_cancelled(self):
        self.is_compressing = False
        self.start_btn.config(state="normal")
        self.scan_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self._set_status("Cancelled", COLORS["orange"])
        self._log("─── Compression Cancelled ───")
        self._log("")

    def _compression_error(self, error):
        self.is_compressing = False
        self.start_btn.config(state="normal")
        self.scan_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self._set_status(f"Error", COLORS["red"])
        self._log(f"─── Error ───")
        self._log(f"  {error}")
        self._log("")
        messagebox.showerror("Compression Error", f"7-Zip returned an error:\n\n{error}")

    def _cancel_compression(self):
        if self.is_compressing:
            self.cancel_flag = True
            self._set_status("Cancelling...", COLORS["orange"])
            self._log("Cancellation requested...")


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = NovaCompress(root)
    root.mainloop()
