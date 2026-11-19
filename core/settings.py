"""
core/settings.py - Application settings (single source of truth).

Stored as JSON in the per-user config folder (see core/paths.py), never next
to the program, which may be read-only.  Writes are atomic (temp file +
fsync + os.replace, with a ``.bak`` of the previous version) and serialised by
a process-wide lock, and reads are cached until the file changes on disk.

Prefer the merge-style API so a component only writes the keys it owns::

    from core import settings
    settings.get("default_crf", "18")
    settings.set("language", "de_DE")
    settings.update({"network.proxy_enabled": True, "network.proxy_port": "8080"})

``load_settings()`` / ``save_settings(dict)`` remain for older callers.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
import threading
from typing import Any, Iterator

from core.paths import APP_ROOT, config_dir

_log = logging.getLogger("crossfader.settings")

SETTINGS_FILE = os.path.join(config_dir(), "settings.json")

_DEFAULTS: dict = {
    # rendering
    "default_crf": "18",
    "default_preset": "fast",
    "default_audio_bitrate": "192k",
    "hw_encode": "auto",                 # auto | off | nvenc | qsv | vaapi | amf | videotoolbox
    "overwrite_policy": "ask",           # ask | rename | overwrite
    "default_output_folder": "",
    "auto_open_output": False,
    "notify_on_complete": True,
    "max_parallel_jobs": 1,
    # tools
    "ffmpeg_path_override": "",
    "binary_source": "auto",             # auto | bundled | system
    "check_updates": True,
    # interface
    "theme": "Classic",
    "ui_scale": "auto",                  # auto | 1.0 | 1.25 | 1.5 | 1.75 | 2.0
    "native_dialogs": True,
    "play_startup_sound": True,
    "star_speed": 1.0,
    "star_count": 55,
    "language": "",
    "favourite_tabs": [],
    "recent_files": [],
}

_lock = threading.RLock()
_cache: dict | None = None
_cache_sig: tuple | None = None


def _sig(path: str) -> tuple | None:
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _migrate_legacy() -> None:
    """One-time copy of settings.json / skin.json from the program folder."""
    if os.path.exists(SETTINGS_FILE) or os.environ.get("CROSSFADER_CONFIG_DIR"):
        return
    legacy = os.path.join(APP_ROOT, "settings.json")
    legacy_skin = os.path.join(APP_ROOT, "skin.json")
    data: dict = {}
    try:
        if os.path.isfile(legacy):
            with open(legacy, encoding="utf-8") as fh:
                data = json.load(fh)
        if os.path.isfile(legacy_skin):
            with open(legacy_skin, encoding="utf-8") as fh:
                name = json.load(fh).get("name")
            if name:
                data["theme"] = name
    except (OSError, ValueError) as exc:
        _log.warning("Could not read legacy settings: %s", exc)
        return
    if data:
        _write(data)
        _log.info("Migrated settings from %s", APP_ROOT)


def _read_file() -> dict:
    for path in (SETTINGS_FILE, SETTINGS_FILE + ".bak"):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                if path.endswith(".bak"):
                    _log.warning("settings.json unreadable - recovered from backup")
                return data
        except (OSError, ValueError) as exc:
            _log.warning("Ignoring unreadable %s: %s", path, exc)
    return {}


def _write(data: dict) -> bool:
    folder = os.path.dirname(SETTINGS_FILE)
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings_", suffix=".json", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(SETTINGS_FILE):
            try:
                shutil.copy2(SETTINGS_FILE, SETTINGS_FILE + ".bak")
            except OSError:
                pass
        os.replace(tmp, SETTINGS_FILE)
        return True
    except OSError as exc:
        _log.error("Could not save settings to %s: %s", SETTINGS_FILE, exc)
        try:
            os.remove(tmp)  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return False


def _current() -> dict:
    """Merged defaults + file (cached; re-read when the file changes)."""
    global _cache, _cache_sig
    with _lock:
        if _cache is None:
            _migrate_legacy()
        sig = _sig(SETTINGS_FILE)
        if _cache is None or sig != _cache_sig:
            merged = dict(_DEFAULTS)
            merged.update(_read_file())
            _cache, _cache_sig = merged, sig
        return _cache


def load_settings() -> dict:
    """A copy of all settings (defaults filled in).  Never raises."""
    with _lock:
        return json.loads(json.dumps(_current()))


def save_settings(data: dict) -> bool:
    """Replace the stored settings with *data* (prefer :func:`update`)."""
    global _cache, _cache_sig
    with _lock:
        ok = _write(dict(data))
        if ok:
            _cache, _cache_sig = None, None
        return ok


def update(changes: dict) -> bool:
    """Merge *changes* into the stored settings atomically."""
    global _cache, _cache_sig
    with _lock:
        stored = _read_file()
        stored.update(changes)
        ok = _write(stored)
        _cache, _cache_sig = None, None
        return ok


def get(key: str, fallback: Any = None) -> Any:
    with _lock:
        val = _current().get(key, fallback)
    return json.loads(json.dumps(val)) if isinstance(val, (dict, list)) else val


def set(key: str, value: Any) -> bool:  # noqa: A001 - established public name
    return update({key: value})


def delete(key: str) -> bool:
    global _cache, _cache_sig
    with _lock:
        stored = _read_file()
        stored.pop(key, None)
        ok = _write(stored)
        _cache, _cache_sig = None, None
        return ok


@contextlib.contextmanager
def transaction() -> Iterator[dict]:
    """Read-modify-write under the lock::

        with settings.transaction() as s:
            s["recent_files"] = [p] + s.get("recent_files", [])
    """
    with _lock:
        stored = _read_file()
        yield stored
        update(stored)


def defaults() -> dict:
    return dict(_DEFAULTS)
