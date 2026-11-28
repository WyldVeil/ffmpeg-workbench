"""
tabs/builder/step_mode.py - a tool, whole, as one step of the All-in-One Builder.

:class:`StepHost` builds the tool's own page in a window of its own, on top of
the builder: everything the tool normally has (its preview, its options, its
buttons) except the file it reads (the builder gives it that), where it saves
and its Render button.  In their place, at the bottom: "Discard changes" and
"Save and return".

Saving doesn't render.  It presses the tool's Render button with the render
queue switched off: what the tool would have queued - its FFmpeg commands, or
its Python job - is *captured* instead (:class:`Captured`), and becomes the
step.  So every tool keeps all its checks and messages ("choose a LUT file
first") and the builder never needs to know how a tool works.

The window stays alive (hidden) as long as the step: opening its settings
again shows it as it was left, and a Python job captured from it can still
read the tool's settings when the builder runs it.  :meth:`StepHost.snapshot`
and :meth:`StepHost.restore` read and set the tool's settings (every Tk
variable on the page, plus ``step_state()``/``set_step_state()`` on tools
that keep settings elsewhere) - for "Discard changes" and for recipes.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import tkinter as tk
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any, cast

from core import tasks
from core.i18n import t
from core.scaling import px
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.builder import catalog
from tabs.kit.tktyped import raw_value
from tabs.widgets import EncoderPanel, FileRow, SaveRow

logger = logging.getLogger("crossfader.builder")

#: Every step window's host, by window path (tests find a host from its window).
HOSTS: dict[str, StepHost] = {}

#: Attributes never walked when looking for a page's settings.
_SKIP = frozenset({"master", "tk", "children", "_w", "widgetName", "_name", "_tclCommands"})


@dataclass
class Captured:
    """What a tool would have queued: *cmds* (FFmpeg commands) or *run* (its
    Python job), writing *output*."""

    cmds: list[list[str]] | None
    run: Any
    output: str
    name: str


class CaptureFailed(Exception):
    """The tool didn't queue anything; *str()* is what it said, if anything."""


def _variables(
    obj: object, prefix: str, depth: int, seen: set[int]
) -> Iterator[tuple[str, tk.Variable]]:
    if id(obj) in seen:
        return
    seen.add(id(obj))
    for attr, value in list(vars(obj).items()):
        if attr in _SKIP or attr.startswith("__"):
            continue
        path = f"{prefix}{attr}"
        if isinstance(value, tk.Variable):
            yield path, value
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                if isinstance(item, tk.Variable):
                    yield f"{path}[{i}]", item
        elif isinstance(value, dict):
            for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
                if isinstance(key, str) and isinstance(item, tk.Variable):
                    yield f"{path}[{key}]", item
        elif (
            depth > 0 and type(value).__module__.startswith("tabs.") and hasattr(value, "__dict__")
        ):
            yield from _variables(value, path + ".", depth - 1, seen)


def variables(tab: object) -> dict[str, tk.Variable]:
    """Every Tk variable on *tab* and its own widgets, by attribute path."""
    return dict(_variables(tab, "", 3, set()))


