"""Dropping files on the window.

Drag and drop comes from tkinterdnd2, which the release packages carry in
libs/.  That folder used to reach the import path only when the voices or the
network were first used, after the window had already tried (and failed) to
switch drag and drop on - so in every release, dropping a file did nothing.
The development setup has tkinterdnd2 installed elsewhere, which hid it."""

from __future__ import annotations

import os
import sys

import pytest

from core import deps
from tabs.kit import dialogs

#: Pages with nothing to drop a video into; a file dropped there opens in the Media Inspector.
NO_FILE_INPUT = {
    "Home",
    "Settings",
    "Advanced Settings",
    "Video Downloader",
    "Preset Manager",
    "Media Generator",
    "Screen Recorder",
}


def test_drag_and_drop_set_up_puts_libs_on_the_path_first(ui, monkeypatch):
    calls: list[str] = []
    real = deps.ensure_libs_on_path

    def recording() -> None:
        calls.append("libs")
        real()

    monkeypatch.setattr(deps, "ensure_libs_on_path", recording)
    ui.app.files.accept_drops(ui.app)
    assert calls, "accept_drops must put libs/ on the path before importing tkinterdnd2"


def test_main_puts_libs_on_the_path_before_the_window():
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "ensure_libs_on_path()" in src
    assert src.index("ensure_libs_on_path()") < src.index("from shell.app import App")


def test_libs_folder_is_added_when_present(tmp_path, monkeypatch):
    libs = tmp_path / "libs"
    libs.mkdir()
    monkeypatch.setattr(deps, "_LIBS_DIR", str(libs))
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(libs)])
    deps.ensure_libs_on_path()
    assert str(libs) in sys.path


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("clip.MP4", "video"),
        ("song.flac", "audio"),
        ("photo.jpeg", "image"),
        ("words.srt", "subtitle"),
        ("script.txt", "text"),
        ("thing.xyz", "other"),
    ],
)
def test_kind_of(name, kind):
    assert dialogs.kind_of(name) == kind


@pytest.mark.gui
def test_every_page_with_a_file_input_takes_a_dropped_video(ui, media):
    """Dropping a video on a tool loads it there; only pages with nothing to
    load it into hand it to the Media Inspector."""
    missed = []
    for name in ui.page_names():
        if name in NO_FILE_INPUT:
            continue
        ui.open(name)
        ui.app.update()
        before = ui.app.pages.current
        took = ui.app.files.open([media["h264"]])
        ui.app.update()
        if not took or ui.app.pages.current != before:
            missed.append(name)
    assert not missed, f"these pages ignore a dropped video: {missed}"


@pytest.mark.gui
def test_dropped_files_go_where_they_belong(ui, media):
    mixer = ui.open("Audio Mixer")
    assert mixer.load_file(media["mp3"])
    assert mixer.tracks[-1].path_var.get() == media["mp3"]

    intro = ui.open("Intro/Outro Maker")
    assert intro.load_file(media["png"])
    assert intro.bg_kind() == "file"
    assert intro.load_file(media["h264"])
    assert intro.attach_key() == "before"

    splicer = ui.open("The Splicer")
    count = len(splicer.segments)
    assert splicer.load_file(media["h264"])
    assert len(splicer.segments) == count + 1


@pytest.mark.gui
def test_drag_and_drop_is_switched_on(ui):
    """The main window can't be a drop target itself (tkinterdnd2 adds its
    methods to widgets only); registering it first used to abort the set-up."""
    pytest.importorskip("tkinterdnd2")
    ui.app.files.drag_and_drop = False
    ui.app.files.accept_drops(ui.app, ui.app.content)
    assert ui.app.files.drag_and_drop
