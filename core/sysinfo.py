"""
core/sysinfo.py - the System Info report on the settings page (also pasted
into bug reports).

Blocking - call it from a background task.  Each section is its own function
and a section that can't be read says so instead of failing the report.  No
addresses or host names: this text gets pasted into bug reports, and looking
up the public IP would contact a third party behind the person's back (and
around their proxy/Tor settings).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
from collections.abc import Callable

from core import proc as _proc

logger = logging.getLogger("crossfader.sysinfo")

_WINDOWS = platform.system() == "Windows"


def _ps(cmd: str) -> str:
    """A PowerShell command's output (Windows)."""
    return _proc.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=30
    ).stdout.strip()


def _heading(title: str = "") -> list[str]:
    return ["", f"──── {title} " + "─" * max(0, 46 - len(title)) if title else "─" * 52]


def _windows_edition() -> list[str]:
    reg = _ps(
        "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT"
        "\\CurrentVersion' | Select-Object ProductName, "
        "DisplayVersion, CurrentBuildNumber | ConvertTo-Json"
    )
    rv = json.loads(reg)
    product, disp_ver = rv.get("ProductName", ""), rv.get("DisplayVersion", "")
    build_num = int(rv.get("CurrentBuildNumber", 0) or 0)
    if build_num >= 22000 and "Windows 10" in product:
        product = product.replace("Windows 10", "Windows 11")
    lines = [
        f"  Edition:      {product} {disp_ver}".strip(),
        f"  Build:        {build_num}  ({disp_ver})",
    ]
    uptime_s = _ps(
        "(Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime | Select-Object -ExpandProperty TotalSeconds"
    )
    if uptime_s:
        h, m = divmod(int(float(uptime_s)) // 60, 60)
        lines.append(f"  Uptime:       {h}h {m}m")
    return lines


def _os() -> list[str]:
    lines = [
        f"  Platform:     {platform.system()} {platform.release()}",
        f"  Version:      {platform.version()[:70]}",
        f"  Architecture: {platform.machine()}",
    ]
    if _WINDOWS:
        try:
            lines += _windows_edition()
        except Exception:
            logger.debug("sysinfo: the Windows edition could not be read", exc_info=True)
    return lines


def _cpu() -> list[str]:
    if not _WINDOWS:
        keys = ("Model name", "CPU(s)", "Thread(s)", "MHz")
        out = _proc.run(["lscpu"], timeout=30).stdout
        return [
            f"  {ln.strip()}"
            for ln in out.splitlines()
            for key in keys
            if ln.strip().startswith(key)
        ]
    cpu = "(Get-CimInstance Win32_Processor)"
    name, cores = _ps(f"{cpu}.Name"), _ps(f"{cpu}.NumberOfCores")
    threads, mhz = _ps(f"{cpu}.NumberOfLogicalProcessors"), _ps(f"{cpu}.MaxClockSpeed")
    lines = [f"  Model:        {name}", f"  Cores:        {cores}  /  Threads: {threads}"]
    if mhz:
        try:
            lines.append(f"  Base clock:   {float(mhz) / 1000:.2f} GHz  ({mhz} MHz)")
        except ValueError:
            lines.append(f"  Base clock:   {mhz} MHz")
    load = _ps(f"{cpu}.LoadPercentage")
    return lines + ([f"  Current load: {load}%"] if load else [])


def _ram() -> list[str]:
    if not _WINDOWS:
        rows = [
            ln.split()
            for ln in _proc.run(["free", "-h"], timeout=30).stdout.splitlines()
            if ln.startswith("Mem")
        ]
        return [f"  Total: {p[1]}  Used: {p[2]}  Free: {p[3]}" for p in rows]
    total_kb = int(_ps("(Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize"))
    free_kb = int(_ps("(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"))
    used_kb = total_kb - free_kb
    pct = used_kb / total_kb * 100 if total_kb else 0
    lines = [
        f"  Total:        {total_kb / 1024 / 1024:.1f} GB",
        f"  Used:         {used_kb / 1024 / 1024:.1f} GB  ({pct:.0f}%)",
        f"  Free:         {free_kb / 1024 / 1024:.1f} GB",
    ]
    try:
        sticks = _ps(
            "Get-CimInstance Win32_PhysicalMemory | "
            'ForEach-Object { "$($_.Capacity/1GB)GB $($_.Speed)MHz $($_.Manufacturer)" }'
        )
        lines += [f"  Stick:        {stick.strip()}" for stick in sticks.splitlines()[:4]]
    except Exception:
        logger.debug("sysinfo: the memory sticks could not be read", exc_info=True)
    return lines


def _disk() -> list[str]:
    if not _WINDOWS:
        out = _proc.run(["df", "-h", "-x", "tmpfs", "-x", "devtmpfs"], timeout=30).stdout
        return [f"  {line}" for line in out.splitlines()[1:6]]
    drives = _ps(
        "Get-CimInstance Win32_DiskDrive | "
        'ForEach-Object { "$($_.Model)|$([math]::Round($_.Size/1GB))GB|$($_.MediaType)" }'
    )
    vols = _ps(
        "Get-PSDrive -PSProvider FileSystem | "
        "Where-Object { $_.Used -ne $null } | "
        'ForEach-Object { "$($_.Name):|$([math]::Round(($_.Used+$_.Free)/1GB,0))GB total'
        '|$([math]::Round($_.Used/1GB,1))GB used|$([math]::Round($_.Free/1GB,1))GB free" }'
    )
    lines = [
        f"  Drive:        {p[0]}  ({p[1]})"
        for d in drives.splitlines()
        if len(p := d.strip().split("|")) >= 2
    ]
    return lines + [
        f"  {p[0]:<5} {p[1]:<14} {p[2]:<14} {p[3]}"
        for v in vols.splitlines()
        if len(p := v.strip().split("|")) >= 4
    ]


def _vram_mb(hw_accel: str) -> int | None:
    """Video memory: from nvidia-smi on NVIDIA, else the display driver's registry key."""
    if hw_accel == "NVIDIA":
        try:
            r = _proc.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                timeout=30,
            )
            return int(r.stdout.strip().splitlines()[0].strip())
        except Exception:
            logger.debug("sysinfo: nvidia-smi gave no memory size", exc_info=True)
    try:
        reg = _ps(
            "$key = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class"
            "\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0000'; "
            "(Get-ItemProperty -Path $key -ErrorAction SilentlyContinue)"
            '."HardwareInformation.MemorySize"'
        )
        return int(reg.strip()) // (1024 * 1024) if reg.strip() else None
    except Exception:
        logger.debug("sysinfo: the registry gave no video memory size", exc_info=True)
        return None


