"""
core/media_notes.py  -  Things worth knowing about a media file

What the Media Inspector shows under a file's summary: variable frame rate,
interlacing, HDR, rotation, odd sizes, unusual pixel formats, non-square
pixels, missing or extra audio tracks.  Each note is ``(level, message)``
with level ``"warn"``, ``"info"`` or ``"ok"``.
"""

from __future__ import annotations

from core import ffmpeg as ff
from core.i18n import t_or

Note = tuple[str, str]


def fps_text(fps: float) -> str:
    """A frame rate without trailing zeros (29.97, 25), or a dash."""
    if not fps:
        return "—"
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def diagnose(info: ff.MediaInfo) -> list[Note]:
    """Notes about *info*, or one "nothing unusual" note."""
    notes = _video_notes(info) + _track_notes(info)
    return notes or [("ok", t_or("inspector.all_good", "Nothing unusual detected."))]


def _video_notes(info: ff.MediaInfo) -> list[Note]:
    """Timing, scanning and colour."""
    v = info.video
    if not v:
        return []
    notes: list[Note] = []
    if v.r_fps and v.fps and abs(v.r_fps - v.fps) / max(v.fps, 1e-6) > 0.01 and not info.is_image:
        notes.append(
            (
                "warn",
                t_or(
                    "inspector.vfr",
                    "Variable frame rate likely (average {avg} fps vs nominal {nom} fps). "
                    "Editing tools may drift; convert to constant frame rate first.",
                ).format(avg=fps_text(v.fps), nom=fps_text(v.r_fps)),
            )
        )
    if v.field_order and v.field_order not in ("progressive", "unknown"):
        notes.append(
            (
                "warn",
                t_or(
                    "inspector.interlaced",
                    "Interlaced video ({order}). Use the Deinterlace tool before scaling or "
                    "slow motion.",
                ).format(order=v.field_order),
            )
        )
    if v.is_hdr:
        notes.append(
            (
                "info",
                t_or(
                    "inspector.hdr",
                    "HDR video ({tr}). Converting to SDR needs tone-mapping or colours "
                    "will look washed out.",
                ).format(tr=v.color_transfer),
            )
        )
    return notes + _shape_notes(info)


def _shape_notes(info: ff.MediaInfo) -> list[Note]:
    """Rotation, frame size, pixel format and pixel shape."""
    v = info.video
    assert v is not None
    notes: list[Note] = []
    if v.rotation:
        notes.append(
            (
                "info",
                t_or(
                    "inspector.rotation",
                    "Rotation metadata: {deg}°. Players rotate it on display; FFmpeg "
                    "applies it automatically when re-encoding.",
                ).format(deg=v.rotation),
            )
        )
    if v.width % 2 or v.height % 2:
        notes.append(
            (
                "warn",
                t_or(
                    "inspector.odd_size",
                    "Odd frame size ({w}×{h}): H.264/H.265 in 4:2:0 need even "
                    "dimensions, so tools will round them.",
                ).format(w=v.width, h=v.height),
            )
        )
    if (
        v.pix_fmt
        and not v.pix_fmt.startswith(("yuv420p", "yuvj420p", "nv12", "p010"))
        and not info.is_image
    ):
        notes.append(
            (
                "info",
                t_or(
                    "inspector.pixfmt",
                    "Pixel format {pf} is not 4:2:0; some players and web sites will "
                    "not play it until converted.",
                ).format(pf=v.pix_fmt),
            )
        )
    if v.sar and v.sar not in ("1:1", "0:1", ""):
        notes.append(
            ("info", t_or("inspector.sar", "Non-square pixels (SAR {sar}).").format(sar=v.sar))
        )
    return notes


