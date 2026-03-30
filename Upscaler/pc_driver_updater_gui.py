"""
PC Driver Updater GUI — Full System Driver & Software Maintenance
===================================================================
A modern tkinter GUI that scans all drivers, identifies outdated/problem
ones, and updates everything through Windows Update, pnputil, and winget.

Right-click → Run as Administrator for full functionality.
"""

import subprocess
import sys
import os
import ctypes
import time
import json
import shutil
import datetime
import re
import threading
import concurrent.futures
import webbrowser
import urllib.request
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_PATH = SCRIPT_DIR / "driver_update_log.json"

# ── NVIDIA GPU Series/Product Mappings ───────────────────────────────────────
# Maps GPU name keywords → (series_id, product_id) for NVIDIA's API
# osid=57 = Windows 10/11 64-bit, lid=1 = English, whql=1, dtcid=1 = Game Ready
NVIDIA_SERIES = {
    # RTX 50 Series
    "RTX 5090": (131, 1066), "RTX 5080": (131, 1065), "RTX 5070 Ti": (131, 1068),
    "RTX 5070": (131, 1070), "RTX 5060 Ti": (131, 1076), "RTX 5060": (131, 1078),
    # RTX 40 Series
    "RTX 4090": (127, 995), "RTX 4080 SUPER": (127, 1041), "RTX 4080": (127, 999),
    "RTX 4070 Ti SUPER": (127, 1040), "RTX 4070 Ti": (127, 1001),
    "RTX 4070 SUPER": (127, 1039), "RTX 4070": (127, 1015),
    "RTX 4060 Ti": (127, 1022), "RTX 4060": (127, 1023),
    # RTX 30 Series
    "RTX 3090 Ti": (120, 956), "RTX 3090": (120, 866), "RTX 3080 Ti": (120, 903),
    "RTX 3080": (120, 865), "RTX 3070 Ti": (120, 904), "RTX 3070": (120, 862),
    "RTX 3060 Ti": (120, 872), "RTX 3060": (120, 879),
    # RTX 20 Series
    "RTX 2080 Ti": (107, 797), "RTX 2080 SUPER": (107, 845), "RTX 2080": (107, 796),
    "RTX 2070 SUPER": (107, 844), "RTX 2070": (107, 798),
    "RTX 2060 SUPER": (107, 843), "RTX 2060": (107, 805),
    # GTX 16 Series
    "GTX 1660 Ti": (112, 830), "GTX 1660 SUPER": (112, 851), "GTX 1660": (112, 835),
    "GTX 1650 SUPER": (112, 852), "GTX 1650 Ti": (112, 855), "GTX 1650": (112, 836),
    # GTX 10 Series
    "GTX 1080 Ti": (101, 770), "GTX 1080": (101, 761), "GTX 1070 Ti": (101, 789),
    "GTX 1070": (101, 762), "GTX 1060": (101, 763), "GTX 1050 Ti": (101, 777),
    "GTX 1050": (101, 778),
}


def check_nvidia_driver():
    """Check for NVIDIA GPU driver updates via NVIDIA's API.
    Returns dict with keys: gpu_name, installed_ver, latest_ver, download_url, needs_update
    or None if no NVIDIA GPU found."""
    try:
        # Get GPU info from WMI
        out, rc = run_ps(
            "Get-CimInstance Win32_VideoController | Where-Object { $_.Name -like '*NVIDIA*' } | "
            "Select-Object -First 1 -Property Name, DriverVersion | ConvertTo-Json -Compress"
        )
        if not out or rc != 0:
            return None
        gpu_info = json.loads(out.strip())
        gpu_name = gpu_info.get("Name", "")
        driver_ver_raw = gpu_info.get("DriverVersion", "")
        if not gpu_name:
            return None

        # Convert Windows driver version (32.0.15.9174) → NVIDIA version (591.74)
        parts = driver_ver_raw.split(".")
        if len(parts) >= 4:
            # Combine last two segments: "15" + "9174" = "159174" → "591.74"
            combined = parts[-2] + parts[-1]
            nvidia_ver = combined[-5:-2] + "." + combined[-2:]
        else:
            nvidia_ver = driver_ver_raw

        # Match GPU name to NVIDIA product database (longest match first)
        gpu_upper = gpu_name.upper()
        psid, pfid = None, None
        matched_key = ""
        for key in sorted(NVIDIA_SERIES.keys(), key=len, reverse=True):
            if key.upper() in gpu_upper:
                psid, pfid = NVIDIA_SERIES[key]
                matched_key = key
                break

        if not psid:
            return {"gpu_name": gpu_name, "installed_ver": nvidia_ver,
                    "latest_ver": "?", "download_url": "", "needs_update": False,
                    "error": "GPU not in lookup table"}

        # Query NVIDIA's API for latest Game Ready driver
        api_url = (f"https://www.nvidia.com/Download/processDriver.aspx?"
                   f"psid={psid}&pfid={pfid}&osid=57&lid=1&whql=1&dtcid=1&ctk=0")
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            redirect_url = resp.read().decode().strip()

        if not redirect_url or "nvidia.com" not in redirect_url:
            return {"gpu_name": gpu_name, "installed_ver": nvidia_ver,
                    "latest_ver": "?", "download_url": "", "needs_update": False,
                    "error": "API returned no driver"}

        # Extract driver ID and build download page URL
        # redirect_url = "https://www.nvidia.com/en-us/drivers/details/263199/"
        download_url = redirect_url.strip().rstrip("/")

        # Get the actual version from the processFind API (first result)
        find_url = (f"https://www.nvidia.com/Download/processFind.aspx?"
                    f"psid={psid}&pfid={pfid}&osid=57&lid=1&whql=1&dtcid=1&ctk=0&qnfslb=00&qnf=0")
        req2 = urllib.request.Request(find_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=15) as resp2:
            html = resp2.read().decode()

        # Parse version from the driverResults link text — extract version from URL path
        # The page lists driver versions but not in easily parseable format
        # Use the redirect URL's driver ID to build a direct download link
        # For now, compare using the driver page ID as a proxy
        latest_ver = "?"
        # Extract version number (format: 582.16) from HTML
        ver_match = re.search(r'(\d{3}\.\d{2})', html)
        if ver_match:
            latest_ver = ver_match.group(1)

        needs_update = latest_ver != "?" and latest_ver != nvidia_ver

        return {
            "gpu_name": gpu_name, "installed_ver": nvidia_ver,
            "latest_ver": latest_ver, "download_url": download_url,
            "needs_update": needs_update,
        }

    except Exception:
        return None


def check_vcpp_runtimes():
    """Check which Visual C++ Redistributables are installed.
    Returns list of dicts: {name, version, arch, installed}."""
    required = [
        {"name": "Visual C++ 2015-2022 Redistributable (x64)", "arch": "x64",
         "reg": r"HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64",
         "winget": "Microsoft.VCRedist.2015+.x64"},
        {"name": "Visual C++ 2015-2022 Redistributable (x86)", "arch": "x86",
         "reg": r"HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X86",
         "winget": "Microsoft.VCRedist.2015+.x86"},
        {"name": "Visual C++ 2013 Redistributable (x64)", "arch": "x64",
         "reg": r"HKLM:\SOFTWARE\Microsoft\VisualStudio\12.0\VC\Runtimes\X64",
         "winget": "Microsoft.VCRedist.2013.x64"},
        {"name": "Visual C++ 2012 Redistributable (x64)", "arch": "x64",
         "reg": r"HKLM:\SOFTWARE\Microsoft\VisualStudio\11.0\VC\Runtimes\X64",
         "winget": "Microsoft.VCRedist.2012.x64"},
        {"name": "Visual C++ 2010 Redistributable (x64)", "arch": "x64",
         "reg": r"HKLM:\SOFTWARE\Microsoft\VisualStudio\10.0\VC\Runtimes\X64",
         "winget": "Microsoft.VCRedist.2010.x64"},
    ]
    results = []
    for r in required:
        out, rc = run_ps(
            f"if(Test-Path '{r['reg']}') {{ "
            f"(Get-ItemProperty '{r['reg']}').Version "
            f"}} else {{ 'NOT_INSTALLED' }}", timeout=10
        )
        installed = out.strip() != "NOT_INSTALLED" and out.strip() != "" if out else False
        version = out.strip() if installed else None
        results.append({
            "name": r["name"], "arch": r["arch"], "installed": installed,
            "version": version, "winget": r["winget"]
        })
    return results


def check_dotnet_runtimes():
    """Check installed .NET runtimes. Returns list of {type, version}."""
    try:
        out, rc = run_cmd("dotnet --list-runtimes", timeout=15)
        if rc != 0 or not out:
            return []
        runtimes = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # Format: "Microsoft.NETCore.App 8.0.11 [path]"
            parts = line.split()
            if len(parts) >= 2:
                runtimes.append({"type": parts[0], "version": parts[1]})
        return runtimes
    except Exception:
        return []


def check_directx():
    """Get DirectX version info. Returns dict with version and feature_level."""
    try:
        out, rc = run_ps(
            "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\DirectX').Version",
            timeout=10
        )
        dx_ver = out.strip() if out else "Unknown"
        # Get GPU feature level
        out2, _ = run_ps(
            "(Get-CimInstance Win32_VideoController | Select-Object -First 1).DriverVersion",
            timeout=10
        )
        return {"version": dx_ver, "driver_ver": out2.strip() if out2 else "?"}
    except Exception:
        return {"version": "Unknown", "driver_ver": "?"}


def get_bios_info():
    """Get BIOS and motherboard information."""
    try:
        out, rc = run_ps(
            "$b = Get-CimInstance Win32_BIOS; $m = Get-CimInstance Win32_BaseBoard; "
            "[PSCustomObject]@{ "
            "BIOSVersion=$b.SMBIOSBIOSVersion; BIOSDate=$b.ReleaseDate; "
            "Manufacturer=$b.Manufacturer; "
            "BoardManuf=$m.Manufacturer; BoardProduct=$m.Product "
            "} | ConvertTo-Json -Compress", timeout=15
        )
        if out and rc == 0:
            return json.loads(out.strip())
        return None
    except Exception:
        return None


def get_system_info():
    """Get CPU and RAM summary info."""
    try:
        out, rc = run_ps(
            "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1; "
            "$ram = Get-CimInstance Win32_PhysicalMemory; "
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "[PSCustomObject]@{ "
            "CPUName=$cpu.Name; Cores=$cpu.NumberOfCores; Threads=$cpu.ThreadCount; "
            "MaxClock=$cpu.MaxClockSpeed; "
            "TotalRAM=[math]::Round($os.TotalVisibleMemorySize/1MB, 1); "
            "RAMSlots=($ram | Measure-Object).Count; "
            "RAMSpeed=($ram | Select-Object -First 1).Speed; "
            "OSName=$os.Caption; OSBuild=$os.BuildNumber "
            "} | ConvertTo-Json -Compress", timeout=15
        )
        if out and rc == 0:
            return json.loads(out.strip())
        return None
    except Exception:
        return None


def get_temp_sizes():
    """Get sizes of temp/cache folders in MB. Returns dict of {folder: size_mb}."""
    folders = {
        "User Temp": os.environ.get("TEMP", ""),
        "Windows Temp": r"C:\Windows\Temp",
        "WU Cache": r"C:\Windows\SoftwareDistribution\Download",
    }
    results = {}
    for label, path in folders.items():
        if not path or not os.path.exists(path):
            results[label] = 0
            continue
        try:
            out, rc = run_ps(
                f"(Get-ChildItem -Path '{path}' -Recurse -Force -ErrorAction SilentlyContinue | "
                f"Measure-Object -Property Length -Sum).Sum / 1MB", timeout=15
            )
            size = float(out.strip()) if out and out.strip() else 0
            results[label] = round(size, 1)
        except Exception:
            results[label] = 0
    return results


def check_amd_chipset():
    """Check if AMD CPU is present and get chipset driver info."""
    try:
        out, rc = run_ps(
            "Get-CimInstance Win32_Processor | Select-Object -First 1 -Property Name, Manufacturer | "
            "ConvertTo-Json -Compress", timeout=10
        )
        if not out or rc != 0:
            return None
        cpu = json.loads(out.strip())
        if "AMD" not in cpu.get("Manufacturer", "") and "AMD" not in cpu.get("Name", ""):
            return None
        # Get AMD chipset driver info
        out2, _ = run_ps(
            "Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.Manufacturer -like '*AMD*' -and "
            "$_.DeviceClass -eq 'SYSTEM' } | Select-Object -First 1 -Property "
            "DeviceName, DriverVersion, DriverDate | ConvertTo-Json -Compress", timeout=15
        )
        chipset = json.loads(out2.strip()) if out2 and out2.strip() and out2.strip() != "" else None
        return {"cpu_name": cpu.get("Name", "AMD CPU"), "chipset": chipset}
    except Exception:
        return None


# ─── System Restore Point ───────────────────────────────────────────────────────

def create_restore_point(description="PC Maintenance Suite Auto-Backup"):
    """Create a Windows System Restore point. Returns (success, message)."""
    out, rc = run_ps(
        f"Enable-ComputerRestore -Drive 'C:\\' -EA SilentlyContinue; "
        f"Checkpoint-Computer -Description '{description}' -RestorePointType 'MODIFY_SETTINGS' -EA Stop",
        timeout=120
    )
    if rc == 0:
        return True, "Restore point created"
    return False, out.strip() if out else "Failed to create restore point"


# ─── Power Plan Management ───────────────────────────────────────────────────────

def get_power_plan():
    """Get current active power plan. Returns dict {name, guid}."""
    out = run_ps(
        "powercfg /getactivescheme",
        timeout=10
    )[0]
    if out:
        m = re.search(r'([0-9a-f\-]{36})\s+\((.+)\)', out)
        if m:
            return {"guid": m.group(1), "name": m.group(2)}
    return {"guid": "unknown", "name": "Unknown"}

def set_power_plan(mode="ultimate", clone_settings=True):
    """Set power plan, optionally cloning settings from the current plan.
    mode: 'ultimate', 'high', 'balanced'. Returns (success, plan_name)."""
    guids = {
        "ultimate": "e9a42b02-d5df-448d-aa00-03f14749eb61",
        "high": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
    }
    names = {"ultimate": "Ultimate Performance", "high": "High Performance", "balanced": "Balanced"}
    target_guid = guids.get(mode, guids["balanced"])
    target_name = names.get(mode, "Balanced")

    # Capture current plan's custom settings before switching
    custom_settings = []
    if clone_settings:
        try:
            analysis = get_power_plan_analysis()
            for group in analysis.get("groups", []):
                for s in group.get("settings", []):
                    if s.get("ac") or s.get("dc"):
                        custom_settings.append({
                            "subgroup": group["guid"],
                            "setting": s["guid"],
                            "ac": s.get("ac", ""),
                            "dc": s.get("dc", ""),
                        })
        except Exception:
            pass

    # Create Ultimate Performance if needed
    if mode == "ultimate":
        run_ps(f"powercfg -duplicatescheme {target_guid}", timeout=10)

    # Activate the target plan
    run_ps(f"powercfg /setactive {target_guid}", timeout=10)

    # Apply captured custom settings to the new plan
    if clone_settings and custom_settings:
        cmds = []
        for cs in custom_settings:
            ac_val = cs["ac"]
            dc_val = cs["dc"]
            sg = cs["subgroup"]
            st = cs["setting"]
            if ac_val:
                try:
                    ac_int = int(ac_val, 16) if ac_val.startswith("0x") else int(ac_val)
                    cmds.append(f"powercfg /setacvalueindex {target_guid} {sg} {st} {ac_int}")
                except (ValueError, TypeError):
                    pass
            if dc_val:
                try:
                    dc_int = int(dc_val, 16) if dc_val.startswith("0x") else int(dc_val)
                    cmds.append(f"powercfg /setdcvalueindex {target_guid} {sg} {st} {dc_int}")
                except (ValueError, TypeError):
                    pass
        # Batch apply all settings
        if cmds:
            batch = "; ".join(cmds)
            run_ps(batch, timeout=30)

    return True, target_name

def get_power_plan_analysis():
    """Deep-analyze the active power plan. Returns dict with plan info and all settings."""
    result = {"plan_name": "Unknown", "plan_guid": "", "groups": []}
    try:
        # Get active plan name and GUID
        out, _ = run_ps("powercfg /getactivescheme", timeout=10)
        if out:
            import re as _re
            m = _re.search(r"([0-9a-f\-]{36})\s+\((.+?)\)", out.strip())
            if m:
                result["plan_guid"] = m.group(1)
                result["plan_name"] = m.group(2)

        # Full query of the active plan
        out2, _ = run_ps("powercfg /query", timeout=15)
        if not out2:
            return result

        current_group = None
        current_setting = None
        for raw_line in out2.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("Subgroup GUID:"):
                m = _re.search(r"Subgroup GUID:\s+([0-9a-f\-]+)\s+\((.+?)\)", line)
                if m:
                    current_group = {"name": m.group(2), "guid": m.group(1), "settings": []}
                    result["groups"].append(current_group)
                    current_setting = None

            elif line.startswith("Power Setting GUID:"):
                m = _re.search(r"Power Setting GUID:\s+([0-9a-f\-]+)\s+\((.+?)\)", line)
                if m and current_group is not None:
                    current_setting = {"name": m.group(2), "guid": m.group(1),
                                       "ac": "", "dc": "", "min": "", "max": "", "unit": ""}
                    current_group["settings"].append(current_setting)

            elif current_setting is not None:
                if line.startswith("Minimum Possible Setting:"):
                    current_setting["min"] = line.split(":")[-1].strip()
                elif line.startswith("Maximum Possible Setting:"):
                    current_setting["max"] = line.split(":")[-1].strip()
                elif line.startswith("Possible Settings units:"):
                    current_setting["unit"] = line.split(":")[-1].strip()
                elif line.startswith("Current AC Power Setting Index:"):
                    current_setting["ac"] = line.split(":")[-1].strip()
                elif line.startswith("Current DC Power Setting Index:"):
                    current_setting["dc"] = line.split(":")[-1].strip()
    except Exception:
        pass
    return result



# ─── Browser Cache ───────────────────────────────────────────────────────────────

def get_browser_caches():
    """Scan browser cache sizes. Returns dict of {browser: size_mb}."""
    user_profile = os.environ.get("USERPROFILE", "")
    browsers = {
        "Chrome": os.path.join(user_profile, r"AppData\Local\Google\Chrome\User Data\Default\Cache"),
        "Edge": os.path.join(user_profile, r"AppData\Local\Microsoft\Edge\User Data\Default\Cache"),
        "Firefox": os.path.join(user_profile, r"AppData\Local\Mozilla\Firefox\Profiles"),
        "Brave": os.path.join(user_profile, r"AppData\Local\BraveSoftware\Brave-Browser\User Data\Default\Cache"),
    }
    result = {}
    for name, path in browsers.items():
        try:
            if os.path.exists(path):
                total = 0
                for root, dirs, files in os.walk(path):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except (OSError, PermissionError):
                            pass
                result[name] = round(total / (1024 * 1024), 1)
        except Exception:
            pass
    return result

