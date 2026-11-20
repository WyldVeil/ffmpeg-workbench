"""
packaging/release.py  -  Build the release files (same formats as earlier releases)

    python packaging/release.py --linux
        wyldveils-media-editor_<v>_debian_amd64.deb, ..._ubuntu_amd64.deb,
        wyldveils-media-editor-<v>-arch.tar.gz (PKGBUILD + install.sh),
        wyldveils-media-editor-<v>-fedora.tar.gz (install.sh + .spec)

    python packaging/release.py --windows --python-embed DIR [--iscc "wine ...ISCC.exe"]
        WyldVeils-Media-Editor_<v>_Windows_Setup.exe

Both bring their own Python, so nothing else has to be installed first:
Linux packages get CPython from python-build-standalone with its Tk rebuilt
for anti-aliased fonts (see linux_python(); needs podman or docker once),
Windows gets an embeddable Python (DIR: python.exe, python3XX.dll, Lib,
tcl/tk and _tkinter - the layout earlier releases used).  Both include the
pinned tools fetched by scripts/fetch_binaries.py (FFmpeg, yt-dlp, Deno,
whisper.cpp, FluidSynth, upscaling shaders, SoundFont) and the Python add-ons
in packaging/bundled-libs.txt, so every system runs the tested versions.
Output goes to dist/release/.
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
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from fetch_binaries import download  # noqa: E402

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

#: Python bundled with the Linux packages: CPython from python-build-standalone, which runs on any
#: distribution with glibc 2.17 or newer and has OpenSSL 3, SQLite and the rest built in.  Its Tk
#: draws only the X server's old bitmap fonts, so linux_python() swaps in the same Tk version built
#: with Xft (packaging/linux/build-tk.sh); the licence texts come from the same build's full archive.
PBS = "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/"
LINUX_PYTHON = ("cpython-3.14.10+20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
                "c2ff2e97422f5d3896a16e0317fd212ba977bbf2688d93a6f7a03d817506bd30")
LINUX_PYTHON_LICENSES = ("cpython-3.14.10+20261110-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst",
                         "4730c0dc79e7eecbe54a323d587fdeaab621094fe09b5827167d69045115fcbc")
#: The Tk that Python was built with (python-build-standalone's pythonbuild/downloads.json).
TK_VERSION = "9.0.4"
TK_SOURCES = {f"tcl{TK_VERSION}-src.tar.gz": "d0aed49230bc02a65c1e0229e65f34590a4b037ec40d546f32573b467f7551ea",
              f"tk{TK_VERSION}-src.tar.gz": "d7a146d2917eb8b5cc95276dbf0e3d03c7464d2b19c1675357857c989301dbb4"}
#: Tar format for every archive: dpkg can't read the PAX headers Python's default format uses for
#: long paths (the bundled Python has plenty), and GNU tar, bsdtar and dpkg all read GNU format.
TAR_FORMAT = tarfile.GNU_FORMAT


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
        for folder, prefix in ((linux_libs(), "libs/"), (linux_python(), "python/")):
            for dirpath, dirs, files in os.walk(folder):
                dirs.sort()
                for f in sorted(files):
                    full = os.path.join(dirpath, f)
                    if prefix == "python/" or not any(s in full for s in ("__pycache__", ".dist-info" + os.sep + "RECORD")):
                        out.append((full, prefix + os.path.relpath(full, folder).replace(os.sep, "/")))
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
_LINUX_PYTHON = None
DOWNLOADS = os.path.join(ROOT, "build", "downloads")


def linux_python() -> str:
    """The Python folder the Linux packages ship (as /opt/<pkg>/python): python-build-standalone's
    CPython with its Tk replaced by one that draws anti-aliased fonts.  Building that Tk takes a
    minute in an AlmaLinux 8 container (podman or docker) and is kept in build/ for next time."""
    global _LINUX_PYTHON
    if _LINUX_PYTHON is not None:
        return _LINUX_PYTHON
    name, sha = LINUX_PYTHON
    archive = download(PBS + name.replace("+", "%2B"), os.path.join(DOWNLOADS, name), sha)
    lic_name, lic_sha = LINUX_PYTHON_LICENSES
    lic_archive = download(PBS + lic_name.replace("+", "%2B"), os.path.join(DOWNLOADS, lic_name), lic_sha)
    stage = os.path.join(ROOT, "build", "release-linux-python")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    with tarfile.open(archive) as tf:
        tf.extractall(stage, filter="data")
    py = os.path.join(stage, "python")
    with tarfile.open(lic_archive, "r:zst") as tf:       # Python 3.14+
        tf.extractall(stage, members=[m for m in tf.getmembers() if m.name.startswith("python/licenses/")],
                      filter="data")
    # Not needed to run the app: the library for embedding Python in other programs (the
    # interpreter has it built in), headers, man pages and terminal descriptions, IDLE, and the
    # Berkeley DB module (dbm falls back to its SQLite and plain-file versions).
    # Licence texts for parts that are left out: Berkeley DB, Tix, and the X11 libraries that were
    # built into the replaced Tk (the new one uses the system's).
    for rel in ("include", "share", "lib/pkgconfig", "lib/python3.14/idlelib", "lib/python3.14/turtledemo",
                "licenses/LICENSE.bdb.txt", "licenses/LICENSE.tix.txt", "licenses/LICENSE.libX11.txt",
                "licenses/LICENSE.libXau.txt", "licenses/LICENSE.libxcb.txt"):
        target = os.path.join(py, rel)
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.lexists(target):
            os.remove(target)
    for folder, prefixes in (("lib", ("libpython3",)), ("bin", ("idle3", "pydoc3", "python3.14-config", "python3-config")),
                             ("lib/python3.14/lib-dynload", ("_dbm.",))):
        for f in os.listdir(os.path.join(py, folder)):
            if f.startswith(prefixes):
                os.remove(os.path.join(py, folder, f))
    tk_lib = f"libtcl9tk{TK_VERSION.rsplit('.', 1)[0]}.so"
    shutil.copy2(os.path.join(xft_tk(), tk_lib), os.path.join(py, "lib", tk_lib))
    needed = subprocess.run(["readelf", "-d", os.path.join(py, "lib", tk_lib)], capture_output=True, text=True,
                            check=True).stdout
    if "libXft.so.2" not in needed:
        raise SystemExit(f"{tk_lib} was built without Xft")
    with open(os.path.join(py, "licenses", "README.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"Python {name.split('-')[1].split('+')[0]} from python-build-standalone ({PBS.rsplit('/', 2)[-2]}),\n"
                 "https://github.com/astral-sh/python-build-standalone - these are the licences of the\n"
                 f"libraries built into it.  Tk {TK_VERSION} (lib/{tk_lib}) was rebuilt from the official\n"
                 "sources (https://www.tcl-lang.org) with Xft for anti-aliased fonts; it is under the\n"
                 "Tcl/Tk licence in LICENSE.tcl.txt.  CPython itself is under LICENSE.cpython.txt.\n")
    _LINUX_PYTHON = py
    return py


def xft_tk() -> str:
    """Folder with Tk built with Xft (packaging/linux/build-tk.sh), building it if needed."""
    out = os.path.join(ROOT, "build", f"tk-{TK_VERSION}-xft")
    if os.path.isfile(os.path.join(out, f"libtcl9tk{TK_VERSION.rsplit('.', 1)[0]}.so")):
        return out
    src = os.path.join(ROOT, "build", f"tk-{TK_VERSION}-src")
    for name, sha in TK_SOURCES.items():
        download(f"https://prdownloads.sourceforge.net/tcl/{name}", os.path.join(src, name), sha)
    shutil.copy2(os.path.join(ROOT, "packaging", "linux", "build-tk.sh"), src)
    engine = shutil.which("podman") or shutil.which("docker")
    if not engine:
        raise SystemExit("Building Tk for the Linux packages needs podman or docker")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    tmp = os.path.join(ROOT, "build", "container-tmp")      # not /tmp: image layers are big
    os.makedirs(tmp, exist_ok=True)
    subprocess.run([engine, "run", "--rm", "-v", f"{src}:/src:ro,Z", "-v", f"{out}:/out:Z",
                    "docker.io/library/almalinux:8", "sh", "/src/build-tk.sh", TK_VERSION],
                   check=True, env={**os.environ, "TMPDIR": tmp})
    return out


def linux_libs() -> str:
    """The Python add-ons (packaging/bundled-libs.txt: drag and drop, online voices, system
    certificates, SOCKS proxies) for the bundled Python, so nothing is downloaded on first use."""
    global _LINUX_LIBS
    if _LINUX_LIBS is None:
        target = os.path.join(ROOT, "build", "release-linux-libs")
        shutil.rmtree(target, ignore_errors=True)
        # Wheels for glibc 2.28 and older, like everything else in the packages
        platforms = [a for p in ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64",
                                 "manylinux_2_5_x86_64", "manylinux1_x86_64") for a in ("--platform", p)]
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "--target", target, *platforms, "--python-version", "3.14", "--implementation", "cp",
                        "--abi", "cp314", "--abi", "abi3", "--abi", "none",
                        "--only-binary=:all:", "--no-deps", "--require-hashes",
                        "-r", os.path.join(ROOT, "packaging", "bundled-libs.txt")], check=True)
        # tkinterdnd2 carries its native part for every system; keep the Linux x86-64 ones
        tkdnd = os.path.join(target, "tkinterdnd2", "tkdnd")
        for d in os.listdir(tkdnd):
            if not d.startswith("linux-x64"):
                shutil.rmtree(os.path.join(tkdnd, d))
        shutil.rmtree(os.path.join(target, "bin"), ignore_errors=True)    # console scripts for pip's Python
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
    if info.issym():                            # e.g. python/bin/python3 -> python3.14
        tf.addfile(info)
        return
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
# WyldVeil's Media Editor - runs on its own Python, in /opt/{PKG}/python.
exec /opt/{PKG}/python/bin/python3 -I /opt/{PKG}/main.py "$@"
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
    with tarfile.open(raw, mode="w", format=TAR_FORMAT) as tf:
        names = [f"opt/{PKG}/{rel}" for _src, rel in tree] + [p for _d, p, _m in system_files()]
        for d in _dirs_for(names):
            info = tarfile.TarInfo("./" + d)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, MTIME
            tf.addfile(info)
        for src, rel in tree:
            _add_file(tf, src, f"./opt/{PKG}/{rel}")
            if os.path.islink(src):
                continue
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
               f"Installed-Size: {size // 1024}\n"
               f"Depends: libc6 (>= 2.28), libx11-6, libxft2, libxcursor1, libfontconfig1, libfreetype6, zlib1g\n"
               f"Recommends: espeak-ng, fluidsynth, xdg-utils\nSection: video\nPriority: optional\n"
               f"Homepage: https://github.com/WyldVeil/{PKG}\n"
               f"Description: {SUMMARY}\n" + "".join(f" {line}\n" for line in DESCRIPTION.splitlines()))
    ctrl_buf = io.BytesIO()
    with tarfile.open(fileobj=ctrl_buf, mode="w:gz", format=TAR_FORMAT) as tf:
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
    with tarfile.open(raw, "w", format=TAR_FORMAT) as tf:
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
depends=('glibc' 'libx11' 'libxft' 'libxcursor' 'fontconfig' 'freetype2' 'zlib')
optdepends=('espeak-ng: offline voices for TTS Voice-Over'
            'fluidsynth: MIDIfier playback' 'xdg-utils: open folders')
options=('!strip')

package() {{
    install -d "$pkgdir/opt/{PKG}"
    cp -r "$srcdir/../app/." "$pkgdir/opt/{PKG}/"
    cp -r "$srcdir/../files/usr" "$pkgdir/"
}}
"""
    return _tarball("arch", {"PKGBUILD": pkgbuild},
                    "command -v pacman >/dev/null 2>&1 && sudo pacman -S --needed --noconfirm libxft libxcursor fontconfig || true")


def build_fedora() -> str:
    spec = f"""Name:           {PKG}
Epoch:          {EPOCH}
Version:        {APP_VERSION}
Release:        1%{{?dist}}
Summary:        {SUMMARY}
License:        Custom
URL:            https://github.com/WyldVeil/{PKG}
Requires:       libX11, libXft, libXcursor, fontconfig, freetype, zlib
Recommends:     espeak-ng, fluidsynth
# Python and Tk are inside the package (in /opt); don't derive dependencies from their libraries
AutoReqProv:    no
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
                    "command -v dnf >/dev/null 2>&1 && sudo dnf install -y libXft libXcursor fontconfig || true")


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