def _track_notes(info: ff.MediaInfo) -> list[Note]:
    """No sound, or more than one sound track."""
    notes: list[Note] = []
    if not info.audio and info.video and not info.is_image:
        notes.append(("info", t_or("inspector.no_audio", "No audio stream.")))
    if len(info.audio_streams) > 1:
        notes.append(
            (
                "info",
                t_or(
                    "inspector.multi_audio", "{n} audio tracks - most tools use the first one."
                ).format(n=len(info.audio_streams)),
            )
        )
    return notes


# ── The inspector's tables and pages, as text ────────────────────────────────


def human_bytes(n: int) -> str:
    """A size in B / KB / MB / GB / TB, or a dash."""
    if not n:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return str(n)


def human_rate(bps: int) -> str:
    """A bit rate in kb/s or Mb/s, or a dash."""
    if not bps:
        return "—"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mb/s"
    return f"{bps / 1000:.0f} kb/s"


def _seconds(value: object) -> float:
    """A time from ffprobe's JSON (a string or a number), or 0."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _stream_detail(s: ff.Stream) -> str:
    """Size, rate and format for video; rate, channels and format for sound; else the codec."""
    if s.type == "video":
        w, h = s.display_size
        if s.attached_pic:
            return f"cover art {w}×{h}"
        detail = f"{w}×{h}  {fps_text(s.fps)} fps  {s.pix_fmt or ''}  {s.bit_depth}-bit"
        if s.color_transfer or s.color_primaries:
            detail += f"  {s.color_primaries or '?'}/{s.color_transfer or '?'}"
        if s.rotation:
            detail += f"  rot {s.rotation}°"
        return detail
    if s.type == "audio":
        return f"{s.sample_rate} Hz  {s.channels} ch ({s.channel_layout or '?'})  {s.sample_fmt}"
    return s.codec_long


def stream_row(s: ff.Stream) -> tuple[object, ...]:
    """One row of the Streams table: index, type, codec, detail, bit rate, language, flags."""
    marks = (("default", s.default), ("forced", s.forced), ("cover", s.attached_pic))
    flags = ", ".join(f for f, on in marks if on)
    lang = " / ".join(x for x in (s.language if s.language != "und" else "", s.title) if x)
    codec = s.codec + (f" ({s.profile})" if s.profile else "")
    return (s.index, s.type, codec, _stream_detail(s), human_rate(s.bit_rate), lang, flags)


def container_text(info: ff.MediaInfo) -> str:
    """The Container page: file, format, size, length, bit rate, start, stream counts."""
    return "\n".join(
        [
            f"File:        {info.path}",
            f"Container:   {info.format_long} ({info.format_name})",
            f"Size:        {human_bytes(info.size)}",
            f"Duration:    {ff.format_time(info.duration)}  ({info.duration:.3f} s)",
            f"Bitrate:     {human_rate(info.bit_rate)}",
            f"Start time:  {info.start_time:.3f} s",
            f"Streams:     {len(info.video_streams)} video, {len(info.audio_streams)} audio, "
            f"{len(info.subtitle_streams)} subtitle, {len(info.streams)} total",
        ]
    )


def chapters_text(info: ff.MediaInfo) -> str:
    """The Chapters page: number, start → end, title."""
    if not info.chapters:
        return t_or("inspector.no_chapters", "No chapters.")
    lines: list[str] = []
    for i, c in enumerate(info.chapters, 1):
        start, end = _seconds(c.get("start_time")), _seconds(c.get("end_time"))
        tags: dict[str, object] = c.get("tags") or {}
        title = tags.get("title", "")
        lines.append(f"{i:>3}. {ff.format_time(start)} → {ff.format_time(end)}  {title}")
    return "\n".join(lines)


def tags_text(info: ff.MediaInfo) -> str:
    """The Tags page: the file's tags, then each stream's."""
    lines = [f"{k}: {v}" for k, v in sorted(info.tags.items())]
    for s in info.streams:
        lines += [f"[stream {s.index}] {k}: {v}" for k, v in sorted(s.tags.items())]
    return "\n".join(lines) or t_or("inspector.no_tags", "No tags.")
