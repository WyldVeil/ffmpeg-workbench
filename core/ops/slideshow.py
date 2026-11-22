"""
core/ops/slideshow.py - photos, animated GIFs, video clips and title cards into
one video, for tabs/social/slideshow.py.

Pure functions: values in, FFmpeg arguments and subtitle scripts out - no
widgets, no Tk.  How a slideshow is made:

1. every slide becomes a clip of its own length (``slide_cmd``): a photo is
   framed and moved Ken Burns style, a GIF plays (looping to fill its time), a
   video gives the chosen part (with its sound, if kept), a card is text on a
   colour;
2. one pass joins the clips with each slide's own transition
   (``join_graph``, any of core/ops/transitions.py, or a cut), burns the
   captions in, and lays the sound under it (``audio_graph``): the clips' own
   sound, music tracks one after another - quieter while a clip speaks.

``fit_to_music`` spreads the slides over the music; ``on_beats`` moves every
change to the nearest beat.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from core import ass
from core import ffmpeg as ff
from core.ops import transitions

CUT = "cut"  # a join with no transition

PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif")
ANIMATION_EXTS = (".gif",)
VIDEO_EXTS = (
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".mts",
    ".m2ts",
    ".wmv",
    ".mpg",
    ".mpeg",
)


class Kind(StrEnum):
    PHOTO = "photo"
    ANIMATION = "animation"  # an animated GIF: plays through, looping
    VIDEO = "video"
    CARD = "card"  # a title or ending card: text on a colour


def kind_of(path: str) -> Kind | None:
    """What a file can be in a slideshow (None: it can't)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in PHOTO_EXTS:
        return Kind.PHOTO
    if ext in ANIMATION_EXTS:
        return Kind.ANIMATION
    if ext in VIDEO_EXTS:
        return Kind.VIDEO
    return None


@dataclass(frozen=True, slots=True)
class Card:
    title: str
    subtitle: str = ""
    background: str = "#101010"
    colour: str = "#FFFFFF"


@dataclass(frozen=True, slots=True, kw_only=True)
class Slide:
    """One slide, everything decided: *seconds* on screen (with its transitions)."""

    kind: Kind
    path: str = ""
    seconds: float = 4.0
    motion: str = "static"  # photos: see motion_filter
    caption: str = ""
    start: float = 0.0  # videos: where the part starts in the file
    sound: bool = False  # videos: keep their sound
    card: Card | None = None


@dataclass(frozen=True, slots=True)
class Join:
    """How a slide gives way to the next: a transition *name* (or CUT) lasting *seconds*."""

    name: str
    seconds: float = 0.0

    @property
    def overlap(self) -> float:
        return 0.0 if self.name == CUT else self.seconds


@dataclass(frozen=True, slots=True)
class Look:
    """Every slide's frame: *fit* "blur", "bars" or "fill"; *zoom* the Ken Burns amount."""

    width: int
    height: int
    fps: str
    fit: str = "blur"
    zoom: float = 1.2
    background: str = "#000000"  # behind "bars"


# ── Timing ───────────────────────────────────────────────────────────────────


def timeline(seconds: Sequence[float], joins: Sequence[Join]) -> tuple[list[float], float]:
    """(when each slide starts, total length): transitions overlap two slides."""
    starts = [0.0] * len(seconds)
    for i in range(1, len(seconds)):
        starts[i] = starts[i - 1] + seconds[i - 1] - joins[i - 1].overlap
    return starts, (starts[-1] + seconds[-1]) if seconds else 0.0


def check_joins(seconds: Sequence[float], joins: Sequence[Join]) -> None:
    """ValueError (a sentence) when a transition is too long for its slides."""
    for i, j in enumerate(joins):
        limit = min(seconds[i], seconds[i + 1]) / 2
        if j.overlap >= limit:
            raise ValueError(
                f"The transition after slide {i + 1} ({j.seconds:g}s) must be shorter than half "
                f"of the shorter slide next to it ({limit:g}s)."
            )


def fit_to_music(
    fixed: Sequence[float | None], joins: Sequence[Join], music: float, shortest: float = 0.5
) -> list[float]:
    """Slide lengths filling *music* seconds: slides with a length keep it, the
    others (None) share what is left equally (never under *shortest*)."""
    free = [i for i, s in enumerate(fixed) if s is None]
    taken = sum(s for s in fixed if s is not None)
    share = (music + sum(j.overlap for j in joins) - taken) / max(len(free), 1)
    return [max(shortest, share) if s is None else s for s in fixed]


def on_beats(
    seconds: Sequence[float], joins: Sequence[Join], beats: Sequence[float], shortest: float = 0.5
) -> list[float]:
    """Slide lengths moved so each change (the start of a cut or transition)
    lands on the nearest beat; a slide never gets shorter than *shortest*
    (plus its transitions)."""
    if not beats or len(seconds) < 2:
        return list(seconds)
    starts, _total = timeline(seconds, joins)
    out = list(seconds)
    prev = 0.0  # where the previous slide began
    for i in range(1, len(seconds)):
        earliest = prev + shortest + joins[i - 1].overlap + (joins[i - 2].overlap if i > 1 else 0.0)
        target = starts[i]
        candidates = [b for b in beats if b >= earliest] or [max(target, earliest)]
        change = min(candidates, key=lambda b: abs(b - target))
        out[i - 1] = change - prev + joins[i - 1].overlap
        prev = change
    return out


# ── One slide -> one clip ────────────────────────────────────────────────────


def motion_filter(motion: str, w: int, h: int, fps: str, frames: int, zoom: float = 1.2) -> str:
    """Ken Burns on ONE input frame: expressions use the output frame number."""
    d = max(frames - 1, 1)
    p = f"(on/{d})"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        z, x, y = f"1+{zoom - 1:.4f}*{p}", cx, cy
    elif motion == "zoom_out":
        z, x, y = f"{zoom:.4f}-{zoom - 1:.4f}*{p}", cx, cy
    elif motion == "pan_right":
        z, x, y = f"{zoom:.4f}", f"(iw-iw/zoom)*{p}", cy
    elif motion == "pan_left":
        z, x, y = f"{zoom:.4f}", f"(iw-iw/zoom)*(1-{p})", cy
    elif motion == "pan_down":
        z, x, y = f"{zoom:.4f}", cx, f"(ih-ih/zoom)*{p}"
    elif motion == "pan_up":
        z, x, y = f"{zoom:.4f}", cx, f"(ih-ih/zoom)*(1-{p})"
    else:
        z, x, y = "1", "0", "0"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}"


