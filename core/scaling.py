"""
core/scaling.py - HiDPI support: one scale factor for every size in the UI.

The interface is designed at 96 DPI ("100 %").  Text is given in points, so it
grows on its own on a HiDPI screen; everything given in pixels (padding,
borders, fixed widths and heights, canvas drawing, table rows) has to grow by
the same factor or the text no longer fits around it.  ``px()`` converts those
design pixels into screen pixels:

    from core.scaling import px
    frame.pack(padx=px(12), pady=px(0, 8))     # 24 and (0, 16) at 200 %

Use it where a design size is handed to Tk, never on sizes measured from the
screen (``winfo_width()``, ``event.width``), which are already screen pixels.

The factor comes from the same place the fonts get their size:

* Linux (X11): Tk draws text with Xft, which uses the ``Xft.dpi`` X resource
  (set by GNOME, KDE, Xfce... from their scaling setting) or, without it, the
  screen's DPI.  Tk's own ``tk scaling`` is set to match.
* Windows: the system DPI (the app is DPI aware), which Tk already uses.
* macOS: points are already logical pixels, so the factor stays 1.

The ``ui_scale`` setting (or ``GDK_SCALE`` / ``QT_SCALE_FACTOR`` when the
desktop sets no DPI) overrides detection.  On Linux the fonts can only be made
bigger through ``Xft.dpi``, so :func:`prepare` arranges that before Tk starts.
"""
from __future__ import annotations

import logging
import os
import sys

_log = logging.getLogger("crossfader.scaling")

BASE_DPI = 96.0
MIN_FACTOR, MAX_FACTOR = 0.75, 4.0

_factor = 1.0
_override: float | None = None      # set by prepare()
_saved_xenv: str | None = None      # XENVIRONMENT before prepare() changed it
_changed_xenv = False


def factor() -> float:
    """Screen pixels per design pixel (1.0 at 100 %, 2.0 at 200 %)."""
    return _factor


def px(*values):
    """Design pixels -> screen pixels.

    ``px(8)`` -> ``16`` at 200 %; ``px(0, 8)`` or ``px((0, 8))`` -> ``(0, 16)``
    (padx/pady pairs).  Integers stay integers and a non-zero size never
    rounds down to 0; floats (line widths) stay floats.
    """
    if len(values) == 1:
        v = values[0]
        if isinstance(v, (tuple, list)):
            return tuple(_one(x) for x in v)
        return _one(v)
    return tuple(_one(x) for x in values)


def _one(v):
    if _factor == 1.0 or not v:
        return v
    if isinstance(v, int):
        r = int(round(v * _factor))
        if r == 0:
            r = 1 if v > 0 else -1
        return r
    return v * _factor


def scale_drawing(canvas, tag: str = "all") -> None:
    """Grow canvas items drawn at fixed design coordinates (a swatch, an
    icon) to screen size in one go.  Text keeps its font, which is already
    the right size; line widths do not change, so give those with px()."""
    if _factor != 1.0:
        canvas.scale(tag, 0, 0, _factor, _factor)


def set_factor(value: float) -> None:
    """Set the factor directly (tests, and :func:`apply`)."""
    global _factor
    value = float(value)
    if abs(value - 1.0) < 0.05:
        value = 1.0
    _factor = min(MAX_FACTOR, max(MIN_FACTOR, value))


# ─────────────────────────────────────────────────────────────────────────────
#  Detection
# ─────────────────────────────────────────────────────────────────────────────

def _is_x11() -> bool:
    return not sys.platform.startswith(("win", "darwin"))


def _requested_scale() -> float | None:
    """The ``ui_scale`` setting, when it is a number rather than "auto"."""
    try:
        from core import settings as _settings
        value = float(_settings.get("ui_scale", "auto"))
    except (TypeError, ValueError, ImportError):
        return None
    return value if MIN_FACTOR <= value <= MAX_FACTOR else None


def _env_scale() -> float | None:
    """A desktop-wide scale from the environment (GTK / Qt apps honour these)."""
    for var in ("GDK_SCALE", "QT_SCALE_FACTOR"):
        try:
            value = float(os.environ.get(var, ""))
        except ValueError:
            continue
        if value > 1.0:
            return min(value, MAX_FACTOR)
    return None


def _run_quiet(cmd: list) -> str:
    import subprocess
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def window_scale() -> int:
    """The desktop's whole-window scale (2 = everything twice the size) on
    desktops that scale windows rather than the font DPI: Xfce and MATE keep
    ``Xft.dpi`` at 96 and draw GTK apps twice as big.  1 if there is none."""
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" +
               os.environ.get("DESKTOP_SESSION", "")).upper()
    out = ""
    if "XFCE" in desktop:
        out = _run_quiet(["xfconf-query", "-c", "xsettings", "-p", "/Gdk/WindowScalingFactor"])
    elif "MATE" in desktop:
        out = _run_quiet(["gsettings", "get", "org.mate.interface", "window-scaling-factor"])
        out = out.split()[-1] if out else ""          # "uint32 2" -> "2"; 0 means "auto"
    elif "GNOME" in desktop or "CINNAMON" in desktop or "UNITY" in desktop:
        import re
        text = _run_quiet(["gsettings", "get", "org.gnome.settings-daemon.plugins.xsettings", "overrides"])
        m = re.search(r"'Gdk/WindowScalingFactor':\s*<(\d+)>", text)
        out = m.group(1) if m else ""
    try:
        value = int(out)
    except ValueError:
        return 1
    return value if 1 < value <= MAX_FACTOR else 1


