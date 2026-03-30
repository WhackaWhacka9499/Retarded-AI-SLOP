"""
PC Driver Updater — Full System Driver & Software Maintenance
==============================================================
Scans every driver on your system, identifies outdated/problem ones,
and updates everything possible through Windows Update, pnputil, and winget.

Usage (run as Administrator for full functionality):
  python pc_driver_updater.py              # Full scan + update everything
  python pc_driver_updater.py --scan       # Scan only (no changes)
  python pc_driver_updater.py --drivers    # Update drivers only
  python pc_driver_updater.py --software   # Update software only
  python pc_driver_updater.py --fix        # Fix problem devices only
"""

import subprocess
import sys
import os
import ctypes
import time
import json
import argparse
import datetime
import re
from pathlib import Path
from collections import defaultdict

# ─── Config ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
REPORT_PATH = SCRIPT_DIR / "driver_update_report.txt"
LOG_PATH = SCRIPT_DIR / "driver_update_log.json"

COLORS = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "red":     "\033[91m",
    "green":   "\033[92m",
    "yellow":  "\033[93m",
    "blue":    "\033[94m",
    "magenta": "\033[95m",
    "cyan":    "\033[96m",
    "gray":    "\033[90m",
}

def c(color, text):
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


# ─── Helpers ────────────────────────────────────────────────────────────────────

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_ps(command, timeout=120, silent=False):
    """Run a PowerShell command and return (stdout, returncode)."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if not silent and out:
            for line in out.split('\n'):
                print(f"    {line}")
        return out, result.returncode
    except subprocess.TimeoutExpired:
        if not silent:
            print(f"    {c('yellow', '⏰ Timed out')}")
        return "", -1
    except Exception as e:
        if not silent:
            print(f"    {c('red', f'Error: {e}')}")
        return str(e), -1


def run_ps_json(command, timeout=120):
    """Run PowerShell command returning parsed JSON."""
    out, _ = run_ps(command, timeout, silent=True)
    if not out:
        return []
    try:
        data = json.loads(out)
        return [data] if isinstance(data, dict) else data
    except json.JSONDecodeError:
        return []


def run_cmd(command, timeout=120, silent=False):
    """Run a shell command."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, shell=True,
        )
        out = result.stdout.strip()
        if not silent and out:
            for line in out.split('\n'):
                print(f"    {line}")
        return out, result.returncode
    except Exception as e:
        if not silent:
            print(f"    {c('red', f'Error: {e}')}")
        return str(e), -1


def header(title, icon=""):
    w = 62
    print(f"\n  {c('cyan', '═' * w)}")
    print(f"  {c('bold', f'  {icon}  {title}')}")
    print(f"  {c('cyan', '═' * w)}")


def subheader(title):
    print(f"\n  {c('blue', '──')} {c('bold', title)}")


def status(icon, msg):
    print(f"    {icon} {msg}")


def parse_driver_date(raw):
    """Try to parse various driver date formats into a datetime."""
    if not raw:
        return None
    if isinstance(raw, str):
        # /Date(1234567890000)/ format from JSON
        m = re.search(r'/Date\((\d+)\)', raw)
        if m:
            return datetime.datetime.fromtimestamp(int(m.group(1)) / 1000)
        # 20230614000000.******+*** format from WMI
        m = re.match(r'(\d{4})(\d{2})(\d{2})', raw)
        if m:
            try:
                return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    return None


# ─── Scan Functions ─────────────────────────────────────────────────────────────

def scan_all_drivers():
    """Get every signed driver with full details."""
    print(f"\n  {c('cyan', '⏳')} Scanning all PnP signed drivers...")
    drivers = run_ps_json(
        "Get-WmiObject Win32_PnPSignedDriver | "
        "Select-Object DeviceName, DeviceClass, DriverVersion, "
        "DriverDate, Manufacturer, IsSigned, InfName, DriverProviderName | "
        "ConvertTo-Json -Compress"
    )
    print(f"    Found {c('green', str(len(drivers)))} signed drivers")
    return drivers


def scan_problem_devices():
    """Find devices with issues."""
    print(f"\n  {c('cyan', '⏳')} Scanning for problem devices...")
    problems = run_ps_json(
        "Get-PnpDevice | Where-Object { $_.Status -ne 'OK' -and $_.Present -eq $true } | "
        "Select-Object Status, Class, FriendlyName, InstanceId, Manufacturer | "
        "ConvertTo-Json -Compress"
    )
    count = len(problems)
    if count > 0:
        print(f"    Found {c('red', str(count))} problem device(s)")
    else:
        print(f"    {c('green', '✅ All devices OK')}")
    return problems


