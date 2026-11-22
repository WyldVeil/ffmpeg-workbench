"""
tabs/kit/dialogs.py - File dialogs that feel native and behave the same everywhere.

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
import sys
import threading
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from core import proc as _proc
from core import settings, tasks

if TYPE_CHECKING:
    import tkinter as tk

#: The window a dialog belongs to (None: the main window)
Parent: TypeAlias = "tk.Misc | None"
#: A file-type entry's patterns: "*.mp4 *.mov" or ["*.mp4", "*.mov"]
Patterns = str | Sequence[str]

_IS_WIN = sys.platform.startswith("win")
_IS_MAC = sys.platform == "darwin"

VIDEO_EXTS = (
    "mp4",
    "mov",
    "mkv",
    "avi",
    "webm",
    "m4v",
    "wmv",
    "flv",
    "mpg",
    "mpeg",
    "ts",
    "mts",
    "m2ts",
    "3gp",
    "ogv",
    "vob",
    "mxf",
    "gif",
    "y4m",
)
AUDIO_EXTS = (
    "mp3",
    "wav",
    "flac",
    "aac",
    "m4a",
    "ogg",
    "oga",
    "opus",
    "wma",
    "aif",
    "aiff",
    "alac",
    "ac3",
    "eac3",
    "mka",
    "caf",
    "amr",
)
IMAGE_EXTS = ("png", "jpg", "jpeg", "bmp", "webp", "tif", "tiff", "gif", "avif", "heic")
SUB_EXTS = ("srt", "ass", "ssa", "vtt", "sub", "sbv")
LUT_EXTS = ("cube", "3dl", "dat", "m3d", "csp", "png", "tif", "tiff")
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
    if _IS_WIN:  # Windows matching is already case-insensitive
        return " ".join(f"*.{e}" for e in exts)
    return " ".join(f"*.{e} *.{e.upper()}" for e in exts)


def filetypes(kind: str = "media", extra: Sequence[str] = ()) -> list[tuple[str, str]]:
    """Tk-style ``filetypes`` list for *kind* (plus optional extra kinds)."""
    out: list[tuple[str, str]] = []
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
    d = settings.get(f"last_dir.{kind}", "") or settings.get("last_dir.any", "")
    if isinstance(d, str) and d and os.path.isdir(d):
        return d
    return os.path.expanduser("~")


def _start_dir(kw: Mapping[str, Any], kind: str) -> str:
    """The folder a dialog opens in: the one asked for, else the last one used."""
    asked = kw.get("initialdir")
    return asked if isinstance(asked, str) and os.path.isdir(asked) else _last_dir(kind)


def _remember(kind: str, path: str) -> None:
    if not path:
        return
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    settings.update({f"last_dir.{kind}": folder, "last_dir.any": folder})


# ── native dialogs (Linux) ───────────────────────────────────────────────────


def _native_tool() -> str | None:
    if _IS_WIN or _IS_MAC:
        return None
    if os.environ.get("CROSSFADER_NATIVE_DIALOGS", "1") == "0":
        return None
    if not settings.NATIVE_DIALOGS.get():
        return None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP", "") + os.environ.get("DESKTOP_SESSION", "")
    ).lower()
    order = (
        ("kdialog", "zenity")
        if ("kde" in desktop or "plasma" in desktop)
        else ("zenity", "kdialog")
    )
    for tool in order:
        if shutil.which(tool):
            return tool
    return None


def _run_blocking_keep_ui(cmd: list[str], parent: Parent = None) -> str | None:
    """Run *cmd* in a thread while the Tk event loop keeps running."""
    import tkinter as tk

    from core import ui

    root = parent or ui.tk_default_root()
    result: dict[str, Any] = {}
    finished = threading.Event()

    def _worker():
        # Never touch Tk from this thread: the main thread is inside
        # wait_variable(), not mainloop(), so cross-thread Tk calls fail.
        try:
            from core.procenv import child_env

            r = _proc.run(cmd, timeout=None, env=child_env())
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

        tasks.submit(_worker)
        root.after(25, _poll)
        try:
            root.wait_variable(done)
        except tk.TclError:
            finished.wait()
    if result.get("rc") == 0:
        return result.get("out", "").rstrip("\n")
    if result.get("rc") == -1:
        return None  # tool failed to start - caller falls back to Tk
    return ""  # cancelled


def _kdialog_filter(kind: str) -> str:
    label, exts = _KINDS.get(kind, _KINDS["any"])
    parts: list[str] = []
    if exts:
        parts.append(f"{_patterns(exts)}|{label}")
    parts.append("*|All files")
    return "\n".join(parts)


def _zenity_filters(kind: str) -> list[str]:
    label, exts = _KINDS.get(kind, _KINDS["any"])
    out: list[str] = []
    if exts:
        out.append(f"--file-filter={label} | {_patterns(exts)}")
    out.append("--file-filter=All files | *")
    return out


def _native_open(
    tool: str, kind: str, title: str, start: str, many: bool, parent: Parent
) -> list[str] | None:
    if tool == "kdialog":
        cmd = [
            "kdialog",
            "--title",
            title,
            "--getopenfilename",
            start + os.sep,
            _kdialog_filter(kind),
        ]
        if many:
            cmd += ["--multiple", "--separate-output"]
    else:
        cmd = [
            "zenity",
            "--file-selection",
            f"--title={title}",
            f"--filename={start}{os.sep}",
            *_zenity_filters(kind),
        ]
        if many:
            cmd += ["--multiple", "--separator=\n"]
    out = _run_blocking_keep_ui(cmd, parent)
    if out is None:
        return None
    return [p for p in out.splitlines() if p.strip()] if out else []


def _native_save(tool: str, kind: str, title: str, start_path: str, parent: Parent) -> str | None:
    if tool == "kdialog":
        cmd = ["kdialog", "--title", title, "--getsavefilename", start_path, _kdialog_filter(kind)]
    else:
        cmd = [
            "zenity",
            "--file-selection",
            "--save",
            f"--title={title}",
            f"--filename={start_path}",
            *_zenity_filters(kind),
        ]
    return _run_blocking_keep_ui(cmd, parent)


def _native_dir(tool: str, title: str, start: str, parent: Parent) -> str | None:
    if tool == "kdialog":
        cmd = ["kdialog", "--title", title, "--getexistingdirectory", start]
    else:
        cmd = [
            "zenity",
            "--file-selection",
            "--directory",
            f"--title={title}",
            f"--filename={start}{os.sep}",
        ]
    return _run_blocking_keep_ui(cmd, parent)


# ── public API ───────────────────────────────────────────────────────────────


def ask_open(
    kind: str = "media",
    *,
    title: str = "Open",
    parent: Parent = None,
    initialdir: str | None = None,
) -> str:
    start = initialdir if initialdir and os.path.isdir(initialdir) else _last_dir(kind)
    tool = _native_tool()
    if tool:
        res = _native_open(tool, kind, title, start, False, parent)
        if res is not None:
            path = res[0] if res else ""
            _remember(kind, path)
            return path
    from tkinter import filedialog

    path = (
        filedialog.askopenfilename(
            title=title, parent=parent, initialdir=start, filetypes=filetypes(kind)
        )
        or ""
    )
    path = str(path)
    _remember(kind, path)
    return path


def ask_open_many(
    kind: str = "media",
    *,
    title: str = "Open",
    parent: Parent = None,
    initialdir: str | None = None,
) -> list[str]:
    start = initialdir if initialdir and os.path.isdir(initialdir) else _last_dir(kind)
    tool = _native_tool()
    if tool:
        res = _native_open(tool, kind, title, start, True, parent)
        if res is not None:
            if res:
                _remember(kind, res[0])
            return res
    from tkinter import filedialog

    paths = (
        filedialog.askopenfilenames(
            title=title, parent=parent, initialdir=start, filetypes=filetypes(kind)
        )
        or ()
    )
    paths = [str(p) for p in paths]
    if paths:
        _remember(kind, paths[0])
    return paths


def ask_save(
    default_name: str = "",
    *,
    kind: str = "video",
    defaultextension: str = ".mp4",
    title: str = "Save as",
    parent: Parent = None,
    initialdir: str | None = None,
) -> str:
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

    path = (
        filedialog.asksaveasfilename(
            title=title,
            parent=parent,
            initialdir=start,
            initialfile=default_name or None,
            defaultextension=defaultextension,
            filetypes=filetypes(kind),
        )
        or ""
    )
    path = str(path)
    if path:
        _remember(kind, path)
        _CONFIRMED.add(os.path.abspath(path))
    return path


def ask_directory(
    *,
    title: str = "Choose folder",
    parent: Parent = None,
    initialdir: str | None = None,
    kind: str = "folder",
) -> str:
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
_CONFIRMED: set[str] = set()


def was_confirmed(path: str) -> bool:
    return bool(path) and os.path.abspath(path) in _CONFIRMED


# ── Drop-in replacements for tkinter.filedialog ──────────────────────────────
# Same keyword arguments as tkinter.filedialog.*, so existing call sites can
# switch with a one-word change and gain native Linux dialogs, case-insensitive
# patterns and remembered folders.


def _expand_case(patterns: str | Iterable[str]) -> str:
    items = patterns.split() if isinstance(patterns, str) else list(patterns)
    out: list[str] = []
    for p in items:
        out.append(p)
        if not _IS_WIN and "." in p and p not in ("*.*",) and p.upper() != p:
            out.append(p.upper())
    return " ".join(dict.fromkeys(out))


def _clean_patterns(pats: Patterns) -> str:
    """Keep real glob patterns only: a pattern that came through the
    translation layer can be a sentence in some languages, which would make
    the filter match nothing.  On Linux '*.*' also hides files without an
    extension, so it becomes '*'."""
    items = pats.split() if isinstance(pats, str) else [str(p) for p in pats]
    good = [p for p in items if "*" in p or p.startswith(".")]
    if not good:
        return "*"
    if not _IS_WIN:
        good = ["*" if p == "*.*" else p for p in good]
    return " ".join(good)


def _normalise_filetypes(filetypes: object) -> list[tuple[str, str]]:
    norm: list[tuple[str, str]] = []
    entries = cast("Iterable[object]", filetypes) if isinstance(filetypes, (list, tuple)) else ()
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(cast("Sequence[object]", entry)) != 2:
            continue
        label, pats = cast("tuple[object, Patterns]", tuple(cast("Sequence[object]", entry)))
        norm.append((str(label), _expand_case(_clean_patterns(pats))))
    if not any(p.strip() in ("*", "*.*") for _, p in norm):
        norm.append(("All files", "*"))
    return norm


def _kind_from_filetypes(filetypes: Sequence[tuple[str, str]]) -> str:
    exts: set[str] = set()
    for _label, pats in filetypes:
        for p in pats.split():
            if p.startswith("*."):
                exts.add(p[2:].lower())
    for kind in ("subtitle", "lut", "midi", "soundfont", "font", "image", "audio", "video"):
        if exts & set(_KINDS[kind][1]):
            if (
                kind in ("audio", "video")
                and exts & set(_KINDS["video"][1])
                and exts & set(_KINDS["audio"][1])
            ):
                return "media"
            return kind
    return "any"


def _native_filter_from_types(tool: str, filetypes: Sequence[tuple[str, str]]) -> list[str]:
    if tool == "kdialog":
        return ["\n".join(f"{p}|{label}" for label, p in filetypes)]
    return [f"--file-filter={label} | {p}" for label, p in filetypes]


def askopenfilename(**kw: Any) -> str:
    filetypes = _normalise_filetypes(kw.pop("filetypes", None))
    kind = _kind_from_filetypes(filetypes)
    title = kw.get("title") or "Open"
    start = _start_dir(kw, kind)
    tool = _native_tool()
    if tool:
        if tool == "kdialog":
            cmd = [
                "kdialog",
                "--title",
                title,
                "--getopenfilename",
                start + os.sep,
                *_native_filter_from_types(tool, filetypes),
            ]
        else:
            cmd = [
                "zenity",
                "--file-selection",
                f"--title={title}",
                f"--filename={start}{os.sep}",
                *_native_filter_from_types(tool, filetypes),
            ]
        res = _run_blocking_keep_ui(cmd, kw.get("parent"))
        if res is not None:
            path = res.splitlines()[0] if res else ""
            _remember(kind, path)
            return path
    from tkinter import filedialog

    kw.setdefault("initialdir", start)
    path = str(filedialog.askopenfilename(filetypes=filetypes, **kw) or "")
    _remember(kind, path)
    return path


def askopenfilenames(**kw: Any) -> tuple[str, ...]:
    filetypes = _normalise_filetypes(kw.pop("filetypes", None))
    kind = _kind_from_filetypes(filetypes)
    title = kw.get("title") or "Open"
    start = _start_dir(kw, kind)
    tool = _native_tool()
    if tool:
        if tool == "kdialog":
            cmd = [
                "kdialog",
                "--title",
                title,
                "--getopenfilename",
                start + os.sep,
                *_native_filter_from_types(tool, filetypes),
                "--multiple",
                "--separate-output",
            ]
        else:
            cmd = [
                "zenity",
                "--file-selection",
                f"--title={title}",
                f"--filename={start}{os.sep}",
                *_native_filter_from_types(tool, filetypes),
                "--multiple",
                "--separator=\n",
            ]
        res = _run_blocking_keep_ui(cmd, kw.get("parent"))
        if res is not None:
            paths = tuple(p for p in res.splitlines() if p.strip())
            if paths:
                _remember(kind, paths[0])
            return paths
    from tkinter import filedialog

    kw.setdefault("initialdir", start)
    paths = tuple(str(p) for p in (filedialog.askopenfilenames(filetypes=filetypes, **kw) or ()))
    if paths:
        _remember(kind, paths[0])
    return paths


def asksaveasfilename(**kw: Any) -> str:
    filetypes = _normalise_filetypes(kw.pop("filetypes", None))
    kind = _kind_from_filetypes(filetypes)
    title = kw.get("title") or "Save as"
    defext = kw.get("defaultextension") or ""
    initialfile = kw.get("initialfile") or ""
    start = _start_dir(kw, kind)
    if initialfile and os.path.isabs(initialfile):
        start, initialfile = os.path.dirname(initialfile), os.path.basename(initialfile)
    tool = _native_tool()
    if tool:
        target = os.path.join(start, initialfile)
        if tool == "kdialog":
            cmd = [
                "kdialog",
                "--title",
                title,
                "--getsavefilename",
                target,
                *_native_filter_from_types(tool, filetypes),
            ]
        else:
            cmd = [
                "zenity",
                "--file-selection",
                "--save",
                f"--title={title}",
                f"--filename={target}",
                *_native_filter_from_types(tool, filetypes),
            ]
        res = _run_blocking_keep_ui(cmd, kw.get("parent"))
        if res is not None:
            if res and defext and not os.path.splitext(res)[1]:
                res += defext if defext.startswith(".") else "." + defext
            if res:
                _remember(kind, res)
                _CONFIRMED.add(os.path.abspath(res))
            return res
    from tkinter import filedialog

    kw.setdefault("initialdir", start)
    if initialfile:
        kw["initialfile"] = initialfile
    path = str(filedialog.asksaveasfilename(filetypes=filetypes, **kw) or "")
    if path:
        _remember(kind, path)
        _CONFIRMED.add(os.path.abspath(path))
    return path


def askdirectory(**kw: Any) -> str:
    title = kw.get("title") or "Choose folder"
    start = _start_dir(kw, "folder")
    tool = _native_tool()
    if tool:
        res = _native_dir(tool, title, start, kw.get("parent"))
        if res is not None:
            _remember("folder", res)
            return res
    from tkinter import filedialog

    kw.setdefault("initialdir", start)
    path = str(filedialog.askdirectory(**kw) or "")
    _remember("folder", path)
    return path


def ask_overwrite(existing: str, alternative: str, *, parent: Any = None) -> bool | None:
    """The "file already exists" question for core.outputs.resolve_output:
    True = overwrite, False = save as *alternative*, None = don't render."""
    from tkinter import messagebox

    return messagebox.askyesnocancel(
        "File already exists",
        f"{os.path.basename(existing)} already exists.\n\n"
        f"Yes — overwrite it\nNo — save as {os.path.basename(alternative)}\n"
        "Cancel — don't render",
        parent=parent,
    )
