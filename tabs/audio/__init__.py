"""
tabs.audio  -  Audio engineering and processing tools
"""

# Tool classes are imported lazily (PEP 562) so that importing one tool
# never imports - or breaks on - its siblings.
import importlib

_EXPORTS = {
    "SilenceTab": "tabs.audio.silence",
    "AudioExtractorTab": "tabs.audio.extractor",
    "AudioReplacerTab": "tabs.audio.replacer",
    "LoudnessNormTab": "tabs.audio.loudness",
    "AudioSyncTab": "tabs.audio.sync",
    "MuterTab": "tabs.audio.muter",
    "AudioDynamicsTab": "tabs.audio.dynamics",
    "AudioMixerTab": "tabs.audio.mixer",
    "VoiceIsolationTab": "tabs.audio.voice_isolation",
    "VoiceChangerTab": "tabs.audio.voice_changer",
    "TTSVoiceOverTab": "tabs.audio.tts",
    "WaveformEditorTab": "tabs.audio.waveform",
    "LaughTrackRemoverTab": "tabs.audio.laugh_track",
    "AudioDuckerTab": "tabs.audio.ducker",
    "KaraokeTab": "tabs.audio.karaoke",
    "AudioExporterTab": "tabs.audio.exporter",
    "MIDIfierTab": "tabs.audio.midifier",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
