"""
core/ops/youtube.py - the commands of the Video Downloader (tabs/social/youtube.py):
yt-dlp's (fetch, download, record, subtitles) and FFmpeg's (re-encode after
download, the thumbnail).

Values in, arguments out.  Every yt-dlp command starts with core.ytdlp.base_argv:
the bundled JS runtime, FFmpeg's folder and the app's network route for the URL.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

from core import APP_NAME
from core import ffmpeg as ff
from core.filters import subtitles_filter
from core.ytdlp import base_argv

VIDEO_CODECS = ["libx264", "libx265", "libsvtav1", "libvpx-vp9", "prores_ks", "copy"]


# ── yt-dlp ───────────────────────────────────────────────────────────────────


def check_route(ytdlp: str, url: str) -> None:
    """Raise OSError (a sentence) now if *url* can't be reached the way the app is set up."""
    base_argv(ytdlp, url=url)


BEST = "bv*+ba/b"  # the best video with the best sound (or the best single file)

_H264 = "[vcodec~='^(avc|h264)']"
_AAC = "[acodec~='^(mp4a|aac)']"


def share_format(max_height: int = 0) -> str:
    """The default download: H.264 video with AAC sound, the pair every site,
    phone and editor takes.  YouTube's best is usually AV1 or VP9 with Opus,
    which X/Twitter (and older phones and TVs) refuse.  A site without H.264
    gets its best instead.  *max_height*: no taller than this (0: any)."""
    h = f"[height<={max_height}]" if max_height else ""
    return "/".join(
        (
            f"bv*{_H264}{h}+ba{_AAC}",
            f"b{_H264}{_AAC}{h}",
            f"bv*{_H264}{h}+ba",
            f"bv*{h}+ba",
            f"b{h}",
        )
    )


SHARE = share_format()


def with_audio(format_id: str, vcodec: str) -> str:
    """Video-only format *format_id* with sound added: AAC next to H.264 (so
    the file stays shareable), else the best sound."""
    if vcodec.startswith(("avc", "h264")):
        return f"{format_id}+ba{_AAC}/{format_id}+ba/b"
    return f"{format_id}+ba/b"


# ── Links ────────────────────────────────────────────────────────────────────

#: the sites a pasted link is recognised from (yt-dlp reads 1000+; these are
#: the ones people paste most)
VIDEO_SITES = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "vimeo.com",
    "twitch.tv",
    "twitter.com",
    "x.com",
    "tiktok.com",
)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _on(host: str, site: str) -> bool:
    return host == site or host.endswith("." + site)


def site_link(text: str) -> str | None:
    """*text* if it is a link to a page on one of :data:`VIDEO_SITES` (the
    https:// added when it was left off), else None."""
    s = text.strip()
    if not s or any(c.isspace() for c in s):
        return None
    if "://" not in s:
        s = "https://" + s
    try:
        parts = urlsplit(s)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or len(parts.path.strip("/")) == 0:
        return None
    return s if any(_on(host, site) for site in VIDEO_SITES) else None


def is_one_video(url: str) -> bool:
    """Does *url* point at one video (not a playlist, channel or profile, which
    would download many, nor a live channel)?"""
    parts = urlsplit(url)
    host, path = _host(url), parts.path
    if _on(host, "youtu.be"):
        return bool(path.strip("/"))
    if _on(host, "youtube.com") or _on(host, "youtube-nocookie.com"):
        if path.rstrip("/") == "/watch":
            return bool(parse_qs(parts.query).get("v"))
        return path.startswith(("/shorts/", "/live/", "/embed/", "/v/"))
    if _on(host, "twitter.com") or _on(host, "x.com"):
        return "/status/" in path
    if _on(host, "tiktok.com"):
        return "/video/" in path or host in ("vm.tiktok.com", "vt.tiktok.com")
    if _on(host, "vimeo.com"):
        return path.strip("/").split("/")[-1].isdigit()
    if _on(host, "twitch.tv"):
        return "/videos/" in path or "/clip/" in path or host == "clips.twitch.tv"
    return False


def playlist_arg(playlist: bool) -> str:
    """The whole playlist, or just the one video the link points at."""
    return "--yes-playlist" if playlist else "--no-playlist"


def output_args(out: str, url: str) -> list[str]:
    """Where to save (*out*: a path or an output template), then what."""
    return ["-o", out, url]


