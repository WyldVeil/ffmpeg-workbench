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
from typing import Final

from core.tools import SPECS, Category, ToolId, ToolSpec, spec

AIO: Final = spec(ToolId.ALL_IN_ONE_BUILDER).name


#: name -> what the Start page shows and searches (icon, blurb, words), for
#: every tool a person can open (declared in core/tools.py).
TOOLS: Final[Mapping[str, ToolSpec]] = {s.name: s for s in SPECS if s.category is not Category.HIDDEN}


@dataclass(frozen=True)
class Goal:
    key: str
    icon: str
    title: str
    blurb: str
    tools: tuple


GOALS = (
    Goal("cut", "✂", "Cut, trim & join", "Shorten clips, split them, or put several together",
         ("Quick Trimmer", "Batch Joiner", "Crossfader", "The Splicer", "Manual Multi-Splitter",
          "Multi-Clip Sequencer", "Scene Detector", "Silence Remover")),
    Goal("smaller", "📉", "Make a file smaller", "Fit a size limit or save space",
         ("Universal Downsizer", "Codec Cruncher", "Resolution Scaler", "WebM Maker", "Proxy Generator",
          "Compare & Quality")),
    Goal("social", "📱", "Post on social media", "Vertical versions, GIFs, thumbnails, chapters",
         ("The Shortifier", "Smart Reframe", "Pro GIF Maker", "Thumbnail Maker", "Chapter Markers",
          "Auto-Subtitles", "Universal Downsizer")),
    Goal("convert", "🔄", "Convert to another format", "MP4, MKV, MOV, WebM, MP3, GIF...",
         ("Format Converter", "Tracks & Remux", "Audio Converter", "Codec Cruncher", "HDR to SDR",
          "Audio Extractor", "WebM Maker", "Pro GIF Maker")),
    Goal("text", "💬", "Subtitles, titles & text", "Captions from speech, titles, watermarks",
         ("Auto-Subtitles", "Hard-Subber", "Tracks & Remux", "Animated Titles", "Intro/Outro Maker",
          "Watermarker", "Image Watermark", "Karaoke Generator")),
    Goal("sound", "🔊", "Fix the sound", "Too quiet, noisy, out of sync, long pauses",
         ("Loudness Normalizer", "Voice Isolation", "Silence Remover", "Audio Sync Shifter", "Audio Dynamics",
          "The Muter", "Laugh Track Remover", "Waveform Editor")),
    Goal("music", "🎵", "Music, voice & mixing", "Soundtracks, voice-overs, karaoke",
         ("Audio Mixer", "Music Ducker", "Audio Replacer", "TTS Voice-Over", "Voice Changer", "Audio Extractor",
          "Karaoke Generator", "Beat Sync Cutter", "MIDIfier")),
    Goal("look", "🎨", "Colour & look", "Brighter, warmer, cinematic, matching",
         ("Basic Color Corrector", "LUT Applicator", "Colour Match", "Special Effects", "Green Screen Keyer",
          "Video Scopes")),
    Goal("repair", "🩹", "Repair a video", "Shaky, noisy, sideways, washed out, interlaced, black bars",
         ("Deshaker", "Denoise", "Rotate & Flip", "HDR to SDR", "Sharpen", "Deinterlace", "Auto-Cropper",
          "Manual Crop", "Framerate Interpolator", "Resolution Scaler")),
    Goal("motion", "✨", "Effects, speed & motion", "Slow motion, zooms, reverse, freeze, loops",
         ("Speed Ramper", "Framerate Interpolator", "Animated Zoom", "Video Reverser", "Freeze Frame",
          "Clip Looper", "Special Effects", "Transition Studio")),
    Goal("combine", "🧩", "Combine & create", "Collages, picture-in-picture, slideshows, intros",
         ("Video Collage", "Side-by-Side", "Picture-in-Picture", "Slideshow Maker", "Intro/Outro Maker",
          AIO, "Media Generator")),
    Goal("privacy", "🔒", "Privacy", "Remove location and names, blur faces, bleep words",
         ("Metadata Cleaner", "Privacy Blur", "The Muter", "Voice Changer")),
    Goal("get", "⬇", "Download & record", "From YouTube and 1000+ sites, or your screen",
         ("Video Downloader", "Screen Recorder", "Snapshot", "Frame Extractor")),
    Goal("manage", "🧰", "Inspect, batch & automate", "File details, folders of files, job queue",
         ("Media Inspector", "Compare & Quality", "Batch Processor", "Encode Queue", "Preset Manager",
          "Advanced Settings")),
)


# ── Search ────────────────────────────────────────────────────────────────────

#: everyday words -> words the catalogue uses (both spellings, common phrasings)
ALIASES = {
    "color": "colour", "colors": "colour", "colours": "colour", "stabilize": "stabilise", "normalize": "normalise",
    "grey": "gray", "caption": "captions", "subtitle": "subtitles", "subs": "subtitles", "cc": "captions",
    "shrink": "smaller", "compress": "smaller", "reduce": "smaller", "tiny": "smaller", "huge": "big",
    "merge": "join", "combine": "join", "concat": "join", "glue": "join", "stitch": "join",
    "portrait": "vertical", "tiktok": "vertical", "reels": "vertical", "shorts": "vertical",
    "slowmo": "slow", "slo": "slow", "timelapse": "fast", "hyperlapse": "fast", "quieter": "volume",
    "louder": "loud", "boost": "loud", "hiss": "noise", "hum": "noise", "wind": "noise",
    "shaky": "shake", "wobbly": "shake", "steady": "shake", "sideways": "rotate", "upside": "rotate",
    "gps": "location", "exif": "metadata", "rip": "extract", "screenshot": "still", "photo": "still",
    "transcribe": "subtitles", "transcript": "subtitles", "bleep": "mute", "swear": "mute",
}

