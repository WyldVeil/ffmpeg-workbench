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
from tkinter import ttk

from core.i18n import t
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

METHODS = {"cas": "Smart sharpen (CAS) - recommended", "unsharp": "Unsharp mask (classic)",
           "clarity": "Clarity (local contrast)"}
PRESETS = {"Subtle": 0.25, "Light": 0.45, "Medium": 0.65, "Strong": 0.85}


def sharpen_chain(method: str, amount: float, radius: int = 5) -> str:
    """*amount* 0..1, *radius* odd 3..13 (unsharp only)."""
    a = max(0.0, min(1.0, amount))
    if method == "unsharp":
        r = max(3, min(13, int(radius) | 1))
        return f"unsharp={r}:{r}:{a * 2.5:.2f}:{r}:{r}:0"
    if method == "clarity":
        return f"unsharp=13:13:{a * 1.2:.2f}:5:5:0"
    return f"cas=strength={a:.2f}"


class SharpenTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.sharpen"), "Bring back detail in soft or upscaled video.", icon="🔪")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var, on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=20, pady=(10, 4))
        self.compare = CompareView(body, 400, 225, after_label="Sharpened")
        self.compare.pack(pady=4)
        opt = tk.LabelFrame(body, text="  Sharpen  ", padx=12, pady=8)
        opt.pack(fill="x", padx=20, pady=4)
        self.method_var = tk.StringVar(value=METHODS["cas"])
        mb = ttk.Combobox(opt, textvariable=self.method_var, values=list(METHODS.values()), state="readonly",
                          width=36)
        mb.grid(row=0, column=0, columnspan=3, sticky="w")
        mb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.amount_var = tk.DoubleVar(value=PRESETS["Light"])
        tk.Scale(opt, variable=self.amount_var, from_=0.05, to=1.0, resolution=0.05, orient="horizontal",
                 length=240, label="Amount", command=lambda _v: self._changed()).grid(row=1, column=0, sticky="w")
        self.radius_var = tk.IntVar(value=5)
        self.radius_scale = tk.Scale(opt, variable=self.radius_var, from_=3, to=13, resolution=2,
                                     orient="horizontal", length=160, label="Radius (unsharp)",
                                     command=lambda _v: self._changed())
        self.radius_scale.grid(row=1, column=1, sticky="w", padx=10)
        pf = tk.Frame(opt)
        pf.grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        for name, val in PRESETS.items():
            tk.Button(pf, text=name, relief="flat",
                      command=lambda v=val: (self.amount_var.set(v), self._changed())).pack(side="left", padx=2)
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=12, pady=8)
        out_lf.pack(fill="x", padx=20, pady=6)
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "sharp"))
        self.out_row.pack(fill="x", pady=(8, 0))
        self.btn_render = tk.Button(body, text="🔪  SHARPEN", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", width=22, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=8)
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._changed()

    def _method(self):
        return next((k for k, v in METHODS.items() if v == self.method_var.get()), "cas")

    def _chain(self):
        return sharpen_chain(self._method(), float(self.amount_var.get()), int(self.radius_var.get()))

    def _source_changed(self, path):
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "sharp"))

    def _changed(self):
        self.radius_scale.config(state="normal" if self._method() == "unsharp" else "disabled")
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, lambda: (setattr(self, "_job", None), self.compare.set_filter(self._chain())))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        out = common.output_for(self, self.out_row, src, "sharp")
        if not out:
            return
        common.render(self, src=src, info=info, out=out, chain=self._chain(), encoder=self.encoder,
                      console=self.console, btn=self.btn_render, btn_label="🔪  SHARPEN", name="Sharpen")
