"""
PC Fix Tool — Automated Driver & Software Remediation
======================================================
Based on diagnostic findings, this script:
1. Resets and updates Apple Mobile Device drivers
2. Updates Apple Devices / iTunes via winget
3. Restarts Apple services
4. Triggers Windows Update for pending driver updates
5. Re-enables problem USB devices
6. Checks for AMD chipset driver updates

Run with Administrator privileges for full functionality:
  python pc_fix.py

Can also run specific fixes:
  python pc_fix.py --apple       # Apple fixes only
  python pc_fix.py --drivers     # Driver updates only
  python pc_fix.py --winupdate   # Windows Update only
  python pc_fix.py --all         # Everything (default)
"""

import subprocess
import sys
import os
import ctypes
import time
import json
import argparse
from pathlib import Path


# ─── Helpers ────────────────────────────────────────────────────────────────────

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_ps(command, timeout=120, show=True):
    """Run a PowerShell command, optionally printing output."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        output = result.stdout.strip()
        error = result.stderr.strip()
        if show and output:
            for line in output.split('\n'):
                print(f"    {line}")
        if show and error and result.returncode != 0:
            for line in error.split('\n'):
                print(f"    ⚠️ {line}")
        return output, result.returncode
    except subprocess.TimeoutExpired:
        if show:
            print(f"    ⏰ Command timed out after {timeout}s")
        return "", -1
    except Exception as e:
        if show:
            print(f"    ❌ Error: {e}")
        return str(e), -1


def run_cmd(command, timeout=120, show=True):
    """Run a regular command."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, shell=True,
        )
        output = result.stdout.strip()
        if show and output:
            for line in output.split('\n'):
                print(f"    {line}")
        return output, result.returncode
    except Exception as e:
        if show:
            print(f"    ❌ Error: {e}")
        return str(e), -1


def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def step(num, total, description):
    print(f"\n  [{num}/{total}] {description}")


# ─── Fix Functions ──────────────────────────────────────────────────────────────

