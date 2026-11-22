"""
tabs/visuals/green_key.py  -  Green Screen Keyer

Remove a green/blue (or any colour, or white/black) background and put the
subject over a video, an image, a solid colour - or keep it transparent
(ProRes 4444 .mov or VP9 .webm with alpha).

* The key colour is sampled automatically from the frame edges, and can be
  picked with the eyedropper by clicking the "Before" preview.
* Chroma keys add colour-spill suppression (despill).
* The background is scaled to cover the frame without distortion and runs
  at the foreground's frame rate; the result lasts as long as the
  foreground.
"""

from __future__ import annotations

import os
import statistics
import tkinter as tk
from functools import partial
from tkinter import colorchooser, messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core import tasks
from core.i18n import t
from core.ops.shared import RenderJob, frame_rate, keep_audio_args
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, SaveRow, scroll_body

METHODS = {
    "chroma": "Chroma key (green / blue screens)",
    "color": "Colour key (any colour)",
    "luma_white": "Luma key - remove white",
    "luma_black": "Luma key - remove black",
}
BACKGROUNDS = {
    "file": "Video or image file",
    "color": "Solid colour",
    "alpha": "Transparent (keep alpha channel)",
}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".avif")


def hex_colour(value: str) -> str:
    v = value.strip().lstrip("#").upper()
    if len(v) != 6 or any(c not in "0123456789ABCDEF" for c in v):
        raise ValueError(f"Not a colour: {value}")
    return v


def spill_type(hex_rgb: str) -> str | None:
    r, g, b = (int(hex_rgb[i : i + 2], 16) for i in (0, 2, 4))
    if g > r and g >= b:
        return "green"
    if b > r and b > g:
        return "blue"
    return None


def key_chain(method: str, colour: str, similarity: float, blend: float, despill: bool) -> str:
    """Filter chain that makes the background transparent (yuva output)."""
    if method == "luma_white":
        return (
            f"format=yuva420p,lumakey=threshold=1:tolerance={similarity:.3f}:softness={blend:.3f}"
        )
    if method == "luma_black":
        return (
            f"format=yuva420p,lumakey=threshold=0:tolerance={similarity:.3f}:softness={blend:.3f}"
        )
    if method == "color":
        return f"colorkey=color=0x{colour}:similarity={similarity:.3f}:blend={blend:.3f}"
    chain = f"chromakey=color=0x{colour}:similarity={similarity:.3f}:blend={blend:.3f}"
    kind = spill_type(colour)
    if despill and kind:
        chain += f",despill=type={kind}:mix=0.5:expand=0.1"
    return chain


