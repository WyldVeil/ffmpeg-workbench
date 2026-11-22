#!/usr/bin/env python3
"""
scripts/fetch_binaries.py - Download the third-party tools bundled with the app.

Everything is pinned in ``scripts/binaries.lock.json`` (exact URLs + SHA-256)
so builds are reproducible.  Each archive is verified before extraction, only
the files we need are extracted (with path-traversal protection), and the
licence texts are collected next to the binaries together with a
``VERSIONS.json`` provenance record.

Layout produced::

    bin/windows-x64/ffmpeg.exe ffprobe.exe ffplay.exe *.dll yt-dlp.exe deno.exe
    bin/windows-x64/fluidsynth/fluidsynth.exe *.dll
    bin/<platform>/whisper/whisper-cli[.exe] + whisper/ggml libraries (speech-to-text)
    bin/linux-x64/ffmpeg ffprobe ffplay yt-dlp deno
    bin/<platform>/licenses/<tool>/...
    bin/<platform>/VERSIONS.json

Usage::

    python scripts/fetch_binaries.py                      # both platforms from the lock
    python scripts/fetch_binaries.py --platform linux-x64 # one platform
    python scripts/fetch_binaries.py --refresh            # re-resolve latest versions into the lock
    python scripts/fetch_binaries.py --sources dist/sources   # also fetch FFmpeg source (GPL)

Network access uses GITHUB_TOKEN (or `gh auth token`) when available to avoid
API rate limits.  Requires only the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "scripts", "binaries.lock.json")
PLATFORMS = ("windows-x64", "linux-x64")
UA = "binary-fetcher/1.0 (+https://github.com/WyldVeil/wyldveils-media-editor)"

# Which FFmpeg release line to track and which BtbN variant to use per platform.
# Shared builds on both platforms: ffmpeg/ffprobe/ffplay share one set of
# libraries, roughly halving the size versus three static executables.
# The FFmpeg tree is kept intact under bin/<platform>/ffmpeg/{bin,lib}
# (Linux binaries find their libraries through RPATH $ORIGIN/../lib).
FFMPEG_LINE = "9.0"
FFMPEG_VARIANT = {"windows-x64": "win64-gpl-shared", "linux-x64": "linux64-gpl-shared"}


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────


def _token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    if shutil.which("gh"):
        try:
            r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
            return r.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None
    return None


def _request(url: str, *, api: bool = False) -> urllib.request.Request:
    headers = {"User-Agent": UA}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        tok = _token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    return urllib.request.Request(url, headers=headers)


def get_json(url: str):
    with urllib.request.urlopen(_request(url, api=True), timeout=60) as r:
        return json.load(r)


def get_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def download(url: str, dest: str, expected_sha256: str | None = None) -> str:
    """Download *url* to *dest* (resumable cache) and verify SHA-256."""
    if os.path.isfile(dest) and expected_sha256 and sha256_file(dest) == expected_sha256.lower():
        print(f"  cached  {os.path.basename(dest)}")
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    print(f"  fetch   {url}")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(_request(url), timeout=120) as r, open(tmp, "wb") as fh:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                last = time.monotonic()
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if total and time.monotonic() - last > 2:
                        print(f"          {done / total:5.1%} of {total / 1e6:.0f} MB", flush=True)
                        last = time.monotonic()
            break
        except (urllib.error.URLError, OSError) as exc:
            if attempt == 3:
                raise
            print(f"          retry after error: {exc}")
            time.sleep(3 * (attempt + 1))
    digest = sha256_file(tmp)
    if expected_sha256 and digest != expected_sha256.lower():
        os.remove(tmp)
        raise SystemExit(
            f"SHA-256 mismatch for {url}\n  expected {expected_sha256}\n  got      {digest}"
        )
    os.replace(tmp, dest)
    return dest


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
#  Resolving latest versions (writes the lock file)
# ─────────────────────────────────────────────────────────────────────────────


def _parse_sums(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


_BUILDS_REPO = "FFmpeg-Builds/FFmpeg-Builds"  # formerly BtbN/FFmpeg-Builds


def _month_end_release() -> dict:
    """The newest *month-end* autobuild.  Daily builds are deleted after 14
    days; the last build of each month is kept for two years, so pinning one
    keeps the lock file reproducible."""
    releases = []
    for page in (1, 2, 3):
        batch = get_json(
            f"https://api.github.com/repos/{_BUILDS_REPO}/releases?per_page=100&page={page}"
        )
        if not batch:
            break
        releases += [r for r in batch if r["tag_name"].startswith("autobuild-")]
    if not releases:
        raise SystemExit("No FFmpeg autobuild releases found")
    by_month = {}
    for r in releases:
        month = r["tag_name"][len("autobuild-") : len("autobuild-") + 7]  # YYYY-MM
        if month not in by_month or r["tag_name"] > by_month[month]["tag_name"]:
            by_month[month] = r
    months = sorted(by_month)
    current = max(r["tag_name"] for r in releases)[len("autobuild-") : len("autobuild-") + 7]
    complete = [m for m in months if m < current] or months
    return by_month[complete[-1]]


def resolve_ffmpeg() -> dict:
    rel = _month_end_release()
    pattern = re.compile(
        r"^ffmpeg-(n"
        + re.escape(FFMPEG_LINE)
        + r"[^-]*-\d+-g[0-9a-f]+)-(.+)-"
        + re.escape(FFMPEG_LINE)
        + r"\.(zip|tar\.xz)$"
    )
    assets = {a["name"]: a for a in rel["assets"]}
    found, version = {}, None
    for name, a in assets.items():
        m = pattern.match(name)
        if not m:
            continue
        version = m.group(1)
        for plat, variant in FFMPEG_VARIANT.items():
            if m.group(2) == variant:
                found[plat] = {"name": name, "url": a["browser_download_url"]}
    if len(found) != len(FFMPEG_VARIANT) or version is None:
        raise SystemExit(f"{rel['tag_name']} lacks FFmpeg {FFMPEG_LINE} builds for every platform")
    sums = _parse_sums(get_text(assets["checksums.sha256"]["browser_download_url"]))
    for plat in found:
        found[plat]["sha256"] = sums[found[plat]["name"]]
    commit = version.rsplit("-g", 1)[-1]
    ref = get_json(f"https://api.github.com/repos/{_BUILDS_REPO}/git/ref/tags/{rel['tag_name']}")
    scripts_sha = ref.get("object", {}).get("sha", rel["tag_name"])
    return {
        "version": version.lstrip("n"),
        "release": rel["tag_name"],
        "source": f"https://github.com/FFmpeg/FFmpeg/commit/{commit}",
        "source_tarball": f"https://github.com/FFmpeg/FFmpeg/archive/{commit}.tar.gz",
        "build_scripts": f"https://github.com/{_BUILDS_REPO}/tree/{scripts_sha}",
        "license": "GPL-3.0-or-later",
        "assets": found,
    }


def resolve_ytdlp() -> dict:
    rel = get_json("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest")
    tag = rel["tag_name"]
    assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
    sums = _parse_sums(get_text(assets["SHA2-256SUMS"]))
    want = {"windows-x64": "yt-dlp.exe", "linux-x64": "yt-dlp_linux"}
    return {
        "version": tag,
        "license": "Unlicense; the stand-alone executable is a GPL-3.0-or-later combined work "
        "(see THIRD_PARTY_LICENSES.txt)",
        "license_urls": [
            f"https://raw.githubusercontent.com/yt-dlp/yt-dlp/{tag}/LICENSE",
            f"https://raw.githubusercontent.com/yt-dlp/yt-dlp/{tag}/THIRD_PARTY_LICENSES.txt",
        ],
        "assets": {p: {"name": n, "url": assets[n], "sha256": sums[n]} for p, n in want.items()},
    }


def resolve_deno() -> dict:
    rel = get_json("https://api.github.com/repos/denoland/deno/releases/latest")
    tag = rel["tag_name"]
    assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
    want = {
        "windows-x64": "deno-x86_64-pc-windows-msvc.zip",
        "linux-x64": "deno-x86_64-unknown-linux-gnu.zip",
    }
    out = {}
    for plat, name in want.items():
        text = get_text(assets[name + ".sha256sum"])
        m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
        out[plat] = {"name": name, "url": assets[name], "sha256": m.group(1).lower() if m else None}
    return {
        "version": tag.lstrip("v"),
        "license": "MIT",
        "license_urls": [f"https://raw.githubusercontent.com/denoland/deno/{tag}/LICENSE.md"],
        "assets": out,
    }


def resolve_fluidsynth(cache: str) -> dict:
    rel = get_json("https://api.github.com/repos/FluidSynth/fluidsynth/releases/latest")
    tag = rel["tag_name"]
    asset = next(a for a in rel["assets"] if re.search(r"win10-x64.*\.zip$", a["name"]))
    digest = (asset.get("digest") or "").split(":", 1)[-1]
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        # older API responses have no digest: pin the hash of what we download now
        digest = sha256_file(
            download(asset["browser_download_url"], os.path.join(cache, asset["name"]))
        )
    return {
        "version": tag.lstrip("v"),
        "license": "LGPL-2.1-or-later (bundled DLLs: SDL3 - zlib licence; libsndfile - LGPL-2.1-or-later)",
        "license_urls": [
            f"https://raw.githubusercontent.com/FluidSynth/fluidsynth/{tag}/LICENSE",
            "https://raw.githubusercontent.com/libsndfile/libsndfile/1.2.2/COPYING",
            "https://raw.githubusercontent.com/libsdl-org/SDL/release-3.2.10/LICENSE.txt",
        ],
        "assets": {
            "windows-x64": {
                "name": asset["name"],
                "url": asset["browser_download_url"],
                "sha256": digest,
            }
        },
    }


def resolve_whisper() -> dict:
    """whisper.cpp CLI builds.  Release binaries are attached to the numbered
    'bNNNN' build releases, so take the newest release that has both."""
    want = {"windows-x64": "whisper-bin-x64.zip", "linux-x64": "whisper-bin-ubuntu-x64.tar.gz"}
    for rel in get_json("https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=15"):
        assets = {a["name"]: a for a in rel.get("assets", [])}
        if all(name in assets for name in want.values()):
            tag = rel["tag_name"]
            out = {}
            for plat, name in want.items():
                a = assets[name]
                digest = (a.get("digest") or "").split(":", 1)[-1]
                out[plat] = {
                    "name": name,
                    "url": a["browser_download_url"],
                    "sha256": digest if re.fullmatch(r"[0-9a-f]{64}", digest) else None,
                }
            return {
                "version": tag,
                "license": "MIT",
                "license_urls": [
                    f"https://raw.githubusercontent.com/ggml-org/whisper.cpp/{tag}/LICENSE"
                ],
                "assets": out,
            }
    raise SystemExit("no whisper.cpp release with Windows and Linux binaries found")


GENERALUSER_REPO = "mrbumpy409/GeneralUser-GS"


def resolve_soundfont(cache: str) -> dict:
    """GeneralUser GS (General MIDI SoundFont for the MIDIfier).  Its licence
    asks for a local copy rather than links to the author's downloads, so the
    build ships one, pinned to a commit and verified by hash."""
    commit = get_json(f"https://api.github.com/repos/{GENERALUSER_REPO}/commits?per_page=1")[0][
        "sha"
    ]
    raw = f"https://raw.githubusercontent.com/{GENERALUSER_REPO}/{commit}"
    url = f"{raw}/GeneralUser-GS.sf2"
    licence = get_text(f"{raw}/documentation/LICENSE.txt")
    m = re.search(r"GeneralUser GS v(\d+(?:\.\d+)+)", licence)
    version = m.group(1) if m else commit[:10]
    path = download(
        url, os.path.join(cache, f"{version}-GeneralUser-GS.sf2")
    )  # same name install uses
    return {
        "version": version,
        "license": "GeneralUser GS License v2.0 (free use in software projects)",
        "license_urls": [f"{raw}/documentation/LICENSE.txt"],
        "assets": {"any": {"name": "GeneralUser-GS.sf2", "url": url, "sha256": sha256_file(path)}},
    }


SHADER_SOURCES = (
    # (name in the bundle, how to find it, licence)
    (
        "FSR.glsl",
        ("gist", "agyild", "82219c545228d70c5604f865ce0b0ce5", "FSR.glsl"),
        "MIT (AMD FidelityFX Super Resolution 1.0, mpv port by agyild; licence in the file)",
    ),
    (
        "NVScaler.glsl",
        ("gist", "agyild", "7e8951915b2bf24526a9343d951db214", "NVScaler.glsl"),
        "MIT (NVIDIA Image Scaling, mpv port by agyild; licence in the file)",
    ),
    ("ArtCNN_C4F16.glsl", ("repo", "Artoriuz/ArtCNN", "GLSL/ArtCNN_C4F16.glsl", "LICENSE"), "MIT"),
    ("ArtCNN_C4F32.glsl", ("repo", "Artoriuz/ArtCNN", "GLSL/ArtCNN_C4F32.glsl", "LICENSE"), "MIT"),
    (
        "Anime4K_Upscale_CNN_x2_M.glsl",
        ("repo", "bloc97/Anime4K", "glsl/Upscale/Anime4K_Upscale_CNN_x2_M.glsl", "LICENSE"),
        "MIT",
    ),
)


def resolve_shaders(cache: str) -> dict:
    """Upscaling shaders for the Resolution Scaler (libplacebo / mpv hook
    format), each pinned to a commit or gist revision and verified by hash.
    Only MIT-licensed ones: FSRCNNX was left out because it is GPL-3.0."""
    assets, licences, commits = {}, [], {}
    for name, (kind, *where), licence in SHADER_SOURCES:
        if kind == "gist":
            owner, gist, file = where
            rev = get_json(f"https://api.github.com/gists/{gist}")["history"][0]["version"]
            url = f"https://gist.githubusercontent.com/{owner}/{gist}/raw/{rev}/{file}"
        else:
            repo, path, lic = where
            if repo not in commits:
                commits[repo] = get_json(f"https://api.github.com/repos/{repo}/commits?per_page=1")[
                    0
                ]["sha"]
                licences.append(f"https://raw.githubusercontent.com/{repo}/{commits[repo]}/{lic}")
            url = f"https://raw.githubusercontent.com/{repo}/{commits[repo]}/{path}"
        local = download(url, os.path.join(cache, "shaders", name))
        assets[name] = {"url": url, "sha256": sha256_file(local), "license": licence}
    return {
        "version": time.strftime("%Y-%m-%d"),
        "license": "MIT (each file)",
        "license_urls": licences,
        "assets": {"any": assets},
    }


def refresh_lock(cache: str) -> dict:
    print("Resolving latest versions …")
    lock = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tools": {
            "ffmpeg": resolve_ffmpeg(),
            "yt-dlp": resolve_ytdlp(),
            "deno": resolve_deno(),
            "fluidsynth": resolve_fluidsynth(cache),
            "whisper": resolve_whisper(),
            "soundfont": resolve_soundfont(cache),
            "shaders": resolve_shaders(cache),
        },
    }
    with open(LOCK, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(lock, fh, indent=2)
        fh.write("\n")
    for name, info in lock["tools"].items():
        print(f"  {name:<11} {info['version']}")
    return lock


# ─────────────────────────────────────────────────────────────────────────────
#  Extraction
# ─────────────────────────────────────────────────────────────────────────────


def _safe_name(name: str) -> str:
    norm = os.path.normpath(name).replace("\\", "/")
    if (
        norm.startswith("/")
        or norm.startswith("../")
        or "/../" in norm
        or re.match(r"^[A-Za-z]:", norm)
    ):
        raise SystemExit(f"refusing unsafe archive member: {name}")
    return norm


def _members(archive: str):
    """Yield (member_name, reader) for each regular file in a zip or tar.xz."""
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = _safe_name(info.filename)
                yield name, (lambda i=info: zf.read(i))
    else:
        with tarfile.open(archive, "r:*") as tf:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                name = _safe_name(m.name)
                yield name, (lambda mm=m: (tf.extractfile(mm) or io.BytesIO()).read())


def _write(dest: str, data: bytes, executable: bool = False) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(data)
    if executable and os.name != "nt":
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


_SONAME_RE = re.compile(r"^(lib[\w.+-]+?\.so\.\d+)\.\d+\.\d+$")


def install_ffmpeg(info: dict, plat: str, archive: str, out: str) -> list:
    """Extract the FFmpeg tree to OUT/ffmpeg/{bin,lib}.

    Windows: executables + DLLs side by side in bin/.  Linux: executables in
    bin/, shared libraries in lib/ stored under their SONAME (``libavcodec.so.63``)
    only - the development symlinks are dropped so packagers never copy the
    same 100 MB library three times.
    """
    wanted_bins = {"ffmpeg", "ffprobe", "ffplay"}
    root = os.path.join(out, "ffmpeg")
    placed = []
    for name, read in _members(archive):
        base = os.path.basename(name)
        stem, ext = os.path.splitext(base)
        parent = os.path.basename(os.path.dirname(name))
        if parent == "bin" and ((stem in wanted_bins and ext in ("", ".exe")) or ext == ".dll"):
            _write(os.path.join(root, "bin", base), read(), executable=True)
            placed.append(base)
        elif parent == "lib":
            m = _SONAME_RE.match(base)
            if m:
                _write(os.path.join(root, "lib", m.group(1)), read())
                placed.append(m.group(1))
        elif base in ("LICENSE.txt", "LICENSE") and name.count("/") <= 1:
            _write(os.path.join(out, "licenses", "ffmpeg", "LICENSE.txt"), read())
    missing = [b for b in wanted_bins if not any(p.startswith(b) for p in placed)]
    if missing:
        raise SystemExit(f"FFmpeg archive lacks {missing}")
    note = (
        f"FFmpeg {info['version']} ({plat}) - built by BtbN/FFmpeg-Builds\n"
        f"Release: https://github.com/BtbN/FFmpeg-Builds/releases/tag/{info['release']}\n"
        f"Corresponding source (FFmpeg): {info['source']}\n"
        f"Source tarball: {info['source_tarball']}\n"
        f"Build scripts (exact library versions and configure flags): {info['build_scripts']}\n"
        f"Licence: {info['license']} - see LICENSE.txt in this folder.\n"
    )
    _write(os.path.join(out, "licenses", "ffmpeg", "SOURCE.txt"), note.encode())
    return placed


def install_single(
    archive_or_file: str, out: str, target_name: str, member_suffix: str | None = None
) -> None:
    if member_suffix is None:
        shutil.copyfile(archive_or_file, os.path.join(out, target_name))
        os.chmod(os.path.join(out, target_name), 0o755)
        return
    for name, read in _members(archive_or_file):
        if name.endswith(member_suffix):
            _write(os.path.join(out, target_name), read(), executable=True)
            return
    raise SystemExit(f"{member_suffix} not found in {archive_or_file}")


def install_fluidsynth(archive: str, out: str) -> None:
    dest = os.path.join(out, "fluidsynth")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    for name, read in _members(archive):
        base = os.path.basename(name)
        if base.lower().endswith((".exe", ".dll")) and "/bin/" in "/" + name:
            _write(os.path.join(dest, base), read(), executable=True)
        elif base.upper().startswith(("LICENSE", "COPYING")):
            _write(os.path.join(out, "licenses", "fluidsynth", base), read())


_WHISPER_KEEP = re.compile(
    r"^(whisper-cli(\.exe)?|(lib)?whisper[\w.-]*\.(dll|so[\d.]*)|"
    r"(lib)?ggml[\w.-]*\.(dll|so[\d.]*)|LICENSE)$"
)


def install_whisper(archive: str, out: str) -> list:
    """Extract whisper-cli and the ggml/whisper libraries to OUT/whisper.

    Linux libraries are written under their SONAME (libwhisper.so.1 …) since
    the archive's version symlinks are not extracted."""
    dest = os.path.join(out, "whisper")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    written = []
    for name, read in _members(archive):
        base = os.path.basename(name)
        if not _WHISPER_KEEP.match(base):
            continue
        if base == "LICENSE":
            _write(os.path.join(out, "licenses", "whisper", "LICENSE"), read())
            continue
        m = _SONAME_RE.match(base)
        target = m.group(1) if m else base
        _write(os.path.join(dest, target), read(), executable=True)
        written.append(target)
    if not any(w.startswith("whisper-cli") for w in written):
        raise SystemExit(f"whisper-cli not found in {archive}")
    return written