def xft_dpi() -> float | None:
    """The ``Xft.dpi`` X resource, read the way Xft itself reads it (the
    server's resources plus the ``XENVIRONMENT`` file); None if unset or no
    X display can be opened."""
    if not _is_x11() or not os.environ.get("DISPLAY"):
        return None
    try:
        import ctypes
        import ctypes.util
        lib = None
        for name in ("libX11.so.6", ctypes.util.find_library("X11")):
            if not name:
                continue
            try:
                lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if lib is None:
            return None
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XGetDefault.restype = ctypes.c_char_p
        lib.XGetDefault.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        dpy = lib.XOpenDisplay(None)
        if not dpy:
            return None
        try:
            raw = lib.XGetDefault(dpy, b"Xft", b"dpi")
        finally:
            lib.XCloseDisplay(dpy)
        value = float(raw.decode("ascii", "replace")) if raw else 0.0
        return value if value > 0 else None
    except Exception:                     # noqa: BLE001 - detection must never stop startup
        _log.debug("could not read Xft.dpi", exc_info=True)
        return None


def _user_resource_file() -> str | None:
    """The per-user resource file Xlib merges when XENVIRONMENT is unset."""
    import socket
    path = os.path.expanduser(f"~/.Xdefaults-{socket.gethostname()}")
    return path if os.path.isfile(path) else None


def _set_xft_dpi(dpi: float) -> None:
    """Make Xft (and so Tk's fonts) use *dpi*: XENVIRONMENT names a resource
    file that Xlib merges over the server's resources.  Whatever that file
    would otherwise have been is copied in first, so nothing else changes."""
    global _saved_xenv, _changed_xenv
    from core.paths import cache_dir
    original = os.environ.get("XENVIRONMENT")
    source = original if original and os.path.isfile(original) else (
        None if original else _user_resource_file())
    text = ""
    if source:
        try:
            with open(source, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            text = ""
    path = os.path.join(cache_dir(), "xresources-scale")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.rstrip("\n") + ("\n" if text else ""))
        fh.write(f"Xft.dpi: {dpi:g}\n")
    _saved_xenv = original
    _changed_xenv = True
    os.environ["XENVIRONMENT"] = path


def prepare() -> None:
    """Call before the Tk root is created: works out whether the scale must
    be overridden and, on Linux, sets the font DPI to match."""
    global _override
    _override = _requested_scale()
    if _override is None and _is_x11():
        dpi = xft_dpi()
        ws = window_scale()
        if ws > 1 and (dpi or BASE_DPI) < BASE_DPI * ws * 0.9:
            # Window scaling with the font DPI left alone (Xfce, MATE): GTK
            # apps multiply the two, so do the same
            _override = ws * (dpi or BASE_DPI) / BASE_DPI
        elif dpi is None:
            _override = _env_scale()
    if _override is None or not _is_x11():
        return
    try:
        _set_xft_dpi(BASE_DPI * _override)
    except OSError:
        _log.warning("could not apply ui_scale %s to fonts", _override, exc_info=True)


def apply(root) -> float:
    """Call right after the Tk root is created: sets ``tk scaling`` and the
    factor used by :func:`px`.  Returns the factor."""
    tk_call = root.tk.call
    if sys.platform == "darwin":
        value = _override or 1.0
        if _override:
            tk_call("tk", "scaling", float(tk_call("tk", "scaling")) * _override)
    elif sys.platform.startswith("win"):
        if _override:
            tk_call("tk", "scaling", BASE_DPI * _override / 72.0)
        value = float(tk_call("tk", "scaling")) * 72.0 / BASE_DPI
    else:
        dpi = xft_dpi() if not _override else BASE_DPI * _override
        if not dpi:
            dpi = float(tk_call("tk", "scaling")) * 72.0     # Xft's own fallback: the screen DPI
        tk_call("tk", "scaling", dpi / 72.0)
        value = dpi / BASE_DPI
        _undo_tk_font_doubling(tk_call)
        if _changed_xenv:
            # Fonts read the resources once, on first use: make sure that has
            # happened, then stop passing the file on to programs we start.
            try:
                tk_call("font", "measure", "TkDefaultFont", "M")
            except Exception:                     # noqa: BLE001
                pass
            _restore_xenvironment()
    set_factor(value)
    _log.info("UI scale %.2f", _factor)
    return _factor


def _undo_tk_font_doubling(tk_call) -> None:
    """Tk 9 doubles its standard fonts (TkDefaultFont...) when it sees a
    desktop window scale of 2.  The font DPI already makes text big enough
    here, so put them back to their normal point sizes."""
    try:
        factor_ = int(tk_call("::tk::FontScalingFactor"))
    except Exception:                         # noqa: BLE001 - Tk 8.6 has no such command
        return
    if factor_ <= 1:
        return
    for name in tk_call("font", "names"):
        name = str(name)
        if not name.startswith("Tk"):
            continue
        try:
            size = int(tk_call("font", "configure", name, "-size"))
            tk_call("font", "configure", name, "-size", round(size / factor_))
        except Exception:                     # noqa: BLE001
            continue


def _restore_xenvironment() -> None:
    global _changed_xenv
    if _saved_xenv is None:
        os.environ.pop("XENVIRONMENT", None)
    else:
        os.environ["XENVIRONMENT"] = _saved_xenv
    _changed_xenv = False