def stream_url_argv(ytdlp: str, url: str, *, playlist: bool) -> list[str]:
    """Ask for a playable stream URL (up to 720p), for the preview."""
    fmt = "best[height<=720][ext=mp4]/best[height<=720]/best"
    return [*base_argv(ytdlp, url=url), playlist_arg(playlist), "-g", "-f", fmt, url]


def live_record_argv(ytdlp: str, url: str, out_template: str) -> list[str]:
    return [*base_argv(ytdlp, url=url), "-f", "best", "--live-from-start", "-o", out_template, url]


def title_argv(ytdlp: str, url: str) -> list[str]:
    return [*base_argv(ytdlp, url=url), "--no-playlist", "--get-title", url]


def download_argv(ytdlp: str, url: str, fmt: str, container: str, *, playlist: bool) -> list[str]:
    """The download (format *fmt*, merged into *container*), without the output and URL."""
    return [
        *base_argv(ytdlp, url=url),
        playlist_arg(playlist),
        "-f",
        fmt,
        "--merge-output-format",
        container,
        "--no-overwrites",
    ]


def audio_argv(
    ytdlp: str,
    url: str,
    audio_format: str,
    quality: str,
    *,
    playlist: bool,
    thumbnail: bool = False,
    metadata: bool = False,
) -> list[str]:
    """Sound only, converted to *audio_format* (cover picture and tags embedded
    when asked), without the output and URL."""
    head = [
        *base_argv(ytdlp, url=url),
        playlist_arg(playlist),
        "-x",
        "--audio-format",
        audio_format,
    ]
    extras = ["--embed-thumbnail"] * thumbnail + ["--add-metadata"] * metadata
    return [*head, "--audio-quality", quality, "--no-overwrites", *extras]


def subtitle_args(lang: str, fmt: str, *, auto: bool, embed: bool) -> list[str]:
    args = ["--write-sub", "--sub-lang", lang, "--sub-format", fmt]
    args += ["--write-auto-sub"] if auto else []
    return args + (["--embed-subs"] if embed else [])


def subtitles_only_argv(
    ytdlp: str, url: str, lang: str, fmt: str, out_base: str, *, playlist: bool, auto: bool = False
) -> list[str]:
    """Only the subtitles (the automatic ones too when *auto*), to *out_base*.<lang>.<fmt>."""
    head = [*base_argv(ytdlp, url=""), playlist_arg(playlist), "--write-sub", "--skip-download"]
    head += ["--write-auto-sub"] if auto else []
    return [*head, "--sub-lang", lang, "--sub-format", fmt, *output_args(out_base, url)]


