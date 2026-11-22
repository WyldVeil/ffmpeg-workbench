# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for WyldVeil's Media Editor (Windows and Linux).

Build with ``python packaging/build.py`` (preferred), or directly:

    pyinstaller --noconfirm --clean packaging/app.spec

The third-party tools must first be downloaded into bin/<platform>/ with
``python scripts/fetch_binaries.py --platform <platform>``.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from core import APP_VERSION  # noqa: E402
from core.hardware import platform_tag  # noqa: E402

IS_WIN = sys.platform.startswith("win")
PLAT = platform_tag()
NAME = "WyldVeilMediaEditor" if IS_WIN else "wyldveil-media-editor"

datas = [
    (os.path.join(ROOT, "locale"), "locale"),
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), "."),
]
for opt in ("LICENSE", "EULA.md", "README.md", "CHANGELOG.md", "AGENTS.md", "skin.zip"):
    if os.path.isfile(os.path.join(ROOT, opt)):
        datas.append((os.path.join(ROOT, opt), "."))
if os.path.isdir(os.path.join(ROOT, "soundfonts")):
    datas.append((os.path.join(ROOT, "soundfonts"), "soundfonts"))

# Bundled tools for THIS platform only, keeping the folder layout that
# core/hardware.py searches (bin/<platform>/ffmpeg/bin, bin/<platform>).
# They are added to COLLECT as an unanalysed Tree: PyInstaller 6 otherwise
# "helpfully" resolves the bundled FFmpeg's libraries against the build
# machine's system copies and drags hundreds of MB of duplicates in.
bin_src = os.path.join(ROOT, "bin", PLAT)
if not os.path.isdir(bin_src):
    raise SystemExit(f"{bin_src} is missing - run scripts/fetch_binaries.py --platform {PLAT}")
tools_tree = Tree(bin_src, prefix=os.path.join("bin", PLAT), typecode="DATA")

# tkinterdnd2 ships its own PyInstaller hook (collects only this platform's tkdnd).

hiddenimports = (collect_submodules("tabs") + collect_submodules("core") + collect_submodules("shell")
                 + ["tkinterdnd2"])

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "tests", "numpy.tests", "IPython", "matplotlib"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, "assets", "icons", "app.ico" if IS_WIN else "app.png"),
    version=os.path.join(ROOT, "packaging", "windows", "version_info.txt") if IS_WIN else None,
)

# Windows: the app above has no console, so commands typed in a terminal get a
# companion with one - WyldVeilMediaEditor-cli.exe (same files; main.py sees
# the "-cli" name and runs the command line, see AGENTS.md).
extra_exes = []
if IS_WIN:
    extra_exes.append(EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=NAME + "-cli",
        debug=False,
        strip=False,
        upx=False,
        console=True,
        icon=os.path.join(ROOT, "assets", "icons", "app.ico"),
        version=os.path.join(ROOT, "packaging", "windows", "version_info.txt"),
    ))

coll = COLLECT(
    exe,
    *extra_exes,
    a.binaries,
    a.datas,
    tools_tree,
    strip=False,
    upx=False,
    name=NAME,
)