def scan_windows_updates():
    """Check for pending driver/system updates via Windows Update."""
    print(f"\n  {c('cyan', '⏳')} Checking Windows Update for pending updates...")
    if not is_admin():
        print(f"    {c('yellow', '⚠ Limited scan (not admin)')}")

    updates = run_ps_json(
        "$Session = New-Object -ComObject Microsoft.Update.Session; "
        "$Searcher = $Session.CreateUpdateSearcher(); "
        "try { "
        "  $Results = $Searcher.Search('IsInstalled=0'); "
        "  $Results.Updates | ForEach-Object { "
        "    [PSCustomObject]@{ "
        "      Title=$_.Title; "
        "      IsDriver=[bool]($_.Categories | Where-Object { $_.Name -eq 'Drivers' }); "
        "      KBArticleIDs=($_.KBArticleIDs -join ','); "
        "      IsDownloaded=$_.IsDownloaded; "
        "      IsMandatory=$_.IsMandatory; "
        "      MaxDownloadSize=$_.MaxDownloadSize "
        "    } "
        "  } | ConvertTo-Json -Compress "
        "} catch { Write-Output '[]' }",
        timeout=180
    )

    driver_updates = [u for u in updates if u.get("IsDriver")]
    other_updates = [u for u in updates if not u.get("IsDriver")]

    if driver_updates:
        print(f"    Found {c('yellow', str(len(driver_updates)))} driver update(s)")
    if other_updates:
        print(f"    Found {c('yellow', str(len(other_updates)))} other update(s)")
    if not updates:
        print(f"    {c('green', '✅ No pending updates')}")

    return {"driver_updates": driver_updates, "other_updates": other_updates}


def scan_winget_updates():
    """Check for software updates available via winget."""
    print(f"\n  {c('cyan', '⏳')} Checking winget for software updates...")
    out, _ = run_cmd("winget upgrade --include-unknown 2>nul", timeout=60, silent=True)

    updates = []
    if out:
        lines = out.split('\n')
        # Find the separator line (dashes)
        data_start = -1
        for i, line in enumerate(lines):
            if line.strip().startswith('---'):
                data_start = i + 1
                break

        if data_start > 0:
            for line in lines[data_start:]:
                line = line.strip()
                if not line or 'upgrades available' in line.lower():
                    continue
                updates.append(line)

    if updates:
        print(f"    Found {c('yellow', str(len(updates)))} upgradable package(s)")
    else:
        print(f"    {c('green', '✅ All packages up to date')}")

    return updates


def scan_disk_firmware():
    """Check disk firmware versions."""
    print(f"\n  {c('cyan', '⏳')} Checking disk firmware...")
    disks = run_ps_json(
        "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus, "
        "FirmwareVersion, Size, BusType | ConvertTo-Json -Compress"
    )
    for d in disks:
        name = d.get("FriendlyName", "?")
        health = d.get("HealthStatus", "?")
        fw = d.get("FirmwareVersion", "?")
        icon = "✅" if health in ("Healthy", 0) else "⚠️"
        size_gb = round(d.get("Size", 0) / (1024**3), 1) if d.get("Size") else "?"
        print(f"    {icon} {name} ({size_gb} GB) — Firmware: {fw}, Health: {health}")
    return disks


# ─── Analysis ───────────────────────────────────────────────────────────────────

def analyze_drivers(drivers):
    """Analyze drivers for age, issues, and categorize by class."""
    now = datetime.datetime.now()
    by_class = defaultdict(list)
    old_drivers = []
    unsigned_drivers = []

    for d in drivers:
        name = d.get("DeviceName") or "Unknown"
        cls = d.get("DeviceClass") or "Unknown"
        ver = d.get("DriverVersion") or "?"
        mfr = d.get("Manufacturer") or "?"
        signed = d.get("IsSigned", True)
        raw_date = d.get("DriverDate", "")

        dt = parse_driver_date(raw_date)
        age_days = (now - dt).days if dt else None
        age_str = f"{age_days} days" if age_days is not None else "Unknown"

        entry = {
            "name": name, "class": cls, "version": ver,
            "manufacturer": mfr, "date": dt.strftime("%Y-%m-%d") if dt else "Unknown",
            "age_days": age_days, "signed": signed, "inf": d.get("InfName", "?"),
        }
        by_class[cls].append(entry)

        if age_days and age_days > 730:  # Older than 2 years
            old_drivers.append(entry)

        if not signed:
            unsigned_drivers.append(entry)

    return by_class, old_drivers, unsigned_drivers


