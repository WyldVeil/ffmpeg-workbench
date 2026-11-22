"""
Shared pytest fixtures.

* ``media``  - dict of synthetic media files (see tests/media.py)
* ``ui``     - the headless GUI harness (tests/gui_harness.py), reset per test
* ``outdir`` - a fresh temporary directory for render outputs

Tests that need FFmpeg are marked ``ffmpeg``; GUI tests are marked ``gui``.
Both are skipped automatically when the prerequisite is missing.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Before anything is imported: the tests never touch the developer's own
# settings, caches or logs (a folder made under the app's name would also stop
# that person's own upgrade from bringing their old folders over).
_HOME = tempfile.mkdtemp(prefix="workbench-tests-")
for _kind in ("config", "data", "cache", "log"):
    os.environ.setdefault(f"CROSSFADER_{_kind.upper()}_DIR", os.path.join(_HOME, _kind))


def _record_commands(log_path: str) -> None:
    """CROSSFADER_ARGV_LOG=FILE: append every program the tests start (with the
    test that started it) to FILE, one JSON line each - to compare what a
    refactor makes FFmpeg do, old tree against new."""
    import json

    from core import proc

    spawn = proc.spawn

    def recording(argv, *args, **kwargs):
        test = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0]
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"test": test, "argv": [str(a) for a in argv]}) + "\n")
        return spawn(argv, *args, **kwargs)

    proc.spawn = recording


if os.environ.get("CROSSFADER_ARGV_LOG"):
    _record_commands(os.environ["CROSSFADER_ARGV_LOG"])


def pytest_configure(config):
    config.addinivalue_line("markers", "ffmpeg: needs ffmpeg/ffprobe on PATH or in bin/")
    config.addinivalue_line("markers", "gui: drives the Tk application headlessly")
    config.addinivalue_line("markers", "slow: long-running render test")


def _have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _have_display() -> bool:
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    return bool(shutil.which("Xvfb") or os.environ.get("DISPLAY"))


def pytest_collection_modifyitems(config, items):
    skip_ff = pytest.mark.skip(reason="ffmpeg/ffprobe not available")
    skip_gui = pytest.mark.skip(reason="no display / Xvfb available")
    for item in items:
        if "ffmpeg" in item.keywords and not _have_ffmpeg():
            item.add_marker(skip_ff)
        if "gui" in item.keywords and not _have_display():
            item.add_marker(skip_gui)


@pytest.fixture(scope="session")
def media():
    from tests.media import build_media, default_media_dir

    if not _have_ffmpeg():
        pytest.skip("ffmpeg not available")
    return build_media(default_media_dir())


@pytest.fixture(scope="session")
def _harness():
    from tests.gui_harness import Harness

    return Harness.instance()


@pytest.fixture
def ui(_harness):
    _harness.reset()
    yield _harness
    # Make sure nothing a test started is still running into the next test.
    try:
        _harness.wait_idle(timeout=120)
    except TimeoutError:
        pass
    errors = list(_harness.callback_errors)
    _harness.callback_errors.clear()
    if errors:
        pytest.fail("Tk callback raised:\n" + "\n".join(errors))


@pytest.fixture(autouse=True)
def no_leftover_processes():
    """Every child process a test starts must be finished (or killed) when it
    ends - a leak here is a leak in the app (Effective Python item 72)."""
    yield
    import time

    from core import ffmpeg as ff

    deadline = time.monotonic() + 5
    while ff.active_count() and time.monotonic() < deadline:
        time.sleep(0.1)
    left = ff.active_count()
    if left:
        ff.kill_all()
        pytest.fail(f"{left} child process(es) still running after the test")


@pytest.fixture
def outdir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d
