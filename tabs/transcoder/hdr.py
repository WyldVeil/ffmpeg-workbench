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
from functools import partial
from tkinter import messagebox, ttk

from core import capabilities, hdr, tasks
from core import ffmpeg as ff
from core.i18n import t
from core.ops.hdr_args import subtitle_args
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, FileRow, output_section, scroll_body

ASSUME = {
    "": "From the file",
    "pq": "Treat as HDR10 / PQ (tags were lost)",
    "hlg": "Treat as HLG (tags were lost)",
}


_NO_GPU = "(no Vulkan graphics driver found - GPU mode unavailable)"


def _inspect(path: str) -> tuple[ff.MediaInfo | None, hdr.HdrInfo]:
    """Worker: what kind of HDR (if any) *path* holds."""
    info = ff.try_probe(path)
    found = hdr.inspect(info, path) if info is not None and info.has_video else hdr.HdrInfo()
    return info, found


class HdrToSdrTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._info = None
        self._inspecting: str | None = None  # the file being read in the background
        self._hdr = hdr.HdrInfo()
        self._job = None
        self._build_ui()

    def _build_ui(self):
        body, known, opt = self._build_header()
        self._build_gpu_note(known, opt)
        self._build_output(body, opt)
        self._build_console(body)

    def _build_header(self):
        self.make_header(
            self,
            "HDR to SDR",
            "Make HDR video from phones, cameras and streaming look right on normal screens.",
            icon="🌗",
        )
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.kind_lbl = tk.Label(
            body,
            text="Choose an HDR video - from a phone, camera, game or download.",
            fg=CLR["fgdim"],
            anchor="w",
            justify="left",
            font=(UI_FONT, 10),
        )
        self.kind_lbl.pack(fill="x", padx=px(24), pady=px(0, 4))
        self.compare = CompareView(
            body, 400, 225, before_label="As most players show it", after_label="Converted"
        )
        self.compare.pack(pady=px(4))

        opt = tk.LabelFrame(body, text="  Conversion  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        known = capabilities.peek("vulkan")  # None: still being checked (never block the window)
        self.gpu_ok = bool(known)
        self.engine_var = tk.StringVar(value="gpu" if self.gpu_ok else "cpu")
        self.gpu_radio = tk.Radiobutton(
            opt,
            text="GPU - best quality, understands Dolby Vision",
            variable=self.engine_var,
            value="gpu",
            command=self._changed,
            state="normal" if self.gpu_ok else "disabled",
        )
        return body, known, opt

    def _build_gpu_note(self, known, opt):
        self.gpu_radio.grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Radiobutton(
            opt,
            text="CPU - works on any computer",
            variable=self.engine_var,
            value="cpu",
            command=self._changed,
        ).grid(row=1, column=0, columnspan=2, sticky="w")
        self.gpu_note = tk.Label(opt, text="", fg=CLR["fgdim"])
        self.gpu_note.grid(row=0, column=2, sticky="w")
        if known is None:
            self.gpu_note.config(text="(checking the graphics driver...)")
            capabilities.on_ready("vulkan", self._gpu_known)
        elif not known:
            self.gpu_note.config(text=_NO_GPU)
        self.curve_var = tk.StringVar(value=hdr.CURVES["bt2390"][0])
        cb = ttk.Combobox(
            opt,
            textvariable=self.curve_var,
            values=[v[0] for v in hdr.CURVES.values()],
            state="readonly",
            width=38,
        )
        self.form_row(opt, 2, "Tone curve:", cb, pady=(8, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.adaptive_var = tk.BooleanVar(value=True)
        self.adaptive_cb = tk.Checkbutton(
            opt,
            text="Adapt to each scene's brightness (GPU)",
            variable=self.adaptive_var,
            command=self._changed,
        )
        self.adaptive_cb.grid(row=3, column=0, columnspan=2, sticky="w", pady=px(4, 0))
        self.assume_var = tk.StringVar(value=ASSUME[""])

    def _build_output(self, body, opt):
        ab = ttk.Combobox(
            opt,
            textvariable=self.assume_var,
            values=list(ASSUME.values()),
            state="readonly",
            width=38,
        )
        self.form_row(opt, 4, "Input:", ab, pady=(6, 0))
        ab.bind("<<ComboboxSelected>>", lambda _e: self._changed())

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores"),
            suggest=lambda: self.suggest_output(self.src_row.path, "sdr", ".mp4"),
        )
        tk.Label(
            self.out_row.master,
            text="Tip: HEVC or AV1 with 10-bit gives the smoothest skies and shadows.",
            fg=CLR["fgdim"],
        ).pack(anchor="w", pady=px(2, 0), before=self.out_row)
        self.btn_render = self.make_render_btn(body, "🌗  CONVERT TO SDR", self._render, width=24)
        self.btn_render.pack(pady=px(8))

    def _build_console(self, body):
        self.console = self.make_console_block(body, height=5)
        self._changed()

    def _gpu_known(self, ok: bool) -> None:
        self.gpu_ok = bool(ok)
        self.gpu_radio.config(state="normal" if ok else "disabled")
        self.gpu_note.config(text="" if ok else _NO_GPU)
        if ok:
            self.engine_var.set("gpu")  # the default whenever the GPU works
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
        return hdr.sdr_chain(
            gpu=self._gpu(),
            curve=self._curve(),
            out_fmt=out_fmt,
            adaptive=bool(self.adaptive_var.get()),
            assume=self._assume(),
        )

    def _source_changed(self, path):
        self._info, self._hdr, self._inspecting = (
            None,
            hdr.HdrInfo(),
            path,
        )  # nothing stale meanwhile
        self.kind_lbl.config(text="Reading the file...", fg=CLR["fgdim"])
        tasks.submit(
            partial(_inspect, path),
            on_done=lambda res: self._inspected(path, *res),
            name="HdrInspect",
        )

    def _inspected(self, path: str, info: ff.MediaInfo | None, found: hdr.HdrInfo) -> None:
        if path != self.src_row.path:  # another file was chosen meanwhile
            return
        self._info, self._hdr, self._inspecting = info, found, None
        if self._info is None or not self._info.has_video:
            self.kind_lbl.config(text="Can't read this file as video.", fg=CLR["red"])
        elif self._hdr.is_hdr:
            note = ""
            if self._hdr.needs_gpu:
                note = (
                    "  -  Dolby Vision without an HDR10 layer: use GPU mode, the CPU gets its colours wrong."
                    if self.gpu_ok
                    else "  -  Dolby Vision without an HDR10 layer: colours will be off "
                    "without a Vulkan graphics driver."
                )
            self.kind_lbl.config(
                text=f"✓  {self._hdr.label()}{note}", fg=CLR["orange"] if note else CLR["green"]
            )
        else:
            self.kind_lbl.config(
                text="This video isn't tagged as HDR - it should already look right. If it "
                "looks grey and flat, its HDR tags were lost: set Input below.",
                fg=CLR["fgdim"],
            )
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
        if self._inspecting:
            messagebox.showinfo(
                "HDR to SDR", "Still reading the file - try again in a moment.", parent=self
            )
            return
        info = common.check_source(self, src)
        if info is None:
            return
        if not (self._hdr.is_hdr or self._assume()):
            messagebox.showwarning(
                "Not HDR",
                "This video isn't tagged as HDR, so there's nothing to convert.\n\n"
                "If it looks grey and flat, its HDR tags were lost - choose "
                '"Treat as HDR10" (or HLG) under Input.',
                parent=self,
            )
            return
        if self._hdr.needs_gpu and not self._gpu():
            if not messagebox.askyesno(
                "Dolby Vision",
                "This Dolby Vision video has no HDR10 layer, so the CPU "
                "conversion will have wrong colours (purple/green).\n\n"
                "Convert anyway?",
                parent=self,
            ):
                return
        out = common.output_for(self, self.out_row, src, "sdr")
        if not out:
            return
        venc = self.encoder.video_encode(common.container_of(out))
        ten = venc.pix_fmt.endswith(("10le", "p010le", "p010")) if venc.pix_fmt else False
        chain = self.chain("yuv420p10le" if ten else "yuv420p")
        self.log(
            self.console,
            f"{self._hdr.label() if self._hdr.is_hdr else 'Assumed HDR'} → SDR "
            f"({'GPU' if self._gpu() else 'CPU'}, {hdr.CURVES[self._curve()][0]})",
        )
        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                chain=chain,
                name="HDR to SDR",
                extra_args=subtitle_args(info, common.container_of(out)),
                pre_input=ff.VULKAN_DEVICE if self._gpu() else (),
            ),
            encoder=venc,
            console=self.console,
            btn=self.btn_render,
        )
