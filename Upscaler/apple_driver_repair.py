"""
Apple iPhone USB Driver Repair — Deep Fix
==========================================
This script performs a thorough repair of Apple iPhone USB connectivity:
1. Removes old/broken Apple USB drivers from the driver store
2. Resets Apple Devices app (forces driver reinstall)
3. Re-scans for devices with fresh drivers
4. Verifies connectivity

MUST be run as Administrator:
  python apple_driver_repair.py
"""

import subprocess
import sys
import os
import ctypes
import time
import json


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run(cmd, shell=True, timeout=120):
    """Run command, print output, return (stdout, returncode)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=shell)
        out = r.stdout.strip()
        err = r.stderr.strip()
        if out:
            for line in out.split('\n'):
                print(f"    {line}")
        if err and r.returncode != 0:
            for line in err.split('\n'):
                print(f"    ⚠ {line}")
        return out, r.returncode
    except Exception as e:
        print(f"    ❌ {e}")
        return str(e), -1


def run_ps(cmd, timeout=120):
    """Run PowerShell command."""
    return run(["powershell", "-NoProfile", "-Command", cmd], shell=False, timeout=timeout)


def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║   Apple iPhone USB Driver Repair — Deep Fix             ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    if not is_admin():
        print("  ❌ This script MUST be run as Administrator!")
        print("     Right-click PowerShell → Run as Administrator")
        print("     Then: python apple_driver_repair.py")
        sys.exit(1)

    print("  ✅ Running as Administrator\n")

    # ── Step 1: Show current Apple device state ─────────────────────────
    print("═" * 60)
    print("  STEP 1: Current Apple Device Status")
    print("═" * 60)
    run_ps(
        "Get-PnpDevice | Where-Object { $_.FriendlyName -match 'Apple|iPhone' } | "
        "Select-Object Status, FriendlyName, InstanceId | Format-Table -AutoSize | Out-String -Width 200"
    )

    # ── Step 2: List current Apple drivers in store ─────────────────────
    print("\n" + "═" * 60)
    print("  STEP 2: Apple Drivers in Driver Store")
    print("═" * 60)
    out, _ = run(["pnputil", "/enum-drivers"], shell=False)

    # Parse out Apple driver OEM names
    apple_oems = []
    if out:
        lines = out.split('\n')
        current_oem = None
        for i, line in enumerate(lines):
            if "Published Name:" in line:
                current_oem = line.split(":")[-1].strip()
            if "Apple" in line and current_oem:
                apple_oems.append(current_oem)
                current_oem = None

    print(f"\n    Found {len(apple_oems)} Apple driver package(s): {apple_oems}")

    # ── Step 3: Disable all Apple USB devices ───────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 3: Disabling All Apple USB Devices")
    print("═" * 60)
    run_ps(
        "$devs = Get-PnpDevice | Where-Object { $_.FriendlyName -match 'Apple|iPhone' -and $_.Present -eq $true }; "
        "foreach($d in $devs) { "
        "  Write-Output \"  Disabling: $($d.FriendlyName)\"; "
        "  Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction SilentlyContinue "
        "}"
    )
    time.sleep(3)

    # ── Step 4: Delete old Apple drivers from driver store ──────────────
    print("\n" + "═" * 60)
    print("  STEP 4: Removing Old Apple Drivers from Driver Store")
    print("═" * 60)
    for oem in apple_oems:
        print(f"\n    Deleting driver package: {oem}")
        run(["pnputil", "/delete-driver", oem, "/force"], shell=False)
        time.sleep(1)

    # ── Step 5: Remove and reinstall Apple Devices from Store ───────────
    print("\n" + "═" * 60)
    print("  STEP 5: Resetting Apple Devices App (Forces Driver Reinstall)")
    print("═" * 60)

    # Reset the Apple Devices app (clears cache, forces driver re-extraction)
    print("\n  Resetting Apple Devices app data...")
    run_ps(
        "Get-AppxPackage -Name 'AppleInc.AppleDevices' | "
        "ForEach-Object { "
        "  $pkg = $_.PackageFullName; "
        "  Write-Output \"  Resetting: $pkg\"; "
        "  wsreset.exe -i $pkg 2>$null; "  
        "}"
    )

    # Use PowerShell to reset the app
    print("\n  Forcing app reset via wsreset...")
    run(["powershell", "-Command",
         "Get-AppxPackage AppleInc.AppleDevices | Remove-AppxPackage -ErrorAction SilentlyContinue"],
        shell=False, timeout=60)
    time.sleep(3)

    # Reinstall from Store
    print("\n  Reinstalling Apple Devices from Microsoft Store...")
    run(["winget", "install", "--id", "9NP83LWLPZ9K",
         "--accept-package-agreements", "--accept-source-agreements",
         "--force", "--silent"], shell=False, timeout=300)
    time.sleep(5)

    # Also reinstall iTunes
    print("\n  Reinstalling iTunes from Microsoft Store...")
    run(["winget", "install", "--id", "9PB2MZ1ZMB1S",
         "--accept-package-agreements", "--accept-source-agreements",
         "--force", "--silent"], shell=False, timeout=300)
    time.sleep(5)

    # ── Step 6: Scan for hardware changes ───────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 6: Scanning for Hardware Changes (Triggers Driver Install)")
    print("═" * 60)
    run(["pnputil", "/scan-devices"], shell=False)
    time.sleep(5)

    # ── Step 7: Re-enable Apple devices ─────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 7: Re-enabling All Apple USB Devices")
    print("═" * 60)
    run_ps(
        "$devs = Get-PnpDevice | Where-Object { $_.FriendlyName -match 'Apple|iPhone' }; "
        "foreach($d in $devs) { "
        "  Write-Output \"  Enabling: $($d.FriendlyName)\"; "
        "  Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction SilentlyContinue "
        "}"
    )
    time.sleep(5)

    # ── Step 8: Another hardware scan ───────────────────────────────────
    print("\n  Running final device scan...")
    run(["pnputil", "/scan-devices"], shell=False)
    time.sleep(5)

    # ── Step 9: Verify ──────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 8: VERIFICATION — Final Apple Device Status")
    print("═" * 60)
    run_ps(
        "Get-PnpDevice | Where-Object { $_.FriendlyName -match 'Apple|iPhone' } | "
        "Select-Object Status, FriendlyName | Format-Table -AutoSize"
    )

    # Check Apple driver versions
    print("\n  Apple Driver Versions:")
    run_ps(
        "Get-WmiObject Win32_PnPSignedDriver | Where-Object { $_.Manufacturer -match 'Apple' } | "
        "Select-Object DeviceName, DriverVersion, DriverDate | Format-Table -AutoSize"
    )

    # Check services
    print("\n  Apple Services:")
    run_ps(
        "Get-Service | Where-Object { $_.DisplayName -match 'Apple|Bonjour|Mobile Device' } | "
        "Format-Table Name, DisplayName, Status -AutoSize"
    )

    print("\n" + "═" * 60)
    print("  NEXT STEPS:")
    print("═" * 60)
    print("  1. Unplug your iPhone USB-C cable")
    print("  2. Wait 10 seconds")
    print("  3. Plug it back in")
    print("  4. On your iPhone, tap 'Trust This Computer' if prompted")
    print("  5. Open the 'Apple Devices' app from Start Menu")
    print("  6. If still not working, REBOOT your PC and try again")
    print("     (Windows Update also requested a reboot)")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