class StepHost:
    """One tool's page in its own (normally hidden) window, set up as a step."""

    def __init__(self, owner: tk.Misc, kind: str, workdir: str, uid: str) -> None:
        self.kind = kind
        self.uid = uid
        self.workdir = workdir
        self.input = ""
        self.win = tk.Toplevel(owner)
        self.win.withdraw()
        HOSTS[str(self.win)] = self
        self.win.configure(bg=CLR["bg"])
        self.win.protocol("WM_DELETE_WINDOW", self._close_asked)
        self.banner = tk.Label(
            self.win, anchor="w", justify="left", bg=CLR["panel"], fg=CLR["fg"],
            font=(UI_FONT, 10), padx=px(16), pady=px(8),
        )  # fmt: skip
        self.banner.pack(side="top", fill="x")
        bar = tk.Frame(self.win, bg=CLR["panel"])
        bar.pack(side="bottom", fill="x")
        tk.Frame(self.win, bg=CLR["border"], height=px(1)).pack(side="bottom", fill="x")
        self.btn_save = tk.Button(
            bar, text=t("builder.save_return"), font=(UI_FONT, 11, "bold"), bg=CLR["green"],
            fg="white", activebackground=CLR["green"], activeforeground="white",
            cursor="hand2", padx=px(16), pady=px(4), command=self._save_pressed,
        )  # fmt: skip
        self.btn_save.pack(side="right", padx=px(16), pady=px(10))
        self.btn_discard = tk.Button(
            bar, text=t("builder.discard_return"), font=(UI_FONT, 10), relief="flat",
            cursor="hand2", padx=px(12), pady=px(4), command=self._close_asked,
        )  # fmt: skip
        self.btn_discard.pack(side="right", pady=px(10))
        self.saving = tk.Label(bar, text="", bg=CLR["panel"], fg=CLR["fgdim"], font=(UI_FONT, 9))
        self.saving.pack(side="left", padx=px(16))
        holder = tk.Frame(self.win)
        holder.pack(fill="both", expand=True)
        self.tab: BaseTab = self._build(holder)
        self._managed: set[str] = set()  # the variables the builder sets (input, output)
        self._opened: dict[str, Any] = {}
        self._on_save: Callable[[], object] | None = None
        self._on_close: Callable[[], object] | None = None
        self._install()

    # ── Building ──────────────────────────────────────────────────────
    def _build(self, holder: tk.Frame) -> BaseTab:
        from tabs.builder.basic import QuickAdjustTab
        from tabs.registry import PAGES

        tool = catalog.kind_of(self.kind)
        page = QuickAdjustTab(holder) if tool is None else PAGES[tool](holder)
        page.pack(fill="both", expand=True)
        return page

    def _install(self) -> None:
        """Point the tool's saving at the step's folder, and its queue at us."""
        tab, work, uid = self.tab, self.workdir, self.uid
        self.captured: list[Captured] = []

        def suggest_output(src: str, suffix: str = "edited", ext: str | None = None) -> str:
            del src
            return os.path.join(work, f"{uid}-{suffix}{ext or '.mp4'}")

        def pick_output(
            default_name: str = "", kind: str = "video", ext: str = ".mp4", title: str | None = None
        ) -> str:
            del default_name, kind, title
            return os.path.join(work, f"{uid}-out{ext}")

        def confirm_output(out: str, inputs: object = (), default_ext: str = ".mp4") -> str:
            del inputs
            return out if os.path.splitext(out)[1] else out + default_ext

        def enqueue_render(task_name: str, output_path: str = "", *, work: Any, **_kw: Any) -> str:
            if callable(work):
                self.captured.append(Captured(None, work, output_path, task_name))
            else:
                cmds = [[str(a) for a in c] for c in work]
                out = output_path or (cmds[-1][-1] if cmds and cmds[-1] else "")
                self.captured.append(Captured(cmds, None, out, task_name))
            return f"step-{uid}"

        for name, fn in (
            ("suggest_output", suggest_output),
            ("pick_output", pick_output),
            ("confirm_output", confirm_output),
            ("enqueue_render", enqueue_render),
        ):
            setattr(tab, name, fn)

    def load(self, path: str, done: Callable[[], object]) -> None:
        """Give the tool *path* as its video, then call *done* once it settles."""
        self.input = path
        try:
            accepted = self.tab.load_file(path)
        except Exception:
            logger.exception("step %s: load_file failed", self.kind)
            accepted = False
        if not accepted:
            logger.info("step %s didn't take the file through load_file", self.kind)
        for widget in _walk(self.tab):  # a file row waits for typing to pause: not now
            if isinstance(widget, FileRow):
                widget.flush()
        self._settle(lambda: (self._adapt(), done()))

    def _settle(self, then: Callable[[], object], waited: float = 0.0, quiet: int = 0) -> None:
        """Wait until the tool's background work (probing, thumbnails) is done."""
        busy = tasks.busy() > 0
        quiet = 0 if busy else quiet + 1
        if quiet >= 5 or waited > 8.0:
            then()
            return
        self.win.after(120, self._settle, then, waited + 0.12, quiet)

    def _adapt(self) -> None:
        """Hide what the builder takes care of: the input row, where it saves,
        the encoder (unless the tool decides the format) and Render."""
        own = catalog.own_format(self.kind)
        found = variables(self.tab)
        self._managed = {
            k
            for k, v in found.items()
            if _raw(self.tab, v) == self.input or str(_raw(self.tab, v)).startswith(self.workdir)
        }
        for widget in _walk(self.tab):
            if isinstance(widget, SaveRow):
                widget.set(os.path.join(self.workdir, f"{self.uid}-out{widget.ext}"))
                _hide(widget)
            elif (isinstance(widget, FileRow) and widget.path == self.input) or (
                isinstance(widget, EncoderPanel) and not own
            ):
                _hide(widget)
            elif isinstance(widget, tk.Entry) and _entry_value(widget) == self.input:
                _hide(widget.master)
        for name, var in found.items():
            low = name.lower()
            if "out" in low and isinstance(var, tk.StringVar) and _looks_like_path(var.get()):
                ext = os.path.splitext(var.get())[1] or ".mp4"
                var.set(os.path.join(self.workdir, f"{self.uid}-out{ext}"))
                self._managed.add(name)
                for widget in _walk(self.tab):
                    if isinstance(widget, tk.Entry) and _entry_value(widget) == var.get():
                        _hide(widget.master)
        for widget in _walk(self.tab):  # an Output box left empty goes too
            if isinstance(widget, tk.LabelFrame) and _all_hidden(widget):
                _hide(widget)
        btn = getattr(self.tab, "btn_render", None)
        if isinstance(btn, tk.Button):
            _hide(btn)

    # ── Settings ──────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        """The tool's settings now (JSON-friendly)."""
        values = {
            k: _raw(self.tab, v) for k, v in variables(self.tab).items() if k not in self._managed
        }
        extra: dict[str, Any] = {}
        getter = getattr(self.tab, "step_state", None)
        if callable(getter):
            raw: object = getter()
            if isinstance(raw, dict):
                extra = cast("dict[str, Any]", raw)
        return {
            "vars": {k: v for k, v in values.items() if isinstance(v, (str, int, float))},
            "extra": extra,
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Put the settings *state* (from :meth:`snapshot`) back."""
        found = variables(self.tab)
        wanted: dict[str, Any] = dict(state.get("vars", {}))
        for _round in range(2):  # a setting's trace can change another: set twice
            for k, value in wanted.items():
                var = found.get(k)
                if var is None or k in self._managed:
                    continue
                if str(_raw(self.tab, var)) != str(value):
                    with contextlib.suppress(tk.TclError):
                        self.tab.setvar(str(var), value)
        setter = getattr(self.tab, "set_step_state", None)
        if callable(setter) and state.get("extra") is not None:
            try:
                setter(state["extra"])
            except Exception:
                logger.exception("step %s: set_step_state failed", self.kind)

    # ── Capturing ─────────────────────────────────────────────────────
    def capture(
        self, done: Callable[[Captured | None, str], object], *, quiet: bool = False
    ) -> None:
        """Press the tool's Render with the queue switched off; *done(captured,
        message)* gets what it would have queued (or None and what it said)."""
        self.captured.clear()
        _Capture(self, done, quiet).start()

    # ── Showing ───────────────────────────────────────────────────────
    def show(
        self,
        title: str,
        banner: str,
        on_save: Callable[[], object],
        on_close: Callable[[], object],
    ) -> None:
        """Show the window over its owner (offset, so the builder is still seen
        behind it) and keep the owner out of reach until it's closed."""
        self._on_save, self._on_close = on_save, on_close
        self._opened = self.snapshot()
        self.win.title(title)
        self.banner.config(text=banner)
        owner = self.win.master.winfo_toplevel()
        owner.update_idletasks()
        off = px(36)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w = min(max(owner.winfo_width(), px(900)), sw - off * 2)
        h = min(max(owner.winfo_height(), px(640)), sh - off * 3)
        x = min(owner.winfo_rootx() + off, max(0, sw - w))
        y = min(owner.winfo_rooty() + off, max(0, sh - h - off))
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.transient(owner)  # pyright: ignore[reportArgumentType]
        self.win.deiconify()
        self.win.bind("<Control-s>", lambda _e: self._save_pressed())
        self.win.bind("<Escape>", lambda _e: self._close_asked())
        self.win.after(50, self._grab)

    def _grab(self) -> None:
        with contextlib.suppress(tk.TclError):
            self.win.grab_set()
            self.win.focus_set()

    def hide(self) -> None:
        with contextlib.suppress(tk.TclError):
            self.win.grab_release()
        with contextlib.suppress(Exception):
            self.tab.stop_preview()
        self.win.withdraw()

    def changed(self) -> bool:
        return self.snapshot() != self._opened

    def _save_pressed(self) -> None:
        if self._on_save is not None:
            self._on_save()

    def _close_asked(self) -> None:
        if self.changed() and not messagebox.askyesno(
            t("builder.discard_title"), t("builder.discard_ask"), parent=self.win
        ):
            return
        self.restore(self._opened)
        if self._on_close is not None:
            self._on_close()

    def busy(self, on: bool) -> None:
        """While saving: the buttons wait, and say so."""
        state = "disabled" if on else "normal"
        for b in (self.btn_save, self.btn_discard):
            b.config(state=state)
        self.saving.config(text=t("builder.saving") if on else "")

    def destroy(self) -> None:
        with contextlib.suppress(tk.TclError):
            self.win.grab_release()
        with contextlib.suppress(Exception):
            self.tab.stop_preview()
            self.tab.on_app_closing()
        HOSTS.pop(str(self.win), None)
        with contextlib.suppress(tk.TclError):
            self.win.destroy()


