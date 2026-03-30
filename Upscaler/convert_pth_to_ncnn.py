#!/usr/bin/env python3
"""
Convert PyTorch .pth/.safetensors ESRGAN models to ncnn format (.bin + .param).
Supports: RRDBNet (old & new), SRVGGNetCompact, and partial DAT/SPAN.

Pipeline: .pth → detect arch → build model → trace → TorchScript → pnnx → ncnn

Usage: python convert_pth_to_ncnn.py
"""
import os, sys, re, struct, subprocess, shutil, math
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

# ─── Paths ─────────────────────────────────────────────────
PTH_DIR  = Path(r"c:\Users\Alexander Jarvis\Desktop\Upscaler\custom_models\pth_models")
NCNN_DIR = Path(r"c:\Users\Alexander Jarvis\Desktop\Upscaler\custom_models")


# ═══════════════════════════════════════════════════════════
# Model Architectures (self-contained, no external deps)
# ═══════════════════════════════════════════════════════════

# ─── RRDBNet (ESRGAN / Real-ESRGAN) ────────────────────────
class ResidualDenseBlock(nn.Module):
    def __init__(self, nf=64, gc=32):
        super().__init__()
        self.conv1 = nn.Conv2d(nf,       gc, 3, 1, 1)
        self.conv2 = nn.Conv2d(nf + gc,  gc, 3, 1, 1)
        self.conv3 = nn.Conv2d(nf+2*gc,  gc, 3, 1, 1)
        self.conv4 = nn.Conv2d(nf+3*gc,  gc, 3, 1, 1)
        self.conv5 = nn.Conv2d(nf+4*gc,  nf, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class RRDB(nn.Module):
    def __init__(self, nf=64, gc=32):
        super().__init__()
        self.RDB1 = ResidualDenseBlock(nf, gc)
        self.RDB2 = ResidualDenseBlock(nf, gc)
        self.RDB3 = ResidualDenseBlock(nf, gc)

    def forward(self, x):
        out = self.RDB1(x)
        out = self.RDB2(out)
        out = self.RDB3(out)
        return out * 0.2 + x


class RRDBNet(nn.Module):
    """RRDBNet - supports both old ESRGAN and new Real-ESRGAN key formats."""
    def __init__(self, in_nc=3, out_nc=3, nf=64, nb=23, gc=32, scale=4):
        super().__init__()
        self.scale = scale
        n_up = int(math.log2(scale)) if scale > 1 else 0

        self.conv_first = nn.Conv2d(in_nc, nf, 3, 1, 1)
        self.body = nn.Sequential(*[RRDB(nf, gc) for _ in range(nb)])
        self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)

        # Upsampling
        self.upconvs = nn.ModuleList()
        for _ in range(n_up):
            self.upconvs.append(nn.Conv2d(nf, nf, 3, 1, 1))

        if scale == 1:
            self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
        else:
            self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_last = nn.Conv2d(nf, out_nc, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        feat = self.conv_first(x)
        trunk = self.conv_body(self.body(feat))
        feat = feat + trunk

        for upconv in self.upconvs:
            feat = self.lrelu(upconv(F.interpolate(feat, scale_factor=2, mode='nearest')))

        out = self.conv_last(self.lrelu(self.conv_hr(feat)))
        return out


# ─── SRVGGNetCompact (Real-ESRGAN v2 compact) ─────────────
class SRVGGNetCompact(nn.Module):
    def __init__(self, in_nc=3, out_nc=3, nf=64, nb=16, scale=4, act_type='prelu'):
        super().__init__()
        self.scale = scale
        body = [nn.Conv2d(in_nc, nf, 3, 1, 1)]
        if act_type == 'prelu':
            body.append(nn.PReLU(nf))
        else:
            body.append(nn.LeakyReLU(0.1, True))
        for _ in range(nb):
            body.append(nn.Conv2d(nf, nf, 3, 1, 1))
            if act_type == 'prelu':
                body.append(nn.PReLU(nf))
            else:
                body.append(nn.LeakyReLU(0.1, True))
        body.append(nn.Conv2d(nf, out_nc * scale * scale, 3, 1, 1))
        self.body = nn.Sequential(*body)
        self.ps = nn.PixelShuffle(scale)

    def forward(self, x):
        out = self.body(x)
        out = self.ps(out)
        base = F.interpolate(x, scale_factor=self.scale, mode='bilinear', align_corners=False)
        return out + base


# ═══════════════════════════════════════════════════════════
# State Dict Analysis & Architecture Detection 
# ═══════════════════════════════════════════════════════════

def load_state_dict(filepath):
    """Load state dict from .pth or .safetensors file."""
    fp = str(filepath)
    if fp.endswith('.safetensors'):
        from safetensors.torch import load_file
        return load_file(fp)
    
    sd = torch.load(fp, map_location='cpu', weights_only=True)
    if isinstance(sd, dict):
        if 'params_ema' in sd: return sd['params_ema']
        if 'params' in sd: return sd['params']
        if 'model' in sd: return sd['model']
    return sd


def detect_config(sd):
    """Detect model architecture and hyperparameters from state dict keys."""
    keys = list(sd.keys())
    
    # SRVGGNetCompact: body.0.weight, body.1.weight, ...
    if any(k.startswith('body.') for k in keys) and not any('rdb' in k.lower() for k in keys) and not any('RDB' in k for k in keys):
        in_nc = sd['body.0.weight'].shape[1]
        nf = sd['body.0.weight'].shape[0]
        # Count conv layers (every other layer is PReLU/act)
        conv_keys = [k for k in keys if re.match(r'body\.\d+\.weight', k) and sd[k].dim() == 4]
        nb = len(conv_keys) - 2  # minus first and last conv
        last_conv = [k for k in conv_keys if sd[k].dim() == 4][-1]
        out_channels = sd[last_conv].shape[0]
        # Determine scale from last conv output channels
        # out_nc * scale^2 = out_channels, assume out_nc = in_nc
        out_nc = in_nc
        scale_sq = out_channels // out_nc
        scale = int(math.sqrt(scale_sq))
        if scale < 1: scale = 1
        return 'SRVGGNetCompact', {'in_nc': in_nc, 'out_nc': out_nc, 'nf': nf, 'nb': nb, 'scale': scale}
    
    # RRDBNet OLD format: model.0.weight, model.1.sub.X.RDB1...
    if any(k.startswith('model.') for k in keys):
        in_nc = sd['model.0.weight'].shape[1]
        nf = sd['model.0.weight'].shape[0]
        gc = sd.get('model.1.sub.0.RDB1.conv1.0.weight', sd.get('model.1.sub.0.RDB1.conv1.weight', None))
        gc = gc.shape[0] if gc is not None else 32
        # Count RRDB blocks: keys like model.1.sub.0.RDB1, model.1.sub.1.RDB1, etc.
        rdb_indices = []
        for k in keys:
            m = re.match(r'model\.1\.sub\.(\d+)\.RDB', k)
            if m:
                rdb_indices.append(int(m.group(1)))
        nb = max(rdb_indices) + 1 if rdb_indices else 23
        # Find output conv
        out_nc = 3  # Default
        for k in keys:
            if re.match(r'model\.\d+\.weight', k) and sd[k].dim() == 4:
                if sd[k].shape[0] <= 4:  # likely output conv
                    out_nc = sd[k].shape[0]
        return 'RRDBNet_old', {'in_nc': in_nc, 'out_nc': out_nc, 'nf': nf, 'nb': nb, 'gc': gc}
    
    # RRDBNet NEW format: conv_first.weight, body.0.rdb1...
    if any('conv_first' in k for k in keys) and any('.rdb1.' in k.lower() or '.RDB1.' in k for k in keys):
        in_nc = sd['conv_first.weight'].shape[1]
        nf = sd['conv_first.weight'].shape[0]
        gc_key = [k for k in keys if 'rdb1.conv1' in k.lower() and 'weight' in k]
        gc = sd[gc_key[0]].shape[0] if gc_key else 32
        body_indices = [int(k.split('.')[1]) for k in keys if re.match(r'body\.\d+\.rdb', k, re.IGNORECASE)]
        nb = max(body_indices) + 1 if body_indices else 23
        out_nc = sd.get('conv_last.weight', torch.zeros(3,1,1,1)).shape[0]
        return 'RRDBNet_new', {'in_nc': in_nc, 'out_nc': out_nc, 'nf': nf, 'nb': nb, 'gc': gc}
    
    # DAT / HAT: These have layers.X.blocks.Y.attn patterns  
    if any('layers.' in k and 'attn' in k for k in keys):
        return 'DAT', {}
    
    # SPAN
    if any('block_' in k for k in keys):
        return 'SPAN', {}
    
    return 'unknown', {}


def remap_old_to_new(sd):
    """Remap old ESRGAN keys (model.X) to new RRDBNet keys."""
    new_sd = {}
    for k, v in sd.items():
        nk = k
        # model.0 → conv_first
        nk = re.sub(r'^model\.0\.', 'conv_first.', nk)
        # model.1.sub.X.RDBY.convZ.0. → body.X.RDBY.convZ.
        nk = re.sub(r'^model\.1\.sub\.(\d+)\.RDB(\d+)\.conv(\d+)\.0\.', r'body.\1.RDB\2.conv\3.', nk)
        # model.1.sub.23. → conv_body. (or last sub entry)
        nk = re.sub(r'^model\.1\.sub\.\d+\.$', 'conv_body.', nk)
        # Find the trunk conv
        m = re.match(r'^model\.1\.sub\.(\d+)\.(weight|bias)$', k)
        if m:
            nk = f'conv_body.{m.group(2)}'
        # model.2 through model.N → upconvs and output
        new_sd[nk] = v
    return new_sd


def detect_scale_from_sd(sd, arch):
    """Detect upscaling factor from state dict."""
    keys = list(sd.keys())
    
    if arch == 'RRDBNet_old':
        # Count upconv layers (model.3, model.6 for 4x, etc.)
        up_keys = [k for k in keys if re.match(r'model\.[3-9]\d*\.weight', k) and sd[k].dim() == 4 and sd[k].shape[0] == sd[k].shape[1]]
        n_ups = len(up_keys)
        if n_ups >= 2: return 4
        if n_ups >= 1: return 2
        return 1
    
    if arch in ('RRDBNet_new',):
        up_keys = [k for k in keys if 'upconv' in k.lower() or 'up_conv' in k.lower()]
        n_ups = len([k for k in up_keys if 'weight' in k])
        if n_ups >= 2: return 4
        if n_ups >= 1: return 2
        return 1
    
    if arch == 'SRVGGNetCompact':
        return 4  # detected from output channels in detect_config
    
    # Fallback: try from filename
    return 4


def build_model(arch, config, scale):
    """Build model instance from detected architecture and config."""
    if arch in ('RRDBNet_old', 'RRDBNet_new'):
        return RRDBNet(
            in_nc=config.get('in_nc', 3),
            out_nc=config.get('out_nc', 3),
            nf=config.get('nf', 64),
            nb=config.get('nb', 23),
            gc=config.get('gc', 32),
            scale=scale,
        )
    
    if arch == 'SRVGGNetCompact':
        return SRVGGNetCompact(
            in_nc=config.get('in_nc', 3),
            out_nc=config.get('out_nc', 3),
            nf=config.get('nf', 64),
            nb=config.get('nb', 16),
            scale=config.get('scale', scale),
        )
    
    return None


# ═══════════════════════════════════════════════════════════
# Conversion Pipeline
# ═══════════════════════════════════════════════════════════

def convert_model(pth_path, ncnn_dir):
    """Convert a single .pth model to ncnn format."""
    name = pth_path.stem
    ncnn_bin = ncnn_dir / f"{name}.bin"
    ncnn_param = ncnn_dir / f"{name}.param"
    # pnnx replaces hyphens with underscores
    alt_name = name.replace('-', '_')
    alt_bin = ncnn_dir / f"{alt_name}.bin"
    alt_param = ncnn_dir / f"{alt_name}.param"
    
    if (ncnn_bin.exists() and ncnn_param.exists()) or (alt_bin.exists() and alt_param.exists()):
        return "skip", "already exists"
    
    # Step 1: Load state dict
    try:
        sd = load_state_dict(pth_path)
    except Exception as e:
        return "error", f"load failed: {e}"
    
    # Step 2: Detect architecture
    arch, config = detect_config(sd)
    
    if arch in ('DAT', 'SPAN', 'unknown'):
        return "skip_arch", f"architecture '{arch}' not yet supported for ncnn conversion"
    
    # Step 3: Detect scale
    if arch == 'SRVGGNetCompact':
        scale = config.get('scale', 4)
    else:
        scale = detect_scale_from_sd(sd, arch)
        # Also check filename for scale hints
        m = re.search(r'(\d)x|x(\d)', name.lower())
        if m:
            file_scale = int(m.group(1) or m.group(2))
            if file_scale in (1, 2, 4, 8):
                scale = file_scale
    
    # Step 4: Remap old keys if needed
    if arch == 'RRDBNet_old':
        sd = remap_old_to_new(sd)
        arch = 'RRDBNet_new'
    
    # Step 5: Build model and load weights
    model = build_model(arch, config, scale)
    if model is None:
        return "error", f"could not build model for {arch}"
    
    try:
        model.load_state_dict(sd, strict=False)
    except Exception as e:
        return "error", f"weight loading failed: {e}"
    
    model.eval()
    
    # Step 6: Trace model
    in_nc = config.get('in_nc', 3)
    dummy_input = torch.randn(1, in_nc, 64, 64)
    
    try:
        with torch.no_grad():
            traced = torch.jit.trace(model, dummy_input)
    except Exception as e:
        return "error", f"tracing failed: {e}"
    
    # Step 7: Save TorchScript
    ts_path = pth_path.with_suffix('.pt')
    traced.save(str(ts_path))
    
    # Step 8: Convert with pnnx
    # Find pnnx executable
    pnnx_exe = shutil.which('pnnx')
    if not pnnx_exe:
        import importlib.util
        spec = importlib.util.find_spec('pnnx')
        if spec and spec.origin:
            pnnx_dir = Path(spec.origin).parent
            pnnx_exe = str(pnnx_dir / 'pnnx.exe')
    if not pnnx_exe:
        # Try common user install path
        user_scripts = Path(os.environ.get('APPDATA', '')) / 'Python' / 'Python314' / 'Scripts' / 'pnnx.exe'
        if user_scripts.exists():
            pnnx_exe = str(user_scripts)
    if not pnnx_exe:
        return "error", "pnnx not found! Install with: pip install pnnx"
    
    try:
        result = subprocess.run(
            [pnnx_exe, str(ts_path), f'inputshape=[1,{in_nc},64,64]'],
            capture_output=True, text=True, timeout=120,
            cwd=str(pth_path.parent)
        )
        
        # pnnx outputs files with underscored names: stem.ncnn.param, stem.ncnn.bin
        # It also replaces hyphens with underscores 
        # Search for any .ncnn.bin/.ncnn.param in the output directory
        parent = pth_path.parent
        ncnn_files_bin = list(parent.glob("*.ncnn.bin"))
        ncnn_files_param = list(parent.glob("*.ncnn.param"))
        
        # Find the newest ones (just created by pnnx)
        pnnx_bin_found = None
        pnnx_param_found = None
        for f in ncnn_files_bin:
            # Match by stem similarity
            fstem = f.name.replace('.ncnn.bin', '')
            tstem = ts_path.stem.replace('-', '_')
            if fstem == ts_path.stem or fstem == tstem:
                pnnx_bin_found = f
                break
        for f in ncnn_files_param:
            fstem = f.name.replace('.ncnn.param', '')
            tstem = ts_path.stem.replace('-', '_')
            if fstem == ts_path.stem or fstem == tstem:
                pnnx_param_found = f
                break
        
        if pnnx_param_found and pnnx_bin_found:
            # Use the pnnx-generated name (with underscores) as the ncnn model name
            pnnx_stem = pnnx_bin_found.name.replace('.ncnn.bin', '')
            final_bin = ncnn_dir / f"{pnnx_stem}.bin"
            final_param = ncnn_dir / f"{pnnx_stem}.param"
            shutil.move(str(pnnx_bin_found), str(final_bin))
            shutil.move(str(pnnx_param_found), str(final_param))
            
            # Cleanup all intermediate pnnx files
            for pattern in ['*.pnnx.param', '*.pnnx.bin', '*.pnnx.onnx', '*_pnnx.py', '*_ncnn.py', '*.foldable_constants.zip']:
                for f in parent.glob(pattern):
                    if ts_path.stem.replace('-', '_') in f.name.replace('-', '_'):
                        f.unlink()
            
            size_mb = final_bin.stat().st_size / (1024*1024)
            return "ok", f"{size_mb:.1f} MB (scale={scale}, arch={arch})"
        else:
            return "error", f"pnnx output not found. stderr: {result.stderr[:300]}"
    except FileNotFoundError:
        return "error", "pnnx not found! Install with: pip install pnnx"
    except Exception as e:
        return "error", f"pnnx failed: {e}"
    finally:
        # Clean up TorchScript file
        if ts_path.exists():
            ts_path.unlink()


def main():
    print("=== PTH → NCNN Converter ===")
    print(f"Source: {PTH_DIR}")
    print(f"Output: {NCNN_DIR}\n")
    
    # Find all model files
    models = sorted(PTH_DIR.glob("*.pth")) + sorted(PTH_DIR.glob("*.safetensors"))
    print(f"Found {len(models)} model files\n")
    
    results = {'ok': [], 'skip': [], 'skip_arch': [], 'error': []}
    
    for i, model_path in enumerate(models):
        print(f"[{i+1:2d}/{len(models)}] {model_path.name}...", end=" ", flush=True)
        status, msg = convert_model(model_path, NCNN_DIR)
        results[status].append((model_path.name, msg))
        
        symbols = {'ok': '✓', 'skip': '→', 'skip_arch': '⊘', 'error': '✗'}
        print(f"{symbols.get(status, '?')} {msg}")
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Converted: {len(results['ok'])}  |  Skipped: {len(results['skip'])}  |  Unsupported: {len(results['skip_arch'])}  |  Errors: {len(results['error'])}")
    
    if results['ok']:
        print(f"\n✓ Successfully converted:")
        for name, msg in results['ok']:
            print(f"  ⭐ {name} → {msg}")
    
    if results['skip_arch']:
        print(f"\n⊘ Unsupported architectures (DAT/SPAN/etc):")
        for name, msg in results['skip_arch']:
            print(f"  {name}: {msg}")
    
    if results['error']:
        print(f"\n✗ Errors:")
        for name, msg in results['error']:
            print(f"  {name}: {msg}")


if __name__ == "__main__":
    main()
