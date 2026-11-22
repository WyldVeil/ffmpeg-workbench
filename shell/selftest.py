"""shell/selftest.py - ``--self-test``: check an installation without a person.

The bundled programs are found and run, a short test encode works, and every
tool page loads.  Prints what it checked; the exit code is 1 on any problem."""
from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING

from core import ffmpeg as ff
from core.hardware import find_binary
from core.tools import ToolId
from shell.pages import ErrorPage

if TYPE_CHECKING:
    from shell.app import App


def run(app: App) -> int:
    problems: list[str] = []
    for tool in ("ffmpeg", "ffprobe"):
        path = find_binary(tool)
        print(f"{tool:8} {path} -> {ff.version_of(path) if path else 'MISSING'}")
        if not path:
            problems.append(f"{tool} missing")
    out = os.path.join(tempfile.mkdtemp(prefix="selftest_"), "t.mp4")
    res = ff.run([ff.ffmpeg_path(), "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:d=1",
                  "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-shortest", out])
    info = ff.try_probe(out)
    ok = res.ok and info is not None and info.has_video and info.has_audio
    print("encode  ", "ok" if ok else res.error_summary)
    if not ok:
        problems.append("test encode failed")
    for tool in ToolId:
        try:
            page = app.show_page(tool)
            app.update()
            if isinstance(page, ErrorPage):
                problems.append(f"tool failed to load: {tool.value}")
        except Exception as exc:                     # report every problem, not just the first
            problems.append(f"{tool.value}: {exc}")
    print(f"pages    {len(ToolId)} checked")
    for problem in problems:
        print("PROBLEM:", problem)
    return 1 if problems else 0
