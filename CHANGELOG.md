# Changelog

## Unreleased

### Fixed

- **The preview stayed empty in Image Watermark** whenever a logo was chosen
  ("Could not decode a frame here"). The same happened in the Green Screen
  Keyer with a photo as the background, and in Picture-in-Picture with a photo
  as the inset. Renders were never affected.

### Changed

- The README shows a full-size screenshot of every tool, grouped by category,
  taken with footage from the 4K version of *Big Buck Bunny*.

## 1.2.2

### New

- **Portable downloads for Windows and Linux.** Unpack the `.zip` (Windows) or
  `.tar.gz` (Linux) anywhere, e.g. your Documents or a USB stick, and run it:
  nothing is installed, no administrator password is needed, and nothing has
  to be on the computer first. Settings, cache and logs stay in a `userdata`
  folder next to the app, so it can be carried between computers. If that
  folder can't be written to (a read-only drive), the usual user folders are
  used instead.
- On Linux, `add-to-menu.sh` puts the portable app in your applications menu,
  just for you (`--remove` takes it out again). The portable Linux version also
  brings the few X11 libraries it uses (libXft, libXrender, libXcursor,
  libXfixes) for systems that lack them; the system's own are used when present.

The installers and Linux packages are still available for anyone who prefers
them.

## 1.2.1

### Fixed

- **The mouse wheel scrolled the wrong way on Linux**, in the sidebar and on
  every page. Wheel down now scrolls down everywhere.
- **Scrolling a page could change a setting.** When the pointer passed over a
  drop-down or number box while scrolling, the wheel changed its value (in
  Universal Downsizer, the size target jumped from 8 MB to 10 MB). Now they only
  respond to the wheel after you click them, as on Windows, and the page scrolls.
- A long list or log inside a page scrolls on its own instead of dragging the
  page with it, and a window in front of the sidebar scrolls itself, not the
  sidebar.
- Touchpads and smooth-scrolling mice on Windows now scroll the pages (small
  steps were ignored). Some pages also scrolled twice as far as others, and the
  Audio Exporter page scrolled even while another tool was showing.

### Changed

- **The Linux packages include their own Python** (3.14.10), so Python and Tk
  no longer need to be installed, and the online voices, system certificates and
  proxy support come included instead of being downloaded on first use. The
  packages only need the basic X11 and font libraries every Linux desktop has,
  and work on any distribution from 2018 on (glibc 2.28). Text uses your
  desktop's fonts, smoothed, as before.
- **Windows:** the bundled Python is updated from 3.14.3 to 3.14.10 (security
  and bug fixes), with Tk 9.
- Mouse back/forward buttons no longer zoom the Waveform Editor (Tk 9 reports
  them as buttons 4 and 5).

## 1.2.0

This is the biggest update yet. Every tool was rebuilt and tested on real
files, there are new tools, and the bundled programs are up to date.

### New

- **Start page.** The app opens on *"What do you want to do?"*. Pick an everyday
  goal (make a file smaller, fix the sound, post on social media, privacy...),
  search in plain words ("too big", "vertical", "hiss", "captions"), or drop in a
  file to get suggestions for it, such as *"It's 1.3 GB - shrink it"* or *"It
  contains a GPS location - remove it before sharing"*. Home still lists every
  tool.
- **HDR to SDR.** Fixes washed-out HDR video from phones and cameras. It
  detects HDR10, HDR10+, HLG and Dolby Vision. Tone mapping runs on the graphics
  card (it also handles Dolby Vision profile 5), or on the processor when there
  is no graphics card support. A before/after preview is included.
- **Tracks & Remux.** Keep, drop, reorder and label audio and subtitle tracks
  (language, title, default/forced), add tracks from other files, save any
  track on its own, and switch between MKV, MP4, MOV and WebM. Nothing is
  re-encoded.
- **Compare & Quality.** See how much quality an encode lost: VMAF, SSIM and
  PSNR, a score-over-time graph, the worst moments, and both videos side by
  side.
- **Metadata Cleaner.** Removes GPS location, phone model, dates, names and
  camera telemetry before you share a file. Nothing is re-encoded, and the file
  is checked afterwards.
