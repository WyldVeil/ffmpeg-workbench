# Developing WyldVeil's Media Editor

This guide covers how the app is put together, the shared building blocks
every tool uses, and how to test changes. Read it before you change a tab.

## Layout

```
main.py                 application shell: window, sidebar, menus, status bar
core/                   shared, UI-independent building blocks (see below)
tabs/base_tab.py        BaseTab + UI helpers that every tool tab inherits
tabs/widgets.py         FramePreview (in-app frame viewer), EncoderPanel
tabs/<category>/*.py    one module per tool
tabs/registry.py        which tools appear in the sidebar
locale/<lang>.json      UI strings; locale/fragments/*.json = new English strings
bin/<platform>/         bundled tools (scripts/fetch_binaries.py); never committed
tests/                  pytest suite + tests/gui_harness.py (headless Tk driver)
packaging/              PyInstaller spec, build script, Linux/Windows installers
```

## Golden rules for tools

1. **Never build a command by hand where a helper exists.** Use these:
   - `core.ffmpeg` for probing, timing, and running commands.
   - `core.encoders` for codec arguments.
   - `core.filters` for escaping paths and text inside filtergraphs.
2. **Never touch Tk from a worker thread.** Use `self.call_ui(fn, ...)`
   (`core.ui.call_soon`) or `self.log(...)`. Never call `widget.after` from a
   thread, and never run `subprocess.run` on the Tk thread for anything that
   can take more than about 100 ms.
3. **Every render goes through the queue**: `self.run_ffmpeg(...)` or
   `self.enqueue_render(...)`. That gives the user progress, a Cancel
   button, atomic outputs and error messages for free. Never spawn ffmpeg
   with `subprocess` yourself, and never `taskkill /IM`.
4. **Never overwrite the input, and never silently overwrite anything.** Call
   `out = self.confirm_output(out, inputs=[src, ...])` right before
   rendering. It returns `None` when the user cancels.
