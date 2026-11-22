"""
tabs/registry.py  -  Single source of truth for which tools appear where.

Tools are registered as :class:`LazyTab` stand-ins: a tool's module is only
imported the first time its page is opened.  That keeps start-up fast and
means one broken or half-edited tool can never stop the app (or another
tool) from loading - the shell shows an error page for that tool instead.

Usage in main.py:
    from tabs.registry import TOOLS, PINNED, HIDDEN
    tab = TOOLS["…"][0][1](parent)          # imports + instantiates on demand
"""
import importlib


class LazyTab:
    """Callable stand-in for a tab class that imports it on first use."""

    def __init__(self, module: str, cls: str):
        self.module = module
        self.cls_name = cls
        self.__name__ = cls
        self._cls = None

    def load(self):
        """Import and return the real tab class (raises on import errors)."""
        if self._cls is None:
            self._cls = getattr(importlib.import_module(self.module), self.cls_name)
        return self._cls

    def __call__(self, *args, **kwargs):
        return self.load()(*args, **kwargs)

    def __repr__(self) -> str:
        return f"LazyTab({self.module}.{self.cls_name})"


def _lazy(module: str, cls: str) -> LazyTab:
    return LazyTab(module, cls)


# ── Pinned tool (always visible at top of sidebar, outside categories) ────────
PINNED = ("All-in-One Builder", _lazy("tabs.all_in_one", "AllInOneTab"))

# ── Hidden tools (accessible programmatically, not shown in sidebar nav) ──────
HIDDEN = {
    "Settings": _lazy("tabs.system.settings", "SettingsTab"),
    "Home":     _lazy("tabs.system.home", "HomeTab"),
    "Start":    _lazy("tabs.system.start", "StartTab"),
}