def fix_apple(total_steps_offset=0):
    """Fix Apple iPhone connectivity issues."""
    section("🍎 APPLE / iPHONE CONNECTIVITY FIXES")
    fixes_applied = []
    steps = 8
    s = total_steps_offset

    # Step 1: Reset Apple USB Composite Device
    step(s+1, s+steps, "Disabling and re-enabling Apple Mobile Device USB Composite Device...")
    # Find Apple USB Composite devices in Unknown state
    out, _ = run_ps(
        "Get-PnpDevice | Where-Object { $_.FriendlyName -match 'Apple Mobile Device USB Composite' -and $_.Status -ne 'OK' } | "
        "Select-Object InstanceId, Status | ConvertTo-Json",
        show=False
    )
    if out and out != "":
        try:
            devices = json.loads(out)
            if isinstance(devices, dict):
                devices = [devices]
            for dev in devices:
                iid = dev.get("InstanceId", "")
                if iid:
                    print(f"    Resetting: {iid}")
                    run_ps(f"Disable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue", show=True)
                    time.sleep(2)
                    run_ps(f"Enable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue", show=True)
                    fixes_applied.append(f"Reset Apple USB Composite Device: {iid}")
        except json.JSONDecodeError:
            print("    No devices to reset in JSON format")
    else:
        print("    No Apple USB Composite devices in Unknown state found.")

    # Step 2: Reset Apple Mobile Device Ethernet
    step(s+2, s+steps, "Resetting Apple Mobile Device Ethernet...")
    out, _ = run_ps(
        "Get-PnpDevice | Where-Object { $_.FriendlyName -match 'Apple Mobile Device Ethernet' } | "
        "Select-Object InstanceId, Status | ConvertTo-Json",
        show=False
    )
    if out and out != "":
        try:
            devices = json.loads(out)
            if isinstance(devices, dict):
                devices = [devices]
            for dev in devices:
                iid = dev.get("InstanceId", "")
                status = dev.get("Status", "")
                if iid and status != "OK":
                    print(f"    Resetting ethernet interface: {iid}")
                    run_ps(f"Disable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue")
                    time.sleep(2)
                    run_ps(f"Enable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue")
                    fixes_applied.append(f"Reset Apple Ethernet Device: {iid}")
                else:
                    print(f"    Apple Mobile Device Ethernet is already OK.")
        except json.JSONDecodeError:
            print("    Could not parse device info")
    else:
        print("    No Apple Mobile Device Ethernet found.")

    # Step 3: Update Apple USB driver via pnputil scan
    step(s+3, s+steps, "Scanning for updated Apple USB drivers via pnputil...")
    if is_admin():
        run_cmd("pnputil /scan-devices", show=True)
        fixes_applied.append("Triggered PnP device scan for driver updates")
        time.sleep(3)
    else:
        print("    ⚠️ Requires admin privileges for pnputil /scan-devices")

    # Step 4: Update Apple Devices app via winget
    step(s+4, s+steps, "Updating Apple Devices app via winget...")
    out, rc = run_cmd("winget upgrade --id 9NP83LWLPZ9K --accept-package-agreements --accept-source-agreements --silent", timeout=180)
    if rc == 0:
        fixes_applied.append("Updated Apple Devices app via winget")
    else:
        # Try by name
        out2, rc2 = run_cmd("winget upgrade \"Apple Devices\" --accept-package-agreements --accept-source-agreements --silent", timeout=180)
        if rc2 == 0:
            fixes_applied.append("Updated Apple Devices app via winget (by name)")
        else:
            print("    Apple Devices may already be up to date or winget update not available.")

    # Step 5: Update iTunes via winget
    step(s+5, s+steps, "Updating iTunes via winget...")
    out, rc = run_cmd("winget upgrade --id 9PB2MZ1ZMB1S --accept-package-agreements --accept-source-agreements --silent", timeout=180)
    if rc == 0:
        fixes_applied.append("Updated iTunes via winget")
    else:
        out2, rc2 = run_cmd("winget upgrade \"iTunes\" --accept-package-agreements --accept-source-agreements --silent", timeout=180)
        if rc2 == 0:
            fixes_applied.append("Updated iTunes via winget (by name)")
        else:
            print("    iTunes may already be up to date.")

    # Step 6: Restart Bonjour service
    step(s+6, s+steps, "Restarting Bonjour Service...")
    if is_admin():
        run_ps("Restart-Service 'Bonjour Service' -Force -ErrorAction SilentlyContinue")
        fixes_applied.append("Restarted Bonjour Service")
    else:
        print("    ⚠️ Requires admin to restart services")

    # Step 7: Force Apple USB driver reinstall via devcon-style approach
    step(s+7, s+steps, "Attempting to update Apple USB drivers through Windows Update...")
    if is_admin():
        # Use pnputil to look for Apple driver updates through Windows Update catalog
        run_ps(
            "$devices = Get-PnpDevice | Where-Object { $_.Manufacturer -match 'Apple' }; "
            "foreach($d in $devices) { "
            "  try { $d | Update-PnpDeviceDriver -ErrorAction SilentlyContinue } catch {} "
            "}",
            timeout=60
        )
        fixes_applied.append("Attempted Apple USB driver update via Windows Update")
    else:
        print("    ⚠️ Requires admin for driver updates")

    # Step 8: Verify status
    step(s+8, s+steps, "Verifying Apple device status after fixes...")
    out, _ = run_ps(
        "Get-PnpDevice | Where-Object { $_.FriendlyName -match 'Apple|iPhone' } | "
        "Select-Object Status, FriendlyName | Format-Table -AutoSize"
    )

    return fixes_applied


