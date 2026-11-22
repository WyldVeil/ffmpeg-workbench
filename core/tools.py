"""
core/tools.py - every tool the app has, declared once.

Robust Python ch. 8 (enumerations) and ch. 10 (data classes): a tool is
identified by a :class:`ToolId` - a stable, short id that is what settings,
recent lists and favourites store - and described by a frozen
:class:`ToolSpec`: its English name, its sidebar section and the plain words
the Start page searches.  Display names can change (or be translated)
without breaking anything a person has saved.

The GUI side (which module builds each page) is tabs/registry.py; this file
stays GUI-free so the engine and the command line can name tools too.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Category(StrEnum):
    """Sidebar sections, in order.  The value is the section's heading."""

    PINNED = "pinned"
    CUTTING = "✂  CUTTING ROOM"
    SOCIAL = "📱  SOCIAL & FORMAT"
    AUDIO = "🔊  AUDIO ENGINEERING"
    TRANSCODER = "⚙  TRANSCODER"
    VISUALS = "🎨  COLOR & VISUALS"
    SYSTEM = "🛠  SYSTEM"
    HIDDEN = "hidden"  # pages reached from elsewhere (Start, Home, Settings)


class ToolId(StrEnum):
    """Stable ids: stored in settings, so never rename a value."""

    ALL_IN_ONE_BUILDER = "all_in_one"
    CROSSFADER = "crossfader"
    QUICK_TRIMMER = "trimmer"
    MANUAL_MULTI_SPLITTER = "splitter"
    SPLICER = "splicer"
    BATCH_JOINER = "batch_joiner"
    ROTATE_AND_FLIP = "rotate_flip"
    SIDE_BY_SIDE = "side_by_side"
    BEAT_SYNC_CUTTER = "beat_sync"
    SMART_REFRAME = "smart_reframe"
    MANUAL_CROP = "crop"
    VIDEO_REVERSER = "reverser"
    FREEZE_FRAME = "freeze_frame"
    CLIP_LOOPER = "clip_looper"
    MULTI_CLIP_SEQUENCER = "sequencer"
    SCENE_DETECTOR = "scene_detect"
    SHORTIFIER = "shortifier"
    AUTO_CROPPER = "auto_cropper"
    PRO_GIF_MAKER = "gif_maker"
    WEBM_MAKER = "webm_maker"
    WATERMARKER = "watermark"
    IMAGE_WATERMARK = "image_watermark"
    HARD_SUBBER = "hard_subber"
    AUTO_SUBTITLES = "auto_subs"
    ANIMATED_TITLES = "titles"
    FRAME_EXTRACTOR = "frame_extractor"
    SNAPSHOT = "snapshot"
    INTRO_OUTRO_MAKER = "intro_maker"
    SLIDESHOW_MAKER = "slideshow"
    CHAPTER_MARKERS = "chapter_markers"
    THUMBNAIL_MAKER = "thumbnail"
    VIDEO_COLLAGE = "collage"
    VIDEO_DOWNLOADER = "youtube"
    SILENCE_REMOVER = "silence"
    AUDIO_EXTRACTOR = "extractor"
    AUDIO_REPLACER = "replacer"
    LOUDNESS_NORMALIZER = "loudness"
    AUDIO_SYNC_SHIFTER = "sync"
    MUTER = "muter"
    AUDIO_DYNAMICS = "dynamics"
    AUDIO_MIXER = "mixer"
    VOICE_ISOLATION = "voice_isolation"
    VOICE_CHANGER = "voice_changer"
    TTS_VOICE_OVER = "tts"
    WAVEFORM_EDITOR = "waveform"
    LAUGH_TRACK_REMOVER = "laugh_track"
    MUSIC_DUCKER = "ducker"
    KARAOKE_GENERATOR = "karaoke"
    AUDIO_CONVERTER = "exporter"
    MIDIFIER = "midifier"
    UNIVERSAL_DOWNSIZER = "downsizer"
    PROXY_GENERATOR = "proxy"
    CODEC_CRUNCHER = "codec"
    HDR_TO_SDR = "hdr"
    TRACKS_AND_REMUX = "tracks"
    COMPARE_AND_QUALITY = "compare"
    FORMAT_CONVERTER = "converter"
    RESOLUTION_SCALER = "scaler"
    FRAMERATE_INTERPOLATOR = "fps_interpolator"
    ENCODE_QUEUE = "encode_queue"
    PRESET_MANAGER = "presets"
    MEDIA_GENERATOR = "media_gen"
    SPECIAL_EFFECTS = "effects"
    LUT_APPLICATOR = "lut"
    BASIC_COLOR_CORRECTOR = "color_correct"
    COLOUR_MATCH = "color_match"
    VIDEO_SCOPES = "scopes"
    SPEED_RAMPER = "speed_ramp"
    DESHAKER = "deshaker"
    GREEN_SCREEN_KEYER = "green_key"
    DENOISE = "denoise"
    DEINTERLACE = "deinterlace"
    SHARPEN = "sharpen"
    PICTURE_IN_PICTURE = "pip"
    PRIVACY_BLUR = "region_blur"
    ANIMATED_ZOOM = "zoom"
    TRANSITION_STUDIO = "transitions"
    MEDIA_INSPECTOR = "media_inspector"
    METADATA_CLEANER = "metadata_cleaner"
    SCREEN_RECORDER = "screen_recorder"
    BATCH_PROCESSOR = "batch"
    ADVANCED_SETTINGS = "advanced_settings"
    SETTINGS = "settings"
    HOME = "home"
    START = "start"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    id: ToolId
    name: str  # English display name (also the page's title)
    category: Category
    icon: str = ""
    blurb: str = ""  # one plain sentence: what it is for
    words: str = ""  # what people type when they want it (Start page search)

    @property
    def in_sidebar(self) -> bool:
        return self.category not in (Category.PINNED, Category.HIDDEN)