def frame_filter(fit: str, w: int, h: int, *, scale: int = 2, background: str = "#000000") -> str:
    """Frame a picture at *scale* times the output size (2 for photos: smooth
    zoompan sub-pixel steps; 1 for moving pictures)."""
    W, H = scale * w, scale * h
    if fit == "blur":
        return (
            f"split[a][b];[a]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
            f"crop={W // 8}:{H // 8},gblur=sigma=6,eq=brightness=-0.08,scale={W}:{H}[bg];"
            f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    if fit == "bars":
        colour = "0x" + background.lstrip("#")
        return (
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={colour},setsar=1"
        )
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"


def _frames(slide: Slide, look: Look) -> int:
    num, _, den = look.fps.partition("/")
    return max(2, round(slide.seconds * float(num) / float(den or 1)))


_X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p"]
_PCM = ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]


def clip_name(folder: str, i: int, slide: Slide) -> str:
    """Where slide *i*'s clip goes (Matroska when it carries sound)."""
    return os.path.join(folder, f"s{i:04d}.{'mkv' if slide.sound else 'mp4'}")


def card_script(card: Card, look: Look, font: str = "Montserrat") -> ass.Script:
    """The card's text, centred: the title, the subtitle under it."""
    w, h = look.width, look.height
    u = min(w, h) / 1080
    sc = ass.Script(w, h)
    sc.style(
        ass.Style("T", font=font, size=round(110 * u), colour=card.colour, bold=True, alignment=5)
    )
    sc.style(ass.Style("S", font=font, size=round(54 * u), colour=card.colour, alignment=8))
    gap = (50 * u) if card.subtitle else 0
    sc.add(
        0,
        36000,
        f"{{\\pos({w / 2:.0f},{h / 2 - gap:.0f})\\fad(400,400)}}{ass.escape(card.title)}",
        "T",
    )
    if card.subtitle:
        y = h / 2 + 30 * u
        sc.add(
            0,
            36000,
            f"{{\\pos({w / 2:.0f},{y:.0f})\\fad(600,400)}}{ass.escape(card.subtitle)}",
            "S",
        )
    return sc


