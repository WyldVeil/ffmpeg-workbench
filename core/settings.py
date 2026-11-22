"""
core/settings.py - Application settings (single source of truth).

Stored as JSON in the per-user config folder (see core/paths.py), never next
to the program, which may be read-only.  Writes are atomic (temp file +
fsync + os.replace, with a ``.bak`` of the previous version) and serialised by
a process-wide lock, and reads are cached until the file changes on disk.

Every setting is declared once, with its type, at the bottom of this file;
read and write it through that declaration::

    from core import settings
    settings.DEFAULT_CRF.get()                 # "18" - always a str
    settings.AUTO_OPEN_OUTPUT.get()            # always a bool, whatever the file holds
    settings.LANGUAGE.set("de_DE")             # checked; InputError if it can't be stored
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
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Generic, TypeVar

from core.errors import InputError
from core.paths import APP_ROOT, config_dir

logger = logging.getLogger("crossfader.settings")

SETTINGS_FILE = os.path.join(config_dir(), "settings.json")


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
        logger.warning("Could not read legacy settings: %s", exc)
        return
    if data:
        _write(data)
        logger.info("Migrated settings from %s", APP_ROOT)


def _read_file() -> dict:
    for path in (SETTINGS_FILE, SETTINGS_FILE + ".bak"):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                if path.endswith(".bak"):
                    logger.warning("settings.json unreadable - recovered from backup")
                return data
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable %s: %s", path, exc)
    return {}


def _readable(path: str) -> bool:
    """True when *path* holds a settings object (worth keeping as the backup)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return isinstance(json.load(fh), dict)
    except (OSError, ValueError):
        return False


def _write(data: dict) -> bool:
    folder = os.path.dirname(SETTINGS_FILE)
    tmp = ""
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings_", suffix=".json", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        if _readable(SETTINGS_FILE):                          # never let a broken file replace a good backup
            with contextlib.suppress(OSError):
                shutil.copy2(SETTINGS_FILE, SETTINGS_FILE + ".bak")
        os.replace(tmp, SETTINGS_FILE)
        return True
    except OSError as exc:
        logger.error("Could not save settings to %s: %s", SETTINGS_FILE, exc)
        if tmp:
            with contextlib.suppress(OSError):
                os.remove(tmp)
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
    """A copy of all settings (defaults filled in, declared keys checked and
    converted to their types).  Never raises."""
    with _lock:
        data = json.loads(json.dumps(_current()))
    for key, value in data.items():
        setting = REGISTRY.get(key)
        if setting is not None:
            data[key] = setting.read(value)
    return data


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
    """The value stored for *key* (prefer the typed ``SETTING.get()``).  For a
    declared key the stored value is checked and converted like ``get()`` would."""
    with _lock:
        data = _current()
        present = key in data
        val = data.get(key, fallback)
    setting = REGISTRY.get(key)
    if setting is not None and present:
        return setting.read(val)
    return json.loads(json.dumps(val)) if isinstance(val, (dict, list)) else val


def set(key: str, value: Any) -> bool:
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


# ─────────────────────────────────────────────────────────────────────────────
#  Typed settings
# ─────────────────────────────────────────────────────────────────────────────
#
# Robust Python ch. 9-10 and Effective Python item 51: settings are data that
# came from outside the program (a JSON file a person can edit), so they are
# checked once, at the boundary, and everything past it gets the right type.
# Each setting is declared exactly once below - its key, its default (which
# also fixes its type) and, where it has one, the values it may take:
#
#     if settings.AUTO_OPEN_OUTPUT.get(): ...          # a bool, whatever the file says
#     settings.HW_ENCODE.set("nvenc")                  # checked: InputError if not a choice
#
# A typo in a key is an AttributeError at import, not a silent default, and
# tests/test_settings_keys.py fails if code reads a key nobody declared.

T = TypeVar("T")

_TRUE: Final = frozenset({"1", "true", "yes", "on"})
_FALSE: Final = frozenset({"0", "false", "no", "off", ""})


def _as_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"not a yes/no value: {raw!r}")


@dataclass(frozen=True, slots=True)
class Setting(Generic[T]):
    """One setting.  ``default`` also fixes the type every read returns."""
    key: str
    default: T
    choices: tuple[T, ...] = ()

    def coerce(self, raw: object) -> T:
        """*raw* as this setting's type; raises ValueError when it can't be."""
        d: object = self.default
        value: object
        if isinstance(d, StrEnum):                        # before str: a StrEnum is a str
            try:
                value = type(d)(str(raw))
            except ValueError:
                raise ValueError(f"{self.key}: {raw!r} is not one of {[m.value for m in type(d)]}") from None
        elif isinstance(d, bool):
            value = _as_bool(raw)
        elif isinstance(d, (int, float)):
            try:
                if isinstance(raw, bool):
                    raise TypeError
                number = float(raw)                      # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise ValueError(f"{self.key}: expected a number, got {raw!r}") from None
            value = int(number) if isinstance(d, int) else number
        elif isinstance(d, str):
            if raw is None or isinstance(raw, (dict, list)):
                raise ValueError(f"{self.key}: expected text, got {raw!r}")
            value = str(raw)
        elif isinstance(raw, list):
            value = json.loads(json.dumps(raw))          # a private copy
        else:
            raise ValueError(f"{self.key}: expected a list, got {raw!r}")
        if self.choices and value not in self.choices:
            raise ValueError(f"{self.key}: {value!r} is not one of {self.choices}")
        return value  # type: ignore[return-value]

    def read(self, raw: object) -> T:
        """Like :meth:`coerce`, but an unusable stored value means the default
        (logged once per key: a hand-edited file must never stop the app)."""
        try:
            return self.coerce(raw)
        except (TypeError, ValueError) as exc:
            if self.key not in _complained:
                _complained[self.key] = None
                logger.warning("settings.json: %s - using the default %r", exc, self.default)
            return self.coerce(self.default)

    def get(self) -> T:
        with _lock:
            raw = _current().get(self.key, self.default)
        return self.read(raw)

    def set(self, value: T) -> bool:
        try:
            checked = self.coerce(value)
        except (TypeError, ValueError) as exc:
            raise InputError(str(exc)) from exc
        return update({self.key: checked})