def _t(
    tid: ToolId, name: str, category: Category, icon: str = "", blurb: str = "", words: str = ""
) -> ToolSpec:
    return ToolSpec(tid, name, category, icon, blurb, words)


#: Every tool, in sidebar order.
SPECS: Final[tuple[ToolSpec, ...]] = (
    _t(
        ToolId.ALL_IN_ONE_BUILDER,
        "All-in-One Builder",
        Category.PINNED,
        "⚡",
        "Several fixes in one go - trim, crop, colour, sound, subtitles, encode.",
        "all in one everything combine steps several",
    ),
    _t(
        ToolId.CROSSFADER,
        "Crossfader",
        Category.CUTTING,
        "🔀",
        "Join clips with smooth transitions - fades, wipes, slides.",
        "transition fade dissolve crossfade join wipe slide",
    ),
    _t(
        ToolId.QUICK_TRIMMER,
        "Quick Trimmer",
        Category.CUTTING,
        "✂",
        "Cut the start and end off a video - instantly or to the exact frame.",
        "trim cut shorten clip start end beginning remove lossless",
    ),
    _t(
        ToolId.MANUAL_MULTI_SPLITTER,
        "Manual Multi-Splitter",
        Category.CUTTING,
        "🔪",
        "Save several parts of one video as separate files.",
        "split segments parts pieces cut separate files chop",
    ),
    _t(
        ToolId.SPLICER,
        "The Splicer",
        Category.CUTTING,
        "🧵",
        "Pick the good bits from several videos and stitch them into one.",
        "splice combine highlights best parts edit montage join",
    ),
    _t(
        ToolId.BATCH_JOINER,
        "Batch Joiner",
        Category.CUTTING,
        "🔗",
        "Join many clips end to end into one video.",
        "join merge combine concatenate append glue stitch together",
    ),
    _t(
        ToolId.ROTATE_AND_FLIP,
        "Rotate & Flip",
        Category.CUTTING,
        "🔄",
        "Fix sideways or upside-down video, or mirror it.",
        "rotate sideways upside down flip mirror orientation portrait landscape turn",
    ),
    _t(
        ToolId.SIDE_BY_SIDE,
        "Side-by-Side",
        Category.CUTTING,
        "◫",
        "Comparison, reaction and before/after layouts with 2-4 clips.",
        "side by side comparison compare before after reaction split screen",
    ),
    _t(
        ToolId.BEAT_SYNC_CUTTER,
        "Beat Sync Cutter",
        Category.CUTTING,
        "🥁",
        "Music montages that cut on the beat.",
        "beat music montage rhythm sync cut edit tempo",
    ),
    _t(
        ToolId.SMART_REFRAME,
        "Smart Reframe",
        Category.CUTTING,
        "🎯",
        "Landscape to vertical or square, with the crop following the action.",
        "reframe vertical portrait square follow track auto crop tiktok shorts reels",
    ),
    _t(
        ToolId.MANUAL_CROP,
        "Manual Crop",
        Category.CUTTING,
        "⬚",
        "Cut away the edges of the picture, with aspect presets.",
        "crop edges frame aspect zoom cut out region",
    ),
    _t(
        ToolId.VIDEO_REVERSER,
        "Video Reverser",
        Category.CUTTING,
        "⏪",
        "Play backwards, boomerangs and ping-pong loops.",
        "reverse backwards rewind boomerang ping pong",
    ),
    _t(
        ToolId.FREEZE_FRAME,
        "Freeze Frame",
        Category.CUTTING,
        "🧊",
        "Pause the picture on a moment, with an optional flash and caption.",
        "freeze still pause hold frame moment",
    ),
    _t(
        ToolId.CLIP_LOOPER,
        "Clip Looper",
        Category.CUTTING,
        "🔁",
        "Repeat, stutter or echo a moment.",
        "loop repeat stutter echo again",
    ),
    _t(
        ToolId.MULTI_CLIP_SEQUENCER,
        "Multi-Clip Sequencer",
        Category.CUTTING,
        "🎞",
        "Trim and join clips in order - any mix of sizes and formats.",
        "sequence order timeline join trim mix arrange",
    ),
    _t(
        ToolId.SCENE_DETECTOR,
        "Scene Detector",
        Category.CUTTING,
        "🎬",
        "Find every cut in a video, then split it or make chapters.",
        "scenes shots cuts detect split chapters",
    ),
    _t(
        ToolId.SHORTIFIER,
        "The Shortifier",
        Category.SOCIAL,
        "📱",
        "Make a vertical or square version for Shorts, TikTok, Reels and more.",
        "vertical portrait shorts tiktok reels instagram story square 9:16 social phone",
    ),
    _t(
        ToolId.AUTO_CROPPER,
        "Auto-Cropper",
        Category.SOCIAL,
        "🔲",
        "Find and remove black bars automatically.",
        "black bars letterbox pillarbox borders remove crop auto",
    ),
    _t(
        ToolId.PRO_GIF_MAKER,
        "Pro GIF Maker",
        Category.SOCIAL,
        "🖼",
        "High-quality animated GIFs, optionally under a size limit.",
        "gif animated meme loop reaction",
    ),
    _t(
        ToolId.WEBM_MAKER,
        "WebM Maker",
        Category.SOCIAL,
        "🌐",
        "WebM files for the web at a chosen file size.",
        "webm web vp9 av1 4chan size",
    ),
    _t(
        ToolId.WATERMARKER,
        "Watermarker",
        Category.SOCIAL,
        "💧",
        "Put text on a video - fixed, moving or only at chosen moments.",
        "watermark text name handle copyright overlay",
    ),
    _t(
        ToolId.IMAGE_WATERMARK,
        "Image Watermark",
        Category.SOCIAL,
        "🏷",
        "Put a logo or badge on a video.",
        "logo watermark badge image overlay brand png",
    ),
    _t(
        ToolId.HARD_SUBBER,
        "Hard-Subber",
        Category.SOCIAL,
        "🔤",
        "Burn subtitles from a file into the picture.",
        "subtitles captions burn hardsub srt ass vtt text",
    ),
    _t(
        ToolId.AUTO_SUBTITLES,
        "Auto-Subtitles",
        Category.SOCIAL,
        "💬",
        "Turn speech into subtitles - runs entirely on this computer.",
        "subtitles captions transcribe transcript speech text whisper cc translate",
    ),
    _t(
        ToolId.ANIMATED_TITLES,
        "Animated Titles",
        Category.SOCIAL,
        "🅰",
        "Animated titles, lower thirds and name tags over footage.",
        "titles text lower third name tag heading animated",
    ),
    _t(
        ToolId.FRAME_EXTRACTOR,
        "Frame Extractor",
        Category.SOCIAL,
        "📸",
        "Save stills from a video - one frame or many.",
        "frames stills many every seconds scenes contact sheet storyboard extract image png jpg",
    ),
    _t(
        ToolId.SNAPSHOT,
        "Snapshot",
        Category.SOCIAL,
        "📷",
        "Save any frame as a full-size, high-quality picture.",
        "screenshot screen shot snapshot still frame picture image png capture grab exact frame by frame full resolution",
    ),
    _t(
        ToolId.INTRO_OUTRO_MAKER,
        "Intro/Outro Maker",
        Category.SOCIAL,
        "🎬",
        "Title cards with animated text, on their own or added to a video.",
        "intro outro title card opening ending credits",
    ),
    _t(
        ToolId.SLIDESHOW_MAKER,
        "Slideshow Maker",
        Category.SOCIAL,
        "🖼",
        "Photos to video with motion, transitions, captions and music.",
        "slideshow photos pictures images ken burns memories",
    ),
    _t(
        ToolId.CHAPTER_MARKERS,
        "Chapter Markers",
        Category.SOCIAL,
        "🔖",
        "Chapter timestamps for YouTube, or chapters inside the file.",
        "chapters timestamps markers youtube description sections",
    ),
    _t(
        ToolId.THUMBNAIL_MAKER,
        "Thumbnail Maker",
        Category.SOCIAL,
        "🖼",
        "Pick the perfect frame and make a thumbnail with text.",
        "thumbnail cover youtube image text",
    ),
    _t(
        ToolId.VIDEO_COLLAGE,
        "Video Collage",
        Category.SOCIAL,
        "🎞",
        "Any number of clips on one screen, laid out automatically.",
        "collage grid side by side multiple split screen mosaic wall together",
    ),
    _t(
        ToolId.VIDEO_DOWNLOADER,
        "Video Downloader",
        Category.SOCIAL,
        "⬇",
        "Download videos or music from YouTube and 1000+ sites.",
        "download youtube save url link online tiktok twitter music mp3",
    ),
    _t(
        ToolId.SILENCE_REMOVER,
        "Silence Remover",
        Category.AUDIO,
        "🤫",
        "Cut out the pauses - jump cuts for talks and podcasts.",
        "silence pauses gaps dead air jump cut podcast talk",
    ),
    _t(
        ToolId.AUDIO_EXTRACTOR,
        "Audio Extractor",
        Category.AUDIO,
        "🎵",
        "Save a video's sound as its own file.",
        "extract audio sound mp3 music soundtrack rip save",
    ),
    _t(
        ToolId.AUDIO_REPLACER,
        "Audio Replacer",
        Category.AUDIO,
        "🔁",
        "Replace a video's sound, or mix in new audio.",
        "replace audio music soundtrack swap new sound background",
    ),
    _t(
        ToolId.LOUDNESS_NORMALIZER,
        "Loudness Normalizer",
        Category.AUDIO,
        "📶",
        "Make the volume even and right for YouTube, Spotify or TV.",
        "loud louder quiet volume normalize normalise level lufs even",
    ),
    _t(
        ToolId.AUDIO_SYNC_SHIFTER,
        "Audio Sync Shifter",
        Category.AUDIO,
        "⏱",
        "Fix sound that's ahead of or behind the picture.",
        "sync lip sync delay offset out of sync late early",
    ),
    _t(
        ToolId.MUTER,
        "The Muter",
        Category.AUDIO,
        "🔇",
        "Silence or bleep moments, or remove the sound completely.",
        "mute silence bleep censor swear remove sound quiet",
    ),
    _t(
        ToolId.AUDIO_DYNAMICS,
        "Audio Dynamics",
        Category.AUDIO,
        "🎚",
        "Gate, compressor, EQ and limiter for a polished sound.",
        "compressor eq equaliser equalizer gate limiter dynamics bass treble",
    ),
    _t(
        ToolId.AUDIO_MIXER,
        "Audio Mixer",
        Category.AUDIO,
        "🎛",
        "Mix up to 8 tracks - voice, music, effects - over a video.",
        "mix mixer tracks layer music voice effects",
    ),
    _t(
        ToolId.VOICE_ISOLATION,
        "Voice Isolation",
        Category.AUDIO,
        "🎙",
        "Remove background noise from speech.",
        "noise hiss hum background clean voice speech denoise wind isolate",
    ),
    _t(
        ToolId.VOICE_CHANGER,
        "Voice Changer",
        Category.AUDIO,
        "🗣",
        "Change a voice - deeper, higher, robot and more.",
        "voice changer pitch deep high robot chipmunk anonymous",
    ),
    _t(
        ToolId.TTS_VOICE_OVER,
        "TTS Voice-Over",
        Category.AUDIO,
        "🗨",
        "Turn text into natural speech, on its own or over a video.",
        "text to speech tts voice over narration read aloud",
    ),
    _t(
        ToolId.WAVEFORM_EDITOR,
        "Waveform Editor",
        Category.AUDIO,
        "〰",
        "See the sound, then mark parts to change, mute or export.",
        "waveform audio edit volume regions visual",
    ),
    _t(
        ToolId.LAUGH_TRACK_REMOVER,
        "Laugh Track Remover",
        Category.AUDIO,
        "😂",
        "Reduce or remove laugh tracks.",
        "laugh track laughter sitcom audience remove",
    ),
    _t(
        ToolId.MUSIC_DUCKER,
        "Music Ducker",
        Category.AUDIO,
        "🦆",
        "Lower the music automatically whenever someone talks.",
        "duck ducking music lower voice over background sidechain",
    ),
    _t(
        ToolId.KARAOKE_GENERATOR,
        "Karaoke Generator",
        Category.AUDIO,
        "🎤",
        "Sing-along videos with words that light up in time.",
        "karaoke sing lyrics words vocals singalong",
    ),
    _t(
        ToolId.AUDIO_CONVERTER,
        "Audio Converter",
        Category.AUDIO,
        "🔊",
        "Convert audio to MP3, AAC, FLAC, WAV, Opus and more.",
        "convert audio mp3 flac wav aac m4a opus ogg format",
    ),
    _t(
        ToolId.MIDIFIER,
        "MIDIfier",
        Category.AUDIO,
        "🎹",
        "Turn a recording into MIDI notes and play them back with instruments.",
        "midi notes piano music instrument transcribe",
    ),
    _t(
        ToolId.UNIVERSAL_DOWNSIZER,
        "Universal Downsizer",
        Category.TRANSCODER,
        "📉",
        "Shrink a video to fit a size limit - Discord, email, WhatsApp.",
        "smaller compress shrink size reduce limit discord email whatsapp mb big large file",
    ),
    _t(
        ToolId.PROXY_GENERATOR,
        "Proxy Generator",
        Category.TRANSCODER,
        "🪶",
        "Light copies for smooth editing in other editors.",
        "proxy edit lightweight premiere resolve davinci",
    ),
    _t(
        ToolId.CODEC_CRUNCHER,
        "Codec Cruncher",
        Category.TRANSCODER,
        "⚙",
        "Re-encode with full control - H.264, HEVC, AV1, ProRes, HDR.",
        "codec encode reencode h264 hevc h265 av1 prores hdr sdr quality bitrate",
    ),
    _t(
        ToolId.HDR_TO_SDR,
        "HDR to SDR",
        Category.TRANSCODER,
        "🌗",
        "Make HDR video from phones and cameras look right on normal screens.",
        "hdr sdr washed out grey gray faded dull iphone dolby vision hlg hdr10 tone map colours look wrong",
    ),
    _t(
        ToolId.TRACKS_AND_REMUX,
        "Tracks & Remux",
        Category.TRANSCODER,
        "🗂",
        "Keep, drop or label audio and subtitle tracks, add new ones, change MKV/MP4 - no re-encoding.",
        "tracks audio track subtitle track language dub remux mkv mp4 default forced add subtitles extract streams remove track commentary",
    ),
    _t(
        ToolId.COMPARE_AND_QUALITY,
        "Compare & Quality",
        Category.TRANSCODER,
        "📊",
        "How much quality did an encode lose? VMAF, SSIM, PSNR and the two side by side.",
        "compare quality vmaf ssim psnr difference before after check encode loss side by side a/b",
    ),
    _t(
        ToolId.FORMAT_CONVERTER,
        "Format Converter",
        Category.TRANSCODER,
        "🔄",
        "Convert to MP4, MKV, MOV, WebM, MP3... copying what fits.",
        "convert format mp4 mkv mov webm avi container change",
    ),
    _t(
        ToolId.RESOLUTION_SCALER,
        "Resolution Scaler",
        Category.TRANSCODER,
        "📐",
        "Upscale or downscale to any resolution - FSR, NIS and AI upscalers.",
        "resolution resize scale upscale downscale 4k 1080p 720p bigger sharper enhance ai super resolution fsr nis anime4k artcnn old low quality",
    ),
    _t(
        ToolId.FRAMERATE_INTERPOLATOR,
        "Framerate Interpolator",
        Category.TRANSCODER,
        "🎞",
        "Change the frame rate smoothly, or make true slow motion.",
        "framerate fps 60fps smooth slow motion slowmo interpolate",
    ),
    _t(
        ToolId.ENCODE_QUEUE,
        "Encode Queue",
        Category.TRANSCODER,
        "📋",
        "See and manage every job that's running or waiting.",
        "queue jobs tasks progress running",
    ),
    _t(
        ToolId.PRESET_MANAGER,
        "Preset Manager",
        Category.TRANSCODER,
        "💾",
        "Save your own FFmpeg recipes and run them on files.",
        "preset recipe custom ffmpeg command template",
    ),
    _t(
        ToolId.MEDIA_GENERATOR,
        "Media Generator",
        Category.VISUALS,
        "🧪",
        "Colour bars, test patterns, countdowns and tones.",
        "test pattern colour bars countdown tone leader generate",
    ),
    _t(
        ToolId.SPECIAL_EFFECTS,
        "Special Effects",
        Category.VISUALS,
        "✨",
        "Intros, outros, glitch, VHS, shake, zoom punch and more.",
        "effects glitch vhs retro shake zoom vignette light leak film grain",
    ),
    _t(
        ToolId.LUT_APPLICATOR,
        "LUT Applicator",
        Category.VISUALS,
        "🎨",
        "Apply a colour look (.cube LUT) at any strength.",
        "lut look grade cinematic film cube",
    ),
    _t(
        ToolId.BASIC_COLOR_CORRECTOR,
        "Basic Color Corrector",
        Category.VISUALS,
        "🎨",
        "Exposure, contrast, white balance and colour, with a live preview.",
        "colour color brightness contrast exposure saturation white balance dark bright correct",
    ),
    _t(
        ToolId.COLOUR_MATCH,
        "Colour Match",
        Category.VISUALS,
        "🎨",
        "Make one clip's colours match another's.",
        "colour color match grade consistent cameras",
    ),
    _t(
        ToolId.VIDEO_SCOPES,
        "Video Scopes",
        Category.VISUALS,
        "📊",
        "Waveform, parade, histogram and vectorscope.",
        "scopes waveform histogram vectorscope exposure analysis",
    ),
    _t(
        ToolId.SPEED_RAMPER,
        "Speed Ramper",
        Category.VISUALS,
        "⏩",
        "Speed up or slow down, smoothly or in ramps.",
        "speed fast faster slow slower ramp timelapse hyperlapse slow motion",
    ),
    _t(
        ToolId.DESHAKER,
        "Deshaker",
        Category.VISUALS,
        "🤳",
        "Steady shaky footage.",
        "shaky shake stabilise stabilize steady wobbly handheld",
    ),
    _t(
        ToolId.GREEN_SCREEN_KEYER,
        "Green Screen Keyer",
        Category.VISUALS,
        "🟩",
        "Remove a green or blue background and replace it.",
        "green screen chroma key background remove replace blue",
    ),
    _t(
        ToolId.DENOISE,
        "Denoise",
        Category.VISUALS,
        "🌫",
        "Clean up noisy, grainy or blocky video.",
        "noise grain grainy noisy dark low light blocky clean denoise",
    ),
    _t(
        ToolId.DEINTERLACE,
        "Deinterlace",
        Category.VISUALS,
        "📺",
        "Remove the comb lines from TV, DVD and camcorder video.",
        "interlaced comb lines tv dvd camcorder vhs deinterlace",
    ),
    _t(
        ToolId.SHARPEN,
        "Sharpen",
        Category.VISUALS,
        "🔍",
        "Bring back detail in soft or upscaled video.",
        "sharpen soft blurry detail crisp clarity",
    ),
    _t(
        ToolId.PICTURE_IN_PICTURE,
        "Picture-in-Picture",
        Category.VISUALS,
        "🖼",
        "Put one video in a corner of another.",
        "picture in picture pip overlay corner facecam webcam reaction",
    ),
    _t(
        ToolId.PRIVACY_BLUR,
        "Privacy Blur",
        Category.VISUALS,
        "🙈",
        "Blur or pixelate faces, number plates, names or screens.",
        "blur pixelate hide face plate license number privacy censor",
    ),
    _t(
        ToolId.ANIMATED_ZOOM,
        "Animated Zoom",
        Category.VISUALS,
        "🔎",
        "Punch-ins, Ken Burns moves and smooth pans.",
        "zoom punch in ken burns pan move",
    ),
    _t(
        ToolId.TRANSITION_STUDIO,
        "Transition Studio",
        Category.VISUALS,
        "🌀",
        "One polished transition between two clips.",
        "transition wipe fade slide two clips",
    ),
    _t(
        ToolId.MEDIA_INSPECTOR,
        "Media Inspector",
        Category.SYSTEM,
        "ℹ",
        "Everything about a file: codecs, HDR, rotation, tracks.",
        "info information inspect details codec properties what is",
    ),
    _t(
        ToolId.METADATA_CLEANER,
        "Metadata Cleaner",
        Category.SYSTEM,
        "🧹",
        "Remove GPS location, phone model, dates and names before sharing.",
        "metadata gps location privacy exif remove clean phone anonymous",
    ),
    _t(
        ToolId.SCREEN_RECORDER,
        "Screen Recorder",
        Category.SYSTEM,
        "⏺",
        "Record your screen, with sound.",
        "record screen capture desktop game tutorial",
    ),
    _t(
        ToolId.BATCH_PROCESSOR,
        "Batch Processor",
        Category.SYSTEM,
        "📦",
        "Do the same thing to a whole folder of videos.",
        "batch folder many files bulk all",
    ),
    _t(
        ToolId.ADVANCED_SETTINGS,
        "Advanced Settings",
        Category.SYSTEM,
        "⚙",
        "Network, proxy, skins, updates and system information.",
        "settings preferences network proxy skin theme update",
    ),
    _t(ToolId.SETTINGS, "Settings", Category.HIDDEN),
    _t(ToolId.HOME, "Home", Category.HIDDEN),
    _t(ToolId.START, "Start", Category.HIDDEN),
)

BY_ID: Final[Mapping[ToolId, ToolSpec]] = {s.id: s for s in SPECS}
_BY_NAME: Final[Mapping[str, ToolSpec]] = {s.name: s for s in SPECS}


def spec(tool: ToolId) -> ToolSpec:
    return BY_ID[tool]


def by_name(name: str) -> ToolSpec | None:
    """The tool with English display name *name* (None if there is none)."""
    return _BY_NAME.get(name)


def parse(value: str) -> ToolId | None:
    """A stored value as a ToolId: an id, or - from before ids existed - a
    display name.  None for anything else (a tool that no longer exists)."""
    try:
        return ToolId(value)
    except ValueError:
        found = _BY_NAME.get(value)
        return found.id if found is not None else None


def parse_all(values: Iterable[object]) -> list[ToolId]:
    """Stored ids/names as ToolIds, in order, without unknowns or repeats."""
    out: list[ToolId] = []
    for v in values:
        tid = parse(v) if isinstance(v, str) else None
        if tid is not None and tid not in out:
            out.append(tid)
    return out


def sidebar(category: Category) -> tuple[ToolSpec, ...]:
    return tuple(s for s in SPECS if s.category is category)
