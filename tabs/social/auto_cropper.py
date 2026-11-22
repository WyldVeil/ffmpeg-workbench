"""
tabs/social/auto_cropper.py  -  Auto-Cropper

Remove black bars (letterboxing / pillarboxing) automatically.  Several
points across the video are sampled with FFmpeg's cropdetect (threshold
relative to the bit depth, so 10-bit HDR works too); the largest picture
area seen is kept so bright scenes never get over-cropped.  Before/after
preview, then render.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox

from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body


def detect_bars(path: str, duration: float, samples: int = 6) -> tuple | None:
    """(x, y, w, h) of the picture area, or None when there are no bars."""
    boxes = []
    for i in range(samples):
        at = duration * (i + 0.5) / samples
        r = ff.capture([ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-ss", ff.ts(at), "-t", "1.5",
                        "-i", path, "-an", "-sn", "-vf", "cropdetect=limit=0.094:round=2:reset=0",
                        "-f", "null", "-"], timeout=60)
        found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr or "")
        if found:
            w, h, x, y = (int(v) for v in found[-1])
            boxes.append((x, y, x + w, y + h))
    if not boxes:
        return None
    # union of what each sample considered picture: never cut into a bright scene
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return x0 // 2 * 2, y0 // 2 * 2, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2


class AutoCropperTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self.box = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.auto_cropper"), "Find and remove black bars automatically.",
                         icon="✂")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var,
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.result_lbl = tk.Label(body, text="", fg=CLR["accent"], font=(UI_FONT, 10, "bold"))
        self.result_lbl.pack(anchor="w", padx=px(24))
        self.compare = CompareView(body, 400, 225, after_label="Cropped")
        self.compare.pack(pady=px(4))
        self.btn_detect = tk.Button(body, text="🔍 Detect again", relief="flat", command=self._detect)
        self.btn_detect.pack()

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "nobars"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text="✂  REMOVE BARS", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", width=22, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self.box = None
        self.compare.load(path)
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "nobars"))
        self._detect()

    def _detect(self):
        path, info = self.src_row.path, self._info
        if not path or info is None or not info.has_video:
            return
        self.result_lbl.config(text="Looking for black bars…")
        self.btn_detect.config(state="disabled")

        def done(box):
            self.btn_detect.config(state="normal")
            w, h = info.video.display_size
            if not box or (box[2] >= w - 2 and box[3] >= h - 2):
                self.box = None
                self.result_lbl.config(text="No black bars found - nothing to crop.")
                self.compare.set_filter("")
                return
            self.box = box
            x, y, bw, bh = box
            self.result_lbl.config(text=f"Picture is {bw}×{bh} at ({x}, {y}) inside {w}×{h}.")
            self.compare.set_filter(f"crop={bw}:{bh}:{x}:{y}")

        from core import ui
        ui.run_in_thread(detect_bars, path, info.duration, on_done=done,
                         on_error=lambda _e: done(None), name="AutoCrop")

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        if not self.box:
            messagebox.showinfo(t("common.warning"), "No black bars were found in this video.", parent=self)
            return
        out = common.output_for(self, self.out_row, src, "nobars")
        if not out:
            return
        x, y, w, h = self.box
        common.render(self, src=src, info=info, out=out, chain=f"crop={w}:{h}:{x}:{y},setsar=1",
                      encoder=self.encoder, console=self.console, btn=self.btn_render,
                      btn_label="✂  REMOVE BARS", name="Auto-Cropper")
