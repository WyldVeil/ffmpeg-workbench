# AGENTS.md: WyldVeil's Media Editor

This is a guide for AI assistants and scripts. **WyldVeil's Media Editor** is a
desktop video and audio editing suite with 81 tools. It ships its own FFmpeg 9,
yt-dlp, whisper.cpp (offline speech-to-text) and Deno.

Everything below runs from a terminal **without opening the window**. If a
person asks you to do something to their video or audio files, or asks what
can be done, use this file.

## 1. How to run it

| What the person has | Command (called `wme` below) |
|---|---|
| The source code (this folder) | `python main.py --cli ...` (Python 3.11+) |
| Portable folder (Linux) | `<folder>/wyldveils-media-editor --cli ...` |
| Portable folder (Windows) | `<folder>\wme-cli.bat ...` (the `--cli` is optional) |
| Linux package (Debian, Ubuntu, Arch, Fedora) | `wyldveils-media-editor --cli ...` |
| Windows (installed) | `"C:\Program Files\WyldVeil Media Editor\wme-cli.bat" ...` (the `--cli` is optional). Earlier versions were installed under `C:\Program Files\DoffysApp`, and an upgrade stays there. |

Start with these:

```sh
wme --cli tools --json         # every operation and its options, as JSON
wme --cli OPERATION --help     # one operation in detail
wme --cli info FILE --json     # what a file contains: streams, length, HDR, rotation, personal metadata
wme --cli paths --json         # where the bundled ffmpeg, ffprobe and yt-dlp live
wme --cli guide                # print this file
```

On Windows the `wme` part is the `wme-cli.bat` in the install folder, and the
`--cli` can be left out.

## 2. Rules the command line follows, and you should too

1. **Inputs are never changed.** By default a result is saved next to the first
   input as `name_<suffix>.<ext>`, numbered if that name is taken. Use
   `-o FILE` (or `-o FOLDER`) to choose where it goes.
2. **Existing files are never replaced** unless you add `--overwrite`. Only add
   it when the person has asked for that particular file to be replaced. Trying
   to write over an input is always refused.
3. **No half-written files.** Output goes to `name.part.ext` first and is renamed
   only after it succeeds.
4. **Add `--json` to read the result.** The last line on stdout is
   `{"ok": true, "output": "...", "duration": 12.5, "video": "h264 1920x1080 30fps", "audio": "aac 2ch 48000Hz", "size_bytes": ...}`
   or `{"ok": false, "error": "a sentence a person can act on"}`. The exit code is
   0 or 1. Progress goes to stderr as `progress 45%` lines.
5. **Times** can be seconds (`75.5`), `MM:SS` or `HH:MM:SS.mmm`.
6. **Check your work.** After an operation, run `info --json` on the result and
   make sure the length, size and resolution are what the person asked for.
   Then tell them where the file is.
