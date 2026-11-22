"""
core/catalog.py  -  What each tool is for, in plain words

Used by the Start page ("What do you want to do?"):

* ``TOOLS``   - for every tool in the sidebar: an icon, one plain sentence,
  and the words people use when they want it ("smaller", "vertical",
  "captions", "hiss"...).
* ``GOALS``   - everyday goals, each leading to the tools that do it.
* ``search(text)``  - tools ranked for whatever the person typed.
* ``suggest(info)`` - tools worth trying on a particular file, each with the
  reason ("It's 1.3 GB", "It contains a GPS location"...).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from core.tools import SPECS, Category, ToolId, ToolSpec, spec

if TYPE_CHECKING:
    from core.ffmpeg import MediaInfo, Stream

AIO: Final = spec(ToolId.ALL_IN_ONE_BUILDER).name


#: name -> what the Start page shows and searches (icon, blurb, words), for
#: every tool a person can open (declared in core/tools.py).
TOOLS: Final[Mapping[str, ToolSpec]] = {
    s.name: s for s in SPECS if s.category is not Category.HIDDEN
}


@dataclass(frozen=True)
class Goal:
    key: str
    icon: str
    title: str
    blurb: str
    tools: tuple[str, ...]


GOALS = (
    Goal(
        "cut",
        "✂",
        "Cut, trim & join",
        "Shorten clips, split them, or put several together",
        (
            "Quick Trimmer",
            "Batch Joiner",
            "Crossfader",
            "The Splicer",
            "Manual Multi-Splitter",
            "Multi-Clip Sequencer",
            "Scene Detector",
            "Silence Remover",
        ),
    ),
    Goal(
        "smaller",
        "📉",
        "Make a file smaller",
        "Fit a size limit or save space",
        (
            "Universal Downsizer",
            "Codec Cruncher",
            "Resolution Scaler",
            "WebM Maker",
            "Proxy Generator",
            "Compare & Quality",
        ),
    ),
    Goal(
        "social",
        "📱",
        "Post on social media",
        "Vertical versions, GIFs, thumbnails, chapters",
        (
            "The Shortifier",
            "Smart Reframe",
            "Pro GIF Maker",
            "Thumbnail Maker",
            "Chapter Markers",
            "Auto-Subtitles",
            "Universal Downsizer",
        ),
    ),
    Goal(
        "convert",
        "🔄",
        "Convert to another format",
        "MP4, MKV, MOV, WebM, MP3, GIF...",
        (
            "Format Converter",
            "Tracks & Remux",
            "Audio Converter",
            "Codec Cruncher",
            "HDR to SDR",
            "Audio Extractor",
            "WebM Maker",
            "Pro GIF Maker",
        ),
    ),
    Goal(
        "text",
        "💬",
        "Subtitles, titles & text",
        "Captions from speech, titles, watermarks",
        (
            "Auto-Subtitles",
            "Hard-Subber",
            "Tracks & Remux",
            "Animated Titles",
            "Intro/Outro Maker",
            "Watermarker",
            "Image Watermark",
            "Karaoke Generator",
        ),
    ),
    Goal(
        "sound",
        "🔊",
        "Fix the sound",
        "Too quiet, noisy, out of sync, long pauses",
        (
            "Loudness Normalizer",
            "Voice Isolation",
            "Silence Remover",
            "Audio Sync Shifter",
            "Audio Dynamics",
            "The Muter",
            "Laugh Track Remover",
            "Waveform Editor",
        ),
    ),
    Goal(
        "music",
        "🎵",
        "Music, voice & mixing",
        "Soundtracks, voice-overs, karaoke",
        (
            "Audio Mixer",
            "Music Ducker",
            "Audio Replacer",
            "TTS Voice-Over",
            "Voice Changer",
            "Audio Extractor",
            "Karaoke Generator",
            "Beat Sync Cutter",
            "MIDIfier",
        ),
    ),
    Goal(
        "look",
        "🎨",
        "Colour & look",
        "Brighter, warmer, cinematic, matching",
        (
            "Basic Color Corrector",
            "LUT Applicator",
            "Colour Match",
            "Special Effects",
            "Green Screen Keyer",
            "Video Scopes",
        ),
    ),
    Goal(
        "repair",
        "🩹",
        "Repair a video",
        "Shaky, noisy, sideways, washed out, interlaced, black bars",
        (
            "Deshaker",
            "Denoise",
            "Rotate & Flip",
            "HDR to SDR",
            "Sharpen",
            "Deinterlace",
            "Auto-Cropper",
            "Manual Crop",
            "Framerate Interpolator",
            "Resolution Scaler",
        ),
    ),
    Goal(
        "motion",
        "✨",
        "Effects, speed & motion",
        "Slow motion, zooms, reverse, freeze, loops",
        (
            "Speed Ramper",
            "Framerate Interpolator",
            "Animated Zoom",
            "Video Reverser",
            "Freeze Frame",
            "Clip Looper",
            "Special Effects",
            "Transition Studio",
        ),
    ),
    Goal(
        "combine",
        "🧩",
        "Combine & create",
        "Collages, picture-in-picture, slideshows, intros",
        (
            "Video Collage",
            "Side-by-Side",
            "Picture-in-Picture",
            "Slideshow Maker",
            "Intro/Outro Maker",
            AIO,
            "Media Generator",
        ),
    ),
    Goal(
        "privacy",
        "🔒",
        "Privacy",
        "Remove location and names, blur faces, bleep words",
        ("Metadata Cleaner", "Privacy Blur", "The Muter", "Voice Changer"),
    ),
    Goal(
        "get",
        "⬇",
        "Download & record",
        "From YouTube and 1000+ sites, or your screen",
        ("Video Downloader", "Screen Recorder", "Snapshot", "Frame Extractor"),
    ),
    Goal(
        "manage",
        "🧰",
        "Inspect, batch & automate",
        "File details, contact sheets, folders of files, job queue",
        (
            "Media Inspector",
            "Contact Sheet",
            "Compare & Quality",
            "Batch Processor",
            "Encode Queue",
            "Preset Manager",
            "Advanced Settings",
        ),
    ),
)


# ── Search ────────────────────────────────────────────────────────────────────

#: everyday words -> words the catalogue uses (both spellings, common phrasings)
ALIASES = {
    "color": "colour",
    "colors": "colour",
    "colours": "colour",
    "stabilize": "stabilise",
    "normalize": "normalise",
    "grey": "gray",
    "caption": "captions",
    "subtitle": "subtitles",
    "subs": "subtitles",
    "cc": "captions",
    "shrink": "smaller",
    "compress": "smaller",
    "reduce": "smaller",
    "tiny": "smaller",
    "huge": "big",
    "merge": "join",
    "combine": "join",
    "concat": "join",
    "glue": "join",
    "stitch": "join",
    "portrait": "vertical",
    "tiktok": "vertical",
    "reels": "vertical",
    "shorts": "vertical",
    "slowmo": "slow",
    "slo": "slow",
    "timelapse": "fast",
    "hyperlapse": "fast",
    "quieter": "volume",
    "louder": "loud",
    "boost": "loud",
    "hiss": "noise",
    "hum": "noise",
    "wind": "noise",
    "shaky": "shake",
    "wobbly": "shake",
    "steady": "shake",
    "sideways": "rotate",
    "upside": "rotate",
    "gps": "location",
    "exif": "metadata",
    "rip": "extract",
    "screenshot": "still",
    "photo": "still",
    "transcribe": "subtitles",
    "transcript": "subtitles",
    "bleep": "mute",
    "swear": "mute",
}

#: words that say nothing about which tool is wanted
STOPWORDS = {
    "a",
    "an",
    "the",
    "it",
    "its",
    "my",
    "me",
    "i",
    "to",
    "of",
    "and",
    "or",
    "up",
    "in",
    "on",
    "for",
    "with",
    "how",
    "do",
    "can",
    "want",
    "from",
    "this",
    "that",
    "make",
    "get",
    "some",
    "into",
    "out",
    "video",
    "videos",
    "file",
    "files",
    "please",
    "need",
    "way",
    "is",
    "be",
    "so",
}

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str, *, query: bool = False) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(text.lower()):
        if query and w in STOPWORDS:
            continue
        out.append(w)
        if query and w in ALIASES:  # only the person's words: tool keywords are already exact
            out.append(ALIASES[w])
        if w.endswith("s") and len(w) > 3:
            out.append(w[:-1])
    return out


def search(text: str, limit: int = 12) -> list[str]:
    """Tool names that match *text*, best first."""
    query = _tokens(text, query=True)
    if not query:
        return []
    goal_words: dict[str, list[str]] = {}
    leaders = {g.tools[0] for g in GOALS}  # ties go to the tool a goal lists first
    for g in GOALS:
        for name in g.tools:
            goal_words.setdefault(name, []).extend(_tokens(g.title + " " + g.blurb))
    scored: list[tuple[float, str]] = []
    for name, info in TOOLS.items():
        name_w = _tokens(name)
        words = set(_tokens(info.words))
        blurb = set(_tokens(info.blurb))
        goals = set(goal_words.get(name, []))
        score = 0.0
        for q in query:
            if len(q) < 2:
                continue
            if q in name_w:
                score += 6
            elif any(w.startswith(q) for w in name_w) and len(q) >= 3:
                score += 4
            if q in words:
                score += 4
            elif len(q) >= 3 and any(w.startswith(q) for w in words):
                score += 2
            if q in blurb:
                score += 1.5
            if q in goals:
                score += 0.5
        if score > 0:
            scored.append((score + (0.25 if name in leaders else 0.0), name))
    if not scored:
        return []
    scored.sort(key=lambda s: (-s[0], s[1]))
    best = scored[0][0]
    return [name for score, name in scored[:limit] if score >= best * 0.4]


# ── Suggestions for a file ────────────────────────────────────────────────────


def _size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def describe(info: MediaInfo) -> str:
    """One line about a file: 'Video · 3840×2160 HDR · 60 fps · 2:31 · 1.3 GB'."""
    parts: list[str] = []
    v = info.video if info.video is not None and not info.video.attached_pic else None
    if v is not None and info.duration <= 0.05:
        parts.append("Image")
    elif v is not None:
        parts.append("Video")
    elif info.has_audio:
        parts.append("Audio")
    if v is not None:
        w, h = v.display_size
        parts.append(f"{w}×{h}" + (" HDR" if v.is_hdr else ""))
        if info.duration > 0.05 and v.fps:
            parts.append(f"{v.fps:.3g} fps")
    if info.duration > 0.05:
        m, s = divmod(round(info.duration), 60)
        h_, m = divmod(m, 60)
        parts.append(f"{h_}:{m:02d}:{s:02d}" if h_ else f"{m}:{s:02d}")
    if info.size:
        parts.append(_size(info.size))
    return " · ".join(parts)


Suggestion = tuple[str, str]  # tool name, why it's worth trying


def _privacy(info: MediaInfo) -> list[Suggestion]:
    from core import metaclean as mc

    cats = {f.category for f in mc.inspect(info)}
    if "location" in cats:
        return [("Metadata Cleaner", "It contains a GPS location - remove it before sharing")]
    if cats & {"device", "telemetry"}:
        why = "It names the camera or phone it was made with - remove that before sharing"
        return [("Metadata Cleaner", why)]
    return []


def _tracks(info: MediaInfo) -> str:
    """'2 audio tracks, 1 subtitle track' - or '' when there's nothing to choose."""
    n_audio = sum(1 for s in info.streams if s.type == "audio")
    n_subs = sum(1 for s in info.streams if s.type == "subtitle")
    parts = (
        f"{n_audio} audio tracks" if n_audio > 1 else "",
        f"{n_subs} subtitle track{'s' if n_subs > 1 else ''}" if n_subs else "",
    )
    return ", ".join(x for x in parts if x)


