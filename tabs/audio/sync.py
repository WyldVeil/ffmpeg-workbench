"""
tabs/audio/sync.py  -  Audio Sync Shifter

Fix lip-sync: move the sound later (positive) or earlier (negative)
relative to the picture.

* Lossless (default): the audio track is re-timed with -itsoffset and
  everything is stream-copied - instant, no quality loss, other tracks kept.
* Re-encode: the audio is delayed (adelay) or trimmed (atrim) in a filter,
  for players that ignore container timing.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import sync as sy
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, SaveRow, scroll_body


class AudioSyncTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.audio_sync_shifter"), "Fix out-of-sync sound.", icon="⏱")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        opt = tk.LabelFrame(body, text="  Shift  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        tk.Label(opt, text="Move the sound by (seconds):").grid(row=0, column=0, sticky="w")
        self.offset_var = tk.StringVar(value="0.000")
        tk.Entry(opt, textvariable=self.offset_var, width=10).grid(
            row=0, column=1, sticky="w", padx=px(4)
        )
        tk.Label(
            opt,
            text="positive = later (sound was early), negative = earlier",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).grid(row=0, column=2, sticky="w")
        nudges = tk.Frame(opt)
        nudges.grid(row=1, column=0, columnspan=3, sticky="w", pady=px(4))
        for d in (-0.5, -0.1, -0.04, 0.04, 0.1, 0.5):
            tk.Button(
                nudges, text=f"{d:+g}s", relief="flat", width=6, command=lambda v=d: self._nudge(v)
            ).pack(side="left", padx=px(1))
        tk.Label(opt, text="Audio track:").grid(row=2, column=0, sticky="w")
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(opt, textvariable=self.track_var, state="readonly", width=36)
        self.track_box.grid(row=2, column=1, columnspan=2, sticky="w")
        self.lossless_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt,
            text="Lossless (instant; re-times the track without re-encoding)",
            variable=self.lossless_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=px(4))
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "synced"),
        )
        self.out_row.pack(fill="x")
        self.btn_render = self.make_render_btn(body, "⏱  APPLY", self._render, width=20)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _nudge(self, d):
        try:
            v = float(self.offset_var.get() or 0)
        except ValueError:
            v = 0.0
        self.offset_var.set(f"{v + d:.3f}")

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        tracks = (
            [
                f"Track {i + 1}: {a.codec}" + (f" [{a.language}]" if a.language else "")
                for i, a in enumerate(self._info.audio_streams)
            ]
            if self._info
            else []
        )
        self.track_box.config(values=tracks or ["(no audio)"])
        self.track_var.set(tracks[0] if tracks else "(no audio)")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "synced"))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        if not info.has_audio:
            messagebox.showwarning(t("common.warning"), "This video has no audio.", parent=self)
            return
        try:
            offset = float(self.offset_var.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning(
                t("common.warning"), "The shift must be a number of seconds.", parent=self
            )
            return
        out = common.output_for(self, self.out_row, src, "synced")
        if not out:
            return
        values = list(self.track_box.cget("values"))
        track = values.index(self.track_var.get()) if self.track_var.get() in values else 0
        if self.lossless_var.get():
            cmd = sy.lossless_cmd(src, out, offset, track, len(info.audio_streams))
        else:
            cmd = sy.reencode_cmd(src, out, offset, track, info.duration)
        self.log(self.console, f"Shifting audio by {offset:+.3f}s")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=info.duration or None,
            output=out,
            inputs=[src],
            name="Audio Sync",
        )
