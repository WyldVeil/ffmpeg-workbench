"""
core/paths.py - Where the application keeps its files.

The program folder may be read-only (Program Files, /opt, an AppImage mount),
so everything the app *writes* lives in per-user folders that follow each
platform's conventions:

=========  ==========================================  =====================================
Kind       Windows                                     Linux (XDG)
=========  ==========================================  =====================================
config     %APPDATA%\\FFmpeg Workbench                  ~/.config/ffmpeg-workbench
data       %LOCALAPPDATA%\\FFmpeg Workbench             ~/.local/share/ffmpeg-workbench
cache      %LOCALAPPDATA%\\FFmpeg Workbench\\Cache  ~/.cache/ffmpeg-workbench
logs       %LOCALAPPDATA%\\FFmpeg Workbench\\Logs   ~/.local/state/ffmpeg-workbench/logs
=========  ==========================================  =====================================

macOS uses ~/Library/Application Support, ~/Library/Caches and ~/Library/Logs.

Portable mode: if a file named ``portable.txt`` exists next to the program
(the portable downloads have one), every folder lives in ``userdata`` inside the
program folder instead, so it can run from anywhere, e.g. a USB stick.  When
that folder can't be written to (a read-only drive), the usual folders are used.

Environment overrides (used by the test-suite):
``CROSSFADER_CONFIG_DIR``, ``CROSSFADER_DATA_DIR``, ``CROSSFADER_CACHE_DIR``,
``CROSSFADER_LOG_DIR``.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Folder name on Windows / macOS.
APP_DIR_NAME = "FFmpeg Workbench"
#: Folder name on Linux (XDG convention: lowercase, no spaces).
APP_SLUG = "ffmpeg-workbench"


_IS_WIN = sys.platform.startswith("win")
_IS_MAC = sys.platform == "darwin"


def is_frozen() -> bool:
    """True when running from a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def is_portable() -> bool:
    return os.path.isfile(os.path.join(APP_ROOT, "portable.txt")) or os.path.isfile(
        os.path.join(os.path.dirname(sys.executable), "portable.txt")
    )


def _portable_root() -> str | None:
    """``userdata`` in the program folder, when running portable and it can be written."""
    if not is_portable():
        return None
    root = os.path.join(APP_ROOT, "userdata")
    probe = root if os.path.isdir(root) else APP_ROOT
    return root if os.access(probe, os.W_OK) else None


def _ensure(path: str) -> str:
    with contextlib.suppress(OSError):
        os.makedirs(path, exist_ok=True)
    return path


def base_dir(kind: str) -> str:
    env = os.environ.get(f"CROSSFADER_{kind.upper()}_DIR")
    if env:
        return env
    portable = _portable_root()
    if portable:
        return os.path.join(portable, kind)
    return _user_dir(kind, APP_DIR_NAME, APP_SLUG)


def _user_dir(kind: str, dir_name: str, slug: str) -> str:
    """The per-user folder of *kind* for an app called *dir_name* / *slug*."""
    home = os.path.expanduser("~")
    if _IS_WIN:
        roaming = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return {
            "config": os.path.join(roaming, dir_name),
            "data": os.path.join(local, dir_name),
            "cache": os.path.join(local, dir_name, "Cache"),
            "log": os.path.join(local, dir_name, "Logs"),
        }[kind]
    if _IS_MAC:
        lib = os.path.join(home, "Library")
        return {
            "config": os.path.join(lib, "Application Support", dir_name),
            "data": os.path.join(lib, "Application Support", dir_name),
            "cache": os.path.join(lib, "Caches", dir_name),
            "log": os.path.join(lib, "Logs", dir_name),
        }[kind]
    xdg = {
        "config": os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"),
        "data": os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"),
        "cache": os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"),
        "log": os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"),
    }[kind]
    path = os.path.join(xdg, slug)
    return os.path.join(path, "logs") if kind == "log" else path


def config_dir() -> str:
    """Settings, skin choice, presets, recent files."""
    return _ensure(base_dir("config"))


def data_dir() -> str:
    """Larger per-user data: optional Python packages, downloaded models."""
    return _ensure(base_dir("data"))


def cache_dir() -> str:
    """Disposable data: frame previews, waveforms, encoder probe results."""
    return _ensure(base_dir("cache"))


def log_dir() -> str:
    return _ensure(base_dir("log"))


def models_dir() -> str:
    """Downloaded ML models (e.g. Whisper ggml files)."""
    return _ensure(os.path.join(data_dir(), "models"))


def temp_dir(prefix: str = "job_") -> str:
    """A fresh private temporary folder for one job (caller removes it)."""
    root = _ensure(os.path.join(tempfile.gettempdir(), APP_SLUG))
    return tempfile.mkdtemp(prefix=prefix, dir=root)


def resource_path(*parts: str) -> str:
    """Absolute path of a read-only resource shipped with the program
    (locale/, assets/, bin/ …), from source or from a frozen bundle."""
    return os.path.join(APP_ROOT, *parts)


def user_videos_dir() -> str:
    """Best guess at the user's Videos folder (falls back to home)."""
    home = os.path.expanduser("~")
    if not _IS_WIN and not _IS_MAC:
        # Honour xdg-user-dirs if configured.
        cfg = os.path.join(
            os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "user-dirs.dirs"
        )
        try:
            with open(cfg, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("XDG_VIDEOS_DIR="):
                        val = line.split("=", 1)[1].strip().strip('"')
                        val = val.replace("$HOME", home)
                        if os.path.isdir(val):
                            return val
        except OSError:
            pass
    for name in ("Videos", "Movies"):
        cand = os.path.join(home, name)
        if os.path.isdir(cand):
            return cand
    return home


def is_writable_dir(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        fd, probe = tempfile.mkstemp(prefix=".wcheck_", dir=path)
        os.close(fd)
        os.remove(probe)
        return True
    except OSError:
        return False
