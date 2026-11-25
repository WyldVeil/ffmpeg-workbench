"""
tabs/cutting/side_by_side.py  -  Side-by-Side

Comparison and reaction layouts from 2-4 clips: side by side, stacked,
2x2 grid, or a before/after wipe (fixed or sweeping).  Each clip is fitted
(or cropped) into its cell, optional labels and divider, audio from either
clip or a mix, and the length follows the shortest or longest clip - with
shorter clips looping or holding their last frame.
"""

from __future__ import annotations

import os
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops.shared import frame_rate
from core.ops.side_by_side import Arrangement, Clip, build_graph, labels_script, sbs_cmd
from core.scaling import px
from tabs import common
from tabs.base_tab import BaseTab
from tabs.widgets import FileRow, output_section, scroll_body

LAYOUTS = {
    "side": "Side by side",
    "stack": "Stacked (top / bottom)",
    "grid": "2×2 grid",
    "wipe": "Before / after wipe",
}
SIZES = {
    "1920×1080": (1920, 1080),
    "3840×2160": (3840, 2160),
    "1280×720": (1280, 720),
    "1080×1920 (vertical)": (1080, 1920),
    "1080×1080": (1080, 1080),
}
AUDIO = {
    "0": "Audio from clip 1",
    "1": "Audio from clip 2",
    "mix": "Mix all clips",
    "none": "No audio",
}
LENGTH = {
    "shortest": "Stop when the shortest clip ends",
    "loop": "Run to the longest - loop shorter clips",
    "hold": "Run to the longest - hold shorter clips' last frame",
}


class SideBySideTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._build_ui()

    def _build_ui(self) -> None:
        body, lay = self._build_header()
        self._build_output(body, lay)
        self._build_render(body)

    def _build_header(self) -> tuple[tk.Frame, tk.LabelFrame]:
        self.make_header(
            self,
            t("tab.side_by_side"),
            t("side_by_side.comparison_reaction_and_before_after_layouts_fro"),
            icon="◫",
        )
        body = scroll_body(self)
        clips = tk.LabelFrame(body, text=t("beat_sync.clips"), padx=px(12), pady=px(6))
        clips.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.rows: list[FileRow] = []
        self.label_vars: list[tk.StringVar] = []
        self.speed_vars: list[tk.StringVar] = []
        for i in range(4):
            line = tk.Frame(clips)
            line.pack(fill="x", pady=px(2))
            fr = FileRow(
                line,
                t("side_by_side.clip_i_value", i=i + 1, value="" if i < 2 else " (grid)"),
                self.src_var if i == 0 else None,
                label_width=12,
                width=40,
            )
            fr.pack(side="left", fill="x", expand=True)
            lv = tk.StringVar(value=("Before", "After", "", "")[i])
            tk.Label(line, text=t("side_by_side.label")).pack(side="left", padx=px(6, 2))
            tk.Entry(line, textvariable=lv, width=12).pack(side="left")
            sv = tk.StringVar(value="1.0")
            tk.Label(line, text=t("common.speed")).pack(side="left", padx=px(6, 2))
            ttk.Combobox(
                line,
                textvariable=sv,
                values=["0.25", "0.5", "0.75", "1.0", "1.25", "1.5", "2.0"],
                width=5,
            ).pack(side="left")
            self.rows.append(fr)
            self.label_vars.append(lv)
            self.speed_vars.append(sv)

        lay = tk.LabelFrame(body, text=t("side_by_side.layout"), padx=px(12), pady=px(6))
        lay.pack(fill="x", padx=px(20), pady=px(4))

        self.layout_var = tk.StringVar(value=LAYOUTS["side"])
        self.size_var = tk.StringVar(value="1920×1080")
        self.form_row(
            lay,
            0,
            t("side_by_side.layout_label"),
            ttk.Combobox(
                lay,
                textvariable=self.layout_var,
                values=list(LAYOUTS.values()),
                state="readonly",
                width=24,
            ),
            ttk.Combobox(
                lay, textvariable=self.size_var, values=list(SIZES), state="readonly", width=18
            ),
            label_width=14,
            pady=2,
        )
        return body, lay

    def _build_output(self, body: tk.Frame, lay: tk.LabelFrame) -> None:
        self.fill_var = tk.BooleanVar(value=False)
        self.divider_var = tk.BooleanVar(value=True)
        self.labels_on = tk.BooleanVar(value=True)
        self.form_row(
            lay,
            1,
            t("slideshow.framing"),
            tk.Checkbutton(
                lay,
                text=t("side_by_side.fill_cells_crop_instead_of_black_bars"),
                variable=self.fill_var,
            ),
            tk.Checkbutton(lay, text=t("side_by_side.divider_line"), variable=self.divider_var),
            tk.Checkbutton(lay, text=t("side_by_side.labels"), variable=self.labels_on),
            label_width=14,
            pady=2,
        )
        self.wipe_pos = tk.DoubleVar(value=0.5)
        self.wipe_period = tk.StringVar(value="0")
        self.form_row(
            lay,
            2,
            t("side_by_side.wipe"),
            tk.Scale(
                lay,
                variable=self.wipe_pos,
                from_=0.05,
                to=0.95,
                resolution=0.05,
                orient="horizontal",
                length=px(160),
            ),
            tk.Label(lay, text=t("side_by_side.sweep_every_s_0_fixed")),
            tk.Entry(lay, textvariable=self.wipe_period, width=5),
            label_width=14,
            pady=2,
        )
        self.audio_var = tk.StringVar(value=AUDIO["0"])
        self.length_var = tk.StringVar(value=LENGTH["shortest"])
        self.form_row(
            lay,
            3,
            t("crossfader.audio"),
            ttk.Combobox(
                lay,
                textvariable=self.audio_var,
                values=list(AUDIO.values()),
                state="readonly",
                width=24,
            ),
            label_width=14,
            pady=2,
        )
        self.form_row(
            lay,
            4,
            t("collage.length"),
            ttk.Combobox(
                lay,
                textvariable=self.length_var,
                values=list(LENGTH.values()),
                state="readonly",
                width=44,
            ),
            label_width=14,
            pady=2,
        )

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.rows[0].path, "side_by_side"),
        )

    def _build_render(self, body: tk.Frame) -> None:
        self.btn_render = self.make_render_btn(
            body, t("side_by_side.create"), self._render, width=22
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _key(self, table: dict[str, str], var: tk.StringVar, default: str) -> str:
        return next((k for k, v in table.items() if v == var.get()), default)

    def _clips(self, layout: str) -> list[Clip] | None:
        """The clips to show (two, or up to four in a grid); None after saying what's wrong."""
        clips: list[Clip] = []
        for i, fr in enumerate(self.rows[: 4 if layout == "grid" else 2]):
            path = fr.path
            if not path and layout == "grid" and i >= 2:
                continue
            info = common.check_source(self, path, what=f"Clip {i + 1}")
            if info is None:
                return None
            try:
                speed = common.number(
                    self.speed_vars[i].get(), t("side_by_side.clip_i_speed", i=i + 1), 0.1, 4
                )
            except ValueError as exc:
                messagebox.showwarning(t("common.warning"), str(exc), parent=self)
                return None
            clips.append(Clip(path, info.duration, info.has_audio, self.label_vars[i].get(), speed))
        return clips

    def _render(self) -> None:
        layout = self._key(LAYOUTS, self.layout_var, "side")
        clips = self._clips(layout)
        if clips is None:
            return
        labels = [c.label for c in clips]
        try:
            period = common.number(self.wipe_period.get() or "0", t("side_by_side.sweep"), 0, 600)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h = SIZES.get(self.size_var.get(), (1920, 1080))
        first = ff.probe(clips[0].path)
        fps = frame_rate(first)
        out = common.output_for(
            self, self.out_row, clips[0].path, "side_by_side", inputs=[c.path for c in clips[1:]]
        )
        if not out:
            return
        length = self._key(LENGTH, self.length_var, "shortest")
        longest = max(c.duration / c.speed for c in clips)
        from core.paths import temp_dir

        work = temp_dir("sbs_")
        labels_path = None
        if self.labels_on.get() and any(lbl.strip() for lbl in labels):
            labels_path = labels_script(layout, labels, w, h, longest).write(
                os.path.join(work, "labels.ass")
            )
        arrangement = Arrangement(
            layout=layout,
            width=w,
            height=h,
            fps=fps,
            fill=self.fill_var.get(),
            audio=self._key(AUDIO, self.audio_var, "0"),
            length=length,
            wipe_pos=float(self.wipe_pos.get()),
            wipe_period=period,
            divider=self.divider_var.get(),
            labels_ass=labels_path,
        )
        graph, total, has_audio = build_graph(clips, arrangement)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        loop_to = longest if length == "loop" else None
        cmd = sbs_cmd(clips, graph, venc, container, total, out, audio=has_audio, loop_to=loop_to)

        def done(rc: int) -> None:
            shutil.rmtree(work, ignore_errors=True)
            self.show_result(rc, out)

        self.log(self.console, f"{LAYOUTS[layout]} · {w}×{h} · {ff.format_time(total)}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=done,
            btn=self.btn_render,
            duration=total,
            output=out,
            inputs=[c.path for c in clips],
            name="Side-by-Side",
        )
