"""
core/ytdlp.py - One place to build yt-dlp command lines.

Every invocation gets:
  * ``--ignore-config`` so a user's personal yt-dlp config can't silently
    change what the app downloads,
  * the JavaScript runtime YouTube now requires (``--js-runtimes deno:PATH``
    - on Linux yt-dlp does not look next to its own binary),
  * the active FFmpeg (``--ffmpeg-location``) for merging/conversion,
  * the app's network settings (proxy, rate limit, timeouts …).
"""
from __future__ import annotations

import os
from typing import Optional

from core.hardware import find_binary


def ytdlp_path() -> Optional[str]:
    return find_binary("yt-dlp")


def js_runtime_args() -> list:
    deno = find_binary("deno")
    if deno:
        return ["--js-runtimes", f"deno:{deno}"]
    for name in ("node", "bun", "qjs"):
        exe = find_binary(name)
        if exe:
            runtime = {"qjs": "quickjs"}.get(name, name)
            return ["--js-runtimes", f"{runtime}:{exe}"]
    return []


def base_argv(ytdlp: Optional[str] = None, *, network: bool = True) -> list:
    exe = ytdlp or ytdlp_path() or "yt-dlp"
    argv = [exe, "--ignore-config", *js_runtime_args()]
    ff = find_binary("ffmpeg")
    if ff:
        argv += ["--ffmpeg-location", os.path.dirname(ff)]
    if network:
        try:
            from core import network as _net
            argv += list(_net.build_yt_dlp_args())
        except Exception:
            pass
    return argv


def redact(argv: list) -> list:
    """Copy of *argv* safe to show in logs (credentials replaced)."""
    secret = {"--password", "--video-password", "--proxy", "--username", "--ap-password",
              "--cookies", "--client-certificate-password"}
    out, hide = [], False
    for a in argv:
        if hide:
            out.append("•••")
            hide = False
            continue
        out.append(a)
        hide = a in secret
    return out
