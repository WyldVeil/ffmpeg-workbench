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
