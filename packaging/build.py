#!/usr/bin/env python3
"""
packaging/build.py - Build a release for the current platform.

    python packaging/build.py              # fetch tools, freeze, package
    python packaging/build.py --skip-fetch # reuse bin/<platform>/ as is

Outputs (in dist/):
  Windows : WyldVeilMediaEditor-<ver>-windows-x64.zip
            WyldVeilMediaEditor-<ver>-windows-x64-setup.exe   (if Inno Setup's ISCC is installed)
  Linux   : wyldveil-media-editor-<ver>-linux-x64.tar.gz     (with install.sh)
            WyldVeil_Media_Editor-<ver>-x86_64.AppImage       (if appimagetool is installed)
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import APP_VERSION  # noqa: E402
from core.hardware import platform_tag  # noqa: E402

IS_WIN = sys.platform.startswith("win")
PLAT = platform_tag()
NAME = "WyldVeilMediaEditor" if IS_WIN else "wyldveil-media-editor"
DIST = os.path.join(ROOT, "dist")
APP_ID = "io.github.wyldveil.MediaEditor"


def run(cmd: list, **kw) -> None:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def write_windows_version_info() -> None:
    """Version resource for the .exe (shown in Explorer's Properties)."""
    nums = [int(x) for x in (APP_VERSION.split(".") + ["0", "0", "0"])[:3]] + [0]
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(nums)}, prodvers={tuple(nums)}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'WyldVeil'),
      StringStruct('FileDescription', 'WyldVeil Media Editor'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'WyldVeil Media Editor'),
      StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""
    path = os.path.join(ROOT, "packaging", "windows", "version_info.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def freeze() -> str:
    if IS_WIN:
        write_windows_version_info()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", DIST, "--workpath", os.path.join(ROOT, "build", "pyinstaller"),
         os.path.join(ROOT, "packaging", "app.spec")])
    out = os.path.join(DIST, NAME)
    bundle_python_libs(out)
    shutil.copy2(os.path.join(ROOT, "AGENTS.md"), os.path.join(out, "AGENTS.md"))   # next to the program too
    if not IS_WIN:
        bundle_libgomp(os.path.join(out, "_internal", "bin", PLAT, "whisper"))
    # PyInstaller copies data files without the executable bit on POSIX.
    if not IS_WIN:
        for dirpath, _dirs, files in os.walk(os.path.join(out, "_internal", "bin")):
            for f in files:
                p = os.path.join(dirpath, f)
                if not f.endswith((".txt", ".json", ".md", ".so")) and ".so." not in f:
                    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return out


def bundle_python_libs(app_dir: str) -> None:
    """Python packages loaded at run time from ``_internal/libs`` (the folder
    core/deps.py puts on sys.path): edge-tts for the online TTS voices and its
    dependencies.  They are installed as plain files from a hash-locked list
    (packaging/bundled-libs.txt, made with ``uv pip compile --universal
    --generate-hashes``) rather than frozen into the archive, so the
    LGPL-3.0 edge-tts stays replaceable by the user."""
    target = os.path.join(app_dir, "_internal", "libs")
    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
         "--target", target, "--require-hashes", "--only-binary=:all:", "--no-deps",
         "-r", os.path.join(ROOT, "packaging", "bundled-libs.txt")])


def bundle_libgomp(whisper_dir: str) -> None:
    """whisper.cpp's CPU backends need GCC's OpenMP runtime (libgomp.so.1).
    Most desktops have it, but ship the build host's copy beside them (their
    RUNPATH is $ORIGIN) so speech-to-text works everywhere.  libgomp is
    covered by the GCC Runtime Library Exception."""
    if not os.path.isdir(whisper_dir):
        return
    found = None
    try:
        out = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "libgomp.so.1 " in line and "x86-64" in line:
                found = line.split("=>")[-1].strip()
                break
    except OSError:
        pass
    for cand in (found, "/usr/lib/x86_64-linux-gnu/libgomp.so.1", "/usr/lib/libgomp.so.1",
                 "/usr/lib64/libgomp.so.1"):
        if cand and os.path.isfile(cand):
            shutil.copy2(os.path.realpath(cand), os.path.join(whisper_dir, "libgomp.so.1"))
            print("bundled", cand)
            return
    print("warning: libgomp.so.1 not found - speech-to-text will rely on the system copy")


