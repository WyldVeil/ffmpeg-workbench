"""Media Inspector + FramePreview/EncoderPanel widgets, driven headlessly."""

import pytest


def _pump(root, seconds):
    root.after(int(seconds * 1000), root.quit)
    root.mainloop()


@pytest.fixture
def tkroot():
    from tests.gui_harness import ensure_display

    ensure_display()
    import tkinter as tk

    from core import ui

    root = tk.Tk()
    with ui.hosted_by(root, poll_ms=5):
        yield root
    root.destroy()


def _wait(root, ready, seconds: float = 10.0) -> None:
    """Pump the event loop until *ready()* (the probe answers in the background)."""
    import time

    deadline = time.monotonic() + seconds
    while not ready():
        assert time.monotonic() < deadline, "timed out waiting for the page"
        _pump(root, 0.05)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_inspector_shows_streams_and_preview(tkroot, media):
    from tabs.system.media_inspector import MediaInspectorTab

    tab = MediaInspectorTab(tkroot)
    tab.pack(fill="both", expand=True)
    assert tab.load_file(media["hevc10"])
    _wait(tkroot, lambda: tab.info is not None)
    rows = [tab.tree.item(i, "values") for i in tab.tree.get_children()]
    assert any(r[1] == "video" and "hevc" in r[2] for r in rows)
    assert any(r[1] == "audio" and "opus" in r[2] for r in rows)
    assert "10-bit" in tab.summary_lbl.cget("text")
    for _ in range(40):
        _pump(tkroot, 0.1)
        if tab.preview._photo is not None:
            break
    assert tab.preview._photo is not None, "frame preview never rendered"
    report = tab.report_text()
    assert "hevc" in report and "Streams" in report


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_inspector_diagnostics(tkroot, media, tmp_path):
    from core import ffmpeg as ff
    from tabs.system.media_inspector import MediaInspectorTab, diagnose

    notes = dict((m, lvl) for lvl, m in diagnose(ff.probe(media["vertical_noaudio"])))
    assert any("No audio" in m for m in notes)
    tab = MediaInspectorTab(tkroot)
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"nope" * 50)
    tab.load_file(str(junk))
    _wait(tkroot, lambda: "Not a readable" in tab.summary_lbl.cget("text"))
    assert tab.info is None
    assert tab.load_file(media["mp3"])  # audio-only: no crash, no picture
    _wait(tkroot, lambda: tab.info is not None)
    _pump(tkroot, 0.3)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_frame_preview_coalesces_requests(tkroot, media):
    from tabs.widgets import FramePreview

    fp = FramePreview(tkroot, width=320, height=180)
    fp.pack()
    fp.load(media["h264"], duration=6.0, at=0.0)
    for i in range(30):  # simulate a fast scrub
        fp.show(i * 0.2)
    for _ in range(60):
        _pump(tkroot, 0.1)
        if fp._shown_t is not None and fp._shown_t > 5.0 and not fp._busy:
            break
    assert fp._shown_t is not None and fp._shown_t > 5.0, fp._shown_t


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_encoder_panel_builds_working_args(tkroot, outdir):
    from core import ffmpeg as ff
    from tabs.widgets import EncoderPanel

    panel = EncoderPanel(tkroot)
    panel.pack()
    _pump(tkroot, 0.5)
    panel.set_codec("hevc")
    ve = panel.video_encode("mp4")
    assert ve.codec == "hevc" and "-c:v" in ve.args
    out = str(outdir / "p.mp4")
    res = ff.run(
        [
            ff.ffmpeg_path(),
            "-y",
            *ve.pre_input,
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30",
            "-t",
            "1",
            *ve.vf_args(),
            *ve.args,
            out,
        ]
    )
    assert res.ok, res.error_summary