def section_args(start: float, end: float) -> list[str]:
    """Only this part of the video (cut exactly)."""
    return ["--download-sections", f"*{start:.3f}-{end:.3f}", "--force-keyframes-at-cuts"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Options:
    """The tab's own yt-dlp options (empty: not used)."""

    cookies: str = ""
    username: str = ""
    password: str = ""
    rate_limit: str = ""
    proxy: str = ""
    aria2c: bool = False
    chapters: bool = False
    metadata: bool = False
    sponsorblock: tuple[str, ...] = ()
    sponsorblock_mark: bool = True  # mark the segments as chapters (else cut them)
    custom: str = ""  # typed by the person, shell-style


def common_args(o: Options) -> list[str]:
    import shlex

    args: list[str] = []
    if o.cookies and os.path.isfile(o.cookies):
        args += ["--cookies", o.cookies]
    args += ["--username", o.username] if o.username else []
    args += ["--password", o.password] if o.password else []
    args += ["--rate-limit", o.rate_limit] if o.rate_limit else []
    args += ["--proxy", o.proxy] if o.proxy else []
    if o.aria2c:
        args += ["--downloader", "aria2c", "--downloader-args", "aria2c:-x 16 -s 16 -k 1M"]
    args += ["--embed-chapters"] if o.chapters else []
    args += ["--embed-metadata"] if o.metadata else []
    if o.sponsorblock:
        action = "--sponsorblock-mark" if o.sponsorblock_mark else "--sponsorblock-remove"
        args += [action, ",".join(o.sponsorblock)]
    return args + (shlex.split(o.custom) if o.custom else [])


# ── FFmpeg ───────────────────────────────────────────────────────────────────


def thumbnail_save_cmd(path: str) -> list[str]:
    """The thumbnail piped in, saved as JPEG or PNG by *path*'s extension."""
    jpeg = path.lower().endswith((".jpg", ".jpeg"))
    codec = ["-c:v", "mjpeg", "-q:v", "2"] if jpeg else ["-c:v", "png"]
    head = [ff.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0"]
    return [*head, "-frames:v", "1", *codec, "-update", "1", path]


def thumbnail_preview_cmd(w: int, h: int) -> list[str]:
    """The thumbnail piped in, fitted into w x h (black bars), out as PNG."""
    fit = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:-1:-1:color=black"
    head = [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
    ]
    return [*head, "-vf", fit, "-f", "image2pipe", "-c:v", "png", "pipe:1"]


def video_codec_args(vcodec: str, crf: int, preset: str, container: str) -> list[str]:
    """Re-encoding after download: the codec, its quality, playable 8-bit pixels."""
    by_codec = {
        "copy": ["-c:v", "copy"],
        "prores_ks": ["-c:v", "prores_ks", "-profile:v", "3"],
        "libsvtav1": ["-c:v", "libsvtav1", "-preset", "4", "-crf", str(crf)],
        "libvpx-vp9": [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            str(crf),
            "-b:v",
            "0",
            "-deadline",
            "good",
            "-cpu-used",
            "2",
        ],
    }
    args = by_codec.get(vcodec, ["-c:v", vcodec, "-crf", str(crf), "-preset", preset])
    if vcodec in ("libx264", "libx265", "libsvtav1", "libvpx-vp9"):
        args = [*args, "-pix_fmt", "yuv420p"]  # 10-bit sources -> playable 8-bit
    if vcodec == "libx265" and container.lower() in ("mp4", "mov", "m4v"):
        args = [*args, "-tag:v", "hvc1"]
    return args


class Resolution(StrEnum):
    ORIGINAL = "original"
    R2160 = "3840x2160"
    R1440 = "2560x1440"
    R1080 = "1920x1080"
    R720 = "1280x720"
    R480 = "854x480"
    R360 = "640x360"
    CUSTOM = "custom"


def scale_filter(res: Resolution, custom: str) -> str | None:
    """The scale filter for a resolution choice (*custom*: the typed size)."""
    if res is Resolution.ORIGINAL:
        return None
    if res is Resolution.CUSTOM:
        parts = custom.strip().replace("×", "x").split("x")  # 1280x720 or 1280×720
        return f"scale={parts[0].strip()}:{parts[1].strip()}" if len(parts) == 2 else None
    parts = res.value.split("x")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"scale={parts[0]}:{parts[1]}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
    return None


def subtitle_filter(sub_path: str) -> str | None:
    if not sub_path or not os.path.isfile(sub_path):
        return None
    return subtitles_filter(sub_path)


def audio_args(acodec: str, bitrate: str, sample_rate: str) -> list[str]:
    if acodec == "copy":
        return ["-c:a", "copy"]
    args = ["-c:a", acodec]
    args += ["-b:a", bitrate] if acodec in ("aac", "libmp3lame", "libopus", "libvorbis") else []
    return args + (["-ar", sample_rate] if acodec != "flac" else [])


@dataclass(frozen=True, slots=True, kw_only=True)
class Encode:
    """Re-encoding after the download."""

    vcodec: str
    crf: int
    preset: str
    container: str
    resolution: Resolution
    custom_size: str
    acodec: str
    abitrate: str
    sample_rate: str


def encode_cmd(inp: str, out: str, e: Encode, sub_path: str = "") -> list[str]:
    """The downloaded file re-encoded to *out* (subtitles burned in when *sub_path* is given)."""
    cmd = [ff.ffmpeg_path(), "-i", inp, *video_codec_args(e.vcodec, e.crf, e.preset, e.container)]
    filters = [
        f for f in (scale_filter(e.resolution, e.custom_size), subtitle_filter(sub_path)) if f
    ]
    cmd += ["-vf", ",".join(filters)] if filters else []
    cmd += audio_args(e.acodec, e.abitrate, e.sample_rate)
    cmd += ["-movflags", "+faststart"] if e.container == "mp4" else []
    return [*cmd, out, "-y"]


def stream_preview_cmd(stream_url: str) -> list[str]:
    """Play a resolved stream in a player window (closes at the end)."""
    return [
        ff.ffplay_path(),
        "-autoexit",
        "-window_title",
        f"Video Preview - {APP_NAME}",
        stream_url,
    ]


def with_proxy(cmd: Sequence[str], proxy: str) -> list[str]:
    """*cmd* going through *proxy* instead (its last --proxy changed)."""
    out = list(cmd)
    idx = max(i for i, a in enumerate(out[:-1]) if a == "--proxy")
    out[idx + 1] = proxy
    return out
