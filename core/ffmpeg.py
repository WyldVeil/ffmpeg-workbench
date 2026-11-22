"""
core/ffmpeg.py - Probe media and run FFmpeg-family tools, in one place.

Highlights
----------
* :func:`probe` returns a typed, cached :class:`MediaInfo` (one ffprobe call
  with JSON output) instead of ad-hoc ``-show_entries`` parsing in every tab.
* :func:`run` executes a command with real progress: it adds
  ``-progress pipe:1 -nostats`` to FFmpeg so percentage, speed and ETA come
  from machine-readable output rather than regex-scraping ``time=`` lines.
* Cancellation kills the whole process tree (FFmpeg, or yt-dlp and the FFmpeg
  it spawned) - never every ffmpeg.exe on the machine.
* Every process is started without a console window on Windows and in its own
  process group, and is tracked so the app can stop everything on exit.
* :func:`summarize_error` turns FFmpeg's log into a one-paragraph message
  suitable for an error dialog.

The module has no Tk dependency and is safe to use from worker threads.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import IO, Any, Literal, cast, overload

from core import errors, proc, tasks
from core.hardware import get_binary_path
from core.timecode import Timecode

_IS_WIN = sys.platform.startswith("win")
logger = logging.getLogger("crossfader.ffmpeg")


# ─────────────────────────────────────────────────────────────────────────────
#  Binaries
# ─────────────────────────────────────────────────────────────────────────────


def ffmpeg_path() -> str:
    return get_binary_path("ffmpeg")


def ffprobe_path() -> str:
    return get_binary_path("ffprobe")


def ffplay_path() -> str:
    return get_binary_path("ffplay")


def is_ffmpeg(argv0: str) -> bool:
    base = os.path.basename(str(argv0)).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base == "ffmpeg"


# ─────────────────────────────────────────────────────────────────────────────
#  Time helpers
# ─────────────────────────────────────────────────────────────────────────────


def parse_time(value: Any) -> float:
    """Parse ``"1:02:03.5"``, ``"02:03"``, ``"75.5"``, ``"75,5"`` or a number
    into seconds.  Raises ``ValueError`` on anything else (including negative
    values).  See :class:`core.timecode.Timecode`."""
    return Timecode.parse(value).seconds


def format_time(
    seconds: float, *, ms: bool = True, always_hours: bool = False, ms_digits: int = 3
) -> str:
    """``83.25 -> "01:23.250"`` (or ``"00:01:23.250"`` with *always_hours*)."""
    spec = ("H" if always_hours else "") + (f".{max(1, min(3, ms_digits))}" if ms else ".0")
    return format(Timecode.of(seconds), spec)


def ts(seconds: float) -> str:
    """Seconds formatted for an FFmpeg time argument (``-ss``/``-t``/``-to``)."""
    return f"{max(0.0, float(seconds)):.3f}"


# ─────────────────────────────────────────────────────────────────────────────
#  Probing
# ─────────────────────────────────────────────────────────────────────────────

ProbeError = errors.ProbeError  # raised by probe(); part of core.errors


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "N/A"):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def json_object(value: object) -> dict[str, Any]:
    """A JSON object from ffprobe, or {} when the field is missing or the wrong shape."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def json_objects(value: object) -> list[dict[str, Any]]:
    """A JSON list of objects from ffprobe (anything else is skipped)."""
    items = cast("list[object]", value) if isinstance(value, list) else []
    return [cast("dict[str, Any]", x) for x in items if isinstance(x, dict)]


def _tags(value: object) -> dict[str, Any]:
    return {k.lower(): v for k, v in json_object(value).items()}


def _rate(value: Any) -> float:
    """``"30000/1001" -> 29.97`` ; ``"0/0" -> 0.0``."""
    if not value or value in ("0/0", "N/A"):
        return 0.0
    text = str(value)
    if "/" in text:
        num, _, den = text.partition("/")
        d = _f(den)
        return _f(num) / d if d else 0.0
    return _f(text)


