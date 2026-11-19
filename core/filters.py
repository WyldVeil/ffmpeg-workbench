"""
core/filters.py - Safe construction of FFmpeg filter arguments and side files.

FFmpeg parses a filtergraph in two levels before a filter sees its options:

1. the *filtergraph* parser splits on ``[ ] , ;`` and handles quotes and
   backslashes, then
2. the *option* parser splits ``key=value:key=value`` on ``:`` and again
   handles quotes and backslashes.

A file path or free text placed into a filter therefore needs escaping for
both levels.  :func:`filter_value` does exactly that, and the higher-level
helpers (:func:`subtitles_filter`, :func:`lut3d_filter`, :func:`movie_source`,
:func:`drawtext_filter`) build complete, correctly escaped filters.  Since the
app always passes argv lists (never a shell), no shell quoting is needed.

Other helpers:
* :func:`write_concat_list` - concat-demuxer list files with proper quoting.
* :func:`write_ffmetadata` - chapter/metadata files with ``= ; # \\`` escaped.
* :func:`find_font` - a usable font file on Windows, Linux and macOS
  (bundled fonts first, then the system, via fontconfig on Linux).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, Optional, Sequence

_IS_WIN = sys.platform.startswith("win")


# ─────────────────────────────────────────────────────────────────────────────
#  Escaping
# ─────────────────────────────────────────────────────────────────────────────

def _escape_option_level(value: str) -> str:
    # option parser: backslash, single quote and ':' are special
    return (value.replace("\\", "\\\\")
                 .replace("'", "\\'")
                 .replace(":", "\\:"))


def _escape_graph_level(value: str) -> str:
    out = []
    for ch in value:
        if ch in "\\'[],;":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def filter_value(value: str) -> str:
    """Escape any string for use as a filter option value inside a
    ``-vf``/``-af``/``-filter_complex`` argument."""
    return _escape_graph_level(_escape_option_level(str(value)))


def filter_path(path: str) -> str:
    """Escape a file path for a filter option (``subtitles=``, ``lut3d=``,
    ``movie=``, ``fontfile=`` …).  Backslashes become forward slashes, which
    every FFmpeg build accepts on Windows too."""
    p = os.path.abspath(path)
    if _IS_WIN:
        p = p.replace("\\", "/")
    return filter_value(p)


def subtitles_filter(path: str, *, force_style: str = "", stream_index: Optional[int] = None,
                     fonts_dir: Optional[str] = None, charenc: Optional[str] = None) -> str:
    """``subtitles=`` filter for an external subtitle file (SRT/ASS/VTT) or
    for a subtitle stream of a media file (*stream_index*)."""
    ext = os.path.splitext(path)[1].lower()
    name = "ass" if ext in (".ass", ".ssa") and not force_style and stream_index is None else "subtitles"
    opts = [f"filename={filter_path(path)}"]
    if stream_index is not None and name == "subtitles":
        opts.append(f"si={int(stream_index)}")
    if force_style and name == "subtitles":
        opts.append(f"force_style={filter_value(force_style)}")
    if fonts_dir:
        opts.append(f"fontsdir={filter_path(fonts_dir)}")
    if charenc and name == "subtitles":
        opts.append(f"charenc={filter_value(charenc)}")
    return f"{name}=" + ":".join(opts)


def lut3d_filter(path: str, interp: str = "tetrahedral") -> str:
    return f"lut3d=file={filter_path(path)}:interp={interp}"


def movie_source(path: str, *, loop: int = 0, stream: str = "") -> str:
    """``movie=`` source (use as the start of a filter chain)."""
    s = f"movie=filename={filter_path(path)}"
    if loop:
        s += f":loop={int(loop)}"
    if stream:
        s += f":streams={filter_value(stream)}"
    return s


def amovie_source(path: str, *, loop: int = 0) -> str:
    s = f"amovie=filename={filter_path(path)}"
    if loop:
        s += f":loop={int(loop)}"
    return s


def ass_style(*, font: str = "Arial", size: int = 24, primary: str = "&H00FFFFFF",
              outline_colour: str = "&H00000000", outline: float = 2.0,
              shadow: float = 0.0, bold: bool = False, alignment: int = 2,
              margin_v: int = 20, back_colour: str = "&H80000000",
              border_style: int = 1) -> str:
    """Build a ``force_style`` value for the subtitles filter."""
    return (f"FontName={font},FontSize={int(size)},PrimaryColour={primary},"
            f"OutlineColour={outline_colour},BackColour={back_colour},"
            f"BorderStyle={int(border_style)},Outline={outline:g},Shadow={shadow:g},"
            f"Bold={-1 if bold else 0},Alignment={int(alignment)},MarginV={int(margin_v)}")


def hex_to_ass_colour(hex_colour: str, alpha: int = 0) -> str:
    """``"#FF8800"`` -> ``"&H000088FF"`` (ASS is AABBGGRR, alpha 0 = opaque)."""
    h = hex_colour.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def drawtext_text_file(text: str, directory: Optional[str] = None) -> str:
    """Write *text* to a UTF-8 file for ``drawtext=textfile=`` and return its
    path.  Using a file sidesteps every text-escaping pitfall (quotes, colons,
    percent signs, backslashes, emoji, newlines)."""
    fd, path = tempfile.mkstemp(prefix="drawtext_", suffix=".txt", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def drawtext_filter(*, text: Optional[str] = None, textfile: Optional[str] = None,
                    fontfile: Optional[str] = None, font: Optional[str] = None,
                    fontsize: str | int = 48, fontcolor: str = "white",
                    x: str = "(w-text_w)/2", y: str = "(h-text_h)/2",
                    box: bool = False, boxcolor: str = "black@0.5", boxborderw: int = 10,
                    borderw: int = 0, bordercolor: str = "black",
                    shadowx: int = 0, shadowy: int = 0, shadowcolor: str = "black",
                    alpha: Optional[str] = None, enable: Optional[str] = None,
                    expansion: str = "none", extra: Sequence[str] = ()) -> str:
    """A fully escaped ``drawtext`` filter.

    Prefer *textfile* (see :func:`drawtext_text_file`) for user text; *text*
    is escaped for literal display (``expansion=none`` by default so ``%``
    is not interpreted).  *x*, *y*, *alpha*, *enable* and *fontsize* are
    expressions and are escaped at the filtergraph level only.
    """
    if textfile is None and text is None:
        raise ValueError("text or textfile required")
    opts = []
    if fontfile is None and font:
        fontfile = find_font(font)
    if fontfile:
        opts.append(f"fontfile={filter_path(fontfile)}")
    if textfile is not None:
        opts.append(f"textfile={filter_path(textfile)}")
    else:
        opts.append(f"text={filter_value(text or '')}")
    opts.append(f"expansion={expansion}")
    opts += [f"fontsize={_expr(str(fontsize))}", f"fontcolor={filter_value(fontcolor)}",
             f"x={_expr(x)}", f"y={_expr(y)}"]
    if box:
        opts += ["box=1", f"boxcolor={filter_value(boxcolor)}", f"boxborderw={int(boxborderw)}"]
    if borderw:
        opts += [f"borderw={int(borderw)}", f"bordercolor={filter_value(bordercolor)}"]
    if shadowx or shadowy:
        opts += [f"shadowx={int(shadowx)}", f"shadowy={int(shadowy)}",
                 f"shadowcolor={filter_value(shadowcolor)}"]
    if alpha is not None:
        opts.append(f"alpha={_expr(alpha)}")
    if enable is not None:
        opts.append(f"enable={_expr(enable)}")
    opts += list(extra)
    return "drawtext=" + ":".join(opts)


def _expr(expr: str) -> str:
    """Escape an expression (which may contain ',' and ':' inside function
    calls such as ``between(t,1,2)`` or ``if(lt(t,2),...)``)."""
    return filter_value(expr)


def enable_between(start: float, end: float) -> str:
    """``between(t,START,END)`` timeline expression (escape with _expr)."""
    return f"between(t,{start:.3f},{end:.3f})"


# ─────────────────────────────────────────────────────────────────────────────
#  Side files
# ─────────────────────────────────────────────────────────────────────────────

def concat_line(path: str) -> str:
    """One ``file '...'`` line for an FFmpeg concat-demuxer list."""
    p = os.path.abspath(path)
    return "file '" + p.replace("'", "'\\''") + "'"


def write_concat_list(paths: Iterable[str], list_path: Optional[str] = None, *,
                      durations: Optional[Sequence[Optional[float]]] = None,
                      inpoints: Optional[Sequence[Optional[float]]] = None,
                      outpoints: Optional[Sequence[Optional[float]]] = None) -> str:
    """Write a concat-demuxer list (use with ``-f concat -safe 0 -i LIST``)."""
    if list_path is None:
        fd, list_path = tempfile.mkstemp(prefix="concat_", suffix=".txt")
        os.close(fd)
    with open(list_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("ffconcat version 1.0\n")
        for i, p in enumerate(paths):
            fh.write(concat_line(p) + "\n")
            if inpoints and i < len(inpoints) and inpoints[i] is not None:
                fh.write(f"inpoint {inpoints[i]:.6f}\n")
            if outpoints and i < len(outpoints) and outpoints[i] is not None:
                fh.write(f"outpoint {outpoints[i]:.6f}\n")
            if durations and i < len(durations) and durations[i] is not None:
                fh.write(f"duration {durations[i]:.6f}\n")
    return list_path


def _meta_escape(value: str) -> str:
    out = []
    for ch in str(value):
        if ch in "=;#\\\n":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def write_ffmetadata(path: str, *, chapters: Sequence[tuple] = (),
                     tags: Optional[dict] = None) -> str:
    """Write an FFMETADATA1 file.  *chapters* is a sequence of
    ``(start_seconds, end_seconds, title)``; use with
    ``-i META -map_metadata 1 -map_chapters 1``."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(";FFMETADATA1\n")
        for k, v in (tags or {}).items():
            fh.write(f"{_meta_escape(k)}={_meta_escape(v)}\n")
        for start, end, title in chapters:
            fh.write("\n[CHAPTER]\nTIMEBASE=1/1000\n")
            fh.write(f"START={int(round(start * 1000))}\n")
            fh.write(f"END={int(round(end * 1000))}\n")
            fh.write(f"title={_meta_escape(title)}\n")
    return path


