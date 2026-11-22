"""Application shell: file routing, drag-and-drop, search, closing with jobs."""

import sys
import time
import tkinter as tk
import types

import pytest

from core.tools import ToolId


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_open_paths_routes_to_tool_or_inspector(ui, media):
    app = ui.app
    app.show_page("Home")
    assert app.open_paths([media["hevc10"]])
    assert app.current_page is ToolId.MEDIA_INSPECTOR
    insp = app.page(ToolId.MEDIA_INSPECTOR)
    ui.wait_for(lambda: insp.info is not None)
    assert insp.info is not None and insp.info.require_video().codec == "hevc"
    app.show_page("Quick Trimmer")
    assert app.open_paths([media["h264"]])
    assert app.current_page is ToolId.QUICK_TRIMMER
    assert app.page(ToolId.QUICK_TRIMMER).file_path.endswith("h264_720p30_aac.mp4")
    assert "WyldVeil" in app.title()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_drop_event_with_spaces_and_uri(ui, media):
    app = ui.app
    app.show_page("Crossfader")
    tab = app.page(ToolId.CROSSFADER)
    for r in tab.clip_rows:
        r["var"].set("")
        r["clip"] = None
    data = "{{{}}} file://{}".format(media["nasty"], media["h264_b"])
    app.files._dropped(types.SimpleNamespace(data=data, action="copy"))
    ui.pump(0.3)
    paths = [r["var"].get() for r in tab.clip_rows if r["var"].get()]
    assert paths == [media["nasty"], media["h264_b"]]


@pytest.mark.gui
def test_sidebar_search_matches_translated_and_english(ui):
    app = ui.app
    rows = dict(app.sidebar._rows)
    assert any("crossfader" in key for key in rows)
    assert any("media inspector" in key for key in rows)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_closing_with_running_job_cancels_it(ui, outdir, monkeypatch):
    from core import ffmpeg as ff
    from core.queue_manager import RenderQueueManager

    q = RenderQueueManager.get_instance()

    def status(task_id: str) -> str:
        task = q.get_task(task_id)
        assert task is not None
        return task.status

    out = str(outdir / "long.mp4")
    tid = q.enqueue(
        "long",
        out,
        work=[
            [
                ff.ffmpeg_path(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=30:d=600",
                "-c:v",
                "libx264",
                "-preset",
                "veryslow",
                out,
            ]
        ],
        expected_duration=600,
    )
    ui.wait_for(lambda: status(tid) == "active", 30)
    destroyed = []
    monkeypatch.setattr(ui.app, "destroy", lambda: destroyed.append(True))
    ui.dialogs.default_yes = False
    ui.app.quit_app()  # user says "no" -> nothing happens
    assert not destroyed and status(tid) == "active"
    ui.dialogs.default_yes = True
    ui.app.quit_app()
    assert destroyed
    ui.wait_for(lambda: status(tid) == "cancelled", 15)
    time.sleep(0.2)
    assert ff.active_count() == 0


@pytest.mark.gui
def test_every_tool_opens(ui):
    """Each tool in the sidebar builds without errors - in any order, after other tools
    (shared widgets cache things between tools, e.g. the GPU encoder probe)."""
    failed = []
    for tool in ToolId:
        name = tool.value
        try:
            ui.open(tool)
        except Exception as exc:  # PlaceholderTab -> RuntimeError
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

    tiny = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND"
        b"\xaeB`\x82"
    )
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
        ui.pump(0.1)  # the first grab is now running
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

    skin = ui.app.skin
    top = tk.Toplevel(ui.app)
    try:
        entry, scale, button, radio = (
            tk.Entry(top),
            tk.Scale(top),
            tk.Button(top),
            tk.Radiobutton(top),
        )
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


# ─────────────────────────────────────────────────────────────────────────────
#  Mouse wheel
# ─────────────────────────────────────────────────────────────────────────────


def _x11_buttons():
    """True when the wheel arrives as buttons 4/5 (X11 with Tk 8.6).  Tk 9 sends
    <MouseWheel> everywhere and uses buttons 4/5 for the back/forward buttons."""
    import tkinter as tk

    return tk.TkVersion < 9 and not sys.platform.startswith(("win", "darwin"))


def _wheel_down(ui, widget):
    """One notch down, the way this Tk reports it."""
    _wheel(ui, widget, **(dict(button=5) if _x11_buttons() else dict(delta=-120)))


def _wheel(ui, widget, button=None, delta=None):
    """Turn the wheel with the pointer over the middle of *widget*: X11 style
    (button 4 = up, 5 = down) or Windows / Tk 9 style (delta > 0 = up)."""
    widget.update_idletasks()
    x, y = widget.winfo_width() // 2, widget.winfo_height() // 2
    where = dict(x=x, y=y, rootx=widget.winfo_rootx() + x, rooty=widget.winfo_rooty() + y)
    if button:
        widget.event_generate(f"<Button-{button}>", **where)
    else:
        widget.event_generate("<MouseWheel>", delta=delta, **where)
    ui.pump(0.05)


