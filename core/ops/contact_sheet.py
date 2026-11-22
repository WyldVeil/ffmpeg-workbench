"""
core/ops/contact_sheet.py - contact sheets (tabs/social/contact_sheet.py and
the CLI): a grid of frames from a video with its details on top.

Pure functions: values in, times, FFmpeg commands and a libass script out -
no widgets, no Tk.  How a sheet is made:

1. ``sample_times`` picks one moment per tile (evenly spread, or at shot
   changes found by ``scene_cmd``).  *variant* moves every pick by a
   golden-ratio step of its slot, so "next set" and "previous set" give new
   frames that never repeat a set already seen.
2. ``grab_cmd`` takes each frame with a fast seek (no full decode, so a
   two-hour film takes seconds), fitted into the tile.
3. ``compose_cmd`` joins the tiles (FFmpeg's ``tile``), adds the margins and
   the header, and draws the text from ``labels_script`` with the bundled fonts.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from core import ass
from core import ffmpeg as ff
from core.errors import CommandError
from core.ops.frame_extractor import quality_args

#: each "next set" moves the picks by this fraction of a slot (1 - 1/golden ratio):
#: the sets interleave and no two are the same.
_STEP = 0.381966


class Sampling(StrEnum):
    EVEN = "even"  # evenly spread through the video
    SCENES = "scenes"  # at shot changes near those points


class Corner(StrEnum):
    BOTTOM_RIGHT = "br"
    BOTTOM_LEFT = "bl"
    TOP_RIGHT = "tr"
    TOP_LEFT = "tl"
    OFF = "off"


class Detail(StrEnum):
    """What the header can show."""

    NAME = "name"
    SIZE = "size"
    DURATION = "duration"
    RESOLUTION = "resolution"
    FPS = "fps"
    BITRATE = "bitrate"
    VIDEO = "video"
    AUDIO = "audio"
    CONTAINER = "container"


DEFAULT_DETAILS = frozenset(
    {
        Detail.NAME,
        Detail.SIZE,
        Detail.DURATION,
        Detail.RESOLUTION,
        Detail.FPS,
        Detail.BITRATE,
        Detail.VIDEO,
        Detail.AUDIO,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Layout:
    """The grid: *cols* x *rows* tiles *tile_width* px wide (height from the
    video's shape), *gap* px apart, *margin* px from the edge, each with an
    optional *border*."""

    cols: int = 4
    rows: int = 5
    tile_width: int = 384
    gap: int = 8
    margin: int = 20
    background: str = "#141414"
    border: int = 0
    border_colour: str = "#FFFFFF"

    @property
    def count(self) -> int:
        return self.cols * self.rows


@dataclass(frozen=True, slots=True, kw_only=True)
class Labels:
    """The text: header *details* (and a *title* in place of the file name),
    each tile's time in a *timestamps* corner, optional tile *numbers*."""

    details: frozenset[Detail] = DEFAULT_DETAILS
    title: str = ""
    timestamps: Corner = Corner.BOTTOM_RIGHT
    numbers: bool = False
    font: str = "Montserrat"
    text_size: int = 0  # 0: from the tile width
    text_colour: str = "#FFFFFF"


@dataclass(frozen=True, slots=True, kw_only=True)
class Picking:
    """Which moments: *skip_start* / *skip_end* per cent of the video left out
    (titles, credits); *variant* 0 is the middle of each slot, +-1 the next or
    previous set."""

    sampling: Sampling = Sampling.EVEN
    skip_start: float = 0.0
    skip_end: float = 0.0
    variant: int = 0


# ── Picking the moments ──────────────────────────────────────────────────────


def _window(duration: float, picking: Picking) -> tuple[float, float]:
    a = duration * min(max(picking.skip_start, 0.0), 45.0) / 100
    b = duration * (1 - min(max(picking.skip_end, 0.0), 45.0) / 100)
    return (a, b) if b - a > 0.1 else (0.0, duration)


def sample_times(
    duration: float, count: int, picking: Picking, cuts: Sequence[float] = ()
) -> list[float]:
    """One time per tile, in order.  With scene sampling and *cuts* (shot
    changes), a tile shows a moment just after a cut inside its slot when
    there is one (the variant picks which); otherwise the even time."""
    a, b = _window(duration, picking)
    step = (b - a) / max(count, 1)
    offset = (0.5 + picking.variant * _STEP) % 1.0
    last = max(0.0, duration - 0.05)
    times: list[float] = []
    for i in range(count):
        start = a + step * i
        t = start + step * offset
        if picking.sampling is Sampling.SCENES:
            # just after each cut in the slot, then the even time: every set differs
            shots = [
                min(c + min(0.5, step / 4), start + step) for c in cuts if start <= c < start + step
            ]
            if shots:
                options = [*shots, t]
                t = options[picking.variant % len(options)]
        times.append(round(min(max(t, 0.0), last), 3))
    return times


def scene_cmd(src: str, threshold: float = 0.3) -> list[str]:
    """Find shot changes (a small, fast decode); ``parse_cuts`` reads the log."""
    vf = f"scale=160:-2,select='gt(scene,{threshold:.3f})',metadata=print"
    head = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-i", src, "-map", "0:v:0"]
    return [*head, "-vf", vf, "-an", "-sn", "-f", "null", "-"]


_PTS_TIME = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def parse_cuts(log: Sequence[str]) -> list[float]:
    """Shot-change times from ``scene_cmd``'s log, sorted, without repeats."""
    return sorted({float(m.group(1)) for line in log if (m := _PTS_TIME.search(line))})


# ── Sizes ────────────────────────────────────────────────────────────────────


def tile_size(info: ff.MediaInfo, layout: Layout) -> tuple[int, int]:
    """(width, height) of one tile: the video's displayed shape, even numbers."""
    dw, dh = info.require_video().display_size
    w = max(32, layout.tile_width // 2 * 2)
    h = max(18, round(w * dh / max(dw, 1) / 2) * 2) if dw and dh else w * 9 // 16 // 2 * 2
    return w, h


def text_size(labels: Labels, layout: Layout) -> int:
    """The tile-label size; the header uses multiples of it."""
    return labels.text_size or max(11, min(40, layout.tile_width // 16))


def header_height(lines: Sequence[str], size: int) -> int:
    if not lines:
        return 0
    return round(size * 1.7 + (len(lines) - 1) * size * 1.35 + size * 0.8)


@dataclass(frozen=True, slots=True)
class Geometry:
    """Where everything goes on the sheet."""

    tile_w: int
    tile_h: int
    top: int  # y of the first row
    width: int
    height: int

    def cell(self, layout: Layout, i: int) -> tuple[int, int]:
        """(x, y) of tile *i*'s picture (inside its border)."""
        b = layout.border
        col, row = i % layout.cols, i // layout.cols
        x = layout.margin + col * (self.tile_w + 2 * b + layout.gap) + b
        y = self.top + row * (self.tile_h + 2 * b + layout.gap) + b
        return x, y


def geometry(info: ff.MediaInfo, layout: Layout, header: int) -> Geometry:
    tw, th = tile_size(info, layout)
    b, gap = layout.border, layout.gap
    top = layout.margin + header
    width = 2 * layout.margin + layout.cols * (tw + 2 * b) + (layout.cols - 1) * gap
    height = top + layout.rows * (th + 2 * b) + (layout.rows - 1) * gap + layout.margin
    return Geometry(tw, th, top, (width + 1) // 2 * 2, (height + 1) // 2 * 2)


# ── The header text ──────────────────────────────────────────────────────────

_CODECS = {
    "h264": "H.264",
    "hevc": "HEVC",
    "av1": "AV1",
    "vp9": "VP9",
    "vp8": "VP8",
    "prores": "ProRes",
    "mpeg4": "MPEG-4",
    "mpeg2video": "MPEG-2",
    "aac": "AAC",
    "mp3": "MP3",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "flac": "FLAC",
    "ac3": "AC-3",
    "eac3": "E-AC-3",
    "dts": "DTS",
    "truehd": "TrueHD",
    "alac": "ALAC",
}


def size_text(n: int) -> str:
    """1234567 -> "1.18 MB"."""
    if n < 1024:
        return f"{n} bytes"
    value, unit = n / 1024, "KB"
    for bigger in ("MB", "GB", "TB"):
        if value < 1024:
            break
        value, unit = value / 1024, bigger
    return f"{value:.2f} {unit}"


def _rate(bps: int) -> str:
    return f"{bps / 1_000_000:.1f} Mb/s" if bps >= 1_000_000 else f"{bps // 1000} kb/s"


def _video_text(info: ff.MediaInfo) -> str:
    v = info.require_video()
    name = _CODECS.get(v.codec, v.codec.upper())
    bits = [name + (f" ({v.profile})" if v.profile else ""), v.pix_fmt]
    if v.is_hdr:
        bits.append("HDR")
    if v.bit_rate:
        bits.append(_rate(v.bit_rate))
    return "Video: " + ", ".join(b for b in bits if b)


def _audio_text(info: ff.MediaInfo) -> str:
    a = info.audio
    if a is None:
        return "Audio: none"
    bits = [_CODECS.get(a.codec, a.codec.upper())]
    if a.sample_rate:
        bits.append(f"{a.sample_rate / 1000:g} kHz")
    bits.append(a.channel_layout or (f"{a.channels} ch" if a.channels else ""))
    if a.bit_rate:
        bits.append(_rate(a.bit_rate))
    more = len(info.audio_streams) - 1
    return "Audio: " + ", ".join(b for b in bits if b) + (f"  (+{more} more)" if more > 0 else "")


def _fps_text(fps: float) -> str:
    """23.976 -> "23.976", 25.0 -> "25", 29.97003 -> "29.97"."""
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def _facts(info: ff.MediaInfo, show: frozenset[Detail]) -> str:
    """The one-line summary: size, length, picture size, frame rate, bit rate."""
    w, h = info.require_video().display_size
    rate = info.bit_rate or (int(info.size * 8 / info.duration) if info.duration else 0)
    facts = {
        Detail.SIZE: size_text(info.size) if info.size else "",
        Detail.DURATION: ff.format_time(info.duration, ms=False, always_hours=True),
        Detail.RESOLUTION: f"{w}×{h}",
        Detail.FPS: f"{_fps_text(info.require_video().fps)} fps"
        if info.require_video().fps
        else "",
        Detail.BITRATE: _rate(rate) if rate else "",
        Detail.CONTAINER: info.format_name.split(",")[0].upper(),
    }
    return "   ·   ".join(text for key, text in facts.items() if key in show and text)


def header_lines(info: ff.MediaInfo, labels: Labels) -> list[str]:
    """The header, line by line (empty when nothing is to be shown)."""
    show = labels.details
    lines: list[str] = []
    if labels.title.strip() or Detail.NAME in show:
        lines.append(labels.title.strip() or os.path.basename(info.path))
    if facts := _facts(info, show):
        lines.append(facts)
    codecs = [_video_text(info)] if Detail.VIDEO in show else []
    codecs += [_audio_text(info)] if Detail.AUDIO in show else []
    if codecs:
        lines.append("   ·   ".join(codecs))
    return lines


# ── The text layer ───────────────────────────────────────────────────────────

_CORNER_TAGS = {
    Corner.BOTTOM_RIGHT: (3, 1, 1),  # (\an, x side: 1 = right edge, y side: 1 = bottom)
    Corner.BOTTOM_LEFT: (1, 0, 1),
    Corner.TOP_RIGHT: (9, 1, 0),
    Corner.TOP_LEFT: (7, 0, 0),
}


def _stamp_time(t: float, duration: float) -> str:
    return ff.format_time(t, ms=False, always_hours=duration >= 3600)


def labels_script(
    geo: Geometry, layout: Layout, labels: Labels, lines: Sequence[str], times: Sequence[float]
) -> ass.Script:
    """Header lines, each tile's time and number, as one libass script."""
    sc = ass.Script(geo.width, geo.height)
    size = text_size(labels, layout)
    font, colour = labels.font, labels.text_colour
    sc.style(ass.Style("Name", font=font, size=round(size * 1.5), colour=colour, bold=True))
    sc.style(ass.Style("Facts", font=font, size=size, colour=colour))
    sc.style(
        ass.Style(
            "Tile",
            font=font,
            size=round(size * 0.85),
            colour=colour,
            bold=True,
            border_style=3,
            back_alpha=0.45,
        )
    )
    y = layout.margin + size * 0.2
    for k, line in enumerate(lines):
        style = "Name" if k == 0 else "Facts"
        sc.add(0, 3600, f"{{\\an7\\pos({layout.margin},{y:.0f})}}{ass.escape(line)}", style)
        y += size * (1.7 if k == 0 else 1.35)
    duration = max(times, default=0.0)
    pad = max(3, size // 3)
    for i, t in enumerate(times):
        x, y0 = geo.cell(layout, i)
        if labels.timestamps is not Corner.OFF:
            an, right, bottom = _CORNER_TAGS[labels.timestamps]
            px_ = x + (geo.tile_w - pad if right else pad)
            py = y0 + (geo.tile_h - pad if bottom else pad)
            stamp = ass.escape(_stamp_time(t, duration))
            sc.add(0, 3600, f"{{\\an{an}\\pos({px_},{py})\\bord{pad // 2}}}{stamp}", "Tile", 1)
        if labels.numbers:
            an = 9 if labels.timestamps is Corner.TOP_LEFT else 7
            nx = x + (geo.tile_w - pad if an == 9 else pad)
            sc.add(0, 3600, f"{{\\an{an}\\pos({nx},{y0 + pad})\\bord{pad // 2}}}{i + 1}", "Tile", 1)
    return sc


# ── The commands ─────────────────────────────────────────────────────────────


def _hex(colour: str) -> str:
    return "0x" + colour.lstrip("#")


def grab_cmd(src: str, t: float, geo: Geometry, layout: Layout, out: str) -> list[str]:
    """One frame at *t* (fast seek), fitted into a tile (+ its border), as PNG."""
    w, h, b = geo.tile_w, geo.tile_h, layout.border
    fit = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={_hex(layout.background)},setsar=1"
    )
    if b:
        fit += f",pad={w + 2 * b}:{h + 2 * b}:{b}:{b}:color={_hex(layout.border_colour)}"
    head = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", src]
    return [*head, "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", fit, "-frames:v", "1", out]


def blank_cmd(geo: Geometry, layout: Layout, out: str) -> list[str]:
    """A plain tile, for a moment no frame could be read at."""
    b = layout.border
    size = f"{geo.tile_w + 2 * b}x{geo.tile_h + 2 * b}"
    src = f"color=c={_hex(layout.background)}:s={size}:d=0.04"
    head = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", src]
    return [*head, "-frames:v", "1", out]


def tile_name(folder: str, i: int) -> str:
    return os.path.join(folder, f"tile{i:04d}.png")


def compose_cmd(
    folder: str, geo: Geometry, layout: Layout, ass_path: str, out: str, *, quality: int = 92
) -> list[str]:
    """The tiles in *folder* (``tile_name``) joined into the sheet, text drawn
    on top.  The picture type follows *out*'s extension (png, jpg, webp);
    *quality* 1..100 for JPEG and WebP."""
    bg = _hex(layout.background)
    tiles = f"tile={layout.cols}x{layout.rows}:padding={layout.gap}:margin=0:color={bg}"
    pad = f"pad={geo.width}:{geo.height}:{layout.margin}:{geo.top}:color={bg}"
    vf = f"{tiles},{pad},{ass.subtitles_filter(ass_path)}"
    ext = os.path.splitext(out)[1].lower().lstrip(".").replace("jpeg", "jpg")
    if ext == "jpg":
        vf += ",format=yuvj444p"  # full-range, full-colour JPEG: crisp text
    pattern = os.path.join(folder.replace("%", "%%"), "tile%04d.png")
    head = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-framerate", "1"]
    cmd = [*head, "-start_number", "0", "-i", pattern, "-vf", vf, "-frames:v", "1", "-update", "1"]
    return [*cmd, *quality_args(ext, quality), out]


# ── Making a sheet ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Plan:
    """Everything one sheet needs, worked out from the video and the settings."""

    src: str
    times: tuple[float, ...]
    geo: Geometry
    layout: Layout
    labels: Labels
    lines: tuple[str, ...]


def plan(
    info: ff.MediaInfo,
    layout: Layout,
    labels: Labels,
    picking: Picking,
    cuts: Sequence[float] = (),
) -> Plan:
    times = sample_times(info.duration, layout.count, picking, cuts)
    lines = header_lines(info, labels)
    geo = geometry(info, layout, header_height(lines, text_size(labels, layout)))
    return Plan(info.path, tuple(times), geo, layout, labels, tuple(lines))


#: runs one command and says whether it worked; the float is how much of the
#: sheet is done once it has (0..1), for progress
Run = Callable[[list[str], float], bool]


def make(
    p: Plan,
    folder: str,
    out: str,
    run: Run,
    *,
    quality: int = 92,
    cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """Grab every tile into *folder*, then join them into *out*.  A frame that
    can't be read (a damaged spot, the very end) is taken a second earlier,
    else left plain; only a failed join is an error."""
    steps = len(p.times) + 1
    for i, t in enumerate(p.times):
        if cancelled():
            raise ff.CancelledError("contact sheet cancelled")
        tile, done = tile_name(folder, i), (i + 1) / steps
        if not (run(grab_cmd(p.src, t, p.geo, p.layout, tile), done) and os.path.isfile(tile)):
            earlier = grab_cmd(p.src, max(0.0, t - 1.0), p.geo, p.layout, tile)
            if not (run(earlier, done) and os.path.isfile(tile)):
                run(blank_cmd(p.geo, p.layout, tile), done)
    script = labels_script(p.geo, p.layout, p.labels, p.lines, p.times)
    ass_path = script.write(os.path.join(folder, "sheet.ass"))
    if not run(compose_cmd(folder, p.geo, p.layout, ass_path, out, quality=quality), 1.0):
        raise CommandError("the tiles couldn't be joined into a sheet")
