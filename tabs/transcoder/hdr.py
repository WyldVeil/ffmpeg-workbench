"""
tabs/transcoder/hdr.py  -  HDR to SDR

HDR video from phones (iPhone, Pixel, Galaxy), cameras, games and streaming
looks grey and washed out in most editors, browsers, chat apps and on
ordinary screens.  This converts it to normal (SDR, BT.709) video that looks
right everywhere:

* detects HDR10, HDR10+, HLG and Dolby Vision, and the brightness the video
  was mastered for;
* GPU tone mapping (libplacebo: BT.2390 with scene-by-scene brightness, the
  only way to get Dolby Vision profile 5 colours right) or CPU (zscale);
* removes the HDR descriptions from the result so TVs and players don't treat
  it as HDR;
* before/after preview, sound and subtitles kept.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core import hdr
from core.i18n import t
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

ASSUME = {"": "From the file", "pq": "Treat as HDR10 / PQ (tags were lost)", "hlg": "Treat as HLG (tags were lost)"}
TEXT_SUBS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}


def subtitle_args(info: ff.MediaInfo, container: str) -> list:
    """Keep subtitles where the container can hold them (text ones converted for MP4/MOV/WebM)."""
    subs = [s for s in info.streams if s.type == "subtitle"]
    if not subs:
        return []
    if container == "mkv":
        return ["-map", "0:s?", "-c:s", "copy"]
    target = {"mp4": "mov_text", "mov": "mov_text", "m4v": "mov_text", "webm": "webvtt"}.get(container)
    maps = [a for s in subs if s.codec in TEXT_SUBS for a in ("-map", f"0:{s.index}")] if target else []
    return maps + (["-c:s", target] if maps else [])


class HdrToSdrTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._info = None
        self._hdr = hdr.HdrInfo()
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, "HDR to SDR", "Make HDR video from phones, cameras and streaming look right on "
                                            "normal screens.", icon="🌗")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=20, pady=(10, 2))
        self.kind_lbl = tk.Label(body, text="Choose an HDR video - from a phone, camera, game or download.",
                                 fg=CLR["fgdim"], anchor="w", justify="left", font=(UI_FONT, 10))
        self.kind_lbl.pack(fill="x", padx=24, pady=(0, 4))
        self.compare = CompareView(body, 400, 225, before_label="As most players show it", after_label="Converted")
        self.compare.pack(pady=4)

        opt = tk.LabelFrame(body, text="  Conversion  ", padx=12, pady=8)
        opt.pack(fill="x", padx=20, pady=4)
        self.gpu_ok = fl.vulkan_ok()
        self.engine_var = tk.StringVar(value="gpu" if self.gpu_ok else "cpu")
        tk.Radiobutton(opt, text="GPU - best quality, understands Dolby Vision", variable=self.engine_var,
                       value="gpu", command=self._changed,
                       state="normal" if self.gpu_ok else "disabled").grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Radiobutton(opt, text="CPU - works on any computer", variable=self.engine_var, value="cpu",
                       command=self._changed).grid(row=1, column=0, columnspan=2, sticky="w")
        if not self.gpu_ok:
            tk.Label(opt, text="(no Vulkan graphics driver found - GPU mode unavailable)", fg=CLR["fgdim"]).grid(
                row=0, column=2, sticky="w")
        tk.Label(opt, text="Tone curve:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.curve_var = tk.StringVar(value=hdr.CURVES["bt2390"][0])
        cb = ttk.Combobox(opt, textvariable=self.curve_var, values=[v[0] for v in hdr.CURVES.values()],
                          state="readonly", width=38)
        cb.grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.adaptive_var = tk.BooleanVar(value=True)
        self.adaptive_cb = tk.Checkbutton(opt, text="Adapt to each scene's brightness (GPU)",
                                          variable=self.adaptive_var, command=self._changed)
        self.adaptive_cb.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        tk.Label(opt, text="Input:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.assume_var = tk.StringVar(value=ASSUME[""])
        ab = ttk.Combobox(opt, textvariable=self.assume_var, values=list(ASSUME.values()), state="readonly",
                          width=38)
        ab.grid(row=4, column=1, sticky="w", padx=6, pady=(6, 0))
        ab.bind("<<ComboboxSelected>>", lambda _e: self._changed())

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=12, pady=8)
        out_lf.pack(fill="x", padx=20, pady=6)
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores"))
        self.encoder.pack(anchor="w")
        tk.Label(out_lf, text="Tip: HEVC or AV1 with 10-bit gives the smoothest skies and shadows.",
                 fg=CLR["fgdim"]).pack(anchor="w", pady=(2, 0))
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "sdr", ".mp4"))
        self.out_row.pack(fill="x", pady=(8, 0))
        self.btn_render = tk.Button(body, text="🌗  CONVERT TO SDR", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                                    fg="white", width=24, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=8)
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._changed()

    # ── State ─────────────────────────────────────────────────────────
    def load_file(self, path) -> bool:
        self.src_row.set(path)
        self._source_changed(path)
        return True

    def _curve(self) -> str:
        return next((k for k, v in hdr.CURVES.items() if v[0] == self.curve_var.get()), "bt2390")

    def _assume(self) -> str:
        return next((k for k, v in ASSUME.items() if v == self.assume_var.get()), "")

    def _gpu(self) -> bool:
        return self.gpu_ok and self.engine_var.get() == "gpu"

    def chain(self, out_fmt: str = "yuv420p") -> str:
        return hdr.sdr_chain(gpu=self._gpu(), curve=self._curve(), out_fmt=out_fmt,
                             adaptive=bool(self.adaptive_var.get()), assume=self._assume())

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self._hdr = hdr.inspect(self._info, path) if self._info is not None and self._info.has_video \
            else hdr.HdrInfo()
        if self._info is None or not self._info.has_video:
            self.kind_lbl.config(text="Can't read this file as video.", fg=CLR["red"])
        elif self._hdr.is_hdr:
            note = ""
            if self._hdr.needs_gpu:
                note = ("  -  Dolby Vision without an HDR10 layer: use GPU mode, the CPU gets its colours wrong."
                        if self.gpu_ok else "  -  Dolby Vision without an HDR10 layer: colours will be off "
                                            "without a Vulkan graphics driver.")
            self.kind_lbl.config(text=f"✓  {self._hdr.label()}{note}", fg=CLR["orange"] if note else CLR["green"])
        else:
            self.kind_lbl.config(text="This video isn't tagged as HDR - it should already look right. If it "
                                      "looks grey and flat, its HDR tags were lost: set Input below.",
                                 fg=CLR["fgdim"])
        self.compare.load(path)
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "sdr", ".mp4"))
        self._changed()

    def _changed(self):
        self.adaptive_cb.config(state="normal" if self._gpu() else "disabled")
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._refresh)

    def _refresh(self):
        self._job = None
        if self._hdr.is_hdr or self._assume():
            self.compare.set_filter(self.chain())
        else:
            self.compare.set_filter("")

    # ── Render ────────────────────────────────────────────────────────
    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        if not (self._hdr.is_hdr or self._assume()):
            messagebox.showwarning("Not HDR", "This video isn't tagged as HDR, so there's nothing to convert.\n\n"
                                              "If it looks grey and flat, its HDR tags were lost - choose "
                                              "\"Treat as HDR10\" (or HLG) under Input.", parent=self)
            return
        if self._hdr.needs_gpu and not self._gpu():
            if not messagebox.askyesno("Dolby Vision", "This Dolby Vision video has no HDR10 layer, so the CPU "
                                                       "conversion will have wrong colours (purple/green).\n\n"
                                                       "Convert anyway?", parent=self):
                return
        out = common.output_for(self, self.out_row, src, "sdr")
        if not out:
            return
        venc = self.encoder.video_encode(common.container_of(out))
        ten = venc.pix_fmt.endswith(("10le", "p010le", "p010")) if venc.pix_fmt else False
        chain = self.chain("yuv420p10le" if ten else "yuv420p")
        self.log(self.console, f"{self._hdr.label() if self._hdr.is_hdr else 'Assumed HDR'} → SDR "
                               f"({'GPU' if self._gpu() else 'CPU'}, {hdr.CURVES[self._curve()][0]})")
        common.render(self, src=src, info=info, out=out, chain=chain, encoder=venc, console=self.console,
                      btn=self.btn_render, btn_label="🌗  CONVERT TO SDR", name="HDR to SDR",
                      extra_args=subtitle_args(info, common.container_of(out)),
                      pre_input=["-init_hw_device", "vulkan"] if self._gpu() else ())
