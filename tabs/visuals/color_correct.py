"""
tabs/visuals/color_correct.py  -  Colour Corrector

Exposure, contrast, tone (blacks / shadows / highlights / whites), white
balance, saturation, vibrance, hue and sharpening, with a live in-app
before/after preview of the exact filter chain that will be rendered.
"""

from __future__ import annotations

import tkinter as tk
from functools import partial
from tkinter import ttk

from core.i18n import Choices, t
from core.ops import color_correct as cc
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, FileRow, output_section, scroll_body

GROUPS = [
    ("Light", ("exposure", "contrast", "brightness", "gamma")),
    ("Tone", ("blacks", "shadows", "highlights", "whites")),
    ("Colour", ("temperature", "tint", "saturation", "vibrance", "hue")),
    ("Detail", ("sharpen",)),
]


#: the looks, shown in the current language
LOOKS = Choices("color_correct.looks", cc.LOOKS)


class ColorCorrectorTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.vars: dict[str, tk.DoubleVar] = {}
        self._preview_job: str | None = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.make_header(
            self,
            t("tab.basic_color_corrector"),
            t("color_correct.exposure_tone_white_balance_and_colour_with"),
            icon="🌈",
        )
        body = scroll_body(self)

        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        self.compare = CompareView(body, 400, 225)
        self.compare.pack(pady=px(6))

        looks = tk.Frame(body, bg=CLR["bg"])
        looks.pack(fill="x", padx=px(20), pady=px(2, 4))
        tk.Label(
            looks,
            text=t("color_correct.quick_look"),
            bg=CLR["bg"],
            fg=CLR["fg"],
            font=(UI_FONT, 10, "bold"),
        ).pack(side="left")
        self.look_var = tk.StringVar(value=LOOKS.label("Neutral (reset)"))
        box = ttk.Combobox(
            looks, textvariable=self.look_var, values=list(LOOKS), state="readonly", width=22
        )
        box.pack(side="left", padx=px(8))
        box.bind("<<ComboboxSelected>>", lambda _e: self._apply_look(self.look_var.get()))
        tk.Button(
            looks,
            text=t("color_correct.btn_reset_all"),
            relief="flat",
            cursor="hand2",
            command=lambda: self._apply_look(LOOKS.label("Neutral (reset)")),
        ).pack(side="left", padx=px(6))

        grid = tk.Frame(body, bg=CLR["bg"])
        grid.pack(fill="x", padx=px(20), pady=px(4))
        for col, (title, keys) in enumerate((GROUPS[0], GROUPS[2])):
            self._group(grid, title, keys).grid(
                row=0, column=col, sticky="nsew", padx=px(4), pady=px(4)
            )
        for col, (title, keys) in enumerate((GROUPS[1], GROUPS[3])):
            self._group(grid, title, keys).grid(
                row=1, column=col, sticky="nsew", padx=px(4), pady=px(4)
            )
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores", "dnxhr"),
            suggest=lambda: self.suggest_output(self.src_row.path, "graded"),
        )

        self.btn_render = self.make_render_btn(
            body, t("color_correct.render_grade"), self._render, width=24
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5, bg=CLR["bg"])

    def _reset_slider(self, var: tk.DoubleVar, default: float) -> None:
        var.set(default)
        self._schedule_preview()

    def _group(self, parent: tk.Misc, title: str, keys: tuple[str, ...]) -> tk.LabelFrame:
        lf = tk.LabelFrame(parent, text=f"  {title}  ", padx=px(10), pady=px(4))
        for row, key in enumerate(keys):
            label, lo, hi, default, res = cc.CONTROLS[key]
            var = tk.DoubleVar(value=default)
            self.vars[key] = var
            self.form_row(
                lf,
                row,
                label,
                tk.Scale(
                    lf,
                    variable=var,
                    from_=lo,
                    to=hi,
                    resolution=res,
                    orient="horizontal",
                    length=px(190),
                    showvalue=True,
                    command=lambda _v: self._schedule_preview(),
                ),
                tk.Button(
                    lf,
                    text="↺",
                    width=2,
                    relief="flat",
                    cursor="hand2",
                    command=partial(self._reset_slider, var, default),
                ),
                label_width=22,
            )
        return lf

    # ── Behaviour ────────────────────────────────────────────────────────────

    def values(self) -> dict[str, float]:
        return {k: v.get() for k, v in self.vars.items()}

    def _apply_look(self, name: str) -> None:
        merged = dict(cc.DEFAULTS)
        merged.update(LOOKS.get(name) or {})
        for key, var in self.vars.items():
            var.set(merged[key])
        self._schedule_preview()

    def _source_changed(self, path: str) -> None:
        self.compare.load(path)
        self.compare.set_filter(cc.build_chain(self.values()))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "graded"))

    def _schedule_preview(self) -> None:
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(150, self._update_preview)

    def _update_preview(self) -> None:
        self._preview_job = None
        self.compare.set_filter(cc.build_chain(self.values()))

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        chain = cc.build_chain(self.values())
        if not chain:
            self.log(self.console, t("color_correct.no_adjustments_set_the_video_will_just"))
        out = common.output_for(self, self.out_row, src, "graded")
        if not out:
            return
        common.render(
            self,
            RenderJob(src=src, info=info, out=out, chain=chain, name="Colour Correct"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
