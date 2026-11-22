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

from core.i18n import t
from core.ops import color_correct as cc
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, SaveRow, scroll_body

GROUPS = [
    ("Light", ("exposure", "contrast", "brightness", "gamma")),
    ("Tone", ("blacks", "shadows", "highlights", "whites")),
    ("Colour", ("temperature", "tint", "saturation", "vibrance", "hue")),
    ("Detail", ("sharpen",)),
]


class ColorCorrectorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.vars: dict[str, tk.DoubleVar] = {}
        self._preview_job = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.basic_color_corrector"),
            "Exposure, tone, white balance and colour with a live before/after preview.",
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
            looks, text="Quick look:", bg=CLR["bg"], fg=CLR["fg"], font=(UI_FONT, 10, "bold")
        ).pack(side="left")
        self.look_var = tk.StringVar(value="Neutral (reset)")
        box = ttk.Combobox(
            looks, textvariable=self.look_var, values=list(cc.LOOKS), state="readonly", width=22
        )
        box.pack(side="left", padx=px(8))
        box.bind("<<ComboboxSelected>>", lambda _e: self._apply_look(self.look_var.get()))
        tk.Button(
            looks,
            text=t("color_correct.btn_reset_all"),
            relief="flat",
            cursor="hand2",
            command=lambda: self._apply_look("Neutral (reset)"),
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

        enc_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        enc_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(
            enc_lf, codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr")
        )
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            enc_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "graded"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))

        self.btn_render = self.make_render_btn(
            body, t("color_correct.render_grade"), self._render, width=24
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5, bg=CLR["bg"])

    def _reset_slider(self, var, default):
        var.set(default)
        self._schedule_preview()

    def _group(self, parent, title, keys):
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

    def values(self) -> dict:
        return {k: v.get() for k, v in self.vars.items()}

    def _apply_look(self, name):
        merged = dict(cc.DEFAULTS)
        merged.update(cc.LOOKS.get(name, {}))
        for key, var in self.vars.items():
            var.set(merged[key])
        self._schedule_preview()

    def _source_changed(self, path):
        self.compare.load(path)
        self.compare.set_filter(cc.build_chain(self.values()))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "graded"))

    def _schedule_preview(self):
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(150, self._update_preview)

    def _update_preview(self):
        self._preview_job = None
        self.compare.set_filter(cc.build_chain(self.values()))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        chain = cc.build_chain(self.values())
        if not chain:
            self.log(self.console, "No adjustments set - the video will just be re-encoded.")
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