# ─────────────────────────────────────────────────────────────────────────────
#  Fonts
# ─────────────────────────────────────────────────────────────────────────────

def _bundled_font_dir() -> str:
    from core.paths import resource_path
    return resource_path("assets", "fonts")


def bundled_fonts() -> dict:
    """``{display name: path}`` for fonts shipped in assets/fonts."""
    out = {}
    d = _bundled_font_dir()
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".ttf", ".otf")):
                out[os.path.splitext(f)[0]] = os.path.join(d, f)
    return out


def _system_font_dirs() -> list:
    if _IS_WIN:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        local = os.environ.get("LOCALAPPDATA", "")
        return [os.path.join(windir, "Fonts"),
                os.path.join(local, "Microsoft", "Windows", "Fonts") if local else ""]
    if sys.platform == "darwin":
        return ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
                "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    return ["/usr/share/fonts", "/usr/local/share/fonts",
            os.path.expanduser("~/.local/share/fonts"), os.path.expanduser("~/.fonts")]


_FONT_CACHE: dict = {}

# Reasonable cross-platform stand-ins for commonly requested fonts.
_ALIASES = {
    "arial": ("Arial", "LiberationSans-Regular", "DejaVuSans", "Inter", "OpenSans"),
    "arial bold": ("Arial Bold", "arialbd", "LiberationSans-Bold", "DejaVuSans-Bold", "Inter-Bold"),
    "impact": ("Impact", "Anton", "BebasNeue", "LeagueGothic"),
    "sans": ("Inter", "OpenSans", "DejaVuSans", "LiberationSans-Regular", "Arial"),
    "sans bold": ("Inter-Bold", "OpenSans-Bold", "DejaVuSans-Bold", "Arial Bold"),
    "mono": ("DejaVuSansMono", "LiberationMono-Regular", "Consolas", "Menlo"),
}


