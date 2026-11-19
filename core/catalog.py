"""
core/catalog.py  -  What each tool is for, in plain words

Used by the Start page ("What do you want to do?"):

* ``TOOLS``   - for every tool in the sidebar: an icon, one plain sentence,
  and the words people use when they want it ("smaller", "vertical",
  "captions", "hiss"...).
* ``GOALS``   - everyday goals, each leading to the tools that do it.
* ``search(text)``  - tools ranked for whatever the person typed.
* ``suggest(info)`` - tools worth trying on a particular file, each with the
  reason ("It's 1.3 GB", "It contains a GPS location"...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

AIO = "All-in-One Builder"


@dataclass(frozen=True)
class ToolInfo:
    icon: str
    blurb: str
    words: str = ""


TOOLS: dict = {
    # ── Cutting room ──────────────────────────────────────────────────────
    "Quick Trimmer": ToolInfo("✂", "Cut the start and end off a video - instantly or to the exact frame.",
                              "trim cut shorten clip start end beginning remove lossless"),
    "Manual Multi-Splitter": ToolInfo("🔪", "Save several parts of one video as separate files.",
                                      "split segments parts pieces cut separate files chop"),
    "The Splicer": ToolInfo("🧵", "Pick the good bits from several videos and stitch them into one.",
                            "splice combine highlights best parts edit montage join"),
    "Batch Joiner": ToolInfo("🔗", "Join many clips end to end into one video.",
                             "join merge combine concatenate append glue stitch together"),
    "Crossfader": ToolInfo("🔀", "Join clips with smooth transitions - fades, wipes, slides.",
                           "transition fade dissolve crossfade join wipe slide"),
    "Multi-Clip Sequencer": ToolInfo("🎞", "Trim and join clips in order - any mix of sizes and formats.",
                                     "sequence order timeline join trim mix arrange"),
    "Scene Detector": ToolInfo("🎬", "Find every cut in a video, then split it or make chapters.",
                               "scenes shots cuts detect split chapters"),
    "Rotate & Flip": ToolInfo("🔄", "Fix sideways or upside-down video, or mirror it.",
                              "rotate sideways upside down flip mirror orientation portrait landscape turn"),
    "Manual Crop": ToolInfo("⬚", "Cut away the edges of the picture, with aspect presets.",
                            "crop edges frame aspect zoom cut out region"),
    "Smart Reframe": ToolInfo("🎯", "Landscape to vertical or square, with the crop following the action.",
                              "reframe vertical portrait square follow track auto crop tiktok shorts reels"),
    "Side-by-Side": ToolInfo("◫", "Comparison, reaction and before/after layouts with 2-4 clips.",
                             "side by side comparison compare before after reaction split screen"),
    "Beat Sync Cutter": ToolInfo("🥁", "Music montages that cut on the beat.",
                                 "beat music montage rhythm sync cut edit tempo"),
    "Video Reverser": ToolInfo("⏪", "Play backwards, boomerangs and ping-pong loops.",
                               "reverse backwards rewind boomerang ping pong"),
    "Freeze Frame": ToolInfo("🧊", "Pause the picture on a moment, with an optional flash and caption.",
                             "freeze still pause hold frame moment"),
    "Clip Looper": ToolInfo("🔁", "Repeat, stutter or echo a moment.",
                            "loop repeat stutter echo again"),
    # ── Social & format ───────────────────────────────────────────────────
    "The Shortifier": ToolInfo("📱", "Make a vertical or square version for Shorts, TikTok, Reels and more.",
                               "vertical portrait shorts tiktok reels instagram story square 9:16 social phone"),
    "Auto-Cropper": ToolInfo("🔲", "Find and remove black bars automatically.",
                             "black bars letterbox pillarbox borders remove crop auto"),
    "Pro GIF Maker": ToolInfo("🖼", "High-quality animated GIFs, optionally under a size limit.",
                              "gif animated meme loop reaction"),
    "WebM Maker": ToolInfo("🌐", "WebM files for the web at a chosen file size.",
                           "webm web vp9 av1 4chan size"),
    "Watermarker": ToolInfo("💧", "Put text on a video - fixed, moving or only at chosen moments.",
                            "watermark text name handle copyright overlay"),
    "Image Watermark": ToolInfo("🏷", "Put a logo or badge on a video.",
                                "logo watermark badge image overlay brand png"),
    "Hard-Subber": ToolInfo("🔤", "Burn subtitles from a file into the picture.",
                            "subtitles captions burn hardsub srt ass vtt text"),
    "Auto-Subtitles": ToolInfo("💬", "Turn speech into subtitles - runs entirely on this computer.",
                               "subtitles captions transcribe transcript speech text whisper cc translate"),
    "Animated Titles": ToolInfo("🅰", "Animated titles, lower thirds and name tags over footage.",
                                "titles text lower third name tag heading animated"),
    "Intro/Outro Maker": ToolInfo("🎬", "Title cards with animated text, on their own or added to a video.",
                                  "intro outro title card opening ending credits"),
    "Slideshow Maker": ToolInfo("🖼", "Photos to video with motion, transitions, captions and music.",
                                "slideshow photos pictures images ken burns memories"),
    "Frame Extractor": ToolInfo("📸", "Save stills from a video - one frame or many.",
                                "frame still screenshot image picture png jpg extract snapshot"),
    "Thumbnail Maker": ToolInfo("🖼", "Pick the perfect frame and make a thumbnail with text.",
                                "thumbnail cover youtube image text"),
    "Chapter Markers": ToolInfo("🔖", "Chapter timestamps for YouTube, or chapters inside the file.",
                                "chapters timestamps markers youtube description sections"),
    "Video Collage": ToolInfo("🎞", "Any number of clips on one screen, laid out automatically.",
                              "collage grid side by side multiple split screen mosaic wall together"),
    "Video Downloader": ToolInfo("⬇", "Download videos or music from YouTube and 1000+ sites.",
                                 "download youtube save url link online tiktok twitter music mp3"),
    # ── Audio ─────────────────────────────────────────────────────────────
    "Silence Remover": ToolInfo("🤫", "Cut out the pauses - jump cuts for talks and podcasts.",
                                "silence pauses gaps dead air jump cut podcast talk"),
    "Audio Extractor": ToolInfo("🎵", "Save a video's sound as its own file.",
                                "extract audio sound mp3 music soundtrack rip save"),
    "Audio Replacer": ToolInfo("🔁", "Replace a video's sound, or mix in new audio.",
                               "replace audio music soundtrack swap new sound background"),
    "Loudness Normalizer": ToolInfo("📶", "Make the volume even and right for YouTube, Spotify or TV.",
                                    "loud louder quiet volume normalize normalise level lufs even"),
    "Audio Sync Shifter": ToolInfo("⏱", "Fix sound that's ahead of or behind the picture.",
                                   "sync lip sync delay offset out of sync late early"),
    "The Muter": ToolInfo("🔇", "Silence or bleep moments, or remove the sound completely.",
                          "mute silence bleep censor swear remove sound quiet"),
    "Audio Dynamics": ToolInfo("🎚", "Gate, compressor, EQ and limiter for a polished sound.",
                               "compressor eq equaliser equalizer gate limiter dynamics bass treble"),
    "Audio Mixer": ToolInfo("🎛", "Mix up to 8 tracks - voice, music, effects - over a video.",
                            "mix mixer tracks layer music voice effects"),
    "Voice Isolation": ToolInfo("🎙", "Remove background noise from speech.",
                                "noise hiss hum background clean voice speech denoise wind isolate"),
    "Voice Changer": ToolInfo("🗣", "Change a voice - deeper, higher, robot and more.",
                              "voice changer pitch deep high robot chipmunk anonymous"),
    "TTS Voice-Over": ToolInfo("🗨", "Turn text into natural speech, on its own or over a video.",
                               "text to speech tts voice over narration read aloud"),
    "Waveform Editor": ToolInfo("〰", "See the sound, then mark parts to change, mute or export.",
                                "waveform audio edit volume regions visual"),
    "Laugh Track Remover": ToolInfo("😂", "Reduce or remove laugh tracks.",
                                    "laugh track laughter sitcom audience remove"),
    "Music Ducker": ToolInfo("🦆", "Lower the music automatically whenever someone talks.",
                             "duck ducking music lower voice over background sidechain"),
    "Karaoke Generator": ToolInfo("🎤", "Sing-along videos with words that light up in time.",
                                  "karaoke sing lyrics words vocals singalong"),
    "Audio Converter": ToolInfo("🔊", "Convert audio to MP3, AAC, FLAC, WAV, Opus and more.",
                                "convert audio mp3 flac wav aac m4a opus ogg format"),
    "MIDIfier": ToolInfo("🎹", "Turn a recording into MIDI notes and play them back with instruments.",
                         "midi notes piano music instrument transcribe"),
    # ── Transcoder ────────────────────────────────────────────────────────
    "Universal Downsizer": ToolInfo("📉", "Shrink a video to fit a size limit - Discord, email, WhatsApp.",
                                    "smaller compress shrink size reduce limit discord email whatsapp mb big "
                                    "large file"),
    "Codec Cruncher": ToolInfo("⚙", "Re-encode with full control - H.264, HEVC, AV1, ProRes, HDR.",
                               "codec encode reencode h264 hevc h265 av1 prores hdr sdr quality bitrate"),
    "HDR to SDR": ToolInfo("🌗", "Make HDR video from phones and cameras look right on normal screens.",
                           "hdr sdr washed out grey gray faded dull iphone dolby vision hlg hdr10 tone map "
                           "colours look wrong"),
    "Tracks & Remux": ToolInfo("🗂", "Keep, drop or label audio and subtitle tracks, add new ones, change "
                                     "MKV/MP4 - no re-encoding.",
                               "tracks audio track subtitle track language dub remux mkv mp4 default forced "
                               "add subtitles extract streams remove track commentary"),
    "Compare & Quality": ToolInfo("📊", "How much quality did an encode lose? VMAF, SSIM, PSNR and the two "
                                        "side by side.",
                                  "compare quality vmaf ssim psnr difference before after check encode loss "
                                  "side by side a/b"),
    "Format Converter": ToolInfo("🔄", "Convert to MP4, MKV, MOV, WebM, MP3... copying what fits.",
                                 "convert format mp4 mkv mov webm avi container change"),
    "Resolution Scaler": ToolInfo("📐", "Upscale or downscale to any resolution.",
                                  "resolution resize scale upscale downscale 4k 1080p 720p bigger sharper"),
    "Framerate Interpolator": ToolInfo("🎞", "Change the frame rate smoothly, or make true slow motion.",
                                       "framerate fps 60fps smooth slow motion slowmo interpolate"),
    "Proxy Generator": ToolInfo("🪶", "Light copies for smooth editing in other editors.",
                                "proxy edit lightweight premiere resolve davinci"),
    "Encode Queue": ToolInfo("📋", "See and manage every job that's running or waiting.",
                             "queue jobs tasks progress running"),
    "Preset Manager": ToolInfo("💾", "Save your own FFmpeg recipes and run them on files.",
                               "preset recipe custom ffmpeg command template"),
    # ── Colour & visuals ──────────────────────────────────────────────────
    "Basic Color Corrector": ToolInfo("🎨", "Exposure, contrast, white balance and colour, with a live preview.",
                                      "colour color brightness contrast exposure saturation white balance dark "
                                      "bright correct"),
    "LUT Applicator": ToolInfo("🎨", "Apply a colour look (.cube LUT) at any strength.",
                               "lut look grade cinematic film cube"),
    "Colour Match": ToolInfo("🎨", "Make one clip's colours match another's.",
                             "colour color match grade consistent cameras"),
    "Video Scopes": ToolInfo("📊", "Waveform, parade, histogram and vectorscope.",
                             "scopes waveform histogram vectorscope exposure analysis"),
    "Special Effects": ToolInfo("✨", "Intros, outros, glitch, VHS, shake, zoom punch and more.",
                                "effects glitch vhs retro shake zoom vignette light leak film grain"),
    "Speed Ramper": ToolInfo("⏩", "Speed up or slow down, smoothly or in ramps.",
                             "speed fast faster slow slower ramp timelapse hyperlapse slow motion"),
    "Deshaker": ToolInfo("🤳", "Steady shaky footage.",
                         "shaky shake stabilise stabilize steady wobbly handheld"),
    "Green Screen Keyer": ToolInfo("🟩", "Remove a green or blue background and replace it.",
                                   "green screen chroma key background remove replace blue"),
    "Denoise": ToolInfo("🌫", "Clean up noisy, grainy or blocky video.",
                        "noise grain grainy noisy dark low light blocky clean denoise"),
    "Sharpen": ToolInfo("🔍", "Bring back detail in soft or upscaled video.",
                        "sharpen soft blurry detail crisp clarity"),
    "Deinterlace": ToolInfo("📺", "Remove the comb lines from TV, DVD and camcorder video.",
                            "interlaced comb lines tv dvd camcorder vhs deinterlace"),
    "Picture-in-Picture": ToolInfo("🖼", "Put one video in a corner of another.",
                                   "picture in picture pip overlay corner facecam webcam reaction"),
    "Privacy Blur": ToolInfo("🙈", "Blur or pixelate faces, number plates, names or screens.",
                             "blur pixelate hide face plate license number privacy censor"),
    "Animated Zoom": ToolInfo("🔎", "Punch-ins, Ken Burns moves and smooth pans.",
                              "zoom punch in ken burns pan move"),
    "Transition Studio": ToolInfo("🌀", "One polished transition between two clips.",
                                  "transition wipe fade slide two clips"),
    "Media Generator": ToolInfo("🧪", "Colour bars, test patterns, countdowns and tones.",
                                "test pattern colour bars countdown tone leader generate"),
    # ── System ────────────────────────────────────────────────────────────
    "Media Inspector": ToolInfo("ℹ", "Everything about a file: codecs, HDR, rotation, tracks.",
                                "info information inspect details codec properties what is"),
    "Metadata Cleaner": ToolInfo("🧹", "Remove GPS location, phone model, dates and names before sharing.",
                                 "metadata gps location privacy exif remove clean phone anonymous"),
    "Screen Recorder": ToolInfo("⏺", "Record your screen, with sound.",
                                "record screen capture desktop game tutorial"),
    "Batch Processor": ToolInfo("📦", "Do the same thing to a whole folder of videos.",
                                "batch folder many files bulk all"),
    "Advanced Settings": ToolInfo("⚙", "Network, proxy, skins, updates and system information.",
                                  "settings preferences network proxy skin theme update"),
    AIO: ToolInfo("⚡", "Several fixes in one go - trim, crop, colour, sound, subtitles, encode.",
                  "all in one everything combine steps several"),
}


@dataclass(frozen=True)
class Goal:
    key: str
    icon: str
    title: str
    blurb: str
    tools: tuple


GOALS = (
    Goal("cut", "✂", "Cut, trim & join", "Shorten clips, split them, or put several together",
         ("Quick Trimmer", "Batch Joiner", "Crossfader", "The Splicer", "Manual Multi-Splitter",
          "Multi-Clip Sequencer", "Scene Detector", "Silence Remover")),
    Goal("smaller", "📉", "Make a file smaller", "Fit a size limit or save space",
         ("Universal Downsizer", "Codec Cruncher", "Resolution Scaler", "WebM Maker", "Proxy Generator",
          "Compare & Quality")),
    Goal("social", "📱", "Post on social media", "Vertical versions, GIFs, thumbnails, chapters",
         ("The Shortifier", "Smart Reframe", "Pro GIF Maker", "Thumbnail Maker", "Chapter Markers",
          "Auto-Subtitles", "Universal Downsizer")),
    Goal("convert", "🔄", "Convert to another format", "MP4, MKV, MOV, WebM, MP3, GIF...",
         ("Format Converter", "Tracks & Remux", "Audio Converter", "Codec Cruncher", "HDR to SDR",
          "Audio Extractor", "WebM Maker", "Pro GIF Maker")),
    Goal("text", "💬", "Subtitles, titles & text", "Captions from speech, titles, watermarks",
         ("Auto-Subtitles", "Hard-Subber", "Tracks & Remux", "Animated Titles", "Intro/Outro Maker",
          "Watermarker", "Image Watermark", "Karaoke Generator")),
    Goal("sound", "🔊", "Fix the sound", "Too quiet, noisy, out of sync, long pauses",
         ("Loudness Normalizer", "Voice Isolation", "Silence Remover", "Audio Sync Shifter", "Audio Dynamics",
          "The Muter", "Laugh Track Remover", "Waveform Editor")),
    Goal("music", "🎵", "Music, voice & mixing", "Soundtracks, voice-overs, karaoke",
         ("Audio Mixer", "Music Ducker", "Audio Replacer", "TTS Voice-Over", "Voice Changer", "Audio Extractor",
          "Karaoke Generator", "Beat Sync Cutter", "MIDIfier")),
    Goal("look", "🎨", "Colour & look", "Brighter, warmer, cinematic, matching",
         ("Basic Color Corrector", "LUT Applicator", "Colour Match", "Special Effects", "Green Screen Keyer",
          "Video Scopes")),
    Goal("repair", "🩹", "Repair a video", "Shaky, noisy, sideways, washed out, interlaced, black bars",
         ("Deshaker", "Denoise", "Rotate & Flip", "HDR to SDR", "Sharpen", "Deinterlace", "Auto-Cropper",
          "Manual Crop", "Framerate Interpolator", "Resolution Scaler")),
    Goal("motion", "✨", "Effects, speed & motion", "Slow motion, zooms, reverse, freeze, loops",
         ("Speed Ramper", "Framerate Interpolator", "Animated Zoom", "Video Reverser", "Freeze Frame",
          "Clip Looper", "Special Effects", "Transition Studio")),
    Goal("combine", "🧩", "Combine & create", "Collages, picture-in-picture, slideshows, intros",
         ("Video Collage", "Side-by-Side", "Picture-in-Picture", "Slideshow Maker", "Intro/Outro Maker",
          AIO, "Media Generator")),
    Goal("privacy", "🔒", "Privacy", "Remove location and names, blur faces, bleep words",
         ("Metadata Cleaner", "Privacy Blur", "The Muter", "Voice Changer")),
    Goal("get", "⬇", "Download & record", "From YouTube and 1000+ sites, or your screen",
         ("Video Downloader", "Screen Recorder", "Frame Extractor")),
    Goal("manage", "🧰", "Inspect, batch & automate", "File details, folders of files, job queue",
         ("Media Inspector", "Compare & Quality", "Batch Processor", "Encode Queue", "Preset Manager",
          "Advanced Settings")),
)


# ── Search ────────────────────────────────────────────────────────────────────

#: everyday words -> words the catalogue uses (both spellings, common phrasings)
ALIASES = {
    "color": "colour", "colors": "colour", "colours": "colour", "stabilize": "stabilise", "normalize": "normalise",
    "grey": "gray", "caption": "captions", "subtitle": "subtitles", "subs": "subtitles", "cc": "captions",
    "shrink": "smaller", "compress": "smaller", "reduce": "smaller", "tiny": "smaller", "huge": "big",
    "merge": "join", "combine": "join", "concat": "join", "glue": "join", "stitch": "join",
    "portrait": "vertical", "tiktok": "vertical", "reels": "vertical", "shorts": "vertical",
    "slowmo": "slow", "slo": "slow", "timelapse": "fast", "hyperlapse": "fast", "quieter": "volume",
    "louder": "loud", "boost": "loud", "hiss": "noise", "hum": "noise", "wind": "noise",
    "shaky": "shake", "wobbly": "shake", "steady": "shake", "sideways": "rotate", "upside": "rotate",
    "gps": "location", "exif": "metadata", "rip": "extract", "screenshot": "still", "photo": "still",
    "transcribe": "subtitles", "transcript": "subtitles", "bleep": "mute", "swear": "mute",
}

#: words that say nothing about which tool is wanted
STOPWORDS = {"a", "an", "the", "it", "its", "my", "me", "i", "to", "of", "and", "or", "up", "in", "on", "for",
             "with", "how", "do", "can", "want", "from", "this", "that", "make", "get", "some", "into", "out",
             "video", "videos", "file", "files", "please", "need", "way", "is", "be", "so"}

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str, *, query: bool = False) -> list:
    out = []
    for w in _WORD.findall(text.lower()):
        if query and w in STOPWORDS:
            continue
        out.append(w)
        if query and w in ALIASES:              # only the person's words: tool keywords are already exact
            out.append(ALIASES[w])
        if w.endswith("s") and len(w) > 3:
            out.append(w[:-1])
    return out


def search(text: str, limit: int = 12) -> list:
    """Tool names that match *text*, best first."""
    query = _tokens(text, query=True)
    if not query:
        return []
    goal_words = {}
    leaders = {g.tools[0] for g in GOALS}          # ties go to the tool a goal lists first
    for g in GOALS:
        for name in g.tools:
            goal_words.setdefault(name, []).extend(_tokens(g.title + " " + g.blurb))
    scored = []
    for name, info in TOOLS.items():
        name_w = _tokens(name)
        words = set(_tokens(info.words))
        blurb = set(_tokens(info.blurb))
        goals = set(goal_words.get(name, []))
        score = 0.0
        for q in query:
            if len(q) < 2:
                continue
            if q in name_w:
                score += 6
            elif any(w.startswith(q) for w in name_w) and len(q) >= 3:
                score += 4
            if q in words:
                score += 4
            elif len(q) >= 3 and any(w.startswith(q) for w in words):
                score += 2
            if q in blurb:
                score += 1.5
            if q in goals:
                score += 0.5
        if score > 0:
            scored.append((score + (0.25 if name in leaders else 0.0), name))
    if not scored:
        return []
    scored.sort(key=lambda s: (-s[0], s[1]))
    best = scored[0][0]
    return [name for score, name in scored[:limit] if score >= best * 0.4]


# ── Suggestions for a file ────────────────────────────────────────────────────

def _size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def describe(info) -> str:
    """One line about a file: 'Video · 3840×2160 HDR · 60 fps · 2:31 · 1.3 GB'."""
    parts = []
    v = info.video if info.has_video and not info.video.attached_pic else None
    if v is not None and info.duration <= 0.05:
        parts.append("Image")
    elif v is not None:
        parts.append("Video")
    elif info.has_audio:
        parts.append("Audio")
    if v is not None:
        w, h = v.display_size
        parts.append(f"{w}×{h}" + (" HDR" if v.is_hdr else ""))
        if info.duration > 0.05 and v.fps:
            parts.append(f"{v.fps:.3g} fps")
    if info.duration > 0.05:
        m, s = divmod(int(round(info.duration)), 60)
        h_, m = divmod(m, 60)
        parts.append(f"{h_}:{m:02d}:{s:02d}" if h_ else f"{m}:{s:02d}")
    if info.size:
        parts.append(_size(info.size))
    return " · ".join(parts)


def suggest(info, limit: int = 8) -> list:
    """[(tool name, reason)] worth trying on the file *info*, most specific first."""
    from core import metaclean as mc
    out: list = []

    def add(name: str, reason: str):
        if name in TOOLS and all(n != name for n, _r in out):
            out.append((name, reason))

    v = info.video if info.has_video and not info.video.attached_pic else None
    still = v is not None and info.duration <= 0.05
    found = mc.inspect(info)
    cats = {f.category for f in found}
    if "location" in cats:
        add("Metadata Cleaner", "It contains a GPS location - remove it before sharing")
    elif cats & {"device", "telemetry"}:
        add("Metadata Cleaner", "It names the camera or phone it was made with - remove that before sharing")
    if still:
        add("Slideshow Maker", "Turn photos into a video with music")
        add("Image Watermark", "Use it as a logo on a video")
        return out[:limit]
    if v is not None:
        w, h = v.display_size
        if v.is_hdr:
            add("HDR to SDR", "It's HDR - convert it so it doesn't look washed out on normal screens")
        if info.size > 25_000_000:
            add("Universal Downsizer", f"It's {_size(info.size)} - shrink it to fit Discord, email or WhatsApp")
        n_audio = sum(1 for s in info.streams if s.type == "audio")
        n_subs = sum(1 for s in info.streams if s.type == "subtitle")
        if n_audio > 1 or n_subs:
            what = ", ".join(x for x in (f"{n_audio} audio tracks" if n_audio > 1 else "",
                                          f"{n_subs} subtitle track{'s' if n_subs > 1 else ''}" if n_subs else "") if x)
            add("Tracks & Remux", f"It has {what} - choose which to keep, label them, or add more")
        if (v.field_order or "") in ("tt", "bb", "tb", "bt"):
            add("Deinterlace", "It's interlaced - remove the comb lines")
        if w > h:
            add("The Shortifier", "Make a vertical version for Shorts, TikTok or Reels")
        if max(w, h) < 1280:
            add("Resolution Scaler", f"It's small ({w}×{h}) - upscale it to HD")
        if info.has_audio and info.duration > 20:
            add("Auto-Subtitles", "Turn what's said into subtitles")
        if info.duration > 30:
            add("Quick Trimmer", "Cut the start or end off")
        if info.has_audio and info.duration > 60:
            add("Silence Remover", "Cut out the pauses")
        if info.has_audio:
            add("Loudness Normalizer", "Make the volume even and right for YouTube")
        if info.duration <= 30:
            add("Pro GIF Maker", "Make it an animated GIF")
        add("Frame Extractor", "Save a still picture from it")
        add("Format Converter", "Convert it to another format")
    elif info.has_audio:
        if info.duration > 20:
            add("Auto-Subtitles", "Turn the speech into text or subtitles")
        add("Loudness Normalizer", "Make the volume even")
        add("Voice Isolation", "Remove background noise from a voice")
        if info.duration > 60:
            add("Silence Remover", "Cut out the pauses")
        add("Audio Converter", "Convert it to MP3, FLAC, WAV...")
        add("Waveform Editor", "See the sound and edit parts of it")
        add("MIDIfier", "Turn the music into MIDI notes")
    return out[:limit]


def goal(key: str) -> Optional[Goal]:
    return next((g for g in GOALS if g.key == key), None)