def _page_canvas(ui, name) -> tk.Canvas:
    """Open a tool and return its scrolling canvas, made to need scrolling."""
    ui.app.geometry("1280x560")
    tab = ui.open(name)
    ui.pump(0.5)
    stack = [tab]
    while stack:
        w = stack.pop(0)
        if isinstance(w, tk.Canvas) and w.winfo_viewable() and w.cget("yscrollcommand"):
            first, last = w.yview()
            if first > 0 or last < 1:
                w.yview_moveto(0)
                return w
        stack.extend(w.winfo_children())
    pytest.skip(f"{name} fits the window without scrolling")
    raise AssertionError  # unreachable: skip() raises


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_wheel_units_follow_the_wheel():
    """Down is positive (scrolls down), on X11 buttons and on <MouseWheel> deltas alike;
    small touchpad steps add up to whole notches."""
    from shell import wheel

    carry = [0]

    def units(num=0, delta=0):
        steps, carry[0] = wheel.units(num, delta, carry[0])
        return steps

    n = wheel.UNITS_PER_NOTCH
    assert units(num=4) == -n and units(num=5) == n
    assert units(delta=120) == -n and units(delta=-240) == 2 * n
    assert [units(delta=-40) for _ in range(3)] == [0, 0, n]
    assert units(delta=60) == 0 and units(delta=-120) == n  # a turn back starts afresh


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_wheel_scrolls_sidebar_and_pages_the_way_it_turns(ui):
    """Wheel down scrolls down.  On Linux (Tk 8.6 reports the wheel as buttons 4/5)
    the sidebar and every page used to scroll the opposite way."""
    page = _page_canvas(ui, "Universal Downsizer")  # also makes the window short
    styles = [(dict(delta=-120), dict(delta=120))]
    if _x11_buttons():
        styles.append((dict(button=5), dict(button=4)))
    for canvas in (ui.app.sidebar.canvas, page):
        for down, up in styles:
            canvas.yview_moveto(0)
            _wheel(ui, canvas, **down)
            after_down = canvas.yview()[0]
            assert after_down > 0, (canvas, down)
            _wheel(ui, canvas, **up)
            assert canvas.yview()[0] < after_down, (canvas, up)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_wheel_over_a_drop_down_scrolls_the_page_unless_it_has_focus(ui):
    """Passing over a drop-down while scrolling a page must not change the setting."""
    from tkinter import ttk

    page = _page_canvas(ui, "Universal Downsizer")
    stack: list[tk.Misc] = [page]
    boxes = []
    while stack:
        w = stack.pop(0)
        if isinstance(w, ttk.Combobox) and w.winfo_viewable() and len(w.cget("values")) > 1:
            boxes.append(w)
        stack.extend(w.winfo_children())
    box = boxes[0]
    box.current(0)
    ui.app.focus_force()
    ui.pump(0.1)
    before = box.get()
    _wheel_down(ui, box)
    assert box.get() == before and page.yview()[0] > 0

    page.yview_moveto(0)
    box.focus_force()
    ui.pump(0.1)
    _wheel_down(ui, box)
    assert box.get() != before and page.yview()[0] == 0


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_wheel_lets_a_long_text_box_scroll_itself(ui):
    import tkinter as tk

    page = _page_canvas(ui, "Universal Downsizer")
    stack: list[tk.Misc] = [page]
    texts = []
    while stack:
        w = stack.pop(0)
        if isinstance(w, tk.Text) and w.winfo_viewable():
            texts.append(w)
        stack.extend(w.winfo_children())
    text = texts[0]
    state = text.cget("state")
    text.configure(state="normal")
    text.insert("end", "".join(f"line {i}\n" for i in range(300)))
    text.configure(state=state)
    text.yview_moveto(0)
    ui.pump(0.1)
    _wheel_down(ui, text)
    first = text.yview()[0]
    assert first > 0 and page.yview()[0] == 0
    _wheel(ui, text, delta=-120)
    assert text.yview()[0] > first and page.yview()[0] == 0


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_wheel_in_a_window_over_the_sidebar_scrolls_that_window(ui):
    import tkinter as tk

    from tabs.widgets import scroll_body

    sidebar = ui.app.sidebar.canvas
    sidebar.yview_moveto(0)
    sx, sy = sidebar.winfo_rootx(), sidebar.winfo_rooty()
    top = tk.Toplevel(ui.app)
    try:
        top.geometry(f"240x200+{sx + 10}+{sy + 10}")
        inner = scroll_body(top)
        for i in range(60):
            tk.Label(inner, text=f"row {i}").pack()
        ui.pump(0.5)
        canvas = inner.master
        assert isinstance(canvas, tk.Canvas)
        _wheel_down(ui, inner.winfo_children()[3])
        assert canvas.yview()[0] > 0 and sidebar.yview()[0] == 0
    finally:
        top.destroy()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_mouse_side_buttons_do_not_scroll_on_tk9(ui):
    """Tk 9 reports the back/forward side buttons as buttons 4 and 5."""
    import tkinter as tk

    if tk.TkVersion < 9:
        pytest.skip("Tk 8.6 uses buttons 4/5 for the wheel")
    page = _page_canvas(ui, "Universal Downsizer")
    for canvas in (ui.app.sidebar.canvas, page):
        canvas.yview_moveto(0)
        _wheel(ui, canvas, button=5)
        assert canvas.yview()[0] == 0