def clean_browser_caches():
    """Clean all browser caches. Returns dict of {browser: mb_cleaned}."""
    caches = get_browser_caches()
    user_profile = os.environ.get("USERPROFILE", "")
    paths = {
        "Chrome": os.path.join(user_profile, r"AppData\Local\Google\Chrome\User Data\Default\Cache"),
        "Edge": os.path.join(user_profile, r"AppData\Local\Microsoft\Edge\User Data\Default\Cache"),
        "Brave": os.path.join(user_profile, r"AppData\Local\BraveSoftware\Brave-Browser\User Data\Default\Cache"),
    }
    # Firefox uses profile folders
    ff_path = os.path.join(user_profile, r"AppData\Local\Mozilla\Firefox\Profiles")
    cleaned = {}
    for browser, path in paths.items():
        if browser in caches and caches[browser] > 0 and os.path.exists(path):
            try:
                import shutil
                shutil.rmtree(path, ignore_errors=True)
                cleaned[browser] = caches[browser]
            except Exception:
                cleaned[browser] = 0
        else:
            cleaned[browser] = 0
    if "Firefox" in caches and caches["Firefox"] > 0 and os.path.exists(ff_path):
        try:
            for profile in os.listdir(ff_path):
                cache_dir = os.path.join(ff_path, profile, "cache2")
                if os.path.exists(cache_dir):
                    import shutil
                    shutil.rmtree(cache_dir, ignore_errors=True)
            cleaned["Firefox"] = caches["Firefox"]
        except Exception:
            cleaned["Firefox"] = 0
    return cleaned


# ─── Windows Defender ────────────────────────────────────────────────────────────

def run_defender_scan(scan_type="quick"):
    """Run Windows Defender scan. scan_type: 'quick' or 'full'. Returns (output, return_code)."""
    t = "1" if scan_type == "quick" else "2"
    return run_ps(
        f"Start-MpScan -ScanType {t}",
        timeout=600
    )


# ─── Network Diagnostics ────────────────────────────────────────────────────────

def get_network_diagnostics():
    """Run network diagnostic checks. Returns dict of results."""
    results = {}
    # Default gateway
    gw_out = run_ps(
        "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -EA SilentlyContinue | Select -First 1).NextHop",
        timeout=10
    )[0]
    results["gateway"] = gw_out.strip() if gw_out else "N/A"

    # DNS servers
    dns_out = run_ps(
        "(Get-DnsClientServerAddress -AddressFamily IPv4 -EA SilentlyContinue | "
        "Where-Object {$_.ServerAddresses} | Select -First 1).ServerAddresses -join ', '",
        timeout=10
    )[0]
    results["dns"] = dns_out.strip() if dns_out else "N/A"

    # Ping tests
    for host in ["8.8.8.8", "1.1.1.1", "google.com"]:
        out = run_ps(
            f"$p = Test-Connection -ComputerName '{host}' -Count 3 -EA SilentlyContinue; "
            f"if($p) {{ [math]::Round(($p | Measure-Object -Property Latency -Average).Average, 1) }} "
            f"else {{ 'FAIL' }}",
            timeout=15
        )[0]
        results[f"ping_{host}"] = f"{out.strip()} ms" if out and out.strip() != "FAIL" else "TIMEOUT"

    # Public IP
    try:
        req = urllib.request.urlopen("https://api.ipify.org?format=json", timeout=5)
        ip_data = json.loads(req.read().decode())
        results["public_ip"] = ip_data.get("ip", "N/A")
    except Exception:
        results["public_ip"] = "N/A"

    # Network adapter
    adapter_out = run_ps(
        "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
        "Select -First 1 | ForEach-Object { $_.Name + '|' + $_.LinkSpeed + '|' + $_.InterfaceDescription }",
        timeout=10
    )[0]
    if adapter_out and "|" in adapter_out:
        parts = adapter_out.strip().split("|")
        results["adapter_name"] = parts[0]
        results["link_speed"] = parts[1]
        results["adapter_desc"] = parts[2] if len(parts) > 2 else ""
    else:
        results["adapter_name"] = "N/A"
        results["link_speed"] = "N/A"
        results["adapter_desc"] = ""

    return results


# ─── Startup Manager ────────────────────────────────────────────────────────────

def get_startup_items():
    """Get list of startup items. Returns list of dicts."""
    out = run_ps(
        "Get-CimInstance Win32_StartupCommand -EA SilentlyContinue | "
        "Select-Object Name, Command, Location, User | ConvertTo-Json -Depth 2",
        timeout=15
    )[0]
    items = []
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                items.append({
                    "name": item.get("Name", "?"),
                    "command": item.get("Command", "?"),
                    "location": item.get("Location", "?"),
                    "user": item.get("User", "?"),
                })
        except (json.JSONDecodeError, TypeError):
            pass
    # Also get Task Scheduler startup tasks
    sched_out = run_ps(
        "Get-ScheduledTask -EA SilentlyContinue | Where-Object { "
        "$_.Settings.StartWhenAvailable -eq $true -or $_.Triggers | "
        "Where-Object { $_ -is [CimInstance] -and $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' } "
        "} | Select TaskName, State, TaskPath | ConvertTo-Json -Depth 2",
        timeout=15
    )[0]
    if sched_out:
        try:
            sdata = json.loads(sched_out)
            if isinstance(sdata, dict):
                sdata = [sdata]
            for s in sdata:
                items.append({
                    "name": s.get("TaskName", "?"),
                    "command": "Scheduled Task",
                    "location": s.get("TaskPath", "?"),
                    "user": s.get("State", "?"),
                })
        except (json.JSONDecodeError, TypeError):
            pass
    return items


# ─── Disk Space Analyzer ────────────────────────────────────────────────────────

def get_disk_space_breakdown():
    """Get disk space breakdown per drive. Returns list of dicts."""
    out = run_ps(
        "Get-Volume | Where-Object {$_.DriveLetter -and $_.DriveType -eq 'Fixed'} | "
        "ForEach-Object { $_.DriveLetter + '|' + [math]::Round($_.Size/1GB,1) + '|' + "
        "[math]::Round($_.SizeRemaining/1GB,1) + '|' + $_.FileSystemLabel } | Out-String",
        timeout=15
    )[0]
    drives = []
    if out:
        for line in out.strip().split("\n"):
            line = line.strip()
            if "|" in line:
                parts = line.split("|")
                if len(parts) >= 3:
                    letter = parts[0]
                    total = float(parts[1])
                    free = float(parts[2])
                    label = parts[3] if len(parts) > 3 else ""
                    used = total - free
                    pct = (used / total * 100) if total > 0 else 0
                    drives.append({
                        "letter": letter,
                        "label": label,
                        "total_gb": total,
                        "used_gb": used,
                        "free_gb": free,
                        "pct_used": round(pct, 1),
                    })
    return drives


# ─── Live System Stats ───────────────────────────────────────────────────────────

def get_live_system_stats():
    """Get current CPU, RAM, GPU usage. Returns dict."""
    out = run_ps(
        "$cpu = (Get-CimInstance Win32_Processor -EA SilentlyContinue).LoadPercentage; "
        "$os = Get-CimInstance Win32_OperatingSystem -EA SilentlyContinue; "
        "$totalMem = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1); "
        "$freeMem = [math]::Round($os.FreePhysicalMemory / 1MB, 1); "
        "$usedMem = $totalMem - $freeMem; "
        "$pctMem = [math]::Round(($usedMem / $totalMem) * 100, 0); "
        "Write-Output \"$cpu|$totalMem|$usedMem|$pctMem\"",
        timeout=10
    )[0]
    stats = {"cpu_pct": 0, "ram_total": 0, "ram_used": 0, "ram_pct": 0}
    if out and "|" in out:
        parts = out.strip().split("|")
        try:
            stats["cpu_pct"] = int(float(parts[0]))
            stats["ram_total"] = float(parts[1])
            stats["ram_used"] = float(parts[2])
            stats["ram_pct"] = int(float(parts[3]))
        except (ValueError, IndexError):
            pass
    # GPU usage via nvidia-smi if available
    try:
        gpu_out, rc = run_cmd("nvidia-smi --query-gpu=utilization.gpu,temperature.gpu --format=csv,noheader,nounits", timeout=5)
        if rc == 0 and gpu_out:
            gparts = gpu_out.strip().split(",")
            stats["gpu_pct"] = int(gparts[0].strip())
            stats["gpu_temp"] = int(gparts[1].strip())
    except Exception:
        stats["gpu_pct"] = 0
        stats["gpu_temp"] = 0
    return stats


# ─── Game Detection ──────────────────────────────────────────────────────────────

def get_installed_games():
    """Detect installed games from common launchers. Returns list of dicts."""
    games = []
    # Steam games
    steam_out = run_ps(
        "$steamPath = (Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\WOW6432Node\\Valve\\Steam' -EA SilentlyContinue).InstallPath; "
        "if($steamPath) { "
        "  $libFile = Join-Path $steamPath 'steamapps\\libraryfolders.vdf'; "
        "  if(Test-Path $libFile) { "
        "    $libs = @($steamPath); "
        "    $content = Get-Content $libFile -Raw; "
        "    [regex]::Matches($content, '\"path\"\\s+\"(.+?)\"') | ForEach-Object { $libs += $_.Groups[1].Value }; "
        "    foreach($lib in $libs) { "
        "      $acfDir = Join-Path $lib 'steamapps'; "
        "      if(Test-Path $acfDir) { "
        "        Get-ChildItem \"$acfDir\\appmanifest_*.acf\" -EA SilentlyContinue | ForEach-Object { "
        "          $c = Get-Content $_.FullName -Raw; "
        "          if($c -match '\"name\"\\s+\"(.+?)\"') { Write-Output $Matches[1] } "
        "        } "
        "      } "
        "    } "
        "  } "
        "}",
        timeout=15
    )[0]
    if steam_out:
        for line in steam_out.strip().split("\n"):
            name = line.strip()
            if name and name not in ("Steamworks Common Redistributables", "Steam Linux Runtime"):
                games.append({"name": name, "launcher": "Steam"})

    # Epic Games
    epic_out = run_ps(
        "$epicPath = 'C:\\ProgramData\\Epic\\EpicGamesLauncher\\Data\\Manifests'; "
        "if(Test-Path $epicPath) { "
        "  Get-ChildItem $epicPath -Filter '*.item' -EA SilentlyContinue | ForEach-Object { "
        "    $j = Get-Content $_.FullName -Raw | ConvertFrom-Json -EA SilentlyContinue; "
        "    if($j.DisplayName) { Write-Output $j.DisplayName } "
        "  } "
        "}",
        timeout=10
    )[0]
    if epic_out:
        for line in epic_out.strip().split("\n"):
            name = line.strip()
            if name:
                games.append({"name": name, "launcher": "Epic"})

    return games


# ─── Scheduled Maintenance ───────────────────────────────────────────────────────

def schedule_maintenance(interval="weekly"):
    """Create a Windows Task Scheduler task for weekly maintenance. Returns (success, message)."""
    script_path = os.path.abspath(sys.argv[0])
    day = "SUN" if interval == "weekly" else "*"
    out, rc = run_ps(
        f"$action = New-ScheduledTaskAction -Execute 'python' -Argument '\"{script_path}\" --auto-scan'; "
        f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3AM; "
        f"$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName 'PCMaintenanceSuiteScan' -Action $action "
        f"-Trigger $trigger -Settings $settings -Description 'Weekly PC maintenance scan' "
        f"-RunLevel Highest -Force",
        timeout=15
    )
    if rc == 0:
        return True, f"Scheduled weekly scan (Sundays 3 AM)"
    return False, out.strip() if out else "Failed to schedule task"

def remove_scheduled_maintenance():
    """Remove the scheduled maintenance task."""
    out, rc = run_ps("Unregister-ScheduledTask -TaskName 'PCMaintenanceSuiteScan' -Confirm:$false -EA SilentlyContinue", timeout=10)
    return rc == 0




RECOMMENDED_SOFTWARE = {
    "🎮 Gaming & GPU": [
        {"name": "MSI Afterburner", "desc": "GPU overclocking, fan curves, voltage control",
         "url": "https://www.msi.com/Landing/afterburner/graphics-cards",
         "winget": "Guru3D.Afterburner"},
        {"name": "RTSS (RivaTuner)", "desc": "Frame limiter + on-screen FPS/temp display",
         "url": "https://www.guru3d.com/download/rtss-rivatuner-statistics-server-download/",
         "winget": None},
        {"name": "NVIDIA Profile Inspector", "desc": "Advanced NVIDIA driver profile tweaks",
         "url": "https://github.com/Orbmu2k/nvidiaProfileInspector/releases",
         "winget": None},
        {"name": "SpecialK", "desc": "Universal game optimizer — HDR, frame pacing, DLSS injection",
         "url": "https://www.special-k.info/",
         "winget": None},
        {"name": "Borderless Gaming", "desc": "Run any game in borderless fullscreen mode",
         "url": "https://github.com/Codeusa/Borderless-Gaming/releases",
         "winget": "Codeusa.BorderlessGaming"},
        {"name": "NVIDIA App", "desc": "Official NVIDIA driver manager + Game Ready updates",
         "url": "https://www.nvidia.com/en-us/software/nvidia-app/",
         "winget": None},
    ],
    "🔧 Garry's Mod": [
        {"name": "GMad Tool", "desc": "Extract & create .gma addon files from command line",
         "url": "https://wiki.facepunch.com/gmod/Extracting_Addon_Files",
         "winget": None},
        {"name": "Crowbar", "desc": "Source engine model decompiler/viewer",
         "url": "https://steamcommunity.com/groups/CrowbarTool",
         "winget": None},
        {"name": "VTFEdit", "desc": "Edit/create Valve Texture Format files",
         "url": "https://developer.valvesoftware.com/wiki/VTFEdit",
         "winget": None},
        {"name": "Source SDK Base 2013 MP", "desc": "Required for many GMod tools and content creation",
         "url": "steam://install/243750",
         "winget": None},
        {"name": "GMod Workshop Collection Helper", "desc": "Bulk subscribe/manage workshop collections",
         "url": "https://steamcommunity.com/sharedfiles/filedetails/?id=104606562",
         "winget": None},
    ],
    "🖥 System Utilities": [
        {"name": "HWiNFO64", "desc": "Detailed hardware sensors — temps, voltages, clocks",
         "url": "https://www.hwinfo.com/download/",
         "winget": "REALiX.HWiNFO"},
        {"name": "CrystalDiskInfo", "desc": "SSD/HDD health monitoring with S.M.A.R.T. data",
         "url": "https://crystalmark.info/en/software/crystaldiskinfo/",
         "winget": "CrystalDewWorld.CrystalDiskInfo"},
        {"name": "WizTree", "desc": "Ultra-fast disk space analyzer (reads MFT directly)",
         "url": "https://diskanalyzer.com/",
         "winget": "AntibodySoftware.WizTree"},
        {"name": "Everything Search", "desc": "Instant file search for your entire PC",
         "url": "https://www.voidtools.com/",
         "winget": "voidtools.Everything"},
        {"name": "7-Zip", "desc": "Free archive manager — ZIP, RAR, 7z, TAR, and more",
         "url": "https://www.7-zip.org/",
         "winget": "7zip.7zip"},
        {"name": "Notepad++", "desc": "Fast, lightweight code/text editor with syntax highlighting",
         "url": "https://notepad-plus-plus.org/",
         "winget": "Notepad++.Notepad++"},
    ],
    "⚡ Performance & Tweaks": [
        {"name": "Process Lasso", "desc": "Automated CPU priority, affinity, power plan optimization",
         "url": "https://bitsum.com/",
         "winget": "Bitsum.ProcessLasso"},
        {"name": "QuickCPU", "desc": "CPU performance tuning — core parking, frequency scaling",
         "url": "https://coderbag.com/product/quickcpu",
         "winget": None},
        {"name": "O&O ShutUp10++", "desc": "Disable Windows telemetry, Cortana, Bing, and bloat",
         "url": "https://www.oo-software.com/en/shutup10",
         "winget": None},
        {"name": "PowerToys", "desc": "Microsoft power tools — FancyZones, PowerRename, ColorPicker",
         "url": "https://learn.microsoft.com/en-us/windows/powertoys/",
         "winget": "Microsoft.PowerToys"},
        {"name": "BloatyNosy", "desc": "Remove Windows 11 bloatware and preinstalled apps",
         "url": "https://github.com/builtbybel/BloatyNosy",
         "winget": None},
        {"name": "Autoruns", "desc": "Sysinternals tool — see everything that auto-starts on boot",
         "url": "https://learn.microsoft.com/en-us/sysinternals/downloads/autoruns",
         "winget": "Microsoft.Sysinternals.Autoruns"},
    ],
    "🌐 Network": [
        {"name": "TCP Optimizer", "desc": "Windows network stack tuning for lower latency",
         "url": "https://www.speedguide.net/downloads.php",
         "winget": None},
        {"name": "Wireshark", "desc": "Network packet capture and protocol analysis",
         "url": "https://www.wireshark.org/",
         "winget": "WiresharkFoundation.Wireshark"},
        {"name": "Angry IP Scanner", "desc": "Fast network IP/port scanner",
         "url": "https://angryip.org/",
         "winget": "angryip.ipscan"},
    ],
    "🔒 Security": [
        {"name": "Malwarebytes", "desc": "Industry-standard anti-malware scanner and removal",
         "url": "https://www.malwarebytes.com/",
         "winget": "Malwarebytes.Malwarebytes"},
        {"name": "Bitwarden", "desc": "Free open-source password manager",
         "url": "https://bitwarden.com/",
         "winget": "Bitwarden.Bitwarden"},
        {"name": "simplewall", "desc": "Lightweight Windows firewall — block apps from phoning home",
         "url": "https://github.com/henrypp/simplewall",
         "winget": "Henry++.simplewall"},
    ],
}


# ─── Helpers ────────────────────────────────────────────────────────────────────

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_ps(command, timeout=120):
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return result.stdout.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "TIMEOUT", -1
    except Exception as e:
        return str(e), -1


def run_ps_json(command, timeout=120):
    out, _ = run_ps(command, timeout)
    if not out or out == "TIMEOUT":
        return []
    try:
        data = json.loads(out)
        return [data] if isinstance(data, dict) else data
    except json.JSONDecodeError:
        return []


def run_cmd(command, timeout=120):
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, shell=True,
            creationflags=0x08000000,
        )
        return result.stdout.strip(), result.returncode
    except Exception as e:
        return str(e), -1


def run_cmd_streamed(command, callback, timeout=600):
    """Run a command and stream stdout lines to callback in real-time."""
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, shell=True, creationflags=0x08000000, bufsize=1,
        )
        lines = []
        import selectors
        deadline = time.time() + timeout
        while True:
            if time.time() > deadline:
                proc.kill()
                callback("[TIMEOUT] Operation exceeded time limit.", "error")
                break
            line = proc.stdout.readline()
            if line:
                stripped = line.rstrip()
                if stripped:
                    lines.append(stripped)
                    callback(stripped, "")
            elif proc.poll() is not None:
                break
        proc.stdout.close()
        return "\n".join(lines), proc.returncode or 0
    except Exception as e:
        callback(f"Error: {e}", "error")
        return str(e), -1


def parse_driver_date(raw):
    if not raw:
        return None
    if isinstance(raw, str):
        m = re.search(r'/Date\((\d+)\)', raw)
        if m:
            return datetime.datetime.fromtimestamp(int(m.group(1)) / 1000)
        m = re.match(r'(\d{4})(\d{2})(\d{2})', raw)
        if m:
            try:
                return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    return None


