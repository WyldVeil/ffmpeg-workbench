"""HiDPI scaling: core/scaling.py, and the app on a 200 % screen."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from core import scaling

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def factor():
    old = scaling.factor()
    yield scaling.set_factor
    scaling.set_factor(old)


def test_px_at_100_percent_returns_values_unchanged(factor):
    factor(1.0)
    assert scaling.px(8) == 8
    assert scaling.px(0, 8) == (0, 8)
    assert scaling.px((4, 2)) == (4, 2)
    assert scaling.px(1.5) == 1.5


def test_px_scales_ints_floats_and_pairs(factor):
    factor(2.0)
    assert scaling.px(8) == 16 and isinstance(scaling.px(8), int)
    assert scaling.px(0, 8) == (0, 16)
    assert scaling.px([3, 3]) == (6, 6)
    assert scaling.px(1.5) == 3.0
    assert scaling.px(-6) == -12
    factor(1.25)
    assert scaling.px(1) == 1  # 1.25 rounds to 1, but never down to 0
    assert scaling.px(10) == 12  # round(12.5) -> 12 (banker's), still close
    factor(0.75)
    assert scaling.px(1) == 1 and scaling.px(-1) == -1


def test_factor_is_snapped_and_clamped(factor):
    factor(1.03)
    assert scaling.factor() == 1.0
    factor(9)
    assert scaling.factor() == scaling.MAX_FACTOR
    factor(0.1)
    assert scaling.factor() == scaling.MIN_FACTOR


@pytest.mark.skipif(sys.platform.startswith(("win", "darwin")), reason="X11 only")
def test_ui_scale_override_sets_font_dpi_and_keeps_user_resources(tmp_path, monkeypatch):
    user = tmp_path / "user-resources"
    user.write_text("XTerm*faceName: Mono\n")
    monkeypatch.setenv("XENVIRONMENT", str(user))
    monkeypatch.setenv("CROSSFADER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(scaling, "_requested_scale", lambda: 1.5)
    scaling.prepare()
    path = os.environ["XENVIRONMENT"]
    assert path != str(user)
    text = open(path).read()
    assert "XTerm*faceName: Mono" in text and text.rstrip().endswith("Xft.dpi: 144")
    scaling._restore_xenvironment()
    assert os.environ["XENVIRONMENT"] == str(user)


@pytest.mark.skipif(sys.platform.startswith(("win", "darwin")), reason="X11 only")
def test_no_override_leaves_the_environment_alone(monkeypatch):
    monkeypatch.delenv("XENVIRONMENT", raising=False)
    monkeypatch.delenv("GDK_SCALE", raising=False)
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    monkeypatch.setattr(scaling, "_requested_scale", lambda: None)
    monkeypatch.setattr(scaling, "xft_dpi", lambda: 96.0)
    scaling.prepare()
    assert "XENVIRONMENT" not in os.environ


@pytest.mark.parametrize(
    "desktop, output, expected",
    [
        ("XFCE", "2", 2),
        ("XFCE", "1", 1),
        ("MATE", "uint32 2", 2),
        ("MATE", "uint32 0", 1),
        ("GNOME", "{'Gdk/WindowScalingFactor': <2>}", 2),
        ("GNOME", "@a{sv} {}", 1),
        ("KDE", "2", 1),
    ],
)
def test_window_scale_from_the_desktop(monkeypatch, desktop, output, expected):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    monkeypatch.setattr(scaling, "_run_quiet", lambda cmd: output)
    assert scaling.window_scale() == expected


@pytest.mark.skipif(sys.platform.startswith(("win", "darwin")), reason="X11 only")
@pytest.mark.parametrize(
    "xft, ws, expected",
    [(96.0, 2, 2.0), (None, 2, 2.0), (120.0, 2, 2.5), (192.0, 2, None), (96.0, 1, None)],
)
def test_window_scale_with_unscaled_font_dpi_overrides(tmp_path, monkeypatch, xft, ws, expected):
    monkeypatch.setenv("CROSSFADER_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("GDK_SCALE", raising=False)
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    monkeypatch.setattr(scaling, "_requested_scale", lambda: None)
    monkeypatch.setattr(scaling, "xft_dpi", lambda: xft)
    monkeypatch.setattr(scaling, "window_scale", lambda: ws)
    old = os.environ.get("XENVIRONMENT")
    try:
        scaling.prepare()
        assert scaling._override == expected
    finally:
        scaling._override = None
        if scaling._changed_xenv:
            scaling._restore_xenvironment()
        assert os.environ.get("XENVIRONMENT") == old


_APP_AT_200 = textwrap.dedent("""
    import json, os, sys
    sys.path.insert(0, {root!r})
    from tests import gui_harness
    from tests.gui_harness import Harness
    from core import scaling
    _isolate = gui_harness._isolate_settings
    def _with_scale(tmpdir):                 # the harness writes a fresh settings.json
        _isolate(tmpdir)
        if os.environ.get("TEST_UI_SCALE"):
            path = os.path.join(tmpdir, "settings.json")
            data = json.load(open(path))
            data["ui_scale"] = os.environ["TEST_UI_SCALE"]
            json.dump(data, open(path, "w"))
    gui_harness._isolate_settings = _with_scale
    h = Harness.instance()
    app = h.app
    h.pump(2.0)
    first = app.winfo_width()
    page = h.open("Start")
    h.pump(2.0)
    canvas = next(w for w in Harness.walk(page) if w.winfo_class() == "Canvas")
    inner = canvas.winfo_children()[0]
    fonts = app.tk.call("font", "measure", ("DejaVu Sans", 10), "M" * 10)
    print("RESULT " + json.dumps({{
        "factor": scaling.factor(), "width": app.winfo_width(), "first": first,
        "screen": app.winfo_screenwidth(), "canvas": canvas.winfo_width(),
        "inner": inner.winfo_width(), "font": fonts, "errors": h.callback_errors}}), flush=True)
    os._exit(0)
