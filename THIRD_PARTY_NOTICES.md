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
| whisper.cpp (`whisper-cli` and its ggml libraries, speech-to-text) | build b5454 (1.9.5) | MIT | https://github.com/ggml-org/whisper.cpp |
| GCC OpenMP runtime `libgomp.so.1` (Linux builds, used by whisper.cpp) | from the build system | GPL-3.0 with the GCC Runtime Library Exception | https://gcc.gnu.org |
| GeneralUser GS SoundFont (`soundfonts/GeneralUser-GS.sf2`, instruments for the MIDIfier) | 2.0.3 | GeneralUser GS License v2.0 (free use in software projects; see `licenses/generaluser-gs`) | https://www.schristiancollins.com/generaluser.php |

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

The Windows and Linux builds both include their own Python 3.14, so it doesn't
have to be installed. On Windows it is python.org's embeddable package with
python.org's Tcl/Tk files (licence texts in `python\LICENSE.txt`). On Linux it is
CPython from python-build-standalone
(https://github.com/astral-sh/python-build-standalone); its Tk was rebuilt from
the official Tcl/Tk sources with anti-aliased font support
(`packaging/linux/build-tk.sh`). The licence texts of everything built into it
are in `/opt/wyldveils-media-editor/python/licenses/`.

| Component | Licence |
|-----------|---------|
| CPython runtime | Python Software Foundation License |
| Tcl/Tk 9.0 | Tcl/Tk licence (BSD-style) |
| OpenSSL 3 (built into Python) | Apache-2.0 |
| SQLite, libffi, XZ, bzip2, zlib, Zstandard, mpdecimal, Expat (built into Python) | public domain, MIT, 0BSD, bzip2 licence, zlib, BSD-3-Clause, BSD-2-Clause and MIT respectively |
| libedit, ncurses, libuuid (built into the Linux Python) | BSD-3-Clause, X11 (MIT) and BSD-3-Clause |
| tkinterdnd2 | MIT |
| tkdnd (native drag-and-drop extension used by tkinterdnd2) | BSD-style (tkdnd licence) |
| PyInstaller bootloader (only in builds made with `packaging/build.py`, not in the released packages) | GPL-2.0 with the PyInstaller bootloader exception, which allows use in any program |

These packages are loaded at run time from the `libs` folder next to the app
(`app\libs` on Windows, `/opt/wyldveils-media-editor/libs` on Linux) as plain
files. Their licence texts are in each package's `*.dist-info` folder there:

| Component | Licence |
|-----------|---------|
| edge-tts 7.3.1 (online text-to-speech voices) | LGPL-3.0 (`srt_composer.py`: MIT). It is shipped as unmodified source in `libs/edge_tts`, and you may replace it with your own version. Source: https://github.com/rany2/edge-tts |
| aiohttp, aiosignal, frozenlist, multidict, propcache, yarl | Apache-2.0 (aiohttp also contains MIT-licensed parts) |
| aiohappyeyeballs, typing_extensions | Python Software Foundation License 2.0 |
| attrs, tabulate | MIT |
| idna | BSD-3-Clause |
| truststore (uses the operating system's own certificate checks) | MIT |
| PySocks (SOCKS proxy support for the app's own downloads) | BSD-3-Clause |
| certifi (CA certificates) | MPL-2.0 |

## Fonts (`assets/fonts`)

| Font | Licence |
|------|---------|
| Anton, Bangers, Bebas Neue, Bungee, Inter, Lato, Montserrat, Open Sans, Poppins, Raleway, Roboto (Black) | SIL Open Font License 1.1 |
| Luckiest Guy | Apache License 2.0 |

Each font's licence text and copyright notice are in
`assets/fonts/licenses/`.

## Noise-reduction models (`assets/models/rnnoise`)

The five RNNoise models used by Voice Isolation (`bd`, `cb`, `lq`, `mp` and `sh`
`.rnnn`) come from https://github.com/GregorR/rnnoise-models (commit
`3eee541a`). Their authors state that the models are not creative works and
are not subject to copyright. The repository's README is included next to
them.

## Upscaling shaders (`bin/<platform>/shaders`)

Used by the Resolution Scaler's FSR, NIS, ArtCNN and Anime4K upscalers
(through FFmpeg's libplacebo filter). Each file is pinned by SHA-256 in
`scripts/binaries.lock.json`; licence texts are in `licenses/shaders` and in
the headers of FSR.glsl and NVScaler.glsl.

| File | Licence | Source |
|---|---|---|
| `FSR.glsl` - AMD FidelityFX Super Resolution 1.0, mpv port by agyild | MIT (Copyright (c) 2021 Advanced Micro Devices, Inc.) | https://gist.github.com/agyild/82219c545228d70c5604f865ce0b0ce5 |
| `NVScaler.glsl` - NVIDIA Image Scaling 1.0.3, mpv port by agyild | MIT (Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES) | https://gist.github.com/agyild/7e8951915b2bf24526a9343d951db214 |
| `ArtCNN_C4F16.glsl`, `ArtCNN_C4F32.glsl` | MIT (Copyright (c) 2024 João Chrisóstomo) | https://github.com/Artoriuz/ArtCNN |
| `Anime4K_Upscale_CNN_x2_M.glsl` | MIT (Copyright (c) 2019 bloc97) | https://github.com/bloc97/Anime4K |

## Optional components downloaded on demand

* **Whisper speech-recognition models** (Auto-Subtitles): ggml conversions of
  OpenAI's Whisper models (MIT licence), downloaded from
  https://huggingface.co/ggerganov/whisper.cpp and checked against pinned
  SHA-256 sums.

Some tools offer to download extra components the first time you use them,
such as text-to-speech voices or speech-recognition and audio-separation
models. These are downloaded from their publishers and installed into your
per-user data folder, and they stay under their publishers' licences. Each
download prompt names the licence.