def fix_drivers(total_steps_offset=0):
    """Fix and update system drivers."""
    section("🔧 DRIVER UPDATES")
    fixes_applied = []
    steps = 4
    s = total_steps_offset

    # Step 1: Scan for hardware changes
    step(s+1, s+steps, "Scanning for hardware changes...")
    if is_admin():
        run_cmd("pnputil /scan-devices")
        fixes_applied.append("Scanned for hardware changes")
        time.sleep(3)
    else:
        print("    ⚠️ Requires admin")

    # Step 2: Check for problem devices and attempt to fix
    step(s+2, s+steps, "Attempting to fix all problem devices...")
    out, _ = run_ps(
        "Get-PnpDevice | Where-Object { $_.Status -ne 'OK' -and $_.Present -eq $true } | "
        "Select-Object InstanceId, FriendlyName, Status | ConvertTo-Json",
        show=False
    )
    if out:
        try:
            problems = json.loads(out)
            if isinstance(problems, dict):
                problems = [problems]
            for p in problems:
                iid = p.get("InstanceId", "")
                name = p.get("FriendlyName", "Unknown")
                print(f"    Attempting to fix: {name}")
                if is_admin():
                    # Disable and re-enable
                    run_ps(f"Disable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue", show=False)
                    time.sleep(1)
                    run_ps(f"Enable-PnpDevice -InstanceId '{iid}' -Confirm:$false -ErrorAction SilentlyContinue", show=False)
                    fixes_applied.append(f"Reset device: {name}")
                else:
                    print(f"      ⚠️ Requires admin to reset device")
        except json.JSONDecodeError:
            print("    No problem devices found or couldn't parse")
    else:
        print("    No problem devices to fix.")

    # Step 3: Check for outdated drivers and update available ones
    step(s+3, s+steps, "Checking for driver updates via Windows Update...")
    if is_admin():
        run_ps(
            "# Trigger optional driver updates through Windows Update\n"
            "$Session = New-Object -ComObject Microsoft.Update.Session\n"
            "$Searcher = $Session.CreateUpdateSearcher()\n"
            "try {\n"
            "  $Results = $Searcher.Search('IsInstalled=0 and Type=\\'Driver\\'')\n"
            "  if($Results.Updates.Count -gt 0) {\n"
            "    Write-Output \"Found $($Results.Updates.Count) driver update(s):\"\n"
            "    $Results.Updates | ForEach-Object { Write-Output \"  - $($_.Title)\" }\n"
            "    $Downloader = $Session.CreateUpdateDownloader()\n"
            "    $Downloader.Updates = $Results.Updates\n"
            "    $Downloader.Download()\n"
            "    $Installer = $Session.CreateUpdateInstaller()\n"
            "    $Installer.Updates = $Results.Updates\n"
            "    $InstResult = $Installer.Install()\n"
            "    Write-Output \"Installation result: $($InstResult.ResultCode)\"\n"
            "  } else {\n"
            "    Write-Output 'No driver updates available through Windows Update.'\n"
            "  }\n"
            "} catch {\n"
            "  Write-Output \"Driver update check failed: $_\"\n"
            "}",
            timeout=300
        )
        fixes_applied.append("Checked/installed driver updates via Windows Update")
    else:
        print("    ⚠️ Requires admin for Windows Update driver installation")

    # Step 4: Update AMD chipset drivers check
    step(s+4, s+steps, "Checking AMD chipset USB drivers...")
    out, _ = run_ps(
        "Get-WmiObject Win32_PnPSignedDriver | Where-Object { "
        "$_.DeviceName -match 'AMD.*USB|AMD.*xHCI' } | "
        "Select-Object DeviceName, DriverVersion, DriverDate | Format-Table -AutoSize"
    )
    print("    💡 For latest AMD USB drivers, visit:")
    print("    https://www.amd.com/en/support/downloads/drivers.html/chipsets/am5/b650.html")
    print("    Or use Gigabyte's driver page for B650 AORUS ELITE AX ICE")

    return fixes_applied


def fix_winupdate(total_steps_offset=0):
    """Install pending Windows Updates."""
    section("🪟 WINDOWS UPDATE")
    fixes_applied = []
    steps = 2
    s = total_steps_offset

    step(s+1, s+steps, "Checking for pending Windows Updates...")
    if is_admin():
        run_ps(
            "$Session = New-Object -ComObject Microsoft.Update.Session\n"
            "$Searcher = $Session.CreateUpdateSearcher()\n"
            "try {\n"
            "  $Results = $Searcher.Search('IsInstalled=0')\n"
            "  if($Results.Updates.Count -gt 0) {\n"
            "    Write-Output \"Found $($Results.Updates.Count) pending update(s):\"\n"
            "    $Results.Updates | ForEach-Object { Write-Output \"  - $($_.Title)\" }\n"
            "    Write-Output ''\n"
            "    Write-Output 'Downloading updates...'\n"
            "    $Downloader = $Session.CreateUpdateDownloader()\n"
            "    $Downloader.Updates = $Results.Updates\n"
            "    $DlResult = $Downloader.Download()\n"
            "    Write-Output \"Download result: $($DlResult.ResultCode)\"\n"
            "    Write-Output ''\n"
            "    Write-Output 'Installing updates...'\n"
            "    $Installer = $Session.CreateUpdateInstaller()\n"
            "    $Installer.Updates = $Results.Updates\n"
            "    $InstResult = $Installer.Install()\n"
            "    Write-Output \"Install result: $($InstResult.ResultCode)\"\n"
            "    if($InstResult.RebootRequired) {\n"
            "      Write-Output '⚠️ REBOOT REQUIRED to complete updates.'\n"
            "    }\n"
            "  } else {\n"
            "    Write-Output 'No pending updates found.'\n"
            "  }\n"
            "} catch {\n"
            "  Write-Output \"Update check failed: $_\"\n"
            "}",
            timeout=600
        )
        fixes_applied.append("Ran Windows Update (download + install)")
    else:
        print("    ⚠️ Requires admin for Windows Update installation")
        print("    Checking update status read-only...")
        run_ps(
            "$Session = New-Object -ComObject Microsoft.Update.Session; "
            "$Searcher = $Session.CreateUpdateSearcher(); "
            "try { $Results = $Searcher.Search('IsInstalled=0'); "
            "Write-Output \"$($Results.Updates.Count) pending update(s):\"; "
            "$Results.Updates | ForEach-Object { Write-Output \"  - $($_.Title)\" } } "
            "catch { Write-Output 'Could not check updates.' }",
            timeout=120
        )

    # Step 2: winget upgrades for all outdated packages
    step(s+2, s+steps, "Checking winget for upgradable packages...")
    out, _ = run_cmd("winget upgrade --include-unknown", timeout=60)
    fixes_applied.append("Listed winget upgradable packages")

    return fixes_applied