""")


def _run_app(env_extra: dict) -> dict:
    env = dict(os.environ)
    env.pop("CROSSFADER_TEST_USE_REAL_DISPLAY", None)
    env.pop("DISPLAY", None)  # the harness starts its own Xvfb
    env.update(env_extra)
    res = subprocess.run(
        [sys.executable, "-c", _APP_AT_200.format(root=ROOT)],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = [ln for ln in res.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, f"app did not start (rc {res.returncode}):\n{res.stderr[-3000:]}"
    return json.loads(lines[-1][7:])


@pytest.mark.gui
@pytest.mark.skipif(
    sys.platform.startswith(("win", "darwin")) or not shutil.which("Xvfb"), reason="needs Xvfb"
)
def test_app_runs_at_200_percent(tmp_path):
    """GNOME at 200 % sets Xft.dpi to 192.  The app used to die at start with
    'BadValue ... X_CreatePixmap' (the Start page widened itself without end)."""
    res = tmp_path / "xres"
    res.write_text("Xft.dpi: 192\n")
    big = _run_app({"XENVIRONMENT": str(res)})
    normal = _run_app({"XENVIRONMENT": str(tmp_path / "none")})
    assert big["factor"] == 2.0 and normal["factor"] == 1.0
    assert not big["errors"]
    assert big["width"] == big["first"] <= big["screen"]  # stable, fits the screen
    assert big["inner"] <= big["canvas"] + 1  # Start page fits its view
    assert 1.8 < big["font"] / normal["font"] < 2.2  # text really is twice the size


@pytest.mark.gui
@pytest.mark.skipif(
    sys.platform.startswith(("win", "darwin")) or not shutil.which("Xvfb"), reason="needs Xvfb"
)
def test_ui_scale_setting_scales_the_whole_app(tmp_path):
    scaled = _run_app({"TEST_UI_SCALE": "1.5", "XENVIRONMENT": str(tmp_path / "none")})
    normal = _run_app({"XENVIRONMENT": str(tmp_path / "none")})
    assert scaled["factor"] == 1.5
    assert 1.3 < scaled["font"] / normal["font"] < 1.7


@pytest.mark.gui
def test_tk_font_doubling_is_undone(ui):
    """Tk 9 doubles its standard fonts on a desktop window scale of 2; the
    font DPI already does the scaling, so they must go back to normal."""
    import tkinter

    if tkinter.TkVersion < 9:
        pytest.skip("Tk 8.6 does not double its fonts")
    call = ui.app.tk.call
    before = int(call("font", "configure", "TkDefaultFont", "-size"))
    call("set", "::tk::fontScalingFactor", 2)
    call("font", "configure", "TkDefaultFont", "-size", before * 2)
    try:
        scaling._undo_tk_font_doubling(call)
        assert int(call("font", "configure", "TkDefaultFont", "-size")) == before
    finally:
        call("set", "::tk::fontScalingFactor", 1)
        call("font", "configure", "TkDefaultFont", "-size", before)
