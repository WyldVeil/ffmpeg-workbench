"""
tabs/cutting/smart_reframe.py  -  Smart Reframe

Turn landscape video into vertical / square (or any aspect) while the crop
window follows the action.

1. Analysis: FFmpeg reduces every frame to a small motion-energy map
   (frame differences at 8 fps); the centre of motion is tracked with a
   dead zone, smoothing and a gentle pull back to the centre, and snaps at
   scene cuts.
2. Render: the crop position is updated on every output frame (sendcmd),
   so the camera glides instead of jumping.

"Fit with blurred background" keeps the whole picture instead.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import reframe as rf
from core.ops.shared import RenderJob, frame_rate, keep_audio_args
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, output_section, scroll_body

RATIOS = {
    "9:16": ("9:16 - Shorts, Reels, TikTok", (9, 16), (1080, 1920)),
    "1:1": ("1:1 - square", (1, 1), (1080, 1080)),
    "4:5": ("4:5 - Instagram portrait", (4, 5), (1080, 1350)),
    "16:9": ("16:9 - widescreen", (16, 9), (1920, 1080)),
    "4:3": ("4:3 - classic", (4, 3), (1440, 1080)),
}
MODES = {
    "follow": "Follow the action (responsive)",
    "smooth": "Smooth drift (calm, centred)",
    "centre": "Fixed centre (no tracking)",
    "fit": "Fit whole picture with blurred background",
}


class SmartReframeTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(
            self,
            t("tab.smart_reframe"),
            "Landscape to vertical or square, with the crop following the action.",
            icon="🎯",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"])
        self.info_lbl.pack(anchor="w", padx=px(24))

        opt = tk.LabelFrame(body, text="  Reframe  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(6))
        self.ratio_var = tk.StringVar(value=RATIOS["9:16"][0])
        self.mode_var = tk.StringVar(value=MODES["follow"])
        r0 = tk.Frame(opt)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text="Shape:", width=12, anchor="w").pack(side="left")
        rb = ttk.Combobox(
            r0,
            textvariable=self.ratio_var,
            values=[v[0] for v in RATIOS.values()],
            state="readonly",
            width=30,
        )
        rb.pack(side="left")
        rb.bind("<<ComboboxSelected>>", lambda _e: self._ratio_changed())
        tk.Label(r0, text="Output size:").pack(side="left", padx=px(12, 2))
        self.ow_var, self.oh_var = tk.StringVar(value="1080"), tk.StringVar(value="1920")
        tk.Entry(r0, textvariable=self.ow_var, width=6).pack(side="left")
        tk.Label(r0, text="×").pack(side="left")
        tk.Entry(r0, textvariable=self.oh_var, width=6).pack(side="left")
        r1 = tk.Frame(opt)
        r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text="Camera:", width=12, anchor="w").pack(side="left")
        ttk.Combobox(
            r1, textvariable=self.mode_var, values=list(MODES.values()), state="readonly", width=40
        ).pack(side="left")
        tk.Label(
            opt,
            text="  ℹ  Tracking analyses the whole clip first (about 10-20× faster than "
            "real time), then renders.",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.src_row.path, "reframed"),
        )
        self.btn_render = self.make_render_btn(body, "🎯  REFRAME", self._render, width=22)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=6)

    def _ratio_key(self) -> str:
        return next((k for k, v in RATIOS.items() if v[0] == self.ratio_var.get()), "9:16")

    def _ratio_changed(self) -> None:
        ow, oh = RATIOS[self._ratio_key()][2]
        self.ow_var.set(str(ow))
        self.oh_var.set(str(oh))

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        if self._info is not None and self._info.has_video:
            w, h = self._info.require_video().display_size
            self.info_lbl.config(text=f"{w}×{h}, {ff.format_time(self._info.duration)}")
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "reframed"))

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            ow = int(common.number(self.ow_var.get(), "Width", 16, 8192)) // 2 * 2
            oh = int(common.number(self.oh_var.get(), "Height", 16, 8192)) // 2 * 2
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "reframed")
        if not out:
            return
        mode = next((k for k, v in MODES.items() if v == self.mode_var.get()), "follow")
        rw, rh = RATIOS[self._ratio_key()][1]
        w, h = common.even_size(info)
        cw, ch = rf.crop_box(w, h, rw, rh)
        dur = info.duration
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        if mode == "fit":
            common.render(
                self,
                RenderJob(
                    src=src, info=info, out=out, chain=rf.fit_chain(ow, oh), name="Smart Reframe"
                ),
                encoder=venc,
                console=self.console,
                btn=self.btn_render,
            )
            return
        job = rf.ReframeJob(
            src=src,
            source=(w, h),
            window=(cw, ch),
            out_size=(ow, oh),
            fps=info.require_video().fps or 30.0,
            rate=frame_rate(info),
            duration=info.duration,
            tuning=None if mode == "centre" else rf.TUNING.get(mode, rf.TUNING["follow"]),
            venc=venc,
            audio=keep_audio_args(info, container),
        )

        self.log(self.console, f"Reframing {w}×{h} → {cw}×{ch} window → {ow}×{oh}")
        common.queue_job(
            self,
            common.Job(name="Smart Reframe", out=out, work=job.run, duration=dur, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
