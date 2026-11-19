"""
tabs.transcoder  -  Encoding, format conversion, and codec tools
"""

# Tool classes are imported lazily (PEP 562) so that importing one tool
# never imports - or breaks on - its siblings.
import importlib

_EXPORTS = {
    "UniversalDownsizerTab": "tabs.transcoder.downsizer",
    "ProxyGenTab": "tabs.transcoder.proxy",
    "CodecCruncherTab": "tabs.transcoder.codec",
    "HdrToSdrTab": "tabs.transcoder.hdr",
    "FormatConverterTab": "tabs.transcoder.converter",
    "ResolutionScalerTab": "tabs.transcoder.scaler",
    "FPSInterpolatorTab": "tabs.transcoder.scaler",
    "EncodeQueueTab": "tabs.transcoder.encode_queue",
    "PresetManagerTab": "tabs.transcoder.presets",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
