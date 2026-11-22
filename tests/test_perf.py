"""Performance rules (High Performance Python ch. 2: deterministic checks first,
timing budgets only as a backstop, because timings vary between machines).

1. Nothing starts a process on the UI thread: opening a page or loading a file
   must never wait for FFmpeg, ffprobe or yt-dlp.  (The allow-list may only shrink.)
2. Heavy optional libraries are not imported at start-up.
3. Start-up stays well inside a generous budget.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: (page, program) pairs that still start a process on the UI thread
TK_THREAD_SPAWNS_ALLOWED: set[tuple[str, str]] = set()  # none: probes run in core.tasks workers


_SPAWN_SCAN = textwrap.dedent("""
    import json, os, subprocess, sys, threading
    sys.path.insert(0, {root!r})
    from tests.gui_harness import Harness
    from tests.media import build_media, default_media_dir
    media = build_media(default_media_dir())
    ui = Harness.instance()
    hits, page_now = set(), [""]
    base = subprocess.Popen

    class Spy(base):
        def __init__(self, args, *a, **kw):
            if threading.current_thread() is threading.main_thread():
                prog = args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]
                hits.add((page_now[0], os.path.splitext(os.path.basename(str(prog)))[0]))
            super().__init__(args, *a, **kw)

    subprocess.Popen = Spy
    for name in ui.page_names():
        page_now[0] = name
        page = ui.open(name)
        ui.pump(0.3)
        if hasattr(page, "load_file"):
            page.load_file(media["h264_b"])
            ui.pump(0.4)
    print("RESULT " + json.dumps(sorted(hits)), flush=True)
    os._exit(0)
""")


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_no_process_starts_on_the_ui_thread():
    """In a fresh app (one-off checks are cached for the rest of a session, so
    a shared test process would miss them): open every page and load a file."""
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env.pop("CROSSFADER_TEST_USE_REAL_DISPLAY", None)
    res = subprocess.run(
        [sys.executable, "-c", _SPAWN_SCAN.format(root=ROOT)],
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
        cwd=ROOT,
    )
    line = [ln for ln in res.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, res.stderr[-3000:]
    hits = {tuple(h) for h in json.loads(line[-1][7:])}
    new = hits - TK_THREAD_SPAWNS_ALLOWED
    assert not new, f"started a process on the UI thread (do it in a worker): {sorted(new)}"
    fixed = TK_THREAD_SPAWNS_ALLOWED - hits
    assert not fixed, f"fixed - remove from TK_THREAD_SPAWNS_ALLOWED: {sorted(fixed)}"


_STARTUP = textwrap.dedent("""
    import json, sys, time
    t0 = time.perf_counter()
    sys.path.insert(0, {root!r})
    from tests import gui_harness
    gui_harness.ensure_display()
    from core import i18n
    from shell.app import App
    i18n.init()
    t_import = time.perf_counter()
    app = App()
    app.update()
    t_paint = time.perf_counter()
    heavy = sorted(m for m in ("aiohttp", "edge_tts", "asyncio", "numpy", "PIL") if m in sys.modules)
    print("RESULT " + json.dumps({{"import": t_import - t0, "paint": t_paint - t0, "heavy": heavy}}), flush=True)
    import os; os._exit(0)
""")


def _startup() -> dict:
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env.pop("CROSSFADER_TEST_USE_REAL_DISPLAY", None)
    res = subprocess.run(
        [sys.executable, "-c", _STARTUP.format(root=ROOT)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=ROOT,
    )
    line = [ln for ln in res.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, res.stderr[-2000:]
    return json.loads(line[-1][7:])


@pytest.mark.gui
def test_startup_imports_and_budget():
    runs = [_startup() for _ in range(2)]
    assert not runs[0]["heavy"], (
        f"heavy optional libraries imported at start-up: {runs[0]['heavy']}"
    )
    scale = float(os.environ.get("PERF_BUDGET_SCALE", "1"))
    assert min(r["paint"] for r in runs) < 2.0 * scale, runs  # measured ~0.2 s on a desktop
