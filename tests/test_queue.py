"""Render queue v2: progress, atomic outputs, cancellation, failure summaries."""

import os
import time

import pytest

from core import ffmpeg as ff
from core.queue_manager import Hooks


@pytest.fixture
def q():
    from tests.gui_harness import ensure_display

    ensure_display()
    import tkinter as tk

    from core import ui
    from core.queue_manager import RenderQueueManager

    root = tk.Tk()
    hosting = ui.hosted_by(root, poll_ms=5)
    hosting.__enter__()  # left in pump_until's teardown below
    mgr = RenderQueueManager.get_instance()
    mgr.cancel_all()
    mgr.clear_finished()

    def pump_until(pred, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.after(30, root.quit)
            root.mainloop()
            if pred():
                return True
        return False

    mgr.pump_until = pump_until  # type: ignore[attr-defined]  # the fixture's helper rides on the manager
    yield mgr
    mgr.cancel_all()
    pump_until(lambda: not mgr.active_tasks(), 20)
    hosting.__exit__(None, None, None)
    root.destroy()


def _lavfi(seconds, out, preset="ultrafast"):
    return [
        ff.ffmpeg_path(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=1280x720:rate=30:d={seconds}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        out,
    ]


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_success_promotes_part_file(q, outdir):
    out = str(outdir / "ok.mp4")
    events = []
    tid = q.enqueue(
        "ok",
        out,
        work=[_lavfi(2, out)],
        expected_duration=2.0,
        hooks=Hooks(on_complete=lambda t, rc: events.append(rc)),
    )
    assert q.pump_until(lambda: events)
    task = q.get_task(tid)
    assert events == [0] and task.status == "done" and task.fraction == 1.0
    assert os.path.getsize(out) > 0
    assert not any(n.endswith(".part.mp4") for n in os.listdir(outdir))


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_cancel_running_kills_and_cleans(q, outdir):
    out = str(outdir / "long.mp4")
    events = []
    tid = q.enqueue(
        "long",
        out,
        work=[_lavfi(600, out, "veryslow")],
        expected_duration=600,
        hooks=Hooks(on_complete=lambda t, rc: events.append(rc)),
    )
    assert q.pump_until(lambda: q.get_task(tid).status == "active" and q.get_task(tid).fraction)
    q.cancel(tid)
    assert q.pump_until(lambda: events, 15)
    assert events == [-1] and q.get_task(tid).status == "cancelled"
    assert ff.active_count() == 0
    assert os.listdir(outdir) == []  # neither final nor .part file


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_cancel_pending_still_completes(q, outdir):
    a, b = str(outdir / "a.mp4"), str(outdir / "b.mp4")
    done = {}
    ta = q.enqueue(
        "a", a, work=[_lavfi(3, a)], hooks=Hooks(on_complete=lambda t, rc: done.setdefault("a", rc))
    )
    tb = q.enqueue(
        "b", b, work=[_lavfi(3, b)], hooks=Hooks(on_complete=lambda t, rc: done.setdefault("b", rc))
    )
    if q._parallel() == 1:
        q.cancel(tb)
        assert q.pump_until(lambda: "b" in done, 5)
        assert done["b"] == -1 and q.get_task(tb).status == "cancelled"
    assert q.pump_until(lambda: "a" in done)
    assert done["a"] == 0 and q.get_task(ta).status == "done"


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_failure_keeps_existing_file_and_reports_error(q, media, outdir):
    out = outdir / "keep.mp4"
    out.write_bytes(b"precious")
    events = []
    tid = q.enqueue(
        "bad",
        str(out),
        work=[[ff.ffmpeg_path(), "-y", "-i", media["h264"], "-vf", "nosuchfilter", str(out)]],
        hooks=Hooks(on_complete=lambda t, rc: events.append(rc)),
    )
    assert q.pump_until(lambda: events)
    task = q.get_task(tid)
    assert task.status == "failed" and events[0] != 0
    assert "nosuchfilter" in task.error or "filter" in task.error.lower()
    assert out.read_bytes() == b"precious"


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_worker_fn_with_context(q, outdir):
    seen = {}

    def job(progress_cb, cancel_fn, ctx):
        tmp = ctx.tempdir()
        seen["tmp"] = tmp
        mid = os.path.join(tmp, "mid.mp4")
        ctx.span(0.0, 0.5).run(_lavfi(1, mid), duration=1.0)
        ctx.span(0.5, 1.0).run(
            [ff.ffmpeg_path(), "-y", "-i", mid, "-c", "copy", str(outdir / "final.mp4")],
            duration=1.0,
        )
        return 0

    events = []
    tid = q.enqueue(
        "multi",
        str(outdir / "final.mp4"),
        work=job,
        hooks=Hooks(on_complete=lambda t, rc: events.append(rc)),
    )
    assert q.pump_until(lambda: events)
    assert events == [0] and q.get_task(tid).fraction == 1.0
    assert (outdir / "final.mp4").exists() and not os.path.exists(seen["tmp"])


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_worker_fn_failure_reports_summary(q, outdir):
    def job(progress_cb, cancel_fn, ctx):
        ctx.run(
            [
                ff.ffmpeg_path(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "nullsrc",
                "-vf",
                "bogus=1",
                "-t",
                "1",
                str(outdir / "x.mp4"),
            ]
        )
        return 0

    events = []
    tid = q.enqueue(
        "multi-bad", "", work=job, hooks=Hooks(on_complete=lambda t, rc: events.append(rc))
    )
    assert q.pump_until(lambda: events)
    assert q.get_task(tid).status == "failed" and "bogus" in q.get_task(tid).error
