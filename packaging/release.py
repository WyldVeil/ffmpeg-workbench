"""
packaging/release.py  -  Build the release files (same formats as earlier releases)

    python packaging/release.py --linux
        wyldveils-media-editor_<v>_debian_amd64.deb, ..._ubuntu_amd64.deb,
        wyldveils-media-editor-<v>-arch.tar.gz (PKGBUILD + install.sh),
        wyldveils-media-editor-<v>-fedora.tar.gz (install.sh + .spec)

    python packaging/release.py --windows --python-embed DIR [--iscc "wine ...ISCC.exe"]
        WyldVeils-Media-Editor_<v>_Windows_Setup.exe

Linux packages run the app on the system's Python (3.10 or newer, with Tk);
Windows gets an embeddable Python (DIR: python.exe, python3XX.dll, Lib,
tcl/tk and _tkinter - the layout earlier releases used).  Both include the
pinned tools fetched by scripts/fetch_binaries.py (FFmpeg, yt-dlp, Deno,
whisper.cpp, FluidSynth, upscaling shaders, SoundFont), so every system runs
the tested versions.  Output goes to dist/release/.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import APP_VERSION  # noqa: E402

PKG = "wyldveils-media-editor"
APP_ID = "io.github.wyldveil.MediaEditor"
OUT = os.path.join(ROOT, "dist", "release")
MAINTAINER = "WyldVeil <50123619+WyldVeil@users.noreply.github.com>"
SUMMARY = "Video and audio editing suite - 80 tools, all on your computer"
DESCRIPTION = ("A free video and audio toolkit: cut, join, convert, compress, subtitle (offline speech\n"
               "recognition), upscale, colour-grade, stabilise, clean up sound, download, record the\n"
               "screen and much more. Bundles FFmpeg 9, yt-dlp, whisper.cpp and Deno.")
APP_FILES = ["main.py", "pyproject.toml", "skin.zip", "AGENTS.md", "README.md", "THIRD_PARTY_NOTICES.md",
             "CHANGELOG.md", "core", "tabs", "assets", "locale"]
SKIP = ("__pycache__", ".pyc", ".pyo", ".part", "settings.json", "skin.json")
MTIME = int(time.time())
#: Releases up to 1.16.0 used another numbering; with epoch 1, package managers treat 1.2.0 and later as
#: newer than those (an upgrade, not a downgrade).  Shown as "1:1.2.0" by apt/pacman/dnf.
EPOCH = 1


def app_tree(plat: str) -> list:
    """[(source path, path inside the app folder)] for every file to ship."""
    out = []
    for item in APP_FILES:
        src = os.path.join(ROOT, item)
        if os.path.isfile(src):
            out.append((src, item))
        elif os.path.isdir(src):
            for dirpath, dirs, files in os.walk(src):
                dirs[:] = sorted(d for d in dirs if not any(s in d for s in SKIP))
                for f in sorted(files):
                    if any(f.endswith(s) or f == s for s in SKIP):
                        continue
                    full = os.path.join(dirpath, f)
                    out.append((full, os.path.relpath(full, ROOT).replace(os.sep, "/")))
    if plat.startswith("linux"):
        libs = linux_libs()
        for dirpath, dirs, files in os.walk(libs):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                if not any(s in full for s in ("__pycache__", ".dist-info" + os.sep + "RECORD")):
                    out.append((full, "libs/" + os.path.relpath(full, libs).replace(os.sep, "/")))
    tools = os.path.join(ROOT, "bin", plat)
    if not os.path.isdir(os.path.join(tools, "ffmpeg")):
        raise SystemExit(f"{tools} is missing - run: python scripts/fetch_binaries.py --platform {plat}")
    for dirpath, dirs, files in os.walk(tools):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            out.append((full, "bin/" + os.path.relpath(full, os.path.join(ROOT, "bin")).replace(os.sep, "/")))
    return out


_LINUX_LIBS = None


def linux_libs() -> str:
    """Pure-Python add-ons shipped with the Linux packages (drag and drop).  The
    ones with compiled parts (online voices) depend on the system's Python
    version and are installed on first use instead (into a per-user folder)."""
    global _LINUX_LIBS
    if _LINUX_LIBS is None:
        target = os.path.join(ROOT, "build", "release-linux-libs")
        shutil.rmtree(target, ignore_errors=True)
        req = os.path.join(ROOT, "build", "release-linux-libs.txt")
        os.makedirs(os.path.dirname(req), exist_ok=True)
        with open(os.path.join(ROOT, "packaging", "bundled-libs.txt"), encoding="utf-8") as fh:
            text = fh.read()
        block = text[text.index("tkinterdnd2=="):].split("\n    # via")[0]
        with open(req, "w", encoding="utf-8") as fh:
            fh.write(block + "\n")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "--target", target, "--no-deps", "--only-binary=:all:", "--require-hashes", "-r", req],
                       check=True)
        _LINUX_LIBS = target
    return _LINUX_LIBS


def _add_bytes(tf: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), mode, MTIME
    info.uname = info.gname = "root"
    tf.addfile(info, io.BytesIO(data))


def _add_file(tf: tarfile.TarFile, src: str, name: str) -> None:
    info = tf.gettarinfo(src, arcname=name)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = MTIME
    executable = bool(os.stat(src).st_mode & 0o111) or name.startswith("bin/") and "." not in os.path.basename(src)
    info.mode = 0o755 if executable else 0o644
    with open(src, "rb") as fh:
        tf.addfile(info, fh)


def _dirs_for(paths) -> list:
    seen = set()
    for p in paths:
        parts = p.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            seen.add("/".join(parts[:i]))
    return sorted(seen)


# ── Linux ─────────────────────────────────────────────────────────────────────

LAUNCHER = f"""#!/bin/sh
# WyldVeil's Media Editor - runs on the system's Python 3.10+ (with Tk).
exec python3 /opt/{PKG}/main.py "$@"
"""


def desktop_entry() -> str:
    with open(os.path.join(ROOT, "packaging", "linux", f"{APP_ID}.desktop"), encoding="utf-8") as fh:
        text = fh.read()
    lines = []
    for line in text.splitlines():
        if line.startswith("Exec="):
            line = f"Exec={PKG} %F"
        elif line.startswith("TryExec="):
            line = f"TryExec={PKG}"
        elif line.startswith("Name="):
            line = "Name=WyldVeil's Media Editor"
        lines.append(line)
    return "\n".join(lines) + "\n"


def system_files() -> list:
    """[(bytes, path, mode)] outside /opt: launcher, menu entry, icons, AppStream data."""
    files = [(LAUNCHER.encode(), f"usr/bin/{PKG}", 0o755),
             (desktop_entry().encode(), f"usr/share/applications/{APP_ID}.desktop", 0o644)]
    icons = os.path.join(ROOT, "assets", "icons")
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        src = os.path.join(icons, f"app_{size}.png")
        if os.path.isfile(src):
            with open(src, "rb") as fh:
                files.append((fh.read(), f"usr/share/icons/hicolor/{size}x{size}/apps/{APP_ID}.png", 0o644))
    with open(os.path.join(icons, "app.svg"), "rb") as fh:
        files.append((fh.read(), f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg", 0o644))
    with open(os.path.join(ROOT, "packaging", "linux", f"{APP_ID}.metainfo.xml"), "rb") as fh:
        files.append((fh.read(), f"usr/share/metainfo/{APP_ID}.metainfo.xml", 0o644))
    return files


def _ar(out: str, members: list) -> None:
    """A Debian package is an 'ar' archive of these members."""
    with open(out, "wb") as fh:
        fh.write(b"!<arch>\n")
        for name, data in members:
            header = (f"{name + '/':<16}{MTIME:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}").encode() + b"`\n"
            fh.write(header)
            fh.write(data)
            if len(data) % 2:
                fh.write(b"\n")


_DEB_DATA = None


def deb_data() -> tuple:
    """(data.tar.xz path, md5sums text, installed bytes) - shared by the Debian and
    Ubuntu packages; compressed with a multi-threaded xz when available."""
    global _DEB_DATA
    if _DEB_DATA is not None:
        return _DEB_DATA
    tree = app_tree("linux-x64")
    raw = os.path.join(ROOT, "build", "release-deb-data.tar")
    md5 = []
    size = 0
    with tarfile.open(raw, mode="w") as tf:
        names = [f"opt/{PKG}/{rel}" for _src, rel in tree] + [p for _d, p, _m in system_files()]
        for d in _dirs_for(names):
            info = tarfile.TarInfo("./" + d)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, MTIME
            tf.addfile(info)
        for src, rel in tree:
            _add_file(tf, src, f"./opt/{PKG}/{rel}")
            with open(src, "rb") as fh:
                md5.append(f"{hashlib.md5(fh.read()).hexdigest()}  opt/{PKG}/{rel}")
            size += os.path.getsize(src)
        for data, path, mode in system_files():
            _add_bytes(tf, "./" + path, data, mode)
            md5.append(f"{hashlib.md5(data).hexdigest()}  {path}")
            size += len(data)
    if shutil.which("xz"):
        subprocess.run(["xz", "-T0", "-6", "-f", raw], check=True)
    else:
        import lzma
        with open(raw, "rb") as src, lzma.open(raw + ".xz", "wb", preset=6) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        os.remove(raw)
    _DEB_DATA = (raw + ".xz", "\n".join(md5) + "\n", size)
    return _DEB_DATA


def build_deb(distro: str) -> str:
    data_path, md5sums, size = deb_data()
    control = (f"Package: {PKG}\nVersion: {EPOCH}:{APP_VERSION}\nArchitecture: amd64\nMaintainer: {MAINTAINER}\n"
               f"Installed-Size: {size // 1024}\nDepends: python3 (>= 3.10), python3-tk\n"
               f"Recommends: python3-pip, espeak-ng, fluidsynth, xdg-utils\nSection: video\nPriority: optional\n"
               f"Homepage: https://github.com/WyldVeil/{PKG}\n"
               f"Description: {SUMMARY}\n" + "".join(f" {line}\n" for line in DESCRIPTION.splitlines()))
    ctrl_buf = io.BytesIO()
    with tarfile.open(fileobj=ctrl_buf, mode="w:gz") as tf:
        _add_bytes(tf, "./control", control.encode())
        _add_bytes(tf, "./md5sums", md5sums.encode())
    with open(data_path, "rb") as fh:
        data = fh.read()
    out = os.path.join(OUT, f"{PKG}_{APP_VERSION}_{distro}_amd64.deb")
    _ar(out, [("debian-binary", b"2.0\n"), ("control.tar.gz", ctrl_buf.getvalue()), ("data.tar.xz", data)])
    return out


INSTALL_SH = """#!/bin/sh
# Installs WyldVeil's Media Editor {v} system-wide (asks for your password).
set -e
cd "$(dirname "$0")"
{deps}
sudo rm -rf /opt/{pkg}
sudo mkdir -p /opt/{pkg}
sudo cp -r app/. /opt/{pkg}/
sudo cp -r files/usr/. /usr/
command -v update-desktop-database >/dev/null 2>&1 && sudo update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && sudo gtk-update-icon-cache -q /usr/share/icons/hicolor || true
echo "Installed. Start it from your applications menu, or run: {pkg}"
"""

UNINSTALL_SH = """#!/bin/sh
# Removes WyldVeil's Media Editor (your settings in ~/.config are kept).
set -e
sudo rm -rf /opt/{pkg}
sudo rm -f /usr/bin/{pkg} /usr/share/applications/{app_id}.desktop /usr/share/metainfo/{app_id}.metainfo.xml
sudo find /usr/share/icons/hicolor -name '{app_id}.*' -delete
echo "Removed."
"""


def _tarball(distro: str, extras: dict, deps: str) -> str:
    prefix = f"{PKG}-{APP_VERSION}"
    out = os.path.join(OUT, f"{PKG}-{APP_VERSION}-{distro}.tar.gz")
    raw = out[:-3]
    with tarfile.open(raw, "w") as tf:
        for src, rel in app_tree("linux-x64"):
            _add_file(tf, src, f"{prefix}/app/{rel}")
        for data, path, mode in system_files():
            _add_bytes(tf, f"{prefix}/files/{path}", data, mode)
        _add_bytes(tf, f"{prefix}/install.sh",
                   INSTALL_SH.format(v=APP_VERSION, pkg=PKG, deps=deps).encode(), 0o755)
        _add_bytes(tf, f"{prefix}/uninstall.sh", UNINSTALL_SH.format(pkg=PKG, app_id=APP_ID).encode(), 0o755)
        for name, text in extras.items():
            _add_bytes(tf, f"{prefix}/{name}", text.encode())
    if shutil.which("pigz"):                    # multi-threaded gzip
        subprocess.run(["pigz", "-6", "-f", raw], check=True)
    else:
        import gzip
        with open(raw, "rb") as src, gzip.open(out, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        os.remove(raw)
    return out


def build_arch() -> str:
    pkgbuild = f"""# Maintainer: {MAINTAINER}
