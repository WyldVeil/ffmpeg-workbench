"""
core/hardware.py  ─  Platform-safe hardware and binary utilities

Provides:
  - get_binary_path()       - locate ffmpeg/ffplay/ffprobe for current OS
  - detect_gpu()            - NVIDIA / AMD / Apple / CPU (Cached for performance)
  - get_video_duration()    - ffprobe-based precise duration
  - get_audio_bitrate_kbps()- ffprobe-based exact audio stream bitrate
  - launch_preview()        - ffplay preview window
  - get_local_version()     - installed FFmpeg version string
  - get_latest_online_version() - latest release from gyan.dev
  - download_and_extract_ffmpeg() - auto-update bundled FFmpeg
  - open_in_explorer()      - cross-platform file/folder opener

Platform notes
──────────────
• Windows  - ffmpeg.exe / ffplay.exe / ffprobe.exe
• macOS    - ffmpeg / ffplay / ffprobe  (no .exe)
• Linux    - ffmpeg / ffplay / ffprobe  (no .exe)

CREATE_NO_WINDOW is a Windows-only subprocess flag (0x08000000). On other
platforms it is defined as 0 so it can be passed safely without guards.
"""

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from typing import Optional

# ── Platform constants ────────────────────────────────────────────────────────
_IS_WIN = platform.system() == "Windows"
_IS_MAC = platform.system() == "Darwin"
_IS_LINUX = platform.system() == "Linux"

# Suppress the console window on Windows; harmless (0) elsewhere.
CREATE_NO_WINDOW: int = 0x08000000 if _IS_WIN else 0

# Binary extension depends on platform
_EXE = ".exe" if _IS_WIN else ""

# Project / bundle root (works from source and from a PyInstaller bundle,
# where __file__ lives under sys._MEIPASS).
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Global cache to prevent redundant, slow hardware polling
_CACHED_GPU: Optional[str] = None


# ── Binary path resolution ────────────────────────────────────────────────────

def platform_tag() -> str:
    """Return the bundle sub-folder name for this OS/CPU, e.g. ``linux-x64``."""
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64",
            "arm64": "arm64"}.get(machine, machine or "x64")
    if _IS_WIN:
        osname = "windows"
    elif _IS_MAC:
        osname = "macos"
    else:
        osname = "linux"
    return f"{osname}-{arch}"


def user_tools_dir() -> Optional[str]:
    """Per-user folder for tools the app downloads or updates itself."""
    try:
        from core.paths import data_dir
        return os.path.join(data_dir(), "bin", platform_tag())
    except Exception:
        return None


def bundled_bin_dirs() -> list:
    """Folders searched for bundled tools, most specific first.

    ``bin/<platform-tag>/`` holds the per-platform downloads made by
    ``scripts/fetch_binaries.py``; plain ``bin/`` is the legacy flat layout
    used by older Windows builds.
    """
    roots = [APP_ROOT]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.abspath(meipass) != APP_ROOT:
        roots.append(meipass)
    dirs = []
    # Tools installed or updated by the app itself (always user-writable).
    user_bin = user_tools_dir()
    if user_bin:
        dirs += [os.path.join(user_bin, "ffmpeg", "bin"), user_bin]
    for root in roots:
        tag_dir = os.path.join(root, "bin", platform_tag())
        # FFmpeg's own tree (shared build: executables + DLLs, or bin/ + lib/).
        dirs.append(os.path.join(tag_dir, "ffmpeg", "bin"))
        dirs.append(tag_dir)
        dirs.append(os.path.join(root, "bin"))
    return dirs


def _exe_name(name: str) -> str:
    base = os.path.basename(name)
    if base.lower().endswith(".exe"):
        base = base[:-4]
    return base + _EXE


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and (_IS_WIN or os.access(path, os.X_OK))


def _check_settings_override(name: str) -> Optional[str]:
    """Return a user-chosen binary from settings, or None.

    ``ffmpeg_path_override`` may point at the ffmpeg executable itself or at
    the folder containing it; sibling tools (ffprobe/ffplay) are looked up
    next to it.
    """
    try:
        from core.settings import load_settings
        override = str(load_settings().get("ffmpeg_path_override", "") or "").strip()
    except Exception:
        return None
    if not override:
        return None
    folder = override if os.path.isdir(override) else os.path.dirname(override)
    candidate = os.path.join(folder, _exe_name(name))
    return candidate if _is_executable(candidate) else None


def _binary_source_pref() -> str:
    """'auto' (bundled, then system), 'bundled' or 'system'."""
    try:
        from core.settings import load_settings
        pref = str(load_settings().get("binary_source", "auto") or "auto").lower()
    except Exception:
        pref = "auto"
    return pref if pref in ("auto", "bundled", "system") else "auto"


def find_binary(name: str) -> Optional[str]:
    """Locate *name* (``"ffmpeg"``, ``"ffprobe"``, ``"yt-dlp"`` …).

    Priority: explicit settings override → bundled ``bin/`` folders →
    system ``PATH``.  With ``binary_source = "system"`` the PATH is tried
    before the bundled copy.  Returns None when the tool is not found.
    """
    override = _check_settings_override(name)
    if override:
        return override

    exe = _exe_name(name)
    bundled = None
    for folder in bundled_bin_dirs():
        candidate = os.path.join(folder, exe)
        if _is_executable(candidate):
            bundled = candidate
            break
    system = shutil.which(exe)

    if _binary_source_pref() == "system":
        return system or bundled
    return bundled or system


