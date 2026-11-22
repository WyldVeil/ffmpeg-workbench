"""
core/remux.py  -  Tracks & Remux: change what's in a file without re-encoding

``tracks_of(info)`` lists a file's tracks (video, audio, subtitles, attached
fonts/pictures, data); the caller keeps, drops, reorders and labels them
(language, title, default/forced), adds tracks from other files (with a
delay), then ``remux_command(...)`` copies everything into the chosen
container.  Nothing is re-encoded except text subtitles going into MP4/MOV
(MP4's own subtitle format) or WebM (WebVTT).

``problems(...)`` explains, before anything runs, what a container can't
hold (picture-based subtitles in MP4, fonts outside MKV, PCM audio in MP4...)
- those tracks are left out rather than making FFmpeg fail.

``extract_command(...)`` saves a single track as its own file, in the format
that track already has (.srt, .ass, .sup, .m4a, .flac, .ac3...).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from core import ffmpeg as ff

TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
BITMAP_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}

#: what each container can carry as-is (None = anything)
VIDEO_OK: dict[str, set[str] | None] = {
    "mp4": {"h264", "hevc", "av1", "vp9", "mpeg4", "mpeg2video", "mjpeg"},
    "mov": {"h264", "hevc", "prores", "dnxhd", "mjpeg", "mpeg4", "mpeg2video", "av1"},
    "webm": {"vp8", "vp9", "av1"},
    "mkv": None,
}
AUDIO_OK: dict[str, set[str] | None] = {
    "mp4": {"aac", "mp3", "ac3", "eac3", "alac", "opus", "flac", "mp2"},
    "mov": {
        "aac",
        "mp3",
        "ac3",
        "eac3",
        "alac",
        "pcm_s16le",
        "pcm_s24le",
        "pcm_s32le",
        "pcm_s16be",
        "pcm_s24be",
        "pcm_f32le",
    },
    "webm": {"opus", "vorbis"},
    "mkv": None,
}

LANGUAGES = [
    ("", "(not set)"),
    ("eng", "English"),
    ("spa", "Spanish"),
    ("fra", "French"),
    ("deu", "German"),
    ("ita", "Italian"),
    ("por", "Portuguese"),
    ("nld", "Dutch"),
    ("pol", "Polish"),
    ("rus", "Russian"),
    ("ukr", "Ukrainian"),
    ("tur", "Turkish"),
    ("ara", "Arabic"),
    ("heb", "Hebrew"),
    ("fas", "Persian"),
    ("hin", "Hindi"),
    ("ben", "Bengali"),
    ("urd", "Urdu"),
    ("jpn", "Japanese"),
    ("kor", "Korean"),
    ("zho", "Chinese"),
    ("vie", "Vietnamese"),
    ("tha", "Thai"),
    ("ind", "Indonesian"),
    ("msa", "Malay"),
    ("tgl", "Tagalog"),
    ("swe", "Swedish"),
    ("nor", "Norwegian"),
    ("dan", "Danish"),
    ("fin", "Finnish"),
    ("ell", "Greek"),
    ("ces", "Czech"),
    ("hun", "Hungarian"),
    ("ron", "Romanian"),
    ("und", "Undetermined"),
]
LANGUAGE_NAMES = dict(LANGUAGES)

#: extension for a track saved on its own, by codec
EXTRACT_EXT = {
    "subrip": ".srt",
    "srt": ".srt",
    "ass": ".ass",
    "ssa": ".ass",
    "webvtt": ".vtt",
    "mov_text": ".srt",
    "hdmv_pgs_subtitle": ".sup",
    "dvd_subtitle": ".mks",
    "dvb_subtitle": ".mks",
    "aac": ".m4a",
    "alac": ".m4a",
    "mp3": ".mp3",
    "ac3": ".ac3",
    "eac3": ".eac3",
    "flac": ".flac",
    "opus": ".opus",
    "vorbis": ".ogg",
    "dts": ".dts",
    "truehd": ".thd",
    "mp2": ".mp2",
    "h264": ".mkv",
    "hevc": ".mkv",
    "av1": ".mkv",
    "vp9": ".webm",
    "vp8": ".webm",
    "prores": ".mov",
    "mjpeg": ".jpg",
    "png": ".png",
}


@dataclass
class Track:
    source: int  # input number (0 = the main file)
    index: int  # stream index inside that input
    kind: str  # video | audio | subtitle | attachment | data | cover
    codec: str
    details: str = ""
    language: str = ""
    title: str = ""
    default: bool = False
    forced: bool = False
    keep: bool = True
    delay: float = 0.0  # added files: start this many seconds later
    path: str = ""  # added files: where the track comes from

    def describe(self) -> str:
        lang = LANGUAGE_NAMES.get(self.language, self.language) if self.language else ""
        bits = [self.kind.capitalize(), self.codec.upper() if len(self.codec) <= 5 else self.codec]
        if self.details:
            bits.append(self.details)
        if lang and self.language != "und":
            bits.append(lang)
        return " · ".join(bits)


def _kind(s: ff.Stream) -> str:
    if s.attached_pic:
        return "cover"
    return s.type if s.type in ("video", "audio", "subtitle", "attachment") else "data"


def tracks_of(info: ff.MediaInfo, source: int = 0, path: str = "") -> list[Track]:
    out: list[Track] = []
    for s in info.streams:
        kind = _kind(s)
        tags = s.tags or {}
        if kind == "video":
            details = (
                f"{s.width}×{s.height}"
                + (f" {s.fps:.3g} fps" if s.fps else "")
                + (" HDR" if s.is_hdr else "")
            )
        elif kind == "audio":
            layout = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(
                s.channels, f"{s.channels} ch"
            )
            details = f"{layout}, {s.sample_rate // 1000 if s.sample_rate else '?'} kHz"
        elif kind == "attachment":
            details = tags.get("filename", "") or tags.get("mimetype", "")
        elif kind == "cover":
            details = f"{s.width}×{s.height}"
        else:
            details = ""
        disp = ff.json_object(s.raw.get("disposition"))
        codec = s.codec if s.codec and s.codec != "unknown" else ""
        if kind == "attachment" and not codec:
            codec = os.path.splitext(tags.get("filename", ""))[1].lstrip(".").lower() or "file"
        tag = str(s.raw.get("codec_tag_string") or "")
        codec = codec or (tag if tag and not tag.startswith("[") else "data")
        out.append(
            Track(
                source,
                s.index,
                kind,
                codec,
                details,
                language=str(tags.get("language", "") or ""),
                title=str(tags.get("title", "") or ""),
                default=bool(disp.get("default")),
                forced=bool(disp.get("forced")),
                path=path,
            )
        )
    return out


def problems(tracks: Sequence[Track], container: str) -> list[tuple[Track, str]]:
    """[(track, sentence)] for tracks the container can't hold (they'll be left
    out) or will change (text subtitles converted)."""
    out: list[tuple[Track, str]] = []
    if container not in VIDEO_OK:
        return out
    video_ok, audio_ok = VIDEO_OK[container], AUDIO_OK[container]
    if video_ok is None or audio_ok is None:  # MKV holds anything
        return out
    for t in tracks:
        if not t.keep:
            continue
        if t.kind == "video" and t.codec not in video_ok:
            out.append((t, f"{t.codec} video can't go into {container.upper()} - use MKV"))
        elif t.kind == "audio" and t.codec not in audio_ok:
            out.append((t, f"{t.codec} audio can't go into {container.upper()} - use MKV"))
        elif t.kind == "subtitle":
            if t.codec in BITMAP_SUBS:
                out.append(
                    (
                        t,
                        f"picture-based subtitles ({t.codec}) can't go into {container.upper()} - "
                        "they'll be left out (use MKV to keep them)",
                    )
                )
            elif container == "webm" and t.codec != "webvtt":
                out.append((t, "text subtitles will be converted to WebVTT (styling is lost)"))
            elif container in ("mp4", "mov") and t.codec != "mov_text":
                out.append(
                    (
                        t,
                        f"{t.codec} subtitles will be converted to MP4 text subtitles (styling is lost)",
                    )
                )
        elif t.kind == "attachment":
            out.append(
                (
                    t,
                    f"attached files (fonts) can only go into MKV - '{t.details or 'attachment'}' "
                    "will be left out",
                )
            )
        elif t.kind == "data" and container != "mov":
            out.append((t, f"data track ({t.codec}) will be left out"))
        elif t.kind == "cover" and container == "webm":
            out.append((t, "cover pictures can't go into WebM - left out"))
    return out


def _droppable(t: Track, container: str) -> bool:
    """Tracks the container can't hold at all (left out instead of failing)."""
    return any("left out" in why or "can't go into" in why for _t, why in problems([t], container))


