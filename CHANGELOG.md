# Changelog

## 1.3.3

### New

- **The All-in-One Builder is rebuilt around the tools themselves.** Add any of
  about fifty tools as steps, in any order and as many times as you like - blur
  text, put a logo over it, crop, resize, crop again, make a GIF. Each step
  opens that tool, whole, in a window over the builder (its preview, its
  options, its buttons); "Save and return" keeps its settings, and ⚙ opens it
  again. A step after the first works on, and shows, the video as the earlier
  steps leave it. Steps can be moved, copied and removed; a step that depends
  on positions or times is flagged when an earlier step changes.
- **One encode, however the steps are mixed.** Rendering joins the steps into
  as few FFmpeg runs as it can - usually one - and says so above the Render
  button. Where a step has to run on its own (it does several passes, or works
  in Python), the video is handed to it lossless, so the picture is still only
  compressed once. If a joined command ever fails its quick check, those steps
  simply run one at a time.
- **Recipes.** Save a step list and open it again, on the same video or another
  (steps are set up for the new video when you render).
- **Text Blurrer: your own logo or text on top** of what it hides - optional,
  under "How to hide them", with size and opacity, shown in the preview.

### Changed

- The old builder's sections (trim, size, speed, colour, sound, caption,
  fades) are now one step, "Quick adjustments".

## 1.3.2

### New