# ── Main tool registry (sidebar category → [(display_name, TabClass), ...]) ──
TOOLS = {
    "✂  CUTTING ROOM": [
        ("Crossfader",            _lazy("tabs.cutting.crossfader", "CrossfaderTab")),
        ("Quick Trimmer",         _lazy("tabs.cutting.trimmer", "TrimmerTab")),
        ("Manual Multi-Splitter", _lazy("tabs.cutting.splitter", "MultiSplitterTab")),
        ("The Splicer",           _lazy("tabs.cutting.splicer", "SplicerTab")),
        ("Batch Joiner",          _lazy("tabs.cutting.batch_joiner", "BatchJoinerTab")),
        ("Rotate & Flip",         _lazy("tabs.cutting.rotate_flip", "RotateFlipTab")),
        ("Side-by-Side",          _lazy("tabs.cutting.side_by_side", "SideBySideTab")),
        ("Beat Sync Cutter",      _lazy("tabs.cutting.beat_sync", "BeatSyncCutterTab")),
        ("Smart Reframe",         _lazy("tabs.cutting.smart_reframe", "SmartReframeTab")),
        ("Manual Crop",           _lazy("tabs.cutting.crop", "ManualCropTab")),
        ("Video Reverser",        _lazy("tabs.cutting.reverser", "ReverserTab")),
        ("Freeze Frame",          _lazy("tabs.cutting.freeze_frame", "FreezeFrameTab")),
        ("Clip Looper",           _lazy("tabs.cutting.clip_looper", "ClipLooperTab")),
        ("Multi-Clip Sequencer",  _lazy("tabs.cutting.sequencer", "SequencerTab")),
        ("Scene Detector",        _lazy("tabs.cutting.scene_detect", "SceneDetectTab")),
    ],
    "📱  SOCIAL & FORMAT": [
        ("The Shortifier",     _lazy("tabs.social.shortifier", "ShortifierTab")),
        ("Auto-Cropper",       _lazy("tabs.social.auto_cropper", "AutoCropperTab")),
        ("Pro GIF Maker",      _lazy("tabs.social.gif_maker", "GIFMakerTab")),
        ("WebM Maker",         _lazy("tabs.social.webm_maker", "WebMTab")),
        ("Watermarker",        _lazy("tabs.social.watermark", "TextBurnerTab")),
        ("Image Watermark",    _lazy("tabs.social.image_watermark", "ImageWatermarkTab")),
        ("Hard-Subber",        _lazy("tabs.social.hard_subber", "HardSubberTab")),
        ("Auto-Subtitles",     _lazy("tabs.social.auto_subs", "AutoSubsTab")),
        ("Animated Titles",    _lazy("tabs.social.titles", "TitlesGeneratorTab")),
        ("Frame Extractor",    _lazy("tabs.social.frame_extractor", "FrameExtractorTab")),
        ("Snapshot",           _lazy("tabs.social.snapshot", "SnapshotTab")),
        ("Intro/Outro Maker",  _lazy("tabs.social.intro_maker", "IntroMakerTab")),
        ("Slideshow Maker",    _lazy("tabs.social.slideshow", "SlideshowMakerTab")),
        ("Chapter Markers",    _lazy("tabs.social.chapter_markers", "ChapterMarkersTab")),
        ("Thumbnail Maker",    _lazy("tabs.social.thumbnail", "ThumbnailMakerTab")),
        ("Video Collage",      _lazy("tabs.social.collage", "VideoCollageTab")),
        ("Video Downloader",   _lazy("tabs.social.youtube", "YouTubeDownloaderTab")),
    ],
    "🔊  AUDIO ENGINEERING": [
        ("Silence Remover",     _lazy("tabs.audio.silence", "SilenceTab")),
        ("Audio Extractor",     _lazy("tabs.audio.extractor", "AudioExtractorTab")),
        ("Audio Replacer",      _lazy("tabs.audio.replacer", "AudioReplacerTab")),
        ("Loudness Normalizer", _lazy("tabs.audio.loudness", "LoudnessNormTab")),
        ("Audio Sync Shifter",  _lazy("tabs.audio.sync", "AudioSyncTab")),
        ("The Muter",           _lazy("tabs.audio.muter", "MuterTab")),
        ("Audio Dynamics",      _lazy("tabs.audio.dynamics", "AudioDynamicsTab")),
        ("Audio Mixer",         _lazy("tabs.audio.mixer", "AudioMixerTab")),
        ("Voice Isolation",     _lazy("tabs.audio.voice_isolation", "VoiceIsolationTab")),
        ("Voice Changer",       _lazy("tabs.audio.voice_changer", "VoiceChangerTab")),
        ("TTS Voice-Over",      _lazy("tabs.audio.tts", "TTSVoiceOverTab")),
        ("Waveform Editor",     _lazy("tabs.audio.waveform", "WaveformEditorTab")),
        ("Laugh Track Remover", _lazy("tabs.audio.laugh_track", "LaughTrackRemoverTab")),
        ("Music Ducker",        _lazy("tabs.audio.ducker", "AudioDuckerTab")),
        ("Karaoke Generator",   _lazy("tabs.audio.karaoke", "KaraokeTab")),
        ("Audio Converter",     _lazy("tabs.audio.exporter", "AudioExporterTab")),
        ("MIDIfier",            _lazy("tabs.audio.midifier", "MIDIfierTab")),
    ],
    "⚙  TRANSCODER": [
        ("Universal Downsizer",    _lazy("tabs.transcoder.downsizer", "UniversalDownsizerTab")),
        ("Proxy Generator",        _lazy("tabs.transcoder.proxy", "ProxyGenTab")),
        ("Codec Cruncher",         _lazy("tabs.transcoder.codec", "CodecCruncherTab")),
        ("HDR to SDR",             _lazy("tabs.transcoder.hdr", "HdrToSdrTab")),
        ("Tracks & Remux",         _lazy("tabs.transcoder.tracks", "TracksRemuxTab")),
        ("Compare & Quality",      _lazy("tabs.transcoder.compare", "CompareQualityTab")),
        ("Format Converter",       _lazy("tabs.transcoder.converter", "FormatConverterTab")),
        ("Resolution Scaler",      _lazy("tabs.transcoder.scaler", "ResolutionScalerTab")),
        ("Framerate Interpolator", _lazy("tabs.transcoder.scaler", "FPSInterpolatorTab")),
        ("Encode Queue",           _lazy("tabs.transcoder.encode_queue", "EncodeQueueTab")),
        ("Preset Manager",         _lazy("tabs.transcoder.presets", "PresetManagerTab")),
    ],
    "🎨  COLOR & VISUALS": [
        ("Media Generator",       _lazy("tabs.visuals.media_gen", "MediaGeneratorTab")),
        ("Special Effects",       _lazy("tabs.visuals.effects", "SpecialEffectsTab")),
        ("LUT Applicator",        _lazy("tabs.visuals.lut", "LUTApplicatorTab")),
        ("Basic Color Corrector", _lazy("tabs.visuals.color_correct", "ColorCorrectorTab")),
        ("Colour Match",          _lazy("tabs.visuals.color_match", "ColorMatchTab")),
        ("Video Scopes",          _lazy("tabs.visuals.scopes", "VideoScopesTab")),
        ("Speed Ramper",          _lazy("tabs.visuals.speed_ramp", "SpeedRamperTab")),
        ("Deshaker",              _lazy("tabs.visuals.deshaker", "DeshakerTab")),
        ("Green Screen Keyer",    _lazy("tabs.visuals.green_key", "GreenKeyerTab")),
        ("Denoise",               _lazy("tabs.visuals.denoise", "DenoiseTab")),
        ("Deinterlace",           _lazy("tabs.visuals.deinterlace", "DeinterlaceTab")),
        ("Sharpen",               _lazy("tabs.visuals.sharpen", "SharpenTab")),
        ("Picture-in-Picture",    _lazy("tabs.visuals.pip", "PIPTab")),
        ("Privacy Blur",          _lazy("tabs.visuals.region_blur", "RegionBlurTab")),
        ("Animated Zoom",         _lazy("tabs.visuals.zoom", "AnimatedZoomTab")),
        ("Transition Studio",     _lazy("tabs.visuals.transitions", "TransitionStudioTab")),
    ],
    "🛠  SYSTEM": [
        ("Media Inspector",   _lazy("tabs.system.media_inspector", "MediaInspectorTab")),
        ("Metadata Cleaner",  _lazy("tabs.system.metadata_cleaner", "MetadataCleanerTab")),
        ("Screen Recorder",   _lazy("tabs.system.screen_recorder", "ScreenRecorderTab")),
        ("Batch Processor",   _lazy("tabs.system.batch", "BatchTab")),
        ("Advanced Settings", _lazy("tabs.system.advanced_settings", "AdvancedSettingsTab")),
    ],
}