def remux_command(
    inputs: Sequence[str],
    tracks: Sequence[Track],
    out: str,
    *,
    keep_chapters: bool = True,
    title: str | None = None,
    keep_tags: bool = True,
) -> tuple[list[str], list[str]]:
    """(argv, [notes]) copying *tracks* (in order) from *inputs* into *out*."""
    container = os.path.splitext(out)[1].lstrip(".").lower()
    container = {"m4v": "mp4", "mka": "mkv", "mks": "mkv"}.get(container, container)
    argv = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]
    delays = {t.source: t.delay for t in tracks if t.source > 0}
    for n, path in enumerate(inputs):
        if n > 0 and abs(delays.get(n, 0.0)) > 1e-6:
            argv += ["-itsoffset", f"{delays[n]:.3f}"]
        argv += ["-i", path]
    notes: list[str] = []
    kept: list[Track] = []
    for t in tracks:
        if not t.keep:
            continue
        if _droppable(t, container if container in VIDEO_OK else "mkv"):
            notes.append(f"Left out: {t.describe()}")
            continue
        kept.append(t)
    if not any(t.kind in ("video", "audio") for t in kept):
        raise ValueError("Keep at least one video or audio track.")
    for t in kept:
        argv += ["-map", f"{t.source}:{t.index}"]
    argv += ["-c", "copy"]
    argv += [
        "-map_metadata",
        "0" if keep_tags else "-1",
        "-map_chapters",
        "0" if keep_chapters else "-1",
    ]
    if title is not None:
        argv += ["-metadata", f"title={title}"]
    for n, t in enumerate(kept):
        if t.kind == "subtitle" and t.codec in TEXT_SUBS:
            if container in ("mp4", "mov") and t.codec != "mov_text":
                argv += [f"-c:{n}", "mov_text"]
            elif container == "webm" and t.codec != "webvtt":
                argv += [f"-c:{n}", "webvtt"]
            elif container == "mkv" and t.codec == "mov_text":
                argv += [f"-c:{n}", "srt"]
        if t.kind == "video" and t.codec == "hevc" and container in ("mp4", "mov"):
            argv += [f"-tag:{n}", "hvc1"]  # Apple players need this tag for HEVC
        if t.kind in ("audio", "subtitle", "video"):
            argv += [
                f"-metadata:s:{n}",
                f"language={t.language or 'und'}",
                f"-metadata:s:{n}",
                f"title={t.title}",
            ]
            flags = [f for f, on in (("default", t.default), ("forced", t.forced)) if on]
            argv += [f"-disposition:{n}", "+".join(flags) if flags else "0"]
        elif t.kind == "cover":
            argv += [f"-disposition:{n}", "attached_pic"]
    if container in ("mp4", "mov"):
        argv += ["-movflags", "+faststart"]
    return [*argv, out], notes


