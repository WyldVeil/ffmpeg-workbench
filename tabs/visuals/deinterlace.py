"""
tabs/visuals/deinterlace.py  -  Deinterlace

Turn interlaced video (TV, camcorder, DVD) into clean progressive frames:

* Smart - finds the frames that are actually interlaced (idet) and fixes
  only those; progressive frames pass through untouched.  Recommended.
* Every frame - deinterlace everything (for files with wrong flags).
* Double rate - one frame per field: 25i -> 50p / 29.97i -> 59.94p, the
  smoothest motion, like the original broadcast.
* Undo telecine - film transferred to 29.97 fps (3:2 pulldown) back to its
  original 23.976 progressive frames.

The field order is read from the file; it can be forced when the file lies.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops.deinterlace import deinterlace_chain
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

MODES = {"smart": "Smart - only frames that are interlaced (recommended)", "all": "Every frame",
         "double": "Double frame rate (smoothest motion)", "ivtc": "Undo telecine (film at 29.97 → 23.976)"}
ENGINES = {"bwdif": "BWDIF (best)", "yadif": "YADIF (classic)", "w3fdif": "W3FDIF (BBC)",
           "estdif": "ESTDIF (edge-directed)"}
ORDERS = {"auto": "From the file", "tff": "Top field first", "bff": "Bottom field first"}


class DeinterlaceTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.deinterlace"), "Remove the comb lines from interlaced video.", icon="〰")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var, on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], anchor="w")
        self.info_lbl.pack(fill="x", padx=px(20))
        self.compare = CompareView(body, 400, 225, after_label="Deinterlaced")
        self.compare.pack(pady=px(4))
        opt = tk.LabelFrame(body, text="  Deinterlace  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))

        def row(r, label, var, values, width):
            tk.Label(opt, text=label, width=12, anchor="w").grid(row=r, column=0, sticky="w", pady=px(3))
            cb = ttk.Combobox(opt, textvariable=var, values=values, state="readonly", width=width)
            cb.grid(row=r, column=1, sticky="w")
            cb.bind("<<ComboboxSelected>>", lambda _e: self._changed())

        self.mode_var = tk.StringVar(value=MODES["smart"])
        row(0, "Mode:", self.mode_var, list(MODES.values()), 46)
        self.engine_var = tk.StringVar(value=ENGINES["bwdif"])
        row(1, "Method:", self.engine_var, list(ENGINES.values()), 24)
        self.order_var = tk.StringVar(value=ORDERS["auto"])
        row(2, "Field order:", self.order_var, list(ORDERS.values()), 24)
        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "progressive"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text="〰  DEINTERLACE", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", width=22, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _keys(self):
        mode = next((k for k, v in MODES.items() if v == self.mode_var.get()), "smart")
        engine = next((k for k, v in ENGINES.items() if v == self.engine_var.get()), "bwdif")
        order = next((k for k, v in ORDERS.items() if v == self.order_var.get()), "auto")
        return mode, engine, order

    def _chain(self):
        return deinterlace_chain(*self._keys())

    def _source_changed(self, path):
        self._info = info = ff.try_probe(path)
        if info is not None and info.has_video:
            fo = info.require_video().field_order or "unknown"
            label = {"progressive": "progressive", "tt": "interlaced, top field first",
                     "bb": "interlaced, bottom field first", "tb": "interlaced (top coded first)",
                     "bt": "interlaced (bottom coded first)"}.get(fo, fo)
            self.info_lbl.config(text=f"{info.require_video().width}×{info.require_video().height} · {info.require_video().fps:.3f} fps · "
                                      f"the file says: {label}")
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "progressive"))

    def _changed(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, lambda: (setattr(self, "_job", None), self.compare.set_filter(self._chain())))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        out = common.output_for(self, self.out_row, src, "progressive")
        if not out:
            return
        self.log(self.console, self._chain())
        common.render(self, src=src, info=info, out=out, chain=self._chain(), encoder=self.encoder,
                      console=self.console, btn=self.btn_render, btn_label="〰  DEINTERLACE", name="Deinterlace")
