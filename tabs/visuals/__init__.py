"""
tabs.visuals  -  Color grading, visual effects, and compositing tools
"""

# Tool classes are imported lazily (PEP 562) so that importing one tool
# never imports - or breaks on - its siblings.
import importlib

_EXPORTS = {
    "MediaGeneratorTab": "tabs.visuals.media_gen",
    "SpecialEffectsTab": "tabs.visuals.effects",
    "LUTApplicatorTab": "tabs.visuals.lut",
    "ColorCorrectorTab": "tabs.visuals.color_correct",
    "ColorMatchTab": "tabs.visuals.color_match",
    "VideoScopesTab": "tabs.visuals.scopes",
    "SpeedRamperTab": "tabs.visuals.speed_ramp",
    "DeshakerTab": "tabs.visuals.deshaker",
    "GreenKeyerTab": "tabs.visuals.green_key",
    "DenoiseTab": "tabs.visuals.denoise",
    "DeinterlaceTab": "tabs.visuals.deinterlace",
    "SharpenTab": "tabs.visuals.sharpen",
    "PIPTab": "tabs.visuals.pip",
    "RegionBlurTab": "tabs.visuals.region_blur",
    "AnimatedZoomTab": "tabs.visuals.zoom",
    "TransitionStudioTab": "tabs.visuals.transitions",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
