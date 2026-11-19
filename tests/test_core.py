"""
tests/test_core.py  -  Tests for core utilities (no GUI required).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_app_version_format():
    """APP_VERSION is a semantic version string."""
    from core import APP_VERSION
    parts = APP_VERSION.split(".")
    assert len(parts) == 3, f"Expected major.minor.patch, got: {APP_VERSION}"
    assert all(p.isdigit() for p in parts), f"Non-numeric version parts: {APP_VERSION}"


def test_core_imports():
    """Core submodules import without error."""
    import core.hardware
    import core.settings
    import core.skins
    assert True


def test_settings_roundtrip(tmp_path, monkeypatch):
    """load_settings returns defaults when no file exists; save_settings is idempotent."""
    from core import settings as s
    fake_path = str(tmp_path / "settings.json")
    monkeypatch.setattr(s, "SETTINGS_FILE", fake_path)

    data = s.load_settings()
    assert isinstance(data, dict) and data["theme"] == "Classic"

    s.save_settings(data)
    assert s.load_settings() == data
    assert s.update({"theme": "Mystical", "x.y": 3})
    again = s.load_settings()
    assert again["theme"] == "Mystical" and again["x.y"] == 3
    assert s.get("x.y") == 3 and s.get("missing", "fb") == "fb"
    # a corrupt file falls back to the .bak copy
    with open(fake_path, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert s.load_settings()["theme"] in ("Classic", "Mystical")