def fetch_licence_texts(tool: str, urls: list, out: str) -> None:
    for url in urls:
        try:
            text = get_text(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        parts = url.split("/")
        repo = parts[4] if len(parts) > 4 and "raw.githubusercontent.com" in url else tool
        name = (
            os.path.basename(url)
            if repo == tool or repo.lower() in tool.lower()
            else f"{repo}-{os.path.basename(url)}"
        )
        _write(os.path.join(out, "licenses", tool, name), text.encode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────


def _entry(tool: dict, a: dict, **more: str) -> dict:
    """VERSIONS.json's record of one downloaded file."""
    return {
        "version": tool["version"],
        "url": a["url"],
        "sha256": a["sha256"],
        "license": tool["license"],
        **more,
    }


def _main_tools(tools: dict, plat: str, cache: str, out: str) -> dict:
    """FFmpeg, yt-dlp and Deno: every platform has them."""
    exe = ".exe" if plat.startswith("windows") else ""
    ff = tools["ffmpeg"]
    a = ff["assets"][plat]
    install_ffmpeg(ff, plat, download(a["url"], os.path.join(cache, a["name"]), a["sha256"]), out)
    versions = {"ffmpeg": _entry(ff, a, source=ff["source"])}

    yt = tools["yt-dlp"]
    a = yt["assets"][plat]
    path = download(a["url"], os.path.join(cache, f"{yt['version']}-{a['name']}"), a["sha256"])
    install_single(path, out, "yt-dlp" + exe)
    fetch_licence_texts("yt-dlp", yt["license_urls"], out)
    versions["yt-dlp"] = _entry(yt, a)

    dn = tools["deno"]
    a = dn["assets"][plat]
    path = download(a["url"], os.path.join(cache, f"{dn['version']}-{a['name']}"), a["sha256"])
    install_single(path, out, "deno" + exe, member_suffix="deno" + exe)
    fetch_licence_texts("deno", dn["license_urls"], out)
    versions["deno"] = _entry(dn, a)
    return versions


def _extra_tools(tools: dict, plat: str, cache: str, out: str) -> dict:
    """FluidSynth and whisper.cpp where the platform has a build."""
    versions = {}
    fs = tools.get("fluidsynth", {})
    if plat in fs.get("assets", {}):
        a = fs["assets"][plat]
        install_fluidsynth(download(a["url"], os.path.join(cache, a["name"]), a["sha256"]), out)
        fetch_licence_texts("fluidsynth", fs["license_urls"], out)
        versions["fluidsynth"] = _entry(fs, a)

    wh = tools.get("whisper", {})
    if plat in wh.get("assets", {}):
        a = wh["assets"][plat]
        path = download(a["url"], os.path.join(cache, f"{wh['version']}-{a['name']}"), a["sha256"])
        install_whisper(path, out)
        fetch_licence_texts("whisper", wh["license_urls"], out)
        versions["whisper.cpp"] = _entry(wh, a)
    return versions


def _data_files(tools: dict, cache: str, out: str) -> dict:
    """The soundfont and the shaders (the same on every platform)."""
    versions = {}
    sf = tools.get("soundfont", {})
    a = sf.get("assets", {}).get("any")
    if a:
        path = download(a["url"], os.path.join(cache, f"{sf['version']}-{a['name']}"), a["sha256"])
        os.makedirs(os.path.join(out, "soundfonts"), exist_ok=True)
        shutil.copyfile(path, os.path.join(out, "soundfonts", a["name"]))
        fetch_licence_texts("generaluser-gs", sf["license_urls"], out)
        versions["GeneralUser GS"] = _entry(sf, a)

    sh = tools.get("shaders", {})
    for name, a in sh.get("assets", {}).get("any", {}).items():
        path = download(
            a["url"], os.path.join(cache, "shaders", f"{a['sha256'][:12]}-{name}"), a["sha256"]
        )
        os.makedirs(os.path.join(out, "shaders"), exist_ok=True)
        shutil.copyfile(path, os.path.join(out, "shaders", name))
        versions[f"shader {name}"] = {**_entry(sh, a), "license": a["license"]}
    if sh:
        fetch_licence_texts("shaders", sh.get("license_urls", []), out)
    return versions


def _smoke_test(plat: str, out: str) -> None:
    """Run what we can on this machine (only when it is *plat*)."""
    host = (
        "windows-x64"
        if os.name == "nt"
        else "linux-x64"
        if sys.platform.startswith("linux")
        else ""
    )
    if host != plat:
        return
    exe = ".exe" if plat.startswith("windows") else ""
    for tool, args in (
        ("ffmpeg", ["-version"]),
        ("ffprobe", ["-version"]),
        ("yt-dlp", ["--version"]),
        ("deno", ["--version"]),
        ("whisper-cli", ["--help"]),
    ):
        if tool == "whisper-cli" and not os.path.isdir(os.path.join(out, "whisper")):
            continue
        folder = (
            os.path.join(out, "ffmpeg", "bin")
            if tool.startswith("ff")
            else os.path.join(out, "whisper")
            if tool == "whisper-cli"
            else out
        )
        r = subprocess.run(
            [os.path.join(folder, tool + exe), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
        first = (r.stdout or r.stderr).splitlines()[:1]
        print(
            f"  ok      {tool:<8} {first[0] if first else '?'}"
            if r.returncode == 0
            else f"  FAILED  {tool}: {r.stderr[:200]}"
        )
        if r.returncode != 0:
            raise SystemExit(1)


def install_platform(lock: dict, plat: str, cache: str, dest_root: str) -> None:
    out = os.path.join(dest_root, plat)
    print(f"\n== {plat} -> {out}")
    if os.path.isdir(out):
        for entry in os.listdir(out):
            p = os.path.join(out, entry)
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
    os.makedirs(out, exist_ok=True)
    tools = lock["tools"]
    versions = {
        **_main_tools(tools, plat, cache, out),
        **_extra_tools(tools, plat, cache, out),
        **_data_files(tools, cache, out),
    }
    with open(os.path.join(out, "VERSIONS.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(
            {"platform": plat, "generated": lock.get("generated"), "tools": versions}, fh, indent=2
        )
        fh.write("\n")
    _smoke_test(plat, out)


def fetch_sources(lock: dict, dest: str) -> None:
    """Download the FFmpeg source matching the bundled build (GPL obligation)."""
    ff = lock["tools"]["ffmpeg"]
    os.makedirs(dest, exist_ok=True)
    name = f"ffmpeg-{ff['version']}-source.tar.gz"
    download(ff["source_tarball"], os.path.join(dest, name))
    print(f"  source  {os.path.join(dest, name)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--platform", choices=(*PLATFORMS, "all"), default="all")
    ap.add_argument(
        "--refresh", action="store_true", help="resolve the latest versions into the lock file"
    )
    ap.add_argument("--dest", default=os.path.join(ROOT, "bin"))
    ap.add_argument("--cache", default=os.path.join(ROOT, ".cache", "downloads"))
    ap.add_argument("--sources", metavar="DIR", help="also download FFmpeg source to DIR")
    ap.add_argument("--lock-only", action="store_true", help="with --refresh: only update the lock")
    args = ap.parse_args(argv)

    os.makedirs(args.cache, exist_ok=True)
    if args.refresh:
        lock = refresh_lock(args.cache)
        if args.lock_only:
            return 0
    else:
        with open(LOCK, encoding="utf-8") as fh:
            lock = json.load(fh)
    plats = PLATFORMS if args.platform == "all" else (args.platform,)
    for plat in plats:
        install_platform(lock, plat, args.cache, args.dest)
    if args.sources:
        fetch_sources(lock, args.sources)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
