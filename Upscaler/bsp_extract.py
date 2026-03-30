#!/usr/bin/env python3
"""
BSP PAK Lump Extractor — Extracts VTF textures from Source Engine BSP files.
The PAK lump (lump 40) is a ZIP archive embedded in the BSP containing textures,
materials, and other assets.
"""

import struct
import zipfile
import os
import sys
import io

# BSP header: 4-byte magic "VBSP", 4-byte version, then 64 lumps (each 16 bytes)
BSP_MAGIC = b'VBSP'
LUMP_PAKFILE = 40  # PAK lump index

def extract_bsp_vtfs(bsp_path: str, output_dir: str, extract_all: bool = False):
    """
    Extract VTF files from a BSP's embedded PAK lump.
    
    Args:
        bsp_path: Path to .bsp file
        output_dir: Directory to extract VTFs into (preserving folder structure)
        extract_all: If True, extract ALL files (VMT, VTF, etc). If False, only VTFs.
    """
    if not os.path.exists(bsp_path):
        print(f"ERROR: BSP file not found: {bsp_path}")
        return False
    
    print(f"Reading BSP: {bsp_path}")
    print(f"File size: {os.path.getsize(bsp_path) / 1024 / 1024:.1f} MB")
    
    with open(bsp_path, 'rb') as f:
        # Read header
        magic = f.read(4)
        if magic != BSP_MAGIC:
            print(f"ERROR: Not a valid BSP file (magic: {magic})")
            return False
        
        version = struct.unpack('<I', f.read(4))[0]
        print(f"BSP version: {version}")
        
        # Read lump directory (64 lumps, each 16 bytes: offset, length, version, fourCC)
        lumps = []
        for i in range(64):
            offset, length, lump_ver, four_cc = struct.unpack('<IIII', f.read(16))
            lumps.append((offset, length, lump_ver, four_cc))
        
        # Get PAK lump
        pak_offset, pak_length, _, _ = lumps[LUMP_PAKFILE]
        
        if pak_length == 0:
            print("ERROR: PAK lump is empty — this BSP has no embedded files.")
            return False
        
        print(f"PAK lump: offset={pak_offset}, size={pak_length / 1024 / 1024:.1f} MB")
        
        # Read PAK data
        f.seek(pak_offset)
        pak_data = f.read(pak_length)
    
    # PAK lump is a ZIP archive
    try:
        pak_zip = zipfile.ZipFile(io.BytesIO(pak_data))
    except zipfile.BadZipFile:
        print("ERROR: PAK lump is not a valid ZIP archive.")
        return False
    
    all_files = pak_zip.namelist()
    print(f"Total files in PAK: {len(all_files)}")
    
    # Filter to VTFs (or all files)
    if extract_all:
        to_extract = all_files
    else:
        to_extract = [f for f in all_files if f.lower().endswith('.vtf')]
    
    # Also list VMTs for reference
    vmts = [f for f in all_files if f.lower().endswith('.vmt')]
    other = [f for f in all_files if not f.lower().endswith(('.vtf', '.vmt'))]
    
    print(f"  VTFs: {len([f for f in all_files if f.lower().endswith('.vtf')])}")
    print(f"  VMTs: {len(vmts)}")
    print(f"  Other: {len(other)}")
    print(f"Extracting: {len(to_extract)} files")
    
    # Extract
    os.makedirs(output_dir, exist_ok=True)
    extracted = 0
    total_size = 0
    
    for filepath in to_extract:
        try:
            data = pak_zip.read(filepath)
            out_path = os.path.join(output_dir, filepath.replace('/', os.sep))
            os.makedirs(os.path.dirname(out_path) or output_dir, exist_ok=True)
            with open(out_path, 'wb') as out_f:
                out_f.write(data)
            extracted += 1
            total_size += len(data)
        except Exception as e:
            print(f"  WARN: Failed to extract {filepath}: {e}")
    
    print(f"\n✓ Extracted {extracted} files ({total_size / 1024 / 1024:.1f} MB)")
    print(f"  Output: {output_dir}")
    
    # List the material directories found
    dirs = set()
    for filepath in to_extract:
        parts = filepath.replace('\\', '/').split('/')
        if len(parts) > 1:
            dirs.add('/'.join(parts[:-1]))
    if dirs:
        print(f"\nMaterial directories:")
        for d in sorted(dirs):
            count = len([f for f in to_extract if f.startswith(d)])
            print(f"  {d}/ ({count} files)")
    
    pak_zip.close()
    return True


if __name__ == '__main__':
    bsp = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\maps\rp_liberator_sup_b8c.bsp"
    out = r"G:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons\maps\rp_liberator_extracted"
    
    # Extract VTFs + VMTs (need VMTs for material context)
    extract_bsp_vtfs(bsp, out, extract_all=True)
