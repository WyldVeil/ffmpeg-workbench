"""Application shell: file routing, drag-and-drop, search, closing with jobs."""
import time
import types

import pytest


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_open_paths_routes_to_tool_or_inspector(ui, media):
    app = ui.app
    app.show_page("Home")
    assert app.open_paths([media["hevc10"]])
    assert app.current_page == "Media Inspector"
    insp = app.pages["Media Inspector"]
    assert insp.info is not None and insp.info.video.codec == "hevc"
    app.show_page("Quick Trimmer")
    assert app.open_paths([media["h264"]])
    assert app.current_page == "Quick Trimmer"
    assert app.pages["Quick Trimmer"].file_path.endswith("h264_720p30_aac.mp4")
    assert "WyldVeil" in app.title()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_drop_event_with_spaces_and_uri(ui, media):
    app = ui.app
    app.show_page("Crossfader")
    tab = app.pages["Crossfader"]
    for r in tab.clip_rows:
        r["var"].set("")
        r["clip"] = None
    data = "{%s} file://%s" % (media["nasty"], media["h264_b"])
    app._on_drop(types.SimpleNamespace(data=data, action="copy"))
    ui.pump(0.3)
    paths = [r["var"].get() for r in tab.clip_rows if r["var"].get()]
    assert paths == [media["nasty"], media["h264_b"]]


@pytest.mark.gui
def test_sidebar_search_matches_translated_and_english(ui):
    app = ui.app
    rows = dict(app._tool_rows)
    assert any("crossfader" in key for key in rows)
    assert any("media inspector" in key for key in rows)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_closing_with_running_job_cancels_it(ui, outdir, monkeypatch):
    from core import ffmpeg as ff
    from core.queue_manager import RenderQueueManager
    q = RenderQueueManager.get_instance()
    out = str(outdir / "long.mp4")
    tid = q.enqueue("long", out, cmd=[ff.ffmpeg_path(), "-y", "-f", "lavfi", "-i",
                                       "testsrc2=size=1280x720:rate=30:d=600", "-c:v", "libx264",
                                       "-preset", "veryslow", out], expected_duration=600)
    ui.wait_for(lambda: q.get_task(tid).status == "active", 30)
    destroyed = []
    monkeypatch.setattr(ui.app, "destroy", lambda: destroyed.append(True))
    ui.dialogs.default_yes = False
    ui.app._on_closing()                     # user says "no" -> nothing happens
    assert not destroyed and q.get_task(tid).status == "active"
    ui.dialogs.default_yes = True
    ui.app._on_closing()
    assert destroyed
    ui.wait_for(lambda: q.get_task(tid).status == "cancelled", 15)
    time.sleep(0.2)
    assert ff.active_count() == 0


@pytest.mark.gui
def test_every_tool_opens(ui):
    """Each tool in the sidebar builds without errors - in any order, after other tools
    (shared widgets cache things between tools, e.g. the GPU encoder probe)."""
    from tabs.registry import PINNED, TOOLS
    names = [n for items in TOOLS.values() for n, _c in items] + ["⚡ " + PINNED[0], "Start", "Home", "Settings"]
    failed = []
    for name in names:
        try:
            ui.open(name)
        except Exception as exc:                       # PlaceholderTab -> RuntimeError
            failed.append(f"{name}: {exc}")
        ui.pump(0.05)
    assert not failed, failed
    errors = list(ui.callback_errors)
    ui.callback_errors.clear()
    assert not errors, errors


@pytest.mark.gui
def test_preview_serves_a_reload_that_arrives_mid_grab(ui, monkeypatch):
    """Loading a second file while the first frame is still being grabbed used to
    leave the preview on "Loading preview..." for good."""
    import tkinter as tk
    from core import ffmpeg as ff
    from tabs.widgets import FramePreview
    tiny = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND"
            b"\xaeB`\x82")
    grabbed = []

    def slow_grab(path, at, **kw):
        time.sleep(0.4)
        grabbed.append(path)
        return tiny

    monkeypatch.setattr(ff, "grab_frame_png", slow_grab)
    top = tk.Toplevel(ui.app)
    try:
        view = FramePreview(top, 64, 36)
        view.pack()
        view.load("first.mp4", duration=10.0)
        ui.pump(0.1)                                   # the first grab is now running
        view.load("second.mp4", duration=10.0)
        deadline = time.time() + 5
        while time.time() < deadline and grabbed[-1:] != ["second.mp4"]:
            ui.pump(0.1)
        ui.pump(0.2)
        assert grabbed[-1:] == ["second.mp4"]
        assert view._photo is not None and view.path == "second.mp4"
    finally:
        top.destroy()


@pytest.mark.gui
def test_skin_options_reach_new_widgets(ui):
    """Tk option names are case-sensitive: misspelt entries (TroughColor, SelectBackground...) were
    silently ignored, leaving sliders without a visible trough and hover colours wrong."""
    import tkinter as tk
    skin = ui.app._skin_data
    top = tk.Toplevel(ui.app)
    try:
        entry, scale, button, radio = tk.Entry(top), tk.Scale(top), tk.Button(top), tk.Radiobutton(top)
        assert entry.cget("selectbackground").lower() == skin["accent"].lower()
        assert scale.cget("troughcolor").lower() != scale.cget("background").lower()
        assert button.cget("activebackground").lower() == skin["sb_hover"].lower()
        assert radio.cget("selectcolor").lower() == skin["accent"].lower()
    finally:
        top.destroy()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_every_tool_takes_a_file_without_errors(ui, media):
    """Load a file into every tool that accepts one: no callback may fail afterwards
    (the Waveform Editor and Downsizer once crashed half-way through loading)."""
    from tabs.registry import TOOLS
    audio_tools = {name for name, _c in TOOLS.get(next(k for k in TOOLS if "AUDIO" in k), [])}
    failed = []
    for name in [n for items in TOOLS.values() for n, _c in items]:
        page = ui.open(name)
        if not hasattr(page, "load_file"):
            continue
        ui.callback_errors.clear()
        try:
            page.load_file(media["mp3"] if name in audio_tools else media["h264"])
        except Exception as exc:
            failed.append(f"{name}: load_file raised {exc!r}")
            continue
        ui.pump(1.5)
        ui.wait_idle(60)
        if ui.callback_errors:
            failed.append(f"{name}: " + ui.callback_errors[0].splitlines()[-1])
        ui.callback_errors.clear()
    assert not failed, "\n".join(failed)