# ─── Theme ──────────────────────────────────────────────────────────────────────

THEME = {
    "bg":           "#1a1b26",
    "bg_secondary": "#24283b",
    "bg_card":      "#1f2335",
    "bg_input":     "#292e42",
    "accent":       "#7aa2f7",
    "accent_hover": "#89b4fa",
    "success":      "#9ece6a",
    "warning":      "#e0af68",
    "error":        "#f7768e",
    "text":         "#c0caf5",
    "text_dim":     "#565f89",
    "text_bright":  "#ffffff",
    "border":       "#3b4261",
    "progress_bg":  "#292e42",
    "progress_fg":  "#7aa2f7",
    "tag_ok":       "#9ece6a",
    "tag_warn":     "#e0af68",
    "tag_error":    "#f7768e",
    "tag_info":     "#7dcfff",
}


# ─── Main Application ──────────────────────────────────────────────────────────

class DriverUpdaterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PC Driver Updater")
        self.root.geometry("1100x780")
        self.root.minsize(900, 600)
        self.root.configure(bg=THEME["bg"])

        # State
        self.drivers = []
        self.problems = []
        self.wu_data = {}
        self.winget_updates = []
        self.disks = []
        self.nvidia_result = None
        self.sys_info = None
        self.bios_info = None
        self.amd_info = None
        self.vcpp_results = []
        self.dotnet_runtimes = []
        self.dx_info = {}
        self.temp_sizes = {}
        self.is_scanning = False
        self.is_fixing = False

        self._setup_styles()
        self._build_ui()
        self._check_admin()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        """Clean up Turbo Mode and reboot suppression before exiting."""
        if self.turbo_active:
            try:
                self._disable_turbo()
            except Exception:
                pass
        try:
            self._unsuppress_reboot()
        except Exception:
            pass
        self.root.destroy()

    # ── Styles ──────────────────────────────────────────────────────────

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=THEME["bg"], foreground=THEME["text"])

        style.configure("Card.TFrame", background=THEME["bg_card"],
                         borderwidth=1, relief="solid")
        style.configure("Main.TFrame", background=THEME["bg"])
        style.configure("Secondary.TFrame", background=THEME["bg_secondary"])

        style.configure("Title.TLabel", background=THEME["bg"],
                         foreground=THEME["text_bright"], font=("Segoe UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=THEME["bg"],
                         foreground=THEME["text_dim"], font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=THEME["bg_card"],
                         foreground=THEME["text_bright"], font=("Segoe UI", 12, "bold"))
        style.configure("Stat.TLabel", background=THEME["bg_card"],
                         foreground=THEME["accent"], font=("Segoe UI", 28, "bold"))
        style.configure("StatLabel.TLabel", background=THEME["bg_card"],
                         foreground=THEME["text_dim"], font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=THEME["bg"],
                         foreground=THEME["text_dim"], font=("Segoe UI", 9))
        style.configure("Admin.TLabel", background=THEME["bg"],
                         foreground=THEME["success"], font=("Segoe UI", 9, "bold"))
        style.configure("NoAdmin.TLabel", background=THEME["bg"],
                         foreground=THEME["warning"], font=("Segoe UI", 9, "bold"))

        style.configure("Accent.TButton", background=THEME["accent"],
                         foreground=THEME["bg"], font=("Segoe UI", 10, "bold"),
                         padding=(20, 10))
        style.map("Accent.TButton",
                   background=[("active", THEME["accent_hover"]), ("disabled", THEME["border"])],
                   foreground=[("disabled", THEME["text_dim"])])

        style.configure("Secondary.TButton", background=THEME["bg_input"],
                         foreground=THEME["text"], font=("Segoe UI", 10),
                         padding=(16, 8))
        style.map("Secondary.TButton",
                   background=[("active", THEME["border"]), ("disabled", THEME["bg_secondary"])],
                   foreground=[("disabled", THEME["text_dim"])])

        style.configure("Danger.TButton", background=THEME["error"],
                         foreground=THEME["bg"], font=("Segoe UI", 10, "bold"),
                         padding=(16, 8))
        style.map("Danger.TButton",
                   background=[("active", "#ff9e9e"), ("disabled", THEME["border"])])

        style.configure("Custom.Horizontal.TProgressbar",
                         background=THEME["accent"], troughcolor=THEME["progress_bg"],
                         borderwidth=0, lightcolor=THEME["accent"],
                         darkcolor=THEME["accent"])

        style.configure("Treeview", background=THEME["bg_card"],
                         foreground=THEME["text"], fieldbackground=THEME["bg_card"],
                         font=("Segoe UI", 9), rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background=THEME["bg_secondary"],
                         foreground=THEME["text_bright"], font=("Segoe UI", 9, "bold"),
                         borderwidth=0)
        style.map("Treeview",
                   background=[("selected", THEME["accent"])],
                   foreground=[("selected", THEME["bg"])])

        style.configure("TNotebook", background=THEME["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=THEME["bg_secondary"],
                         foreground=THEME["text_dim"], font=("Segoe UI", 10),
                         padding=(16, 8))
        style.map("TNotebook.Tab",
                   background=[("selected", THEME["bg_card"])],
                   foreground=[("selected", THEME["text_bright"])])

    # ── UI Build ────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        header_frame = ttk.Frame(self.root, style="Main.TFrame")
        header_frame.pack(fill="x", padx=24, pady=(18, 0))

        title_row = ttk.Frame(header_frame, style="Main.TFrame")
        title_row.pack(fill="x")

        ttk.Label(title_row, text="⚙  PC Driver Updater", style="Title.TLabel").pack(side="left")

        self.admin_label = ttk.Label(title_row, text="", style="Admin.TLabel")
        self.admin_label.pack(side="right", padx=(0, 8))

        ttk.Label(header_frame, text="Scan, analyze, and update all drivers & software on your system",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        # Button bar
        btn_frame = ttk.Frame(self.root, style="Main.TFrame")
        btn_frame.pack(fill="x", padx=24, pady=(14, 0))

        self.scan_btn = ttk.Button(btn_frame, text="🔎  Scan System",
                                    style="Accent.TButton", command=self._start_scan)
        self.scan_btn.pack(side="left", padx=(0, 8))

        self.fix_btn = ttk.Button(btn_frame, text="🔧  Fix Problem Devices",
                                   style="Secondary.TButton", command=self._start_fix_problems,
                                   state="disabled")
        self.fix_btn.pack(side="left", padx=(0, 8))

        self.update_drivers_btn = ttk.Button(btn_frame, text="🪟  Windows Update",
                                              style="Secondary.TButton",
                                              command=self._start_windows_update,
                                              state="disabled")
        self.update_drivers_btn.pack(side="left", padx=(0, 8))

        self.winget_btn = ttk.Button(btn_frame, text="📦  Update Software",
                                      style="Secondary.TButton",
                                      command=self._start_winget_update,
                                      state="disabled")
        self.winget_btn.pack(side="left", padx=(0, 8))

        self.update_all_btn = ttk.Button(btn_frame, text="🚀  Update Everything",
                                          style="Danger.TButton",
                                          command=self._start_update_all,
                                          state="disabled")
        self.update_all_btn.pack(side="right")

        # Turbo Mode toggle
        self.turbo_active = False
        self.turbo_btn = ttk.Button(btn_frame, text="⚡  Turbo Mode",
                                     style="Secondary.TButton",
                                     command=self._toggle_turbo)
        self.turbo_btn.pack(side="right", padx=(0, 8))

        # Second button row — Power Plan, Defender, Export, Schedule
        btn_frame2 = ttk.Frame(self.root, style="Main.TFrame")
        btn_frame2.pack(fill="x", padx=24, pady=(6, 0))

        self.power_mode = "balanced"
        self.power_btn = ttk.Button(btn_frame2, text="\ud83d\udd0b  Gaming Mode",
                                     style="Secondary.TButton",
                                     command=self._toggle_power_plan)
        self.power_btn.pack(side="left", padx=(0, 8))

        self.defender_btn = ttk.Button(btn_frame2, text="\ud83d\udee1  Defender Scan",
                                        style="Secondary.TButton",
                                        command=self._start_defender_scan)
        self.defender_btn.pack(side="left", padx=(0, 8))

        self.export_btn = ttk.Button(btn_frame2, text="\ud83d\udccb  Export Report",
                                      style="Secondary.TButton",
                                      command=self._export_health_report,
                                      state="disabled")
        self.export_btn.pack(side="left", padx=(0, 8))

        self.schedule_active = False
        self.schedule_btn = ttk.Button(btn_frame2, text="\ud83d\udcc5  Schedule Weekly",
                                        style="Secondary.TButton",
                                        command=self._toggle_schedule)
        self.schedule_btn.pack(side="left", padx=(0, 8))

        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(self.root, variable=self.progress_var,
                                         maximum=100, style="Custom.Horizontal.TProgressbar")
        self.progress.pack(fill="x", padx=24, pady=(12, 0))

        self.status_label = ttk.Label(self.root, text="Ready — Click 'Scan System' to begin",
                                       style="Status.TLabel")
        self.status_label.pack(anchor="w", padx=26, pady=(4, 0))

        # Stats cards
        stats_frame = ttk.Frame(self.root, style="Main.TFrame")
        stats_frame.pack(fill="x", padx=24, pady=(12, 0))

        self.stat_vars = {}
        stat_defs = [
            ("total_drivers", "Total Drivers", "0"),
            ("problem_devices", "Problem Devices", "—"),
            ("old_drivers", "Old (2yr+)", "—"),
            ("wu_updates", "Windows Updates", "—"),
            ("winget_updates", "Software Updates", "—"),
            ("disk_health", "Disks", "—"),
            ("gpu_driver", "GPU Driver", "—"),
        ]
        for i, (key, label, default) in enumerate(stat_defs):
            card = tk.Frame(stats_frame, bg=THEME["bg_card"], highlightbackground=THEME["border"],
                            highlightthickness=1, padx=14, pady=10)
            card.pack(side="left", fill="both", expand=True, padx=(0 if i == 0 else 4, 0))

            var = tk.StringVar(value=default)
            self.stat_vars[key] = var
            tk.Label(card, textvariable=var, bg=THEME["bg_card"],
                     fg=THEME["accent"], font=("Segoe UI", 24, "bold")).pack()
            tk.Label(card, text=label, bg=THEME["bg_card"],
                     fg=THEME["text_dim"], font=("Segoe UI", 9)).pack()

        # Tabbed content
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=24, pady=(12, 18))

        # Tab 1: All Drivers
        self.drivers_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.drivers_frame, text="  All Drivers  ")
        self._build_drivers_tab()

        # Tab 2: Problem Devices
        self.problems_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.problems_frame, text="  Problem Devices  ")
        self._build_problems_tab()

        # Tab 3: Updates Available
        self.updates_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.updates_frame, text="  Updates Available  ")
        self._build_updates_tab()

        # Tab 4: Log
        self.log_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.log_frame, text="  Activity Log  ")
        self._build_log_tab()

        # Tab 5: PC Health
        self.health_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.health_frame, text="  🩺 PC Health  ")
        self._build_health_tab()

        # Tab 6: Recommended
        self.recommended_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.recommended_frame, text="  ⭐ Recommended  ")
        self._build_recommended_tab()

        # Tab 7: Network Diagnostics
        self.network_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.network_frame, text="  \ud83c\udf10 Network  ")
        self._build_network_tab()

        # Tab 8: Startup Manager
        self.startup_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.startup_frame, text="  \ud83d\ude80 Startup  ")
        self._build_startup_tab()

        # Tab 9: Tools (Disk Analyzer, Games)
        self.tools_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.tools_frame, text="  \ud83d\udee0 Tools  ")
        self._build_tools_tab()

        # Tab 10: System Monitor
        self.monitor_frame = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(self.monitor_frame, text="  \ud83d\udcca Monitor  ")
        self._build_monitor_tab()

    def _build_drivers_tab(self):
        # Search bar
        search_frame = ttk.Frame(self.drivers_frame, style="Main.TFrame")
        search_frame.pack(fill="x", pady=(8, 4))

        tk.Label(search_frame, text="🔍", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Segoe UI", 11)).pack(side="left", padx=(4, 4))

        self.driver_search_var = tk.StringVar()
        self.driver_search_var.trace_add("write", self._filter_drivers)
        search_entry = tk.Entry(search_frame, textvariable=self.driver_search_var,
                                bg=THEME["bg_input"], fg=THEME["text"],
                                insertbackground=THEME["text"], font=("Segoe UI", 10),
                                relief="flat", highlightthickness=1,
                                highlightcolor=THEME["accent"],
                                highlightbackground=THEME["border"])
        search_entry.pack(side="left", fill="x", expand=True, ipady=5)

        # Filter by class
        tk.Label(search_frame, text="  Class:", bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Segoe UI", 9)).pack(side="left", padx=(12, 4))
        self.class_filter_var = tk.StringVar(value="All")
        self.class_filter = ttk.Combobox(search_frame, textvariable=self.class_filter_var,
                                          state="readonly", width=18)
        self.class_filter["values"] = ["All"]
        self.class_filter.pack(side="left")
        self.class_filter.bind("<<ComboboxSelected>>", self._filter_drivers)

        # Treeview — added INF and Available columns
        cols = ("name", "class", "version", "available", "date", "age", "manufacturer", "signed", "inf")
        self.driver_tree = ttk.Treeview(self.drivers_frame, columns=cols,
                                         show="headings", selectmode="browse")

        col_config = [
            ("name",         "Device Name",      240),
            ("class",        "Class",              90),
            ("version",      "Installed Ver.",    110),
            ("available",    "Available Ver.",    110),
            ("date",         "Driver Date",        85),
            ("age",          "Age",                55),
            ("manufacturer", "Manufacturer",      130),
            ("signed",       "Signed",             45),
            ("inf",          "INF File",          100),
        ]
        for col_id, heading, width in col_config:
            self.driver_tree.heading(col_id, text=heading,
                                     command=lambda c=col_id: self._sort_tree(self.driver_tree, c))
            self.driver_tree.column(col_id, width=width, minwidth=35)

        # Tags for coloring rows
        self.driver_tree.tag_configure("old", foreground=THEME["warning"])
        self.driver_tree.tag_configure("very_old", foreground=THEME["error"])
        self.driver_tree.tag_configure("ok", foreground=THEME["text"])
        self.driver_tree.tag_configure("unsigned", foreground=THEME["error"])
        self.driver_tree.tag_configure("has_update", foreground=THEME["tag_info"])

        scrollbar = ttk.Scrollbar(self.drivers_frame, orient="vertical",
                                   command=self.driver_tree.yview)
        self.driver_tree.configure(yscrollcommand=scrollbar.set)

        self.driver_tree.pack(side="left", fill="both", expand=True, pady=(4, 0))
        scrollbar.pack(side="right", fill="y", pady=(4, 0))

        # Right-click context menu for drivers
        self.driver_ctx_menu = tk.Menu(self.root, tearoff=0,
            bg=THEME["bg_secondary"], fg=THEME["text"],
            activebackground=THEME["accent"], activeforeground=THEME["bg"],
            font=("Segoe UI", 9))
        self.driver_ctx_menu.add_command(label="🔍  Search for Update Online",
                                          command=self._driver_search_online)
        self.driver_ctx_menu.add_command(label="📂  Open INF File Location",
                                          command=self._driver_open_file_location)
        self.driver_ctx_menu.add_separator()
        self.driver_ctx_menu.add_command(label="🖥️  Open Device Manager",
                                          command=self._open_device_manager)
        self.driver_tree.bind("<Button-3>", self._driver_right_click)

    def _build_problems_tab(self):
        cols = ("status", "class", "name", "instance_id")
        self.problem_tree = ttk.Treeview(self.problems_frame, columns=cols,
                                          show="headings", selectmode="browse")

        for col_id, heading, width in [
            ("status", "Status", 80), ("class", "Class", 120),
            ("name", "Device Name", 350), ("instance_id", "Instance ID", 400),
        ]:
            self.problem_tree.heading(col_id, text=heading)
            self.problem_tree.column(col_id, width=width, minwidth=40)

        self.problem_tree.tag_configure("error", foreground=THEME["error"])
        self.problem_tree.tag_configure("unknown", foreground=THEME["warning"])
        self.problem_tree.tag_configure("degraded", foreground=THEME["warning"])

        scrollbar = ttk.Scrollbar(self.problems_frame, orient="vertical",
                                   command=self.problem_tree.yview)
        self.problem_tree.configure(yscrollcommand=scrollbar.set)
        self.problem_tree.pack(side="left", fill="both", expand=True, pady=(8, 0))
        scrollbar.pack(side="right", fill="y", pady=(8, 0))

        # Right-click context menu for problems
        self.problem_ctx_menu = tk.Menu(self.root, tearoff=0,
            bg=THEME["bg_secondary"], fg=THEME["text"],
            activebackground=THEME["accent"], activeforeground=THEME["bg"],
            font=("Segoe UI", 9))
        self.problem_ctx_menu.add_command(label="🔍  Search Fix Online",
                                           command=self._problem_search_online)
        self.problem_ctx_menu.add_command(label="🖥️  Open Device Manager",
                                           command=self._open_device_manager)
        self.problem_tree.bind("<Button-3>", self._problem_right_click)

    def _build_updates_tab(self):
        cols = ("source", "name", "current", "available")
        self.update_tree = ttk.Treeview(self.updates_frame, columns=cols,
                                         show="headings", selectmode="browse")

        for col_id, heading, width in [
            ("source", "Source", 100), ("name", "Name", 420),
            ("current", "Current", 140), ("available", "Available", 140),
        ]:
            self.update_tree.heading(col_id, text=heading)
            self.update_tree.column(col_id, width=width, minwidth=40)

        self.update_tree.tag_configure("driver", foreground=THEME["tag_info"])
        self.update_tree.tag_configure("software", foreground=THEME["text"])
        self.update_tree.tag_configure("system", foreground=THEME["warning"])

        scrollbar = ttk.Scrollbar(self.updates_frame, orient="vertical",
                                   command=self.update_tree.yview)
        self.update_tree.configure(yscrollcommand=scrollbar.set)
        self.update_tree.pack(side="left", fill="both", expand=True, pady=(8, 0))
        scrollbar.pack(side="right", fill="y", pady=(8, 0))

        # Right-click context menu for updates
        self.update_ctx_menu = tk.Menu(self.root, tearoff=0,
            bg=THEME["bg_secondary"], fg=THEME["text"],
            activebackground=THEME["accent"], activeforeground=THEME["bg"],
            font=("Segoe UI", 9))
        self.update_ctx_menu.add_command(label="🌐  Visit Update Page",
                                          command=self._update_visit_page)
        self.update_ctx_menu.add_command(label="🔍  Search Online",
                                          command=self._update_search_online)
        self.update_tree.bind("<Button-3>", self._update_right_click)

    def _build_log_tab(self):
        self.log_text = scrolledtext.ScrolledText(
            self.log_frame, bg=THEME["bg_card"], fg=THEME["text"],
            font=("Cascadia Code", 9), relief="flat", wrap="word",
            insertbackground=THEME["text"], highlightthickness=0,
            selectbackground=THEME["accent"], selectforeground=THEME["bg"],
        )
        self.log_text.pack(fill="both", expand=True, pady=(8, 0))

        self.log_text.tag_configure("header", foreground=THEME["accent"], font=("Cascadia Code", 10, "bold"))
        self.log_text.tag_configure("success", foreground=THEME["success"])
        self.log_text.tag_configure("warning", foreground=THEME["warning"])
        self.log_text.tag_configure("error", foreground=THEME["error"])
        self.log_text.tag_configure("info", foreground=THEME["tag_info"])
        self.log_text.tag_configure("dim", foreground=THEME["text_dim"])

    def _build_health_tab(self):
        """Build the PC Health diagnostics tab."""
        self.health_text = scrolledtext.ScrolledText(
            self.health_frame, bg=THEME["bg_card"], fg=THEME["text"],
            font=("Cascadia Code", 9), relief="flat", wrap="word",
            insertbackground=THEME["text"], highlightthickness=0,
            selectbackground=THEME["accent"], selectforeground=THEME["bg"],
        )
        self.health_text.pack(fill="both", expand=True, pady=(8, 0))

        self.health_text.tag_configure("header", foreground=THEME["accent"], font=("Cascadia Code", 11, "bold"))
        self.health_text.tag_configure("section", foreground="#bb9af7", font=("Cascadia Code", 10, "bold"))
        self.health_text.tag_configure("good", foreground=THEME["success"])
        self.health_text.tag_configure("warn", foreground=THEME["warning"])
        self.health_text.tag_configure("bad", foreground=THEME["error"])
        self.health_text.tag_configure("info", foreground=THEME["tag_info"])
        self.health_text.tag_configure("tip", foreground="#7dcfff", font=("Cascadia Code", 9, "italic"))
        self.health_text.tag_configure("dim", foreground=THEME["text_dim"])

        self.health_text.insert("end", "Run a scan to generate your PC Health Report\n", "dim")
        self.health_text.configure(state="disabled")

    def _health_log(self, msg, tag=""):
        """Append text to the health tab (thread-safe via root.after)."""
        def _do():
            self.health_text.configure(state="normal")
            self.health_text.insert("end", msg + "\n", tag if tag else ())
            self.health_text.see("end")
            self.health_text.configure(state="disabled")
        self.root.after(0, _do)

    def _build_recommended_tab(self):
        """Build the Recommended Software tab with clickable cards."""
        # Canvas + scrollbar for scrollable area
        canvas = tk.Canvas(self.recommended_frame, bg=THEME["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(self.recommended_frame, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=THEME["bg"])

        scroll_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")

        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True, pady=(8, 0))

        # Header
        tk.Label(scroll_frame, text="⭐  Recommended Software & Tools",
                 bg=THEME["bg"], fg=THEME["accent"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(scroll_frame, text="Curated picks for gaming, GMod, system maintenance, and performance",
                 bg=THEME["bg"], fg=THEME["text_dim"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 12))

        for category, items in RECOMMENDED_SOFTWARE.items():
            # Category header
            cat_frame = tk.Frame(scroll_frame, bg=THEME["bg"])
            cat_frame.pack(fill="x", padx=16, pady=(12, 4))
            tk.Label(cat_frame, text=category, bg=THEME["bg"], fg="#bb9af7",
                     font=("Segoe UI", 12, "bold")).pack(anchor="w")

            for sw in items:
                # Card
                card = tk.Frame(scroll_frame, bg=THEME["bg_card"],
                                highlightbackground=THEME["border"], highlightthickness=1)
                card.pack(fill="x", padx=20, pady=3)

                inner = tk.Frame(card, bg=THEME["bg_card"])
                inner.pack(fill="x", padx=12, pady=8)

                # Left side: name + description
                left = tk.Frame(inner, bg=THEME["bg_card"])
                left.pack(side="left", fill="x", expand=True)

                tk.Label(left, text=sw["name"], bg=THEME["bg_card"],
                         fg=THEME["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
                tk.Label(left, text=sw["desc"], bg=THEME["bg_card"],
                         fg=THEME["text_dim"], font=("Segoe UI", 8)).pack(anchor="w")

                # Right side: buttons
                right = tk.Frame(inner, bg=THEME["bg_card"])
                right.pack(side="right", padx=(8, 0))

                url = sw["url"]
                wid = sw.get("winget")

                if wid:
                    install_btn = tk.Button(right, text="⬇ Install",
                        bg=THEME["accent"], fg=THEME["bg"], font=("Segoe UI", 8, "bold"),
                        relief="flat", padx=8, pady=2, cursor="hand2",
                        command=lambda w=wid: self._install_recommended(w))
                    install_btn.pack(side="left", padx=(0, 4))

                link_btn = tk.Button(right, text="🔗 Open",
                    bg=THEME["bg"], fg=THEME["accent"], font=("Segoe UI", 8),
                    relief="flat", padx=8, pady=2, cursor="hand2",
                    command=lambda u=url: webbrowser.open(u))
                link_btn.pack(side="left")

        # Bottom spacer
        tk.Frame(scroll_frame, bg=THEME["bg"], height=20).pack()

    # ─── Network Diagnostics Tab ─────────────────────────────────────────────
    def _build_network_tab(self):
        """Build the Network Diagnostics tab."""
        header = ttk.Frame(self.network_frame, style="Main.TFrame")
        header.pack(fill="x", pady=(8, 4), padx=8)
        ttk.Label(header, text="\ud83c\udf10  Network Diagnostics", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="\ud83d\udd04  Run Diagnostics", style="Accent.TButton",
                   command=self._run_network_diagnostics).pack(side="right")

        self.network_text = scrolledtext.ScrolledText(self.network_frame, wrap="word",
            bg=THEME["bg_card"], fg=THEME["text"], font=("Cascadia Mono", 10),
            insertbackground=THEME["text"], relief="flat", padx=12, pady=12)
        self.network_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for tag in ("good", "warn", "bad", "info", "dim", "tip"):
            self.network_text.tag_configure(tag, foreground=THEME.get(f"tag_{tag}", THEME["text"]))
        self.network_text.insert("end", "Click 'Run Diagnostics' to test your network connection.\n", "dim")
        self.network_text.configure(state="disabled")

    def _net_log(self, msg, tag=""):
        def _do():
            self.network_text.configure(state="normal")
            self.network_text.insert("end", msg + "\n", tag)
            self.network_text.see("end")
            self.network_text.configure(state="disabled")
        self.root.after(0, _do)

    def _run_network_diagnostics(self):
        def _work():
            self.root.after(0, lambda: self.network_text.configure(state="normal"))
            self.root.after(0, lambda: self.network_text.delete("1.0", "end"))
            self.root.after(0, lambda: self.network_text.configure(state="disabled"))
            self._net_log("\u2501\u2501\u2501 NETWORK DIAGNOSTICS \u2501\u2501\u2501", "info")
            self._net_log(f"Generated: {datetime.datetime.now():%Y-%m-%d %H:%M}\n", "dim")
            self._net_log("Running tests...\n", "dim")

            results = get_network_diagnostics()

            self._net_log(f"\ud83c\udf10  Adapter: {results.get('adapter_name', 'N/A')}", "info")
            self._net_log(f"   {results.get('adapter_desc', '')}", "dim")
            self._net_log(f"   Link Speed: {results.get('link_speed', 'N/A')}", "info")
            self._net_log(f"   Public IP: {results.get('public_ip', 'N/A')}", "info")
            self._net_log(f"   Gateway: {results.get('gateway', 'N/A')}", "info")
            self._net_log(f"   DNS: {results.get('dns', 'N/A')}", "info")
            self._net_log("")

            self._net_log("\ud83d\udce1  Latency Tests:", "info")
            for host in ["8.8.8.8", "1.1.1.1", "google.com"]:
                val = results.get(f"ping_{host}", "TIMEOUT")
                if val == "TIMEOUT":
                    self._net_log(f"   \u274c {host}: TIMEOUT", "bad")
                else:
                    try:
                        ms = float(val.replace(" ms", ""))
                        tag = "good" if ms < 50 else ("warn" if ms < 100 else "bad")
                        icon = "\u2705" if ms < 50 else ("\u26a0" if ms < 100 else "\u274c")
                        self._net_log(f"   {icon} {host}: {val}", tag)
                    except ValueError:
                        self._net_log(f"   {host}: {val}", "dim")

            self._net_log("\n\ud83d\udca1 Tip: For gaming, aim for < 30ms to your game server", "tip")
            self._net_log("\ud83d\udca1 Speed test: https://www.speedtest.net/", "tip")
        threading.Thread(target=_work, daemon=True).start()

    # ─── Startup Manager Tab ─────────────────────────────────────────────────
    def _build_startup_tab(self):
        """Build the Startup Manager tab."""
        header = ttk.Frame(self.startup_frame, style="Main.TFrame")
        header.pack(fill="x", pady=(8, 4), padx=8)
        ttk.Label(header, text="\ud83d\ude80  Startup Manager", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="\ud83d\udd04  Scan Startup Items", style="Accent.TButton",
                   command=self._scan_startup_items).pack(side="right")

        cols = ("Name", "Location", "Command")
        self.startup_tree = ttk.Treeview(self.startup_frame, columns=cols,
                                          show="headings", style="Custom.Treeview")
        for c in cols:
            self.startup_tree.heading(c, text=c)
            w = 200 if c == "Name" else (150 if c == "Location" else 400)
            self.startup_tree.column(c, width=w, minwidth=100)
        sb = ttk.Scrollbar(self.startup_frame, orient="vertical", command=self.startup_tree.yview)
        self.startup_tree.configure(yscrollcommand=sb.set)
        self.startup_tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        sb.pack(side="right", fill="y", pady=(0, 8), padx=(0, 8))

    def _scan_startup_items(self):
        def _work():
            self.log("Scanning startup items...", "info")
            for item in self.startup_tree.get_children():
                self.root.after(0, lambda i=item: self.startup_tree.delete(i))
            items = get_startup_items()
            for item in items:
                self.root.after(0, lambda i=item: self.startup_tree.insert("", "end",
                    values=(i["name"], i["location"], i["command"])))
            self.log(f"  Found {len(items)} startup item(s)", "success")
        threading.Thread(target=_work, daemon=True).start()

    # ─── Tools Tab (Disk Analyzer + Game Detection) ──────────────────────────
    def _build_tools_tab(self):
        """Build the Tools tab with Disk Analyzer and Game Detection sections."""
        # Scrollable container
        canvas = tk.Canvas(self.tools_frame, bg=THEME["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.tools_frame, orient="vertical", command=canvas.yview)
        self.tools_scroll = ttk.Frame(canvas, style="Main.TFrame")
        self.tools_scroll.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.tools_scroll, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        # Disk Analyzer Section
        disk_header = ttk.Frame(self.tools_scroll, style="Main.TFrame")
        disk_header.pack(fill="x", pady=(8, 4), padx=8)
        ttk.Label(disk_header, text="\ud83d\udcbe  Disk Space Analyzer", style="Title.TLabel").pack(side="left")
        ttk.Button(disk_header, text="\ud83d\udd04  Analyze", style="Accent.TButton",
                   command=self._analyze_disk_space).pack(side="right")

        self.disk_display = ttk.Frame(self.tools_scroll, style="Main.TFrame")
        self.disk_display.pack(fill="x", padx=8, pady=(0, 12))

        # Game Detection Section
        game_header = ttk.Frame(self.tools_scroll, style="Main.TFrame")
        game_header.pack(fill="x", pady=(8, 4), padx=8)
        ttk.Label(game_header, text="\ud83c\udfae  Installed Games", style="Title.TLabel").pack(side="left")
        ttk.Button(game_header, text="\ud83d\udd04  Detect Games", style="Accent.TButton",
                   command=self._detect_games).pack(side="right")

        cols = ("Game", "Launcher")
        self.games_tree = ttk.Treeview(self.tools_scroll, columns=cols,
                                        show="headings", style="Custom.Treeview", height=15)
        self.games_tree.heading("Game", text="Game")
        self.games_tree.heading("Launcher", text="Launcher")
        self.games_tree.column("Game", width=500, minwidth=200)
        self.games_tree.column("Launcher", width=100, minwidth=80)
        self.games_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _analyze_disk_space(self):
        def _work():
            self.log("Analyzing disk space...", "info")
            # Clear old widgets
            for w in self.disk_display.winfo_children():
                self.root.after(0, w.destroy)
            time.sleep(0.1)
            drives = get_disk_space_breakdown()
            for drv in drives:
                self.root.after(0, lambda d=drv: self._add_disk_bar(d))
            self.log(f"  Found {len(drives)} drive(s)", "success")
        threading.Thread(target=_work, daemon=True).start()

    def _add_disk_bar(self, drv):
        """Add a visual disk usage bar for a drive."""
        frame = tk.Frame(self.disk_display, bg=THEME["bg_card"], padx=12, pady=8,
                         highlightbackground=THEME["border"], highlightthickness=1)
        frame.pack(fill="x", pady=(4, 0))

        label = drv.get("label", "")
        lbl_text = f"{drv['letter']}: ({label})" if label else f"{drv['letter']}:"
        tk.Label(frame, text=f"\ud83d\udcbe {lbl_text}  \u2014  {drv['used_gb']:.1f} / {drv['total_gb']:.1f} GB used ({drv['pct_used']:.0f}%)",
                 bg=THEME["bg_card"], fg=THEME["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")

        bar_frame = tk.Frame(frame, bg=THEME["border"], height=20)
        bar_frame.pack(fill="x", pady=(4, 0))
        bar_frame.pack_propagate(False)

        pct = drv["pct_used"]
        color = "#4ade80" if pct < 70 else ("#fbbf24" if pct < 90 else "#ef4444")
        fill = tk.Frame(bar_frame, bg=color, width=1)
        fill.place(relwidth=max(0.01, pct / 100.0), relheight=1.0)

        tk.Label(frame, text=f"{drv['free_gb']:.1f} GB free",
                 bg=THEME["bg_card"], fg=THEME["text_dim"], font=("Segoe UI", 9)).pack(anchor="e")

    def _detect_games(self):
        def _work():
            self.log("Detecting installed games...", "info")
            for item in self.games_tree.get_children():
                self.root.after(0, lambda i=item: self.games_tree.delete(i))
            games = get_installed_games()
            for g in sorted(games, key=lambda x: x["name"]):
                self.root.after(0, lambda g=g: self.games_tree.insert("", "end",
                    values=(g["name"], g["launcher"])))
            self.log(f"  Found {len(games)} game(s) across all launchers", "success")
        threading.Thread(target=_work, daemon=True).start()

    # ─── System Monitor Tab ──────────────────────────────────────────────────
    def _build_monitor_tab(self):
        """Build the System Monitor tab with live gauge bars."""
        header = ttk.Frame(self.monitor_frame, style="Main.TFrame")
        header.pack(fill="x", pady=(8, 4), padx=8)
        ttk.Label(header, text="\ud83d\udcca  System Monitor", style="Title.TLabel").pack(side="left")

        self.monitor_active = False
        self.monitor_btn = ttk.Button(header, text="\u25b6  Start Monitoring",
                                       style="Accent.TButton", command=self._toggle_monitor)
        self.monitor_btn.pack(side="right")

        self.gauges_frame = ttk.Frame(self.monitor_frame, style="Main.TFrame")
        self.gauges_frame.pack(fill="x", padx=8, pady=(8, 4))

        # Create gauge bars
        self.gauge_vars = {}
        self.gauge_bars = {}
        self.gauge_labels = {}
        gauge_defs = [
            ("cpu_pct", "\ud83d\udda5 CPU Usage", "#7c3aed"),
            ("ram_pct", "\ud83d\udcbe RAM Usage", "#2563eb"),
            ("gpu_pct", "\ud83c\udfae GPU Usage", "#059669"),
        ]
        for key, label, color in gauge_defs:
            gframe = tk.Frame(self.gauges_frame, bg=THEME["bg_card"], padx=16, pady=12,
                             highlightbackground=THEME["border"], highlightthickness=1)
            gframe.pack(fill="x", pady=(4, 0))

            lbl_frame = tk.Frame(gframe, bg=THEME["bg_card"])
            lbl_frame.pack(fill="x")
            tk.Label(lbl_frame, text=label, bg=THEME["bg_card"],
                     fg=THEME["text"], font=("Segoe UI", 11, "bold")).pack(side="left")
            val_lbl = tk.Label(lbl_frame, text="0%", bg=THEME["bg_card"],
                               fg=THEME["accent"], font=("Segoe UI", 18, "bold"))
            val_lbl.pack(side="right")
            self.gauge_labels[key] = val_lbl

            bar_outer = tk.Frame(gframe, bg=THEME["border"], height=24)
            bar_outer.pack(fill="x", pady=(6, 0))
            bar_outer.pack_propagate(False)
            bar_inner = tk.Frame(bar_outer, bg=color, width=1)
            bar_inner.place(relwidth=0.01, relheight=1.0)
            self.gauge_bars[key] = (bar_inner, color)

        # GPU temp display
        self.gpu_temp_label = tk.Label(self.gauges_frame, text="",
                                        bg=THEME["bg"], fg=THEME["text_dim"],
                                        font=("Segoe UI", 10))
        self.gpu_temp_label.pack(anchor="e", padx=8, pady=(4, 0))

        # Monitor log area
        self.monitor_text = scrolledtext.ScrolledText(self.monitor_frame, wrap="word",
            bg=THEME["bg_card"], fg=THEME["text"], font=("Cascadia Mono", 9),
            insertbackground=THEME["text"], relief="flat", padx=12, pady=8, height=6)
        self.monitor_text.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.monitor_text.insert("end", "Click 'Start Monitoring' to begin live system monitoring.\n")
        self.monitor_text.configure(state="disabled")

    def _toggle_monitor(self):
        if self.monitor_active:
            self.monitor_active = False
            self.monitor_btn.configure(text="\u25b6  Start Monitoring")
        else:
            self.monitor_active = True
            self.monitor_btn.configure(text="\u23f9  Stop Monitoring")
            threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        while self.monitor_active:
            try:
                stats = get_live_system_stats()
                self.root.after(0, lambda s=stats: self._update_gauges(s))
            except Exception:
                pass
            time.sleep(2)

    def _update_gauges(self, stats):
        for key in ("cpu_pct", "ram_pct", "gpu_pct"):
            val = stats.get(key, 0)
            if key in self.gauge_bars:
                bar, color = self.gauge_bars[key]
                pct = max(0.01, val / 100.0)
                bar.place(relwidth=pct, relheight=1.0)
                # Color shift: green < 60%, yellow 60-85%, red > 85%
                if val > 85:
                    bar.configure(bg="#ef4444")
                elif val > 60:
                    bar.configure(bg="#fbbf24")
                else:
                    bar.configure(bg=color)
            if key in self.gauge_labels:
                self.gauge_labels[key].configure(text=f"{val}%")

        gpu_temp = stats.get("gpu_temp", 0)
        if gpu_temp > 0:
            temp_color = "#4ade80" if gpu_temp < 70 else ("#fbbf24" if gpu_temp < 85 else "#ef4444")
            self.gpu_temp_label.configure(text=f"\ud83c\udf21 GPU: {gpu_temp}\u00b0C", fg=temp_color)

    # ─── Action Handlers ─────────────────────────────────────────────────────
    def _toggle_power_plan(self):
        def _work():
            if self.power_mode == "balanced":
                # Save current plan so we can restore it later
                try:
                    current = get_power_plan_analysis()
                    self._saved_plan_guid = current.get("plan_guid", "")
                    self._saved_plan_name = current.get("plan_name", "")
                except Exception:
                    self._saved_plan_guid = ""
                    self._saved_plan_name = ""

                self.log("\u26a1 Switching to Ultimate Performance...", "info")
                self.log("  \ud83d\udd04 Cloning your custom settings to new plan...", "dim")
                ok, name = set_power_plan("ultimate", clone_settings=True)
                if ok:
                    self.power_mode = "ultimate"
                    self.root.after(0, lambda: self.power_btn.configure(text="\ud83d\udd0b  Restore Plan"))
                    self.log(f"  \u2705 Power plan: {name} (with your custom settings)", "success")
                    self.log("  \ud83d\udca1 All your tweaks carried over \u2014 toggle back when done", "tip")
            else:
                # Restore original plan
                orig_name = getattr(self, "_saved_plan_name", "High Performance")
                orig_guid = getattr(self, "_saved_plan_guid", "")
                self.log(f"\ud83d\udd0b Restoring {orig_name}...", "info")
                if orig_guid:
                    run_ps(f"powercfg /setactive {orig_guid}", timeout=10)
                else:
                    set_power_plan("high", clone_settings=False)
                self.power_mode = "balanced"
                self.root.after(0, lambda: self.power_btn.configure(text="\ud83d\udd0b  Gaming Mode"))
                self.log(f"  \u2705 Restored: {orig_name}", "success")
        threading.Thread(target=_work, daemon=True).start()

    def _start_defender_scan(self):
        def _work():
            self.log("\ud83d\udee1 Starting Windows Defender Quick Scan...", "info")
            self.log("  This may take 1\u20135 minutes...", "dim")
            self.root.after(0, lambda: self.defender_btn.configure(state="disabled"))
            out, rc = run_defender_scan("quick")
            if rc == 0:
                self.log("  \u2705 Quick scan complete \u2014 no threats found", "success")
            else:
                self.log(f"  \u26a0 Scan finished with code {rc}", "warning")
                if out:
                    self.log(f"  {out.strip()}", "dim")
            self.root.after(0, lambda: self.defender_btn.configure(state="normal"))
        threading.Thread(target=_work, daemon=True).start()

    def _export_health_report(self):
        """Export the health report as an HTML file."""
        def _work():
            try:
                content = self.health_text.get("1.0", "end").strip()
                if not content:
                    self.log("  \u26a0 No health report to export \u2014 run a scan first", "warning")
                    return

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"PC_Health_Report_{timestamp}.html"
                desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
                filepath = os.path.join(desktop, filename)

                html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PC Health Report</title>
<style>
body {{ font-family: 'Cascadia Mono', 'Consolas', monospace; background: #1a1a2e; color: #e0e0e0; padding: 40px; line-height: 1.6; }}
pre {{ white-space: pre-wrap; font-size: 13px; }}
h1 {{ color: #7dcfff; border-bottom: 2px solid #333; padding-bottom: 10px; }}
</style></head><body>
<h1>\u2699 PC Health Report</h1>
<pre>{content}</pre>
<p style="color:#666; margin-top:40px;">Generated by PC Maintenance Suite \u2014 {datetime.datetime.now():%Y-%m-%d %H:%M}</p>
</body></html>"""

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(html)
                self.log(f"\ud83d\udccb Health report exported to Desktop: {filename}", "success")
                os.startfile(filepath)
            except Exception as e:
                self.log(f"  \u274c Export failed: {e}", "bad")
        threading.Thread(target=_work, daemon=True).start()

    def _toggle_schedule(self):
        def _work():
            if not self.schedule_active:
                self.log("\ud83d\udcc5 Setting up weekly scheduled maintenance...", "info")
                ok, msg = schedule_maintenance("weekly")
                if ok:
                    self.schedule_active = True
                    self.root.after(0, lambda: self.schedule_btn.configure(text="\ud83d\udcc5  Unschedule"))
                    self.log(f"  \u2705 {msg}", "success")
                else:
                    self.log(f"  \u274c {msg}", "bad")
            else:
                self.log("\ud83d\udcc5 Removing scheduled maintenance...", "info")
                if remove_scheduled_maintenance():
                    self.schedule_active = False
                    self.root.after(0, lambda: self.schedule_btn.configure(text="\ud83d\udcc5  Schedule Weekly"))
                    self.log("  \u2705 Scheduled task removed", "success")
                else:
                    self.log("  \u274c Failed to remove scheduled task", "bad")
        threading.Thread(target=_work, daemon=True).start()

    def _install_recommended(self, winget_id):
        """Install from Recommended tab via winget."""
        def _do():
            self.log(f"📦 Installing {winget_id} via winget...", "info")
            out, rc = run_cmd(
                f"winget install --id {winget_id} --accept-package-agreements "
                f"--accept-source-agreements --silent", timeout=300
            )
            if rc == 0:
                self.log(f"  ✅ {winget_id} installed successfully", "success")
            else:
                self.log(f"  ⚠ {winget_id} install may have failed (rc={rc})", "warning")
        threading.Thread(target=_do, daemon=True).start()

    # ── Admin Check ─────────────────────────────────────────────────────

    def _check_admin(self):
        if is_admin():
            self.admin_label.configure(text="✅ Administrator", style="Admin.TLabel")
        else:
            self.admin_label.configure(text="⚠ Not Administrator — some features limited",
                                        style="NoAdmin.TLabel")

    # ── Logging ─────────────────────────────────────────────────────────

    def log(self, message, tag=""):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] ", "dim")
        self.log_text.insert("end", f"{message}\n", tag)
        self.log_text.see("end")

    def log_threadsafe(self, message, tag=""):
        """Log from a background thread — schedules on the main thread."""
        self.root.after(0, lambda m=message, t=tag: self.log(m, t))

    def set_status(self, msg):
        self.status_label.configure(text=msg)

    def set_progress(self, value):
        self.progress_var.set(value)
        self.root.update_idletasks()

    def _start_heartbeat(self, label="Working"):
        """Start a heartbeat timer that pulses in the status bar and log."""
        self._heartbeat_active = True
        self._heartbeat_start = time.time()
        self._heartbeat_label = label
        self._heartbeat_tick()

    def _heartbeat_tick(self):
        if not self._heartbeat_active:
            return
        elapsed = int(time.time() - self._heartbeat_start)
        mins, secs = divmod(elapsed, 60)
        time_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
        self.set_status(f"{self._heartbeat_label}... ({time_str} elapsed)")
        self.root.after(1000, self._heartbeat_tick)

    def _stop_heartbeat(self):
        self._heartbeat_active = False

    def _set_buttons_state(self, scanning=False):
        state = "disabled" if scanning else "normal"
        self.scan_btn.configure(state=state)
        # Only enable action buttons if we have scan data
        has_data = len(self.drivers) > 0
        self.fix_btn.configure(state=state if has_data and not scanning else "disabled")
        self.update_drivers_btn.configure(state=state if has_data and not scanning else "disabled")
        self.winget_btn.configure(state=state if has_data and not scanning else "disabled")
        self.update_all_btn.configure(state=state if has_data and not scanning else "disabled")

    # ── Scan ────────────────────────────────────────────────────────────

    def _start_scan(self):
        if self.is_scanning:
            return
        self.is_scanning = True
        self._set_buttons_state(scanning=True)
        threading.Thread(target=self._run_scan, daemon=True).start()

    def _run_scan(self):
        try:
            self.root.after(0, lambda: self.log("━━━ SYSTEM SCAN STARTED ━━━", "header"))
            self.root.after(0, lambda: self.set_status("Scanning drivers..."))
            self.root.after(0, lambda: self.set_progress(0))

            # Step 1: Drivers
            self.root.after(0, lambda: self.log("Scanning all PnP signed drivers...", "info"))
            self.root.after(0, lambda: self.set_progress(5))
            self.drivers = run_ps_json(
                "Get-WmiObject Win32_PnPSignedDriver | "
                "Select-Object DeviceName, DeviceClass, DriverVersion, "
                "DriverDate, Manufacturer, IsSigned, InfName | "
                "ConvertTo-Json -Compress"
            )
            self.root.after(0, lambda: self.log(f"  Found {len(self.drivers)} signed drivers", "success"))
            self.root.after(0, lambda: self.set_progress(25))

            # Step 2: Problem devices
            self.root.after(0, lambda: self.set_status("Scanning for problem devices..."))
            self.root.after(0, lambda: self.log("Scanning for problem devices...", "info"))
            self.problems = run_ps_json(
                "Get-PnpDevice | Where-Object { $_.Status -ne 'OK' -and $_.Present -eq $true } | "
                "Select-Object Status, Class, FriendlyName, InstanceId, Manufacturer | "
                "ConvertTo-Json -Compress"
            )
            count = len(self.problems)
            tag = "warning" if count > 0 else "success"
            self.root.after(0, lambda: self.log(f"  Found {count} problem device(s)", tag))
            self.root.after(0, lambda: self.set_progress(40))

            # Step 3: Windows Updates
            self.root.after(0, lambda: self.set_status("Checking Windows Update..."))
            self.root.after(0, lambda: self.log("Checking Windows Update...", "info"))
            wu_raw = run_ps_json(
                "$Session = New-Object -ComObject Microsoft.Update.Session; "
                "$Searcher = $Session.CreateUpdateSearcher(); "
                "try { "
                "  $Results = $Searcher.Search('IsInstalled=0'); "
                "  $Results.Updates | ForEach-Object { "
                "    [PSCustomObject]@{ "
                "      Title=$_.Title; "
                "      IsDriver=[bool]($_.Categories | Where-Object { $_.Name -eq 'Drivers' }); "
                "      IsDownloaded=$_.IsDownloaded "
                "    } "
                "  } | ConvertTo-Json -Compress "
                "} catch { Write-Output '[]' }",
                timeout=180
            )
            self.wu_data = {
                "driver_updates": [u for u in wu_raw if u.get("IsDriver")],
                "other_updates": [u for u in wu_raw if not u.get("IsDriver")],
            }
            wu_total = len(wu_raw)
            tag = "warning" if wu_total > 0 else "success"
            self.root.after(0, lambda: self.log(f"  Found {wu_total} pending update(s)", tag))
            self.root.after(0, lambda: self.set_progress(65))

            # Step 4: Winget
            self.root.after(0, lambda: self.set_status("Checking software updates (winget)..."))
            self.root.after(0, lambda: self.log("Checking winget for software updates...", "info"))
            out, _ = run_cmd("winget upgrade --include-unknown 2>nul", timeout=60)
            self.winget_updates = []
            if out:
                lines = out.split('\n')
                data_start = -1
                for i, line in enumerate(lines):
                    if re.match(r'^-{5,}', line.strip()):
                        data_start = i + 1
                        break
                if data_start > 0:
                    for line in lines[data_start:]:
                        stripped = line.strip()
                        if not stripped or 'upgrades available' in stripped.lower():
                            continue
                        self.winget_updates.append(stripped)
            wg_count = len(self.winget_updates)
            tag = "warning" if wg_count > 0 else "success"
            self.root.after(0, lambda: self.log(f"  Found {wg_count} upgradable package(s)", tag))
            self.root.after(0, lambda: self.set_progress(80))

            # Step 5: Disks
            self.root.after(0, lambda: self.set_status("Checking disk firmware..."))
            self.root.after(0, lambda: self.log("Checking disk firmware...", "info"))
            self.disks = run_ps_json(
                "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus, "
                "FirmwareVersion, Size, BusType | ConvertTo-Json -Compress"
            )
            for d in self.disks:
                name = d.get("FriendlyName", "?")
                health = d.get("HealthStatus", "?")
                fw = d.get("FirmwareVersion", "?")
                icon = "✅" if health in ("Healthy", 0) else "⚠️"
                self.root.after(0, lambda n=name, h=health, f=fw, ic=icon:
                    self.log(f"  {ic} {n} — FW: {f}, Health: {h}"))
            self.root.after(0, lambda: self.set_progress(85))

            # Step 6: NVIDIA GPU Driver
            self.root.after(0, lambda: self.set_status("Checking NVIDIA GPU driver..."))
            self.root.after(0, lambda: self.log("Checking NVIDIA Game Ready driver...", "info"))
            self.nvidia_result = check_nvidia_driver()
            if self.nvidia_result:
                nv = self.nvidia_result
                gpu = nv["gpu_name"]
                installed = nv["installed_ver"]
                latest = nv["latest_ver"]
                url = nv.get("download_url", "")
                if nv.get("error"):
                    self.root.after(0, lambda g=gpu, v=installed:
                        self.log(f"  🎮 {g} — v{v} (couldn't check for updates)", "dim"))
                elif nv["needs_update"]:
                    self.root.after(0, lambda g=gpu, v=installed, l=latest:
                        self.log(f"  ⚠ {g} — v{v} → v{l} available!", "warning"))
                    self.root.after(0, lambda u=url:
                        self.log(f"  📥 Download: {u}", "info"))
                else:
                    self.root.after(0, lambda g=gpu, v=installed:
                        self.log(f"  ✅ {g} — v{v} (up to date)", "success"))
            else:
                self.root.after(0, lambda: self.log("  No NVIDIA GPU detected", "dim"))
            self.root.after(0, lambda: self.set_progress(88))

            # Steps 7-13: Run all independent checks in parallel
            self.root.after(0, lambda: self.set_status("Gathering system & component info..."))
            self.root.after(0, lambda: self.log("Gathering system & component info (parallel)...", "info"))

            with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
                f_sys = pool.submit(get_system_info)
                f_bios = pool.submit(get_bios_info)
                f_amd = pool.submit(check_amd_chipset)
                f_vcpp = pool.submit(check_vcpp_runtimes)
                f_dotnet = pool.submit(check_dotnet_runtimes)
                f_dx = pool.submit(check_directx)
                f_temp = pool.submit(get_temp_sizes)

            self.sys_info = f_sys.result()
            self.bios_info = f_bios.result()
            self.amd_info = f_amd.result()
            self.vcpp_results = f_vcpp.result()
            self.dotnet_runtimes = f_dotnet.result()
            self.dx_info = f_dx.result()
            self.temp_sizes = f_temp.result()
            self.root.after(0, lambda: self.set_progress(93))

            # Log results — System Info
            if self.sys_info:
                cpu = self.sys_info.get("CPUName", "?").strip()
                cores = self.sys_info.get("Cores", "?")
                threads = self.sys_info.get("Threads", "?")
                ram = self.sys_info.get("TotalRAM", "?")
                speed = self.sys_info.get("RAMSpeed", "?")
                slots = self.sys_info.get("RAMSlots", "?")
                os_name = self.sys_info.get("OSName", "?")
                build = self.sys_info.get("OSBuild", "?")
                self.root.after(0, lambda c=cpu, co=cores, t=threads:
                    self.log(f"  🖥 {c} ({co}C/{t}T)", "info"))
                self.root.after(0, lambda r=ram, s=speed, sl=slots:
                    self.log(f"  💾 {r} GB RAM @ {s} MHz ({sl} slot(s))", "info"))
                self.root.after(0, lambda o=os_name, b=build:
                    self.log(f"  🪟 {o} (Build {b})", "dim"))

            # BIOS / Motherboard
            if self.bios_info:
                bios_ver = self.bios_info.get("BIOSVersion", "?")
                board = self.bios_info.get("BoardProduct", "?")
                board_manuf = self.bios_info.get("BoardManuf", "?")
                self.root.after(0, lambda bm=board_manuf, bp=board, bv=bios_ver:
                    self.log(f"  🔧 {bm} {bp} — BIOS: {bv}", "info"))

            # AMD Chipset
            if self.amd_info:
                chipset = self.amd_info.get("chipset")
                if chipset:
                    cs_name = chipset.get("DeviceName", "?")
                    cs_ver = chipset.get("DriverVersion", "?")
                    self.root.after(0, lambda n=cs_name, v=cs_ver:
                        self.log(f"  🔴 AMD Chipset: {n} — v{v}", "info"))
                self.root.after(0, lambda:
                    self.log(f"  📥 AMD drivers: https://www.amd.com/en/support", "dim"))

            # Visual C++ Runtimes
            missing_vcpp = [r for r in self.vcpp_results if not r["installed"]]
            installed_vcpp = [r for r in self.vcpp_results if r["installed"]]
            for r in installed_vcpp:
                self.root.after(0, lambda r=r:
                    self.log(f"  ✅ {r['name']} — v{r['version']}", "success"))
            for r in missing_vcpp:
                self.root.after(0, lambda r=r:
                    self.log(f"  ❌ {r['name']} — NOT INSTALLED", "warning"))
            if missing_vcpp:
                self.root.after(0, lambda c=len(missing_vcpp):
                    self.log(f"  💡 {c} missing — will be installed during 'Update Everything'", "info"))

            # .NET Runtimes
            if self.dotnet_runtimes:
                seen = {}
                for rt in self.dotnet_runtimes:
                    t = rt["type"]
                    if t not in seen or rt["version"] > seen[t]:
                        seen[t] = rt["version"]
                for t, v in seen.items():
                    short = t.replace("Microsoft.", "")
                    self.root.after(0, lambda s=short, v=v:
                        self.log(f"  ✅ {s} — v{v}", "success"))
            else:
                self.root.after(0, lambda: self.log("  ⚠ .NET SDK/Runtime not found", "warning"))

            # DirectX
            dx_ver = self.dx_info.get("version", "?")
            self.root.after(0, lambda v=dx_ver:
                self.log(f"  🎮 DirectX: {v}", "info"))

            # Temp Files
            total_temp = sum(self.temp_sizes.values())
            for folder, size in self.temp_sizes.items():
                icon = "⚠" if size > 500 else "✅"
                self.root.after(0, lambda f=folder, s=size, ic=icon:
                    self.log(f"  {ic} {f}: {s:.0f} MB", "info" if s < 500 else "warning"))
            if total_temp > 1000:
                self.root.after(0, lambda t=total_temp:
                    self.log(f"  💡 {t:.0f} MB reclaimable — clean during 'Update Everything'", "info"))
            self.root.after(0, lambda: self.set_progress(96))

            # Generate PC Health Report
            self._generate_health_report()

            # Populate UI
            self.root.after(0, self._populate_ui)
            self.root.after(0, lambda: self.set_progress(100))
            self.root.after(0, lambda: self.set_status("Scan complete ✅"))
            self.root.after(0, lambda: self.log("━━━ SCAN COMPLETE ━━━", "header"))

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Scan error: {e}", "error"))
            self.root.after(0, lambda: self.set_status(f"Scan failed: {e}"))
        finally:
            self.is_scanning = False
            self.root.after(0, lambda: self._set_buttons_state(scanning=False))

    def _generate_health_report(self):
        """Generate comprehensive PC Health Report in the health tab."""
        # Clear previous report
        def _clear():
            self.health_text.configure(state="normal")
            self.health_text.delete("1.0", "end")
            self.health_text.configure(state="disabled")
        self.root.after(0, _clear)
        time.sleep(0.1)  # let the clear propagate

        score = 100  # start at 100, deduct for issues
        tips = []

        self._health_log("━━━ PC HEALTH REPORT ━━━", "header")
        self._health_log(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", "dim")
        self._health_log("")

        # ── CPU ──
        self._health_log("🖥  PROCESSOR", "section")
        if self.sys_info:
            cpu = self.sys_info.get("CPUName", "?").strip()
            cores = self.sys_info.get("Cores", 0)
            threads = self.sys_info.get("Threads", 0)
            clock = self.sys_info.get("MaxClock", 0)
            self._health_log(f"  {cpu}", "info")
            self._health_log(f"  {cores} cores / {threads} threads @ {clock} MHz", "dim")
            if cores and cores >= 8:
                self._health_log("  ✅ Excellent core count for gaming and multitasking", "good")
            elif cores and cores >= 4:
                self._health_log("  ✅ Good core count", "good")
            else:
                self._health_log("  ⚠ Low core count — may bottleneck modern games", "warn")
                score -= 10
                tips.append("Consider upgrading CPU — 6+ cores recommended for modern gaming")
        self._health_log("")

        # ── RAM ──
        self._health_log("💾  MEMORY (RAM)", "section")
        if self.sys_info:
            ram_raw = self.sys_info.get("TotalRAM", 0)
            try:
                ram = float(ram_raw)
            except (ValueError, TypeError):
                ram = 0
            speed_raw = self.sys_info.get("RAMSpeed", 0)
            try:
                speed = int(float(speed_raw))
            except (ValueError, TypeError):
                speed = 0
            slots = self.sys_info.get("RAMSlots", 0)
            self._health_log(f"  {ram:.1f} GB total — {speed} MHz — {slots} stick(s)", "info")
            if ram >= 30:
                self._health_log("  \u2705 Excellent \u2014 32+ GB is ideal for gaming + content creation", "good")
            elif ram >= 14:
                self._health_log("  \u2705 Good \u2014 16 GB is solid for most games", "good")
            elif ram >= 8:
                self._health_log("  ⚠ 8 GB is minimum — you may experience stuttering", "warn")
                score -= 10
                tips.append("Upgrade to 16+ GB RAM for smoother gaming")
            else:
                self._health_log("  ❌ Below minimum — upgrade urgently", "bad")
                score -= 20
                tips.append("CRITICAL: Upgrade RAM to at least 16 GB")
            if speed and speed < 3200 and ram >= 16:
                self._health_log("  💡 RAM speed is below 3200 MHz — consider enabling XMP/EXPO in BIOS", "tip")
                tips.append("Enable XMP/EXPO in BIOS to run RAM at rated speed")
            if slots and slots == 1:
                self._health_log("  💡 Single-channel detected — dual-channel gives ~20% more bandwidth", "tip")
                tips.append("Add a matching RAM stick for dual-channel (20% bandwidth boost)")
        self._health_log("")

        # ── GPU ──
        self._health_log("🎮  GRAPHICS (GPU)", "section")
        if self.nvidia_result:
            nv = self.nvidia_result
            self._health_log(f"  {nv['gpu_name']}", "info")
            self._health_log(f"  Driver: v{nv['installed_ver']}", "dim")
            if nv.get("needs_update"):
                self._health_log(f"  ⚠ Update available: v{nv['latest_ver']}", "warn")
                self._health_log(f"  📥 {nv.get('download_url', '')}", "tip")
                score -= 5
                tips.append("Update NVIDIA GPU driver to latest Game Ready version")
            elif not nv.get("error"):
                self._health_log("  ✅ Driver is up to date", "good")
        else:
            self._health_log("  No NVIDIA GPU detected", "dim")
        if self.amd_info:
            self._health_log("  AMD Chipset drivers:", "info")
            self._health_log("  📥 Check AMD drivers: https://www.amd.com/en/support", "tip")
        self._health_log("")

        # ── Storage ──
        self._health_log("💿  STORAGE", "section")
        for d in self.disks:
            name = d.get("FriendlyName", "?")
            health = d.get("HealthStatus", "?")
            media = d.get("MediaType", "?")
            size_bytes = d.get("Size", 0)
            size_gb = round(size_bytes / (1024**3), 0) if size_bytes else 0
            bus = d.get("BusType", "?")
            self._health_log(f"  {name} — {size_gb:.0f} GB ({media}, {bus})", "info")
            if health in ("Healthy", 0):
                self._health_log("  ✅ Healthy", "good")
            else:
                self._health_log(f"  ❌ Health: {health} — BACK UP DATA IMMEDIATELY", "bad")
                score -= 25
                tips.append(f"CRITICAL: {name} health is {health} — back up and replace drive")
            if str(media) == "HDD" or str(media) == "4":
                self._health_log("  💡 HDD detected — SSD upgrade would significantly improve load times", "tip")
                tips.append(f"Upgrade {name} from HDD to SSD for faster load times")
        self._health_log("")

        # ── Drivers ──
        self._health_log("📋  DRIVERS", "section")
        prob_count = len(self.problems)
        total_count = len(self.drivers)
        self._health_log(f"  {total_count} drivers scanned, {prob_count} problem device(s)", "info")
        if prob_count == 0:
            self._health_log("  ✅ All devices healthy", "good")
        else:
            self._health_log(f"  ⚠ {prob_count} device(s) need attention", "warn")
            score -= (prob_count * 3)
            for p in self.problems[:5]:
                self._health_log(f"    • {p.get('FriendlyName', '?')} — {p.get('Status', '?')}", "dim")
            tips.append("Run 'Fix Problem Devices' to repair driver issues")

        # Old drivers
        now = datetime.datetime.now()
        old_count = 0
        for d in self.drivers:
            raw_date = d.get("DriverDate", "")
            parsed = parse_driver_date(raw_date)
            if parsed and (now - parsed).days > 730:
                old_count += 1
        if old_count > 10:
            self._health_log(f"  ⚠ {old_count} drivers older than 2 years", "warn")
            tips.append("Many old drivers — run Windows Update to refresh")
        self._health_log("")

        # ── Runtimes ──
        self._health_log("📦  RUNTIMES & FRAMEWORKS", "section")
        missing_vcpp = [r for r in self.vcpp_results if not r["installed"]]
        if missing_vcpp:
            for r in missing_vcpp:
                self._health_log(f"  ❌ {r['name']} — MISSING", "bad")
            score -= (len(missing_vcpp) * 5)
            tips.append("Install missing VC++ Redistributables via 'Update Everything'")
        else:
            self._health_log("  ✅ All Visual C++ Redistributables installed", "good")

        if self.dotnet_runtimes:
            self._health_log(f"  ✅ .NET runtimes found ({len(self.dotnet_runtimes)} installed)", "good")
        else:
            self._health_log("  ⚠ No .NET runtimes detected", "warn")
            score -= 5

        dx_ver = self.dx_info.get("version", "?")
        self._health_log(f"  DirectX: {dx_ver}", "info")
        self._health_log("")

        # ── Windows Updates ──
        self._health_log("🪟  WINDOWS UPDATES", "section")
        wu_total = len(self.wu_data.get("driver_updates", [])) + len(self.wu_data.get("other_updates", []))
        if wu_total == 0:
            self._health_log("  ✅ System is up to date", "good")
        else:
            self._health_log(f"  ⚠ {wu_total} update(s) pending", "warn")
            score -= min(wu_total * 2, 15)
            tips.append("Install pending Windows Updates")

        wg_count = len(self.winget_updates)
        if wg_count > 0:
            self._health_log(f"  ⚠ {wg_count} software update(s) available via winget", "warn")
            score -= min(wg_count, 10)
        self._health_log("")

        # ── BIOS / Motherboard ──
        self._health_log("🔧  MOTHERBOARD & BIOS", "section")
        if self.bios_info:
            bm = self.bios_info.get("BoardManuf", "?")
            bp = self.bios_info.get("BoardProduct", "?")
            bv = self.bios_info.get("BIOSVersion", "?")
            self._health_log(f"  {bm} {bp}", "info")
            self._health_log(f"  BIOS Version: {bv}", "dim")
            self._health_log("  💡 Check manufacturer website for BIOS updates", "tip")
        self._health_log("")

        # ── Temp Files ──
        self._health_log("🗑  TEMP FILES & CACHE", "section")
        total_temp = sum(self.temp_sizes.values())
        for folder, size in self.temp_sizes.items():
            icon = "⚠" if size > 500 else "✅"
            self._health_log(f"  {icon} {folder}: {size:.0f} MB", "good" if size < 500 else "warn")
        if total_temp > 500:
            self._health_log(f"  💡 {total_temp:.0f} MB can be cleaned up", "tip")
            score -= 3
            tips.append(f"Clean {total_temp:.0f} MB of temp files via 'Update Everything'")
        else:
            self._health_log("  ✅ Temp folders are clean", "good")
        self._health_log("")

        # ── Power Plan Analysis ──
        self._health_log("\u26a1  POWER PLAN ANALYSIS", "section")
        try:
            pp = get_power_plan_analysis()
            plan_name = pp.get("plan_name", "Unknown")
            is_perf = "performance" in plan_name.lower() or "high" in plan_name.lower()
            plan_icon = "\u2705" if is_perf else "\u26a0"
            self._health_log(f"  {plan_icon} Active Plan: {plan_name}", "good" if is_perf else "warn")
            self._health_log(f"  GUID: {pp.get('plan_guid', 'N/A')}", "dim")
            self._health_log("")

            # Important subgroups to highlight
            key_groups = {
                "processor power management": "\ud83d\udda5",
                "sleep": "\ud83d\udca4",
                "display": "\ud83d\udcbb",
                "hard disk": "\ud83d\udcbe",
                "pci express": "\ud83d\udd0c",
                "usb settings": "\ud83d\udd0c",
                "desktop background settings": "\ud83c\udf05",
                "multimedia settings": "\ud83c\udfa5",
                "internet explorer": "\ud83c\udf10",
                "wireless adapter settings": "\ud83d\udce1",
            }

            for group in pp.get("groups", []):
                gname = group["name"]
                gname_lower = gname.lower()
                icon = "\ud83d\udce6"
                for kw, ic in key_groups.items():
                    if kw in gname_lower:
                        icon = ic
                        break

                settings = group.get("settings", [])
                if not settings:
                    continue

                self._health_log(f"  {icon} {gname}", "info")
                for s in settings:
                    name = s["name"]
                    ac = s.get("ac", "")
                    dc = s.get("dc", "")
                    unit = s.get("unit", "")

                    # Format hex values to decimal for readability
                    def _hex_to_readable(val, unit_str=""):
                        if not val:
                            return "N/A"
                        try:
                            num = int(val, 16) if val.startswith("0x") else int(val)
                            if "seconds" in unit_str.lower():
                                if num == 0:
                                    return "Never"
                                return f"{num}s" if num < 60 else f"{num // 60}m"
                            elif "percent" in unit_str.lower() or "%" in unit_str:
                                return f"{num}%"
                            elif num > 1000000:
                                return f"{num // 1000} MHz"
                            return str(num)
                        except (ValueError, TypeError):
                            return val

                    ac_val = _hex_to_readable(ac, unit)
                    dc_val = _hex_to_readable(dc, unit)

                    if dc_val and dc_val != "N/A":
                        self._health_log(f"    {name}: AC={ac_val}  DC={dc_val}", "dim")
                    else:
                        self._health_log(f"    {name}: {ac_val}", "dim")
                self._health_log("")

            if not is_perf:
                tips.append("Switch to High/Ultimate Performance power plan for better gaming performance")
                score -= 3
        except Exception as e:
            self._health_log(f"  \u26a0 Could not analyze power plan: {e}", "dim")
        self._health_log("")

        # ── Overall Score ──
        score = max(0, min(100, score))
        self._health_log("━━━ OVERALL HEALTH SCORE ━━━", "header")
        if score >= 90:
            grade = "🟢 EXCELLENT"
            tag = "good"
        elif score >= 75:
            grade = "🟡 GOOD"
            tag = "good"
        elif score >= 50:
            grade = "🟠 NEEDS ATTENTION"
            tag = "warn"
        else:
            grade = "🔴 POOR"
            tag = "bad"
        self._health_log(f"  {grade} — {score}/100", tag)
        self._health_log("")

        # ── Tips ──
        if tips:
            self._health_log("━━━ RECOMMENDATIONS ━━━", "header")
            for i, tip in enumerate(tips, 1):
                self._health_log(f"  {i}. {tip}", "tip")
            self._health_log("")
            self._health_log("  💡 Run 'Update Everything' to fix most of these automatically", "info")
        else:
            self._health_log("  🎉 Your PC is in great shape! No action needed.", "good")

        # Enable export button after report is generated
        self.root.after(0, lambda: self.export_btn.configure(state="normal"))

    def _populate_ui(self):
        now = datetime.datetime.now()

        # Build a set of WU driver update titles for cross-referencing
        wu_driver_titles = set()
        for u in self.wu_data.get("driver_updates", []):
            wu_driver_titles.add(u.get("Title", "").lower())

        # ── Populate driver tree ────────────────────────────────────────
        self.driver_tree.delete(*self.driver_tree.get_children())
        self._driver_data = []  # Store for filtering
        self._driver_lookup = {}  # iid -> extra data for context menus
        classes = set()

        for d in self.drivers:
            name = d.get("DeviceName") or "Unknown"
            cls = d.get("DeviceClass") or "Unknown"
            ver = d.get("DriverVersion") or "?"
            mfr = d.get("Manufacturer") or "?"
            signed = "✅" if d.get("IsSigned", True) else "❌"
            inf = d.get("InfName") or "?"
            raw_date = d.get("DriverDate", "")

            dt = parse_driver_date(raw_date)
            date_str = dt.strftime("%Y-%m-%d") if dt else "Unknown"
            age_days = (now - dt).days if dt else None

            if age_days is not None:
                if age_days > 365:
                    age_str = f"{round(age_days / 365, 1)}yr"
                else:
                    age_str = f"{age_days}d"
            else:
                age_str = "?"

            # Check if a WU update mentions this device
            available = "—"
            has_wu_update = False
            name_lower = name.lower()
            for title in wu_driver_titles:
                if name_lower in title or (mfr.lower() in title and cls.lower() in title):
                    available = "WU Available"
                    has_wu_update = True
                    break

            tag = "ok"
            if has_wu_update:
                tag = "has_update"
            elif age_days and age_days > 1825:
                tag = "very_old"
            elif age_days and age_days > 730:
                tag = "old"
            if not d.get("IsSigned", True):
                tag = "unsigned"

            # entry: (name, class, version, available, date, age, manufacturer, signed, inf, tag, age_days)
            entry = (name, cls, ver, available, date_str, age_str, mfr, signed, inf, tag, age_days or 0)
            self._driver_data.append(entry)
            classes.add(cls)

        # Sort by age descending (oldest first)
        self._driver_data.sort(key=lambda x: -x[10])
        for entry in self._driver_data:
            iid = self.driver_tree.insert("", "end", values=entry[:9], tags=(entry[9],))
            # Store lookup data: name, inf, manufacturer
            self._driver_lookup[iid] = {
                "name": entry[0], "inf": entry[8], "manufacturer": entry[6],
                "class": entry[1], "version": entry[2],
            }

        # Update class filter
        self.class_filter["values"] = ["All"] + sorted(classes)

        # ── Populate problems tree ──────────────────────────────────────
        self.problem_tree.delete(*self.problem_tree.get_children())
        for p in self.problems:
            st = p.get("Status", "?")
            tag = "error" if st == "Error" else "unknown"
            self.problem_tree.insert("", "end", values=(
                st, p.get("Class", "?"), p.get("FriendlyName", "?"),
                p.get("InstanceId", "?")
            ), tags=(tag,))

        # ── Populate updates tree ───────────────────────────────────────
        self.update_tree.delete(*self.update_tree.get_children())
        self._update_lookup = {}  # iid -> extra data for context menus

        for u in self.wu_data.get("driver_updates", []):
            iid = self.update_tree.insert("", "end", values=(
                "🔌 Driver", u.get("Title", "?"), "Installed", "Available"
            ), tags=("driver",))
            kb = u.get("KBArticleIDs", "")
            self._update_lookup[iid] = {"title": u.get("Title", ""), "kb": kb, "source": "wu"}

        for u in self.wu_data.get("other_updates", []):
            iid = self.update_tree.insert("", "end", values=(
                "🪟 System", u.get("Title", "?"), "Installed", "Available"
            ), tags=("system",))
            kb = u.get("KBArticleIDs", "")
            self._update_lookup[iid] = {"title": u.get("Title", ""), "kb": kb, "source": "wu"}

        for line in self.winget_updates:
            parts = re.split(r'\s{2,}', line.strip())
            pkg_name = parts[0] if parts else line.strip()
            pkg_id = parts[1] if len(parts) > 1 else "?"
            cur_ver = parts[2] if len(parts) > 2 else "?"
            new_ver = parts[3] if len(parts) > 3 else "?"
            iid = self.update_tree.insert("", "end", values=(
                "📦 Winget", pkg_name, cur_ver, new_ver
            ), tags=("software",))
            self._update_lookup[iid] = {
                "title": pkg_name, "id": pkg_id, "source": "winget",
                "current": cur_ver, "available": new_ver,
            }

        # ── Update stats ────────────────────────────────────────────────
        old_count = sum(1 for d in self._driver_data if d[10] > 730)

        self.stat_vars["total_drivers"].set(str(len(self.drivers)))
        self.stat_vars["problem_devices"].set(str(len(self.problems)))
        self.stat_vars["old_drivers"].set(str(old_count))

        wu_total = len(self.wu_data.get("driver_updates", [])) + len(self.wu_data.get("other_updates", []))
        self.stat_vars["wu_updates"].set(str(wu_total))
        self.stat_vars["winget_updates"].set(str(len(self.winget_updates)))

        healthy = sum(1 for d in self.disks if d.get("HealthStatus") in ("Healthy", 0))
        self.stat_vars["disk_health"].set(f"{healthy}/{len(self.disks)}")

        # GPU driver stat
        if self.nvidia_result:
            nv = self.nvidia_result
            if nv.get("needs_update"):
                self.stat_vars["gpu_driver"].set("⚠ Update")
            elif nv.get("error"):
                self.stat_vars["gpu_driver"].set("? Err")
            else:
                self.stat_vars["gpu_driver"].set("✅ OK")
        else:
            self.stat_vars["gpu_driver"].set("—")

    # ── Filtering & Sorting ─────────────────────────────────────────────

    def _filter_drivers(self, *args):
        search = self.driver_search_var.get().lower()
        class_filter = self.class_filter_var.get()

        self.driver_tree.delete(*self.driver_tree.get_children())
        self._driver_lookup = {}
        for entry in self._driver_data:
            name, cls, ver, available, date_str, age_str, mfr, signed, inf, tag, age_days = entry

            if class_filter != "All" and cls != class_filter:
                continue
            if search and search not in name.lower() and search not in mfr.lower() and search not in cls.lower():
                continue

            iid = self.driver_tree.insert("", "end", values=entry[:9], tags=(tag,))
            self._driver_lookup[iid] = {
                "name": name, "inf": inf, "manufacturer": mfr,
                "class": cls, "version": ver,
            }

    def _sort_tree(self, tree, col):
        items = [(tree.set(k, col), k) for k in tree.get_children("")]
        try:
            items.sort(key=lambda t: float(t[0].replace("yr", "").replace("d", "")))
        except (ValueError, TypeError):
            items.sort(key=lambda t: t[0].lower())
        for i, (val, k) in enumerate(items):
            tree.move(k, "", i)

    # ── Right-Click Context Menu Handlers ────────────────────────────────

    def _driver_right_click(self, event):
        item = self.driver_tree.identify_row(event.y)
        if item:
            self.driver_tree.selection_set(item)
            self.driver_ctx_menu.post(event.x_root, event.y_root)

    def _driver_search_online(self):
        sel = self.driver_tree.selection()
        if not sel:
            return
        data = self._driver_lookup.get(sel[0], {})
        name = data.get("name", "")
        mfr = data.get("manufacturer", "")
        query = f"{mfr} {name} driver download".replace(" ", "+")
        webbrowser.open(f"https://www.google.com/search?q={query}")

    def _driver_open_file_location(self):
        sel = self.driver_tree.selection()
        if not sel:
            return
        data = self._driver_lookup.get(sel[0], {})
        inf = data.get("inf", "")
        if not inf or inf == "?":
            messagebox.showinfo("No INF", "No INF file path available for this driver.")
            return
        # INF files are in C:\Windows\INF or the driver store
        inf_path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "INF" / inf
        if inf_path.exists():
            subprocess.Popen(f'explorer /select,"{inf_path}"', shell=True)
        else:
            # Try the driver store
            store_path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "DriverStore" / "FileRepository"
            # Search for the INF in the driver store
            matches = list(store_path.glob(f"**/{inf}"))
            if matches:
                subprocess.Popen(f'explorer /select,"{matches[0]}"', shell=True)
            else:
                messagebox.showinfo("Not Found", f"Could not locate {inf} on disk.\n\nChecked:\n• {inf_path}\n• {store_path}")

    def _problem_right_click(self, event):
        item = self.problem_tree.identify_row(event.y)
        if item:
            self.problem_tree.selection_set(item)
            self.problem_ctx_menu.post(event.x_root, event.y_root)

    def _problem_search_online(self):
        sel = self.problem_tree.selection()
        if not sel:
            return
        values = self.problem_tree.item(sel[0], "values")
        name = values[2] if len(values) > 2 else "device"
        status = values[0] if values else "error"
        query = f"{name} {status} driver fix windows 11".replace(" ", "+")
        webbrowser.open(f"https://www.google.com/search?q={query}")

    def _update_right_click(self, event):
        item = self.update_tree.identify_row(event.y)
        if item:
            self.update_tree.selection_set(item)
            self.update_ctx_menu.post(event.x_root, event.y_root)

    def _update_visit_page(self):
        sel = self.update_tree.selection()
        if not sel:
            return
        data = self._update_lookup.get(sel[0], {})
        if data.get("source") == "wu":
            kb = data.get("kb", "")
            if kb:
                webbrowser.open(f"https://support.microsoft.com/kb/{kb}")
            else:
                title = data.get("title", "")
                query = f"{title} windows update".replace(" ", "+")
                webbrowser.open(f"https://www.google.com/search?q={query}")
        elif data.get("source") == "winget":
            pkg_id = data.get("id", "")
            if pkg_id and pkg_id != "?":
                webbrowser.open(f"https://winget.run/pkg/{pkg_id.replace('.', '/')}")
            else:
                title = data.get("title", "")
                webbrowser.open(f"https://www.google.com/search?q={title.replace(' ', '+')}+download")

    def _update_search_online(self):
        sel = self.update_tree.selection()
        if not sel:
            return
        data = self._update_lookup.get(sel[0], {})
        title = data.get("title", "update")
        query = f"{title} download".replace(" ", "+")
        webbrowser.open(f"https://www.google.com/search?q={query}")

    def _open_device_manager(self):
        subprocess.Popen("devmgmt.msc", shell=True)

    # ── Turbo Mode ──────────────────────────────────────────────────────

    def _toggle_turbo(self):
        if self.turbo_active:
            self._disable_turbo()
        else:
            self._enable_turbo()

    def _enable_turbo(self):
        if not is_admin():
            self.log("❌ Turbo Mode requires admin privileges!", "error")
            return
        self.turbo_active = True
        self.turbo_btn.configure(text="⚡  Turbo ON", style="Danger.TButton")
        self.log("━━━ TURBO MODE ENABLED ━━━", "header")
        self.log("  Maximizing all download speeds...", "info")

        # Single batched PS call instead of 7 sequential ones
        run_ps(
            "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DeliveryOptimization' "
            "-Name 'DOPercentageMaxForegroundBandwidth' -Value 100 -Type DWord -Force -EA SilentlyContinue; "
            "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DeliveryOptimization' "
            "-Name 'DOPercentageMaxBackgroundBandwidth' -Value 100 -Type DWord -Force -EA SilentlyContinue; "
            "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DeliveryOptimization' "
            "-Name 'DODownloadMode' -Value 1 -Type DWord -Force -EA SilentlyContinue; "
            "Get-BitsTransfer -AllUsers -EA SilentlyContinue | "
            "Set-BitsTransfer -Priority Foreground -EA SilentlyContinue; "
            "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\BITS' "
            "-Name 'EnableBITSMaxBandwidth' -Value 0 -Type DWord -Force -EA SilentlyContinue; "
            "Restart-Service -Name 'DoSvc' -Force -EA SilentlyContinue; "
            "Restart-Service -Name 'BITS' -Force -EA SilentlyContinue",
            timeout=15
        )

        self.log("  ✅ Delivery Optimization — bandwidth caps removed", "success")
        self.log("  ✅ BITS — set to foreground priority", "success")
        self.log("  ⚡ All downloads will now use maximum speed", "info")
        self.log("  💡 Toggle off when done to restore defaults", "info")

    def _disable_turbo(self):
        self.turbo_active = False
        self.turbo_btn.configure(text="⚡  Turbo Mode", style="Secondary.TButton")
        self.log("━━━ TURBO MODE DISABLED ━━━", "header")
        self.log("  Restoring Delivery Optimization defaults...", "info")

        # Single batched PS call
        run_ps(
            "Remove-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DeliveryOptimization' "
            "-Name 'DOPercentageMaxForegroundBandwidth' -Force -EA SilentlyContinue; "
            "Remove-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DeliveryOptimization' "
            "-Name 'DOPercentageMaxBackgroundBandwidth' -Force -EA SilentlyContinue; "
            "Remove-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DeliveryOptimization' "
            "-Name 'DODownloadMode' -Force -EA SilentlyContinue; "
            "Remove-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\BITS' "
            "-Name 'EnableBITSMaxBandwidth' -Force -EA SilentlyContinue; "
            "Restart-Service -Name 'DoSvc' -Force -EA SilentlyContinue; "
            "Restart-Service -Name 'BITS' -Force -EA SilentlyContinue",
            timeout=15
        )

        self.log("  ✅ All download settings restored to defaults", "success")

    # ── Reboot Suppression ──────────────────────────────────────────────

    def _suppress_reboot(self):
        """Block automatic restarts during updates."""
        if not is_admin():
            return
        run_ps(
            "New-Item -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU' "
            "-Force -ErrorAction SilentlyContinue | Out-Null; "
            "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU' "
            "-Name 'NoAutoRebootWithLoggedOnUsers' -Value 1 -Type DWord -Force",
            timeout=10
        )
        # Abort any pending shutdown that might already be queued
        run_cmd("shutdown /a 2>nul")
        self.log_threadsafe("🛡 Auto-restart blocked during updates", "info")

    def _unsuppress_reboot(self):
        """Restore default reboot behavior."""
        if not is_admin():
            return
        run_ps(
            "Remove-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU' "
            "-Name 'NoAutoRebootWithLoggedOnUsers' -Force -ErrorAction SilentlyContinue",
            timeout=10
        )
        # Abort any shutdown that was queued during the update
        run_cmd("shutdown /a 2>nul")

    def _prompt_reboot(self):
        """Ask user if they want to restart now (runs on main thread)."""
        def ask():
            self._unsuppress_reboot()
            if messagebox.askyesno("Restart Required",
                    "Some updates may require a restart to complete.\n\n"
                    "Would you like to restart your PC now?"):
                self.log("🔄 Restarting PC in 10 seconds...", "warning")
                run_cmd("shutdown /r /t 10 /c \"PC Driver Updater: Restarting to complete updates\"")
            else:
                self.log("✅ Restart deferred — remember to restart later", "info")
        self.root.after(500, ask)

    # ── Actions ─────────────────────────────────────────────────────────

    def _start_fix_problems(self):
        if self.is_fixing or not self.problems:
            return
        self.is_fixing = True
        self._set_buttons_state(scanning=True)
        threading.Thread(target=self._run_fix_problems, daemon=True).start()

    def _run_fix_problems(self):
        try:
            self.root.after(0, lambda: self.log("━━━ FIXING PROBLEM DEVICES ━━━", "header"))
            self.root.after(0, lambda: self.set_status("Fixing problem devices..."))

            if not is_admin():
                self.root.after(0, lambda: self.log("❌ Admin privileges required!", "error"))
                self.root.after(0, lambda: self.set_status("Admin required for device fixes"))
                return

            total = len(self.problems)
            fixed = 0

            for i, p in enumerate(self.problems):
                iid = p.get("InstanceId", "")
                name = p.get("FriendlyName", "Unknown")
                progress = int((i / total) * 100)
                self.root.after(0, lambda n=name: self.log(f"  Fixing: {n}...", "info"))
                self.root.after(0, lambda v=progress: self.set_progress(v))

                run_ps(f"Disable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue")
                time.sleep(2)
                run_ps(f"Enable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue")
                time.sleep(2)

                out, _ = run_ps(f"(Get-PnpDevice -InstanceId '{iid}' -ErrorAction SilentlyContinue).Status")
                if out and out.strip() == "OK":
                    self.root.after(0, lambda n=name: self.log(f"  ✅ Fixed: {n}", "success"))
                    fixed += 1
                else:
                    self.root.after(0, lambda n=name, s=out.strip() if out else "?":
                        self.log(f"  ⚠ Still {s}: {n} (may need reboot)", "warning"))

            run_cmd("pnputil /scan-devices")
            self.root.after(0, lambda: self.log(f"Fixed {fixed}/{total} devices", "success"))
            self.root.after(0, lambda: self.set_progress(100))
            self.root.after(0, lambda: self.set_status(f"Fixed {fixed}/{total} problem devices"))

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error: {e}", "error"))
        finally:
            self.is_fixing = False
            self.root.after(0, lambda: self._set_buttons_state(scanning=False))

    def _start_windows_update(self):
        if self.is_fixing:
            return
        self.is_fixing = True
        self._set_buttons_state(scanning=True)
        threading.Thread(target=self._run_windows_update, daemon=True).start()

    def _run_windows_update(self):
        try:
            self.root.after(0, lambda: self.log("━━━ WINDOWS UPDATE ━━━", "header"))
            self.root.after(0, lambda: self.set_progress(5))
            self.root.after(0, lambda: self._start_heartbeat("Windows Update"))

            if not is_admin():
                self.root.after(0, lambda: self.log("❌ Admin privileges required!", "error"))
                return

            self._suppress_reboot()

            # Phase 1: Search for updates
            self.root.after(0, lambda: self.log("⏳ Phase 1/3: Searching for updates...", "info"))
            self.root.after(0, lambda: self.set_progress(10))

            wu_script = (
                "$ProgressPreference = 'SilentlyContinue'; "
                "Write-Output 'WU_SEARCH Creating update session...'; "
                "$Session = New-Object -ComObject Microsoft.Update.Session; "
                "$Searcher = $Session.CreateUpdateSearcher(); "
                "Write-Output 'WU_SEARCH Querying Microsoft servers...'; "
                "$Results = $Searcher.Search('IsInstalled=0'); "
                "$Count = $Results.Updates.Count; "
                "Write-Output \"WU_SEARCH Found $Count update(s)\"; "
                "if($Count -gt 0) { "
                "  $Results.Updates | ForEach-Object { "
                "    $size = [math]::Round($_.MaxDownloadSize / 1MB, 1); "
                "    Write-Output \"  WU_UPDATE $($_.Title) - $($size) MB\" "
                "  }; "
                "  Write-Output 'WU_DOWNLOAD Starting downloads (high priority)...'; "
                "  $Downloader = $Session.CreateUpdateDownloader(); "
                "  $Downloader.Updates = $Results.Updates; "
                "  $Downloader.Priority = 3; "
                "  $DlResult = $Downloader.Download(); "
                "  Write-Output \"WU_DOWNLOAD Complete - ResultCode=$($DlResult.ResultCode)\"; "
                "  Write-Output 'WU_INSTALL Installing updates...'; "
                "  $Installer = $Session.CreateUpdateInstaller(); "
                "  $Installer.Updates = $Results.Updates; "
                "  $InstResult = $Installer.Install(); "
                "  for($i=0; $i -lt $Count; $i++) { "
                "    $r = $InstResult.GetUpdateResult($i); "
                "    $title = $Results.Updates.Item($i).Title; "
                "    Write-Output \"  WU_RESULT $title - Code=$($r.ResultCode)\" "
                "  }; "
                "  Write-Output \"WU_INSTALL Complete - ResultCode=$($InstResult.ResultCode)\"; "
                "  if($InstResult.RebootRequired) { Write-Output 'WU_REBOOT A reboot is required to finish.' } "
                "} else { Write-Output 'WU_DONE No pending updates.' }"
            )

            def wu_line_callback(line, tag):
                if "WU_SEARCH" in line:
                    self.log_threadsafe(f"  {line}", "info")
                    self.root.after(0, lambda: self.set_progress(20))
                elif "WU_UPDATE" in line:
                    self.log_threadsafe(f"  {line}", "dim")
                elif "WU_DOWNLOAD" in line:
                    self.log_threadsafe(f"  {line}", "info")
                    self.root.after(0, lambda: self.set_progress(50))
                elif "WU_INSTALL" in line:
                    self.log_threadsafe(f"  {line}", "info")
                    self.root.after(0, lambda: self.set_progress(75))
                elif "WU_RESULT" in line:
                    t = "success" if "Code=2" in line else "warning"
                    self.log_threadsafe(f"  {line}", t)
                elif "WU_REBOOT" in line:
                    self.log_threadsafe(f"  {line}", "warning")
                elif "WU_DONE" in line:
                    self.log_threadsafe(f"  {line}", "success")
                else:
                    self.log_threadsafe(f"  {line}", tag)

            run_cmd_streamed(
                f'powershell -NoProfile -Command "{wu_script}"',
                wu_line_callback, timeout=600
            )

            self.root.after(0, lambda: self._stop_heartbeat())
            self.root.after(0, lambda: self.set_progress(100))
            self.root.after(0, lambda: self.set_status("Windows Update complete ✅"))
            self.root.after(0, lambda: self.log("Windows Update finished.", "success"))
            self._prompt_reboot()

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error: {e}", "error"))
        finally:
            self.root.after(0, lambda: self._stop_heartbeat())
            self._unsuppress_reboot()
            self.is_fixing = False
            self.root.after(0, lambda: self._set_buttons_state(scanning=False))

    def _start_winget_update(self):
        if self.is_fixing:
            return
        self.is_fixing = True
        self._set_buttons_state(scanning=True)
        threading.Thread(target=self._run_winget_update, daemon=True).start()

    def _run_winget_update(self):
        try:
            self.root.after(0, lambda: self.log("━━━ SOFTWARE UPDATE (WINGET) ━━━", "header"))
            self.root.after(0, lambda: self.set_progress(10))
            self.root.after(0, lambda: self._start_heartbeat("Software Update (winget)"))
            self.root.after(0, lambda: self.log("⏳ Running winget upgrade --all ...", "info"))
            self._suppress_reboot()

            pkg_count = 0
            failed_pkgs = []  # list of (name, url)
            current_pkg = [""]  # mutable for nested scope
            current_url = [""]

            def winget_line_callback(line, tag):
                nonlocal pkg_count
                stripped = line.strip()
                if not stripped:
                    return
                # Skip spinner chars, separator lines, and progress bar fragments
                if stripped in ('-', '\\', '|', '/'):
                    return
                if re.match(r'^[-]{5,}$', stripped):
                    return
                if re.match(r'^[\u2588\u2591\u2592\u2593\u2580\u2584\s]+\d', stripped):
                    return  # Progress bar lines like ████░░░░ 100 MB / 200 MB
                # Track current package being processed
                m = re.match(r'^\(\d+/\d+\)\s+Found\s+(.+?)\s+\[', stripped)
                if m:
                    current_pkg[0] = m.group(1)
                    current_url[0] = ""
                    self.log_threadsafe(f"  📦 {stripped}", "info")
                    return
                # Track download URL
                url_m = re.search(r'(https?://\S+)', stripped)
                if url_m and "downloading" in stripped.lower():
                    current_url[0] = url_m.group(1)
                    self.log_threadsafe(f"  📥 {stripped}", "info")
                    return
                if "successfully installed" in stripped.lower():
                    pkg_count += 1
                    self.log_threadsafe(f"  ✅ {stripped}", "success")
                elif "successfully verified" in stripped.lower():
                    pass  # Skip hash verification lines
                elif "failed" in stripped.lower() or "unexpected error" in stripped.lower():
                    self.log_threadsafe(f"  ❌ {stripped}", "error")
                    if current_pkg[0]:
                        failed_pkgs.append((current_pkg[0], current_url[0]))
                elif "installing" in stripped.lower() or "downloading" in stripped.lower():
                    if url_m:
                        current_url[0] = url_m.group(1)
                    self.log_threadsafe(f"  📥 {stripped}", "info")
                elif "upgrades available" in stripped.lower():
                    self.log_threadsafe(f"  📋 {stripped}", "info")
                elif "starting package" in stripped.lower():
                    pass  # Skip redundant "Starting package install..." lines
                elif "licensed to you" in stripped.lower() or "not responsible" in stripped.lower():
                    pass  # Skip boilerplate license notices
                else:
                    self.log_threadsafe(f"  {stripped}", "dim")

            _, rc = run_cmd_streamed(
                "winget upgrade --all --accept-package-agreements "
                "--accept-source-agreements --include-unknown 2>nul",
                winget_line_callback, timeout=900
            )

            self.root.after(0, lambda: self._stop_heartbeat())
            self.root.after(0, lambda: self.set_progress(100))
            result = f"complete — {pkg_count} package(s) updated ✅" if rc == 0 else f"finished — {pkg_count} updated (some may have failed)"
            self.root.after(0, lambda r=result: self.set_status(f"Software update {r}"))
            self.root.after(0, lambda r=result: self.log(f"Software update {r}", "success"))

            # Failed package summary with download links
            if failed_pkgs:
                self.root.after(0, lambda: self.log("\n⚠ THESE FAILED — manual install links:", "warning"))
                for name, url in failed_pkgs:
                    if url:
                        self.root.after(0, lambda n=name, u=url:
                            self.log(f"  ❌ {n}  →  {u}", "error"))
                    else:
                        # Build a search URL as fallback
                        search_url = f"https://www.google.com/search?q={name.replace(' ', '+')}+download"
                        self.root.after(0, lambda n=name, u=search_url:
                            self.log(f"  ❌ {n}  →  {u}", "error"))

            self._prompt_reboot()

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error: {e}", "error"))
        finally:
            self.root.after(0, lambda: self._stop_heartbeat())
            self._unsuppress_reboot()
            self.is_fixing = False
            self.root.after(0, lambda: self._set_buttons_state(scanning=False))

    def _start_update_all(self):
        if self.is_fixing:
            return
        if not messagebox.askyesno("Update Everything",
                "This will:\n"
                "• Fix all problem devices\n"
                "• Install Windows Updates\n"
                "• Upgrade all software via winget\n"
                "• Install missing VC++ Redistributables\n"
                "• Run SFC & DISM system repair\n"
                "• Clean temp files & WU cache\n"
                "• Check NVIDIA driver\n\n"
                "This may take 15-30 minutes. Continue?"):
            return
        self.is_fixing = True
        self._set_buttons_state(scanning=True)
        threading.Thread(target=self._run_update_all, daemon=True).start()

    def _run_update_all(self):
        try:
            self.root.after(0, lambda: self.log("━━━ UPDATING EVERYTHING ━━━", "header"))
            self.root.after(0, lambda: self._start_heartbeat("Updating Everything"))
            start_time = time.time()
            self._suppress_reboot()

            # Create System Restore Point (safety net)
            self.root.after(0, lambda: self.log("\ud83d\udee1 Creating restore point...", "info"))
            rp_ok, rp_msg = create_restore_point()
            if rp_ok:
                self.root.after(0, lambda: self.log(f"  \u2705 {rp_msg}", "success"))
            else:
                self.root.after(0, lambda m=rp_msg: self.log(f"  \u26a0 {m} (continuing anyway)", "dim"))

            # Phase 1: Fix problems
            if self.problems and is_admin():
                self.root.after(0, lambda: self.log("\n▶ Phase 1/4: Fixing problem devices", "header"))
                self.root.after(0, lambda: self.set_progress(5))
                total = len(self.problems)
                for i, p in enumerate(self.problems):
                    iid = p.get("InstanceId", "")
                    name = p.get("FriendlyName", "Unknown")
                    self.root.after(0, lambda n=name, idx=i+1, t=total:
                        self.log(f"  [{idx}/{t}] Disabling: {n}", "info"))
                    run_ps(f"Disable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue")
                    time.sleep(2)
                    self.root.after(0, lambda n=name, idx=i+1, t=total:
                        self.log(f"  [{idx}/{t}] Re-enabling: {n}", "info"))
                    run_ps(f"Enable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue")
                    time.sleep(1)
                    # Check result
                    out, _ = run_ps(f"(Get-PnpDevice -InstanceId '{iid}' -ErrorAction SilentlyContinue).Status")
                    status = out.strip() if out else "?"
                    if status == "OK":
                        self.root.after(0, lambda n=name: self.log(f"  ✅ Fixed: {n}", "success"))
                    else:
                        self.root.after(0, lambda n=name, s=status:
                            self.log(f"  ⚠ Still {s}: {n}", "warning"))
            else:
                self.root.after(0, lambda: self.log("\n▶ Phase 1/4: No problem devices to fix", "dim"))

            # Phase 2: PnP scan
            self.root.after(0, lambda: self.log("\n▶ Phase 2/4: PnP device scan", "header"))
            self.root.after(0, lambda: self.set_progress(20))
            if is_admin():
                self.root.after(0, lambda: self.log("  Running pnputil /scan-devices...", "info"))
                out, _ = run_cmd("pnputil /scan-devices")
                if out:
                    for line in out.split('\n'):
                        if line.strip():
                            self.root.after(0, lambda l=line.strip(): self.log(f"  {l}", "dim"))
                self.root.after(0, lambda: self.log("  PnP device scan complete", "success"))

            # Phase 3: Windows Update (streamed)
            self.root.after(0, lambda: self.log("\n▶ Phase 3/4: Windows Update", "header"))
            self.root.after(0, lambda: self.set_progress(30))
            if is_admin():
                wu_script = (
                    "$ProgressPreference = 'SilentlyContinue'; "
                    "Write-Output 'WU_SEARCH Querying Microsoft servers...'; "
                    "$Session = New-Object -ComObject Microsoft.Update.Session; "
                    "$Searcher = $Session.CreateUpdateSearcher(); "
                    "$Results = $Searcher.Search('IsInstalled=0'); "
                    "$Count = $Results.Updates.Count; "
                    "Write-Output \"WU_SEARCH Found $Count update(s)\"; "
                    "if($Count -gt 0) { "
                    "  $Results.Updates | ForEach-Object { Write-Output \"  WU_UPDATE $($_.Title)\" }; "
                    "  Write-Output 'WU_DOWNLOAD Downloading (high priority)...'; "
                    "  $Downloader = $Session.CreateUpdateDownloader(); "
                    "  $Downloader.Updates = $Results.Updates; "
                    "  $Downloader.Priority = 3; "
                    "  $DlResult = $Downloader.Download(); "
                    "  Write-Output \"WU_DOWNLOAD Done - Code=$($DlResult.ResultCode)\"; "
                    "  Write-Output 'WU_INSTALL Installing...'; "
                    "  $Installer = $Session.CreateUpdateInstaller(); "
                    "  $Installer.Updates = $Results.Updates; "
                    "  $InstResult = $Installer.Install(); "
                    "  Write-Output \"WU_INSTALL Done - Code=$($InstResult.ResultCode)\"; "
                    "  if($InstResult.RebootRequired) { Write-Output 'WU_REBOOT Reboot required.' } "
                    "} else { Write-Output 'WU_DONE No pending updates.' }"
                )
                def wu_cb(line, tag):
                    t = "info"
                    if "WU_SEARCH" in line: t = "info"
                    elif "WU_UPDATE" in line: t = "dim"
                    elif "WU_DOWNLOAD" in line: t = "info"
                    elif "WU_INSTALL" in line: t = "info"
                    elif "WU_REBOOT" in line: t = "warning"
                    elif "WU_DONE" in line: t = "success"
                    self.log_threadsafe(f"  {line}", t)

                run_cmd_streamed(
                    f'powershell -NoProfile -Command "{wu_script}"',
                    wu_cb, timeout=600
                )
            else:
                self.root.after(0, lambda: self.log("  Skipped — admin required", "warning"))

            # Phase 4: Winget (streamed)
            self.root.after(0, lambda: self.log("\n▶ Phase 4/4: Software Update (winget)", "header"))
            self.root.after(0, lambda: self.set_progress(60))
            pkg_count = 0
            failed_pkgs = []
            current_pkg = [""]
            current_url = [""]

            def winget_cb(line, tag):
                nonlocal pkg_count
                stripped = line.strip()
                if not stripped or stripped in ('-', '\\', '|', '/'):
                    return
                if re.match(r'^[-]{5,}$', stripped):
                    return
                if re.match(r'^[\u2588\u2591\u2592\u2593\u2580\u2584\s]+\d', stripped):
                    return
                m = re.match(r'^\(\d+/\d+\)\s+Found\s+(.+?)\s+\[', stripped)
                if m:
                    current_pkg[0] = m.group(1)
                    current_url[0] = ""
                    self.log_threadsafe(f"  📦 {stripped}", "info")
                    return
                url_m = re.search(r'(https?://\S+)', stripped)
                if url_m and "downloading" in stripped.lower():
                    current_url[0] = url_m.group(1)
                    self.log_threadsafe(f"  📥 {stripped}", "info")
                    return
                if "successfully installed" in stripped.lower():
                    pkg_count += 1
                    self.log_threadsafe(f"  ✅ {stripped}", "success")
                elif "successfully verified" in stripped.lower() or "starting package" in stripped.lower():
                    pass
                elif "licensed to you" in stripped.lower() or "not responsible" in stripped.lower():
                    pass
                elif "failed" in stripped.lower() or "unexpected error" in stripped.lower():
                    self.log_threadsafe(f"  ❌ {stripped}", "error")
                    if current_pkg[0]:
                        failed_pkgs.append((current_pkg[0], current_url[0]))
                else:
                    self.log_threadsafe(f"  {stripped}", "dim")

            run_cmd_streamed(
                "winget upgrade --all --accept-package-agreements "
                "--accept-source-agreements --include-unknown 2>nul",
                winget_cb, timeout=900
            )
            self.root.after(0, lambda: self.log(f"  Software updates: {pkg_count} package(s) updated", "success"))
            if failed_pkgs:
                self.root.after(0, lambda: self.log("\n⚠ THESE FAILED — manual install links:", "warning"))
                for name, url in failed_pkgs:
                    if url:
                        self.root.after(0, lambda n=name, u=url:
                            self.log(f"  ❌ {n}  →  {u}", "error"))
                    else:
                        search_url = f"https://www.google.com/search?q={name.replace(' ', '+')}+download"
                        self.root.after(0, lambda n=name, u=search_url:
                            self.log(f"  ❌ {n}  →  {u}", "error"))
            self.root.after(0, lambda: self.set_progress(90))

            # Verification
            self.root.after(0, lambda: self.log("\n▶ Verification", "header"))
            remaining = run_ps_json(
                "Get-PnpDevice | Where-Object { $_.Status -ne 'OK' -and $_.Present -eq $true } | "
                "Select-Object Status, FriendlyName | ConvertTo-Json -Compress"
            )
            rem_count = len(remaining)
            if rem_count > 0:
                self.root.after(0, lambda: self.log(
                    f"  ⚠ {rem_count} device(s) still have issues (may need reboot)", "warning"))
                for r in remaining[:10]:
                    self.root.after(0, lambda r=r: self.log(
                        f"    • {r.get('FriendlyName', '?')} — {r.get('Status', '?')}", "dim"))
            else:
                self.root.after(0, lambda: self.log("  🎉 All devices healthy!", "success"))

            # NVIDIA GPU driver check
            self.root.after(0, lambda: self.log("\n▶ NVIDIA Game Ready Driver", "header"))
            nv = check_nvidia_driver()
            if nv:
                gpu = nv["gpu_name"]
                installed = nv["installed_ver"]
                latest = nv["latest_ver"]
                url = nv.get("download_url", "")
                if nv.get("error"):
                    self.root.after(0, lambda g=gpu, v=installed:
                        self.log(f"  🎮 {g} — v{v} (couldn't check)", "dim"))
                elif nv["needs_update"]:
                    self.root.after(0, lambda g=gpu, v=installed, l=latest:
                        self.log(f"  ⚠ {g} — v{v} → v{l} available!", "warning"))
                    self.root.after(0, lambda u=url:
                        self.log(f"  📥 Download: {u}", "info"))
                else:
                    self.root.after(0, lambda g=gpu, v=installed:
                        self.log(f"  ✅ {g} — v{v} (up to date)", "success"))
            else:
                self.root.after(0, lambda: self.log("  No NVIDIA GPU detected", "dim"))

            # -- Phase 5: Install missing VC++ Redistributables (AIO) --
            self.root.after(0, lambda: self.log("\n\u25b6 Visual C++ Redistributables", "header"))
            vcpp = check_vcpp_runtimes()
            missing = [r for r in vcpp if not r["installed"]]
            if missing:
                self.root.after(0, lambda c=len(missing):
                    self.log(f"  \ud83d\udce6 {c} missing \u2014 installing VC++ AIO package...", "info"))
                out, rc = run_cmd(
                    "winget install --id abbodi1406.vcredist --accept-package-agreements "
                    "--accept-source-agreements --silent", timeout=180
                )
                if rc == 0:
                    self.root.after(0, lambda:
                        self.log("  \u2705 All VC++ Redistributables installed (AIO)", "success"))
                else:
                    self.root.after(0, lambda:
                        self.log("  \u26a0 VC++ AIO install may have failed \u2014 check manually", "warning"))
            else:
                self.root.after(0, lambda: self.log("  \u2705 All VC++ Redistributables already installed", "success"))

            # ── Phase 6: SFC & DISM System Repair (Optimized) ──
            self.root.after(0, lambda: self.log("\n▶ System File Integrity Check", "header"))
            self.root.after(0, lambda: self.log("  Quick health check...", "info"))

            # Step 1: Fast CheckHealth (~10 seconds) — instant corruption check
            check_out, check_rc = run_cmd(
                "dism /online /cleanup-image /checkhealth", timeout=30
            )
            check_text = (check_out or "").lower()
            has_corruption = ("repairable" in check_text or "repair" in check_text
                              or check_rc != 0)

            if not has_corruption:
                # Clean! Skip everything — saves 10-20 minutes
                self.root.after(0, lambda: self.log("  ✅ Component store is healthy — no repair needed", "success"))
                self.root.after(0, lambda: self.log("  ⚡ Skipped full scan (CheckHealth passed in ~10s)", "dim"))
            else:
                # Corruption detected — escalate to full repair
                self.root.after(0, lambda: self.log("  ⚠ Corruption detected — escalating to full repair", "warning"))

                def dism_cb(line, tag):
                    line = line.strip()
                    if line and not line.startswith("Deployment Image"):
                        self.log_threadsafe(f"  {line}", "dim")

                # Step 2: RestoreHealth (scans AND repairs — skip ScanHealth since this does both)
                self.root.after(0, lambda: self.log("  Running DISM RestoreHealth (5-15 min)...", "info"))
                restore_out, restore_rc = run_cmd_streamed(
                    "dism /online /cleanup-image /restorehealth", dism_cb, timeout=1800
                )
                if restore_rc == 0:
                    self.root.after(0, lambda: self.log("  ✅ DISM repair complete", "success"))
                else:
                    self.root.after(0, lambda: self.log("  ⚠ DISM repair finished with warnings", "warning"))

                # Step 3: SFC only runs if DISM found issues (otherwise redundant)
                self.root.after(0, lambda: self.log("  Running SFC scan (5-10 min)...", "info"))

                def sfc_cb(line, tag):
                    line = line.strip()
                    if line:
                        self.log_threadsafe(f"  {line}", "dim")

                sfc_out, sfc_rc = run_cmd_streamed("sfc /scannow", sfc_cb, timeout=900)
                if sfc_rc == 0:
                    self.root.after(0, lambda: self.log("  ✅ System file check complete", "success"))
                else:
                    self.root.after(0, lambda: self.log("  ⚠ SFC found issues (reboot may be needed)", "warning"))

            # ── Phase 7: Temp File Cleanup ──
            self.root.after(0, lambda: self.log("\n▶ Temp File Cleanup", "header"))
            temp_folders = {
                "User Temp": os.environ.get("TEMP", ""),
                "Windows Temp": r"C:\Windows\Temp",
                "WU Cache": r"C:\Windows\SoftwareDistribution\Download",
            }
            total_cleaned = 0
            for label, path in temp_folders.items():
                if not path or not os.path.exists(path):
                    continue
                out, rc = run_ps(
                    f"$before = (Get-ChildItem -Path '{path}' -Recurse -Force -EA SilentlyContinue | "
                    f"Measure-Object -Property Length -Sum).Sum; "
                    f"Get-ChildItem -Path '{path}' -Recurse -Force -EA SilentlyContinue | "
                    f"Remove-Item -Force -Recurse -EA SilentlyContinue; "
                    f"$after = (Get-ChildItem -Path '{path}' -Recurse -Force -EA SilentlyContinue | "
                    f"Measure-Object -Property Length -Sum).Sum; "
                    f"[math]::Round(($before - $after) / 1MB, 1)", timeout=60
                )
                try:
                    cleaned = max(0, float(out.strip())) if out and out.strip() else 0
                except (ValueError, TypeError):
                    cleaned = 0
                total_cleaned += cleaned
                self.root.after(0, lambda l=label, c=cleaned:
                    self.log(f"  🗑 {l}: {c:.0f} MB cleaned", "success" if c > 0 else "dim"))
            self.root.after(0, lambda t=total_cleaned:
                self.log(f"  Total freed: {t:.0f} MB", "info"))

            # Browser cache cleanup
            self.root.after(0, lambda: self.log("\n\u25b6 Browser Cache Cleanup", "header"))
            try:
                bcaches = get_browser_caches()
                browser_total = sum(bcaches.values())
                if browser_total > 10:
                    cleaned_browsers = clean_browser_caches()
                    bc_total = sum(cleaned_browsers.values())
                    for browser, amt in cleaned_browsers.items():
                        if amt > 0:
                            self.root.after(0, lambda b=browser, a=amt:
                                self.log(f"  \ud83d\uddd1 {b}: {a:.0f} MB cleaned", "success"))
                    self.root.after(0, lambda t=bc_total:
                        self.log(f"  Total browser cache freed: {t:.0f} MB", "info"))
                else:
                    self.root.after(0, lambda: self.log("  \u2705 Browser caches are clean", "dim"))
            except Exception as e:
                self.root.after(0, lambda e=e: self.log(f"  \u26a0 Browser cleanup skipped: {e}", "dim"))

            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            time_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"

            self.root.after(0, lambda: self._stop_heartbeat())
            self.root.after(0, lambda: self.set_progress(100))
            self.root.after(0, lambda ts=time_str: self.set_status(f"All updates complete ✅ ({ts})"))
            self.root.after(0, lambda ts=time_str: self.log(f"━━━ ALL UPDATES COMPLETE ({ts}) ━━━", "header"))
            self._prompt_reboot()

            # Save log
            log_data = {
                "timestamp": datetime.datetime.now().isoformat(),
                "admin": is_admin(),
                "drivers_scanned": len(self.drivers),
                "problems_before": len(self.problems),
                "problems_after": rem_count,
                "elapsed_seconds": elapsed,
            }
            with open(LOG_PATH, "w") as f:
                json.dump(log_data, f, indent=2)

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error: {e}", "error"))
        finally:
            self.root.after(0, lambda: self._stop_heartbeat())
            self._unsuppress_reboot()
            self.is_fixing = False
            self.root.after(0, lambda: self._set_buttons_state(scanning=False))


# ─── Entry Point ────────────────────────────────────────────────────────────────

def elevate_to_admin():
    """Re-launch this script as Administrator via UAC prompt."""
    if is_admin():
        return True  # Already admin

    try:
        # Re-launch with "runas" to trigger UAC elevation dialog
        params = f'"{os.path.abspath(__file__)}"'
        # Pass along any command-line args
        if len(sys.argv) > 1:
            params += " " + " ".join(f'"{a}"' for a in sys.argv[1:])

        result = ctypes.windll.shell32.ShellExecuteW(
            None,           # hwnd
            "runas",        # operation — triggers UAC prompt
            sys.executable, # python.exe
            params,         # script path + args
            None,           # working directory
            1               # SW_SHOWNORMAL
        )

        # ShellExecuteW returns > 32 on success
        if result > 32:
            sys.exit(0)  # Exit the non-admin instance
        else:
            return False  # User declined UAC or error
    except Exception:
        return False


def main():
    # Auto-elevate to admin on launch
    if not is_admin():
        if not elevate_to_admin():
            # User declined UAC — still launch, but with limited features
            pass

    root = tk.Tk()
    root.tk.call("tk", "scaling", 1.25)

    # Dark title bar on Windows 11
    try:
        from ctypes import windll
        root.update()
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        windll.dwmapi.DwmSetWindowAttribute(
            windll.user32.GetParent(root.winfo_id()),
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass

    app = DriverUpdaterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