def slide_cmd(slide: Slide, look: Look, clip: str, card_ass: str = "") -> list[str]:
    """One slide -> a clip of exactly its length at the slideshow's size and rate
    (*card_ass*: the card's script, for a card)."""
    w, h, fps, frames = look.width, look.height, look.fps, _frames(slide, look)
    head = [ff.ffmpeg_path(), "-y"]
    tail = ["-frames:v", str(frames), *_X264]
    if slide.kind is Kind.PHOTO:
        framed = frame_filter(look.fit, w, h, background=look.background)
        graph = f"[0:v]{framed},{motion_filter(slide.motion, w, h, fps, frames, look.zoom)},format=yuv420p[v]"
        return [*head, "-i", slide.path, "-filter_complex", graph, "-map", "[v]", *tail, clip]
    moving = f"{frame_filter(look.fit, w, h, scale=1, background=look.background)},fps={fps},format=yuv420p"
    if slide.kind is Kind.ANIMATION:
        src = ["-ignore_loop", "0", "-i", slide.path]  # loops for as long as it is shown
        return [*head, *src, "-filter_complex", f"[0:v]{moving}[v]", "-map", "[v]", *tail, clip]
    if slide.kind is Kind.CARD:
        colour = "0x" + (slide.card.background if slide.card else "#101010").lstrip("#")
        src = ["-f", "lavfi", "-i", f"color=c={colour}:s={w}x{h}:r={fps}:d={slide.seconds:.3f}"]
        text = f",{ass.subtitles_filter(card_ass)}" if card_ass else ""
        return [
            *head,
            *src,
            "-filter_complex",
            f"[0:v]format=yuv420p{text}[v]",
            "-map",
            "[v]",
            *tail,
            clip,
        ]
    src = ["-ss", f"{slide.start:.3f}", "-t", f"{slide.seconds:.3f}", "-i", slide.path]
    sound = ["-map", "0:a:0", *_PCM] if slide.sound else ["-an"]
    return [*head, *src, "-filter_complex", f"[0:v]{moving}[v]", "-map", "[v]", *sound, *tail, clip]


# ── Joining ──────────────────────────────────────────────────────────────────