def extract_command(path: str, track: Track, out: str) -> list[str]:
    """Save one track on its own (copied; MP4 text subtitles become SRT)."""
    argv = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]
    if track.kind == "attachment":
        return [*argv, f"-dump_attachment:{track.index}", out, "-i", path]
    argv += ["-i", path, "-map", f"0:{track.index}"]
    if track.kind == "cover":
        return [*argv, "-c", "copy", "-frames:v", "1", out]
    if track.codec == "mov_text":
        return [*argv, "-c", "srt", out]
    ext = os.path.splitext(out)[1].lower()
    if ext == ".wav":
        return [*argv, "-c", "pcm_s24le" if "24" in track.codec else "pcm_s16le", out]
    return [*argv, "-c", "copy", out]


def extract_ext(track: Track) -> str:
    if track.kind == "attachment":
        return os.path.splitext(track.details)[1] or ".bin"
    if track.codec.startswith("pcm_"):
        return ".wav"
    return EXTRACT_EXT.get(track.codec, ".mkv" if track.kind != "audio" else ".mka")


@dataclass
class Plan:
    """What the Tracks tool holds: the files and the track list in output order."""

    inputs: list[str] = field(default_factory=list[str])
    tracks: list[Track] = field(default_factory=list[Track])

    def load(self, path: str) -> None:
        info = ff.probe(path)
        self.inputs = [path]
        self.tracks = tracks_of(info, 0, path)

    def add_file(self, path: str, delay: float = 0.0, language: str = "") -> list[Track]:
        info = ff.probe(path)
        n = len(self.inputs)
        self.inputs.append(path)
        added = [t for t in tracks_of(info, n, path) if t.kind in ("audio", "subtitle")]
        if not added:
            self.inputs.pop()
            raise ValueError(f"{os.path.basename(path)} has no audio or subtitle tracks.")
        for t in added:
            t.delay = delay
            t.default = False
            if language:
                t.language = language
        self.tracks.extend(added)
        return added

    def move(self, i: int, step: int) -> int:
        j = i + step
        if 0 <= i < len(self.tracks) and 0 <= j < len(self.tracks):
            self.tracks[i], self.tracks[j] = self.tracks[j], self.tracks[i]
            return j
        return i

    def set_default(self, i: int) -> None:
        """Make track *i* the default of its kind (only one default per kind)."""
        kind = self.tracks[i].kind
        for n, t in enumerate(self.tracks):
            if t.kind == kind:
                t.default = n == i