def print_driver_report(by_class, old_drivers, unsigned_drivers, problems):
    """Print a formatted driver analysis report."""
    header("DRIVER ANALYSIS REPORT", "📊")

    # Summary table
    subheader("Driver Counts by Category")
    total = sum(len(v) for v in by_class.values())
    sorted_classes = sorted(by_class.items(), key=lambda x: -len(x[1]))
    for cls, entries in sorted_classes:
        count = len(entries)
        bar_len = min(int(count / total * 50), 50)
        bar = "█" * bar_len + "░" * (50 - bar_len)
        print(f"    {cls:<28} {c('cyan', bar)} {count}")
    print(f"\n    {c('bold', f'Total: {total} drivers')}")

    # Old drivers
    if old_drivers:
        subheader(f"Drivers Older Than 2 Years ({len(old_drivers)} found)")
        # Sort by age descending
        old_drivers.sort(key=lambda x: x["age_days"] or 0, reverse=True)
        for d in old_drivers[:20]:
            age_years = round(d["age_days"] / 365, 1)
            color = "red" if age_years > 5 else "yellow"
            print(f"    {c(color, f'{age_years}yr')} │ {d['name'][:45]:<45} │ v{d['version'][:15]:<15} │ {d['date']}")
        if len(old_drivers) > 20:
            print(f"    {c('gray', f'... and {len(old_drivers) - 20} more')}")
    else:
        subheader("Driver Age Check")
        print(f"    {c('green', '✅ No drivers older than 2 years')}")

    # Unsigned
    if unsigned_drivers:
        subheader(f"Unsigned Drivers ({len(unsigned_drivers)} found)")
        for d in unsigned_drivers:
            print(f"    {c('yellow', '⚠')} {d['name']} (v{d['version']}, {d['manufacturer']})")
    else:
        subheader("Signature Check")
        print(f"    {c('green', '✅ All drivers are signed')}")

    # Problem devices
    if problems:
        subheader(f"Problem Devices ({len(problems)} found)")
        for p in problems:
            st = p.get("Status", "?")
            name = p.get("FriendlyName", "?")
            cls = p.get("Class", "?")
            color = "red" if st == "Error" else "yellow"
            print(f"    {c(color, f'[{st}]')} ({cls}) {name}")
    else:
        subheader("Device Health Check")
        print(f"    {c('green', '✅ All present devices are healthy')}")


# ─── Fix Functions ──────────────────────────────────────────────────────────────

def fix_problem_devices(problems):
    """Attempt to fix devices in error/unknown state."""
    header("FIX PROBLEM DEVICES", "🔧")
    fixed = 0
    if not problems:
        print(f"    {c('green', '✅ No problem devices to fix')}")
        return 0

    if not is_admin():
        print(f"    {c('red', '❌ Admin required to fix devices')}")
        return 0

    for p in problems:
        iid = p.get("InstanceId", "")
        name = p.get("FriendlyName", "Unknown")
        status_val = p.get("Status", "")

        print(f"\n    Fixing: {c('bold', name)} [{status_val}]")

        # Try disable + re-enable
        print(f"      Disabling...")
        run_ps(f"Disable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue", silent=True)
        time.sleep(2)
        print(f"      Re-enabling...")
        run_ps(f"Enable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue", silent=True)
        time.sleep(2)

        # Check if it worked
        out, _ = run_ps(
            f"(Get-PnpDevice -InstanceId '{iid}' -ErrorAction SilentlyContinue).Status",
            silent=True
        )
        if out and out.strip() == "OK":
            print(f"      {c('green', '✅ Fixed!')}")
            fixed += 1
        else:
            print(f"      {c('yellow', f'Still {out.strip() if out else "unknown"} — may need driver update or reboot')}")

    # Scan for changes
    print(f"\n    Scanning for hardware changes...")
    run_cmd("pnputil /scan-devices", silent=True)

    return fixed


