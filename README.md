# FFmpeg Workbench

[![Found it useful? Buy me a coffee](https://img.shields.io/badge/Found_it_useful%3F-Buy_me_a_coffee-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/wyldveil)

**84 video and audio tools in one free app.** Cut, join, convert, compress,
subtitle, upscale, fix the sound, clean up old footage and get videos ready for
social media.

![The Start page: what do you want to do?](screenshots/start.png)

A completely free, open-source video and audio processing suite. You don't need
any editing knowledge: tell it what you want to do, or drop in a file and it
suggests what you can do with it.

## Download

Get the file for your system from the **[Releases page](../../releases/latest)**.

**Portable: nothing to install.** Unpack it anywhere (your Documents, a USB
stick...) and run it. No administrator password needed.

| System | Download | Run |
|---|---|---|
| **Windows 10 / 11** (64-bit) | `FFmpeg-Workbench_1.3.2_Windows_Portable.zip` | Unzip it, then double-click **Start FFmpeg Workbench** in the folder. |
| **Linux** (any distribution from 2018 on, 64-bit) | `ffmpeg-workbench-1.3.2-linux-x86_64-portable.tar.gz` | Extract it, then run `./ffmpeg-workbench` in the folder. `./add-to-menu.sh` adds it to your applications menu. |

Everything is included: FFmpeg, the downloader, offline speech recognition, the
upscalers and the app's own Python. Nothing else needs to be set up. The portable
version keeps its settings in a `userdata` folder next to it, so you can carry it
between computers.

<details>
<summary>Prefer an installer? (Start menu entry, uninstaller, system package)</summary>

| System | Download | Install |
|---|---|---|
| **Windows 10 / 11** (64-bit) | `FFmpeg-Workbench_1.3.2_Windows_Setup.exe` | Run it. An earlier version is upgraded in place. |
| **Ubuntu** | `ffmpeg-workbench_1.3.2_ubuntu_amd64.deb` | `sudo apt install ./ffmpeg-workbench_1.3.2_ubuntu_amd64.deb` |
| **Debian** | `ffmpeg-workbench_1.3.2_debian_amd64.deb` | `sudo apt install ./ffmpeg-workbench_1.3.2_debian_amd64.deb` |
| **Fedora** | `ffmpeg-workbench-1.3.2-fedora.tar.gz` | Extract it, then run `./install.sh` |
| **Arch** | `ffmpeg-workbench-1.3.2-arch.tar.gz` | Extract it, then run `makepkg -si` (or `./install.sh`) |

</details>

## Highlights

### A Start page that knows what you want

Pick a goal, search in plain words ("too big", "vertical", "background noise"),
or drop in a file. Here, a phone clip contains a GPS location, so the first
suggestion is to remove it before sharing.

![Suggestions for a dropped file](screenshots/start-suggestions.png)

### Contact Sheet

A whole film at a glance: a grid of frames with the file's details on top. Pick
any grid, step through **other sets of frames** until you like them, take them at
shot changes, leave out the titles and credits, and choose what the header and
tiles show. A two-hour film takes seconds.

![A contact sheet of Big Buck Bunny](screenshots/contact-sheet-example.jpg)

### Slideshow Maker

Photos, animated GIFs, video clips and title cards in one video, with 61
transitions - including fading squares and a swirl - captions, and music that
the slideshow fits itself to, with every change on the beat.

![Fading squares, a swirl and a caption](screenshots/slideshow-transitions.jpg)

### Snapshot

Save any frame as a full-size PNG. Step through the video frame by frame with
the `,` and `.` keys, and see exactly how the picture will look: accurate
colours, the pixel shape corrected, HDR tone-mapped, black bars cropped if you
like, with optional sharpening and noise reduction.

![Snapshot](screenshots/29_snapshot.png)

### HDR to SDR

HDR video from phones and cameras looks grey and washed out in most apps. This
converts it properly: HDR10, HDR10+, HLG and Dolby Vision, using the graphics
card for the best result.

![HDR to SDR, before and after](screenshots/hdr-to-sdr.png)

### Compare & Quality

How much did that compression really cost? You get VMAF, SSIM and PSNR, the
worst moments, and both versions side by side.

![Compare and Quality](screenshots/compare.png)

### Upscaling with FSR, NIS and AI

The Resolution Scaler can upscale with AMD FSR, NVIDIA Image Scaling, ArtCNN or
Anime4K on the graphics card. No suitable graphics card? It works on the
processor too: Lanczos (sharp), Bicubic (soft) and Nearest (for pixel art),
with adjustable sharpening, and Automatic picks the best one either way.

![Resolution Scaler upscaling with FSR](screenshots/upscale.png)

### Video Collage

Any number of clips on one screen. The layout keeps every clip as large as
possible, and shorter clips can loop.

![Video Collage with automatic layout](screenshots/collage.png)

### Tracks & Remux

Choose which audio and subtitle tracks to keep, label their languages, add new
ones, and switch between MKV and MP4. Nothing is re-encoded.

![Tracks and Remux](screenshots/tracks.png)

### Metadata Cleaner

Removes GPS location, phone model, dates and names before you share a video,
without touching the picture or sound.

![Metadata Cleaner](screenshots/metadata-cleaner.png)

### All-in-One Builder

Combine trimming, scaling, frame rate, rotation, speed and colour in one pass,
with the FFmpeg command shown (and editable) underneath.

![All-in-One Builder](screenshots/03_all_in_one_builder.png)

Also: `,` and `.` step one frame back and forward in every tool that picks a moment
(with Shift, a second), sharp and properly sized on HiDPI screens at any scaling,
offline subtitles from speech (whisper.cpp, no uploads and no account) and a
command line for scripts and AI assistants (see below). See the
[changelog](CHANGELOG.md) for everything.

## Everything inside

Every tool has a screenshot in [Every tool, up close](#every-tool-up-close).

| | |
|---|---|
| ✂ **Cutting room** | Quick Trimmer · Crossfader · Splicer · Batch Joiner · Multi-Splitter · Multi-Clip Sequencer · Scene Detector · Beat Sync Cutter · Smart Reframe · Side-by-Side · Manual Crop · Rotate & Flip · Video Reverser · Freeze Frame · Clip Looper |
| 📱 **Social & format** | Shortifier (Shorts / TikTok / Reels) · Auto-Cropper · GIF Maker · WebM Maker · Watermarks · Auto-Subtitles · Hard-Subber · Animated Titles · Intro/Outro Maker · Slideshow Maker · Frame Extractor · Contact Sheet · Snapshot · Thumbnail Maker · Chapter Markers · Video Collage · Video Downloader |
| 🔊 **Audio** | Loudness Normalizer · Voice Isolation · Silence Remover · Audio Mixer · Music Ducker · Audio Replacer · Audio Sync Shifter · The Muter · Audio Dynamics · Voice Changer · TTS Voice-Over · Waveform Editor · Laugh Track Remover · Karaoke Generator · Audio Converter · Audio Extractor · MIDIfier |
| ⚙ **Transcoder** | Universal Downsizer · Codec Cruncher · HDR to SDR · Tracks & Remux · Compare & Quality · Format Converter · Resolution Scaler (with FSR/NIS/AI upscaling) · Framerate Interpolator · Proxy Generator · Encode Queue · Preset Manager |
| 🎨 **Colour & visuals** | Basic Color Corrector · LUT Applicator · Colour Match · Video Scopes · Special Effects · Speed Ramper · Deshaker · Green Screen Keyer · Denoise · Sharpen · Deinterlace · Picture-in-Picture · Privacy Blur · Text Blurrer · Animated Zoom · Transition Studio · Media Generator |
| 🛠 **System** | Media Inspector · Metadata Cleaner · Screen Recorder · Batch Processor · All-in-One Builder |

![Home: every tool at a glance](screenshots/home.png)

There's a screenshot of every tool just below.

## Every tool, up close

A screenshot of each tool, by category. Click a category's title to fold it away.

<details open>
<summary><b>✂ Cutting room</b> (15 tools)</summary>

#### Crossfader

Join clips with smooth transitions - fades, wipes, slides.

![Crossfader](screenshots/04_crossfader.png)

#### Quick Trimmer

Cut the start and end off a video - instantly or to the exact frame.

![Quick Trimmer](screenshots/05_quick_trimmer.png)

#### Manual Multi-Splitter

Save several parts of one video as separate files.

![Manual Multi-Splitter](screenshots/06_manual_multi_splitter.png)

#### The Splicer

Pick the good bits from several videos and stitch them into one.

![The Splicer](screenshots/07_the_splicer.png)

#### Batch Joiner

Join many clips end to end into one video.

![Batch Joiner](screenshots/08_batch_joiner.png)

#### Rotate & Flip

Fix sideways or upside-down video, or mirror it.

![Rotate & Flip](screenshots/09_rotate_flip.png)

#### Side-by-Side

Comparison, reaction and before/after layouts with 2-4 clips.

![Side-by-Side](screenshots/10_side_by_side.png)

#### Beat Sync Cutter

Music montages that cut on the beat.

![Beat Sync Cutter](screenshots/11_beat_sync_cutter.png)

#### Smart Reframe

Landscape to vertical or square, with the crop following the action.

![Smart Reframe](screenshots/12_smart_reframe.png)

#### Manual Crop

Cut away the edges of the picture, with aspect presets.

![Manual Crop](screenshots/13_manual_crop.png)

#### Video Reverser

Play backwards, boomerangs and ping-pong loops.

![Video Reverser](screenshots/14_video_reverser.png)

#### Freeze Frame

Pause the picture on a moment, with an optional flash and caption.

![Freeze Frame](screenshots/15_freeze_frame.png)

#### Clip Looper

Repeat, stutter or echo a moment.

![Clip Looper](screenshots/16_clip_looper.png)

#### Multi-Clip Sequencer

Trim and join clips in order - any mix of sizes and formats.

![Multi-Clip Sequencer](screenshots/17_multi_clip_sequencer.png)

#### Scene Detector

Find every cut in a video, then split it or make chapters.

![Scene Detector](screenshots/18_scene_detector.png)

</details>

<details open>
<summary><b>📱 Social & format</b> (18 tools)</summary>

#### The Shortifier

Make a vertical or square version for Shorts, TikTok, Reels and more.

![The Shortifier](screenshots/19_the_shortifier.png)

#### Auto-Cropper

Find and remove black bars automatically.

![Auto-Cropper](screenshots/20_auto_cropper.png)

#### Pro GIF Maker

High-quality animated GIFs, optionally under a size limit.

![Pro GIF Maker](screenshots/21_pro_gif_maker.png)

#### WebM Maker

WebM files for the web at a chosen file size.

![WebM Maker](screenshots/22_webm_maker.png)

#### Watermarker

Put text on a video - fixed, moving or only at chosen moments.

![Watermarker](screenshots/23_watermarker.png)

#### Image Watermark

Put a logo or badge on a video.

![Image Watermark](screenshots/24_image_watermark.png)

#### Hard-Subber

Burn subtitles from a file into the picture.

![Hard-Subber](screenshots/25_hard_subber.png)

#### Auto-Subtitles

Turn speech into subtitles.

![Auto-Subtitles](screenshots/26_auto_subtitles.png)

#### Animated Titles

Animated titles, lower thirds and name tags over footage.

![Animated Titles](screenshots/27_animated_titles.png)

#### Frame Extractor

Save stills from a video - one frame or many.

![Frame Extractor](screenshots/28_frame_extractor.png)

#### Contact Sheet

A grid of frames from a video with its details; step through other sets of frames.

![Contact Sheet](screenshots/28b_contact_sheet.png)

#### Snapshot

Save any frame as a full-size, high-quality picture.

![Snapshot](screenshots/29_snapshot.png)

#### Intro/Outro Maker

Title cards with animated text, on their own or added to a video.

![Intro/Outro Maker](screenshots/30_intro_outro_maker.png)

#### Slideshow Maker

Photos, GIFs, video clips and title cards into one video: 61 transitions, captions, music.

![Slideshow Maker](screenshots/31_slideshow_maker.png)

#### Chapter Markers

Chapter timestamps for YouTube, or chapters inside the file.

![Chapter Markers](screenshots/32_chapter_markers.png)

#### Thumbnail Maker

Pick the perfect frame and make a thumbnail with text.

![Thumbnail Maker](screenshots/33_thumbnail_maker.png)

#### Video Collage

Any number of clips on one screen, laid out automatically.

![Video Collage](screenshots/34_video_collage.png)

#### Video Downloader

Save videos and audio you have the right to download. Videos are saved as
H.264 with AAC sound by default, so they play anywhere.

![Video Downloader](screenshots/35_video_downloader.png)

</details>

<details open>
<summary><b>🔊 Audio</b> (17 tools)</summary>

#### Silence Remover

Cut out the pauses - jump cuts for talks and podcasts.

![Silence Remover](screenshots/36_silence_remover.png)

#### Audio Extractor

Save a video's sound as its own file.

![Audio Extractor](screenshots/37_audio_extractor.png)

#### Audio Replacer

Replace a video's sound, or mix in new audio.

![Audio Replacer](screenshots/38_audio_replacer.png)

#### Loudness Normalizer

Make the volume even and right for YouTube, Spotify or TV.

![Loudness Normalizer](screenshots/39_loudness_normalizer.png)

#### Audio Sync Shifter

Fix sound that's ahead of or behind the picture.

![Audio Sync Shifter](screenshots/40_audio_sync_shifter.png)

#### The Muter

Silence or bleep moments, or remove the sound completely.

![The Muter](screenshots/41_the_muter.png)

#### Audio Dynamics

Gate, compressor, EQ and limiter for a polished sound.

![Audio Dynamics](screenshots/42_audio_dynamics.png)

#### Audio Mixer

Mix up to 8 tracks - voice, music, effects - over a video.

![Audio Mixer](screenshots/43_audio_mixer.png)

#### Voice Isolation

Remove background noise from speech.

![Voice Isolation](screenshots/44_voice_isolation.png)

#### Voice Changer

Change a voice - deeper, higher, robot and more.

![Voice Changer](screenshots/45_voice_changer.png)

#### TTS Voice-Over

Turn text into natural speech, on its own or over a video.

![TTS Voice-Over](screenshots/46_tts_voice_over.png)

#### Waveform Editor

See the sound, then mark parts to change, mute or export.

![Waveform Editor](screenshots/47_waveform_editor.png)

#### Laugh Track Remover

Reduce or remove laugh tracks.

![Laugh Track Remover](screenshots/48_laugh_track_remover.png)

#### Music Ducker

Lower the music automatically whenever someone talks.

![Music Ducker](screenshots/49_music_ducker.png)

#### Karaoke Generator

Sing-along videos with words that light up in time.

![Karaoke Generator](screenshots/50_karaoke_generator.png)

#### Audio Converter

Convert audio to MP3, AAC, FLAC, WAV, Opus and more.

![Audio Converter](screenshots/51_audio_converter.png)

#### MIDIfier

Turn a recording into MIDI notes and play them back with instruments.

![MIDIfier](screenshots/52_midifier.png)

</details>

<details open>
<summary><b>⚙ Transcoder</b> (11 tools)</summary>

#### Universal Downsizer

Shrink a video to fit a size limit - Discord, email, WhatsApp.

![Universal Downsizer](screenshots/53_universal_downsizer.png)

#### Proxy Generator

Light copies for smooth editing in other editors.

![Proxy Generator](screenshots/54_proxy_generator.png)

#### Codec Cruncher

Re-encode with full control - H.264, HEVC, AV1, ProRes, HDR.

![Codec Cruncher](screenshots/55_codec_cruncher.png)

#### HDR to SDR

Make HDR video from phones and cameras look right on normal screens.

![HDR to SDR](screenshots/56_hdr_to_sdr.png)

#### Tracks & Remux

Keep, drop or label audio and subtitle tracks, add new ones, change MKV/MP4 - no re-encoding.

![Tracks & Remux](screenshots/57_tracks_remux.png)

#### Compare & Quality

How much quality did an encode lose? VMAF, SSIM, PSNR and the two side by side.

![Compare & Quality](screenshots/58_compare_quality.png)

#### Format Converter

Convert to MP4, MKV, MOV, WebM, MP3... copying what fits.

![Format Converter](screenshots/59_format_converter.png)

#### Resolution Scaler

Upscale or downscale to any resolution - FSR, NIS and AI upscalers.

![Resolution Scaler](screenshots/60_resolution_scaler.png)

#### Framerate Interpolator

Change the frame rate smoothly, or make true slow motion.

![Framerate Interpolator](screenshots/61_framerate_interpolator.png)

#### Encode Queue

See and manage every job that's running or waiting.

![Encode Queue](screenshots/62_encode_queue.png)

#### Preset Manager

Save your own FFmpeg recipes and run them on files.

![Preset Manager](screenshots/63_preset_manager.png)

</details>

<details open>
<summary><b>🎨 Colour & visuals</b> (16 tools)</summary>

#### Media Generator

Colour bars, test patterns, countdowns and tones.

![Media Generator](screenshots/64_media_generator.png)

#### Special Effects

Intros, outros, glitch, VHS, shake, zoom punch and more.

![Special Effects](screenshots/65_special_effects.png)

#### LUT Applicator

Apply a colour look (.cube LUT) at any strength.

![LUT Applicator](screenshots/66_lut_applicator.png)

#### Basic Color Corrector

Exposure, contrast, white balance and colour, with a live preview.

![Basic Color Corrector](screenshots/67_basic_color_corrector.png)

#### Colour Match

Make one clip's colours match another's.

![Colour Match](screenshots/68_colour_match.png)

#### Video Scopes

Waveform, parade, histogram and vectorscope.

![Video Scopes](screenshots/69_video_scopes.png)

#### Speed Ramper

Speed up or slow down, smoothly or in ramps.

![Speed Ramper](screenshots/70_speed_ramper.png)

#### Deshaker

Steady shaky footage.

![Deshaker](screenshots/71_deshaker.png)

#### Green Screen Keyer

Remove a green or blue background and replace it.

![Green Screen Keyer](screenshots/72_green_screen_keyer.png)

#### Denoise

Clean up noisy, grainy or blocky video.

![Denoise](screenshots/73_denoise.png)

#### Deinterlace

Remove the comb lines from TV, DVD and camcorder video.

![Deinterlace](screenshots/74_deinterlace.png)

#### Sharpen

Bring back detail in soft or upscaled video.

![Sharpen](screenshots/75_sharpen.png)

#### Picture-in-Picture

Put one video in a corner of another.

![Picture-in-Picture](screenshots/76_picture_in_picture.png)

#### Privacy Blur

Blur or pixelate faces, number plates, names or screens.

![Privacy Blur](screenshots/77_privacy_blur.png)

#### Text Blurrer

Finds the text laid over a video - names, handles, captions, phone numbers,
logos - and blurs the ones you click. Each find is outlined while it's on
screen; the preview shows the result straight away.

![Text Blurrer](screenshots/77b_text_blurrer.png)

#### Animated Zoom

Punch-ins, Ken Burns moves and smooth pans.

![Animated Zoom](screenshots/78_animated_zoom.png)

#### Transition Studio

One polished transition between two clips.

![Transition Studio](screenshots/79_transition_studio.png)

</details>

<details open>
<summary><b>🛠 System</b> (5 tools)</summary>

#### Media Inspector

Everything about a file: codecs, HDR, rotation, tracks.

![Media Inspector](screenshots/80_media_inspector.png)

#### Metadata Cleaner

Remove GPS location, phone model, dates and names before sharing.

![Metadata Cleaner](screenshots/81_metadata_cleaner.png)

#### Screen Recorder

Record your screen, with sound.

![Screen Recorder](screenshots/82_screen_recorder.png)

#### Batch Processor

Do the same thing to a whole folder of videos.

![Batch Processor](screenshots/83_batch_processor.png)

#### Advanced Settings

Network, proxy, skins, updates and system information.

![Advanced Settings](screenshots/84_advanced_settings.png)

</details>


## Private by design

- Your files never leave your computer. Every tool works locally.
- The network is used only when you ask for something that needs it: the Video
  Downloader, the optional online voices in TTS Voice-Over, downloading a speech
  model for Auto-Subtitles or Karaoke the first time you use one, installing the
  MIDIfier's extras, and checking for FFmpeg and yt-dlp updates. All of it can be
  switched off.
- It works behind company proxies, VPNs and filtered networks. If you need to
  set those up, the network settings are under *Advanced Settings*.
- Results never replace your files unless you say so, and a failed or cancelled
  job never leaves a half-written file.

## For scripts and AI assistants

Most of the app's work is available from a terminal without opening the window:

```sh
ffmpeg-workbench --cli tools                          # what it can do
ffmpeg-workbench --cli compress clip.mp4 --size 10MB  # make a video fit Discord
ffmpeg-workbench --cli subtitles talk.mp4 --burn      # subtitles from speech, burned in
ffmpeg-workbench --cli hdr-to-sdr IMG_2087.MOV
```

On Windows, use `ffw-cli.bat` in the install folder. [AGENTS.md](AGENTS.md)
tells AI assistants how to use the app safely: every operation, the rules (it
never overwrites your files), and recipes for common requests. If you ask an
assistant to do something to your videos, point it at that file.

## 🌍 Multilingual

The interface is available in 32 languages. You can switch at any time in
*Advanced Settings*. On first start the app picks your system's language if
it has it.

| | | |
|---|---|---|
| English (US) | English (GB) | العربية |
| বাংলা | Čeština | Deutsch |
| Ελληνικά | Español | فارسی |
| Français | हिन्दी | Magyar |
| Bahasa Indonesia | Italiano | 日本語 |
| 한국어 | Bahasa Melayu | Nederlands |
| Naijá Pidgin | Polski | Português (Brasil) |
| Română | Русский | Kiswahili |
| ภาษาไทย | Filipino | Türkçe |
| Українська | اردو | Tiếng Việt |
| 中文 (简体) | 中文 (繁體) | |

## Uninstall

- **Portable:** delete the folder (`./add-to-menu.sh --remove` first if you
  added the Linux menu entry). Its settings were in the folder too.
- **Windows installer:** use *Add or Remove Programs* in Settings, or the uninstaller in
  the Start Menu.
- **Ubuntu / Debian:** `sudo apt remove ffmpeg-workbench`
- **Fedora:** run `./uninstall.sh` from the extracted folder.
- **Arch:** `sudo pacman -R ffmpeg-workbench`, or `./uninstall.sh`

With the installers, your settings stay in your user folder in case you come back.

## Under the hood

The app is written in Python with Tkinter. The work is done by bundled, pinned
copies of:

| Tool | Version | Used for |
|---|---|---|
| [Python](https://www.python.org/) with Tk | 3.14.10 (Tk 9.0.4) | Runs the app; included, so it doesn't need to be installed |
| [FFmpeg](https://ffmpeg.org/) | 9.0.2 | All video and audio processing, with libplacebo, libvmaf, rubberband and more |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 2026.11.23 | Video Downloader |
| [Deno](https://deno.com/) | 2.9.8 | A JavaScript runtime yt-dlp uses for some sites |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | b5907 | Offline speech-to-text (Auto-Subtitles, Karaoke) |
| [FluidSynth](https://www.fluidsynth.org/) + GeneralUser GS SoundFont | 2.6.3 on Windows (Linux uses the system's copy) | MIDIfier playback |
| FSR, NIS, ArtCNN, Anime4K shaders | | Upscaling in the Resolution Scaler |

Every download is checked against a SHA-256 checksum
(`scripts/binaries.lock.json`). Their licences and sources are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

### Running from source

```sh
python scripts/fetch_binaries.py --platform linux-x64   # or windows-x64: fetches the pinned tools
python main.py                                          # Python 3.11 or newer
python -m pytest -q                                     # the test suite renders real files
python packaging/release.py --linux                     # build the release packages
```

The code is organised in three layers - the engine (`core/`, no window
code), the tool pages (`tabs/`) and the window (`shell/`) - with the rules
that keep them apart checked by the tests. [ARCHITECTURE.md](ARCHITECTURE.md)
is the map, [CONVENTIONS.md](CONVENTIONS.md) the house rules and
[docs/DEVELOPING.md](docs/DEVELOPING.md) the cheat sheet.

## License

All bundled third-party tools keep their original licences (FFmpeg: GPL, yt-dlp:
Unlicense, FluidSynth: LGPL-2.1, whisper.cpp and Deno: MIT, upscaling shaders:
MIT; see THIRD_PARTY_NOTICES.md). The application code is provided as-is.

The screenshots use footage from *Big Buck Bunny* (4K), © Blender Foundation,
licensed CC BY 3.0 ([peach.blender.org](https://peach.blender.org)).