@dataclass(frozen=True, slots=True)
class Stream:
    """One stream of a probed file.  Frozen: probe() results are cached and shared."""

    index: int
    type: str  # video | audio | subtitle | data | attachment
    codec: str = ""
    codec_long: str = ""
    profile: str = ""
    width: int = 0
    height: int = 0
    pix_fmt: str = ""
    fps: float = 0.0  # average frame rate (falls back to r_frame_rate)
    r_fps: float = 0.0
    sar: str = ""
    field_order: str = ""
    color_space: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    color_range: str = ""
    bits_per_raw_sample: int = 0
    sample_rate: int = 0
    channels: int = 0
    channel_layout: str = ""
    sample_fmt: str = ""
    bit_rate: int = 0
    duration: float = 0.0
    start_time: float = 0.0
    nb_frames: int = 0
    language: str = ""
    title: str = ""
    default: bool = False
    forced: bool = False
    attached_pic: bool = False
    rotation: int = 0  # clockwise display rotation: 0/90/180/270
    tags: Mapping[str, Any] = field(default_factory=dict[str, Any])
    raw: Mapping[str, Any] = field(default_factory=dict[str, Any])

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer in ("smpte2084", "arib-std-b67")

    @property
    def bit_depth(self) -> int:
        if self.bits_per_raw_sample:
            return self.bits_per_raw_sample
        m = re.search(r"(\d+)(?:le|be)$", self.pix_fmt or "")
        return int(m.group(1)) if m else 8

    @property
    def display_size(self) -> tuple[int, int]:
        """Width/height as shown to the viewer (rotation applied)."""
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """What ffprobe says about a file.  Frozen: probe() caches and shares it,
    so nobody may change it (Robust Python ch. 10; Fluent Python ch. 5)."""

    path: str
    format_name: str = ""
    format_long: str = ""
    duration: float = 0.0
    size: int = 0
    bit_rate: int = 0
    start_time: float = 0.0
    streams: tuple[Stream, ...] = ()
    chapters: tuple[Mapping[str, Any], ...] = ()
    tags: Mapping[str, Any] = field(default_factory=dict[str, Any])
    raw: Mapping[str, Any] = field(default_factory=dict[str, Any])

    # ── stream shortcuts ────────────────────────────────────────────────────
    @property
    def video_streams(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.type == "video" and not s.attached_pic)

    @property
    def audio_streams(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.type == "audio")

    @property
    def subtitle_streams(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.type == "subtitle")

    @property
    def video(self) -> Stream | None:
        vs = self.video_streams
        return vs[0] if vs else None

    @property
    def audio(self) -> Stream | None:
        a = self.audio_streams
        return a[0] if a else None

    def require_video(self) -> Stream:
        """The first video stream; MediaError when the file has none."""
        v = self.video
        if v is None:
            raise errors.MediaError(f"{os.path.basename(self.path)} has no video")
        return v

    def require_audio(self) -> Stream:
        """The first audio stream; MediaError when the file has none."""
        a = self.audio
        if a is None:
            raise errors.MediaError(f"{os.path.basename(self.path)} has no sound")
        return a

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def width(self) -> int:
        return self.video.display_size[0] if self.video else 0

    @property
    def height(self) -> int:
        return self.video.display_size[1] if self.video else 0

    @property
    def fps(self) -> float:
        return self.video.fps if self.video else 0.0

    @property
    def is_image(self) -> bool:
        if not self.has_video or self.has_audio:
            return False
        fmt = self.format_name
        if fmt.endswith("_pipe") or fmt in ("image2", "png", "webp", "bmp"):
            return True
        return self.duration <= 0.05

    @property
    def is_hdr(self) -> bool:
        return bool(self.video and self.video.is_hdr)

    def summary(self) -> str:
        """One-line human description, e.g. ``1920×1080 · 29.97 fps · H264 · 02:03``."""
        parts: list[str] = []
        v, a = self.video, self.audio
        if v:
            w, h = v.display_size
            parts.append(f"{w}×{h}")
            if v.fps and not self.is_image:
                parts.append(f"{v.fps:.3f}".rstrip("0").rstrip(".") + " fps")
            parts.append(v.codec.upper())
            if v.bit_depth > 8:
                parts.append(f"{v.bit_depth}-bit")
            if v.is_hdr:
                parts.append("HDR")
        if a:
            ch = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(a.channels, f"{a.channels}ch")
            parts.append(f"{a.codec.upper()} {ch}")
        elif v:
            parts.append("no audio")
        if self.duration and not self.is_image:
            parts.append(format_time(self.duration, ms=False))
        return " · ".join(parts)


