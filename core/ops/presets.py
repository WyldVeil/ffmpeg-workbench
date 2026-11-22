"""
core/ops/presets.py - FFmpeg command presets (tabs/transcoder/presets.py, and the
Encode Queue's extra arguments): the built-in ones, the user's saved ones, and
turning a template ("-i {INPUT} ... {OUTPUT}") into a command - no shell involved.
"""

from __future__ import annotations

import json
import os
import shlex
import time
from collections.abc import Sequence
from typing import Any, cast

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.paths import config_dir

Preset = dict[str, Any]  # {"name", "category", "desc", "cmd", "ext"}

SHELL_TOKENS = {"&&", "||", "|", ";", "&", ">", ">>", "<", "2>", "2>&1", "1>", "&>"}


def presets_file() -> str:
    return os.path.join(config_dir(), "encode_presets.json")


# Built-in starter presets ("ext" = default output type)
BUILT_IN: list[Preset] = [
    {
        "name": t("presets.instagram_reel_1080_1920_h_264"),
        "category": "Social",
        "description": t("presets.vertical_9_16_for_instagram_reels_and_tiktok"),
        "cmd": '-i {INPUT} -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1" -c:v libx264 -crf 20 -preset fast -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart {OUTPUT}',
        "ext": ".mp4",
    },
    {
        "name": t("presets.youtube_upload_1080p_h_264_high_quality"),
        "category": "Web",
        "description": "Optimised for YouTube's recommended settings.",
        "cmd": '-i {INPUT} -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1" -c:v libx264 -crf 16 -preset slow -pix_fmt yuv420p -c:a aac -b:a 320k -movflags +faststart {OUTPUT}',
        "ext": ".mp4",
    },
    {
        "name": t("presets.quick_compress_h_265_crf_28"),
        "category": "Compression",
        "description": t("presets.shrink_file_size_fast_half_the_size_of_h_264_at"),
        "cmd": "-i {INPUT} -c:v libx265 -crf 28 -preset fast -tag:v hvc1 -c:a aac -b:a 128k -movflags +faststart {OUTPUT}",
        "ext": ".mp4",
    },
    {
        "name": t("presets.archive_master_lossless_h_265"),
        "category": "Archive",
        "description": t("presets.lossless_encode_for_long_term_archival_storage"),
        "cmd": "-i {INPUT} -map 0 -c:v libx265 -x265-params lossless=1:log-level=error -c:a flac -c:s copy {OUTPUT}",
        "ext": ".mkv",
    },
    {
        "name": t("presets.streaming_proxy_low_bitrate_h_264"),
        "category": "Proxy",
        "description": t("presets.fast_tiny_proxy_for_editing_on_slow_machines"),
        "cmd": '-i {INPUT} -vf "scale=trunc(iw/8)*2:trunc(ih/8)*2" -c:v libx264 -crf 28 -preset ultrafast -pix_fmt yuv420p -c:a aac -b:a 96k {OUTPUT}',
        "ext": ".mp4",
    },
    {
        "name": t("presets.audio_only_hq_aac"),
        "category": "Audio",
        "description": t("presets.strip_video_keep_audio_as_high_quality_aac"),
        "cmd": "-i {INPUT} -vn -c:a aac -b:a 320k {OUTPUT}",
        "ext": ".m4a",
    },
    {
        "name": "WebM for the web (VP9, constant quality)",
        "category": "Web",
        "description": t("presets.efficient_vp9_webm_for_web_embedding"),
        "cmd": "-i {INPUT} -c:v libvpx-vp9 -crf 32 -b:v 0 -row-mt 1 -deadline good -cpu-used 2 -c:a libopus -b:a 128k {OUTPUT}",
        "ext": ".webm",
    },
    {
        "name": t("presets.4k_downscale_to_1080p_lanczos"),
        "category": "Compression",
        "description": t("presets.downscale_4k_footage_to_1080p_with_best_quality"),
        "cmd": '-i {INPUT} -vf "scale=-2:1080:flags=lanczos" -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a copy {OUTPUT}',
        "ext": ".mp4",
    },
]
SNIPPETS = [  # (label, options) the editor's quick-insert buttons add
    ("H.264", "-c:v libx264 -crf 18 -preset fast"),
    ("H.265", "-c:v libx265 -crf 22 -preset fast"),
    ("Scale 1080p", '-vf "scale=-2:1080,setsar=1" -pix_fmt yuv420p'),
    ("AAC audio", "-c:a aac -b:a 192k"),
    ("Copy all", "-map 0 -c copy"),
    ("No audio", "-an"),
]