pkgname={PKG}
pkgver={APP_VERSION}
pkgrel=1
epoch={EPOCH}
pkgdesc="{SUMMARY}"
arch=('x86_64')
url="https://github.com/WyldVeil/{PKG}"
license=('custom')
depends=('python' 'tk')
optdepends=('python-pip: online voices for TTS Voice-Over' 'espeak-ng: offline voices'
            'fluidsynth: MIDIfier playback' 'xdg-utils: open folders')
options=('!strip')

package() {{
    install -d "$pkgdir/opt/{PKG}"
    cp -r "$srcdir/../app/." "$pkgdir/opt/{PKG}/"
    cp -r "$srcdir/../files/usr" "$pkgdir/"
}}
"""
    return _tarball("arch", {"PKGBUILD": pkgbuild},
                    "command -v pacman >/dev/null 2>&1 && sudo pacman -S --needed --noconfirm python tk || true")


def build_fedora() -> str:
    spec = f"""Name:           {PKG}
Epoch:          {EPOCH}
Version:        {APP_VERSION}
Release:        1%{{?dist}}
Summary:        {SUMMARY}
License:        Custom
URL:            https://github.com/WyldVeil/{PKG}
Requires:       python3 >= 3.10, python3-tkinter
Recommends:     python3-pip, espeak-ng, fluidsynth
%global debug_package %{{nil}}
%global __os_install_post %{{nil}}

