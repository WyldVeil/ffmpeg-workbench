"""
tabs/registry.py  -  which page class builds each tool.

core/tools.py says what tools exist (their ids, names, sections and words);
this file says, for each :class:`~core.tools.ToolId`, which tab class builds
its page.  Pages are :class:`LazyTab` stand-ins: a tool's module is imported
the first time its page is opened, which keeps start-up fast and means one
broken tool can never stop the app (or another tool) from loading - the
shell shows an error page for that tool instead.

    page = PAGES[ToolId.QUICK_TRIMMER](parent)     # imports + builds on demand
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, Final

from core.tools import Category, ToolId, sidebar, spec


class LazyTab:
    """Callable stand-in for a tab class that imports it on first use."""

    def __init__(self, module: str, cls: str) -> None:
        self.module = module
        self.cls_name = cls
        self.__name__ = cls
        self._cls: type[Any] | None = None

    def load(self) -> type[Any]:
        """Import and return the real tab class (raises on import errors)."""
        cls = self._cls
        if cls is None:
            cls = self._cls = getattr(importlib.import_module(self.module), self.cls_name)
        return cls

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.load()(*args, **kwargs)

    def __repr__(self) -> str:
        return f"LazyTab({self.module}.{self.cls_name})"


def _lazy(module: str, cls: str) -> LazyTab:
    return LazyTab(module, cls)


PAGES: Final[Mapping[ToolId, LazyTab]] = {
    ToolId.ALL_IN_ONE_BUILDER: _lazy("tabs.all_in_one", "AllInOneTab"),
    ToolId.CROSSFADER: _lazy("tabs.cutting.crossfader", "CrossfaderTab"),
    ToolId.QUICK_TRIMMER: _lazy("tabs.cutting.trimmer", "TrimmerTab"),
    ToolId.MANUAL_MULTI_SPLITTER: _lazy("tabs.cutting.splitter", "MultiSplitterTab"),
    ToolId.SPLICER: _lazy("tabs.cutting.splicer", "SplicerTab"),
    ToolId.BATCH_JOINER: _lazy("tabs.cutting.batch_joiner", "BatchJoinerTab"),
    ToolId.ROTATE_AND_FLIP: _lazy("tabs.cutting.rotate_flip", "RotateFlipTab"),
    ToolId.SIDE_BY_SIDE: _lazy("tabs.cutting.side_by_side", "SideBySideTab"),
    ToolId.BEAT_SYNC_CUTTER: _lazy("tabs.cutting.beat_sync", "BeatSyncCutterTab"),
    ToolId.SMART_REFRAME: _lazy("tabs.cutting.smart_reframe", "SmartReframeTab"),
    ToolId.MANUAL_CROP: _lazy("tabs.cutting.crop", "ManualCropTab"),
    ToolId.VIDEO_REVERSER: _lazy("tabs.cutting.reverser", "ReverserTab"),
    ToolId.FREEZE_FRAME: _lazy("tabs.cutting.freeze_frame", "FreezeFrameTab"),
    ToolId.CLIP_LOOPER: _lazy("tabs.cutting.clip_looper", "ClipLooperTab"),
    ToolId.MULTI_CLIP_SEQUENCER: _lazy("tabs.cutting.sequencer", "SequencerTab"),
    ToolId.SCENE_DETECTOR: _lazy("tabs.cutting.scene_detect", "SceneDetectTab"),
    ToolId.SHORTIFIER: _lazy("tabs.social.shortifier", "ShortifierTab"),
    ToolId.AUTO_CROPPER: _lazy("tabs.social.auto_cropper", "AutoCropperTab"),
    ToolId.PRO_GIF_MAKER: _lazy("tabs.social.gif_maker", "GIFMakerTab"),
    ToolId.WEBM_MAKER: _lazy("tabs.social.webm_maker", "WebMTab"),
    ToolId.WATERMARKER: _lazy("tabs.social.watermark", "TextBurnerTab"),
    ToolId.IMAGE_WATERMARK: _lazy("tabs.social.image_watermark", "ImageWatermarkTab"),
    ToolId.HARD_SUBBER: _lazy("tabs.social.hard_subber", "HardSubberTab"),
    ToolId.AUTO_SUBTITLES: _lazy("tabs.social.auto_subs", "AutoSubsTab"),
    ToolId.ANIMATED_TITLES: _lazy("tabs.social.titles", "TitlesGeneratorTab"),
    ToolId.FRAME_EXTRACTOR: _lazy("tabs.social.frame_extractor", "FrameExtractorTab"),
    ToolId.SNAPSHOT: _lazy("tabs.social.snapshot", "SnapshotTab"),
    ToolId.INTRO_OUTRO_MAKER: _lazy("tabs.social.intro_maker", "IntroMakerTab"),
    ToolId.SLIDESHOW_MAKER: _lazy("tabs.social.slideshow", "SlideshowMakerTab"),
    ToolId.CHAPTER_MARKERS: _lazy("tabs.social.chapter_markers", "ChapterMarkersTab"),
    ToolId.THUMBNAIL_MAKER: _lazy("tabs.social.thumbnail", "ThumbnailMakerTab"),
    ToolId.VIDEO_COLLAGE: _lazy("tabs.social.collage", "VideoCollageTab"),
    ToolId.VIDEO_DOWNLOADER: _lazy("tabs.social.youtube", "YouTubeDownloaderTab"),
    ToolId.SILENCE_REMOVER: _lazy("tabs.audio.silence", "SilenceTab"),
    ToolId.AUDIO_EXTRACTOR: _lazy("tabs.audio.extractor", "AudioExtractorTab"),
    ToolId.AUDIO_REPLACER: _lazy("tabs.audio.replacer", "AudioReplacerTab"),
    ToolId.LOUDNESS_NORMALIZER: _lazy("tabs.audio.loudness", "LoudnessNormTab"),
    ToolId.AUDIO_SYNC_SHIFTER: _lazy("tabs.audio.sync", "AudioSyncTab"),
    ToolId.MUTER: _lazy("tabs.audio.muter", "MuterTab"),
    ToolId.AUDIO_DYNAMICS: _lazy("tabs.audio.dynamics", "AudioDynamicsTab"),
    ToolId.AUDIO_MIXER: _lazy("tabs.audio.mixer", "AudioMixerTab"),
    ToolId.VOICE_ISOLATION: _lazy("tabs.audio.voice_isolation", "VoiceIsolationTab"),
    ToolId.VOICE_CHANGER: _lazy("tabs.audio.voice_changer", "VoiceChangerTab"),
    ToolId.TTS_VOICE_OVER: _lazy("tabs.audio.tts", "TTSVoiceOverTab"),
    ToolId.WAVEFORM_EDITOR: _lazy("tabs.audio.waveform", "WaveformEditorTab"),
    ToolId.LAUGH_TRACK_REMOVER: _lazy("tabs.audio.laugh_track", "LaughTrackRemoverTab"),
    ToolId.MUSIC_DUCKER: _lazy("tabs.audio.ducker", "AudioDuckerTab"),
    ToolId.KARAOKE_GENERATOR: _lazy("tabs.audio.karaoke", "KaraokeTab"),
    ToolId.AUDIO_CONVERTER: _lazy("tabs.audio.exporter", "AudioExporterTab"),
    ToolId.MIDIFIER: _lazy("tabs.audio.midifier", "MIDIfierTab"),
    ToolId.UNIVERSAL_DOWNSIZER: _lazy("tabs.transcoder.downsizer", "UniversalDownsizerTab"),
    ToolId.PROXY_GENERATOR: _lazy("tabs.transcoder.proxy", "ProxyGenTab"),
    ToolId.CODEC_CRUNCHER: _lazy("tabs.transcoder.codec", "CodecCruncherTab"),
    ToolId.HDR_TO_SDR: _lazy("tabs.transcoder.hdr", "HdrToSdrTab"),
    ToolId.TRACKS_AND_REMUX: _lazy("tabs.transcoder.tracks", "TracksRemuxTab"),
    ToolId.COMPARE_AND_QUALITY: _lazy("tabs.transcoder.compare", "CompareQualityTab"),
    ToolId.FORMAT_CONVERTER: _lazy("tabs.transcoder.converter", "FormatConverterTab"),
    ToolId.RESOLUTION_SCALER: _lazy("tabs.transcoder.scaler", "ResolutionScalerTab"),
    ToolId.FRAMERATE_INTERPOLATOR: _lazy("tabs.transcoder.scaler", "FPSInterpolatorTab"),
    ToolId.ENCODE_QUEUE: _lazy("tabs.transcoder.encode_queue", "EncodeQueueTab"),
    ToolId.PRESET_MANAGER: _lazy("tabs.transcoder.presets", "PresetManagerTab"),
    ToolId.MEDIA_GENERATOR: _lazy("tabs.visuals.media_gen", "MediaGeneratorTab"),
    ToolId.SPECIAL_EFFECTS: _lazy("tabs.visuals.effects", "SpecialEffectsTab"),
    ToolId.LUT_APPLICATOR: _lazy("tabs.visuals.lut", "LUTApplicatorTab"),
    ToolId.BASIC_COLOR_CORRECTOR: _lazy("tabs.visuals.color_correct", "ColorCorrectorTab"),
    ToolId.COLOUR_MATCH: _lazy("tabs.visuals.color_match", "ColorMatchTab"),
    ToolId.VIDEO_SCOPES: _lazy("tabs.visuals.scopes", "VideoScopesTab"),
    ToolId.SPEED_RAMPER: _lazy("tabs.visuals.speed_ramp", "SpeedRamperTab"),
    ToolId.DESHAKER: _lazy("tabs.visuals.deshaker", "DeshakerTab"),
    ToolId.GREEN_SCREEN_KEYER: _lazy("tabs.visuals.green_key", "GreenKeyerTab"),
    ToolId.DENOISE: _lazy("tabs.visuals.denoise", "DenoiseTab"),
    ToolId.DEINTERLACE: _lazy("tabs.visuals.deinterlace", "DeinterlaceTab"),
    ToolId.SHARPEN: _lazy("tabs.visuals.sharpen", "SharpenTab"),
    ToolId.PICTURE_IN_PICTURE: _lazy("tabs.visuals.pip", "PIPTab"),
    ToolId.PRIVACY_BLUR: _lazy("tabs.visuals.region_blur", "RegionBlurTab"),
    ToolId.ANIMATED_ZOOM: _lazy("tabs.visuals.zoom", "AnimatedZoomTab"),
    ToolId.TRANSITION_STUDIO: _lazy("tabs.visuals.transitions", "TransitionStudioTab"),
    ToolId.MEDIA_INSPECTOR: _lazy("tabs.system.media_inspector", "MediaInspectorTab"),
    ToolId.METADATA_CLEANER: _lazy("tabs.system.metadata_cleaner", "MetadataCleanerTab"),
    ToolId.SCREEN_RECORDER: _lazy("tabs.system.screen_recorder", "ScreenRecorderTab"),
    ToolId.BATCH_PROCESSOR: _lazy("tabs.system.batch", "BatchTab"),
    ToolId.ADVANCED_SETTINGS: _lazy("tabs.system.advanced_settings", "AdvancedSettingsTab"),
    ToolId.SETTINGS: _lazy("tabs.system.settings", "SettingsTab"),
    ToolId.HOME: _lazy("tabs.system.home", "HomeTab"),
    ToolId.START: _lazy("tabs.system.start", "StartTab"),
}


# ── Name-keyed views, for code that still addresses pages by English name ────
#: sidebar heading -> [(name, page class)]
TOOLS: Final[dict[str, list[tuple[str, LazyTab]]]] = {
    cat.value: [(s.name, PAGES[s.id]) for s in sidebar(cat)]
    for cat in Category
    if cat not in (Category.PINNED, Category.HIDDEN)
}
PINNED: Final[tuple[str, LazyTab]] = (
    spec(ToolId.ALL_IN_ONE_BUILDER).name,
    PAGES[ToolId.ALL_IN_ONE_BUILDER],
)
HIDDEN: Final[dict[str, LazyTab]] = {s.name: PAGES[s.id] for s in sidebar(Category.HIDDEN)}
