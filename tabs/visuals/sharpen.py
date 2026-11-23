"""
tabs/visuals/sharpen.py  -  Sharpen

Bring back detail with a before/after view:

* Smart sharpen (CAS) - AMD's contrast-adaptive sharpening: sharpens soft
  areas more than already-sharp edges, so it rarely halos.  Recommended.
* Unsharp mask - the classic, with radius and amount.
* Clarity - large-radius local contrast ("pop") without sharpening noise.
"""

from __future__ import annotations

import tkinter as tk
from functools import partial
from tkinter import ttk

from core.i18n import t
from core.ops.shared import RenderJob
from core.ops.sharpen import sharpen_chain
from core.scaling import px
from tabs import common
from tabs.base_tab import BaseTab
from tabs.widgets import CompareView, FileRow, output_section, scroll_body

METHODS = {
    "cas": "Smart sharpen (CAS) - recommended",
    "unsharp": "Unsharp mask (classic)",
    "clarity": "Clarity (local contrast)",
}
PRESETS = {"Subtle": 0.25, "Light": 0.45, "Medium": 0.65, "Strong": 0.85}


class SharpenTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._job: str | None = None
        self._build_ui()

    def _preset(self, value: float) -> None:
        self.amount_var.set(value)
        self._changed()

    def _build_ui(self) -> None:
        self.make_header(
            self, t("tab.sharpen"), "Bring back detail in soft or upscaled video.", icon="🔪"
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.compare = CompareView(body, 400, 225, after_label="Sharpened")
        self.compare.pack(pady=px(4))
        opt = tk.LabelFrame(body, text="  Sharpen  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.method_var = tk.StringVar(value=METHODS["cas"])
        mb = ttk.Combobox(
            opt,
            textvariable=self.method_var,
            values=list(METHODS.values()),
            state="readonly",
            width=36,
        )
        mb.grid(row=0, column=0, columnspan=3, sticky="w")
        mb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.amount_var = tk.DoubleVar(value=PRESETS["Light"])
        tk.Scale(
            opt,
            variable=self.amount_var,
            from_=0.05,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=px(240),
            label="Amount",
            command=lambda _v: self._changed(),
        ).grid(row=1, column=0, sticky="w")
        self.radius_var = tk.IntVar(value=5)
        self.radius_scale = tk.Scale(
            opt,
            variable=self.radius_var,
            from_=3,
            to=13,
            resolution=2,
            orient="horizontal",
            length=px(160),
            label="Radius (unsharp)",
            command=lambda _v: self._changed(),
        )
        self.radius_scale.grid(row=1, column=1, sticky="w", padx=px(10))
        pf = tk.Frame(opt)
        pf.grid(row=2, column=0, columnspan=3, sticky="w", pady=px(4))
        for name, val in PRESETS.items():
            tk.Button(
                pf,
                text=name,
                relief="flat",
                command=partial(self._preset, val),
            ).pack(side="left", padx=px(2))
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores"),
            suggest=lambda: self.suggest_output(self.src_row.path, "sharp"),
        )
        self.btn_render = self.make_render_btn(body, "🔪  SHARPEN", self._render, width=22)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)
        self._changed()

    def _method(self) -> str:
        return next((k for k, v in METHODS.items() if v == self.method_var.get()), "cas")

    def _chain(self) -> str:
        return sharpen_chain(
            self._method(), float(self.amount_var.get()), int(self.radius_var.get())
        )

    def _source_changed(self, path: str) -> None:
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "sharp"))

    def _show_filter(self) -> None:
        self._job = None
        self.compare.set_filter(self._chain())

    def _changed(self) -> None:
        self.radius_scale.config(state="normal" if self._method() == "unsharp" else "disabled")
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._show_filter)

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        out = common.output_for(self, self.out_row, src, "sharp")
        if not out:
            return
        common.render(
            self,
            RenderJob(src=src, info=info, out=out, chain=self._chain(), name="Sharpen"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
