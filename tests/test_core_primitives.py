"""The engine's foundations: errors, the process gateway, the task service,
capabilities and Timecode."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from core import capabilities, errors, proc, tasks
from core.timecode import ZERO, Timecode

PY = sys.executable


# ── errors ────────────────────────────────────────────────────────────────────


def test_every_app_error_shares_one_root_and_keeps_its_old_bases():
    from core import cli, ffmpeg, network, outputs, tts

    for cls in (
        ffmpeg.ProbeError,
        ffmpeg.CancelledError,
        ffmpeg.CommandError,
        outputs.OutputRejected,
        tts.TTSError,
        cli.CliError,
        network.OfflineError,
        network.SocksUnavailable,
    ):
        assert issubclass(cls, errors.AppError), cls
    # handlers written for the old bases still catch them
    assert issubclass(ffmpeg.ProbeError, RuntimeError)
    assert issubclass(tts.TTSError, RuntimeError)
    assert issubclass(network.OfflineError, OSError)
    assert issubclass(errors.ProgramNotFound, FileNotFoundError)
    assert issubclass(errors.StartError, OSError)
    assert issubclass(errors.ProcessTimeout, TimeoutError)
    assert issubclass(cli.CliError, ValueError)


# ── core.proc ─────────────────────────────────────────────────────────────────


def test_run_collects_output_and_does_not_raise_for_an_exit_code():
    res = proc.run(
        [PY, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        timeout=30,
    )
    assert (res.returncode, res.ok) == (3, False)
    assert res.stdout.strip() == "out"
    assert res.last_error_line() == "err"
    assert res.seconds >= 0


def test_run_bytes_and_input():
    res = proc.run(
        [PY, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])"],
        timeout=30,
        text=False,
        input=b"abc",
    )
    assert res.stdout == b"cba"


def test_run_times_out_and_stops_the_program():
    started = time.monotonic()
    with pytest.raises(errors.ProcessTimeout):
        proc.run([PY, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert time.monotonic() - started < 10
    assert proc.active_count() == 0


def test_run_can_be_cancelled():
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()
    with pytest.raises(errors.Cancelled):
        proc.run([PY, "-c", "import time; time.sleep(30)"], timeout=None, cancel=flag.is_set)
    assert proc.active_count() == 0


def test_a_missing_program_is_program_not_found():
    with pytest.raises(errors.ProgramNotFound):
        proc.run(["definitely-not-a-program-xyz"], timeout=5)


def test_kill_all_stops_spawned_programs():
    child = proc.spawn([PY, "-c", "import time; time.sleep(30)"])
    assert proc.active_count() >= 1
    assert proc.kill_all() >= 1
    assert child.poll() is not None
    assert proc.active_count() == 0


# ── core.tasks ────────────────────────────────────────────────────────────────


def test_submit_delivers_the_result_or_the_error(monkeypatch):
    monkeypatch.setattr(
        tasks, "_post", lambda fn, arg: fn(arg)
    )  # no event loop here: deliver at once
    got: list[object] = []
    done = threading.Event()

    def record(value: object) -> None:
        got.append(value)
        if len(got) == 2:
            done.set()

    tasks.submit(lambda: 6 * 7, on_done=record)
    tasks.submit(lambda: 1 / 0, on_error=lambda e: record(type(e).__name__))
    assert done.wait(5)
    assert sorted(map(str, got)) == ["42", "ZeroDivisionError"]


def test_submit_returns_a_future_and_bounds_the_threads():
    futures = [tasks.submit(lambda: threading.current_thread().name) for _ in range(40)]
    names = {f.result(timeout=10) for f in futures}
    assert len(names) <= 8  # the background pool's size
    assert all(n.startswith("background") for n in names)


def test_an_unhandled_error_is_logged_not_lost(caplog):
    tasks.submit(lambda: 1 / 0, name="boom").exception(timeout=5)
    deadline = time.monotonic() + 5
    while "boom" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "background task boom failed" in caplog.text


def test_a_crashing_service_is_logged(caplog):
    t = tasks.start_service(lambda: 1 / 0, name="svc")
    t.join(5)
    assert "service thread svc crashed" in caplog.text


# ── core.capabilities ─────────────────────────────────────────────────────────


@pytest.mark.ffmpeg
def test_capabilities_are_learned_once_and_remembered(monkeypatch, tmp_path):
    from core import paths

    monkeypatch.setattr(paths, "cache_dir", lambda: str(tmp_path))
    capabilities.forget()
    filters = capabilities.get("filters")
    assert "scale" in filters and isinstance(filters, frozenset)
    assert (tmp_path / "capabilities.json").is_file()
    capabilities.forget()
    calls = []
    monkeypatch.setattr(proc, "run", lambda *a, **k: calls.append(a))  # the disk answers now
    assert capabilities.peek("filters") == filters
    assert capabilities.get("filters") == filters
    assert calls == []
    capabilities.forget()


def test_capabilities_peek_never_runs_a_program(monkeypatch, tmp_path):
    from core import paths

    monkeypatch.setattr(paths, "cache_dir", lambda: str(tmp_path))
    capabilities.forget()
    monkeypatch.setattr(proc, "run", lambda *a, **k: pytest.fail("peek ran a program"))
    assert capabilities.peek("vulkan", "unknown") == "unknown"
    capabilities.forget()


# ── core.timecode ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "spec", "text"),
    [
        (83.25, "", "01:23.250"),
        (3723.5, "", "01:02:03.500"),
        (5, ".0", "00:05"),
        (5, "H", "00:00:05.000"),
        (59.999, ".2", "01:00.00"),  # carried, never "00:60.00"
        (3725.456, "ass", "1:02:05.46"),
        (3723.5, "m.1", "62:03.5"),
        (12.3456, "ff", "12.346"),
    ],
)
def test_timecode_formats(seconds, spec, text):
    assert format(Timecode.of(seconds), spec) == text


@pytest.mark.parametrize(
    ("text", "seconds"), [("1:02:03.5", 3723.5), ("02:03", 123), ("75,5", 75.5), (7, 7)]
)
def test_timecode_parses(text, seconds):
    assert Timecode.parse(text).seconds == seconds


@pytest.mark.parametrize("bad", ["", "abc", "-1", "1:2:3:4", float("nan")])
def test_timecode_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        Timecode.parse(bad)


def test_timecode_is_a_value():
    a, b = Timecode.of(1.5), Timecode.parse("00:01.500")
    assert a == b and hash(a) == hash(b) and a < Timecode.of(2)
    assert float(a + 0.5) == 2.0 and (a - 10) == ZERO and not ZERO
    assert Timecode.of(1.02).snap(25) == Timecode.of(1.04) and Timecode.of(1).frame(30) == 30
    assert Timecode.of(None) == ZERO == Timecode.of(-4)
    with pytest.raises(ValueError):
        Timecode(-1)
    with pytest.raises(TypeError):
        Timecode(1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        format(a, "nonsense")