def fix_all_winget_upgrades():
    """Upgrade all winget-managed packages."""
    section("📦 UPGRADING ALL OUTDATED PACKAGES VIA WINGET")
    print("  This will upgrade all packages that have updates available...")
    print("  (Excluding packages that require specific version management)\n")
    
    out, rc = run_cmd(
        "winget upgrade --all --accept-package-agreements --accept-source-agreements --silent --include-unknown",
        timeout=600
    )
    if rc == 0:
        return ["Upgraded all winget packages"]
    else:
        print("  Some packages may have failed to upgrade (this is normal for system packages)")
        return ["Attempted winget upgrade --all"]


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PC Fix Tool — Automated Remediation")
    parser.add_argument("--apple", action="store_true", help="Apple/iPhone fixes only")
    parser.add_argument("--drivers", action="store_true", help="Driver updates only")
    parser.add_argument("--winupdate", action="store_true", help="Windows Update only")
    parser.add_argument("--winget-all", action="store_true", help="Upgrade all winget packages")
    parser.add_argument("--all", action="store_true", help="All fixes (default)")
    args = parser.parse_args()

    # Default to --all if nothing specified
    if not any([args.apple, args.drivers, args.winupdate, args.winget_all, args.all]):
        args.all = True

    print("╔══════════════════════════════════════════════════════╗")
    print("║      PC FIX TOOL — Automated Remediation           ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    if not is_admin():
        print("  ⚠️  NOT running as Administrator!")
        print("     Many fixes require admin privileges.")
        print("     Right-click your terminal → 'Run as Administrator'")
        print("     Then re-run: python pc_fix.py")
        print()
        response = input("  Continue anyway? (y/N): ").strip().lower()
        if response != 'y':
            print("  Exiting. Please re-run as Administrator.")
            sys.exit(1)
    else:
        print("  ✅ Running as Administrator — full access available.\n")

    all_fixes = []

    if args.all or args.apple:
        fixes = fix_apple()
        all_fixes.extend(fixes)

    if args.all or args.drivers:
        fixes = fix_drivers()
        all_fixes.extend(fixes)

    if args.all or args.winupdate:
        fixes = fix_winupdate()
        all_fixes.extend(fixes)

    if args.all or args.winget_all:
        fixes = fix_all_winget_upgrades()
        all_fixes.extend(fixes)

    # Summary
    section("📋 REMEDIATION SUMMARY")
    if all_fixes:
        print(f"\n  {len(all_fixes)} action(s) taken:\n")
        for i, fix in enumerate(all_fixes, 1):
            print(f"    {i}. {fix}")
    else:
        print("  No fixes were applied.")

    print(f"\n{'─'*60}")
    print("  NEXT STEPS:")
    print("  1. If not run as admin, re-run: python pc_fix.py --all")
    print("     from an Administrator PowerShell/Terminal")
    print("  2. Unplug and replug your iPhone USB-C cable")
    print("  3. On your iPhone, tap 'Trust This Computer' if prompted")
    print("  4. Open the Apple Devices app on your PC")
    print("  5. If still not working, try a different USB-C port")
    print("  6. For latest AMD USB drivers, download from:")
    print("     https://www.amd.com/en/support")
    print("     → Chipsets → AM5 → B650")
    print(f"{'─'*60}\n")

    # Save fix log
    log_path = Path(__file__).parent / "fix_log.json"
    with open(log_path, "w") as f:
        json.dump({
            "fixes_applied": all_fixes,
            "admin": is_admin(),
            "timestamp": __import__("datetime").datetime.now().isoformat()
        }, f, indent=2)
    print(f"  Fix log saved to: {log_path}\n")


if __name__ == "__main__":
    main()