7. **Privacy.** Files are processed on this computer. Only two operations use
   the network: `download`, and `tts` without `--offline` (which uses
   Microsoft's online voices). Both follow the proxy settings made in the app.
8. **Be patient.** Encoding video can take minutes. Progress lines on stderr
   show the job is still running, so don't stop it early.
9. **Video options** for operations that re-encode:
   - `--codec h264|hevc|av1|vp9|prores|ffv1`. The default, h264, plays everywhere.
   - `--quality very_high|high|good|low|lowest`
   - `--speed fastest..slowest`
   - `--gpu auto|off|nvenc|qsv|vaapi|amf|videotoolbox`. `auto` uses the
     graphics card when there is one. `off` gives smaller files but is slower.

## 3. Operations

| Operation | What it does | Main options | Same as the app's tool |
|---|---|---|---|
| `trim` | Keep part of a video | `--start`, `--end`, `--copy` (instant, but starts at a keyframe) | Quick Trimmer |
| `join` | Clips one after another. Lossless when they match, otherwise re-encoded to match | `--copy` (lossless only) | Batch Joiner |
| `transition` | Join clips with a transition | `--type fade\|wipeleft\|circleopen\|...`, `--duration` | Crossfader |
| `rotate` | Rotate 90/180/270 and/or mirror | `--degrees`, `--flip h\|v`, `--lossless` (just fixes the orientation flag) | Rotate & Flip |
| `crop` | Crop | `--rect X:Y:W:H`, `--aspect 9:16`, or `--auto` (remove black bars) | Manual Crop / Auto-Cropper |
| `speed` | Faster or slower, with the sound kept at the same pitch | `--factor 2` (twice as fast) | Speed Ramper |
| `reverse` | Play backwards (clips up to 10 minutes) | | Video Reverser |
| `remove-silence` | Cut out silent pauses | `--threshold -40`, `--min-silence 0.6`, `--padding 0.15` | Silence Remover |
| `compress` | Fit a size limit at the best quality it can | `--size 10MB`, `--codec`, `--fast`, `--start/--end` | Universal Downsizer |
| `convert` | Change the format, copying streams where possible | `--to mp4\|mkv\|mov\|webm\|mp3\|m4a\|wav\|flac\|ogg\|opus...`, `--reencode` | Format Converter |
| `scale` | Resize; upscale with FSR, NIS or AI shaders on a GPU | `--size 1280x720`, `--height 720`, `--factor 2`, `--fit fit\|pad\|crop\|stretch`, `--method auto\|lanczos\|fsr\|nis\|artcnn\|anime4k` | Resolution Scaler |
| `framerate` | New frame rate or slow motion | `--rate 60`, `--method motion\|blend\|dup`, `--slowmo 2` | Framerate Interpolator |
| `hdr-to-sdr` | Fix washed-out HDR video (HDR10, HDR10+, HLG, Dolby Vision) for normal screens | `--curve bt2390\|spline\|hable\|mobius\|...`, `--assume pq\|hlg` (for untagged files) | HDR to SDR |
| `compare` | Measure how much quality a copy lost against its original (VMAF, SSIM, PSNR), with the worst moments | `ORIGINAL COPY`, `--model hd\|4k\|neg`, `--skip-original/--skip-copy`, `--seconds`, `--every N`, `--csv FILE` | Compare & Quality |
| `remux` | Keep only some audio/subtitle tracks, set languages and defaults, add tracks, change MKV/MP4/MOV - no re-encoding | `--to`, `--keep-languages eng,spa`, `--drop-subtitles`, `--default-audio eng`, `--default-subtitles none`, `--add FILE:LANG:DELAY` | Tracks & Remux |
| `extract-track` | Save one track (audio, subtitles, video or an attached font) as its own file | `--track N` (stream index from `info`) | Tracks & Remux |
| `extract-audio` | Save the sound | `--format mp3\|m4a\|wav\|flac\|opus\|ogg`, `--copy` | Audio Extractor |
| `normalize` | Make loudness even, to a standard | `--target streaming\|youtube\|ebu\|atsc\|film` | Loudness Normalizer |
| `clean-voice` | Remove background noise from speech | `--method neural\|fft` | Voice Isolation |
| `tts` | Turn text into speech | `--text` or `--text-file`, `--voice en-GB-RyanNeural`, `--offline` | TTS Voice-Over |
| `subtitles` | Subtitles from speech, offline. Saves an .srt next to the video, or burns them in | `--model tiny\|base\|small\|turbo`, `--language`, `--translate`, `--burn` | Auto-Subtitles |
| `burn-subs` | Draw an .srt/.ass/.vtt file onto the picture | `--subs FILE` | Hard-Subber |
| `gif` | Animated GIF or WebP | `--start`, `--duration`, `--fps`, `--width`, `--max-mb`, `--webp` | Pro GIF Maker |
| `shorts` | Vertical or square version for Shorts, TikTok, Reels... | `--platform shorts\|tiktok\|reels\|story\|ig_square\|ig_portrait\|x\|youtube\|linkedin\|snapchat`, `--background blur\|crop\|mirror\|black\|white` | The Shortifier |
| `thumbnail` | One frame as an image | `--at 12.5`, `--format jpg\|png\|webp` | Frame Extractor |
| `frames` | Many frames into a folder | `--every 2` or `--count 10`, `--format` | Frame Extractor |
| `logo` | Put a logo or watermark image on a video | `--logo FILE`, `--position tl\|tc\|tr\|ml\|c\|mr\|bl\|bc\|br`, `--size 15` (% of the width), `--opacity` | Image Watermark |
| `collage` | Any number of clips on one screen, laid out automatically | `--size 1920x1080`, `--fill`, `--gap`, `--length longest\|shortest\|SECONDS`, `--no-loop`, `--end hold\|background\|reflow`, `--audio first\|mix\|none` | Video Collage |
| `blur-region` | Blur, pixelate or black out part of the frame | `--region X,Y,W,H` (percentages, repeatable), `--type blur\|pixelate\|box`, `--from/--to` | Privacy Blur |
| `denoise` | Reduce video noise or grain | `--method fast\|nlm\|temporal\|grain`, `--strength 1-10` | Denoise |
| `sharpen` | Sharpen | `--method cas\|unsharp\|clarity`, `--amount 0-1` | Sharpen |
| `deinterlace` | Remove comb lines from old or broadcast video | `--mode smart\|all\|double\|ivtc` | Deinterlace |
| `lut` | Apply a colour look (.cube LUT) | `--lut FILE`, `--intensity 0-1` | LUT Applicator |
| `metadata-clean` | Remove GPS location, device, dates, names and telemetry without re-encoding | `--deep`, `--keep-chapters`, `--keep-cover`, `--drop-languages` | Metadata Cleaner |
| `download` | Download from YouTube and 1000+ other sites | `URL`, `--audio-only`, `--max-height 1080`, `--format mp4\|mkv\|webm`, `-o FOLDER` | Video Downloader |

Every operation also takes `-o`, `--overwrite` and `--json`.

## 4. Common requests

```sh
wme --cli compress clip.mp4 --size 10MB --json                 # "make it small enough for Discord"
wme --cli trim talk.mp4 --start 0:30 --end 5:10 --json         # "cut out the start and the end"
wme --cli shorts video.mp4 --platform tiktok --json            # "make it vertical for TikTok"
wme --cli subtitles interview.mp4 --model small --burn --json  # "add subtitles to the video"
wme --cli metadata-clean IMG_4021.MOV --json                   # "remove the location before I post it"
wme --cli gif clip.mp4 --start 12 --duration 4 --max-mb 8 --json
wme --cli collage a.mp4 b.mp4 c.mp4 d.mp4 --size 1920x1080 --json   # "put these side by side"
wme --cli normalize podcast.mp3 --target youtube --json
wme --cli clean-voice voiceover.wav --json
wme --cli remove-silence lecture.mp4 --json
wme --cli rotate phone.mp4 --degrees 90 --lossless --json      # "it's sideways"
wme --cli hdr-to-sdr iphone_hdr.mov --json                     # "the colours look washed out"
wme --cli extract-audio concert.mp4 --format mp3 --json
wme --cli download "https://..." --max-height 1080 -o ~/Videos --json
```

**Doing several steps in a row.** Feed each result into the next step, using
explicit `-o` paths in a working folder. Afterwards, delete only the in-between
files you created. Never delete the person's own files.

**Downloads.** Only download what the person has the right to download.

## 5. Things the command line doesn't cover

- The app has 81 tools. If a request needs one that isn't listed above, tell
  the person which tool to open from the sidebar. Some examples: Beat Sync
  Cutter, Smart Reframe, Green Screen Keyer, Colour Match, Speed Ramper (with
  speed curves), Deshaker, Animated Titles, Slideshow Maker, Karaoke Generator,
  Music Ducker, Audio Mixer, Waveform Editor, MIDIfier, Screen Recorder, Batch
  Processor. Running `wme FILE...` without `--cli` opens the window with those
  files loaded.
- If you know FFmpeg well, you can use the **bundled FFmpeg** directly. Get its
  path from `wme --cli paths --json`. It is FFmpeg 9 with libx264, libx265,
  SVT-AV1, VP9, Opus, MP3, NVENC/QSV/AMF/VAAPI, libplacebo (Vulkan), zscale,
  libvmaf, rubberband, vid.stab, arnndn and libass. Follow the same rules: never
  write over inputs, and write to a temporary name first.

## 6. Changing the app (for developers)

- Start with `ARCHITECTURE.md` (the map and the reasons) and `CONVENTIONS.md`
  (the house rules; most are enforced by tests).
- `main.py` only decides: `--cli` goes to `core/cli.py` before any window code
  loads; anything else opens the window from `shell/`.
- `core/` is the engine and never imports Tk: `proc.py` (the only place
  programs start), `tasks.py` (the only place threads start), `errors.py`,
  `settings.py` (every setting declared with its type), `tools.py` (every
  tool's id), `ffmpeg.py` (probing, running, progress), `encoders.py`,
  `filters.py`, `network.py`, `whisper.py`, `tts.py`...
- `core/ops/<tool>.py` builds the FFmpeg commands as pure functions. Both the
  window and `core/cli.py` use them, so a fix in one place fixes both.
- `tabs/<category>/<tool>.py` has one tool page per file; `tabs/registry.py`
  says which class builds each tool. The window talks to pages only through
  the protocols in `tabs/kit/contracts.py`. Visible text lives in
  `locale/*.json`.
- **Sizes scale with the screen.** The UI is designed at 96 DPI. Fonts are in
  points and grow on their own; every size in pixels goes through
  `px()` from `core/scaling.py` (`padx=px(12)`, `pady=px(0, 8)`, canvas
  offsets, fixed widths and heights, line widths, dash patterns). Never apply it to
  sizes measured from the screen (`winfo_width()`, `event.width`), which are
  already screen pixels. Drawings at fixed design coordinates can be grown
  in one go with `scale_drawing(canvas)`. `tests/test_scaling.py` starts
  the app at 200 %.
- **To add an operation:** write a function in `core/cli.py` with `@operation`
  and `@options`, reusing the builders in `core/ops/`. Add a test to
  `tests/test_cli.py` and a row to the table above. A test fails if any
  operation is missing from this file.
- **Tests:** `python -m pytest -q`. They need FFmpeg, either on PATH or in `bin/`.
  GUI tests drive the window without showing it. Lint with `ruff check`; types
  with `python scripts/check_types.py` (pyright; it may not get worse).
- **Bundled programs** are pinned with SHA-256 checksums in `binaries.lock.json`
  and fetched by `scripts/fetch_binaries.py`. Their licences are in
  `THIRD_PARTY_NOTICES.md`.
