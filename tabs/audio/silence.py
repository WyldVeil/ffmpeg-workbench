"""
tabs/audio/silence.py  -  Silence Remover

Jump-cut away the pauses in talks, lectures and podcasts.  Silences are
found with silencedetect (threshold, minimum length, padding kept around
speech); the cuts are snapped to video frame boundaries and done with
trim/atrim + concat in one exact pass (the graph is written to a file, so
any number of cuts works), keeping picture and sound in sync.
"""

from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox
from typing import TYPE_CHECKING

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops import silence as sil
from core.ops.shared import AUDIO_ONLY_EXTS
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import FileRow, output_section, scroll_body

if TYPE_CHECKING:
    from core.queue_manager import TaskContext


class SilenceTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._zones = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(
            self,
            t("tab.silence_remover"),
            t("toolinfo.silence"),
            icon="✂",
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
        opt = tk.LabelFrame(body, text=t("silence.detection"), padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.thr_var, self.min_var, self.pad_var = (
            tk.StringVar(value="-35"),
            tk.StringVar(value="0.6"),
            tk.StringVar(value="0.15"),
        )
        for col, (label, var, hint) in enumerate(
            (
                ("Silence below (dB)", self.thr_var, "-30 noisy room … -45 studio"),
                ("Shortest pause (s)", self.min_var, "pauses shorter than this stay"),
                ("Keep around speech (s)", self.pad_var, "breathing room"),
            )
        ):
            tk.Label(opt, text=label).grid(row=0, column=col, sticky="w", padx=px(6))
            tk.Entry(opt, textvariable=var, width=8).grid(row=1, column=col, sticky="w", padx=px(6))
            tk.Label(opt, text=hint, fg=CLR["fgdim"], font=(UI_FONT, 8)).grid(
                row=2, column=col, sticky="w", padx=px(6)
            )
        self.report = tk.Label(body, text="", fg=CLR["accent"], font=(MONO_FONT, 10))
        self.report.pack(anchor="w", padx=px(24), pady=px(4))
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1"),
            kind="media",
            suggest=lambda: self.suggest_output(self.src_row.path, "tight"),
        )
        row = tk.Frame(body)
        row.pack(pady=px(8))
        self.btn_analyse = tk.Button(
            row,
            text=t("silence.analyse"),
            font=(UI_FONT, 11, "bold"),
            width=14,
            command=self._analyse,
        )
        self.btn_analyse.pack(side="left", padx=px(6))
        self.btn_render = self.make_render_btn(
            row, t("silence.remove_silences"), self._render, width=20
        )
        self.btn_render.pack(side="left", padx=px(6))
        self.console = self.make_console_block(body, height=6)

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        self._zones = None
        self.report.config(text="")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "tight"))

    def _params(self) -> tuple[float, float, float]:
        return (
            common.number(self.thr_var.get(), t("silence.silence_threshold"), -90, -5),
            common.number(self.min_var.get(), t("silence.shortest_pause"), 0.05, 60),
            common.number(self.pad_var.get(), t("silence.keep_around_speech"), 0, 5),
        )

    def _detect(
        self,
        src: str,
        info: ff.MediaInfo,
        thr: float,
        min_s: float,
        pad: float,
        runner: Callable[[list[str]], ff.RunResult],
    ) -> list[tuple[float, float]]:
        return sil.find_zones(src, info, thr, min_s, pad, runner)

    def _analyse_failed(self, exc: BaseException) -> None:
        self.btn_analyse.config(state="normal", text=t("silence.analyse"))
        messagebox.showerror(t("common.error"), str(exc), parent=self)

    def _analyse(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        try:
            thr, min_s, pad = self._params()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        self.btn_analyse.config(state="disabled", text=t("silence.listening"))

        def work() -> list[sil.Span]:
            return self._detect(src, info, thr, min_s, pad, lambda c: ff.run(c, tail=100000))

        def done(zones: list[sil.Span]) -> None:
            self.btn_analyse.config(state="normal", text=t("silence.analyse"))
            self._zones = (src, (thr, min_s, pad), zones)
            kept = sum(b - a for a, b in zones)
            self.report.config(
                text=t(
                    "silence.zones_parts_kept_kept_of_duration_removes",
                    zones=len(zones),
                    kept=ff.format_time(kept),
                    duration=ff.format_time(info.duration),
                    duration2=ff.format_time(info.duration - kept),
                )
            )

        tasks.submit(
            work,
            on_done=done,
            on_error=self._analyse_failed,
            name="SilenceDetect",
        )

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        if not info.has_audio:
            messagebox.showwarning(
                t("common.warning"), t("silence.this_file_has_no_audio_to_analyse"), parent=self
            )
            return
        try:
            thr, min_s, pad = self._params()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "tight")
        if not out:
            return
        container = common.container_of(out)
        audio_only = not info.has_video or out.lower().endswith(AUDIO_ONLY_EXTS)
        venc = None if audio_only else self.encoder.video_encode(container)

        def work(_p: object, _c: object, ctx: TaskContext) -> int:
            tmp = ctx.tempdir()
            zones = self._detect(
                src,
                info,
                thr,
                min_s,
                pad,
                lambda c: ctx.span(0.0, 0.25).run(c, duration=info.duration, tail=200000),
            )
            if not zones:
                raise RuntimeError(
                    "Everything is below the silence threshold - try a lower dB value."
                )
            kept = sum(b - a for a, b in zones)
            ctx.log(
                t(
                    "silence.keeping_zones_parts_kept_of_duration",
                    zones=len(zones),
                    kept=ff.format_time(kept),
                    duration=ff.format_time(info.duration),
                )
            )
            with open(os.path.join(tmp, "cuts.txt"), "w", encoding="utf-8") as fh:
                fh.write(sil.cut_graph(zones, not audio_only, True))
            if venc is not None and venc.filter_suffix:
                raise RuntimeError("Choose a software or NVENC/QSV encoder for this tool.")
            cmd = sil.tighten_cmd(src, "cuts.txt", ctx.output(), venc, container)
            ctx.span(0.25, 1.0).run(cmd, duration=kept, cwd=tmp)
            return 0

        common.queue_job(
            self,
            common.Job(
                name="Silence Remover", out=out, work=work, duration=info.duration, inputs=[src]
            ),
            btn=self.btn_render,
            console=self.console,
        )