%description
{DESCRIPTION}

%install
mkdir -p %{{buildroot}}/opt/{PKG}
cp -r %{{_sourcedir}}/app/. %{{buildroot}}/opt/{PKG}/
cp -r %{{_sourcedir}}/files/usr %{{buildroot}}/

%files
/opt/{PKG}
/usr/bin/{PKG}
/usr/share/applications/{APP_ID}.desktop
/usr/share/icons/hicolor/*/apps/{APP_ID}.*
/usr/share/metainfo/{APP_ID}.metainfo.xml
"""
    return _tarball("fedora", {f"{PKG}.spec": spec},
                    "command -v dnf >/dev/null 2>&1 && sudo dnf install -y python3 python3-tkinter || true")


# ── Windows ───────────────────────────────────────────────────────────────────

def build_windows(python_embed: str, iscc: str) -> str:
    if not os.path.isfile(os.path.join(python_embed, "pythonw.exe")) or \
            not os.path.isfile(os.path.join(python_embed, "_tkinter.pyd")):
        raise SystemExit(f"{python_embed}: needs an embeddable Python with tkinter (pythonw.exe, _tkinter.pyd, tcl)")
    stage = os.path.join(ROOT, "build", "release-windows")
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(python_embed, os.path.join(stage, "python"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for src, rel in app_tree("windows-x64"):
        dst = os.path.join(stage, "app", *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    # Python add-ons (online voices, system certificates, SOCKS proxies) as Windows wheels
    ver = "".join(f for f in os.listdir(python_embed) if f.startswith("python3") and f.endswith(".dll")
                  and f != "python3.dll")[len("python"):-len(".dll")]
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--target", os.path.join(stage, "app", "libs"), "--platform", "win_amd64",
                    "--python-version", f"{ver[0]}.{ver[1:]}", "--only-binary=:all:", "--no-deps",
                    "--require-hashes", "-r", os.path.join(ROOT, "packaging", "bundled-libs.txt")], check=True)
    with open(os.path.join(stage, "Launch.bat"), "w", encoding="utf-8", newline="\r\n") as fh:
        # kept for shortcuts made by earlier releases (they pointed here)
        fh.write('@echo off\r\nstart "" "%~dp0python\\pythonw.exe" "%~dp0app\\main.py" %*\r\n')
    with open(os.path.join(stage, "wme-cli.bat"), "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write('@echo off\r\nrem Command line for scripts and AI assistants - see app\\AGENTS.md\r\n'
                 '"%~dp0python\\python.exe" "%~dp0app\\main.py" --cli %*\r\n')
    iss = os.path.join(ROOT, "packaging", "windows", "release.iss")
    argv = shlex.split(iscc) + [f"/DAppVersion={APP_VERSION}", f"/DStageDir={_win_path(stage)}",
                                f"/O{_win_path(OUT)}", _win_path(iss)]
    subprocess.run(argv, check=True)
    return os.path.join(OUT, f"WyldVeils-Media-Editor_{APP_VERSION}_Windows_Setup.exe")


def _win_path(path: str) -> str:
    """Paths for ISCC (a Windows program, possibly running under Wine)."""
    if os.name == "nt":
        return path
    return "Z:" + os.path.abspath(path).replace("/", "\\")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--linux", action="store_true")
    ap.add_argument("--windows", action="store_true")
    ap.add_argument("--python-embed", help="embeddable Python folder with tkinter (Windows)")
    ap.add_argument("--iscc", default="ISCC.exe", help='Inno Setup compiler command, e.g. "wine C:/.../ISCC.exe"')
    args = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    built = []
    if args.linux:
        built += [build_deb("debian"), build_deb("ubuntu"), build_arch(), build_fedora()]
    if args.windows:
        if not args.python_embed:
            ap.error("--windows needs --python-embed")
        built.append(build_windows(args.python_embed, args.iscc))
    if not built:
        ap.error("choose --linux and/or --windows")
    for path in built:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        print(f"{os.path.getsize(path) / 1e6:8.1f} MB  {h.hexdigest()}  {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