class _Capture:
    """One press of a tool's Render, recording what it queues and what it says."""

    def __init__(
        self, host: StepHost, done: Callable[[Captured | None, str], object], quiet: bool
    ) -> None:
        self.host, self.done, self.quiet = host, done, quiet
        self.said: list[str] = []
        btn = getattr(host.tab, "btn_render", None)
        self.btn = btn if isinstance(btn, tk.Button) else None
        self.before: tuple[str, str, str] | None = None
        if self.btn is not None:
            with contextlib.suppress(tk.TclError):
                b = self.btn
                self.before = (str(b.cget("text")), str(b.cget("command")), str(b.cget("state")))
        console = getattr(host.tab, "console", None)
        self.console = console if isinstance(console, tk.Text) else None
        self.mark = self.console.index("end-1c") if self.console is not None else None
        self.started = time.monotonic()

    def _told(self, show: Callable[..., Any]) -> Callable[..., Any]:
        def told(title: str = "", message: str = "", **kw: Any) -> Any:
            self.said.append(str(message or title))
            if self.quiet:
                return "ok"
            kw.setdefault("parent", self.host.win)
            return show(title, message, **kw)

        return told

    def start(self) -> None:
        originals = {n: getattr(messagebox, n) for n in ("showwarning", "showerror", "showinfo")}
        for n, fn in originals.items():
            setattr(messagebox, n, self._told(fn))
        try:
            if self.btn is not None:
                self.btn.config(state="normal")
                self.btn.invoke()
            else:
                self.host.tab.render()
        except Exception as exc:
            logger.exception("step %s: render failed", self.host.kind)
            self.said.append(str(exc))
        finally:
            for n, fn in originals.items():
                setattr(messagebox, n, fn)
        self._wait()

    def _wait(self) -> None:
        """Some tools check things in the background before queueing: wait for them."""
        waited = time.monotonic() - self.started
        if self.host.captured:
            self._finish(self.host.captured[0])
        elif self.said or waited > 30 or (tasks.busy() == 0 and waited > 3):
            self._finish(None)
        else:
            self.host.win.after(100, self._wait)

    def _finish(self, found: Captured | None) -> None:
        if found is not None and self.mark is not None and self.console is not None:
            with contextlib.suppress(tk.TclError):  # "Rendering..." - it wasn't
                self.console.delete(self.mark, "end-1c")
        if self.before is not None and self.btn is not None:
            with contextlib.suppress(tk.TclError):
                text, command, state = self.before
                self.btn.config(text=text, command=command)
                self.btn["state"] = state
        self.done(found, "\n".join(s for s in self.said if s))


def _raw(tab: tk.Misc, var: tk.Variable) -> Any:
    try:
        return raw_value(tab, var)
    except tk.TclError:
        return ""


def _walk(widget: tk.Misc) -> Iterator[tk.Misc]:
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _entry_value(entry: tk.Entry) -> str:
    try:
        return entry.get().strip()
    except tk.TclError:
        return ""


def _looks_like_path(text: str) -> bool:
    return bool(text) and (os.sep in text or "/" in text) and bool(os.path.splitext(text)[1])


def _hide(widget: tk.Misc) -> None:
    if not isinstance(widget, tk.Widget):
        return
    with contextlib.suppress(tk.TclError):
        manager = widget.winfo_manager()
        if manager == "pack":
            widget.pack_forget()
        elif manager == "grid":
            widget.grid_remove()
        elif manager == "place":
            widget.place_forget()


def _all_hidden(frame: tk.Misc) -> bool:
    kids = [w for w in frame.winfo_children() if not isinstance(w, tk.Toplevel)]
    return bool(kids) and all(not w.winfo_manager() for w in kids)
