"""
tabs.social  -  Social media, format, and delivery tools
"""

# Tool classes are imported lazily (PEP 562) so that importing one tool
# never imports - or breaks on - its siblings.
import importlib

_EXPORTS = {
    "ShortifierTab": "tabs.social.shortifier",
    "AutoCropperTab": "tabs.social.auto_cropper",
    "GIFMakerTab": "tabs.social.gif_maker",
    "WebMTab": "tabs.social.webm_maker",
    "TextBurnerTab": "tabs.social.watermark",
    "ImageWatermarkTab": "tabs.social.image_watermark",
    "HardSubberTab": "tabs.social.hard_subber",
    "AutoSubsTab": "tabs.social.auto_subs",
    "TitlesGeneratorTab": "tabs.social.titles",
    "FrameExtractorTab": "tabs.social.frame_extractor",
    "IntroMakerTab": "tabs.social.intro_maker",
    "SlideshowMakerTab": "tabs.social.slideshow",
    "ChapterMarkersTab": "tabs.social.chapter_markers",
    "ThumbnailMakerTab": "tabs.social.thumbnail",
    "VideoCollageTab": "tabs.social.collage",
    "YouTubeDownloaderTab": "tabs.social.youtube",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
