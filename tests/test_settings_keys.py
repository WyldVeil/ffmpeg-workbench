"""Settings are declared once, with their types (core/settings.py): reading
one nobody declared is a typo waiting to happen."""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from core import settings

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "main.py", *(ROOT / "core").rglob("*.py"), *(ROOT / "tabs").rglob("*.py")]
_ACCESSORS = {"get", "set", "delete", "reset_key"}


def _literal_keys(tree: ast.AST):
    """Keys in settings.get("k"), settings.set("k", v), settings.update({"k": v}) and friends."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Name) and owner.id in ("settings", "_settings", "_st")):
            continue
        if node.func.attr in _ACCESSORS and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            yield node.args[0].value, node.lineno
        elif node.func.attr == "update" and node.args and isinstance(node.args[0], ast.Dict):
            for k in node.args[0].keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    yield k.value, node.lineno


def test_every_settings_key_in_the_code_is_declared():
    unknown = []
    for path in SOURCES:
        for key, line in _literal_keys(ast.parse(path.read_text(encoding="utf-8"))):
            if not settings.is_declared(key):
                unknown.append(f"{path.relative_to(ROOT)}:{line} {key!r}")
    assert not unknown, "declare these in core/settings.py:\n" + "\n".join(unknown)


def test_network_defaults_come_from_the_declarations():
    from core import network
    assert {s.key: s.default for s in settings.NETWORK} == network.DEFAULTS


@pytest.mark.parametrize(("setting", "raw", "expected"), [
    (settings.AUTO_OPEN_OUTPUT, "yes", True), (settings.AUTO_OPEN_OUTPUT, 0, False),
    (settings.MAX_PARALLEL_JOBS, "3", 3), (settings.STAR_SPEED, "1.5", 1.5), (settings.STAR_COUNT, 80.0, 80),
    (settings.DEFAULT_CRF, 20, "20"), (settings.RECENT_FILES, ["a"], ["a"]),
])
def test_values_are_converted_to_the_declared_type(setting, raw, expected):
    assert setting.coerce(raw) == expected and type(setting.coerce(raw)) is type(expected)


@pytest.mark.parametrize(("setting", "raw"), [
    (settings.HW_ENCODE, "warp-drive"), (settings.MAX_PARALLEL_JOBS, 9), (settings.STAR_SPEED, "fast"),
    (settings.AUTO_OPEN_OUTPUT, "maybe"), (settings.RECENT_FILES, "not a list"), (settings.STAR_COUNT, True),
])
def test_unusable_values_are_refused_on_write_and_defaulted_on_read(setting, raw):
    with pytest.raises(ValueError):
        setting.coerce(raw)
    assert setting.read(raw) == setting.default
    from core.errors import InputError
    with pytest.raises(InputError):
        setting.set(raw)


def test_a_hand_edited_file_never_stops_the_app(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"max_parallel_jobs": "lots", "auto_open_output": "true", "theme": None}))
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(path))
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.setattr(settings, "_cache_sig", None)
    assert settings.MAX_PARALLEL_JOBS.get() == 1
    assert settings.AUTO_OPEN_OUTPUT.get() is True
    data = settings.load_settings()
    assert data["max_parallel_jobs"] == 1 and data["auto_open_output"] is True and data["theme"] == "Classic"
    monkeypatch.setattr(settings, "_cache", None)