_complained: dict[str, None] = {}               # keys already warned about (``set`` is taken here)

# rendering
DEFAULT_CRF: Final = Setting("default_crf", "18")
DEFAULT_PRESET: Final = Setting("default_preset", "fast")
DEFAULT_AUDIO_BITRATE: Final = Setting("default_audio_bitrate", "192k")
HW_ENCODE: Final = Setting("hw_encode", "auto", ("auto", "off", "nvenc", "qsv", "vaapi", "amf", "videotoolbox"))
OVERWRITE_POLICY: Final = Setting("overwrite_policy", "ask", ("ask", "rename", "overwrite"))
DEFAULT_OUTPUT_FOLDER: Final = Setting("default_output_folder", "")
AUTO_OPEN_OUTPUT: Final = Setting("auto_open_output", False)
NOTIFY_ON_COMPLETE: Final = Setting("notify_on_complete", True)
MAX_PARALLEL_JOBS: Final = Setting("max_parallel_jobs", 1, (1, 2, 3, 4))
# tools
FFMPEG_PATH_OVERRIDE: Final = Setting("ffmpeg_path_override", "")
BINARY_SOURCE: Final = Setting("binary_source", "auto", ("auto", "bundled", "system"))
CHECK_UPDATES: Final = Setting("check_updates", True)
# interface
THEME: Final = Setting("theme", "Classic")
UI_SCALE: Final = Setting("ui_scale", "auto")          # "auto" or a factor such as "1.5"
NATIVE_DIALOGS: Final = Setting("native_dialogs", True)
PLAY_STARTUP_SOUND: Final = Setting("play_startup_sound", True)
STAR_SPEED: Final = Setting("star_speed", 1.0)
STAR_COUNT: Final = Setting("star_count", 55)
LANGUAGE: Final = Setting("language", "")
FAVOURITE_TABS: Final = Setting[list]("favourite_tabs", [])
RECENT_FILES: Final = Setting[list]("recent_files", [])
RECENT_TOOLS: Final = Setting[list]("recent_tools", [])
class StartPage(StrEnum):
    """What the app opens on."""
    START = "start"           # "What do you want to do?"
    HOME = "home"             # every tool
    LAST = "last"             # the tool used last


START_PAGE: Final = Setting("start_page", StartPage.START)

# network (core/network.py explains each)
NETWORK: Final[tuple[Setting[Any], ...]] = (
    Setting("network.mode", "auto", ("auto", "direct", "manual", "offline")),
    Setting("network.strict", False),
    Setting("network.proxy_enabled", False),
    Setting("network.proxy_scheme", "http", ("http", "https", "socks4", "socks4a", "socks5", "socks5h")),
    Setting("network.proxy_host", "127.0.0.1"),
    Setting("network.proxy_port", ""),
    Setting("network.proxy_user", ""),
    Setting("network.proxy_pass", ""),
    Setting("network.no_proxy", "localhost,127.0.0.1,::1"),
    Setting("network.bandwidth_limit", ""),
    Setting("network.connect_timeout", "15"),
    Setting("network.socket_timeout", "60"),
    Setting("network.user_agent", ""),
    Setting("network.ip_version", "auto", ("auto", "ipv4", "ipv6")),
    Setting("network.source_address", ""),
    Setting("network.verify_ssl", True),
    Setting("network.system_certs", True),
    Setting("network.extra_ca", ""),
    Setting("network.allow_http", True),
    Setting("network.hf_mirror", ""),
    Setting("network.github_mirror", ""),
    Setting("network.wrapper", ""),
    Setting("network.tor_retries", "12"),
)

#: Keys made at run time from a fixed prefix (``last_dir.video``...).
FAMILIES: Final[tuple[str, ...]] = ("last_dir.", "custom_color_")

REGISTRY: Final[Mapping[str, Setting[Any]]] = {
    s.key: s for s in (
        DEFAULT_CRF, DEFAULT_PRESET, DEFAULT_AUDIO_BITRATE, HW_ENCODE, OVERWRITE_POLICY, DEFAULT_OUTPUT_FOLDER,
        AUTO_OPEN_OUTPUT, NOTIFY_ON_COMPLETE, MAX_PARALLEL_JOBS, FFMPEG_PATH_OVERRIDE, BINARY_SOURCE,
        CHECK_UPDATES, THEME, UI_SCALE, NATIVE_DIALOGS, PLAY_STARTUP_SOUND, STAR_SPEED, STAR_COUNT, LANGUAGE,
        FAVOURITE_TABS, RECENT_FILES, RECENT_TOOLS, START_PAGE, *NETWORK,
    )
}


def is_declared(key: str) -> bool:
    return key in REGISTRY or key.startswith(FAMILIES)


#: Defaults of the settings that live at the top level (network.* has its own merge rules).
_DEFAULTS: Final[dict[str, Any]] = {k: v.default for k, v in REGISTRY.items() if not k.startswith("network.")}
