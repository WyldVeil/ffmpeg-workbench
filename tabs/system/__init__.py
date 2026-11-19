"""
tabs.system  -  System-level and application tools
"""

# Tool classes are imported lazily (PEP 562) so that importing one tool
# never imports - or breaks on - its siblings.
import importlib

_EXPORTS = {
    "MediaInspectorTab": "tabs.system.media_inspector",
    "ScreenRecorderTab": "tabs.system.screen_recorder",
    "BatchTab": "tabs.system.batch",
    "AdvancedSettingsTab": "tabs.system.advanced_settings",
    "SettingsTab": "tabs.system.settings",
    "HomeTab": "tabs.system.home",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