def _parse_stream(d: dict[str, Any]) -> Stream:
    tags = _tags(d.get("tags"))
    disp = json_object(d.get("disposition"))
    rotation = 0
    for sd in json_objects(d.get("side_data_list")):
        if "rotation" in sd:
            rotation = round(-_f(sd.get("rotation"))) % 360
            break
    else:
        if "rotate" in tags:
            rotation = _i(tags.get("rotate")) % 360
    avg = _rate(d.get("avg_frame_rate"))
    rfr = _rate(d.get("r_frame_rate"))
    return Stream(
        index=_i(d.get("index")),
        type=d.get("codec_type", ""),
        codec=d.get("codec_name", "") or "",
        codec_long=d.get("codec_long_name", "") or "",
        profile=d.get("profile", "") or "",
        width=_i(d.get("width")),
        height=_i(d.get("height")),
        pix_fmt=d.get("pix_fmt", "") or "",
        fps=avg if 0 < avg < 1000 else rfr,
        r_fps=rfr,
        sar=d.get("sample_aspect_ratio", "") or "",
        field_order=d.get("field_order", "") or "",
        color_space=d.get("color_space", "") or "",
        color_transfer=d.get("color_transfer", "") or "",
        color_primaries=d.get("color_primaries", "") or "",
        color_range=d.get("color_range", "") or "",
        bits_per_raw_sample=_i(d.get("bits_per_raw_sample")),
        sample_rate=_i(d.get("sample_rate")),
        channels=_i(d.get("channels")),
        channel_layout=d.get("channel_layout", "") or "",
        sample_fmt=d.get("sample_fmt", "") or "",
        bit_rate=_i(d.get("bit_rate")),
        duration=_f(d.get("duration")),
        start_time=_f(d.get("start_time")),
        nb_frames=_i(d.get("nb_frames")),
        language=str(tags.get("language", "") or ""),
        title=str(tags.get("title", "") or ""),
        default=bool(disp.get("default")),
        forced=bool(disp.get("forced")),
        attached_pic=bool(disp.get("attached_pic")),
        rotation=rotation,
        tags=tags,
        raw=d,
    )


def parse_probe_json(path: str, data: dict[str, Any]) -> MediaInfo:
    fmt = json_object(data.get("format"))
    streams = tuple(_parse_stream(s) for s in json_objects(data.get("streams")))
    duration = _f(fmt.get("duration"))
    if not duration:
        duration = max((s.duration for s in streams), default=0.0)
    return MediaInfo(
        path=path,
        format_name=fmt.get("format_name", "") or "",
        format_long=fmt.get("format_long_name", "") or "",
        duration=duration,
        size=_i(fmt.get("size")),
        bit_rate=_i(fmt.get("bit_rate")),
        start_time=_f(fmt.get("start_time")),
        streams=streams,
        chapters=tuple(json_objects(data.get("chapters"))),
        tags=_tags(fmt.get("tags")),
        raw=data,
    )


_PROBE_CACHE: OrderedDict[tuple[str, int, int], MediaInfo] = OrderedDict()
_PROBE_LOCK = threading.Lock()
_PROBE_CACHE_MAX = 256


def _probe_key(path: str) -> tuple[str, int, int] | None:
    """What identifies this version of the file (None for a URL)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), st.st_mtime_ns, st.st_size)


def _cached(key: tuple[str, int, int]) -> MediaInfo | None:
    with _PROBE_LOCK:
        hit = _PROBE_CACHE.get(key)
        if hit is not None:
            _PROBE_CACHE.move_to_end(key)
        return hit


def _remember(key: tuple[str, int, int], info: MediaInfo) -> None:
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = info
        while len(_PROBE_CACHE) > _PROBE_CACHE_MAX:
            _PROBE_CACHE.popitem(last=False)


def probe(path: str, *, use_cache: bool = True, timeout: float = 30.0) -> MediaInfo:
    """Probe *path* with ffprobe (JSON).  Raises :class:`ProbeError`."""
    if not path:
        raise ProbeError("no file given")
    if not os.path.exists(path) and "://" not in path:
        raise ProbeError(f"file not found: {path}")
    key = _probe_key(path)
    hit = _cached(key) if use_cache and key is not None else None
    if hit is not None:
        return hit
    info = parse_probe_json(path, _run_ffprobe(path, timeout))
    if key is not None:
        _remember(key, info)
    return info


def _run_ffprobe(path: str, timeout: float) -> dict[str, Any]:
    """ffprobe's JSON for *path*; ProbeError with a readable reason when there is none."""
    cmd = [
        ffprobe_path(),
        "-v",
        "error",
        "-hide_banner",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-of",
        "json",
        path,
    ]
    try:
        r = proc.run(cmd, timeout=timeout)
    except errors.ProgramNotFound as exc:
        raise ProbeError(
            "ffprobe was not found - install FFmpeg or set its location in Advanced Settings"
        ) from exc
    except errors.ProcessTimeout as exc:
        raise ProbeError(f"ffprobe timed out on {os.path.basename(path)}") from exc
    except errors.ProcessError as exc:
        raise ProbeError(str(exc)) from exc
    out = str(r.stdout)
    if not r.ok:
        raise ProbeError(r.last_error_line())
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"unreadable ffprobe output: {exc}") from exc
    if not data.get("streams") and not data.get("format"):
        raise ProbeError("no media streams found")
    return data


