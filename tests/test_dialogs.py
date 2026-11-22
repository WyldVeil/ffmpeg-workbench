"""Tests for core/dialogs.py (native Linux dialogs are exercised with fake tools)."""

import os
import stat
import sys

import pytest

from core import dialogs


def test_filetypes_case_insensitive_on_posix():
    ft = dialogs.filetypes("video")
    label, pats = ft[0]
    assert label == "Video files" and ft[-1] == ("All files", "*")
    if not sys.platform.startswith("win"):
        assert "*.mp4" in pats and "*.MP4" in pats
    assert dialogs.is_kind("/x/CLIP.MOV", "video") and not dialogs.is_kind("a.srt", "video")


def _fake_tool(tmp_path, name, script):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSFADER_CONFIG_DIR", str(tmp_path / "cfg"))
    import core.settings as cs

    monkeypatch.setattr(cs, "SETTINGS_FILE", str(tmp_path / "cfg" / "settings.json"), raising=False)
    return tmp_path


@pytest.mark.skipif(sys.platform.startswith("win") or sys.platform == "darwin", reason="Linux only")
@pytest.mark.gui
@pytest.mark.parametrize("tool,desktop", [("kdialog", "KDE"), ("zenity", "GNOME")])
def test_native_open_save_and_cancel(tool, desktop, isolated, monkeypatch):
    from tests.gui_harness import ensure_display

    ensure_display()
    import tkinter as tk

    tmp = isolated
    bindir = tmp / "bin"
    bindir.mkdir()
    picked = tmp / "videos" / "Clip One.MP4"
    picked.parent.mkdir()
    picked.write_bytes(b"x")
    log = tmp / "args.txt"
    _fake_tool(
        bindir,
        tool,
        f'printf "%s\\n" "$@" > "{log}"\n'
        f'case "$*" in *save*) echo "{tmp}/out/new";; '
        f'*directory*) echo "{tmp}/videos";; *) echo "{picked}";; esac\n',
    )
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setenv("CROSSFADER_NATIVE_DIALOGS", "1")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.setenv("DESKTOP_SESSION", desktop.lower())
    monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":0"))
    root = tk.Tk()
    try:
        assert dialogs._native_tool() == tool
        assert dialogs.ask_open("video", parent=root) == str(picked)
        args = log.read_text()
        assert "*.MP4" in args  # case-insensitive filter passed through
        saved = dialogs.ask_save("x.mp4", kind="video", parent=root)
        assert saved == f"{tmp}/out/new.mp4" and dialogs.was_confirmed(saved)
        assert dialogs.ask_directory(parent=root) == f"{tmp}/videos"
        # cancel -> ""
        _fake_tool(bindir, tool, "exit 1\n")
        assert dialogs.ask_open("video", parent=root) == ""
    finally:
        root.destroy()


def test_falls_back_to_tk(isolated, monkeypatch):
    monkeypatch.setenv("CROSSFADER_NATIVE_DIALOGS", "0")
    from tkinter import filedialog

    seen = {}

    def fake(**kw):
        seen.update(kw)
        return "/some/where/file.mkv"

    monkeypatch.setattr(filedialog, "askopenfilename", fake)
    assert dialogs.ask_open("video", title="Pick") == "/some/where/file.mkv"
    assert seen["title"] == "Pick" and seen["filetypes"][0][0] == "Video files"