def _for_video(info: MediaInfo, v: Stream) -> list[Suggestion]:
    w, h = v.display_size
    tracks = _tracks(info)
    talk = info.has_audio
    rules: list[tuple[bool, str, str]] = [
        (
            v.is_hdr,
            "HDR to SDR",
            "It's HDR - convert it so it doesn't look washed out on normal screens",
        ),
        (
            info.size > 25_000_000,
            "Universal Downsizer",
            f"It's {_size(info.size)} - shrink it to fit Discord, email or WhatsApp",
        ),
        (
            bool(tracks),
            "Tracks & Remux",
            f"It has {tracks} - choose which to keep, label them, or add more",
        ),
        (
            (v.field_order or "") in ("tt", "bb", "tb", "bt"),
            "Deinterlace",
            "It's interlaced - remove the comb lines",
        ),
        (w > h, "The Shortifier", "Make a vertical version for Shorts, TikTok or Reels"),
        (max(w, h) < 1280, "Resolution Scaler", f"It's small ({w}×{h}) - upscale it to HD"),
        (talk and info.duration > 20, "Auto-Subtitles", "Turn what's said into subtitles"),
        (info.duration > 30, "Quick Trimmer", "Cut the start or end off"),
        (talk and info.duration > 60, "Silence Remover", "Cut out the pauses"),
        (talk, "Loudness Normalizer", "Make the volume even and right for YouTube"),
        (info.duration <= 30, "Pro GIF Maker", "Make it an animated GIF"),
        (True, "Snapshot", "Save a frame as a full-quality picture"),
        (True, "Format Converter", "Convert it to another format"),
    ]
    return [(name, why) for wanted, name, why in rules if wanted]


