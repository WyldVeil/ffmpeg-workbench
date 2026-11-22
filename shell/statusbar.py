"""
shell/statusbar.py - the strip along the bottom of the window.

Left: a status light and message, the open tool, the render progress and the
queue.  Right: the FFmpeg in use and (on the Video Downloader) yt-dlp.  The
queue part reads the render queue (core/queue_manager.py) a few times a
second while jobs run.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any, Literal

from core.i18n import t
from core.queue_manager import RenderQueueManager, TaskStatus
from core.scaling import px
from core.skins import MONO_FONT, UI_FONT

logger = logging.getLogger("crossfader.shell")

_BUSY_WORDS = ("Processing", "Rendering", "Encoding", "Updating", "Downloading")


class StatusBar(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        skin: dict[str, Any],
        *,
        on_queue_click: Callable[[], object],
        on_ffmpeg_click: Callable[[], object],
        on_ytdlp_click: Callable[[], object],
    ) -> None:
        super().__init__(master, bg=skin["sb_bg"], height=px(26))
        self.skin = skin
        self.pack_propagate(False)
        sb, dim, border = skin["sb_bg"], skin["fgdim"], skin.get("border", "#2C2C2C")
        tk.Frame(self, bg=border, height=px(1)).pack(side="top", fill="x")
        inner = tk.Frame(self, bg=sb)
        inner.pack(fill="both", expand=True, padx=px(12))

        def divider(side: Literal["left", "right"]) -> None:
            tk.Frame(inner, bg=border, width=px(1)).pack(
                side=side, fill="y", pady=px(5), padx=px(10)
            )

        self._dot = tk.Label(inner, text="●", bg=sb, fg=skin["green"], font=(UI_FONT, 7))
        self._dot.pack(side="left", pady=px(5))
        self._message = tk.Label(
            inner, text="  " + t("app.status.ready"), bg=sb, fg=dim, font=(UI_FONT, 8)
        )
        self._message.pack(side="left")
        divider("left")
        self._tool = tk.Label(inner, text="", bg=sb, fg=dim, font=(UI_FONT, 8))
        self._tool.pack(side="left")

        divider("right")
        self.ffmpeg = tk.Label(
            inner,
            text=t("app.status.ffmpeg_checking"),
            bg=sb,
            fg=dim,
            font=(UI_FONT, 8, "bold"),
            cursor="hand2",
        )
        self.ffmpeg.pack(side="right", padx=px(6))
        self.ffmpeg.bind("<Button-1>", lambda _e: on_ffmpeg_click())
        divider("right")
        self.ytdlp = tk.Label(
            inner, text="", bg=sb, fg=skin["accent"], font=(UI_FONT, 8, "bold"), cursor="hand2"
        )
        self.ytdlp.bind("<Button-1>", lambda _e: on_ytdlp_click())  # shown on the Video Downloader

        divider("left")
        self._progress = tk.Frame(inner, bg=sb)  # shown while jobs run
        self._bar = ttk.Progressbar(self._progress, mode="determinate", length=px(140), maximum=100)
        self._bar.pack(side="left", pady=px(6))
        self._pct = tk.Label(
            self._progress, text="", bg=sb, fg=skin["accent"], font=(MONO_FONT, 8, "bold")
        )
        self._pct.pack(side="left", padx=px(4, 0))
        self._spinning = False

        divider("left")
        self._queue = tk.Label(
            inner,
            text=t("app.queue.idle"),
            bg=sb,
            fg=dim,
            font=(UI_FONT, 8, "bold"),
            cursor="hand2",
        )
        self._queue.pack(side="left", padx=px(4))
        self._queue.bind("<Button-1>", lambda _e: on_queue_click())
        self._refresh_pending = False

    # ── Messages ──────────────────────────────────────────────────────────────
    def set_status(self, message: str, color: str | None = None) -> None:
        skin = self.skin
        self._message.config(text=f"  {message}", fg=color or skin["fgdim"])
        low = message.lower()
        if any(w in message for w in _BUSY_WORDS):
            light = skin["orange"]
        elif any(w in low for w in ("error", "failed", "fail")):
            light = skin["red"]
        else:
            light = skin["green"]
        self._dot.config(fg=light)

    def set_tool(self, label: str) -> None:
        self._tool.config(text=label)

    def show_ytdlp(self, shown: bool) -> None:
        if shown:
            self.ytdlp.pack(side="right", padx=px(0, 6), before=self.ffmpeg)
        else:
            self.ytdlp.pack_forget()

    def recolour(self, skin: dict[str, Any]) -> None:
        self.skin = skin
        bg = skin["sb_bg"]
        for frame in (self, *self.winfo_children()):
            for widget in (frame, *frame.winfo_children()):
                with contextlib.suppress(tk.TclError):  # ttk widgets have no background option
                    widget["background"] = bg

    # ── The render queue ──────────────────────────────────────────────────────
    def queue_changed(self) -> None:
        """Refresh the queue label, progress bar and time left (on the UI thread)."""
        q = RenderQueueManager.get_instance()
        jobs = q.get_all_tasks()
        skin = self.skin
        active = [j for j in jobs if j.status is TaskStatus.ACTIVE]
        pending = [j for j in jobs if j.status is TaskStatus.PENDING]
        if active or pending:
            parts = ([f"{len(active)} active"] if active else []) + (
                [f"{len(pending)} pending"] if pending else []
            )
            self._queue.config(text=t("app.queue.busy", n=" | ".join(parts)), fg=skin["orange"])
            self._progress.pack(side="left", padx=px(4, 0))
            self._show_fraction(
                q.overall_progress(), max((j.eta or 0) for j in active) if active else 0
            )
            self._refresh_soon()
            return
        self._hide_progress()
        counts = {
            s: sum(1 for j in jobs if j.status is s)
            for s in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
        }
        bits = [
            f"{n} {label}"
            for n, label in (
                (counts[TaskStatus.DONE], "done ✓"),
                (counts[TaskStatus.FAILED], "failed"),
                (counts[TaskStatus.CANCELLED], "cancelled"),
            )
            if n
        ]
        if bits:
            self._queue.config(
                text="Queue: " + " | ".join(bits),
                fg=skin["red"] if counts[TaskStatus.FAILED] else skin["green"],
            )
        else:
            self._queue.config(text=t("app.queue.idle"), fg=skin["fgdim"])

    def _show_fraction(self, fraction: float | None, eta: float) -> None:
        if fraction is None:
            self._bar.config(mode="indeterminate")
            if not self._spinning:
                self._bar.start(12)
                self._spinning = True
            self._pct.config(text="")
            return
        if self._spinning:
            self._bar.stop()
            self._spinning = False
        self._bar.config(mode="determinate")
        self._bar["value"] = fraction * 100
        left = ""
        if eta > 1:
            m, s = divmod(int(eta), 60)
            left = f"  ≈{m}:{s:02d} left" if m else f"  ≈{s}s left"
        self._pct.config(text=f"{fraction * 100:.0f}%{left}")

    def _refresh_soon(self) -> None:
        """While jobs run, refresh a few times a second (the time left ticks down)."""
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def tick() -> None:
            self._refresh_pending = False
            self.queue_changed()

        self.after(400, tick)

    def _hide_progress(self) -> None:
        if self._spinning:
            self._bar.stop()
            self._spinning = False
        self._progress.pack_forget()
        self._bar["value"] = 0
        self._pct.config(text="")