def try_probe(path: str, *, use_cache: bool = True, timeout: float = 30.0) -> MediaInfo | None:
    """Like :func:`probe` but returns None instead of raising."""
    try:
        return probe(path, use_cache=use_cache, timeout=timeout)
    except ProbeError:
        return None


def duration_of(path: str) -> float:
    info = try_probe(path)
    return info.duration if info else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  Process management
# ─────────────────────────────────────────────────────────────────────────────

# The process registry lives in core.proc (the only place that starts programs).
kill_tree = proc.kill_tree
kill_all = proc.kill_all
active_count = proc.active_count


# ─────────────────────────────────────────────────────────────────────────────
#  Running with progress
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Progress:
    out_time: float = 0.0  # seconds of output written so far
    fraction: float | None = None  # 0..1 when the expected duration is known
    speed: float | None = None  # x realtime
    fps: float | None = None
    frame: int | None = None
    size: int | None = None  # bytes written
    eta: float | None = None  # seconds remaining
    elapsed: float = 0.0
    done: bool = False

    @property
    def percent(self) -> float | None:
        return None if self.fraction is None else self.fraction * 100.0


@dataclass
class RunResult:
    returncode: int
    cancelled: bool = False
    timed_out: bool = False
    log_tail: list[str] = field(default_factory=list[str])
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled

    @property
    def error_summary(self) -> str:
        if self.cancelled:
            return "Cancelled."
        if self.timed_out:
            return "Timed out."
        return summarize_error(self.log_tail, self.returncode)


class CancelledError(errors.Cancelled, RuntimeError):
    """Raised by :func:`check` when a job is cancelled."""


class CommandError(errors.CommandError):
    """Raised by :func:`check` when a command fails."""

    def __init__(self, result: RunResult, cmd: Sequence[str]):
        self.result = result
        self.cmd = list(cmd)
        super().__init__(result.error_summary)


_ERROR_HINTS = re.compile(
    r"(error|invalid|unable|cannot|can't|could not|failed|no such|not found|"
    r"unknown|unrecognized|does not|permission denied|not supported|"
    r"incompatible|too many|out of memory|conversion failed)",
    re.I,
)

_NOISE = re.compile(
    r"^(frame=|size=|video:|audio:|\s*Stream #|\s*Metadata:|"
    r"\s*Duration:|Input #|Output #|\s+[a-z_]+\s+:|Press \[q\])"
)


def summarize_error(lines: Iterable[str], returncode: int | None = None) -> str:
    """Pick the lines from a tool's log that explain why it failed."""
    lines = [ln.strip() for ln in lines if ln and ln.strip()]
    hits = [ln for ln in lines if _ERROR_HINTS.search(ln) and not _NOISE.match(ln)]
    picked: list[str] = []
    for ln in hits[-4:]:
        # strip "[libx264 @ 0x55d…] " prefixes for readability
        ln = re.sub(r"^\[[^\]]+ @ 0x[0-9a-f]+\]\s*", "", ln)
        if ln not in picked:
            picked.append(ln)
    if not picked and lines:
        picked = lines[-3:]
    msg = "\n".join(picked)
    if returncode not in (None, 0):
        prefix = f"Exited with code {returncode}."
        msg = f"{prefix}\n{msg}" if msg else prefix
    return msg or "Unknown error."


def _output_is_stdout(cmd: Sequence[str]) -> bool:
    if not cmd:
        return False
    last = str(cmd[-1])
    return last in ("-", "pipe:", "pipe:1")


