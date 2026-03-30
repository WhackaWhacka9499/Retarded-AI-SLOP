"""
PC Diagnostic Tool — Full System Driver, Program, and Device Analysis
=====================================================================
Scans every driver, program, service, USB device, disk, and Windows component.
Outputs a comprehensive JSON report + human-readable summary.
Run with: python pc_diagnostic.py
For elevated scans: Run from an Administrator terminal.
"""

import subprocess
import json
import re
import sys
import os
import platform
import datetime
import ctypes
from pathlib import Path

REPORT_DIR = Path(__file__).parent
REPORT_JSON = REPORT_DIR / "diagnostic_report.json"
REPORT_TXT  = REPORT_DIR / "diagnostic_report.txt"


# ─── Helpers ────────────────────────────────────────────────────────────────────

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_ps(command, timeout=60):
    """Run a PowerShell command and return stripped stdout."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def run_ps_json(command, timeout=60):
    """Run a PowerShell command that produces JSON output."""
    raw = run_ps(command, timeout)
    if raw.startswith("ERROR:") or not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return [data]
        return data
    except json.JSONDecodeError:
        return []


def section_header(title):
    w = 80
    return f"\n{'='*w}\n  {title}\n{'='*w}"


# ─── Data Collection Functions ──────────────────────────────────────────────────

def get_system_info():
    """Basic system information."""
    print("  [1/12] System Information...")
    info = {
        "hostname": platform.node(),
        "os": platform.platform(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "is_admin": is_admin(),
        "scan_time": datetime.datetime.now().isoformat(),
    }
    # Get detailed info via WMI
    extra = run_ps_json(
        "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, "
        "SystemType, TotalPhysicalMemory | ConvertTo-Json"
    )
    if extra:
        e = extra[0]
        info["manufacturer"] = e.get("Manufacturer", "Unknown")
        info["model"] = e.get("Model", "Unknown")
        info["system_type"] = e.get("SystemType", "Unknown")
        mem_bytes = e.get("TotalPhysicalMemory", 0)
        info["total_ram_gb"] = round(mem_bytes / (1024**3), 1) if mem_bytes else "Unknown"

    # BIOS/Motherboard
    bios = run_ps_json(
        "Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion, Manufacturer, "
        "ReleaseDate, SerialNumber | ConvertTo-Json"
    )
    if bios:
        b = bios[0]
        info["bios_version"] = b.get("SMBIOSBIOSVersion", "Unknown")
        info["bios_manufacturer"] = b.get("Manufacturer", "Unknown")

    board = run_ps_json(
        "Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product, Version | ConvertTo-Json"
    )
    if board:
        bb = board[0]
        info["motherboard"] = f"{bb.get('Manufacturer','')} {bb.get('Product','')} {bb.get('Version','')}"

    return info


def get_all_drivers():
    """Get every single PnP signed driver on the system."""
    print("  [2/12] All PnP Signed Drivers...")
    drivers = run_ps_json(
        "Get-WmiObject Win32_PnPSignedDriver | "
        "Select-Object DeviceName, DeviceClass, DriverVersion, "
        "DriverDate, Manufacturer, IsSigned, InfName, DriverProviderName | "
        "ConvertTo-Json -Compress"
    )
    # Parse driver dates into readable format
    for d in drivers:
        raw_date = d.get("DriverDate", "")
        if raw_date and isinstance(raw_date, str) and raw_date.startswith("/Date("):
            try:
                ms = int(re.search(r'\d+', raw_date).group())
                d["DriverDate"] = datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
            except Exception:
                pass
        elif raw_date and len(raw_date) >= 8:
            try:
                d["DriverDate"] = datetime.datetime.strptime(raw_date[:8], "%Y%m%d").strftime("%Y-%m-%d")
            except Exception:
                pass
    return drivers


def get_all_pnp_devices():
    """Get every PnP device and its status."""
    print("  [3/12] All PnP Devices (including problem devices)...")
    devices = run_ps_json(
        "Get-PnpDevice | Select-Object Status, Class, FriendlyName, "
        "InstanceId, Manufacturer, Present | ConvertTo-Json -Compress"
    )
    return devices


def get_problem_devices():
    """Devices in Error, Degraded, or Unknown state."""
    print("  [4/12] Problem Devices...")
    problems = run_ps_json(
        "Get-PnpDevice | Where-Object { $_.Status -ne 'OK' -and $_.Present -eq $true } | "
        "Select-Object Status, Class, FriendlyName, InstanceId, Manufacturer | "
        "ConvertTo-Json -Compress"
    )
    return problems


def get_usb_devices():
    """Detailed USB device tree."""
    print("  [5/12] USB Device Tree...")
    usb = run_ps_json(
        "Get-PnpDevice | Where-Object { $_.Class -match 'USB|USBDevice' -or $_.InstanceId -match 'USB' } | "
        "Select-Object Status, Class, FriendlyName, InstanceId, Manufacturer, Present | "
        "ConvertTo-Json -Compress"
    )
    return usb


def get_installed_programs():
    """All installed programs from registry + Store apps."""
    print("  [6/12] Installed Programs (Registry)...")
    reg_paths = [
        r"HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        r"HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    ]
    all_programs = []
    for rp in reg_paths:
        progs = run_ps_json(
            f"Get-ItemProperty '{rp}' -ErrorAction SilentlyContinue | "
            "Where-Object { $_.DisplayName -ne $null } | "
            "Select-Object DisplayName, DisplayVersion, Publisher, InstallDate, InstallLocation | "
            "ConvertTo-Json -Compress"
        )
        all_programs.extend(progs)

    # Deduplicate by name
    seen = set()
    deduped = []
    for p in all_programs:
        name = p.get("DisplayName", "")
        if name and name not in seen:
            seen.add(name)
            deduped.append(p)

    # Microsoft Store apps
    print("  [7/12] Microsoft Store Apps...")
    store_apps = run_ps_json(
        "Get-AppxPackage | Select-Object Name, Version, Publisher, Status, "
        "InstallLocation | ConvertTo-Json -Compress"
    )

    return {"registry_programs": deduped, "store_apps": store_apps}


def get_services():
    """All Windows services, with focus on Apple/USB related."""
    print("  [8/12] Windows Services...")
    services = run_ps_json(
        "Get-Service | Select-Object Name, DisplayName, Status, StartType | "
        "ConvertTo-Json -Compress"
    )
    return services


def get_disk_info():
    """All disk drives, partitions, and volumes."""
    print("  [9/12] Disk Drives & Firmware...")
    disks = run_ps_json(
        "Get-CimInstance Win32_DiskDrive | Select-Object Model, InterfaceType, "
        "Status, MediaType, FirmwareRevision, SerialNumber, Size | "
        "ConvertTo-Json -Compress"
    )
    for d in disks:
        size = d.get("Size", 0)
        if size:
            d["SizeGB"] = round(size / (1024**3), 1)

    # NVMe health via Get-PhysicalDisk if available
    nvme = run_ps_json(
        "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus, "
        "OperationalStatus, FirmwareVersion, Size, BusType | "
        "ConvertTo-Json -Compress"
    )
    for n in nvme:
        size = n.get("Size", 0)
        if size:
            n["SizeGB"] = round(size / (1024**3), 1)

    return {"disk_drives": disks, "physical_disks": nvme}


def get_windows_update_status():
    """Check for pending Windows Updates."""
    print("  [10/12] Windows Update Status...")
    # This requires COM object, use PowerShell
    updates = run_ps(
        "$Session = New-Object -ComObject Microsoft.Update.Session; "
        "$Searcher = $Session.CreateUpdateSearcher(); "
        "try { $Results = $Searcher.Search('IsInstalled=0'); "
        "$Results.Updates | ForEach-Object { "
        "[PSCustomObject]@{Title=$_.Title; KBArticleIDs=($_.KBArticleIDs -join ','); "
        "IsDownloaded=$_.IsDownloaded; IsMandatory=$_.IsMandatory} } | "
        "ConvertTo-Json -Compress } catch { Write-Output '[]' }",
        timeout=120
    )
    try:
        parsed = json.loads(updates) if updates and not updates.startswith("ERROR") else []
        if isinstance(parsed, dict):
            parsed = [parsed]
        return parsed
    except Exception:
        return []


def get_apple_specific():
    """Deep Apple / iPhone connectivity analysis."""
    print("  [11/12] Apple-Specific Analysis...")
    results = {}

    # Apple services
    results["apple_services"] = run_ps_json(
        "Get-Service | Where-Object { $_.DisplayName -match 'Apple|Bonjour|iTunes|Mobile Device' } | "
        "Select-Object Name, DisplayName, Status, StartType | ConvertTo-Json"
    )

    # Apple USB devices
    results["apple_usb_devices"] = run_ps_json(
        "Get-PnpDevice | Where-Object { $_.FriendlyName -match 'Apple|iPhone|iPad|iPod' } | "
        "Select-Object Status, Class, FriendlyName, InstanceId | ConvertTo-Json"
    )

    # Apple Store apps
    results["apple_store_apps"] = run_ps_json(
        "Get-AppxPackage | Where-Object { $_.Name -match 'Apple|iTunes' } | "
        "Select-Object Name, Version, Status | ConvertTo-Json"
    )

    # Apple driver details
    results["apple_drivers"] = run_ps_json(
        "Get-WmiObject Win32_PnPSignedDriver | Where-Object { $_.Manufacturer -match 'Apple' } | "
        "Select-Object DeviceName, DriverVersion, DriverDate, InfName, IsSigned | ConvertTo-Json"
    )

    # Check for Apple Mobile Device Support in registry
    amds = run_ps(
        "Get-ItemProperty 'HKLM:\\SOFTWARE\\Apple Inc.\\*' -ErrorAction SilentlyContinue | "
        "Select-Object PSChildName, * -ErrorAction SilentlyContinue | ConvertTo-Json"
    )
    results["apple_registry"] = amds if amds and not amds.startswith("ERROR") else "Not found"

    # USB controller details for USB-C ports
    results["usb_controllers"] = run_ps_json(
        "Get-PnpDevice | Where-Object { $_.FriendlyName -match 'xHCI|USB 3|Host Controller|USB-C|Type-C|UCSI' } | "
        "Select-Object Status, FriendlyName, InstanceId | ConvertTo-Json"
    )

    # Check for USB-C / Type-C / UCSI drivers
    results["usbc_drivers"] = run_ps_json(
        "Get-WmiObject Win32_PnPSignedDriver | Where-Object { "
        "$_.DeviceName -match 'Type-C|USB-C|UCSI|USB Power Delivery' } | "
        "Select-Object DeviceName, DriverVersion, DriverDate, Manufacturer | ConvertTo-Json"
    )

    # Check AMD chipset USB drivers
    results["amd_usb_drivers"] = run_ps_json(
        "Get-WmiObject Win32_PnPSignedDriver | Where-Object { "
        "$_.DeviceName -match 'AMD.*USB|AMD.*xHCI' } | "
        "Select-Object DeviceName, DriverVersion, DriverDate, Manufacturer | ConvertTo-Json"
    )

    return results


def get_gpu_and_chipset():
    """GPU and chipset driver info."""
    print("  [12/12] GPU & Chipset Drivers...")
    gpu = run_ps_json(
        "Get-WmiObject Win32_VideoController | Select-Object Name, DriverVersion, "
        "DriverDate, AdapterRAM, Status | ConvertTo-Json"
    )
    chipset = run_ps_json(
        "Get-WmiObject Win32_PnPSignedDriver | Where-Object { "
        "$_.DeviceClass -eq 'SYSTEM' -and $_.DeviceName -match 'PCI|ACPI|Chipset|SMBus|Bridge' } | "
        "Select-Object DeviceName, DriverVersion, DriverDate, Manufacturer | "
        "ConvertTo-Json -Compress"
    )
    return {"gpu": gpu, "chipset_drivers": chipset}


# ─── Analysis Functions ─────────────────────────────────────────────────────────

def analyze_report(report):
    """Analyze the collected data and produce findings."""
    findings = []
    severity_counts = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}

    def add(sev, category, message, detail=""):
        findings.append({"severity": sev, "category": category, "message": message, "detail": detail})
        severity_counts[sev] += 1

    # ── Apple Analysis ──
    apple = report.get("apple_specific", {})

    # Check Apple services
    apple_svc = apple.get("apple_services", [])
    svc_names = [s.get("DisplayName", "") for s in apple_svc]
    amds_found = any("Mobile Device" in n for n in svc_names)
    if not amds_found:
        add("CRITICAL", "Apple", "Apple Mobile Device Service (AMDS) is NOT running or not installed.",
            "This service is required for Windows to communicate with iPhones. "
            "It should be installed by iTunes or Apple Devices from the Microsoft Store.")
    else:
        for s in apple_svc:
            if "Mobile Device" in s.get("DisplayName", "") and s.get("Status", 0) != 4:
                add("CRITICAL", "Apple", f"Apple Mobile Device Service is not running (Status: {s.get('Status')})",
                    "Restart it via services.msc or by restarting Apple Devices app.")

    bonjour = any("Bonjour" in n for n in svc_names)
    if bonjour:
        add("INFO", "Apple", "Bonjour Service is present and available.")
    else:
        add("WARNING", "Apple", "Bonjour Service is not found.", "Bonjour helps with device discovery.")

    # Apple USB devices
    apple_usb = apple.get("apple_usb_devices", [])
    for dev in apple_usb:
        status = dev.get("Status", "")
        name = dev.get("FriendlyName", "")
        if status != "OK":
            add("CRITICAL", "Apple USB",
                f"'{name}' is in '{status}' state.",
                f"InstanceId: {dev.get('InstanceId', 'N/A')}")
        else:
            add("INFO", "Apple USB", f"'{name}' is OK.")

    # Apple drivers
    apple_drv = apple.get("apple_drivers", [])
    for drv in apple_drv:
        ver = drv.get("DriverVersion", "Unknown")
        name = drv.get("DeviceName", "Unknown")
        date = drv.get("DriverDate", "Unknown")
        if isinstance(date, str) and "/Date(" in date:
            try:
                ms = int(re.search(r'\d+', date).group())
                dt = datetime.datetime.fromtimestamp(ms / 1000)
                date = dt.strftime("%Y-%m-%d")
                if dt < datetime.datetime(2024, 1, 1):
                    add("WARNING", "Apple Driver",
                        f"'{name}' driver v{ver} is from {date} — likely outdated.",
                        "Consider updating Apple Devices from the Microsoft Store.")
                else:
                    add("INFO", "Apple Driver", f"'{name}' driver v{ver} from {date}.")
            except Exception:
                add("INFO", "Apple Driver", f"'{name}' driver v{ver}, date: {date}")
        else:
            add("INFO", "Apple Driver", f"'{name}' driver v{ver}, date: {date}")

    # Apple Store apps
    apple_apps = apple.get("apple_store_apps", [])
    if not apple_apps:
        add("CRITICAL", "Apple Software",
            "No Apple apps found in Microsoft Store.",
            "Install 'Apple Devices' from the Microsoft Store for USB-C iPhone support.")
    else:
        for app in apple_apps:
            add("INFO", "Apple Software", f"Store app: {app.get('Name')} v{app.get('Version')}")

    # USB-C / Type-C drivers
    usbc = apple.get("usbc_drivers", [])
    if not usbc:
        add("WARNING", "USB-C",
            "No dedicated USB-C / Type-C / UCSI drivers detected.",
            "Your motherboard may use generic xHCI drivers for USB-C. "
            "Check Gigabyte's website for B650 AORUS ELITE AX ICE chipset drivers.")
    else:
        for d in usbc:
            add("INFO", "USB-C", f"Type-C driver: {d.get('DeviceName')} v{d.get('DriverVersion')}")

    # AMD USB drivers
    amd_usb = apple.get("amd_usb_drivers", [])
    for d in amd_usb:
        add("INFO", "AMD USB", f"{d.get('DeviceName')} v{d.get('DriverVersion')}")

    # ── Problem Devices ──
    problems = report.get("problem_devices", [])
    for p in problems:
        name = p.get("FriendlyName", "Unknown")
        status = p.get("Status", "Unknown")
        cls = p.get("Class", "Unknown")
        add("WARNING", f"Problem Device ({cls})",
            f"'{name}' is in '{status}' state.",
            f"InstanceId: {p.get('InstanceId', 'N/A')}")

    # ── Windows Updates ──
    updates = report.get("windows_updates", [])
    if updates:
        add("WARNING", "Windows Update",
            f"{len(updates)} pending update(s) found.",
            "; ".join(u.get("Title", "?") for u in updates[:5]))
    else:
        add("INFO", "Windows Update", "No pending updates detected (or scan not available without admin).")

    # ── Disk Health ──
    phys_disks = report.get("disk_info", {}).get("physical_disks", [])
    for d in phys_disks:
        health = d.get("HealthStatus", "Unknown")
        name = d.get("FriendlyName", "Unknown")
        fw = d.get("FirmwareVersion", "Unknown")
        if health != "Healthy" and health != 0:
            add("WARNING", "Disk", f"'{name}' health: {health}, firmware: {fw}")
        else:
            add("INFO", "Disk", f"'{name}' is healthy, firmware: {fw}")

    # ── Driver Statistics ──
    all_drivers = report.get("all_drivers", [])
    unsigned = [d for d in all_drivers if d.get("IsSigned") == False]
    if unsigned:
        add("WARNING", "Drivers",
            f"{len(unsigned)} unsigned driver(s) found.",
            ", ".join(d.get("DeviceName", "?") for d in unsigned[:10]))

    # Count drivers by class
    class_counts = {}
    for d in all_drivers:
        cls = d.get("DeviceClass", "Unknown")
        class_counts[cls] = class_counts.get(cls, 0) + 1

    add("INFO", "Driver Summary",
        f"Total signed drivers: {len(all_drivers)} across {len(class_counts)} device classes.",
        f"Classes: {json.dumps(class_counts, indent=2)}")

    # ── Programs ──
    programs = report.get("installed_programs", {})
    reg_count = len(programs.get("registry_programs", []))
    store_count = len(programs.get("store_apps", []))
    add("INFO", "Programs", f"Found {reg_count} registry programs and {store_count} Store apps.")

    return findings, severity_counts


def generate_text_report(report, findings, severity_counts):
    """Generate a human-readable text report."""
    lines = []
    lines.append(section_header("PC DIAGNOSTIC REPORT"))
    lines.append(f"  Scan Time: {report['system_info'].get('scan_time', 'Unknown')}")
    lines.append(f"  Admin:     {report['system_info'].get('is_admin', False)}")
    lines.append("")

    # System Info
    si = report["system_info"]
    lines.append(section_header("SYSTEM INFORMATION"))
    lines.append(f"  OS:           {si.get('os', 'N/A')}")
    lines.append(f"  Build:        {si.get('os_version', 'N/A')}")
    lines.append(f"  CPU:          {si.get('processor', 'N/A')}")
    lines.append(f"  RAM:          {si.get('total_ram_gb', 'N/A')} GB")
    lines.append(f"  Motherboard:  {si.get('motherboard', 'N/A')}")
    lines.append(f"  BIOS:         {si.get('bios_version', 'N/A')} ({si.get('bios_manufacturer', 'N/A')})")
    lines.append(f"  Manufacturer: {si.get('manufacturer', 'N/A')}")
    lines.append(f"  Model:        {si.get('model', 'N/A')}")
    lines.append("")

    # Findings Summary
    lines.append(section_header("FINDINGS SUMMARY"))
    lines.append(f"  🔴 CRITICAL:  {severity_counts['CRITICAL']}")
    lines.append(f"  🟡 WARNING:   {severity_counts['WARNING']}")
    lines.append(f"  🟢 INFO:      {severity_counts['INFO']}")
    lines.append("")

    # Critical findings first
    if severity_counts["CRITICAL"] > 0:
        lines.append(section_header("🔴 CRITICAL ISSUES"))
        for f in findings:
            if f["severity"] == "CRITICAL":
                lines.append(f"  [{f['category']}] {f['message']}")
                if f["detail"]:
                    lines.append(f"    → {f['detail']}")
                lines.append("")

    # Warnings
    if severity_counts["WARNING"] > 0:
        lines.append(section_header("🟡 WARNINGS"))
        for f in findings:
            if f["severity"] == "WARNING":
                lines.append(f"  [{f['category']}] {f['message']}")
                if f["detail"]:
                    lines.append(f"    → {f['detail']}")
                lines.append("")

    # Info
    lines.append(section_header("🟢 INFORMATIONAL"))
    for f in findings:
        if f["severity"] == "INFO":
            lines.append(f"  [{f['category']}] {f['message']}")
    lines.append("")

    # USB Device Table
    usb_devs = report.get("usb_devices", [])
    if usb_devs:
        lines.append(section_header("USB DEVICE TREE"))
        lines.append(f"  {'Status':<10} {'Class':<20} {'Name'}")
        lines.append(f"  {'-'*10} {'-'*20} {'-'*45}")
        for u in usb_devs:
            if u.get("Present"):
                status = str(u.get("Status", "?"))
                cls = str(u.get("Class", "?"))[:20]
                name = str(u.get("FriendlyName", "?"))
                marker = " ⚠️" if status != "OK" else ""
                lines.append(f"  {status:<10} {cls:<20} {name}{marker}")
        lines.append("")

    # Apple Deep Dive
    apple = report.get("apple_specific", {})
    lines.append(section_header("APPLE / iPHONE CONNECTIVITY DETAILS"))

    lines.append("\n  Apple Services:")
    for s in apple.get("apple_services", []):
        status_map = {4: "Running", 1: "Stopped", 0: "Unknown"}
        st = status_map.get(s.get("Status", -1), str(s.get("Status", "?")))
        lines.append(f"    {s.get('DisplayName', '?')}: {st} ({s.get('StartType', '?')})")

    lines.append("\n  Apple USB Devices:")
    for d in apple.get("apple_usb_devices", []):
        lines.append(f"    [{d.get('Status', '?')}] {d.get('FriendlyName', '?')}")
        lines.append(f"           ID: {d.get('InstanceId', 'N/A')}")

    lines.append("\n  Apple Drivers:")
    for d in apple.get("apple_drivers", []):
        lines.append(f"    {d.get('DeviceName', '?')} v{d.get('DriverVersion', '?')} "
                     f"(INF: {d.get('InfName', '?')}, Signed: {d.get('IsSigned', '?')})")

    lines.append("\n  Apple Store Apps:")
    for a in apple.get("apple_store_apps", []):
        lines.append(f"    {a.get('Name', '?')} v{a.get('Version', '?')}")

    lines.append("\n  USB Controllers (xHCI / USB 3.x):")
    for c in apple.get("usb_controllers", []):
        lines.append(f"    [{c.get('Status', '?')}] {c.get('FriendlyName', '?')}")

    lines.append("\n  USB-C / Type-C / UCSI Drivers:")
    if apple.get("usbc_drivers"):
        for d in apple["usbc_drivers"]:
            lines.append(f"    {d.get('DeviceName', '?')} v{d.get('DriverVersion', '?')}")
    else:
        lines.append("    None found (using generic xHCI drivers for USB-C)")

    lines.append("\n  AMD USB Drivers:")
    for d in apple.get("amd_usb_drivers", []):
        lines.append(f"    {d.get('DeviceName', '?')} v{d.get('DriverVersion', '?')}")
    lines.append("")

    # Disk Info
    lines.append(section_header("DISK DRIVES"))
    for d in report.get("disk_info", {}).get("physical_disks", []):
        lines.append(f"  {d.get('FriendlyName', '?')} — {d.get('SizeGB', '?')} GB")
        lines.append(f"    Type: {d.get('MediaType', '?')}, Bus: {d.get('BusType', '?')}")
        lines.append(f"    Health: {d.get('HealthStatus', '?')}, Firmware: {d.get('FirmwareVersion', '?')}")
    lines.append("")

    # Problem Devices
    problems = report.get("problem_devices", [])
    if problems:
        lines.append(section_header("ALL PROBLEM DEVICES"))
        for p in problems:
            lines.append(f"  [{p.get('Status', '?')}] ({p.get('Class', '?')}) {p.get('FriendlyName', '?')}")
            lines.append(f"           ID: {p.get('InstanceId', 'N/A')}")
        lines.append("")

    # Windows Updates
    updates = report.get("windows_updates", [])
    lines.append(section_header("PENDING WINDOWS UPDATES"))
    if updates:
        for u in updates:
            dl = "✓ Downloaded" if u.get("IsDownloaded") else "⏳ Not downloaded"
            lines.append(f"  [{dl}] {u.get('Title', '?')}")
    else:
        lines.append("  No pending updates found (or requires admin privileges).")
    lines.append("")

    # Driver breakdown by class
    lines.append(section_header("DRIVER COUNTS BY DEVICE CLASS"))
    all_drivers = report.get("all_drivers", [])
    class_counts = {}
    for d in all_drivers:
        cls = d.get("DeviceClass", "Unknown") or "Unknown"
        class_counts[cls] = class_counts.get(cls, 0) + 1
    for cls, count in sorted(class_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {cls:<30} {count}")
    lines.append(f"\n  Total drivers: {len(all_drivers)}")
    lines.append("")

    # GPU/Chipset
    gpu_info = report.get("gpu_chipset", {})
    lines.append(section_header("GPU & CHIPSET"))
    for g in gpu_info.get("gpu", []):
        ram_mb = g.get("AdapterRAM", 0)
        ram_gb = round(ram_mb / (1024**3), 1) if ram_mb else "?"
        lines.append(f"  GPU: {g.get('Name', '?')} — v{g.get('DriverVersion', '?')} ({ram_gb} GB VRAM)")
    lines.append("")

    return "\n".join(lines)


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║     PC DIAGNOSTIC TOOL — Full System Analysis       ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    if not is_admin():
        print("  ⚠️  Not running as Administrator.")
        print("     Some scans (Windows Update, elevated drivers) may be limited.")
        print()

    print("  Scanning system... this may take 1-3 minutes.\n")

    report = {}

    # Collect all data
    report["system_info"]        = get_system_info()
    report["all_drivers"]        = get_all_drivers()
    report["all_pnp_devices"]    = get_all_pnp_devices()
    report["problem_devices"]    = get_problem_devices()
    report["usb_devices"]        = get_usb_devices()
    report["installed_programs"] = get_installed_programs()
    report["services"]           = get_services()
    report["disk_info"]          = get_disk_info()
    report["windows_updates"]    = get_windows_update_status()
    report["apple_specific"]     = get_apple_specific()
    report["gpu_chipset"]        = get_gpu_and_chipset()

    # Analyze
    print("\n  Analyzing results...\n")
    findings, severity_counts = analyze_report(report)
    report["findings"]          = findings
    report["severity_counts"]   = severity_counts

    # Write JSON report
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str, ensure_ascii=False)
    print(f"  ✅ JSON report saved: {REPORT_JSON}")

    # Write text report
    text_report = generate_text_report(report, findings, severity_counts)
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write(text_report)
    print(f"  ✅ Text report saved: {REPORT_TXT}")

    # Print summary to console
    print(text_report)

    return report


if __name__ == "__main__":
    main()