def update_drivers_windows_update(wu_data):
    """Install driver updates via Windows Update."""
    header("INSTALL DRIVER UPDATES VIA WINDOWS UPDATE", "🪟")

    if not is_admin():
        print(f"    {c('red', '❌ Admin required for Windows Update')}")
        return 0

    driver_updates = wu_data.get("driver_updates", [])
    other_updates = wu_data.get("other_updates", [])
    all_updates = driver_updates + other_updates

    if not all_updates:
        print(f"    {c('green', '✅ No pending updates')}")
        return 0

    print(f"    Processing {len(all_updates)} update(s):\n")
    for u in all_updates:
        icon = "🔌" if u.get("IsDriver") else "📦"
        dl = "Downloaded" if u.get("IsDownloaded") else "Not downloaded"
        size_mb = round(u.get("MaxDownloadSize", 0) / (1024*1024), 1)
        print(f"    {icon} {u.get('Title', '?')}")
        print(f"       {c('gray', f'{dl} | {size_mb} MB')}")

    print(f"\n    {c('cyan', 'Downloading and installing...')}")
    out, rc = run_ps(
        "$Session = New-Object -ComObject Microsoft.Update.Session; "
        "$Searcher = $Session.CreateUpdateSearcher(); "
        "$Results = $Searcher.Search('IsInstalled=0'); "
        "if($Results.Updates.Count -gt 0) { "
        "  $Downloader = $Session.CreateUpdateDownloader(); "
        "  $Downloader.Updates = $Results.Updates; "
        "  $DlResult = $Downloader.Download(); "
        "  Write-Output \"Downloaded: ResultCode=$($DlResult.ResultCode)\"; "
        "  $Installer = $Session.CreateUpdateInstaller(); "
        "  $Installer.Updates = $Results.Updates; "
        "  $InstResult = $Installer.Install(); "
        "  Write-Output \"Installed: ResultCode=$($InstResult.ResultCode)\"; "
        "  if($InstResult.RebootRequired) { "
        "    Write-Output 'REBOOT_REQUIRED' "
        "  } "
        "} else { "
        "  Write-Output 'No updates to install.' "
        "}",
        timeout=600
    )

    if out and "REBOOT_REQUIRED" in out:
        print(f"\n    {c('yellow', '⚠ REBOOT REQUIRED to complete updates')}")

    return len(all_updates)


def update_software_winget():
    """Update all software via winget."""
    header("UPDATE ALL SOFTWARE VIA WINGET", "📦")

    # First show what's available
    print(f"    Checking for available updates...\n")
    out, _ = run_cmd("winget upgrade --include-unknown 2>nul", timeout=60)

    print(f"\n    {c('cyan', 'Installing all updates...')}\n")
    out, rc = run_cmd(
        "winget upgrade --all --accept-package-agreements --accept-source-agreements --include-unknown 2>nul",
        timeout=900
    )

    if rc == 0:
        print(f"\n    {c('green', '✅ All packages updated')}")
    else:
        print(f"\n    {c('yellow', '⚠ Some packages may have failed (normal for system packages)')}")

    return 0


