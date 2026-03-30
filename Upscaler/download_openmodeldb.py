#!/usr/bin/env python3
"""
Batch download .pth/.safetensors models from OpenModelDB pages.
Extracts download URLs from each model page and downloads them.
Handles Google Drive, GitHub, HuggingFace, and flags Mega links.
"""
import os, sys, re, time, json, http.cookiejar
import urllib.request, urllib.error, urllib.parse
from pathlib import Path

DEST = Path(r"c:\Users\Alexander Jarvis\Desktop\Upscaler\custom_models\pth_models")
DEST.mkdir(parents=True, exist_ok=True)

# All unique model slugs from the user's open browser tabs
MODEL_SLUGS = [
    "4x-UltraSharpV2",
    "1x-Archiver-Medium",
    "4x-NomosWebPhoto-esrgan",
    "4x-Textures-GTAV-rgt-s-dither",
    "1x-DXTDecompressor-Source-V3",
    "4x-UniScaleNR-Strong",
    "4x-realesrgan-x4minus",
    "2x-AnimeSharpV3",
    "1x-Archiver-AntiLines",
    "4x-UniversalUpscalerV2-Sharp",
    "1x-BC1-smooth2",
    "4x-Normal-RG0",
    "1x-Sega-Genesis-Cleanup-Small",
    "4x-GameAI-2-0",
    "4x-SGI",
    "2x-Pooh-V4",
    "2x-DigitalPokemon-l",
    "4x-UniScale-Restore",
    "4x-PBRify-UpscalerV4",
    "4x-LSDIRDAT",
    "1x-Archiver-Rough",
    "1x-DXTless-SourceEngine",
    "4x-Normal-RG0-BC1",
    "2x-LiveActionV1-SPAN",
    "4x-HDCube3",
    "4x-FaceUpDAT",
    "4x-ESRGAN",
    "4x-Textures-GTAV-rgt-s",
    "1x-NormalMapGenerator-CX-Lite",
    "4x-PBRify-RPLKSRd-V3",
    "4x-Rybu",
    "4x-LSDIRplus",
    "1x-Archiver-RGB",
    "4x-LSDIRplusR",
    "4x-Nomos8kHAT-L-otf",
    "4x-PBRify-UpscalerSPANV4",
    "4x-FatePlusCompact",
    "4x-NomosUniDAT-bokeh-jpg",
    "4x-SPSR",
    "2x-AoMR-mosr",
    "4x-realsr-df2k",
    "2x-HDCube-Compact",
    "1x-DEDXT",
    "1x-SuperScale-RPLKSR-S",
    "4x-NMKD-Siax-CX",
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Cookie-aware opener for Google Drive large files
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [("User-Agent", UA)]


def extract_download_url(slug):
    """Fetch OpenModelDB page and extract the download link."""
    url = f"https://openmodeldb.info/models/{slug}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Primary pattern: the big download button link  
        # Format: <a href="URL" ...> ... Download (XX MB) ...
        # The href can be to Google Drive, GitHub, HuggingFace, Mega, etc.
        # Use a broad pattern that captures any href before "Download (XX"
        m = re.search(
            r'<a\s[^>]*href="([^"]+)"[^>]*>[^<]*(?:<[^>]*>)*[^<]*Download\s*\(\d+',
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            return urllib.parse.unquote(m.group(1)).replace("&amp;", "&")
        
        # Fallback: any href containing known host patterns near "download"
        for pattern in [
            r'href="(https://drive\.google\.com/uc\?[^"]+)"',
            r'href="(https://github\.com/[^"]+/releases/download/[^"]+)"',
            r'href="(https://huggingface\.co/[^"]+/resolve/[^"]+)"',
            r'href="(https://mega\.nz/[^"]+)"',
            r'href="(https://objectstorage[^"]+)"',
        ]:
            m = re.search(pattern, html)
            if m:
                return m.group(1).replace("&amp;", "&")
        
        return None
    except Exception as e:
        return f"ERROR:{e}"


def download_gdrive(url, dest_path):
    """Download from Google Drive, handling virus-scan confirmation for large files."""
    try:
        resp = opener.open(url, timeout=180)
        data = resp.read()
        
        # Check if we got the confirmation page instead of the file
        if len(data) < 100000 and b"virus scan" in data.lower() or b"confirm" in data.lower():
            # Parse the confirmation form
            m = re.search(rb'href="(/uc\?[^"]+)"', data)
            if m:
                confirm_url = "https://drive.google.com" + m.group(1).decode().replace("&amp;", "&")
                resp = opener.open(confirm_url, timeout=180)
                data = resp.read()
        
        with open(dest_path, "wb") as f:
            f.write(data)
        return True, len(data) / (1024*1024)
    except Exception as e:
        return False, str(e)


def download_direct(url, dest_path):
    """Download from GitHub/HuggingFace/etc with redirect following."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=180) as resp:
            # Get filename from Content-Disposition if available
            cd = resp.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                fname = re.search(r'filename[*]?="?([^";\r\n]+)', cd)
                if fname:
                    actual_name = fname.group(1).strip().strip("'").split("''")[-1]
                    dest_path = DEST / actual_name
            
            data = resp.read()
        
        with open(dest_path, "wb") as f:
            f.write(data)
        return True, len(data) / (1024*1024)
    except Exception as e:
        return False, str(e)


def download_model(slug, url):
    """Download a model, routing to the right handler based on host."""
    # Determine extension from URL
    ext = ".pth"
    if ".safetensors" in url:
        ext = ".safetensors"
    
    dest_path = DEST / f"{slug}{ext}"
    
    # Check for existing files with any extension
    for e in [".pth", ".safetensors"]:
        if (DEST / f"{slug}{e}").exists():
            return "skip", 0
    
    if "drive.google.com" in url:
        return download_gdrive(url, dest_path)
    elif "mega.nz" in url:
        return "mega", url  # Can't automate Mega easily
    else:
        return download_direct(url, dest_path)


def main():
    print(f"=== OpenModelDB Batch Downloader ===")
    print(f"Destination: {DEST}")
    print(f"Models: {len(MODEL_SLUGS)}\n")
    
    # Phase 1: Extract download URLs
    print("Phase 1: Extracting download URLs...")
    url_map = {}
    mega_models = []
    errors = []
    
    for i, slug in enumerate(MODEL_SLUGS):
        print(f"  [{i+1:2d}/{len(MODEL_SLUGS)}] {slug}...", end=" ", flush=True)
        dl = extract_download_url(slug)
        if dl and dl.startswith("ERROR:"):
            print(f"✗ {dl}")
            errors.append(slug)
        elif dl:
            url_map[slug] = dl
            host = "GDrive" if "drive.google" in dl else "GitHub" if "github.com" in dl else "HFace" if "huggingface" in dl else "Mega" if "mega.nz" in dl else "Other"
            print(f"✓ [{host}]")
            if "mega.nz" in dl:
                mega_models.append((slug, dl))
        else:
            print("✗ no link found")
            errors.append(slug)
        time.sleep(0.2)
    
    # Save URL map
    with open(DEST / "_download_urls.json", "w") as f:
        json.dump(url_map, f, indent=2)
    
    downloadable = {k: v for k, v in url_map.items() if "mega.nz" not in v}
    print(f"\nFound {len(url_map)} URLs ({len(downloadable)} direct, {len(mega_models)} Mega, {len(errors)} failed)")
    
    # Phase 2: Download
    print(f"\nPhase 2: Downloading {len(downloadable)} models...")
    success = 0
    skipped = 0
    failed = []
    
    for i, (slug, url) in enumerate(downloadable.items()):
        print(f"  [{i+1:2d}/{len(downloadable)}] {slug}...", end=" ", flush=True)
        result = download_model(slug, url)
        
        if result[0] == "skip":
            print("SKIP (exists)")
            skipped += 1
        elif result[0] == True:
            print(f"✓ {result[1]:.1f} MB")
            success += 1
        else:
            print(f"✗ {result[1]}")
            failed.append((slug, str(result[1])))
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Downloaded: {success}  |  Skipped: {skipped}  |  Failed: {len(failed)}")
    
    if mega_models:
        print(f"\n⚠ {len(mega_models)} models on Mega (manual download needed):")
        for slug, url in mega_models:
            print(f"  📎 {slug}: {url}")
    
    if failed:
        print(f"\n✗ {len(failed)} failed downloads:")
        for slug, err in failed:
            print(f"  ✗ {slug}: {err}")
    
    # List results
    all_files = list(DEST.glob("*.pth")) + list(DEST.glob("*.safetensors"))
    print(f"\n📦 Total model files: {len(all_files)}")
    total_mb = 0
    for f in sorted(all_files):
        sz = f.stat().st_size / (1024*1024)
        total_mb += sz
        print(f"  {f.name} ({sz:.1f} MB)")
    print(f"Total size: {total_mb:.0f} MB")


if __name__ == "__main__":
    main()