def prepare_ffmpeg_cmd(cmd: Sequence[str]) -> list[str]:
    """Add ``-hide_banner``, ``-nostdin`` and machine-readable progress to an
    FFmpeg command (idempotent; other tools are returned unchanged)."""
    cmd = [str(c) for c in cmd]
    if not cmd or not is_ffmpeg(cmd[0]):
        return cmd
    head, rest = cmd[:1], cmd[1:]
    extra: list[str] = []
    if "-hide_banner" not in rest:
        extra.append("-hide_banner")
    reads_stdin = any(
        rest[i] == "-i" and i + 1 < len(rest) and rest[i + 1] in ("-", "pipe:", "pipe:0")
        for i in range(len(rest))
    )
    if "-nostdin" not in rest and not reads_stdin:
        extra.append("-nostdin")
    if "-progress" not in rest:
        target = "pipe:2" if _output_is_stdout(cmd) else "pipe:1"
        extra += ["-progress", target, "-nostats", "-stats_period", "0.5"]
    return head + extra + rest


_INPUT_OPTS_WITH_VALUE = {
    "-ss",
    "-t",
    "-to",
    "-sseof",
    "-itsoffset",
    "-stream_loop",
    "-f",
    "-r",
    "-framerate",
    "-c",
    "-c:v",
    "-c:a",
    "-vcodec",
    "-acodec",
    "-hwaccel",
    "-hwaccel_output_format",
    "-i",
    "-loop",
    "-safe",
    "-protocol_whitelist",
    "-analyzeduration",
    "-probesize",
    "-fflags",
    "-video_size",
    "-pix_fmt",
    "-sample_rate",
    "-ar",
    "-ac",
    "-channel_layout",
    "-display_rotation",
}


_LIVE_FORMATS = (
    "lavfi",
    "concat",
    "image2",
    "gdigrab",
    "ddagrab",
    "x11grab",
    "dshow",
    "avfoundation",
    "pulse",
    "alsa",
)
_Options = dict[str, str]


def _option_groups(args: list[str]) -> tuple[list[tuple[str, _Options]], _Options]:
    """Split a command's arguments into ([(input, its options)], output options)."""
    inputs: list[tuple[str, _Options]] = []
    opts: _Options = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-i" and i + 1 < len(args):
            inputs.append((args[i + 1], opts))
            opts = {}
            i += 2
        elif a.startswith("-") and i + 1 < len(args) and a in _INPUT_OPTS_WITH_VALUE:
            opts[a] = args[i + 1]
            i += 2
        else:
            i += 1
    return inputs, opts


def _input_span(path: str, in_opts: _Options, out_opts: _Options) -> float | None:
    """How much of the input file *path* the command reads (None: unknown)."""
    total = duration_of(path)
    if not total:
        return None
    start = parse_time(in_opts.get("-ss", 0)) + parse_time(out_opts.get("-ss", 0))
    remaining = max(0.0, total - start)
    if "-t" in in_opts:
        remaining = min(remaining, parse_time(in_opts["-t"]))
    elif "-to" in in_opts:
        remaining = min(
            remaining, max(0.0, parse_time(in_opts["-to"]) - parse_time(in_opts.get("-ss", 0)))
        )
    return remaining or None


def guess_output_duration(cmd: Sequence[str]) -> float | None:
    """Best-effort expected duration of an FFmpeg command's output, used for
    percentage progress when the caller did not supply one."""
    args = [str(c) for c in cmd[1:]]
    inputs, out_opts = _option_groups(args)
    try:
        if "-t" in out_opts:
            return parse_time(out_opts["-t"])
        if "-to" in out_opts:
            return max(0.0, parse_time(out_opts["-to"]) - parse_time(out_opts.get("-ss", 0)))
    except ValueError:
        pass
    if "-frames:v" in args or "-vframes" in args or not inputs:
        return None
    path, in_opts = inputs[0]
    try:
        if in_opts.get("-f") in _LIVE_FORMATS:  # a generated or captured input: only -t says
            return parse_time(in_opts["-t"]) if "-t" in in_opts else None
        return _input_span(path, in_opts, out_opts)
    except ValueError:
        return None


_LINE_SPLIT = re.compile(rb"[\r\n]")

_PROGRESS_KEYS = {
    "frame",
    "fps",
    "bitrate",
    "total_size",
    "out_time_us",
    "out_time_ms",
    "out_time",
    "dup_frames",
    "drop_frames",
    "speed",
    "progress",
}