def find_font(name: str = "sans") -> Optional[str]:
    """Return a font file for *name* (a bundled font name such as
    ``"Montserrat-Bold"``, a family such as ``"Arial"``, an alias like
    ``"sans bold"``, or a path).  Never returns a path that does not exist."""
    if not name:
        name = "sans"
    if os.path.isfile(name):
        return name
    key = name.lower().strip()
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = list(_ALIASES.get(key, ())) + [name]
    bundled = {k.lower(): v for k, v in bundled_fonts().items()}
    result = None
    for cand in candidates:
        c = cand.lower()
        if c in bundled:
            result = bundled[c]
            break
        if c.replace(" ", "") in bundled:
            result = bundled[c.replace(" ", "")]
            break
    if result is None and not _IS_WIN and shutil.which("fc-match"):
        for cand in candidates:
            try:
                r = subprocess.run(["fc-match", "-f", "%{file}", cand], capture_output=True,
                                   text=True, timeout=5, stdin=subprocess.DEVNULL)
                path = r.stdout.strip()
                if path and os.path.isfile(path):
                    result = path
                    break
            except (OSError, subprocess.TimeoutExpired):
                pass
    if result is None:
        wanted = [c.lower().replace(" ", "") for c in candidates]
        for d in _system_font_dirs():
            if not d or not os.path.isdir(d):
                continue
            for root, _dirs, files in os.walk(d):
                for f in files:
                    stem, ext = os.path.splitext(f)
                    if ext.lower() in (".ttf", ".otf") and stem.lower().replace(" ", "") in wanted:
                        result = os.path.join(root, f)
                        break
                if result:
                    break
            if result:
                break
    if result is None:
        fallback = bundled.get("inter") or next(iter(bundled.values()), None)
        result = fallback
    _FONT_CACHE[key] = result
    return result
