"""
core/updater.py - Verified, atomic updates of the external tools.

Updates are only ever written to the per-user tools folder
(``core.hardware.user_tools_dir()``), which the binary resolver searches
first - never into the program folder (often read-only) and never over a
system package's FFmpeg.  Every download is checked against the publisher's
SHA-256 list, over verified TLS regardless of the user's "verify SSL"
setting, and only replaces the previous version after the new one has run
successfully.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import sys
from collections.abc import Callable
from http.client import HTTPResponse
from typing import Any

from core import proc as _proc
from core import settings
from core.hardware import platform_tag, user_tools_dir

logger = logging.getLogger("crossfader.updater")
Log = Callable[[str], None]

_BTBN_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=8"
_YTDLP_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_VARIANT = {"windows-x64": "win64-gpl-shared", "linux-x64": "linux64-gpl-shared"}
_YTDLP_ASSET = {"windows-x64": "yt-dlp.exe", "linux-x64": "yt-dlp_linux"}


def offline() -> bool:
    """Offline mode (Settings → Network) or the test switch."""
    if os.environ.get("CROSSFADER_NO_NETWORK") == "1":
        return True
    try:
        from core import network

        return network.is_offline()
    except Exception:
        logger.debug("offline: best-effort step failed", exc_info=True)
        return False


def _checks_enabled() -> bool:
    """Automatic update checks: not offline and not switched off."""
    if offline():
        return False
    return settings.CHECK_UPDATES.get()


def open_verified(url: str, timeout: float = 30.0) -> HTTPResponse:
    """urlopen honouring proxy settings but ALWAYS verifying TLS."""
    import urllib.request

    try:
        from core import network

        cfg = dict(network.get())
        cfg["network.verify_ssl"] = True
        return network.urlopen(
            url, timeout=timeout, cfg=cfg, headers={"User-Agent": "FFmpeg-Workbench-updater"}
        )
    except ImportError:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "FFmpeg-Workbench-updater"})
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _json(url: str) -> Any:
    with open_verified(url) as r:
        return json.loads(r.read().decode("utf-8"))


def _text(url: str) -> str:
    with open_verified(url) as r:
        return r.read().decode("utf-8", "replace")


def _download(url: str, dest: str, sha256: str, log: Log) -> None:
    h = hashlib.sha256()
    total = 0
    with open_verified(url, timeout=120) as r, open(dest, "wb") as fh:
        size = int(r.headers.get("Content-Length") or 0)
        last = -10
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            h.update(chunk)
            total += len(chunk)
            if size:
                pct = int(total * 100 / size)
                if pct >= last + 10:
                    log(f"  downloading… {pct}%")
                    last = pct
    if h.hexdigest() != sha256.lower():
        os.remove(dest)
        raise RuntimeError("Checksum mismatch - the download was corrupted or tampered with.")


def _sums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


# ── FFmpeg ───────────────────────────────────────────────────────────────────
# FFmpeg is pinned: the app ships the build every tool is tested against and
# it changes only with app releases (a newer FFmpeg can change or drop options
# the tools rely on).  There is deliberately no in-app FFmpeg updater.


def is_app_managed(path: str | None) -> bool:
    """True if *path* is the FFmpeg shipped with the app."""
    if not path:
        return True
    from core.hardware import APP_ROOT

    p = os.path.abspath(path)
    roots = [os.path.abspath(APP_ROOT)]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(os.path.abspath(meipass))
    return any(p.startswith(r + os.sep) for r in roots)


def latest_ytdlp_release(force: bool = False) -> str | None:
    """Newest yt-dlp version (automatic checks respect 'check for updates')."""
    if offline() or (not force and not _checks_enabled()):
        return None
    try:
        return _json(_YTDLP_API).get("tag_name")
    except Exception as exc:
        logger.info("yt-dlp update check failed: %s", exc, exc_info=True)
        return None


def install_ytdlp(log: Log) -> bool:
    """Download the newest yt-dlp stand-alone build into the user tools folder."""
    if offline():
        log("Updates are disabled (offline mode).")
        return False
    asset = _YTDLP_ASSET.get(platform_tag())
    target_root = user_tools_dir()
    if not asset or not target_root:
        log("No yt-dlp build is published for this platform - update it with your package manager.")
        return False
    try:
        from core import network

        try:
            rel = _json(_YTDLP_API)
            assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        except Exception:
            if not network.get().get("network.github_mirror"):
                raise
            # api.github.com is often blocked together with GitHub; mirrors serve the
            # release files themselves, so use the stable "latest" links.
            base = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
            rel = {"tag_name": "latest"}
            assets = {name: base + name for name in (asset, "SHA2-256SUMS")}
        assets = {k: network.mirror_url(v) for k, v in assets.items()}
        sums = _sums(_text(assets["SHA2-256SUMS"]))
        os.makedirs(target_root, exist_ok=True)
        exe_name = "yt-dlp" + (".exe" if os.name == "nt" else "")
        tmp = os.path.join(target_root, exe_name + ".download")
        log(f"Downloading yt-dlp {rel.get('tag_name')}…")
        _download(assets[asset], tmp, sums[asset], log)
        if os.name != "nt":
            os.chmod(tmp, 0o755)
        r = _proc.run([tmp, "--version"], timeout=60)
        if r.returncode != 0:
            os.remove(tmp)
            log("The downloaded yt-dlp does not run - keeping the current one.")
            return False
        os.replace(tmp, os.path.join(target_root, exe_name))
        log(f"✅ yt-dlp {r.stdout.strip()} installed to {target_root}")
        return True
    except Exception as exc:
        logger.exception("yt-dlp update failed")
        log(f"❌ Update failed: {exc}")
        return False
