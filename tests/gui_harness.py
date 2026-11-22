"""
tests/gui_harness.py - Headless driver for the Tk application.

* Starts a private Xvfb display on Linux so tests never draw on the
  developer's desktop (set CROSSFADER_TEST_USE_REAL_DISPLAY=1 to opt out).
* Isolates settings / skin files into a temporary directory.
* Replaces every modal dialog (messagebox, filedialog, simpledialog,
  colorchooser) with a recorder that returns scripted answers.
* Records every subprocess the app launches (the real process still runs).
* Lets a test open any tab, set its variables, press buttons, and wait for
  the global render queue and any child processes to finish.

Typical use (see tests/conftest.py for the pytest fixtures)::

    h = Harness.instance()
    tab = h.open("Quick Trimmer")
    h.set_var(tab, "_src_var", media["h264"])
    h.answer_save_as("/tmp/out.mp4")
    h.click_text(tab, "RENDER")
    h.wait_idle()
    assert h.probe("/tmp/out.mp4")["format"]["duration"]

It can also be run as a script to inspect a tab::

    python -m tests.gui_harness "Quick Trimmer"        # variables + buttons
    python -m tests.gui_harness --list                 # every page name
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_REAL_POPEN = subprocess.Popen


# ─────────────────────────────────────────────────────────────────────────────
#  Display
# ─────────────────────────────────────────────────────────────────────────────

_XVFB_PROC: subprocess.Popen | None = None


def ensure_display() -> None:
    """Point DISPLAY at a private Xvfb server (Linux/BSD only)."""
    global _XVFB_PROC
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return
    if os.environ.get("CROSSFADER_TEST_USE_REAL_DISPLAY") == "1":
        return
    if _XVFB_PROC is not None:
        return
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("No DISPLAY and Xvfb is not installed")
        return  # fall back to whatever display exists (e.g. CI's xvfb-run)
    rfd, wfd = os.pipe()
    proc = _REAL_POPEN(
        [
            xvfb,
            "-displayfd",
            str(wfd),
            "-screen",
            "0",
            "1600x1000x24",
            "-nolisten",
            "tcp",
            "-noreset",
        ],
        pass_fds=(wfd,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    os.close(wfd)
    buf = b""
    deadline = time.monotonic() + 15
    while not buf.endswith(b"\n") and time.monotonic() < deadline:
        chunk = os.read(rfd, 16)
        if not chunk:
            break
        buf += chunk
    os.close(rfd)
    num = buf.decode().strip()
    if not num:
        proc.kill()
        raise RuntimeError("Xvfb failed to report a display number")
    os.environ["DISPLAY"] = ":" + num
    os.environ.pop("WAYLAND_DISPLAY", None)
    _XVFB_PROC = proc

    def _stop():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    atexit.register(_stop)


# ─────────────────────────────────────────────────────────────────────────────
#  Subprocess recorder
# ─────────────────────────────────────────────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[list[str]] = []
        self.procs: list[subprocess.Popen] = []

    def reset(self) -> None:
        with self.lock:
            self.calls.clear()
            self.procs = [p for p in self.procs if p.poll() is None]


RECORDER = _Recorder()


class _RecordingPopen(_REAL_POPEN):  # type: ignore[misc, valid-type]
    def __init__(self, args, *a, **kw):
        if isinstance(args, (list, tuple)):
            argv = [str(x) for x in args]
        else:
            argv = [str(args)]
        with RECORDER.lock:
            RECORDER.calls.append(argv)
        super().__init__(args, *a, **kw)
        with RECORDER.lock:
            RECORDER.procs.append(self)


# ─────────────────────────────────────────────────────────────────────────────
#  Dialog stubs
# ─────────────────────────────────────────────────────────────────────────────


class _Dialogs:
    """Records modal dialogs and returns scripted answers."""

    def __init__(self) -> None:
        self.log: list[tuple[str, str, str]] = []  # (kind, title, message)
        self.answers: dict[str, deque] = {}
        self.default_yes = True

    def reset(self) -> None:
        self.log.clear()
        self.answers.clear()
        self.default_yes = True

    def push(self, kind: str, value: Any) -> None:
        self.answers.setdefault(kind, deque()).append(value)

    def _pop(self, kind: str, default: Any) -> Any:
        q = self.answers.get(kind)
        if q:
            return q.popleft()
        return default

    def make(self, kind: str, default_factory: Callable[[], Any]):
        def _stub(*args, **kwargs):
            title = kwargs.get("title") or (args[0] if args else "")
            message = kwargs.get("message") or (args[1] if len(args) > 1 else "")
            self.log.append((kind, str(title), str(message)))
            return self._pop(kind, default_factory())

        _stub.__name__ = f"stub_{kind}"
        return _stub

    # convenience views
    def errors(self) -> list[tuple[str, str, str]]:
        return [d for d in self.log if d[0] == "showerror"]

    def warnings(self) -> list[tuple[str, str, str]]:
        return [d for d in self.log if d[0] == "showwarning"]


DIALOGS = _Dialogs()


def _install_dialog_stubs() -> None:
    from tkinter import colorchooser, filedialog, messagebox, simpledialog

    def yes():
        return DIALOGS.default_yes

    for name in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, name, DIALOGS.make(name, lambda: "ok"))
    for name in ("askyesno", "askokcancel", "askretrycancel"):
        setattr(messagebox, name, DIALOGS.make(name, yes))
    messagebox.askyesnocancel = DIALOGS.make("askyesnocancel", yes)
    messagebox.askquestion = DIALOGS.make(
        "askquestion", lambda: "yes" if DIALOGS.default_yes else "no"
    )

    filedialog.askopenfilename = DIALOGS.make("askopenfilename", lambda: "")
    filedialog.askopenfilenames = DIALOGS.make("askopenfilenames", lambda: ())
    filedialog.asksaveasfilename = DIALOGS.make("asksaveasfilename", lambda: "")
    filedialog.askdirectory = DIALOGS.make("askdirectory", lambda: "")
    simpledialog.askstring = DIALOGS.make("askstring", lambda: None)
    simpledialog.askinteger = DIALOGS.make("askinteger", lambda: None)
    simpledialog.askfloat = DIALOGS.make("askfloat", lambda: None)
    colorchooser.askcolor = DIALOGS.make("askcolor", lambda: (None, None))


# ─────────────────────────────────────────────────────────────────────────────
#  Settings isolation
# ─────────────────────────────────────────────────────────────────────────────


def _isolate_settings(tmpdir: str) -> None:
    os.environ["CROSSFADER_CONFIG_DIR"] = tmpdir
    os.environ["CROSSFADER_DATA_DIR"] = os.path.join(tmpdir, "data")
    os.environ["CROSSFADER_CACHE_DIR"] = os.path.join(tmpdir, "cache")
    os.environ.setdefault("CROSSFADER_NO_NETWORK", "1")
    os.environ.setdefault("CROSSFADER_NO_SOUND", "1")
    os.environ["CROSSFADER_NATIVE_DIALOGS"] = "0"  # use the (stubbed) Tk dialogs
    settings_path = os.path.join(tmpdir, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump({"language": "en_US", "auto_open_output": False}, fh)
    try:
        import core.settings as cs

        for attr in ("SETTINGS_FILE", "_SETTINGS_PATH"):
            if hasattr(cs, attr):
                setattr(cs, attr, settings_path)
    except Exception:
        pass
    try:
        import core.skins as sk

        if hasattr(sk, "SKIN_FILE"):
            sk.SKIN_FILE = os.path.join(tmpdir, "skin.json")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Harness
# ─────────────────────────────────────────────────────────────────────────────


class Harness:
    _instance: Harness | None = None

    @classmethod
    def instance(cls) -> Harness:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        ensure_display()
        self.tmpdir = tempfile.mkdtemp(prefix="crossfader_test_cfg_")
        _isolate_settings(self.tmpdir)
        subprocess.Popen = _RecordingPopen  # type: ignore[misc]
        _install_dialog_stubs()

        from core import i18n
        from shell import chime
        from shell.app import App

        chime.play = lambda: None  # no start-up sound in tests
        i18n.init()
        self.app = App()
        # Surface Tk callback exceptions instead of printing and continuing.
        self.callback_errors: list[str] = []

        def _report(exc, val, tb):
            import traceback

            self.callback_errors.append("".join(traceback.format_exception(exc, val, tb)))

        self.app.report_callback_exception = _report

        # Callbacks queued from worker threads (core.ui.call_soon) log their
        # exceptions instead of raising: count those as callback errors too,
        # or a tool can half-fail after loading a file without any test noticing.
        import logging

        class _Collect(logging.Handler):
            def emit(handler, record):
                if record.levelno >= logging.ERROR:
                    self.callback_errors.append(
                        handler.format(record)
                        + (
                            "\n" + logging.Formatter().formatException(record.exc_info)
                            if record.exc_info
                            else ""
                        )
                    )

        logging.getLogger("crossfader.ui").addHandler(_Collect())
        self.pump(0.2)

    # ── event loop ───────────────────────────────────────────────────────────

    def pump(self, seconds: float = 0.05) -> None:
        """Run the REAL Tk main loop for *seconds*.

        Worker threads that call ``widget.after()`` (the render queue and
        many tabs) need the main thread to be inside ``mainloop()``;
        ``app.update()`` is not enough - Tkinter then waits ~1 s and raises
        "main thread is not in main loop", silently dropping the callback.
        """
        ms = max(1, int(seconds * 1000))
        self.app.after(ms, self.app.quit)
        try:
            self.app.mainloop()
        except Exception:
            pass

    def _queue_busy(self) -> bool:
        try:
            from core.queue_manager import RenderQueueManager

            stats = RenderQueueManager.get_instance().get_stats()
            return bool(stats[0] or stats[1])
        except Exception:
            return False

    def _procs_busy(self) -> bool:
        with RECORDER.lock:
            return any(p.poll() is None for p in RECORDER.procs)

    def wait_idle(
        self, timeout: float = 300.0, settle: float = 0.4, extra: Callable[[], bool] | None = None
    ) -> None:
        """Pump Tk until the render queue is empty, every recorded child
        process has exited and *extra()* (if given) returns False."""
        deadline = time.monotonic() + timeout
        quiet_since: float | None = None
        while time.monotonic() < deadline:
            self.pump(0.05)
            busy = self._queue_busy() or self._procs_busy() or bool(extra and extra())
            if busy:
                quiet_since = None
                continue
            if quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= settle:
                return
        raise TimeoutError(f"app did not become idle within {timeout:.0f}s")

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(0.05)
            if predicate():
                return
        raise TimeoutError(f"condition not met within {timeout:.0f}s")

    # ── navigation ───────────────────────────────────────────────────────────

    def page_names(self) -> list[str]:
        """The English name of every page the app can show (core/tools.py)."""
        from core import tools

        return [s.name for s in tools.SPECS]

    def open(self, name: str) -> Any:
        """Open a page by its English name (or ToolId) and return it (typed Any:
        each test knows which page it opened)."""
        from shell.pages import ErrorPage

        page = self.app.show_page(name)
        self.pump(0.15)
        if page is None:
            raise KeyError(name)
        if isinstance(page, ErrorPage):
            raise RuntimeError(f"tab {name!r} failed to construct")
        return page

    # ── scripted answers / resets ───────────────────────────────────────────

    def reset(self) -> None:
        DIALOGS.reset()
        RECORDER.reset()
        self.callback_errors.clear()

    def answer(self, kind: str, value: Any) -> None:
        """Queue the next return value for a dialog *kind*
        (e.g. "asksaveasfilename", "askopenfilename", "askyesno")."""
        DIALOGS.push(kind, value)

    def answer_open(self, path: str) -> None:
        DIALOGS.push("askopenfilename", path)

    def answer_open_many(self, paths: Iterable[str]) -> None:
        DIALOGS.push("askopenfilenames", tuple(paths))

    def answer_save_as(self, path: str) -> None:
        DIALOGS.push("asksaveasfilename", path)

    def answer_dir(self, path: str) -> None:
        DIALOGS.push("askdirectory", path)

    @property
    def dialogs(self) -> _Dialogs:
        return DIALOGS

    @property
    def commands(self) -> list[list[str]]:
        with RECORDER.lock:
            return [list(c) for c in RECORDER.calls]

    def ffmpeg_commands(self) -> list[list[str]]:
        return [c for c in self.commands if os.path.basename(c[0]).lower().startswith("ffmpeg")]

    # ── widget helpers ───────────────────────────────────────────────────────

    @staticmethod
    def walk(widget) -> Iterable[Any]:
        yield widget
        for child in widget.winfo_children():
            yield from Harness.walk(child)

    def find(self, root, cls: type | tuple | None = None, text: str | None = None) -> list[Any]:
        found = []
        for w in self.walk(root):
            if cls is not None and not isinstance(w, cls):
                continue
            if text is not None:
                try:
                    wt = str(w.cget("text"))
                except Exception:
                    continue
                if text.lower() not in wt.lower():
                    continue
            found.append(w)
        return found

    def click_text(self, root, text: str, index: int = 0) -> None:
        """Invoke the *index*-th button whose label contains *text*."""
        import tkinter as tk
        from tkinter import ttk

        buttons = self.find(root, (tk.Button, ttk.Button), text)
        if not buttons:
            labels = [str(b.cget("text")) for b in self.find(root, (tk.Button, ttk.Button))]
            raise LookupError(f"no button containing {text!r}; have {labels}")
        btn = buttons[index]
        if str(btn.cget("state")) == "disabled":
            raise RuntimeError(f"button {btn.cget('text')!r} is disabled")
        btn.invoke()
        self.pump(0.1)

    @staticmethod
    def variables(tab) -> dict[str, tuple[str, Any]]:
        """Return {attribute: (VariableType, value)} for a tab's tk Variables."""
        import tkinter as tk

        out = {}
        for name, val in sorted(vars(tab).items()):
            if isinstance(val, tk.Variable):
                try:
                    out[name] = (type(val).__name__, val.get())
                except Exception:
                    out[name] = (type(val).__name__, "<unreadable>")
        return out

    def set_var(self, tab, attr: str, value: Any) -> None:
        getattr(tab, attr).set(value)
        self.pump(0.05)

    @staticmethod
    def text_of(widget) -> str:
        try:
            return widget.get("1.0", "end-1c")
        except Exception:
            return ""

    def console_text(self, tab) -> str:
        """Concatenate the contents of every tk.Text widget in *tab*."""
        import tkinter as tk

        return "\n".join(self.text_of(w) for w in self.find(tab, tk.Text))

    # ── media helpers ────────────────────────────────────────────────────────

    @staticmethod
    def probe(path: str) -> dict:
        ffprobe = shutil.which("ffprobe") or "ffprobe"
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if r.returncode != 0:
            raise AssertionError(f"ffprobe failed on {path}: {r.stderr}")
        return json.loads(r.stdout)

    def assert_media(
        self,
        path: str,
        *,
        video: bool | None = None,
        audio: bool | None = None,
        min_duration: float = 0.0,
        max_duration: float | None = None,
    ) -> dict:
        assert os.path.isfile(path), f"missing output {path}"
        assert os.path.getsize(path) > 0, f"empty output {path}"
        info = self.probe(path)
        kinds = [s.get("codec_type") for s in info.get("streams", [])]
        if video is not None:
            assert ("video" in kinds) == video, f"video={video} expected, got {kinds}"
        if audio is not None:
            assert ("audio" in kinds) == audio, f"audio={audio} expected, got {kinds}"
        dur = float(info.get("format", {}).get("duration", 0) or 0)
        assert dur >= min_duration, f"duration {dur} < {min_duration}"
        if max_duration is not None:
            assert dur <= max_duration, f"duration {dur} > {max_duration}"
        return info


# ─────────────────────────────────────────────────────────────────────────────
#  CLI: inspect a tab
# ─────────────────────────────────────────────────────────────────────────────


def _cli(argv: list[str]) -> int:
    import tkinter as tk
    from tkinter import ttk

    h = Harness.instance()
    if not argv or argv[0] == "--list":
        for n in h.page_names():
            print(n)
        return 0
    for name in argv:
        tab = h.open(name)
        print(f"== {name}  ({type(tab).__module__}.{type(tab).__name__})")
        print("-- tk variables:")
        for attr, (typ, val) in h.variables(tab).items():
            print(f"   {attr:<32} {typ:<14} {val!r}")
        print("-- buttons:")
        for b in h.find(tab, (tk.Button, ttk.Button)):
            print(f"   [{b.cget('state')}] {b.cget('text')!r}")
        entries = [a for a, v in vars(tab).items() if isinstance(v, (tk.Entry, ttk.Entry))]
        print("-- entry attributes:", ", ".join(sorted(entries)) or "(none)")
        if h.callback_errors:
            print("-- Tk callback errors:")
            for e in h.callback_errors:
                print(e)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
