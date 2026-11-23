"""Waiting on a program a little at a time: Popen.communicate/wait raise
subprocess.TimeoutExpired (core.proc.TimeoutExpired), not the app's
ProcessTimeout - catching the wrong one made every offline voice slower than
half a second fail, and a recorder that ignored Stop was never killed."""

import sys

import pytest


def test_the_system_voice_may_take_its_time():
    from core import tts

    tts._run([sys.executable, "-c", "import time; time.sleep(1.2)"], None)  # finishes, no error


def test_the_system_voice_still_times_out():
    from core import tts

    with pytest.raises(tts.TTSError, match="timed out"):
        tts._run([sys.executable, "-c", "import time; time.sleep(30)"], None, timeout=1)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows stops a recorder with terminate()")
def test_a_recorder_that_ignores_stop_is_killed():
    from core.recorder import Recording

    rec = Recording(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)",
        ],
        "unused.mp4",
    )
    rc = rec.stop(timeout=0.5)
    assert rc is not None and not rec.running()