- **Text Blurrer.** Finds the text laid over a video - names, handles,
  captions, phone numbers, logos - with the times each is on screen, and blurs,
  pixelates or blacks out the ones you click. It needs no download: overlaid
  text keeps its edges still while the picture behind it moves, even when it's
  half see-through. A chosen box can be resized, and you can draw your own.
  Also on the command line as `blur-text` (`--find` lists what's there).

### Fixed

- **Drag and drop works.** Dropping files on the window never worked in the
  release packages (the drag-and-drop library was there but never loaded, and
  registering the window threw). Now a file dropped on any tool loads there,
  and seven more tools take one (The Splicer, Contact Sheet, Intro/Outro Maker,
  Audio Mixer, TTS Voice-Over, Special Effects, Encode Queue).
- **HDR video stays right in every tool.** Re-encoding a phone's HDR video with
  most tools wrote an 8-bit file still labelled HDR (banding, wrong colours on
  many players). Now HEVC, AV1 and VP9 outputs keep the HDR in 10-bit, with its
  brightness information, and other outputs are converted to normal video
  properly.
- In other languages, Special Effects' split-open always went horizontal; the
  cinema-bars and downloader audio-quality defaults weren't among their own
  choices.

### Changed

- **Boxes on the picture are easier to drag.** Manual Crop has a handle in the
  middle of each edge as well as the corners; an edge can be grabbed anywhere
  along it and moves only that edge; the cursor shows what a drag will do.
  Privacy Blur's boxes can now be moved and resized on the picture too.
- **Every drop-down is translated.** About 120 sets of choices, hints and
  sample texts across 70 tools were still in English; they're now in all 32
  languages, and a test builds every tool in a made-up language to keep it so.

## 1.3.1

### Changed

- **The whole app is translated.** Every label, button, message, log line and
  page description added or opened up for translation in 1.3.0 is now
  translated into all 31 other languages, as is the Video Downloader's notice.
  The languages that were behind (Romanian, Swahili, Malay, Bengali, Urdu,
  Persian, Filipino and Nigerian Pidgin) are now complete too. Commands, file
  names, codec and program names stay exactly as they are.
- **Older translations checked line by line.** Many earlier machine
  translations were wrong: shown against the wrong label, using the wrong
  sense of a word ("duck" as the bird, "crop" as a plant), cut short, or with a
  command-line option or filter name translated. Romanian, Swahili, Malay,
  Bengali, Urdu, Persian and Filipino were re-translated in full; the other
  languages had their mistakes corrected. British English now matches the
  current English everywhere.

## 1.3.0

### Fixed

- **Mystical's falling stars on Linux.** They were drawn behind the window's
  panels, so they never showed. They now fall over the whole window (an
  overlay cut to the stars themselves, so every click still reaches the app).
- **Switching theme without restarting** left some colours from the old theme
  behind: buttons, text boxes, menus and drop-downs. Every colour now follows
  the theme.

### Changed

- **Much more of the app can be translated.** About 1,100 labels, titles,
  messages and log lines (and the Start page's goals and suggestions) were fixed
  in English; they now come from the language files, ready for translating. About
  1,200 old entries nothing used are gone from every language file. The Video
  Downloader's notice is translated too (Chinese (Traditional), Czech, Greek and
  Hungarian so far), with each translation saying the English text is the one
  that counts.

## 1.2.9

### Changed

- The Video Downloader is described in general terms (on its page, on the
  Start page, on the command line and in the README): it saves videos and audio
  you have the right to download.

### Updated

- yt-dlp 2026.11.23 (site fixes), whisper.cpp b5907 (1.9.6) for speech to
  text, FluidSynth 2.6.3 on Windows. FFmpeg stays at 9.0.2.

## 1.2.8

### New

- **Four new languages:** Traditional Chinese (繁體中文), Czech (Čeština),
  Greek (Ελληνικά) and Hungarian (Magyar), every string translated. A
  computer set to Taiwanese, Hong Kong, Czech, Greek or Hungarian starts in
  that language. Settings → Language switches at any time.
- **Paste a video link on the Start page** and the Video Downloader opens and
  starts downloading it with your current settings. A playlist or channel link opens there with its
  details fetched, so you can choose what to download.

### Changed

- **Downloads are shareable by default.** The Video Downloader's first
  Quality choice is now *Best for sharing*: H.264 video with AAC sound, which
  every site, phone and editor accepts. The best a site offers is often AV1 or
  VP9 with Opus, which many sites, phones and TVs refuse. *Highest quality* is
  still the next choice down. The command line's `download` does
  the same. A video-only format picked from the list gets AAC sound when it is
  H.264.

### Fixed

- Video Downloader: with the Quality list filtered (say to 720p), the
  download used the row at the same position in the whole list instead of the
  one chosen.
- Bengali: a label that showed the wrong text now shows the English until it
  is translated.

## 1.2.7

### Fixed

- TTS Voice-Over: the offline voice gave up whenever it took more than half a
  second, which on Windows it nearly always does. It now waits for the voice
  as long as it needs (up to its usual time limit).
- Screen Recorder: a recording that didn't stop when asked kept running; it
  is now stopped.

## 1.2.6

### A new name: FFmpeg Workbench

WyldVeil's Media Editor is now **FFmpeg Workbench** - same app, a plainer
name for what it is, in a new repository. On the command line the program is
`ffmpeg-workbench`; on Windows the helper is `ffw-cli.bat`. The Windows
installer replaces 1.2.5; on Linux the package has a new name, so remove
`wyldveils-media-editor` first if you have it.

### New

- **Contact Sheet** (Social & Format): a grid of frames from a video with its
  details on top - file name, size, duration, resolution, frame rate, bit
  rate, video and audio codecs - and each frame's time. It appears as soon as
  a video is opened and changes as you go:
  - any grid from 1×1 to 12×20 (or a preset), tile width, gaps, margin,
    borders and colours;
  - **Previous set / Next set** show other moments from the video - each set
    lands between the ones before, so none repeats;
  - frames evenly spread or **at shot changes**, and the start and end (titles,
    credits) can be left out;
  - choose which details show, or give the sheet a title; times in any corner
    or off; tile numbers; font, size and colour;
  - PNG, JPEG or WebP. Frames are taken with fast seeking, so a two-hour film
    takes seconds. Also on the command line: `contact-sheet`.
- **Slideshow Maker, much bigger:**
  - slides can be photos, **animated GIFs** (they play, looping to fill their
    time), **video clips** (any part, with or without their own sound) and
    **title or ending cards**;
  - every slide can have its own time, transition, motion and caption;
  - **61 transitions** in groups - fades, wipes, slides, soft edges, shapes,
    slices and effects - including two new ones, **fading squares** (the
    picture changes square by square) and **swirl**, or a random one per
    slide; **Preview** plays the selected slide into the next;
  - **music**: several tracks one after another, starting part-way into the
    first, fade in and out, quieter while a video clip speaks; **fit the
    slideshow to the music**, and **change slides on the beat**;
  - sort by name or date, shuffle; bars in any colour; an Instagram portrait
    size.
- **Crossfader**: the same 61 transitions, including fading squares and swirl.

### Easier

- Every time box in the app works the same way: type 12.5, 1:02.5 or
  1:02:03, press 🎞 to find the moment by looking at the frames, or ◉ to take
  the frame the preview shows. A time that can't be read says which box and
  what to type, instead of a general error.

### Fixed

- Video Downloader: in languages other than English, choosing a custom
  re-encode size didn't show the box to type it in.
- Screen Recorder: "Auto (GPU when available)" was the starting codec but
  missing from the list, so it couldn't be chosen again.
- TTS Voice-Over: a saved AAC file now gets the usual .m4a extension.
- Start page: long goal names ("Convert to another format", "Inspect, batch &
  automate") were cut off; they now wrap.
- The Output box's title is translated like the rest of the page, and a few
  labels (mostly in Settings) that showed an internal name instead of their
  text now read properly in every language.
- Audio Converter: asking for variable bitrate on a format that has none no
  longer produces a broken command.

### Under the hood

- **Pages only build the window.** Every FFmpeg command now comes from the
  engine (`core/ops`), so the window and the command line share one version
  of each; a page never builds a command or imports another page, and tests
  keep it that way. Every move was checked by comparing the old and new
  commands.
- **One kit for every page**: the title, the scrolling body, form rows, file
  and folder rows, the Output box (encoder and where the result goes), the
  render button, the log and the colour picker each come from one place,
  checked by tests - a fix there reaches all 83 tools.
- **Short, named steps**: no function is longer than 80 lines or 35
  statements, more complex than 15 or takes more than 8 settings, with no
  exceptions. Pure logic found inside pages moved to the engine: chapter
  parsing and YouTube's chapter rules, the media inspector's notes, the
  built-in LUT looks, preset import checks, subtitle encoding fixes, the
  audio converter's formats and the voice presets.
- **Strictly typed**: the whole app (engine, window and every page) passes
  pyright's strict mode with zero errors, and every function's parameters
  and result are annotated - both checked in CI. Lists of widgets are small
  typed classes instead of dictionaries.
- Lambdas are one step; anything more has a name (checked by a test).
- Every drop-down that the code acts on is read by what was chosen, never by
  its (translated) text - checked by a test.

## 1.2.5

### Smoother

- **No more short freezes when opening a tool.** What the bundled FFmpeg can
  do (its filters, whether your graphics card's Vulkan driver works, whether
  one noise filter is safe on this build) is now checked in the background
  when the app starts and remembered between sessions. HDR to SDR, Voice
  Isolation, the Resolution Scaler, Denoise and Animated Zoom open at once.
- The Media Inspector, HDR to SDR, the Video Downloader and Advanced Settings
  read files and ask programs in the background instead of pausing the window.
- Frame previews of 10-bit and HDR video are about a quarter of the size, so
  scrubbing is lighter.
- The TTS page no longer loads the online voice engine until it is used.
- An idle window wakes the processor far less often (kinder to laptop
  batteries).

### Sturdier

- Every program the app starts now has a time limit, can be stopped, and is
  stopped when the app closes - nothing is left running in the background.
- A broken settings file can no longer overwrite the good backup copy.
- Settings edited by hand with a wrong value fall back to the default for that
  setting (with a note in the log) instead of misbehaving.
- Problems that used to be silently ignored are now written to the log.

### Fixed

- Special Effects: Emoji, Subscribe reminder and Camera shake renders read
  their settings safely (they could fail or use stale values).
- HDR to SDR: pressing Render straight after choosing a file could use the
  previous file's details.
- TTS Voice-Over: playing the result works on Linux and macOS.
- The Encode Queue's job editor is spaced correctly on HiDPI screens.
- Recently used tools and favourites are stored by tool id, so they survive
  tool renames (lists saved by earlier versions are read as before).

### Under the hood

- The code was reorganised along the lines of *Robust Python*, *Effective
  Python*, *Fluent Python* and *High Performance Python*: an engine that
  never touches the window, typed settings and tool ids, page contracts,
  one gateway for programs and one for background work, a typed error tree,
  and tests that keep the structure from eroding. See
  [ARCHITECTURE.md](ARCHITECTURE.md).
- The type checker (pyright) reports no errors anywhere in the code, and CI
  keeps it that way.
- Python 3.11 or newer is needed to run from source.

## 1.2.4

### New

- **Snapshot** (Social & format): save any frame of a video as a full-size
  PNG. Step to the exact frame, see on the right exactly how the picture will
  look, and save (Ctrl+S) or Quick save. It makes stills look their best by
  default: colours converted with the video's own colour standard in full
  range with full-detail colour edges, the pixel shape corrected for
  anamorphic video, HDR tone-mapped to normal colours, and interlacing combs
  removed. Optional: crop black bars, sharpen, reduce noise, brightness /
  contrast / saturation, half or double size, 16-bit PNG.
- **, and . step one frame** back and forward (with Shift, one second) in
  every tool that picks a moment in a video: the Trimmer, the timelines of
  the Multi-Splitter and Splicer (click the in or out handle first to move
  it), every before / after preview, and the position sliders of Freeze
  Frame, Clip Looper, GIF Maker, Frame Extractor, Manual Crop and more. Steps
  land exactly on frames, so cuts can be frame-accurate. Typing a comma or
  full stop in a text box still types it.
- **Set a start or end from the frame shown** wherever a tool has a preview:
  the i and o keys and ◉ buttons in GIF Maker (new End here button, which
  sets the length), Clip Looper, The Muter, the Multi-Clip Sequencer, Privacy
  Blur (new To here), Image Watermark and Picture-in-Picture ("only from /
  to"), Animated Zoom (its from / to / at times) and Animated Titles (each
  card's start and end); i adds a chapter in Chapter Markers. i and o now
  also work straight after stepping with , and . (they were ignored while the
  position slider had the keyboard).
- **🎞 Pick a frame** for the start / end times of tools without a preview
  (WebM Maker, Universal Downsizer, the All-in-One Builder's trim and text
  times, and every Special Effects "at" time): a small window with the
  frame, a slider, ◀ ▶ and the , and . keys; Enter uses that frame.
- Video Scopes and Chapter Markers' sliders step frame by frame too.

### Fixed

- **Manual Multi-Splitter**: the timeline did nothing - every new clip was
  0:00 to 0:10. Add Split Range now takes the range selected on the
  timeline, each clip has a From timeline button, and a dropped or opened
  file loads the timeline too (before, only Browse did).
- **Thumbnail Maker** shows the thumbnail itself as you change it (the frame,
  size, colour and text); before, there was no preview in the app at all.
- The Trimmer and Thumbnail Maker sliders were too coarse to reach every
  frame.
- **Thumbnail Maker, Chapter Markers and Laugh Track Remover** only filled in
  the file name when a video was dropped on them or opened with File > Open;
  its length (and now the preview) wasn't loaded until you typed the path.
- Manual Multi-Splitter numbers the clips 1, 2, 3... again after one is
  removed, and its time boxes are wide enough for milliseconds.
- Some tool names in Bengali, Persian and Urdu were wrong (Frame Extractor
  showed as "Video", "Audio Replacer" and "Slideshow Maker").

## 1.2.3

### Fixed

- **The app closed straight away on HiDPI Linux screens** (display scaling of
  150% or more, e.g. GNOME at 200%) with "X Error of failed request: BadValue
  ... X_CreatePixmap". The Start page kept widening itself until the window
  was too big for the X server.
- **The preview stayed empty in Image Watermark** whenever a logo was chosen
  ("Could not decode a frame here"). The same happened in the Green Screen
  Keyer with a photo as the background, and in Picture-in-Picture with a photo
  as the inset. Renders were never affected.

### Changed

- **Proper HiDPI support.** On a scaled screen (125%, 150%, 200%...) the whole
  interface now grows with the text: spacing, borders, panels, previews,
  timelines, waveforms, graphs, table rows, scrollbars and the window itself.
  Before, only the text grew, so it was cramped and cut off. Linux picks up
  the scale from the desktop (GNOME, KDE, Xfce, MATE...), Windows from its
  display settings. The `ui_scale` setting (e.g. `1.5`) overrides it, and on
  Linux it now makes the text bigger too, not just the spacing.
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