def _parse_progress_block(
    block: dict[str, str], duration: float | None, started: float
) -> Progress:
    p = Progress(elapsed=time.monotonic() - started)
    us = block.get("out_time_us") or block.get("out_time_ms")
    if us not in (None, "", "N/A"):
        p.out_time = max(0.0, _f(us) / 1_000_000.0)
    elif block.get("out_time") not in (None, "", "N/A"):
        with contextlib.suppress(ValueError):
            p.out_time = parse_time(block["out_time"])
    sp = str(block.get("speed", "")).strip().rstrip("x")
    if sp and sp != "N/A":
        p.speed = _f(sp) or None
    p.fps = _f(block.get("fps")) or None
    p.frame = _i(block.get("frame")) or None
    p.size = _i(block.get("total_size")) or None
    p.done = block.get("progress") == "end"
    if duration and duration > 0:
        p.fraction = 1.0 if p.done else max(0.0, min(1.0, p.out_time / duration))
        if p.fraction and p.fraction > 0.001 and not p.done:
            if p.speed:
                p.eta = max(0.0, (duration - p.out_time) / p.speed)
            else:
                p.eta = max(0.0, p.elapsed / p.fraction - p.elapsed)
    return p


class _Reader:
    """Splits a running program's output into log lines and FFmpeg ``-progress``
    blocks, keeping the last *tail* lines and passing both on to the callbacks."""

    def __init__(
        self,
        tail: int,
        duration: float | None,
        on_line: Callable[[str], None] | None,
        on_progress: Callable[[Progress], None] | None,
    ):
        self.lines: deque[str] = deque(maxlen=tail)
        self.duration = duration
        self.started = time.monotonic()
        self._on_line, self._on_progress = on_line, on_progress
        self._block: dict[str, str] = {}

    def emit(self, text: str) -> None:
        self.lines.append(text)
        if self._on_line:
            try:
                self._on_line(text)
            except Exception:  # a bug in the caller's callback: log, keep running
                logger.exception("on_line callback failed")

    def progress(self, p: Progress) -> None:
        if self._on_progress:
            try:
                self._on_progress(p)
            except Exception:
                logger.exception("on_progress callback failed")

    def _progress_line(self, text: str) -> bool:
        key, sep, val = text.partition("=")
        # per-stream keys (stream_0_0_q, stream_0_1_psnr_y...) come once per output stream in every
        # block: as log lines they'd flood the console and push real errors out of the log tail
        if not sep or (key not in _PROGRESS_KEYS and not key.startswith("stream_")):
            return False
        self._block[key] = val.strip()
        if key == "progress":
            self.progress(_parse_progress_block(dict(self._block), self.duration, self.started))
            self._block.clear()
        return True

    def _piece(self, raw: bytes, is_progress_stream: bool) -> None:
        piece = raw.decode("utf-8", "replace").strip()
        if piece and not (is_progress_stream and self._progress_line(piece)):
            self.emit(piece)

    def pump(self, stream: IO[bytes], is_progress_stream: bool) -> None:
        """Read *stream* to its end.  Splits on BOTH \\r and \\n: tools that redraw
        one status line with \\r (yt-dlp, ffmpeg -stats) would otherwise only be
        seen when a \\n finally arrives."""
        buf = b""
        reader = getattr(stream, "read1", None) or stream.read
        try:
            while chunk := reader(65536):
                parts = _LINE_SPLIT.split(buf + chunk)
                buf = parts.pop()
                for raw in parts:
                    self._piece(raw, is_progress_stream)
            if buf:
                self._piece(buf, is_progress_stream)
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                logger.debug("_pump: best-effort step failed", exc_info=True)


def _expected_duration(argv: list[str], duration: float | None) -> float | None:
    if duration is not None or not argv or not is_ffmpeg(argv[0]):
        return duration
    try:
        return guess_output_duration(argv)
    except Exception:
        logger.debug("run: best-effort step failed", exc_info=True)
        return None


def _wait(
    child: proc.Popen[Any],
    result: RunResult,
    reader: _Reader,
    cancel: Callable[[], bool] | None,
    timeout: float | None,
) -> None:
    """Wait for *child*, killing it when *cancel()* says so or *timeout* passes."""
    while True:
        try:
            child.wait(timeout=0.2)
            return
        except proc.TimeoutExpired:
            pass
        if cancel is not None and _asked_to_stop(cancel):
            result.cancelled = True
            kill_tree(child)
            return
        if timeout is not None and time.monotonic() - reader.started > timeout:
            result.timed_out = True
            reader.emit(f"Timed out after {timeout:.0f} s")
            kill_tree(child)
            return