def get_binary_path(name: str) -> str:
    """
    Return the path to use for tool *name* (``"ffmpeg"``, ``"ffprobe"`` …).

    The platform extension is handled automatically, so ``"ffmpeg.exe"`` and
    ``"ffmpeg"`` are equivalent.  When the tool cannot be found anywhere the
    expected bundled location is returned so error messages point somewhere
    meaningful; callers can check ``os.path.isfile`` / :func:`find_binary`.
    """
    found = find_binary(name)
    if found:
        return found
    return os.path.join(APP_ROOT, "bin", platform_tag(), _exe_name(name))


def _resolve(name: str) -> str:
    """Backwards-compatible alias of :func:`get_binary_path`."""
    return get_binary_path(name)


# ── GPU detection ─────────────────────────────────────────────────────────────

def detect_gpu() -> str:
    """Best working hardware-encoder family, as a coarse vendor name:
    ``"nvidia"`` | ``"amd"`` | ``"intel"`` | ``"apple"`` | ``"vaapi"`` | ``"cpu"``.

    Based on a *functional* encode test (core/encoders.py) rather than on
    whether nvidia-smi exists; cached for the session.
    """
    global _CACHED_GPU
    if _CACHED_GPU is not None:
        return _CACHED_GPU
    try:
        from core.encoders import preferred_hw
        kind = preferred_hw()
    except Exception:
        kind = None
    _CACHED_GPU = {"nvenc": "nvidia", "amf": "amd", "qsv": "intel",
                   "videotoolbox": "apple", "vaapi": "vaapi"}.get(kind or "", "cpu")
    return _CACHED_GPU


# ── Metadata helpers (thin wrappers over core.ffmpeg) ─────────────────────────

def get_video_duration(file_path: str) -> float:
    """Duration of *file_path* in seconds (0.0 on any error)."""
    if not file_path:
        return 0.0
    from core.ffmpeg import duration_of
    return duration_of(file_path)


def get_audio_bitrate_kbps(file_path: str) -> float:
    """Bit rate of the first audio stream in kb/s (0.0 if unknown)."""
    from core.ffmpeg import try_probe
    info = try_probe(file_path) if file_path else None
    if info and info.audio and info.audio.bit_rate:
        return info.audio.bit_rate / 1000.0
    return 0.0


def launch_preview(file_path: str, start_time: float = 0) -> Optional[subprocess.Popen]:
    """Open *file_path* in ffplay at *start_time* (tracked, killable)."""
    if not file_path or not os.path.exists(file_path):
        return None
    ffplay = get_binary_path("ffplay")
    if not os.path.isfile(ffplay):
        return None
    from core.ffmpeg import popen, ts
    cmd = [ffplay, "-hide_banner", "-loglevel", "error", "-ss", ts(start_time),
           "-window_title", f"Preview — {os.path.basename(file_path)}  (Space: pause, Q: close)",
           "-x", "960", "-y", "540", "-autoexit", file_path]
    try:
        return popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


# ── Version management ────────────────────────────────────────────────────────

def get_local_version() -> str:
    """Version of the active FFmpeg (``"9.0.2"``), ``"Missing"`` or ``"Unknown"``."""
    if not find_binary("ffmpeg"):
        return "Missing"
    from core.ffmpeg import version_of
    return version_of(get_binary_path("ffmpeg"))


def get_latest_online_version() -> str:
    """Latest FFmpeg release version (from gyan.dev), or the local version."""
    try:
        from core.updater import latest_ffmpeg_release
        return latest_ffmpeg_release() or get_local_version()
    except Exception:
        return get_local_version()


def download_and_extract_ffmpeg(log_func) -> bool:
    """Download a verified FFmpeg build into the per-user tools folder."""
    from core.updater import install_ffmpeg
    return install_ffmpeg(log_func)


# ── Opening files and folders ─────────────────────────────────────────────────

def _spawn(cmd: list) -> bool:
    try:
        from core.procenv import child_env
        subprocess.Popen(cmd, env=child_env(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=CREATE_NO_WINDOW, start_new_session=not _IS_WIN)
        return True
    except OSError:
        return False


def open_in_explorer(path: str) -> bool:
    """Open *path* (file or folder) with the system's default handler."""
    if not path or not os.path.exists(path):
        return False
    try:
        if _IS_WIN:
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        if _IS_MAC:
            return _spawn(["open", path])
        return _spawn(["xdg-open", path])
    except OSError:
        return False


def reveal_file(path: str) -> bool:
    """Show *path* selected in the file manager (falls back to its folder)."""
    if not path:
        return False
    if not os.path.exists(path):
        return open_in_explorer(os.path.dirname(path))
    path = os.path.abspath(path)
    if os.path.isdir(path):
        return open_in_explorer(path)
    if _IS_WIN:
        return _spawn(["explorer", "/select,", path])
    if _IS_MAC:
        return _spawn(["open", "-R", path])
    if shutil.which("gdbus"):
        from urllib.parse import quote
        uri = "file://" + quote(path)
        if _spawn(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                   "--object-path", "/org/freedesktop/FileManager1",
                   "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""]):
            return True
    return open_in_explorer(os.path.dirname(path))
