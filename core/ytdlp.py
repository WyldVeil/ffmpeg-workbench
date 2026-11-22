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
from collections.abc import Sequence

from core.hardware import find_binary


def ytdlp_path() -> str | None:
    return find_binary("yt-dlp")


def js_runtime_args() -> list[str]:
    deno = find_binary("deno")
    if deno:
        return ["--js-runtimes", f"deno:{deno}"]
    for name in ("node", "bun", "qjs"):
        exe = find_binary(name)
        if exe:
            runtime = {"qjs": "quickjs"}.get(name, name)
            return ["--js-runtimes", f"{runtime}:{exe}"]
    return []


def base_argv(
    ytdlp: str | None = None, *, network: bool = True, url: str = "https://www.youtube.com/"
) -> list[str]:
    """yt-dlp command prefix.  Raises OSError (offline mode, strict proxy not
    set up...) instead of quietly connecting some other way."""
    exe = ytdlp or ytdlp_path() or "yt-dlp"
    argv = [exe, "--ignore-config", *js_runtime_args()]
    ff = find_binary("ffmpeg")
    if ff:
        argv += ["--ffmpeg-location", os.path.dirname(ff)]
    if not network:
        return argv
    from core import network as _net
    from core import tails

    if tails.direct_enabled():
        # Tails: the user opened the direct route for this session - run yt-dlp
        # outside Tor the way Tails runs its Unsafe Browser.
        return tails.wrap([*argv, "--proxy", ""])
    argv += list(_net.build_yt_dlp_args(url=url))
    return _net.wrapper_prefix() + argv


def redact(argv: Sequence[str]) -> list[str]:
    """Copy of *argv* safe to show in logs (credentials replaced)."""
    secret = {
        "--password",
        "--video-password",
        "--proxy",
        "--username",
        "--ap-password",
        "--cookies",
        "--client-certificate-password",
    }
    out: list[str] = []
    hide = False
    for a in argv:
        if hide:
            out.append("•••")
            hide = False
            continue
        out.append(a)
        hide = a in secret
    return out