def _asked_to_stop(cancel: Callable[[], bool]) -> bool:
    try:
        return bool(cancel())
    except Exception:
        logger.exception("cancel callback failed")
        return False


def run(
    cmd: Sequence[str],
    *,
    duration: float | None = None,
    on_line: Callable[[str], None] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float | None = None,
    cwd: str | None = None,
    tail: int = 200,
) -> RunResult:
    """Run *cmd* to completion, streaming its log and progress.

    * FFmpeg commands get ``-progress`` added automatically; *duration* (the
      expected output length in seconds) enables percentage/ETA - when omitted
      it is guessed from the command.
    * *on_line(str)* receives each log line (stderr; stdout for non-FFmpeg
      tools such as yt-dlp).  *on_progress(Progress)* receives progress updates.
    * *cancel()* is polled; when it returns True the process tree is killed.
    * Never raises for a failing command - inspect ``RunResult``.
    """
    argv = prepare_ffmpeg_cmd(cmd)
    ffmpeg_mode = bool(argv) and is_ffmpeg(argv[0])
    progress_on_stderr = ffmpeg_mode and "pipe:2" in argv
    reader = _Reader(tail, _expected_duration(argv, duration), on_line, on_progress)
    result = RunResult(returncode=-1)
    try:
        child = proc.spawn(argv, stdout=proc.PIPE, stderr=proc.PIPE, cwd=cwd)
    except errors.ProcessError as exc:
        missing = isinstance(exc, errors.ProgramNotFound)
        name = argv[0] if argv else "?"
        reader.lines.append(
            f"Program not found: {name}" if missing else f"Could not start {name}: {exc}"
        )
        result.returncode = 127 if missing else 126
        result.log_tail = list(reader.lines)
        return result

    assert child.stderr is not None and child.stdout is not None  # both are pipes (above)
    err_thread = tasks.start_service(
        partial(reader.pump, child.stderr, progress_on_stderr), name="ffmpeg-stderr"
    )
    out_thread = tasks.start_service(
        partial(reader.pump, child.stdout, ffmpeg_mode and not progress_on_stderr),
        name="ffmpeg-stdout",
    )
    try:
        _wait(child, result, reader, cancel, timeout)
    finally:
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)
        proc.forget(child)

    rc = child.returncode if child.returncode is not None else -1
    result.returncode = rc if not result.cancelled else (rc if rc != 0 else -1)
    result.log_tail = list(reader.lines)
    result.elapsed = time.monotonic() - reader.started
    if result.returncode == 0 and not result.cancelled:
        d = reader.duration
        reader.progress(
            Progress(
                out_time=d or 0.0, fraction=1.0 if d else None, elapsed=result.elapsed, done=True
            )
        )
    return result


def check(cmd: Sequence[str], **kwargs: Any) -> RunResult:
    """:func:`run`, raising :class:`CancelledError` / :class:`CommandError`
    on cancellation or failure - convenient inside multi-step jobs."""
    res = run(cmd, **kwargs)
    if res.cancelled:
        raise CancelledError("cancelled")
    if res.returncode != 0:
        raise CommandError(res, cmd)
    return res


@overload
def capture(
    cmd: Sequence[str], *, timeout: float | None = ..., text: Literal[True] = ...
) -> proc.ProcResult[str]: ...
@overload
def capture(
    cmd: Sequence[str], *, timeout: float | None = ..., text: Literal[False]
) -> proc.ProcResult[bytes]: ...


def capture(
    cmd: Sequence[str], *, timeout: float | None = 60.0, text: bool = True
) -> proc.ProcResult[Any]:
    """Run a short command and collect its output (see :func:`core.proc.run`)."""
    if text:
        return proc.run(cmd, timeout=timeout)
    return proc.run(cmd, timeout=timeout, text=False)


