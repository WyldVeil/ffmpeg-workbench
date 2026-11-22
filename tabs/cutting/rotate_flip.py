"""
tabs/cutting/rotate_flip.py  -  Rotate & Flip

Turn sideways phone videos the right way up, mirror selfies, and so on.

* Lossless (default for MP4/MOV/MKV): only the orientation flag (display
  matrix) changes - instant, no quality loss, audio and every track kept.
  Players, browsers, phones and editors all honour it.
* Re-encode: the pixels are actually rotated, for software that ignores
  orientation flags.  Uses the chosen encoder; audio is copied.
"""

from __future__ import annotations

import tkinter as tk

from core import ffmpeg as ff
from core.i18n import t
from core.ops.rotate_flip import lossless_args, reencode_chain
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

ROTATIONS = [
    (0, "No rotation"),
    (90, "90° clockwise ↻"),
    (180, "180°"),
    (270, "90° anticlockwise ↺"),
]
LOSSLESS_CONTAINERS = ("mp4", "m4v", "mov", "mkv", "3gp")


class RotateFlipTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.rotate_flip"), t("rotate_flip.subtitle"), icon="🔄")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], font=(UI_FONT, 9))
        self.info_lbl.pack(anchor="w", padx=px(24))

        self.compare = CompareView(body, 360, 240)
        self.compare.pack(pady=px(6))

        opts = tk.LabelFrame(
            body, text=f"  {t('rotate_flip.rotation_section')}  ", padx=px(12), pady=px(8)
        )
        opts.pack(fill="x", padx=px(20), pady=px(4))
        self.rot_var = tk.IntVar(value=90)
        r0 = tk.Frame(opts)
        r0.pack(fill="x")
        for deg, label in ROTATIONS:
            tk.Radiobutton(
                r0,
                text=label,
                value=deg,
                variable=self.rot_var,
                indicatoron=False,
                width=18,
                pady=px(6),
                selectcolor=CLR["accent"],
                cursor="hand2",
                command=self._changed,
            ).pack(side="left", padx=px(3))
        r1 = tk.Frame(opts)
        r1.pack(fill="x", pady=px(8, 0))
        self.hflip_var = tk.BooleanVar(value=False)
        self.vflip_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r1, text="Mirror left ↔ right", variable=self.hflip_var, command=self._changed
        ).pack(side="left", padx=px(4))
        tk.Checkbutton(
            r1, text="Flip upside down ↕", variable=self.vflip_var, command=self._changed
        ).pack(side="left", padx=px(12))

        meth = tk.LabelFrame(body, text="  Method  ", padx=px(12), pady=px(8))
        meth.pack(fill="x", padx=px(20), pady=px(4))
        self.lossless_var = tk.BooleanVar(value=True)
        tk.Radiobutton(
            meth,
            text="Lossless - change the orientation flag only (instant, keeps "
            "quality and all tracks)",
            value=True,
            variable=self.lossless_var,
            command=self._method_changed,
        ).pack(anchor="w")
        tk.Radiobutton(
            meth,
            text="Re-encode - rotate the pixels (for software that ignores orientation)",
            value=False,
            variable=self.lossless_var,
            command=self._method_changed,
        ).pack(anchor="w")
        self.encoder = EncoderPanel(meth, codecs=("h264", "hevc", "av1", "vp9", "prores"))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.out_row = FileRow(
            out_lf,
            t("common.output_file"),
            save=True,
            suggest=lambda: self._suggest(self.src_row.path),
        )
        self.out_row.pack(fill="x")
        self.btn_render = tk.Button(
            body,
            text="🔄  APPLY",
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

    def _suggest(self, src):
        return self.suggest_output(src, "rotated") if src else ""

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        if self._info is not None and self._info.video is not None:
            v = self._info.require_video()
            w, h = v.display_size
            flag = f", orientation flag: {v.rotation}° clockwise" if v.rotation else ""
            self.info_lbl.config(text=f"{w}×{h} as displayed{flag}")
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self._suggest(path))

    def _method_changed(self):
        if self.lossless_var.get():
            self.encoder.pack_forget()
        else:
            self.encoder.pack(anchor="w", pady=px(6, 0))

    def _changed(self):
        self.compare.set_filter(
            reencode_chain(self.rot_var.get(), self.hflip_var.get(), self.vflip_var.get())
        )

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        rot, hf, vf = self.rot_var.get(), self.hflip_var.get(), self.vflip_var.get()
        out = common.output_for(self, self.out_row, src, "rotated")
        if not out:
            return
        if self.lossless_var.get():
            if common.container_of(out) not in LOSSLESS_CONTAINERS:
                self.log_tagged(
                    self.console,
                    "Lossless rotation needs an MP4, MOV or MKV output - re-encoding instead.",
                    "warn",
                )
            else:
                cmd = [
                    ff.ffmpeg_path(),
                    "-y",
                    *lossless_args(info.require_video().rotation, rot, hf, vf),
                    "-i",
                    src,
                    "-map",
                    "0",
                    "-c",
                    "copy",
                    "-map_metadata",
                    "0",
                    out,
                ]
                self.run_ffmpeg(
                    cmd,
                    self.console,
                    on_done=lambda rc: self.show_result(rc, out),
                    btn=self.btn_render,
                    duration=info.duration or None,
                    output=out,
                    inputs=[src],
                    name="Rotate & Flip",
                )
                return
        common.render(
            self,
            src=src,
            info=info,
            out=out,
            chain=reencode_chain(rot, hf, vf),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            name="Rotate & Flip",
        )
