"""
tabs/social/auto_cropper.py  -  Auto-Cropper

Remove black bars (letterboxing / pillarboxing) automatically.  Several
points across the video are sampled with FFmpeg's cropdetect (threshold
relative to the bit depth, so 10-bit HDR works too); the largest picture
area seen is kept so bright scenes never get over-cropped.  Before/after
preview, then render.
"""

from __future__ import annotations

import tkinter as tk
from functools import partial
from tkinter import messagebox

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops.auto_cropper import detect_bars
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, SaveRow, scroll_body


class AutoCropperTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self.box: tuple[int, int, int, int] | None = None  # the picture inside the bars: x, y, w, h
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self, t("tab.auto_cropper"), "Find and remove black bars automatically.", icon="✂"
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.result_lbl = tk.Label(body, text="", fg=CLR["accent"], font=(UI_FONT, 10, "bold"))
        self.result_lbl.pack(anchor="w", padx=px(24))
        self.compare = CompareView(body, 400, 225, after_label="Cropped")
        self.compare.pack(pady=px(4))
        # Before: the bars that go are shaded and the picture that stays is outlined.
        # Cropped: drawn on the page colour, not black, so the frame is visibly
        # shorter than before (on black it looked just like the bars).
        self.compare.view_before.after_draw = self._draw_bars
        self.compare.view_after.after_draw = self._outline_result
        self.compare.view_after.canvas.configure(bg=CLR["bg"])
        self.btn_detect = tk.Button(
            body, text="🔍 Detect again", relief="flat", command=self._detect
        )
        self.btn_detect.pack()

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "nobars"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="✂  REMOVE BARS",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=22,
            cursor="hand2",
            command=self._render,
        )
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
            w, h = info.require_video().display_size
            if not box or (box[2] >= w - 2 and box[3] >= h - 2):
                self.box = None
                self.result_lbl.config(text="No black bars found - nothing to crop.")
                self.compare.set_filter("")
                self._draw_bars()
                return
            self.box = box
            x, y, bw, bh = box
            self.result_lbl.config(text=f"Picture is {bw}×{bh} at ({x}, {y}) inside {w}×{h}.")
            self.compare.set_filter(f"crop={bw}:{bh}:{x}:{y}")
            self._draw_bars()

        tasks.submit(
            partial(detect_bars, path, info.duration),
            on_done=done,
            on_error=lambda _e: done(None),
            name="AutoCrop",
        )

    def _draw_bars(self):
        view = self.compare.view_before
        c = view.canvas
        c.delete("bars")
        shown = view.image_box()
        if shown is None or self.box is None or self._info is None or not self._info.has_video:
            return
        ix, iy, iw, ih = shown
        vw, vh = self._info.require_video().display_size
        sx, sy = iw / max(1, vw), ih / max(1, vh)
        x, y, bw, bh = self.box
        kx0, ky0, kx1, ky1 = ix + x * sx, iy + y * sy, ix + (x + bw) * sx, iy + (y + bh) * sy
        for box in (
            (ix, iy, ix + iw, ky0),
            (ix, ky1, ix + iw, iy + ih),
            (ix, ky0, kx0, ky1),
            (kx1, ky0, ix + iw, ky1),
        ):
            if box[2] - box[0] >= 2 and box[3] - box[1] >= 2:
                c.create_rectangle(*box, fill=CLR["red"], stipple="gray25", outline="", tags="bars")
                if box[3] - box[1] >= px(14) and box[2] - box[0] >= px(90):
                    c.create_text(
                        (box[0] + box[2]) / 2,
                        (box[1] + box[3]) / 2,
                        text="removed",
                        fill="#FFFFFF",
                        font=(UI_FONT, 8, "bold"),
                        tags="bars",
                    )
        c.create_rectangle(
            kx0, ky0, kx1, ky1, outline=CLR["accent"], width=px(2), dash=px(4, 3), tags="bars"
        )

    def _outline_result(self):
        view = self.compare.view_after
        view.canvas.delete("edge")
        shown = view.image_box()
        if shown is not None:
            x, y, w, h = shown
            view.canvas.create_rectangle(
                x, y, x + w, y + h, outline=CLR["accent"], width=px(1), tags="edge"
            )

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        if not self.box:
            messagebox.showinfo(
                t("common.warning"), "No black bars were found in this video.", parent=self
            )
            return
        out = common.output_for(self, self.out_row, src, "nobars")
        if not out:
            return
        x, y, w, h = self.box
        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                chain=f"crop={w}:{h}:{x}:{y},setsar=1",
                name="Auto-Cropper",
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
