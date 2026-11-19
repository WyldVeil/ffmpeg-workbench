"""
core/dialogs.py - File dialogs that feel native and behave the same everywhere.

* One set of file-type lists (video, audio, image, subtitles, LUTs …) with
  patterns in BOTH cases on Linux/macOS, where Tk matching is case-sensitive
  (``*.mp4`` used to hide ``CLIP.MP4``).
* On Linux the desktop's own file chooser is used when available
  (``kdialog`` on KDE, ``zenity`` elsewhere) instead of Tk's bare-bones one;
  the Tk window keeps repainting while the native dialog is open.  Disable
  with the ``native_dialogs`` setting or ``CROSSFADER_NATIVE_DIALOGS=0``.
* The last folder used is remembered per kind of file.

All functions return ``""`` / ``[]`` when the user cancels.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from typing import Optional, Sequence

_IS_WIN = sys.platform.startswith("win")
_IS_MAC = sys.platform == "darwin"

VIDEO_EXTS = ("mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv", "flv", "mpg", "mpeg",
              "ts", "mts", "m2ts", "3gp", "ogv", "vob", "mxf", "gif", "y4m")
AUDIO_EXTS = ("mp3", "wav", "flac", "aac", "m4a", "ogg", "oga", "opus", "wma", "aif",
              "aiff", "alac", "ac3", "eac3", "mka", "caf", "amr")
IMAGE_EXTS = ("png", "jpg", "jpeg", "bmp", "webp", "tif", "tiff", "gif", "avif", "heic")
SUB_EXTS = ("srt", "ass", "ssa", "vtt", "sub", "sbv")
LUT_EXTS = ("cube", "3dl", "dat", "m3d", "csp")
MIDI_EXTS = ("mid", "midi")
SOUNDFONT_EXTS = ("sf2", "sf3")
FONT_EXTS = ("ttf", "otf")
PRESET_EXTS = ("json",)

_KINDS = {
    "video": ("Video files", VIDEO_EXTS),
    "audio": ("Audio files", AUDIO_EXTS),
    "media": ("Media files", VIDEO_EXTS + AUDIO_EXTS),
    "image": ("Images", IMAGE_EXTS),
    "visual": ("Videos and images", VIDEO_EXTS + IMAGE_EXTS),
    "subtitle": ("Subtitles", SUB_EXTS),
    "lut": ("LUT files", LUT_EXTS),
    "midi": ("MIDI files", MIDI_EXTS),
    "soundfont": ("SoundFonts", SOUNDFONT_EXTS),
    "font": ("Fonts", FONT_EXTS),
    "preset": ("Presets", PRESET_EXTS),
    "text": ("Text files", ("txt",)),
    "any": ("All files", ()),
}


def _patterns(exts: Sequence[str]) -> str:
    if not exts:
        return "*"
    if _IS_WIN:      # Windows matching is already case-insensitive
        return " ".join(f"*.{e}" for e in exts)
    return " ".join(f"*.{e} *.{e.upper()}" for e in exts)


def filetypes(kind: str = "media", extra: Sequence[str] = ()) -> list:
    """Tk-style ``filetypes`` list for *kind* (plus optional extra kinds)."""
    out = []
    for k in (kind, *extra):
        label, exts = _KINDS.get(k, _KINDS["any"])
        if exts:
            out.append((label, _patterns(exts)))
    out.append(("All files", "*"))
    return out


def is_kind(path: str, kind: str) -> bool:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext in _KINDS.get(kind, ("", ()))[1]


# ── remembered folders ───────────────────────────────────────────────────────

def _last_dir(kind: str) -> str:
    try:
        from core.settings import get
        d = get(f"last_dir.{kind}", "") or get("last_dir.any", "")
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    return os.path.expanduser("~")


def _remember(kind: str, path: str) -> None:
    if not path:
        return
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        from core.settings import load_settings, save_settings
        data = load_settings()
        data[f"last_dir.{kind}"] = folder
        data["last_dir.any"] = folder
        save_settings(data)
    except Exception:
        pass


# ── native dialogs (Linux) ───────────────────────────────────────────────────

def _native_tool() -> Optional[str]:
    if _IS_WIN or _IS_MAC:
        return None
    if os.environ.get("CROSSFADER_NATIVE_DIALOGS", "1") == "0":
        return None
    try:
        from core.settings import get
        if not get("native_dialogs", True):
            return None
    except Exception:
        pass
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") + os.environ.get("DESKTOP_SESSION", "")).lower()
    order = ("kdialog", "zenity") if ("kde" in desktop or "plasma" in desktop) else ("zenity", "kdialog")
    for tool in order:
        if shutil.which(tool):
            return tool
    return None


def _run_blocking_keep_ui(cmd: list, parent=None) -> Optional[str]:
    """Run *cmd* in a thread while the Tk event loop keeps running."""
    import tkinter as tk
    root = parent or tk._default_root  # type: ignore[attr-defined]
    result: dict = {}
    finished = threading.Event()

    def _worker():
        # Never touch Tk from this thread: the main thread is inside
        # wait_variable(), not mainloop(), so cross-thread Tk calls fail.
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            result["rc"], result["out"] = r.returncode, r.stdout
        except OSError as exc:
            result["rc"], result["out"] = -1, ""
            result["err"] = str(exc)
        finally:
            finished.set()

    if root is None:
        _worker()
    else:
        done = tk.IntVar(root, value=0)

        def _poll():
            if finished.is_set():
                done.set(1)
            else:
                root.after(25, _poll)

        threading.Thread(target=_worker, daemon=True).start()
        root.after(25, _poll)
        try:
            root.wait_variable(done)
        except tk.TclError:
            finished.wait()
    if result.get("rc") == 0:
        return result.get("out", "").rstrip("\n")
    if result.get("rc") == -1:
        return None          # tool failed to start - caller falls back to Tk
    return ""                # cancelled


def _kdialog_filter(kind: str) -> str:
    label, exts = _KINDS.get(kind, _KINDS["any"])
    parts = []
    if exts:
        parts.append(f"{_patterns(exts)}|{label}")
    parts.append("*|All files")
    return "\n".join(parts)


def _zenity_filters(kind: str) -> list:
    label, exts = _KINDS.get(kind, _KINDS["any"])
    out = []
    if exts:
        out.append(f"--file-filter={label} | {_patterns(exts)}")
    out.append("--file-filter=All files | *")
    return out


def _native_open(tool: str, kind: str, title: str, start: str, many: bool, parent) -> Optional[list]:
    if tool == "kdialog":
        cmd = ["kdialog", "--title", title, "--getopenfilename", start + os.sep, _kdialog_filter(kind)]
        if many:
            cmd += ["--multiple", "--separate-output"]
    else:
        cmd = ["zenity", "--file-selection", f"--title={title}", f"--filename={start}{os.sep}",
               *_zenity_filters(kind)]
        if many:
            cmd += ["--multiple", "--separator=\n"]
    out = _run_blocking_keep_ui(cmd, parent)
    if out is None:
        return None
    return [p for p in out.splitlines() if p.strip()] if out else []


def _native_save(tool: str, kind: str, title: str, start_path: str, parent) -> Optional[str]:
    if tool == "kdialog":
        cmd = ["kdialog", "--title", title, "--getsavefilename", start_path, _kdialog_filter(kind)]
    else:
        cmd = ["zenity", "--file-selection", "--save", f"--title={title}",
               f"--filename={start_path}", *_zenity_filters(kind)]
    return _run_blocking_keep_ui(cmd, parent)


def _native_dir(tool: str, title: str, start: str, parent) -> Optional[str]:
    if tool == "kdialog":
        cmd = ["kdialog", "--title", title, "--getexistingdirectory", start]
    else:
        cmd = ["zenity", "--file-selection", "--directory", f"--title={title}", f"--filename={start}{os.sep}"]
    return _run_blocking_keep_ui(cmd, parent)


# ── public API ───────────────────────────────────────────────────────────────

def ask_open(kind: str = "media", *, title: str = "Open", parent=None,
             initialdir: Optional[str] = None) -> str:
    start = initialdir if initialdir and os.path.isdir(initialdir) else _last_dir(kind)
    tool = _native_tool()
    if tool:
        res = _native_open(tool, kind, title, start, False, parent)
        if res is not None:
            path = res[0] if res else ""
            _remember(kind, path)
            return path
    from tkinter import filedialog
    path = filedialog.askopenfilename(title=title, parent=parent, initialdir=start,
                                      filetypes=filetypes(kind)) or ""
    path = str(path)
    _remember(kind, path)
    return path


def ask_open_many(kind: str = "media", *, title: str = "Open", parent=None,
                  initialdir: Optional[str] = None) -> list:
    start = initialdir if initialdir and os.path.isdir(initialdir) else _last_dir(kind)
    tool = _native_tool()
    if tool:
        res = _native_open(tool, kind, title, start, True, parent)
        if res is not None:
            if res:
                _remember(kind, res[0])
            return res
    from tkinter import filedialog
    paths = filedialog.askopenfilenames(title=title, parent=parent, initialdir=start,
                                        filetypes=filetypes(kind)) or ()
    paths = [str(p) for p in paths]
    if paths:
        _remember(kind, paths[0])
    return paths


def ask_save(default_name: str = "", *, kind: str = "video", defaultextension: str = ".mp4",
             title: str = "Save as", parent=None, initialdir: Optional[str] = None) -> str:
    """Ask where to save.  The native/Tk dialog already confirms overwrites."""
    start = initialdir if initialdir and os.path.isdir(initialdir) else _last_dir(kind)
    if default_name and os.path.isabs(default_name):
        start, default_name = os.path.dirname(default_name), os.path.basename(default_name)
    tool = _native_tool()
    if tool:
        res = _native_save(tool, kind, title, os.path.join(start, default_name or ""), parent)
        if res is not None:
            if res and defaultextension and not os.path.splitext(res)[1]:
                res += defaultextension
            _remember(kind, res)
            if res:
                _CONFIRMED.add(os.path.abspath(res))
            return res
    from tkinter import filedialog
    path = filedialog.asksaveasfilename(title=title, parent=parent, initialdir=start,
                                        initialfile=default_name or None,
                                        defaultextension=defaultextension,
                                        filetypes=filetypes(kind)) or ""
    path = str(path)
    if path:
        _remember(kind, path)
        _CONFIRMED.add(os.path.abspath(path))
    return path


def ask_directory(*, title: str = "Choose folder", parent=None,
                  initialdir: Optional[str] = None, kind: str = "folder") -> str:
    start = initialdir if initialdir and os.path.isdir(initialdir) else _last_dir(kind)
    tool = _native_tool()
    if tool:
        res = _native_dir(tool, title, start, parent)
        if res is not None:
            _remember(kind, res)
            return res
    from tkinter import filedialog
    path = str(filedialog.askdirectory(title=title, parent=parent, initialdir=start) or "")
    _remember(kind, path)
    return path


#: Paths the user picked in a save dialog (overwrite already confirmed there).
_CONFIRMED: set = set()


def was_confirmed(path: str) -> bool:
    return bool(path) and os.path.abspath(path) in _CONFIRMED