def _for_audio(info: MediaInfo) -> list[Suggestion]:
    rules: list[tuple[bool, str, str]] = [
        (info.duration > 20, "Auto-Subtitles", "Turn the speech into text or subtitles"),
        (True, "Loudness Normalizer", "Make the volume even"),
        (True, "Voice Isolation", "Remove background noise from a voice"),
        (info.duration > 60, "Silence Remover", "Cut out the pauses"),
        (True, "Audio Converter", "Convert it to MP3, FLAC, WAV..."),
        (True, "Waveform Editor", "See the sound and edit parts of it"),
        (True, "MIDIfier", "Turn the music into MIDI notes"),
    ]
    return [(name, why) for wanted, name, why in rules if wanted]


def suggest(info: MediaInfo, limit: int = 8) -> list[Suggestion]:
    """[(tool name, reason)] worth trying on the file *info*, most specific first."""
    v = info.video if info.video is not None and not info.video.attached_pic else None
    picks = _privacy(info)
    if v is not None and info.duration <= 0.05:  # a still picture
        picks += [
            ("Slideshow Maker", "Turn photos into a video with music"),
            ("Image Watermark", "Use it as a logo on a video"),
        ]
    elif v is not None:
        picks += _for_video(info, v)
    elif info.has_audio:
        picks += _for_audio(info)
    out: list[Suggestion] = []
    for name, why in picks:
        if name in TOOLS and all(n != name for n, _r in out):
            out.append((name, why))
    return out[:limit]


def goal(key: str) -> Goal | None:
    return next((g for g in GOALS if g.key == key), None)
