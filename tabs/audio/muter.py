"""
tabs/audio/muter.py  -  The Muter

Silence (or bleep) chosen moments - swearing, names, background music -
with short fades so cuts don't click, or mute / remove the whole track.
The muting is sample-accurate: one aeval expression with a gain envelope
built from the ranges.  The picture is copied untouched.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import mute
from core.ops.shared import audio_filter_cmd, strip_audio_cmd
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import BaseTab
from tabs.kit.time_fields import RangePicker
from tabs.kit.tktyped import selected_rows
from tabs.widgets import FileRow, FramePreview, save_section, scroll_body

MODES = {
    "silence": "Silence the chosen moments",
    "bleep": "Bleep the chosen moments (1 kHz tone)",
    "silent_track": "Replace all sound with silence (keeps a silent track)",
    "remove": "Remove the audio track completely",
}


class MuterTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.ranges: list[tuple[float, float]] = []
        self._info: ff.MediaInfo | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(
            self, t("tab.the_muter"), t("muter.silence_or_bleep_moments_or_remove_the"), icon="🔇"
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body,
            t("common.source_video"),
            self.src_var,
            kind="media",
            on_change=self._source_changed,
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self._build_moments(body)
        self._build_mode(body)
        self._build_output(body)

    def _build_moments(self, body: tk.Frame) -> None:
        """The picture, the moment picker and the list of moments."""
        top = tk.Frame(body)
        top.pack(fill="x", padx=px(20), pady=px(4))
        pv = tk.Frame(top)
        pv.pack(side="left")
        self.preview = FramePreview(pv, 384, 216)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(
            pv,
            from_=0,
            to=1,
            variable=self.pos_var,
            command=lambda _v: self.preview.show(float(self.pos_var.get())),
        )
        self.slider.pack(fill="x")
        framestep.slider(self.slider, self.preview)  # , and . step a frame
        mark = tk.Frame(pv)
        mark.pack(fill="x")
        tk.Label(mark, text=t("muter.from")).pack(side="left")
        self.span = RangePicker(mark, start="", here=lambda: float(self.pos_var.get()))
        self.span.pack(side="left", padx=px(2))
        tk.Button(mark, text=t("muter.add"), relief="flat", command=self._add).pack(
            side="left", padx=px(4)
        )
        right = tk.Frame(top)
        right.pack(side="left", fill="both", expand=True, padx=px(10, 0))
        self.listbox = tk.Listbox(right, height=10)
        self.listbox.pack(fill="both", expand=True)
        tk.Button(right, text=t("muter.remove_selected"), relief="flat", command=self._remove).pack(
            anchor="w"
        )

    def _build_mode(self, body: tk.Frame) -> None:
        """What to do with the moments."""
        opt = tk.LabelFrame(body, text=t("muter.what_to_do"), padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.mode_var = tk.StringVar(value="silence")
        for key, label in MODES.items():
            tk.Radiobutton(opt, text=label, value=key, variable=self.mode_var).pack(anchor="w")
        fr = tk.Frame(opt)
        fr.pack(anchor="w", pady=px(4, 0))
        tk.Label(fr, text=t("muter.fade_s")).pack(side="left")
        self.fade_var = tk.StringVar(value="0.05")
        tk.Entry(fr, textvariable=self.fade_var, width=6).pack(side="left", padx=px(4))

    def _build_output(self, body: tk.Frame) -> None:
        """Where the result goes, the button and the log."""
        self.out_row = save_section(
            body,
            t("common.output_file"),
            kind="media",
            suggest=lambda: self.suggest_output(self.src_row.path, "muted"),
        )
        self.btn_render = self.make_render_btn(body, t("muter.apply"), self._render, width=20)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.preview.load(path, dur, 0.0)
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "muted"))

    def add_range(self, a: float, b: float) -> None:
        if b <= a:
            raise ValueError("The end must be after the start.")
        self.ranges.append((a, b))
        self.ranges.sort()
        self.listbox.delete(0, "end")
        for x, y in self.ranges:
            self.listbox.insert(
                "end", f"{ff.format_time(x)}  →  {ff.format_time(y)}   ({y - x:.2f}s)"
            )

    # i / o (and the buttons): the moment starts / ends at the frame shown
    def mark_in(self) -> None:
        self.span.start.use_here()

    def mark_out(self) -> None:
        self.span.end.use_here()

    def _add(self) -> None:
        try:
            start, end = self.span.span()
            if end is None:
                raise ValueError("Say where the muted part ends.")
            self.add_range(start, end)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)

    def _remove(self) -> None:
        sel = selected_rows(self.listbox)
        if sel:
            del self.ranges[sel[0]]
            self.listbox.delete(sel[0])

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        mode = self.mode_var.get()
        if mode in ("silence", "bleep") and not self.ranges:
            messagebox.showwarning(
                t("common.warning"), t("muter.add_at_least_one_moment_to_mute"), parent=self
            )
            return
        try:
            fade = common.number(self.fade_var.get() or "0", t("muter.fade"), 0, 5)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "muted")
        if not out:
            return
        if mode == "remove":
            cmd = strip_audio_cmd(src, out)
        elif mode == "silent_track":
            cmd = audio_filter_cmd(src, mute.SILENCE, out, info)
        else:
            cmd = audio_filter_cmd(
                src, mute.mute_filter(self.ranges, fade, mode == "bleep"), out, info
            )
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=info.duration or None,
            output=out,
            inputs=[src],
            name="Muter",
        )