def grab_frame_png(
    path: str,
    at: float,
    *,
    width: int = 0,
    height: int = 0,
    vf: str = "",
    keep_time: bool = False,
    timeout: float = 20.0,
) -> bytes | None:
    """Return one frame of *path* at *at* seconds as PNG bytes (or None).

    Input-side seeking with decoding is frame-accurate in modern FFmpeg.
    *vf* is an optional filter chain applied to the full-size frame first
    (used for live previews of colour grades, keys, scopes...).
    *keep_time* keeps the source timestamps (``-copyts``) so time-based
    filters - subtitles, titles, ``enable=`` ranges - see the real time.
    *width*/*height* bound the size (aspect preserved, even dimensions).
    """
    chain: list[str] = [vf] if vf else []
    if width or height:
        w = width or -2
        h = height or -2
        chain.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2")
    # 8-bit RGB: a 10-bit source would otherwise give a 16-bit PNG, ~4x bigger and ~3x
    # slower for Tk to decode on the UI thread (High Performance Python findings, R5)
    chain.append("format=rgb24")
    gpu = any(
        "libplacebo" in f or "_vulkan" in f for f in chain
    )  # GPU filters need a Vulkan device
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        *(["-copyts"] if keep_time else []),
        *(VULKAN_DEVICE if gpu else ()),
        "-ss",
        ts(max(0.0, at)),
        "-i",
        path,
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
    ]
    cmd += ["-vf", ",".join(chain)]
    cmd += ["-f", "image2pipe", "-c:v", "png", "-"]
    try:
        r = proc.run(cmd, timeout=timeout, text=False)
    except errors.ProcessError:
        return None
    frame = r.stdout
    if not r.ok or not frame.startswith(b"\x89PNG"):
        return None
    return frame


def has_filter(name: str) -> bool:
    """True if the FFmpeg in use was built with filter *name* (see core.capabilities)."""
    from core import capabilities

    return name in capabilities.get("filters")


def version_of(binary: str | None = None) -> str:
    """Version string of an FFmpeg-family binary (``"9.0.2"``, ``"N-12345-g…"``),
    ``"Missing"`` or ``"Unknown"``."""
    binary = binary or ffmpeg_path()
    try:
        r = capture([binary, "-version"], timeout=10)
    except errors.ProcessError:
        return "Missing"
    first = str(r.stdout or "").splitlines()[:1]
    m = re.search(r"version\s+(\S+)", first[0]) if first else None
    if not m:
        return "Unknown"
    ver = m.group(1)
    ver = re.sub(r"^n(?=\d)", "", ver)  # "n9.0.2" -> "9.0.2"
    return ver


_TAKES_VALUE = frozenset(
    {
        "-ss",
        "-t",
        "-to",
        "-i",
        "-vf",
        "-af",
        "-c:v",
        "-c:a",
        "-crf",
        "-preset",
        "-b:v",
        "-b:a",
        "-movflags",
        "-r",
        "-s",
        "-map",
        "-f",
    }
)


def format_cmd_lines(cmd: Sequence[str]) -> str:
    """argv as a readable shell command, one option (with its value) per
    backslash-continued line."""
    if not cmd:
        return ""
    import shlex

    lines = [shlex.quote(cmd[0])]
    i = 1
    while i < len(cmd):
        p = cmd[i]
        if p.startswith("-") and i + 1 < len(cmd) and cmd[i + 1] not in _TAKES_VALUE:
            lines.append(f"  {shlex.quote(p)} {shlex.quote(cmd[i + 1])}")
            i += 2
        else:
            lines.append(f"  {shlex.quote(p)}")
            i += 1
    return " \\\n".join(lines)


#: input options that open a Vulkan device (for libplacebo, nlmeans_vulkan)
VULKAN_DEVICE = ("-init_hw_device", "vulkan")


def test_encode_cmd(out: str) -> list[str]:
    """A one-second H.264 + AAC test file: proves FFmpeg can encode at all."""
    sources = [
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=25:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=d=1",
    ]
    codecs = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"]
    return [ffmpeg_path(), "-y", *sources, *codecs, out]


def listen_cmd(path: str) -> list[str]:
    """Play *path*'s sound with no window; ends by itself."""
    return [ffplay_path(), "-nodisp", "-autoexit", "-loglevel", "quiet", path]


def example_cmd(kind: str) -> str:
    """A command to paste into the developer console: "version" or "codecs"."""
    return format_cmd([ffmpeg_path(), {"version": "-version", "codecs": "-codecs"}[kind]])


def format_cmd(cmd: Sequence[str]) -> str:
    """Render argv as a copy-pasteable shell command for the current OS."""
    if _IS_WIN:
        return proc.list2cmdline([str(c) for c in cmd])
    import shlex

    return " ".join(shlex.quote(str(c)) for c in cmd)