#: words that say nothing about which tool is wanted
STOPWORDS = {"a", "an", "the", "it", "its", "my", "me", "i", "to", "of", "and", "or", "up", "in", "on", "for",
             "with", "how", "do", "can", "want", "from", "this", "that", "make", "get", "some", "into", "out",
             "video", "videos", "file", "files", "please", "need", "way", "is", "be", "so"}

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str, *, query: bool = False) -> list:
    out = []
    for w in _WORD.findall(text.lower()):
        if query and w in STOPWORDS:
            continue
        out.append(w)
        if query and w in ALIASES:              # only the person's words: tool keywords are already exact
            out.append(ALIASES[w])
        if w.endswith("s") and len(w) > 3:
            out.append(w[:-1])
    return out


def search(text: str, limit: int = 12) -> list:
    """Tool names that match *text*, best first."""
    query = _tokens(text, query=True)
    if not query:
        return []
    goal_words = {}
    leaders = {g.tools[0] for g in GOALS}          # ties go to the tool a goal lists first
    for g in GOALS:
        for name in g.tools:
            goal_words.setdefault(name, []).extend(_tokens(g.title + " " + g.blurb))
    scored = []
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


def describe(info) -> str:
    """One line about a file: 'Video · 3840×2160 HDR · 60 fps · 2:31 · 1.3 GB'."""
    parts = []
    v = info.video if info.has_video and not info.video.attached_pic else None
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


def suggest(info, limit: int = 8) -> list:
    """[(tool name, reason)] worth trying on the file *info*, most specific first."""
    from core import metaclean as mc
    out: list = []

    def add(name: str, reason: str):
        if name in TOOLS and all(n != name for n, _r in out):
            out.append((name, reason))

    v = info.video if info.has_video and not info.video.attached_pic else None
    still = v is not None and info.duration <= 0.05
    found = mc.inspect(info)
    cats = {f.category for f in found}
    if "location" in cats:
        add("Metadata Cleaner", "It contains a GPS location - remove it before sharing")
    elif cats & {"device", "telemetry"}:
        add("Metadata Cleaner", "It names the camera or phone it was made with - remove that before sharing")
    if still:
        add("Slideshow Maker", "Turn photos into a video with music")
        add("Image Watermark", "Use it as a logo on a video")
        return out[:limit]
    if v is not None:
        w, h = v.display_size
        if v.is_hdr:
            add("HDR to SDR", "It's HDR - convert it so it doesn't look washed out on normal screens")
        if info.size > 25_000_000:
            add("Universal Downsizer", f"It's {_size(info.size)} - shrink it to fit Discord, email or WhatsApp")
        n_audio = sum(1 for s in info.streams if s.type == "audio")
        n_subs = sum(1 for s in info.streams if s.type == "subtitle")
        if n_audio > 1 or n_subs:
            what = ", ".join(x for x in (f"{n_audio} audio tracks" if n_audio > 1 else "",
                                          f"{n_subs} subtitle track{'s' if n_subs > 1 else ''}" if n_subs else "") if x)
            add("Tracks & Remux", f"It has {what} - choose which to keep, label them, or add more")
        if (v.field_order or "") in ("tt", "bb", "tb", "bt"):
            add("Deinterlace", "It's interlaced - remove the comb lines")
        if w > h:
            add("The Shortifier", "Make a vertical version for Shorts, TikTok or Reels")
        if max(w, h) < 1280:
            add("Resolution Scaler", f"It's small ({w}×{h}) - upscale it to HD")
        if info.has_audio and info.duration > 20:
            add("Auto-Subtitles", "Turn what's said into subtitles")
        if info.duration > 30:
            add("Quick Trimmer", "Cut the start or end off")
        if info.has_audio and info.duration > 60:
            add("Silence Remover", "Cut out the pauses")
        if info.has_audio:
            add("Loudness Normalizer", "Make the volume even and right for YouTube")
        if info.duration <= 30:
            add("Pro GIF Maker", "Make it an animated GIF")
        add("Snapshot", "Save a frame as a full-quality picture")
        add("Format Converter", "Convert it to another format")
    elif info.has_audio:
        if info.duration > 20:
            add("Auto-Subtitles", "Turn the speech into text or subtitles")
        add("Loudness Normalizer", "Make the volume even")
        add("Voice Isolation", "Remove background noise from a voice")
        if info.duration > 60:
            add("Silence Remover", "Cut out the pauses")
        add("Audio Converter", "Convert it to MP3, FLAC, WAV...")
        add("Waveform Editor", "See the sound and edit parts of it")
        add("MIDIfier", "Turn the music into MIDI notes")
    return out[:limit]


def goal(key: str) -> Goal | None:
    return next((g for g in GOALS if g.key == key), None)