def _gpu() -> list[str]:
    from core.hardware import detect_gpu

    hw_accel = detect_gpu().upper()
    lines = [f"  HW accel:     {hw_accel}"]
    if not _WINDOWS:
        return lines
    gpus = _ps(
        'Get-CimInstance Win32_VideoController | ForEach-Object { "$($_.Name)|$($_.DriverVersion)" }'
    )
    for g in gpus.splitlines():
        p = g.strip().split("|")
        if p[0]:
            lines.append(f"  GPU:          {p[0]}")
            lines += [f"  Driver:       {p[1]}"] if len(p) > 1 and p[1] else []
    vram = _vram_mb(hw_accel)
    if vram and vram > 0:
        lines.append(
            f"  VRAM:         {vram / 1024:.0f} GB  ({vram} MB)"
            if vram >= 1024
            else f"  VRAM:         {vram} MB"
        )
    return lines


def _network() -> list[str]:
    from core import network

    cfg = network.get()
    if cfg.get("network.offline"):
        return ["  Mode:         offline (no network use)"]
    proxy = network.proxy_url(cfg)
    if proxy:
        return [f"  Proxy:        {proxy.split('://', 1)[0]} proxy configured in the app"]
    return ["  Proxy:        none set in the app (system settings apply)"]


def _python(tk_version: str) -> list[str]:
    from core import APP_VERSION
    from core.paths import APP_ROOT

    return [
        f"  Python:       {platform.python_version()}  ({sys.executable[:60]})",
        f"  tkinter:      {tk_version}",
        f"  App version:  {APP_VERSION}",
        f"  App root:     {APP_ROOT}",
    ]


def _ffmpeg() -> list[str]:
    from core.hardware import get_binary_path, get_local_version

    ffmpeg = get_binary_path("ffmpeg")
    lines = [
        f"  Binary:       {ffmpeg}",
        f"  Exists:       {os.path.exists(ffmpeg)}",
        f"  Version:      {get_local_version()}",
    ]
    try:
        out = _proc.run([ffmpeg, "-codecs"], timeout=30).stdout
        hw = [
            ln.strip()
            for ln in out.split("\n")
            if any(x in ln for x in ("nvenc", "amf", "qsv", "videotoolbox"))
        ]
        return [*lines, f"  HW codecs:    {len(hw)} found", *(f"    {h[:70]}" for h in hw[:8])]
    except Exception:
        logger.debug("sysinfo: the codec list could not be read", exc_info=True)
        return [*lines, "  Could not query codecs."]


def report(tk_version: str = "") -> str:
    """The whole report, section by section."""
    sections: list[tuple[str, str, Callable[[], list[str]]]] = [
        ("OPERATING SYSTEM", "", _os),
        ("PROCESSOR (CPU)", "CPU", _cpu),
        ("MEMORY (RAM)", "RAM", _ram),
        ("DISK / STORAGE", "Disk", _disk),
        ("GRAPHICS (GPU)", "GPU", _gpu),
        ("NETWORK", "Network settings", _network),
        ("PYTHON & APPLICATION", "", lambda: _python(tk_version)),
        ("FFMPEG", "", _ffmpeg),
    ]
    lines: list[str] = []
    for title, what, read in sections:
        lines += _heading(title)
        try:
            lines += read()
        except Exception as exc:
            logger.debug("sysinfo: %s failed", title, exc_info=True)
            lines.append(
                f"  ({what or title} error: {exc})"
                if what != "Network settings"
                else f"  (Network settings unavailable: {exc})"
            )
    return "\n".join([*lines, *_heading(), "  ← Refresh to update live stats"])
