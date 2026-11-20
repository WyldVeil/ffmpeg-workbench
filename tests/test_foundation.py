"""Tests for core/paths.py, core/outputs.py, core/ui.py and core/logs.py."""
import os
import threading
import time

import pytest

from core import outputs, paths


# ── paths ────────────────────────────────────────────────────────────────────

def test_env_overrides_dirs(tmp_path, monkeypatch):
    for kind in ("CONFIG", "DATA", "CACHE", "LOG"):
        monkeypatch.setenv(f"CROSSFADER_{kind}_DIR", str(tmp_path / kind.lower()))
    assert paths.config_dir() == str(tmp_path / "config")
    assert os.path.isdir(paths.data_dir())
    assert paths.cache_dir().endswith("cache")
    assert os.path.isdir(paths.log_dir())
    assert paths.models_dir().startswith(str(tmp_path / "data"))


def test_xdg_layout_on_linux(tmp_path, monkeypatch):
    if not os.name == "posix" or os.uname().sysname != "Linux":
        pytest.skip("Linux only")
    for kind in ("CONFIG", "DATA", "CACHE", "LOG"):
        monkeypatch.delenv(f"CROSSFADER_{kind}_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(paths, "is_portable", lambda: False)
    assert paths.config_dir() == str(tmp_path / "cfg" / paths.APP_SLUG)
    assert paths.log_dir() == str(tmp_path / "state" / paths.APP_SLUG / "logs")


def test_portable_mode_keeps_everything_in_the_program_folder(tmp_path, monkeypatch):
    for kind in ("CONFIG", "DATA", "CACHE", "LOG"):
        monkeypatch.delenv(f"CROSSFADER_{kind}_DIR", raising=False)
    monkeypatch.setattr(paths, "APP_ROOT", str(tmp_path))
    (tmp_path / "portable.txt").write_text("")
    assert paths.is_portable()
    assert paths.config_dir() == str(tmp_path / "userdata" / "config")
    assert paths.cache_dir() == str(tmp_path / "userdata" / "cache")


def test_portable_mode_on_a_read_only_drive_uses_the_usual_folders(tmp_path, monkeypatch):
    for kind in ("CONFIG", "DATA", "CACHE", "LOG"):
        monkeypatch.delenv(f"CROSSFADER_{kind}_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home-cfg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "home-cfg"))
    monkeypatch.setattr(paths, "APP_ROOT", str(tmp_path / "app"))
    monkeypatch.setattr(paths, "is_portable", lambda: True)
    monkeypatch.setattr(paths.os, "access", lambda path, mode: False)
    assert paths.config_dir().startswith(str(tmp_path / "home-cfg"))


def test_temp_dir_is_private_and_unique():
    a, b = paths.temp_dir(), paths.temp_dir()
    try:
        assert a != b and os.path.isdir(a) and os.path.isdir(b)
    finally:
        os.rmdir(a)
        os.rmdir(b)


def test_is_writable_dir(tmp_path):
    assert paths.is_writable_dir(str(tmp_path))
    assert not paths.is_writable_dir(str(tmp_path / "missing"))


# ── outputs ──────────────────────────────────────────────────────────────────

def test_unique_path(tmp_path):
    p = tmp_path / "clip.mp4"
    assert outputs.unique_path(str(p)) == str(p)
    p.write_bytes(b"x")
    assert outputs.unique_path(str(p)) == str(tmp_path / "clip (2).mp4")
    (tmp_path / "clip (2).mp4").write_bytes(b"x")
    assert outputs.unique_path(str(p)) == str(tmp_path / "clip (3).mp4")
    assert outputs.unique_path(str(tmp_path / "clip (2).mp4")) == str(tmp_path / "clip (3).mp4")


def test_suggest_output(tmp_path, monkeypatch):
    monkeypatch.setattr(outputs, "_setting", lambda k, d: d)
    src = tmp_path / "holiday video.mov"
    src.write_bytes(b"x")
    s = outputs.suggest_output(str(src), "trimmed", "mp4")
    assert s == str(tmp_path / "holiday video_trimmed.mp4")
    assert outputs.suggest_output(str(src), "x").endswith("holiday video_x.mov")
    (tmp_path / "holiday video_trimmed.mp4").write_bytes(b"x")
    assert outputs.suggest_output(str(src), "trimmed", ".mp4").endswith("_trimmed (2).mp4")


def test_resolve_output_refuses_input(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    with pytest.raises(outputs.OutputRejected) as ei:
        outputs.resolve_output(str(src), [str(src)], policy="overwrite")
    assert "same as an input" in str(ei.value)


def test_resolve_output_policies(tmp_path):
    existing = tmp_path / "out.mp4"
    existing.write_bytes(b"x")
    assert outputs.resolve_output(str(existing), policy="overwrite") == str(existing)
    assert outputs.resolve_output(str(existing), policy="rename") == str(tmp_path / "out (2).mp4")
    new = outputs.resolve_output(str(tmp_path / "sub" / "new"), policy="ask")
    assert new == str(tmp_path / "sub" / "new.mp4") and os.path.isdir(tmp_path / "sub")
    with pytest.raises(outputs.OutputRejected):
        outputs.resolve_output("", policy="ask")


# ── ui dispatcher ────────────────────────────────────────────────────────────

@pytest.mark.gui
def test_call_soon_from_threads_runs_on_main_thread():
    from tests.gui_harness import ensure_display
    ensure_display()
    import tkinter as tk

    from core import ui
    root = tk.Tk()
    try:
        ui.install(root, poll_ms=5)
        main = threading.get_ident()
        seen = []

        def worker(i):
            ui.call_soon(lambda: seen.append((i, threading.get_ident() == main)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        deadline = time.monotonic() + 3
        while len(seen) < 20 and time.monotonic() < deadline:
            root.update()
            time.sleep(0.005)
        assert len(seen) == 20 and all(ok for _, ok in seen)

        results = []
        ui.run_in_thread(lambda: 6 * 7, on_done=results.append)
        ui.run_in_thread(lambda: 1 / 0, on_error=lambda e: results.append(type(e).__name__))
        deadline = time.monotonic() + 3
        while len(results) < 2 and time.monotonic() < deadline:
            root.update()
            time.sleep(0.005)
        assert sorted(map(str, results)) == ["42", "ZeroDivisionError"]
    finally:
        root.destroy()
        ui._root = None


# ── logging ──────────────────────────────────────────────────────────────────

def test_logging_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSFADER_LOG_DIR", str(tmp_path / "logs"))
    from core import logs
    monkeypatch.setattr(logs, "_configured", False)
    import logging
    logger = logging.getLogger(logs.LOGGER_NAME)
    old = list(logger.handlers)
    logger.handlers = []
    try:
        logs.setup()
        logs.get("test").warning("hello from test")
        for h in logger.handlers:
            h.flush()
        text = open(logs.log_file(), encoding="utf-8").read()
        assert "hello from test" in text
    finally:
        for h in logger.handlers:
            h.close()
        logger.handlers = old