def smoke_test(app_dir: str) -> None:
    exe = os.path.join(app_dir, NAME + (".exe" if IS_WIN else ""))
    env = dict(os.environ, CROSSFADER_SELFTEST="1")
    r = subprocess.run([exe, "--self-test"], capture_output=True, text=True, timeout=180, env=env)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(r.stderr[-4000:])
        raise SystemExit(f"self-test failed ({r.returncode})")
    # the command line (what AGENTS.md tells assistants to use) must work without a window
    cli_exe = os.path.join(app_dir, NAME + ("-cli.exe" if IS_WIN else ""))
    argv = [cli_exe] + ([] if IS_WIN else ["--cli"]) + ["tools", "--json"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=env)
    if r.returncode != 0 or '"ok": true' not in r.stdout:
        print(r.stdout[-1000:], r.stderr[-2000:])
        raise SystemExit(f"command-line self-test failed ({r.returncode})")
    print("command line: ok")


def package_windows(app_dir: str) -> None:
    zip_path = os.path.join(DIST, f"{NAME}-{APP_VERSION}-{PLAT}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for dirpath, _dirs, files in os.walk(app_dir):
            for f in files:
                full = os.path.join(dirpath, f)
                zf.write(full, os.path.join(NAME, os.path.relpath(full, app_dir)))
    print("built", zip_path)
    iscc = shutil.which("ISCC") or shutil.which("iscc") or \
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if os.path.isfile(iscc) or shutil.which(str(iscc)):
        run([iscc, f"/DAppVersion={APP_VERSION}", f"/DSourceDir={app_dir}", f"/DOutputDir={DIST}",
             os.path.join(ROOT, "packaging", "windows", "installer.iss")])
    else:
        print("Inno Setup not found - skipping the installer (zip only).")


def package_linux(app_dir: str) -> None:
    linux = os.path.join(ROOT, "packaging", "linux")
    # 1) portable tarball with a per-user installer script
    stage = os.path.join(DIST, f"{NAME}-{APP_VERSION}-{PLAT}")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    shutil.copytree(app_dir, os.path.join(stage, NAME), symlinks=True)
    for f in ("install.sh", f"{APP_ID}.desktop", f"{APP_ID}.metainfo.xml"):
        shutil.copy2(os.path.join(linux, f), stage)
    shutil.copy2(os.path.join(ROOT, "assets", "icons", "app.svg"), os.path.join(stage, f"{APP_ID}.svg"))
    os.chmod(os.path.join(stage, "install.sh"), 0o755)
    tar_path = stage + ".tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(stage, arcname=os.path.basename(stage))
    print("built", tar_path)

    # 2) AppImage
    tool = shutil.which("appimagetool")
    if not tool:
        print("appimagetool not found - skipping AppImage.")
        return
    appdir = os.path.join(DIST, "AppDir")
    if os.path.isdir(appdir):
        shutil.rmtree(appdir)
    shutil.copytree(app_dir, os.path.join(appdir, "usr", "lib", NAME), symlinks=True)
    shutil.copy2(os.path.join(linux, "AppRun"), os.path.join(appdir, "AppRun"))
    os.chmod(os.path.join(appdir, "AppRun"), 0o755)
    shutil.copy2(os.path.join(linux, f"{APP_ID}.desktop"), os.path.join(appdir, f"{APP_ID}.desktop"))
    shutil.copy2(os.path.join(ROOT, "assets", "icons", "app_256.png"), os.path.join(appdir, f"{APP_ID}.png"))
    for size in (16, 32, 48, 64, 128, 256, 512):
        d = os.path.join(appdir, "usr", "share", "icons", "hicolor", f"{size}x{size}", "apps")
        os.makedirs(d, exist_ok=True)
        shutil.copy2(os.path.join(ROOT, "assets", "icons", f"app_{size}.png"), os.path.join(d, f"{APP_ID}.png"))
    meta = os.path.join(appdir, "usr", "share", "metainfo")
    os.makedirs(meta, exist_ok=True)
    shutil.copy2(os.path.join(linux, f"{APP_ID}.metainfo.xml"), meta)
    out = os.path.join(DIST, f"WyldVeil_Media_Editor-{APP_VERSION}-x86_64.AppImage")
    run([tool, "--comp", "zstd", appdir, out], env=dict(os.environ, ARCH="x86_64"))
    print("built", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--skip-test", action="store_true")
    args = ap.parse_args()
    if not args.skip_fetch:
        run([sys.executable, os.path.join(ROOT, "scripts", "fetch_binaries.py"), "--platform", PLAT])
    os.makedirs(DIST, exist_ok=True)
    app_dir = freeze()
    if not args.skip_test:
        smoke_test(app_dir)
    if IS_WIN:
        package_windows(app_dir)
    else:
        package_linux(app_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