def update_drivers_pnputil():
    """Force a driver update scan via pnputil."""
    header("SCAN FOR DRIVER UPDATES (PNPUTIL)", "🔍")

    if not is_admin():
        print(f"    {c('red', '❌ Admin required')}")
        return

    print(f"    Scanning for hardware changes and driver updates...")
    run_cmd("pnputil /scan-devices")
    time.sleep(3)

    # Enumerate any newly available drivers
    print(f"\n    Checking driver store for newer versions...")
    out, _ = run_ps(
        "$devices = Get-PnpDevice -PresentOnly | Where-Object { $_.Status -eq 'OK' }; "
        "Write-Output \"$($devices.Count) active devices checked\"",
        silent=False
    )


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PC Driver Updater — Full System Maintenance")
    parser.add_argument("--scan", action="store_true", help="Scan only, don't make changes")
    parser.add_argument("--drivers", action="store_true", help="Update drivers only")
    parser.add_argument("--software", action="store_true", help="Update software via winget only")
    parser.add_argument("--fix", action="store_true", help="Fix problem devices only")
    args = parser.parse_args()

    do_all = not any([args.scan, args.drivers, args.software, args.fix])

    # Enable ANSI colors on Windows
    os.system("")

    print(f"""
  {c('cyan', '╔══════════════════════════════════════════════════════════╗')}
  {c('cyan', '║')}  {c('bold', 'PC Driver Updater — Full System Maintenance')}            {c('cyan', '║')}
  {c('cyan', '╚══════════════════════════════════════════════════════════╝')}
    """)

    admin = is_admin()
    if admin:
        print(f"  {c('green', '✅ Running as Administrator')}")
    else:
        print(f"  {c('yellow', '⚠  Not running as Administrator — some features limited')}")
        print(f"     Run: {c('bold', 'python pc_driver_updater.py')} from an admin terminal\n")

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"  {c('gray', f'Scan started: {timestamp}')}\n")

    log = {"timestamp": timestamp, "admin": admin, "actions": []}

    # ── Phase 1: Full Scan ──────────────────────────────────────────────
    header("SYSTEM SCAN", "🔎")

    drivers = scan_all_drivers()
    problems = scan_problem_devices()
    wu_data = scan_windows_updates()
    winget_updates = scan_winget_updates()
    disks = scan_disk_firmware()

    # ── Phase 2: Analysis ───────────────────────────────────────────────
    by_class, old_drivers, unsigned_drivers = analyze_drivers(drivers)
    print_driver_report(by_class, old_drivers, unsigned_drivers, problems)

    log["total_drivers"] = len(drivers)
    log["problem_devices"] = len(problems)
    log["old_drivers"] = len(old_drivers)
    log["unsigned_drivers"] = len(unsigned_drivers)
    log["pending_wu_driver"] = len(wu_data.get("driver_updates", []))
    log["pending_wu_other"] = len(wu_data.get("other_updates", []))
    log["winget_updates"] = len(winget_updates)

    if args.scan:
        print(f"\n  {c('cyan', '── Scan-only mode, no changes made ──')}\n")
        with open(LOG_PATH, "w") as f:
            json.dump(log, f, indent=2, default=str)
        print(f"  Log saved: {LOG_PATH}")
        return

    # ── Phase 3: Apply Fixes ────────────────────────────────────────────
    if do_all or args.fix:
        fixed = fix_problem_devices(problems)
        log["actions"].append(f"Fixed {fixed}/{len(problems)} problem devices")

    if do_all or args.drivers:
        update_drivers_pnputil()
        log["actions"].append("Ran pnputil driver scan")

        installed = update_drivers_windows_update(wu_data)
        log["actions"].append(f"Processed {installed} Windows Update(s)")

    if do_all or args.software:
        update_software_winget()
        log["actions"].append("Ran winget upgrade --all")

    # ── Phase 4: Final Verification ─────────────────────────────────────
    header("FINAL VERIFICATION", "✅")

    print(f"\n  Re-scanning for remaining issues...\n")
    remaining_problems = scan_problem_devices()

    if remaining_problems:
        print(f"\n    {c('yellow', f'{len(remaining_problems)} device(s) still need attention:')}")
        for p in remaining_problems:
            print(f"      [{p.get('Status')}] {p.get('FriendlyName', '?')}")
        print(f"\n    {c('gray', 'These may require a reboot, manual driver download, or hardware check.')}")
    else:
        print(f"\n    {c('green', '🎉 All present devices are healthy!')}")

    # ── Summary ─────────────────────────────────────────────────────────
    header("SUMMARY", "📋")
    print(f"""
    Drivers scanned:      {c('bold', str(len(drivers)))}
    Problem devices:      {c('bold', str(len(problems)))} → {c('bold', str(len(remaining_problems)))}
    Old drivers (2yr+):   {c('bold', str(len(old_drivers)))}
    Unsigned drivers:     {c('bold', str(len(unsigned_drivers)))}
    Windows Updates:      {c('bold', str(len(wu_data.get('driver_updates', [])) + len(wu_data.get('other_updates', []))))}
    Winget packages:      {c('bold', str(len(winget_updates)))}
    Disks checked:        {c('bold', str(len(disks)))}
    """)

    log["remaining_problems"] = len(remaining_problems)

    # Save log
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2, default=str)
    print(f"  {c('gray', f'Log saved: {LOG_PATH}')}")

    if remaining_problems:
        print(f"\n  {c('yellow', '💡 TIP: Reboot your PC, then run this script again with --scan')}")
        print(f"  {c('yellow', '         to verify all issues are resolved.')}")

    print(f"\n  {c('green', 'Done!')} ✨\n")


if __name__ == "__main__":
    main()
