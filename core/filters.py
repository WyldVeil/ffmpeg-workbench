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
import sys
import tempfile
from collections.abc import Iterable, Sequence

from core import proc as _proc

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


def subtitles_filter(path: str, *, force_style: str = "", stream_index: int | None = None,
                     fonts_dir: str | None = None, charenc: str | None = None) -> str:
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


STILL_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".avif", ".heic")


def preview_movie(path: str) -> str:
    """Start of a preview filter chain that reads a second file (logo,
    background, inset) with ``movie=``, ending in a comma.

    Previews keep the source timestamps, so the frame shown at 0:05 has a
    timestamp of 5 s.  A still image gives a single frame at 0 s, which
    runs out before then and the preview shows nothing; stills are
    therefore repeated for as long as the video needs them."""
    s = f"movie=filename={filter_path(path)},"
    if os.path.splitext(path)[1].lower() in STILL_IMAGE_EXTS:
        s += "loop=loop=-1:size=1,"
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


def drawtext_text_file(text: str, directory: str | None = None) -> str:
    """Write *text* to a UTF-8 file for ``drawtext=textfile=`` and return its
    path.  Using a file sidesteps every text-escaping pitfall (quotes, colons,
    percent signs, backslashes, emoji, newlines)."""
    fd, path = tempfile.mkstemp(prefix="drawtext_", suffix=".txt", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def drawtext_filter(*, text: str | None = None, textfile: str | None = None,
                    fontfile: str | None = None, font: str | None = None,
                    fontsize: str | int = 48, fontcolor: str = "white",
                    x: str = "(w-text_w)/2", y: str = "(h-text_h)/2",
                    box: bool = False, boxcolor: str = "black@0.5", boxborderw: int = 10,
                    borderw: int = 0, bordercolor: str = "black",
                    shadowx: int = 0, shadowy: int = 0, shadowcolor: str = "black",
                    alpha: str | None = None, enable: str | None = None,
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


def write_concat_list(paths: Iterable[str], list_path: str | None = None, *,
                      durations: Sequence[float | None] | None = None,
                      inpoints: Sequence[float | None] | None = None,
                      outpoints: Sequence[float | None] | None = None) -> str:
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
                     tags: dict | None = None) -> str:
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

# Cross-platform stand-ins for commonly requested fonts.  The first entries
# are metric-compatible system fonts (Liberation/DejaVu/Nimbus on Linux),
# then bundled look-alikes.
_ALIASES = {
    "arial": ("Arial", "Liberation Sans", "Arimo", "Nimbus Sans", "DejaVu Sans", "Inter"),
    "arial bold": ("Arial Bold", "Liberation Sans:bold", "Arimo:bold", "DejaVu Sans:bold", "Inter-Bold"),
    "helvetica": ("Helvetica", "Liberation Sans", "Nimbus Sans", "Arimo", "Inter"),
    "verdana": ("Verdana", "DejaVu Sans", "Inter"),
    "times new roman": ("Times New Roman", "Liberation Serif", "Tinos", "Nimbus Roman", "DejaVu Serif"),
    "georgia": ("Georgia", "Gelasio", "Liberation Serif", "DejaVu Serif"),
    "courier new": ("Courier New", "Liberation Mono", "Cousine", "Nimbus Mono PS", "DejaVu Sans Mono"),
    "comic sans ms": ("Comic Sans MS", "Comic Neue", "Bangers"),
    "impact": ("Impact", "Anton", "BebasNeue"),
    # Only scalable outline fonts: bitmap colour-emoji fonts (Noto Color Emoji,
    # Apple Color Emoji) can't be sized by drawtext.
    "emoji": ("Noto Emoji", "Segoe UI Emoji", "Symbola", "DejaVu Sans"),
    "sans": ("Inter", "OpenSans", "DejaVu Sans", "Liberation Sans", "Arial"),
    "sans bold": ("Inter-Bold", "OpenSans-Bold", "DejaVu Sans:bold", "Arial Bold"),
    "mono": ("DejaVu Sans Mono", "Liberation Mono", "Consolas", "Menlo"),
}


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _fc_match(pattern: str) -> str | None:
    """fontconfig lookup that only accepts a REAL match for *pattern*
    (fc-match otherwise returns a generic fallback for anything)."""
    if _IS_WIN or not shutil.which("fc-match"):
        return None
    try:
        r = _proc.run(["fc-match", "-f", "%{file}\t%{family}\t%{fullname}", pattern], timeout=5)
    except _proc.ProcessError:
        return None
    parts = r.stdout.split("\t")
    if len(parts) < 2 or not os.path.isfile(parts[0]):
        return None
    wanted = _norm(pattern.split(":")[0])
    names = [_norm(x) for x in ",".join(parts[1:]).split(",")]
    return parts[0] if any(wanted and (wanted in n or n in wanted) for n in names if n) else None


def _system_file(name: str) -> str | None:
    wanted = _norm(name.split(":")[0]) + ("bold" if ":bold" in name else "")
    for d in _system_font_dirs():
        if not d or not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                stem, ext = os.path.splitext(f)
                if ext.lower() in (".ttf", ".otf") and _norm(stem) in (wanted, wanted + "regular"):
                    return os.path.join(root, f)
    return None


def find_font(name: str = "sans") -> str | None:
    """Return a font file for *name*: a bundled font (``"Montserrat-Bold"``),
    a family (``"Arial"``), an alias (``"sans bold"``) or a path.  Tries the
    exact font first (bundled, then system), then metric-compatible and
    look-alike substitutes, then a bundled sans.  Never returns a path that
    does not exist (None only if no font is available at all)."""
    if not name:
        name = "sans"
    if os.path.isfile(name):
        return name
    key = name.lower().strip()
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    bundled = {_norm(k): v for k, v in bundled_fonts().items()}
    # Alias keys (sans, mono, emoji, arial…) use only their curated list, so a
    # generic family is never resolved by fontconfig's own (possibly bitmap) pick.
    candidates = list(_ALIASES[key]) if key in _ALIASES else [name]
    result = None
    for cand in candidates:
        base = cand.split(":")[0]
        if _norm(base) in bundled and ":" not in cand:
            result = bundled[_norm(base)]
        else:
            result = _fc_match(cand) or _system_file(cand)
        if result:
            break
    if result is None:
        result = bundled.get("inter") or next(iter(bundled.values()), None)
    _FONT_CACHE[key] = result
    return result


#: HDR descriptions that ride along with the frames.  An SDR result must not
#: carry them: encoders write them into the new file (x265/NVENC as SEI, MP4 as
#: mdcv/clli boxes) and TVs then treat the SDR picture as HDR.
STRIP_HDR = ",".join(f"sidedata=mode=delete:type={kind}" for kind in (
    "MASTERING_DISPLAY_METADATA", "CONTENT_LIGHT_LEVEL", "DYNAMIC_HDR_PLUS", "DYNAMIC_HDR_VIVID",
    "DOVI_METADATA", "DOVI_RPU_BUFFER"))

#: re-tag frames whose HDR tags were lost (zscale's own *in options can't do it: "no path between colorspaces")
HDR_INPUTS = {"pq": "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc,",
              "hlg": "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc,"}


def hdr_to_sdr_chain(*, method: str = "hable", peak_nits: float = 100.0, out_fmt: str = "yuv420p",
                     assume: str = "") -> str:
    """CPU tone-mapping of HDR10 / HLG video to SDR BT.709 (zscale + tonemap).

    The input transfer/primaries are taken from the frames, so PQ and HLG both
    work; *assume* ("pq" / "hlg") overrides them for files whose HDR tags were
    lost.  *method*: hable (filmic, default), mobius (keeps saturation),
    reinhard, clip."""
    return (f"{HDR_INPUTS.get(assume, '')}zscale=t=linear:npl={peak_nits:g},format=gbrpf32le,zscale=p=bt709,"
            f"tonemap=tonemap={method}:desat=0,zscale=t=bt709:m=bt709:r=tv,format={out_fmt},{STRIP_HDR}")


def vulkan_ok() -> bool:
    """True when FFmpeg's libplacebo filter runs on a Vulkan GPU here (see core.capabilities)."""
    from core import capabilities
    return bool(capabilities.get("vulkan"))