- **Upscaling with FSR, NIS and AI.** The Resolution Scaler can now upscale with
  AMD FSR 1.0, NVIDIA Image Scaling, ArtCNN or Anime4K on the graphics card.
- **Video Collage, automatic layout.** Put any number of clips on one screen.
  The layout keeps every clip as large as possible. Shorter clips can loop, or
  the others can take over their space when they end.
- **Command line and AGENTS.md.** `--cli` runs 35 operations without opening the
  window, for scripts and AI assistants. AGENTS.md explains how to use them.

### Rebuilt and improved

- **Cutting room:** Crossfader, Quick Trimmer (frame-exact or instant lossless
  cuts, preview inside the app), joiners and splitters, Beat Sync, Smart
  Reframe, Side-by-Side, Crop, Rotate, Reverser, Freeze Frame, Looper, Scene
  Detector and Sequencer.
- **Social & format:** a WebM Maker Simple mode, plus rebuilt Shortifier, GIF
  maker, watermarks, titles, intro cards, frame extraction, hard subtitles,
  slideshows, chapters, thumbnails and the Video Downloader. Cancelling a
  download no longer closes the app. YouTube downloads use the bundled Deno.
- **Auto-Subtitles** run fully offline on the bundled whisper.cpp.
- **Audio:**
  - loudness to streaming and broadcast standards
  - voice isolation with neural noise models
  - text-to-speech with online voices or the computer's own
  - mixer, music ducking, karaoke, laugh-track removal, converter
  - MIDIfier with a bundled SoundFont
  - waveform editor, dynamics, silence remover, sync shifter and more
- **Transcoder:** Universal Downsizer (fits any size limit), smart Format
  Converter (copies what fits), Codec Cruncher, Resolution Scaler, smooth frame
  rates and slow motion, proxies, Encode Queue and Preset Manager.
- **Colour & visuals:**
  - colour correction, LUTs, Colour Match, scopes, stabiliser and green screen
  - speed ramps, denoise (on the graphics card when possible) and sharpen
  - deinterlace, Privacy Blur, picture-in-picture, animated zoom and transitions
  - all 25 Special Effects work on any source
- **System:** a screen recorder (Windows, macOS, X11 and Wayland, with system
  sound), Batch Processor, settings pages, and System Info that keeps your
  details private.

### Under the hood

- One render queue for every tool, with real progress and cancelling.
- Results are written to a temporary file first. A failed or cancelled job never
  leaves half a file, and existing files are never replaced without asking.
- Graphics card encoding (NVIDIA, Intel, AMD, VA-API, Apple) is detected
  automatically, with 10-bit and HDR-aware encoding.
- Drag and drop works everywhere. Tools load only when opened, so one broken
  tool can't stop the app from starting.
- The network works in every situation: system and PAC proxies, proxy apps,
  VPNs, corporate certificates, mirrors, an offline mode and more. Everything
  happens on your computer; only downloads and online voices use the network.
- Bundled and pinned: FFmpeg 9.0.2, yt-dlp 2026.10.26, Deno 2.9.8,
  whisper.cpp, FluidSynth, the GeneralUser GS SoundFont, RNNoise models and the
  upscaling shaders. Their licences are in THIRD_PARTY_NOTICES.md.
- Over 600 automated tests render real files through every tool.

### Fixes worth knowing about

- HDR-to-SDR conversions no longer leave HDR metadata in the result, which made
  some TVs treat the converted file as HDR. This affects the Downsizer and
  Codec Cruncher too.
- Previews could get stuck on "Loading preview..." when a file was loaded
  twice in quick succession.
- Codec Cruncher could fail to open, and loading a file left the Waveform Editor and
  Universal Downsizer half set up.
- Sliders had no visible track in the dark themes, and hover and selection colours
  ignored the theme.
- Long encodes no longer fill the log with progress lines that pushed the real
  error message out of view.
- Fonts that couldn't legally be redistributed were removed and replaced with
  open alternatives.

### Packages

- **Windows:** an installer with its own Python. It upgrades earlier versions in
  place.
- **Debian, Ubuntu, Arch and Fedora:** the packages run on the system's Python
  (3.10 or newer, with Tk) and include the tested FFmpeg and other tools, so
  every distribution behaves the same.
