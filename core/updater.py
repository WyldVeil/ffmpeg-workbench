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
import subprocess
import sys
from collections.abc import Callable

from core.hardware import CREATE_NO_WINDOW, find_binary, platform_tag, user_tools_dir

_log = logging.getLogger("crossfader.updater")
Log = Callable[[str], None]

_BTBN_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=8"
_YTDLP_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_VARIANT = {"windows-x64": "win64-gpl-shared", "linux-x64": "linux64-gpl-shared"}
_YTDLP_ASSET = {"windows-x64": "yt-dlp.exe", "linux-x64": "yt-dlp_linux"}


def _offline() -> bool:
    """Offline mode (Settings → Network) or the test switch."""
    if os.environ.get("CROSSFADER_NO_NETWORK") == "1":
        return True
    try:
        from core import network
        return network.is_offline()
    except Exception:
        return False


def _checks_enabled() -> bool:
    """Automatic update checks: not offline and not switched off."""
    if _offline():
        return False
    try:
        from core.settings import get
        return bool(get("check_updates", True))
    except Exception:
        return True


def _open(url: str, timeout: float = 30.0):
    """urlopen honouring proxy settings but ALWAYS verifying TLS."""
    import urllib.request
    try:
        from core import network
        cfg = dict(network.get())
        cfg["network.verify_ssl"] = True
        return network.urlopen(url, timeout=timeout, cfg=cfg,
                               headers={"User-Agent": "WyldVeil-Media-Editor-updater"})
    except ImportError:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "WyldVeil-Media-Editor-updater"})
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _json(url: str):
    with _open(url) as r:
        return json.loads(r.read().decode("utf-8"))


def _text(url: str) -> str:
    with _open(url) as r:
        return r.read().decode("utf-8", "replace")


def _download(url: str, dest: str, sha256: str, log: Log) -> None:
    h = hashlib.sha256()
    total = 0
    with _open(url, timeout=120) as r, open(dest, "wb") as fh:
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


def _sums(text: str) -> dict:
    out = {}
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
    if _offline() or (not force and not _checks_enabled()):
        return None
    try:
        return _json(_YTDLP_API).get("tag_name")
    except Exception as exc:
        _log.info("yt-dlp update check failed: %s", exc)
        return None


def install_ytdlp(log: Log) -> bool:
    """Download the newest yt-dlp stand-alone build into the user tools folder."""
    if _offline():
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
        r = subprocess.run([tmp, "--version"], capture_output=True, text=True, timeout=60,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            os.remove(tmp)
            log("The downloaded yt-dlp does not run - keeping the current one.")
            return False
        os.replace(tmp, os.path.join(target_root, exe_name))
        log(f"✅ yt-dlp {r.stdout.strip()} installed to {target_root}")
        return True
    except Exception as exc:
        _log.exception("yt-dlp update failed")
        log(f"❌ Update failed: {exc}")
        return False


def tool_versions() -> dict:
    """{tool: (path, version)} for the About / System Info dialogs."""
    from core.ffmpeg import version_of
    out = {}
    for tool in ("ffmpeg", "ffprobe", "ffplay"):
        path = find_binary(tool)
        out[tool] = (path, version_of(path) if path else "Missing")
    for tool, args in (("yt-dlp", ["--version"]), ("deno", ["--version"])):
        path = find_binary(tool)
        ver = "Missing"
        if path:
            try:
                r = subprocess.run([path, *args], capture_output=True, text=True, timeout=30,
                                   creationflags=CREATE_NO_WINDOW)
                ver = (r.stdout.strip().splitlines() or ["?"])[0]
            except (OSError, subprocess.TimeoutExpired):
                ver = "Unknown"
        out[tool] = (path, ver)
    return out
