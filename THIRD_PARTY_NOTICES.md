# Third-party software

WyldVeil's Media Editor runs several independent programs and ships some
third-party assets. Each one keeps its own licence. Nothing in the editor's
own licence restricts the rights those licences give you.

The exact versions and download checksums for each release are recorded in
`bin/<platform>/VERSIONS.json`. The full licence texts ship in
`bin/<platform>/licenses/` and `assets/fonts/licenses/`.

## Bundled programs

| Component | Version | Licence | Upstream |
|-----------|---------|---------|----------|
| FFmpeg (`ffmpeg`, `ffprobe`, `ffplay`), BtbN "gpl-shared" build | 9.0.2 (release/9.0 branch, `n9.0.2-39-g7adfbe40dc`) | GPL-3.0-or-later | https://ffmpeg.org · builds: https://github.com/FFmpeg-Builds/FFmpeg-Builds |
| yt-dlp (stand-alone executable) | 2026.10.26 | Unlicense; the stand-alone executable is a GPL-3.0-or-later combined work (see `THIRD_PARTY_LICENSES.txt`) | https://github.com/yt-dlp/yt-dlp |
| Deno (JavaScript runtime that yt-dlp needs for YouTube) | 2.9.8 | MIT | https://deno.com |
| FluidSynth (Windows build only; Linux uses the system package) | 2.6.2 | LGPL-2.1-or-later; bundled SDL3 (zlib) and libsndfile (LGPL-2.1-or-later) | https://www.fluidsynth.org |

The editor starts these as separate processes. It does not link against
them.

### FFmpeg source code (GPL)

These FFmpeg builds are licensed under the GNU General Public License,
version 3 or later. You can get the complete corresponding source code here:

* FFmpeg source at the exact commit:
  https://github.com/FFmpeg/FFmpeg/commit/7adfbe40dc
  (tarball: https://github.com/FFmpeg/FFmpeg/archive/7adfbe40dc.tar.gz)
* Build scripts, including every library version and configure option:
  https://github.com/FFmpeg-Builds/FFmpeg-Builds/tree/5031c6f1082414e53688d39e521849905071070d
  (release `autobuild-2026-10-31-12-59`)
* Each GitHub release of this editor also attaches the FFmpeg source tarball
  as a download.

The editor's maintainer will also provide this source on request for at
least three years from the date of each release.

## Python components in the packaged builds

| Component | Licence |
|-----------|---------|
| CPython runtime | Python Software Foundation License |
| Tcl/Tk | Tcl/Tk licence (BSD-style) |
| tkinterdnd2 | MIT |
| tkdnd (native drag-and-drop extension used by tkinterdnd2) | BSD-style (tkdnd licence) |
| PyInstaller bootloader | GPL-2.0 with the PyInstaller bootloader exception, which allows use in any program |

## Fonts (`assets/fonts`)

| Font | Licence |
|------|---------|
| Anton, Bangers, Bebas Neue, Bungee, Inter, Lato, Montserrat, Open Sans, Poppins, Raleway, Roboto (Black) | SIL Open Font License 1.1 |
| Luckiest Guy | Apache License 2.0 |

Each font's licence text and copyright notice are in
`assets/fonts/licenses/`.

## Optional components downloaded on demand

Some tools offer to download extra components the first time you use them,
such as text-to-speech voices or speech-recognition and audio-separation
models. These are downloaded from their publishers and installed into your
per-user data folder, and they stay under their publishers' licences. Each
download prompt names the licence.
