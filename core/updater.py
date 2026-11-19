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
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import zipfile
from typing import Callable, Optional

from core.hardware import CREATE_NO_WINDOW, find_binary, platform_tag, user_tools_dir

_log = logging.getLogger("crossfader.updater")
Log = Callable[[str], None]

_BTBN_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=8"
_YTDLP_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_VARIANT = {"windows-x64": "win64-gpl-shared", "linux-x64": "linux64-gpl-shared"}
_YTDLP_ASSET = {"windows-x64": "yt-dlp.exe", "linux-x64": "yt-dlp_linux"}


def _offline() -> bool:
    if os.environ.get("CROSSFADER_NO_NETWORK") == "1":
        return True
    try:
        from core.settings import get
        return not get("check_updates", True)
    except Exception:
        return False


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

def _latest_btbn() -> Optional[dict]:
    variant = _VARIANT.get(platform_tag())
    if not variant:
        return None
    pat = re.compile(r"^ffmpeg-n(\d+)\.(\d+)(?:\.(\d+))?-(\d+)-g([0-9a-f]+)-" + re.escape(variant)
                     + r"-\d+\.\d+\.(zip|tar\.xz)$")
    for rel in _json(_BTBN_API):
        if not rel.get("tag_name", "").startswith("autobuild-"):
            continue
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        best = None
        for name in assets:
            m = pat.match(name)
            if m:
                key = tuple(int(x or 0) for x in m.group(1, 2, 3, 4))
                if best is None or key > best[0]:
                    best = (key, name)
        if best and "checksums.sha256" in assets:
            name = best[1]
            sums = _sums(_text(assets["checksums.sha256"]))
            ver = ".".join(str(x) for x in best[0][:3])
            return {"version": ver, "name": name, "url": assets[name], "sha256": sums.get(name, "")}
    return None


def latest_ffmpeg_release() -> Optional[str]:
    if _offline():
        return None
    try:
        info = _latest_btbn()
        return info["version"] if info else None
    except Exception as exc:
        _log.info("FFmpeg update check failed: %s", exc)
        return None


def is_app_managed(path: Optional[str]) -> bool:
    """True if *path* is a bundled or app-downloaded FFmpeg (safe to update)."""
    if not path:
        return True
    from core.hardware import APP_ROOT
    p = os.path.abspath(path)
    roots = [os.path.abspath(APP_ROOT)]
    if user_tools_dir():
        roots.append(os.path.abspath(user_tools_dir()))
    return any(p.startswith(r + os.sep) for r in roots)


def install_ffmpeg(log: Log) -> bool:
    """Download the newest stable FFmpeg build into the user tools folder."""
    if _offline():
        log("Updates are disabled (offline mode).")
        return False
    target_root = user_tools_dir()
    if not target_root:
        log("No writable tools folder available.")
        return False
    try:
        log("Checking the latest FFmpeg build…")
        info = _latest_btbn()
        if not info or not info["sha256"]:
            log("No suitable FFmpeg build is published for this platform.")
            return False
        log(f"Downloading FFmpeg {info['version']} ({info['name']})")
        work = tempfile.mkdtemp(prefix="ffmpeg_update_")
        try:
            archive = os.path.join(work, info["name"])
            _download(info["url"], archive, info["sha256"], log)
            log("Checksum verified. Extracting…")
            stage = os.path.join(work, "ffmpeg")
            _extract_ffmpeg(archive, stage)
            exe = os.path.join(stage, "bin", "ffmpeg" + (".exe" if os.name == "nt" else ""))
            r = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True,
                               timeout=30, creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                log("The downloaded FFmpeg does not run on this system - keeping the current one.")
                return False
            os.makedirs(target_root, exist_ok=True)
            final = os.path.join(target_root, "ffmpeg")
            old = final + ".old"
            if os.path.isdir(old):
                shutil.rmtree(old, ignore_errors=True)
            if os.path.isdir(final):
                os.replace(final, old)
            shutil.move(stage, final)
            shutil.rmtree(old, ignore_errors=True)
            with open(os.path.join(final, "VERSION.json"), "w", encoding="utf-8") as fh:
                json.dump(info, fh, indent=2)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        try:
            from core import encoders
            encoders._ENCODERS.clear()
            encoders._HW_OK.clear()
        except Exception:
            pass
        log(f"✅ FFmpeg {info['version']} installed to {final}")
        return True
    except Exception as exc:
        _log.exception("FFmpeg update failed")
        log(f"❌ Update failed: {exc}")
        return False


def _safe(name: str) -> str:
    norm = os.path.normpath(name).replace("\\", "/")
    if norm.startswith(("/", "../")) or "/../" in norm:
        raise RuntimeError(f"unsafe archive member {name}")
    return norm


def _extract_ffmpeg(archive: str, dest: str) -> None:
    os.makedirs(os.path.join(dest, "bin"), exist_ok=True)
    soname = re.compile(r"^(lib[\w.+-]+?\.so\.\d+)\.\d+\.\d+$")

    def place(name: str, data: bytes) -> None:
        base, parent = os.path.basename(name), os.path.basename(os.path.dirname(name))
        stem, ext = os.path.splitext(base)
        if parent == "bin" and (stem in ("ffmpeg", "ffprobe", "ffplay") or ext == ".dll"):
            out = os.path.join(dest, "bin", base)
        elif parent == "lib" and soname.match(base):
            out = os.path.join(dest, "lib", soname.match(base).group(1))
        elif base in ("LICENSE.txt", "LICENSE"):
            out = os.path.join(dest, "LICENSE.txt")
        else:
            return
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(data)
        if parent == "bin" and os.name != "nt":
            os.chmod(out, 0o755)

    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    place(_safe(info.filename), zf.read(info))
    else:
        with tarfile.open(archive, "r:*") as tf:
            for m in tf.getmembers():
                if m.isfile():
                    f = tf.extractfile(m)
                    if f is not None:
                        place(_safe(m.name), f.read())


# ── yt-dlp ───────────────────────────────────────────────────────────────────

def latest_ytdlp_release() -> Optional[str]:
    if _offline():
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
        rel = _json(_YTDLP_API)
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
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
