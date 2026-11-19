"""
tabs.cutting  -  Timeline cutting and arrangement tools
"""

# Tool classes are imported lazily (PEP 562) so that importing one tool
# never imports - or breaks on - its siblings.
import importlib

_EXPORTS = {
    "CrossfaderTab": "tabs.cutting.crossfader",
    "TrimmerTab": "tabs.cutting.trimmer",
    "MultiSplitterTab": "tabs.cutting.splitter",
    "SplicerTab": "tabs.cutting.splicer",
    "BatchJoinerTab": "tabs.cutting.batch_joiner",
    "RotateFlipTab": "tabs.cutting.rotate_flip",
    "SideBySideTab": "tabs.cutting.side_by_side",
    "BeatSyncCutterTab": "tabs.cutting.beat_sync",
    "SmartReframeTab": "tabs.cutting.smart_reframe",
    "ManualCropTab": "tabs.cutting.crop",
    "ReverserTab": "tabs.cutting.reverser",
    "FreezeFrameTab": "tabs.cutting.freeze_frame",
    "ClipLooperTab": "tabs.cutting.clip_looper",
    "SequencerTab": "tabs.cutting.sequencer",
    "SceneDetectTab": "tabs.cutting.scene_detect",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