def split_args(template: str) -> list[str]:
    """Split like a shell would (quotes group words) but keep backslashes,
    so Windows paths survive."""
    lex = shlex.shlex(template, posix=True)
    lex.whitespace_split = True
    lex.escape = ""
    lex.commenters = ""
    return list(lex)


def check_template(template: str) -> list[str]:
    """Arguments of *template*; ValueError if it isn't a plain FFmpeg option list."""
    try:
        args = split_args(template.replace("\n", " ").replace("\r", " "))
    except ValueError as exc:
        raise ValueError(f"The command has an unclosed quote ({exc}).") from None
    if args and os.path.splitext(os.path.basename(args[0]))[0].lower() == "ffmpeg":
        args = args[1:]  # a pasted "ffmpeg ..." line
    if any(
        a in SHELL_TOKENS or a[:1] in ("|", ">", "<") or a.endswith(("&&", "||", ";")) for a in args
    ):
        raise ValueError(
            "A preset holds FFmpeg options only - shell operators such as '&&', '|' or "
            "'>' are not allowed."
        )
    joined = " ".join(args)
    if "{INPUT}" not in joined:
        raise ValueError("The command needs {INPUT} where the source file goes.")
    if args.count("{OUTPUT}") != 1:
        raise ValueError("The command needs {OUTPUT} once, as its own word, where the result goes.")
    return args


def build_command(template: str, src: str, out: str) -> list[str]:
    """Final argument list - no shell involved."""
    final: list[str] = []
    for a in check_template(template):
        if a == "{INPUT}":
            final.append(src)
        elif a == "{OUTPUT}":
            final.append(out)
        elif "{INPUT}" in a:  # e.g. subtitles={INPUT} inside a filter
            final.append(a.replace("{INPUT}", fl.filter_path(src)))
        elif a not in ("-y", "-n"):
            final.append(a)
    return [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *final]


def load_presets() -> tuple[list[Preset], str]:
    """(presets, warning).  A file that can't be read is set aside, never overwritten."""
    presets: list[Preset] = list(BUILT_IN)
    path = presets_file()
    if not os.path.exists(path):
        return presets, ""
    try:
        with open(path, encoding="utf-8") as f:
            custom: object = json.load(f)
        if not isinstance(custom, list):
            raise ValueError("it is not a list of presets")
    except (OSError, ValueError) as exc:
        backup = f"{path}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(path, backup)
        except OSError:
            backup = ""
        return presets, (
            f"Your saved presets could not be read ({exc})."
            + (f"\nThe file was kept as:\n{backup}" if backup else "")
        )
    bi_names = {p["name"] for p in BUILT_IN}
    for p in cast("list[object]", custom):
        if isinstance(p, dict):
            entry = cast("Preset", p)
            name, cmd = entry.get("name"), entry.get("cmd")
            if isinstance(name, str) and isinstance(cmd, str) and name not in bi_names:
                presets.append(entry)
    return presets, ""


def save_user_presets(presets: Sequence[Preset]) -> str:
    """Write the user's presets (atomically).  Returns "" or an error message."""
    bi_names = {p["name"] for p in BUILT_IN}
    user_only = [p for p in presets if p.get("name") not in bi_names]
    path = presets_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(user_only, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return ""
    except OSError as exc:
        return str(exc)