5. **i18n is for display text only.** Never pass `t(...)` output to FFmpeg,
   and never compare a widget's displayed text with an English literal.
   Keep an internal key for every choice and map key → label for display (see
   [Choices](#choices-display-labels-vs-internal-values)).
6. **Paths must survive anything**, including spaces, quotes, brackets,
   commas, `%`, colons, non-ASCII names and Windows drive letters. Pass paths
   as separate argv items. Inside filters, use `core.filters.filter_path()`.
7. **Cross-platform by default.** Don't hard-code Windows-only fonts, paths or
   commands. Use `core.filters.find_font()`, `core.dialogs`,
   `core.hardware.reveal_file()` and `core.paths`.

## Core API cheat-sheet

### `core.ffmpeg`
```python
from core import ffmpeg as ff
info = ff.probe(path)                 # MediaInfo (cached); raises ff.ProbeError
info = ff.try_probe(path)             # or None
info.duration, info.width, info.height, info.fps, info.has_audio, info.has_video
info.video.codec / .pix_fmt / .bit_depth / .is_hdr / .rotation / .field_order
info.audio.codec / .sample_rate / .channels / .channel_layout
info.summary()                        # "1920×1080 · 29.97 fps · H264 · AAC stereo · 02:03"
ff.ffmpeg_path(), ff.ffprobe_path(), ff.ffplay_path()
ff.parse_time("1:02.5") -> 62.5      # accepts SS, MM:SS, HH:MM:SS(.ms), "," decimals
ff.format_time(62.5) -> "01:02.500"
ff.ts(62.5) -> "62.500"               # for -ss / -t / -to arguments
ff.grab_frame_png(path, t, width=640) # PNG bytes of one frame (frame-accurate)
ff.run(cmd, duration=..., on_line=..., on_progress=..., cancel=...)  # RunResult
ff.check(cmd, ...)                    # raises ff.CommandError / ff.CancelledError
ff.format_cmd(cmd)                    # copy-pasteable command line
```

### `core.encoders`
```python
from core import encoders as enc
v = enc.video("h264", "high", speed="balanced", hw="auto", ten_bit=False, container="mp4")
cmd = [ff.ffmpeg_path(), "-y", *v.pre_input, "-i", src, *v.vf_args("scale=-2:720"),
       *v.args, *enc.audio("aac", "192k", container="mp4"), out]
# quality: very_high|high|good|low|lowest (or legacy "Very High"...); speed: fastest..slowest
# codecs: h264 hevc av1 vp9 vp8 prores dnxhr ffv1 mpeg4 mjpeg; hw: auto|off|nvenc|qsv|vaapi|amf|videotoolbox
enc.two_pass_args(v.encoder, 1, passlog)   # target-size encodes (software encoders)
enc.audio_ok_for("mp4", "opus"); enc.default_audio_for("webm"); enc.audio_key_from_probe("aac")
enc.hw_available() / enc.working_hw_kinds()  # functional GPU encoder probe (cached)
```
`v.args` already contains `-pix_fmt` (yuv420p by default, so the output plays
everywhere), `-movflags +faststart` for MP4/MOV, and `-tag:v hvc1` for HEVC
in MP4. If you build a `-filter_complex` graph, append `v.filter_suffix` to
the final video chain. It's empty except with VAAPI.

### `core.filters`
```python
from core import filters as fl
fl.subtitles_filter(srt_path, force_style=fl.ass_style(font="Inter", size=28))
fl.lut3d_filter(cube_path);  fl.movie_source(png_path)
fl.drawtext_filter(textfile=fl.drawtext_text_file(user_text), font="Montserrat-Bold",
                   fontsize=48, x="(w-text_w)/2", y="h-th-40", box=True,
                   enable=fl.enable_between(1.0, 4.0))
fl.filter_value(any_text)   # escape any value for a filter option
fl.filter_path(path)        # escape a path for a filter option
fl.write_concat_list(paths, list_path)          # -f concat -safe 0 -i list_path
fl.write_ffmetadata(path, chapters=[(0, 5.0, "Intro"), ...], tags={...})
fl.find_font("Impact")      # bundled → system (fontconfig/Windows Fonts) → fallback
```

### `core.outputs`, `core.dialogs`, `core.paths`
```python
self.suggest_output(src, "trimmed", ".mp4")      # "<src dir>/<name>_trimmed.mp4", unique
out = self.confirm_output(out, inputs=[src])     # None => user cancelled (message shown)
src = self.pick_source("video")                  # native dialog on Linux, case-insensitive
out = self.pick_output("clip_trimmed.mp4", kind="video", ext=".mp4")
core.paths.temp_dir(), cache_dir(), data_dir(), config_dir(), models_dir()
```
File kinds: video, audio, media, image, visual, subtitle, lut, midi,
soundfont, font, preset, text, any.

### Rendering from a tab
```python
self.run_ffmpeg(cmd, self.console, on_done=lambda rc: self.show_result(rc, out),
                btn=self.render_btn, btn_label=t("x.render"),
                duration=expected_output_seconds, output=out, inputs=[src])
```
- While the job runs, `btn` shows progress and works as a Cancel button.
- `on_done(rc)` gets 0 on success. After a cancel it gets a non-zero value,
  but `show_result` stays quiet.
- `show_result` shows FFmpeg's actual error text.

For multi-step jobs, use a worker function that accepts a context:
```python
def job(progress_cb, cancel_fn, ctx):
    tmp = ctx.tempdir()                                   # deleted afterwards
    ctx.span(0.0, 0.5).run(cmd1, duration=d)              # raises on failure/cancel
    ctx.span(0.5, 1.0).run(cmd2, duration=d)
    return 0
self.enqueue_render("Crossfader", out, worker_fn=job, inputs=[...],
                    on_complete=lambda tid, rc: ...)
```

### Threads and the UI
```python
self.call_ui(label.config, text="Done")             # from any thread
core.ui.run_in_thread(work, on_done=show, on_error=fail)
core.ui.debounce(widget, 120, regenerate_preview)   # for sliders
```

### Widgets
```python
from tabs.widgets import FramePreview, EncoderPanel
pv = FramePreview(parent, width=480, height=270); pv.load(path); pv.show(t)
ep = EncoderPanel(parent, codecs=("h264", "hevc", "av1")); v = ep.video_encode("mp4")
```

### Loading files: the tab protocol
Override `load_file(path) -> bool`, or implement `on_source_loaded(path)`.
The shell calls it for drag-and-drop and File > Open. Accept the file only
if your tab can use it.

### Choices: display labels vs internal values
```python
MODES = [("normal", "crossfader.mode_normal"), ("hq", "crossfader.mode_hq")]
labels = [t(k) for _, k in MODES]
var = tk.StringVar(value=labels[0])
key = dict(zip(labels, (m for m, _ in MODES)))[var.get()]   # compare keys, never labels
```

## Strings

New English strings go into **your own** fragment file,
`locale/fragments/<group>.json`, as `{"key": "English text"}`. They are
loaded automatically and merged into `en_US.json` at release time.
Don't edit `locale/<lang>.json` directly; the translation pass handles
those files.

## Testing

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                      # everything
.venv/bin/python -m pytest -q tests/tabs/test_trimmer.py
.venv/bin/python -m tests.gui_harness "Quick Trimmer"   # inspect a tab's variables/buttons
```
- `tests/gui_harness.py` starts a private Xvfb display, stubs every dialog and
  records every subprocess. See its docstring.
- Fixtures in `tests/conftest.py` give you `media` (synthetic test clips) and
  `ui` (the harness), and `outdir`.
- Every tab should have an end-to-end test. It loads a file through Browse,
  sets options, clicks Render, waits with `ui.wait_idle()`, and checks the
  output with `ui.assert_media(...)`.

## Making a release

1. Set the version in `core/__init__.py` (`APP_VERSION`) and `pyproject.toml`, add it to
   `CHANGELOG.md` and to `packaging/linux/io.github.wyldveil.MediaEditor.metainfo.xml`.
2. Fetch the pinned tools for each platform (checked against `scripts/binaries.lock.json`):
   `python scripts/fetch_binaries.py --platform linux-x64` and `--platform windows-x64`.
   `--refresh` resolves newer versions into the lock file first.
3. Linux packages (Debian and Ubuntu `.deb`, Arch and Fedora `.tar.gz`):
   `python packaging/release.py --linux` (needs Python 3.14+ to run, and podman or docker).
   They include `bin/linux-x64`, the add-ons from `packaging/bundled-libs.txt`, and their own
   Python in `/opt/wyldveils-media-editor/python`: CPython from python-build-standalone (pinned
   with SHA-256 in `LINUX_PYTHON` in `release.py`). Its Tk only knows the X server's bitmap fonts,
   so `packaging/linux/build-tk.sh` rebuilds the same Tk version with Xft in an AlmaLinux 8
   container (glibc 2.28) the first time; the result is kept in `build/tk-<version>-xft`. To move
   to a newer Python, update `LINUX_PYTHON` and `LINUX_PYTHON_LICENSES`, and set `TK_VERSION` and
   `TK_SOURCES` to the Tcl/Tk that python-build-standalone release uses (its
   `pythonbuild/downloads.json`). Try the packages in containers before publishing, including
   ones with no Python at all, e.g.
   `podman run --rm -v ./dist/release:/r:Z debian:12 sh -c "apt update && apt install -y /r/*debian*.deb && wyldveils-media-editor --cli tools"`.
4. Windows installer: `python packaging/release.py --windows --python-embed DIR --iscc "ISCC.exe"`.
   DIR is python.org's embeddable package (`python-3.14.x-embed-amd64.zip`, checked against the
   SHA-256 python.org publishes) with tkinter added from the same version's `amd64/tcltk.msi`
   (on python.org next to the installer, and identical to the copy inside it). Unpack that with
   `msiexec /a tcltk.msi /qn TARGETDIR=<folder>` (on Linux: `wine msiexec ...`), then copy its
   `DLLs\*` next to `python.exe`, `Lib\tkinter` to `Lib\tkinter`, and `libtcl9.*.zip`,
   `libtk9.*.zip`, `tk9.0`, `dde1.4` and `registry1.3` into a `tcl` folder. Replace
   `python314._pth` with these lines: `python314.zip`, `.`, `Lib`, `../libs`, `..`, `import site`.
   On Linux, Inno Setup runs under Wine:
   `--iscc "wine '<prefix>/drive_c/users/<you>/AppData/Local/Programs/Inno Setup 7/ISCC.exe'"`.
   The installer keeps the AppId of earlier releases, so it upgrades them in place, and it
   replaces the `python` folder as a whole.
5. Everything lands in `dist/release/` with its SHA-256 printed; upload those files to the GitHub release.