def background_chain(width: int, height: int, fps: str) -> str:
    """Cover-fit a background stream to WxH at *fps* (no distortion)."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps}"
    )


def sample_border_colour(path: str, at: float = 0.5) -> str | None:
    """Median colour of the frame edges (where a backdrop usually is)."""
    w, h = 64, 36
    r = ff.capture(
        [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            ff.ts(at),
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            f"scale={w}:{h}:flags=area,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=30,
        text=False,
    )
    data = r.stdout or b""
    if r.returncode != 0 or len(data) != w * h * 3:
        return None
    px = [
        data[(y * w + x) * 3 : (y * w + x) * 3 + 3]
        for y in range(h)
        for x in range(w)
        if x < 3 or x >= w - 3 or y < 3 or y >= h - 3
    ]
    med = [int(statistics.median(p[i] for p in px)) for i in range(3)]
    return "".join(f"{c:02X}" for c in med)


def sample_point(path: str, at: float, x: int, y: int) -> str | None:
    """Average colour of a 5x5 patch at source pixel (x, y)."""
    r = ff.capture(
        [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            ff.ts(at),
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            f"crop=5:5:{max(0, x - 2)}:{max(0, y - 2)},scale=1:1:flags=area,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=30,
        text=False,
    )
    data = r.stdout or b""
    if r.returncode != 0 or len(data) != 3:
        return None
    return "".join(f"{c:02X}" for c in data)


class GreenKeyerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.key_colour = "00B140"  # typical chroma green until sampled
        self._info = None
        self._job = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        body, opts = self._build_header()
        out_lf = self._build_output(body, opts)
        self._build_encoder(body, out_lf)

    def _build_header(self):
        self.make_header(self, t("tab.green_screen_keyer"), t("green_key.subtitle"), icon="🟩")
        body = scroll_body(self)

        pick = tk.LabelFrame(body, text=f"  {t('section.input_files')}  ", padx=px(12), pady=px(8))
        pick.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.src_row = FileRow(
            pick, t("green_key.foreground_label"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", pady=px(2))
        brow = tk.Frame(pick)
        brow.pack(fill="x", pady=px(2))
        tk.Label(brow, text="Background:", width=16, anchor="w", font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self.bg_mode = tk.StringVar(value=BACKGROUNDS["file"])
        bm = ttk.Combobox(
            brow,
            textvariable=self.bg_mode,
            values=list(BACKGROUNDS.values()),
            state="readonly",
            width=32,
        )
        bm.pack(side="left", padx=px(6))
        bm.bind("<<ComboboxSelected>>", lambda _e: self._bg_mode_changed())
        self.bg_colour = "202020"
        self.bg_swatch = tk.Button(
            brow,
            text="  Colour…  ",
            bg="#202020",
            fg="white",
            relief="flat",
            command=self._pick_bg_colour,
        )
        self.bg_row = FileRow(
            pick,
            t("green_key.background_label"),
            kind="visual",
            on_change=lambda _p: self._schedule(),
        )
        self.bg_row.pack(fill="x", pady=px(2))
        self.loop_var = tk.BooleanVar(value=True)
        self.loop_chk = tk.Checkbutton(
            pick, text=t("green_key.loop_bg_checkbox"), variable=self.loop_var
        )
        self.loop_chk.pack(anchor="w")

        self.compare = CompareView(
            body,
            400,
            225,
            before_label="Before - click to pick the key colour",
            after_label="Result",
        )
        self.compare.pack(pady=px(6))
        self.compare.view_before.canvas.bind("<Button-1>", self._eyedropper)
        self.compare.view_before.canvas.config(cursor="crosshair")

        opts = tk.LabelFrame(
            body, text=f"  {t('green_key.key_options_section')}  ", padx=px(12), pady=px(8)
        )
        opts.pack(fill="x", padx=px(20), pady=px(4))
        return body, opts

    def _build_output(self, body, opts):
        r0 = tk.Frame(opts)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text="Method:", font=(UI_FONT, 10, "bold")).pack(side="left")
        self.method_var = tk.StringVar(value=METHODS["chroma"])
        mb = ttk.Combobox(
            r0,
            textvariable=self.method_var,
            values=list(METHODS.values()),
            state="readonly",
            width=34,
        )
        mb.pack(side="left", padx=px(6))
        mb.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(r0, text=t("green_key.key_colour_label"), font=(UI_FONT, 10, "bold")).pack(
            side="left", padx=px(16, 4)
        )
        self.swatch = tk.Button(
            r0,
            text=f"  #{self.key_colour}  ",
            bg=f"#{self.key_colour}",
            fg="black",
            relief="flat",
            command=self._pick_key_colour,
        )
        self.swatch.pack(side="left")
        tk.Button(
            r0,
            text="Auto-detect",
            relief="flat",
            cursor="hand2",
            command=lambda: self._auto_sample(force=True),
        ).pack(side="left", padx=px(6))

        self.sim_var = tk.DoubleVar(value=0.12)
        self.blend_var = tk.DoubleVar(value=0.08)
        for label, var, hi, hint in (
            (t("green_key.similarity_label"), self.sim_var, 0.6, t("green_key.similarity_hint")),
            (t("green_key.blend_label"), self.blend_var, 0.5, t("green_key.blend_hint")),
        ):
            row = tk.Frame(opts)
            row.pack(fill="x", pady=px(2))
            tk.Label(row, text=label, width=14, anchor="w").pack(side="left")
            tk.Scale(
                row,
                variable=var,
                from_=0.0,
                to=hi,
                resolution=0.005,
                orient="horizontal",
                length=px(260),
                command=lambda _v: self._schedule(),
            ).pack(side="left", padx=px(6))
            tk.Label(row, text=hint, fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(side="left")
        self.despill_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opts,
            text="Remove colour spill from the subject (green/blue screens)",
            variable=self.despill_var,
            command=self._schedule,
        ).pack(anchor="w")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        return out_lf

    def _build_encoder(self, body, out_lf):
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(
            out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr")
        )
        self.encoder.pack(anchor="w")
        self.alpha_note = tk.Label(
            out_lf,
            text="Transparent output: save as .mov (ProRes 4444) or .webm (VP9 with alpha).",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        )
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self._suggest(self.src_row.path),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))

        self.btn_render = tk.Button(
            body,
            text=t("green_key.composite_button"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=28,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── Settings → filters ───────────────────────────────────────────────────

    def method(self) -> str:
        label = self.method_var.get()
        return next((k for k, v in METHODS.items() if v == label), "chroma")

    def bg_kind(self) -> str:
        label = self.bg_mode.get()
        return next((k for k, v in BACKGROUNDS.items() if v == label), "file")

    def keyer(self) -> str:
        return key_chain(
            self.method(),
            self.key_colour,
            self.sim_var.get(),
            self.blend_var.get(),
            self.despill_var.get(),
        )

    def preview_chain(self) -> str:
        info = self._info
        if info is None or not info.has_video:
            return ""
        w, h = common.even_size(info)
        kind = self.bg_kind()
        src = ""
        if kind == "file":
            bg = self.bg_row.path
            if not bg or not os.path.isfile(bg):
                kind = "alpha"
            else:
                src = f"{fl.preview_movie(bg)}{background_chain(w, h, frame_rate(info))}[bg]"
        if kind == "color":
            src = f"color=c=0x{self.bg_colour}:s={w}x{h}[bg]"
        if kind == "alpha":  # checkerboard so transparency is visible
            src = (
                f"nullsrc=s={w}x{h},geq=lum='if(mod(floor(X/24)+floor(Y/24),2),200,140)'"
                f":cb=128:cr=128[bg]"
            )
        return f"{src};[in]{self.keyer()}[fg];[bg][fg]overlay=format=auto:shortest=1"

    def _suggest(self, src):
        return self.suggest_output(src, "keyed", ".mov" if self.bg_kind() == "alpha" else ".mp4")

    # ── Behaviour ────────────────────────────────────────────────────────────

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self.compare.load(path)
        self._auto_sample()
        if not self.out_row.path:
            self.out_row.set(self._suggest(path))

    def _auto_sample(self, force=False):
        path = self.src_row.path
        if not path or not os.path.isfile(path):
            return
        at = (self._info.duration * 0.1) if self._info else 0.0

        def done(colour):
            if colour and (force or spill_type(colour)):
                self._set_key(colour)
                self.log(self.console, f"Key colour sampled from the frame edges: #{colour}")
            elif force:
                self.log(
                    self.console,
                    "Could not detect a backdrop colour - pick one with "
                    "the eyedropper (click the Before image).",
                )
            self._schedule()

        tasks.submit(partial(sample_border_colour, path, at), on_done=done, name="KeySample")

    def _eyedropper(self, event):
        info, path = self._info, self.src_row.path
        if info is None or not info.has_video or not path:
            return
        vw, vh = info.require_video().display_size
        pw, ph = 400, 225
        scale = min(pw / vw, ph / vh)
        iw, ih = vw * scale, vh * scale
        x = int((event.x - (pw - iw) / 2) / scale)
        y = int((event.y - (ph - ih) / 2) / scale)
        if not (0 <= x < vw and 0 <= y < vh):
            return
        x, y = min(max(x, 2), vw - 3), min(max(y, 2), vh - 3)

        def done(colour):
            if colour:
                self._set_key(colour)
                self._schedule()

        tasks.submit(
            partial(sample_point, path, self.compare.position, x, y),
            on_done=done,
            name="KeyEyedropper",
        )

    def _set_key(self, colour):
        self.key_colour = colour
        r, g, b = (int(colour[i : i + 2], 16) for i in (0, 2, 4))
        self.swatch.config(
            text=f"  #{colour}  ",
            bg=f"#{colour}",
            fg="black" if (r * 299 + g * 587 + b * 114) > 128000 else "white",
        )

    def _pick_key_colour(self):
        res = colorchooser.askcolor(f"#{self.key_colour}", parent=self)
        if res and res[1]:
            self._set_key(hex_colour(res[1]))
            self._schedule()

    def _pick_bg_colour(self):
        res = colorchooser.askcolor(f"#{self.bg_colour}", parent=self)
        if res and res[1]:
            self.bg_colour = hex_colour(res[1])
            self.bg_swatch.config(bg=f"#{self.bg_colour}")
            self._schedule()

    def _bg_mode_changed(self):
        kind = self.bg_kind()
        self.bg_row.pack_forget()
        self.loop_chk.pack_forget()
        self.bg_swatch.pack_forget()
        self.alpha_note.pack_forget()
        if kind == "file":
            self.bg_row.pack(fill="x", pady=px(2))
            self.loop_chk.pack(anchor="w")
        elif kind == "color":
            self.bg_swatch.pack(side="left", padx=px(6))
        else:
            self.alpha_note.pack(anchor="w", pady=px(4, 0))
        out = self.out_row.path
        if out:
            base, ext = os.path.splitext(out)
            if kind == "alpha" and ext.lower() not in (".mov", ".webm", ".mkv"):
                self.out_row.set(base + ".mov")
            elif kind != "alpha" and ext.lower() == ".mov" and base.endswith("_keyed"):
                self.out_row.set(base + ".mp4")
        self._schedule()

    def _schedule(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(200, self._refresh)

    def _refresh(self):
        self._job = None
        self.compare.set_filter(self.preview_chain())

    # ── Render ───────────────────────────────────────────────────────────────

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src, what=t("green_key.foreground_label"))
        if info is None:
            return
        kind = self.bg_kind()
        bg = self.bg_row.path
        if kind == "file":
            if common.check_source(self, bg, what=t("green_key.background_label")) is None:
                return
        out = common.output_for(
            self, self.out_row, src, "keyed", inputs=[bg] if kind == "file" else []
        )
        if not out:
            return
        w, h = common.even_size(info)
        fps = frame_rate(info)
        keyer = self.keyer()
        container = common.container_of(out)
        extra_inputs: list = []
        if kind == "alpha":
            if container not in ("mov", "webm", "mkv"):
                messagebox.showwarning(
                    t("common.warning"),
                    "Transparent video needs a .mov (ProRes 4444) or .webm (VP9) output file.",
                    parent=self,
                )
                return
            graph = (
                f"[0:v]{keyer},format=yuva444p10le[v]"
                if container != "webm"
                else f"[0:v]{keyer},format=yuva420p[v]"
            )
            if container == "webm":
                venc_args = [
                    "-c:v",
                    "libvpx-vp9",
                    "-pix_fmt",
                    "yuva420p",
                    "-crf",
                    "28",
                    "-b:v",
                    "0",
                    "-row-mt",
                    "1",
                    "-auto-alt-ref",
                    "0",
                ]
            else:
                venc_args = [
                    "-c:v",
                    "prores_ks",
                    "-profile:v",
                    "4444",
                    "-pix_fmt",
                    "yuva444p10le",
                    "-alpha_bits",
                    "16",
                    "-vendor",
                    "apl0",
                ]
            cmd = [
                ff.ffmpeg_path(),
                "-y",
                "-i",
                src,
                "-filter_complex",
                graph,
                "-map",
                "[v]",
                *keep_audio_args(info, container),
                *venc_args,
                out,
            ]
            self.log(self.console, "Keying to a transparent video…")
            self.run_ffmpeg(
                cmd,
                self.console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self.btn_render,
                duration=info.duration or None,
                output=out,
                inputs=[src],
                name="Green screen",
            )
            return
        if kind == "color":
            bg_src = f"color=c=0x{self.bg_colour}:s={w}x{h}:r={fps},setsar=1[bg]"
        else:
            bg_info = ff.try_probe(bg)
            still = bool(bg_info and bg_info.is_image) or bg.lower().endswith(IMAGE_EXTS)
            if still:
                extra_inputs = ["-loop", "1", "-framerate", fps, "-i", bg]
                hold = ""
            elif self.loop_var.get():
                extra_inputs = ["-stream_loop", "-1", "-i", bg]
                hold = ""
            else:
                extra_inputs = ["-i", bg]
                hold = ",tpad=stop_mode=clone:stop=-1"
            bg_src = f"[1:v]{background_chain(w, h, fps)}{hold}[bg]"
        graph = f"{bg_src};[0:v]setsar=1,{keyer}[fg];[bg][fg]overlay=format=auto:shortest=1[v]"
        self.log(self.console, f"Keying #{self.key_colour} ({self.method_var.get()})")
        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                graph=graph,
                extra_inputs=extra_inputs,
                name="Green screen",
                inputs=[bg] if kind == "file" else [],
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