def join_graph(joins: Sequence[Join], starts: Sequence[float]) -> list[str]:
    """Filter steps joining inputs 0..n-1 (n = len(starts)) -> [xf], each pair
    with its own transition or a cut."""
    n = len(starts)
    # one time base for every clip: xfade needs its two inputs to agree, and
    # .mp4 clips, .mkv clips (with sound) and concat's output all differ
    parts = [f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[v{i}]" for i in range(n)]
    if n == 1:
        return [*parts, "[v0]null[xf]"]
    last = "[v0]"
    for i in range(1, n):
        label = "[xf]" if i == n - 1 else f"[x{i}]"
        j = joins[i - 1]
        if j.name == CUT:
            parts.append(f"{last}[v{i}]concat=n=2:v=1:a=0{label}")
        else:
            parts.append(f"{last}[v{i}]{transitions.xfade(j.name, j.seconds, starts[i])}{label}")
        last = label
    return parts


def caption_script(
    slides: Sequence[Slide],
    joins: Sequence[Join],
    look: Look,
    style: tuple[str, str],
) -> ass.Script:
    """Each slide's caption while it shows (not over its transitions);
    *style* = (font, position "bottom", "top" or "lower_third")."""
    font, position = style
    starts, _total = timeline([s.seconds for s in slides], joins)
    w, h = look.width, look.height
    u = min(w, h) / 1080
    sc = ass.Script(w, h)
    sc.style(
        ass.Style(
            "Cap",
            font=font,
            size=round(46 * u),
            bold=True,
            outline=2.5 * u,
            shadow=1.5 * u,
            back_alpha=0.5,
            alignment={"top": 8, "lower_third": 1}.get(position, 2),
            margin_l=round(60 * u),
            margin_r=round(60 * u),
            margin_v=round((h * 0.12) if position == "lower_third" else 50 * u),
        )
    )
    n = len(slides)
    for i, slide in enumerate(slides):
        if slide.caption.strip():
            a = starts[i] + (joins[i - 1].overlap / 2 if i else 0)
            b = starts[i] + slide.seconds - (joins[i].overlap / 2 if i < n - 1 else 0)
            sc.add(a, b, f"{{\\fad(300,300)}}{ass.escape(slide.caption)}", "Cap")
    return sc


# ── Sound ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class Music:
    """Music under the slides: *tracks* one after another (repeated if the
    slideshow is longer), starting *offset* seconds into the first."""

    tracks: tuple[str, ...]
    lengths: tuple[float, ...]  # each track's length
    offset: float = 0.0
    volume: float = 0.9
    fade_in: float = 0.5
    fade_out: float = 3.0
    duck: bool = True  # quieter while a clip's own sound plays


def music_inputs(music: Music, total: float) -> list[tuple[str, float]]:
    """(file, where to start in it) for each music input, in order, until the
    music covers *total* seconds."""
    out: list[tuple[str, float]] = []
    covered, k = 0.0, 0
    while covered < total and k < 500:
        i = k % len(music.tracks)
        seek = music.offset if k == 0 else 0.0
        out.append((music.tracks[i], seek))
        covered += max(0.5, music.lengths[i] - seek)
        k += 1
    return out


_STEREO = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


def _slide_sound(slides: Sequence[Slide], starts: Sequence[float], total: float) -> list[str]:
    """[snd]: the kept sound of every video slide at its place, faded at its ends."""
    parts: list[str] = []
    labels: list[str] = []
    for i, s in enumerate(slides):
        if s.kind is Kind.VIDEO and s.sound:
            ms = round(starts[i] * 1000)
            fade = f"afade=t=in:d=0.25,afade=t=out:st={max(0.0, s.seconds - 0.25):.3f}:d=0.25"
            parts.append(f"[{i}:a]{_STEREO},{fade},adelay={ms}|{ms}[s{i}]")
            labels.append(f"[s{i}]")
    if not labels:
        return []
    mix = f"amix=inputs={len(labels)}:duration=longest:normalize=0," if len(labels) > 1 else ""
    return [*parts, f"{''.join(labels)}{mix}apad=whole_dur={total:.3f},atrim=0:{total:.3f}[snd]"]


def _music(music: Music, first: int, count: int, total: float) -> list[str]:
    """[mus]: the music inputs *first*.. joined, trimmed, set in level and faded."""
    ins = [f"[{first + k}:a:0]{_STEREO}[m{k}]" for k in range(count)]
    joined = "".join(f"[m{k}]" for k in range(count)) + f"concat=n={count}:v=0:a=1[mc]"
    fo = min(music.fade_out, total / 3)
    shape = (
        f"[mc]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={music.volume:.2f},"
        f"afade=t=in:st=0:d={max(0.01, music.fade_in):.2f},afade=t=out:st={total - fo:.3f}:d={max(0.01, fo):.2f}[mus]"
    )
    return [*ins, joined, shape]


def audio_graph(
    slides: Sequence[Slide],
    joins: Sequence[Join],
    music: Music | None,
    music_first: int,
) -> tuple[list[str], str | None]:
    """(filter steps, the sound's label or None): the slides' sound and the
    music (inputs from *music_first*), the music ducked under the slides' sound."""
    starts, total = timeline([s.seconds for s in slides], joins)
    snd = _slide_sound(slides, starts, total)
    count = len(music_inputs(music, total)) if music else 0
    mus = _music(music, music_first, count, total) if music and count else []
    if snd and mus and music and music.duck:
        mix = [
            "[snd]asplit[snd1][key]",
            "[mus][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]",
            "[duck][snd1]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[a]",
        ]
        return [*snd, *mus, *mix], "[a]"
    if snd and mus:
        return [
            *snd,
            *mus,
            "[mus][snd]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[a]",
        ], "[a]"
    if snd or mus:
        return [*snd, *mus, f"{'[snd]' if snd else '[mus]'}anull[a]"], "[a]"
    return [], None


# ── The joining pass ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class Encode:
    """How the finished slideshow is encoded (from the page's encoder panel)."""

    video: tuple[str, ...] = (
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
    )
    pre_input: tuple[str, ...] = ()
    filter_suffix: str = ""  # e.g. a hardware upload, after every other filter
    audio: tuple[str, ...] = ("-c:a", "aac", "-b:a", "192k")


def join_cmd(
    clips: Sequence[str],
    slides: Sequence[Slide],
    joins: Sequence[Join],
    out: str,
    *,
    enc: Encode,
    music: Music | None = None,
    captions_ass: str = "",
) -> list[str]:
    """The pass that joins the slides' *clips* into *out*: transitions,
    captions (*captions_ass*), the slides' sound and the *music*."""
    starts, total = timeline([s.seconds for s in slides], joins)
    cmd = [ff.ffmpeg_path(), "-y", *enc.pre_input]
    for clip in clips:
        cmd += ["-i", clip]
    for path, seek in music_inputs(music, total) if music else []:
        cmd += [*(["-ss", f"{seek:.3f}"] if seek > 0 else []), "-i", path]
    parts = join_graph(joins, starts)
    video = "[xf]"
    if captions_ass:
        parts.append(f"[xf]{ass.subtitles_filter(captions_ass)}[cv]")
        video = "[cv]"
    if enc.filter_suffix:
        parts.append(f"{video}{enc.filter_suffix}[venc]")
        video = "[venc]"
    sound, label = audio_graph(slides, joins, music, len(clips))
    audio = ["-map", label, *enc.audio] if label else ["-an"]
    graph = ";".join([*parts, *sound])
    return [
        *cmd,
        "-filter_complex",
        graph,
        "-map",
        video,
        *audio,
        *enc.video,
        "-t",
        f"{total:.3f}",
        out,
    ]
