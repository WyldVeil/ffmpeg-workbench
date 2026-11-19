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

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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


@pytest.fixture
def outdir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d
