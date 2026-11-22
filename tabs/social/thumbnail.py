"""
tab_thumbnailmaker.py  ─  Thumbnail Maker

Extract the best frame from a video and export it as a high-res
image suitable for YouTube/TikTok thumbnails.  Options for:
  • Frame scrubbing to pick the perfect moment
  • Multiple output sizes (YouTube 1280×720, TikTok 1080×1920, etc.)
  • Text overlay with customisable font, colour, position, and stroke
  • Brightness / contrast / saturation boost for punchy thumbnails
  • Border / vignette effects
  • Batch: export a frame every N seconds for selection
"""

import logging
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core.hardware import (
    launch_preview,
)
from core.i18n import t
from core.ops import thumbnail as th
from core.scaling import px
from core.timecode import Timecode
from tabs import framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.kit.contracts import ScrubTransport, Transport
from tabs.widgets import FramePreview

logger = logging.getLogger("crossfader.social.thumbnail")


def _fmt(seconds):
    return f"{Timecode.of(seconds):.2}"


PRESETS = {  # stable id: (label, (width, height)); (0, 0) = original size
    "yt": ("YouTube (1280×720)", (1280, 720)),
    "yt_hd": ("YouTube HD (1920×1080)", (1920, 1080)),
    "vertical": ("TikTok / Shorts (1080×1920)", (1080, 1920)),
    "square": ("Instagram (1080×1080)", (1080, 1080)),
    "x": ("X / Twitter (1200×675)", (1200, 675)),
    "original": ("Original resolution", (0, 0)),
}


class ThumbnailMakerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self.duration = 0.0
        self.preview_proc = None
        self._build_ui()
        self._watch_look()

    def transport(self) -> Transport | None:  # tabs/kit/contracts.py
        return ScrubTransport(self._scrub, lambda: self.duration)

    def _build_ui(self):
        self._build_header()
        self._build_source()
        self._build_frame_selection()
        self._build_size_preset()
        self._build_image_adjustments()
        self._build_text_overlay()
        self._build_batch_mode()
        self._build_output()
        self._build_run()
        self._build_console()

    def _build_header(self):
        self.make_header(self, t("tab.thumbnail_maker"), t("thumbnail.subtitle"), icon="🖼")

    def _build_source(self):
        """Source."""
        # ── Source ────────────────────────────────────────────────────────
        sf = tk.Frame(self)
        sf.pack(fill="x", padx=px(20), pady=px(14, 4))
        tk.Label(sf, text=t("common.source_video"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._src_var = tk.StringVar()
        tk.Entry(sf, textvariable=self._src_var, width=55, relief="flat", font=(UI_FONT, 10)).pack(
            side="left", padx=px(8)
        )
        self._src_var.trace_add("write", lambda *_a: self.after(400, self._src_typed))
        tk.Button(
            sf,
            text=t("btn.browse"),
            command=self._browse,
            cursor="hand2",
            relief="flat",
            font=(UI_FONT, 9),
        ).pack(side="left")
        self._dur_lbl = tk.Label(sf, text="", fg=CLR["fgdim"], font=(MONO_FONT, 9))
        self._dur_lbl.pack(side="left", padx=px(10))

    def _build_frame_selection(self):
        """Frame selection."""
        # ── Frame selection ───────────────────────────────────────────────
        frame_lf = tk.LabelFrame(
            self,
            text=f"  {t('thumbnail.frame_section')}  ",
            padx=px(15),
            pady=px(10),
            font=(UI_FONT, 9, "bold"),
        )
        frame_lf.pack(fill="x", padx=px(20), pady=px(8))

        # Live preview of the thumbnail itself: the export's own filters (size,
        # adjustments, text) on the chosen frame
        self._preview = FramePreview(frame_lf, 320, 180, show_time=False)
        self._preview.pack(side="right", padx=px(10, 0))
        self._preview.canvas.configure(bg=CLR["bg"])
        self._preview_job = None

        fr1 = tk.Frame(frame_lf)
        fr1.pack(fill="x", pady=px(3))
        tk.Label(
            fr1, text=t("thumbnail.scrub_label"), font=(UI_FONT, 10, "bold"), width=14, anchor="e"
        ).pack(side="left")
        self._scrub_var = tk.DoubleVar(value=0.0)
        self._scrub = tk.Scale(
            fr1,
            variable=self._scrub_var,
            from_=0,
            to=100,
            resolution=0.001,
            orient="horizontal",
            length=px(420),
            bg=CLR["panel"],
            fg=CLR["fg"],
            highlightthickness=0,
            command=self._on_scrub,
        )
        self._scrub.pack(side="left", padx=px(8))
        framestep.slider(self._scrub, lambda: framestep.fps_of(self.file_path))
        self._time_lbl = tk.Label(
            fr1, text="00:00.00", fg=CLR["accent"], font=(MONO_FONT, 10, "bold"), width=10
        )
        self._time_lbl.pack(side="left")

        fr2 = tk.Frame(frame_lf)
        fr2.pack(fill="x", pady=px(3))
        tk.Label(fr2, text="", width=14).pack(side="left")
        tk.Button(
            fr2,
            text=f"👁 {t('thumbnail.preview_button')}",
            bg=CLR["accent"],
            fg="white",
            font=(UI_FONT, 9),
            cursor="hand2",
            command=self._preview_frame,
        ).pack(side="left", padx=px(4))
        tk.Button(
            fr2,
            text=t("thumbnail.1s"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            font=(UI_FONT, 9),
            width=5,
            cursor="hand2",
            command=lambda: self._nudge(-1.0),
        ).pack(side="left", padx=px(2))
        tk.Button(
            fr2,
            text=t("thumbnail.0_1s"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            font=(UI_FONT, 9),
            width=6,
            cursor="hand2",
            command=lambda: self._nudge(-0.1),
        ).pack(side="left", padx=px(2))
        tk.Button(
            fr2,
            text=t("thumbnail.0_1s_2"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            font=(UI_FONT, 9),
            width=6,
            cursor="hand2",
            command=lambda: self._nudge(0.1),
        ).pack(side="left", padx=px(2))
        tk.Button(
            fr2,
            text=t("thumbnail.1s_2"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            font=(UI_FONT, 9),
            width=5,
            cursor="hand2",
            command=lambda: self._nudge(1.0),
        ).pack(side="left", padx=px(2))

    def _build_size_preset(self):
        """Size preset."""
        # ── Size preset ───────────────────────────────────────────────────
        size_lf = tk.LabelFrame(
            self,
            text=f"  {t('thumbnail.output_size_section')}  ",
            padx=px(15),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
        )
        size_lf.pack(fill="x", padx=px(20), pady=px(6))

        self._size_var = tk.StringVar(value="yt_hd")
        sz_row = tk.Frame(size_lf)
        sz_row.pack(fill="x")
        for key, (label, _wh) in PRESETS.items():
            tk.Radiobutton(
                sz_row, text=label, variable=self._size_var, value=key, font=(UI_FONT, 9)
            ).pack(anchor="w")

    def _build_image_adjustments(self):
        """Image adjustments."""
        # ── Image adjustments ─────────────────────────────────────────────
        adj_lf = tk.LabelFrame(
            self,
            text=f"  {t('thumbnail.adjustments_section')}  ",
            padx=px(15),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
        )
        adj_lf.pack(fill="x", padx=px(20), pady=px(6))

        ar1 = tk.Frame(adj_lf)
        ar1.pack(fill="x", pady=px(2))

        tk.Label(
            ar1, text=t("thumbnail.brightness_label"), font=(UI_FONT, 10), width=12, anchor="e"
        ).pack(side="left")
        self._brightness = tk.DoubleVar(value=0.0)
        tk.Scale(
            ar1,
            variable=self._brightness,
            from_=-0.5,
            to=0.5,
            resolution=0.02,
            orient="horizontal",
            length=px(150),
            bg=CLR["panel"],
            fg=CLR["fg"],
            highlightthickness=0,
        ).pack(side="left", padx=px(4))

        tk.Label(ar1, text=t("thumbnail.contrast_label"), font=(UI_FONT, 10)).pack(
            side="left", padx=px(12, 0)
        )
        self._contrast = tk.DoubleVar(value=1.0)
        tk.Scale(
            ar1,
            variable=self._contrast,
            from_=0.5,
            to=2.0,
            resolution=0.05,
            orient="horizontal",
            length=px(150),
            bg=CLR["panel"],
            fg=CLR["fg"],
            highlightthickness=0,
        ).pack(side="left", padx=px(4))

        tk.Label(ar1, text=t("thumbnail.saturation_label"), font=(UI_FONT, 10)).pack(
            side="left", padx=px(12, 0)
        )
        self._saturation = tk.DoubleVar(value=1.0)
        tk.Scale(
            ar1,
            variable=self._saturation,
            from_=0.0,
            to=3.0,
            resolution=0.05,
            orient="horizontal",
            length=px(150),
            bg=CLR["panel"],
            fg=CLR["fg"],
            highlightthickness=0,
        ).pack(side="left", padx=px(4))

    def _build_text_overlay(self):
        """Text overlay."""
        # ── Text overlay ──────────────────────────────────────────────────
        txt_lf = tk.LabelFrame(
            self,
            text=f"  {t('thumbnail.text_overlay_section')}  ",
            padx=px(15),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
        )
        txt_lf.pack(fill="x", padx=px(20), pady=px(6))

        self._use_text = tk.BooleanVar(value=False)
        tk.Checkbutton(
            txt_lf,
            text=t("thumbnail.add_text_checkbox"),
            variable=self._use_text,
            font=(UI_FONT, 10),
        ).pack(anchor="w")

        tr1 = tk.Frame(txt_lf)
        tr1.pack(fill="x", pady=px(3))
        tk.Label(
            tr1, text=t("thumbnail.text_label"), font=(UI_FONT, 10), width=10, anchor="e"
        ).pack(side="left")
        self._text_var = tk.StringVar(value="")
        tk.Entry(
            tr1, textvariable=self._text_var, width=40, relief="flat", font=(UI_FONT, 11)
        ).pack(side="left", padx=px(6))

        tr2 = tk.Frame(txt_lf)
        tr2.pack(fill="x", pady=px(3))
        tk.Label(
            tr2, text=t("thumbnail.text_size_label"), font=(UI_FONT, 10), width=10, anchor="e"
        ).pack(side="left")
        self._text_size = tk.StringVar(value="64")
        tk.Entry(
            tr2, textvariable=self._text_size, width=5, relief="flat", font=(UI_FONT, 10)
        ).pack(side="left", padx=px(6))
        tk.Label(tr2, text=t("thumbnail.text_color_label"), font=(UI_FONT, 10)).pack(
            side="left", padx=px(12, 0)
        )
        self._text_color = tk.StringVar(value="white")
        ttk.Combobox(
            tr2,
            textvariable=self._text_color,
            width=10,
            values=["white", "yellow", "red", "black", "cyan", "lime"],
            state="readonly",
        ).pack(side="left", padx=px(6))
        tk.Label(tr2, text=t("thumbnail.text_position_label"), font=(UI_FONT, 10)).pack(
            side="left", padx=px(12, 0)
        )
        self._text_pos = tk.StringVar(value="center")
        ttk.Combobox(
            tr2,
            textvariable=self._text_pos,
            width=12,
            values=["center", "top", "bottom", "top-left", "top-right"],
            state="readonly",
        ).pack(side="left", padx=px(6))

        self._text_stroke = tk.BooleanVar(value=True)
        tk.Checkbutton(
            tr2, text=t("thumbnail.stroke_checkbox"), variable=self._text_stroke, font=(UI_FONT, 10)
        ).pack(side="left", padx=px(12))

    def _build_batch_mode(self):
        """Batch mode."""
        # ── Batch mode ────────────────────────────────────────────────────
        batch_lf = tk.LabelFrame(
            self,
            text=f"  {t('thumbnail.batch_section')}  ",
            padx=px(15),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
        )
        batch_lf.pack(fill="x", padx=px(20), pady=px(6))

        br = tk.Frame(batch_lf)
        br.pack(fill="x")
        self._batch_mode = tk.BooleanVar(value=False)
        tk.Checkbutton(
            br,
            text=t("thumbnail.extract_every_label"),
            variable=self._batch_mode,
            font=(UI_FONT, 10),
        ).pack(side="left")
        self._batch_interval = tk.StringVar(value="10")
        tk.Entry(
            br, textvariable=self._batch_interval, width=5, relief="flat", font=(UI_FONT, 10)
        ).pack(side="left", padx=px(4))
        tk.Label(
            br, text=t("thumbnail.seconds_to_folder_hint"), fg=CLR["fgdim"], font=(UI_FONT, 9)
        ).pack(side="left")

    def _build_output(self):
        """Output."""
        # ── Output ────────────────────────────────────────────────────────
        of = tk.Frame(self)
        of.pack(fill="x", padx=px(20), pady=px(6))
        tk.Label(of, text=t("encode_queue.output_label"), font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self._out_var = tk.StringVar()
        tk.Entry(of, textvariable=self._out_var, width=55, relief="flat", font=(UI_FONT, 10)).pack(
            side="left", padx=px(8)
        )
        tk.Button(
            of,
            text=t("common.save_as"),
            command=self._browse_out,
            cursor="hand2",
            relief="flat",
            font=(UI_FONT, 9),
        ).pack(side="left")

        fmt_f = tk.Frame(self)
        fmt_f.pack(fill="x", padx=px(20), pady=px(2))
        tk.Label(fmt_f, text=t("common.format"), font=(UI_FONT, 10)).pack(side="left")
        self._fmt_var = tk.StringVar(value="png")
        for code, (label, _ext, _args) in th.PICTURE_FORMATS.items():
            tk.Radiobutton(
                fmt_f, text=label, variable=self._fmt_var, value=code, font=(UI_FONT, 10)
            ).pack(side="left", padx=px(8))

    def _build_run(self):
        """Run."""
        # ── Run ───────────────────────────────────────────────────────────
        bf = tk.Frame(self)
        bf.pack(pady=px(8))
        self.btn_render = self.make_render_btn(
            bf, "🖼  " + t("tab.thumbnail_maker"), self._render, width=26
        )
        self.btn_render.pack()

    def _build_console(self):
        """Console."""
        # ── Console ───────────────────────────────────────────────────────
        self.console = self.make_console_block(self, height=4, padx=px(20), pady=px(4, 10))

    # ── Callbacks ──────────────────────────────────────────────────────
    def _browse(self):
        p = dialogs.ask_open("video", parent=self)
        if p:
            self._src_var.set(p)
            self._src_typed()

    def _watch_look(self):
        for var in (
            self._size_var,
            self._brightness,
            self._contrast,
            self._saturation,
            self._use_text,
            self._text_var,
            self._text_size,
            self._text_color,
            self._text_pos,
            self._text_stroke,
        ):
            var.trace_add("write", self._look_changed)

    def on_source_loaded(self, path):
        """Drag and drop / File > Open: the shared loader has already set the
        path, so load it here (the typed-path check would think it was done)."""
        self._src_typed(force=True)

    def _src_typed(self, force=False):
        p = self._src_var.get().strip()
        if p and os.path.isfile(p) and (force or p != getattr(self, "file_path", "")):
            from core import ffmpeg as ff

            info = ff.try_probe(p)
            if info is None or not info.has_video:
                return
            self.file_path = p
            self.duration = info.duration
            self._fps = info.require_video().fps or 30.0
            self._dur_lbl.config(text=_fmt(self.duration))
            self._scrub.config(to=max(0.1, self.duration))
            self._preview.load(p, self.duration, float(self._scrub_var.get()))
            self._look_changed()

    def _look_changed(self, *_a):
        """Size, adjustments or text changed: refresh the preview (debounced)."""
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(250, self._refresh_preview)

    def _refresh_preview(self):
        self._preview_job = None
        if getattr(self, "file_path", ""):
            self._preview.set_filter(",".join(self._build_vf()))

    def _browse_out(self):
        p = dialogs.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("WebP", "*.webp")],
        )
        if p:
            self._out_var.set(p)

    def _on_scrub(self, val):
        try:
            self._time_lbl.config(text=_fmt(float(val)))
            self._preview.show(float(val))
        except (ValueError, tk.TclError):
            pass

    def _nudge(self, delta):
        pos = max(0.0, min(self._scrub_var.get() + delta, self.duration))
        self._scrub_var.set(pos)
        self._time_lbl.config(text=_fmt(pos))
        self._preview.show(pos)

    def _preview_frame(self):
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        pos = self._scrub_var.get()
        if self.preview_proc:
            try:
                self.preview_proc.terminate()
            except Exception:
                logger.debug("_preview_frame: best-effort step failed", exc_info=True)
        self.preview_proc = launch_preview(self.file_path, start_time=pos)

    def _render(self):
        src = self._src_var.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        if src != getattr(self, "file_path", ""):
            self._src_typed()
        ext = self._get_ext()
        vf_parts = self._build_vf()
        if self._batch_mode.get():
            try:
                interval = float(self._batch_interval.get())
                if not 0 < interval < max(self.duration, 0.1):
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    t("common.warning"),
                    "The interval must be a number of seconds shorter than the video.",
                    parent=self,
                )
                return
            out_root = dialogs.ask_directory(
                title="Choose a folder for the thumbnails", parent=self
            )
            if not out_root:
                return
            from core.outputs import fresh_folder

            base = os.path.splitext(os.path.basename(src))[0]
            out_dir = fresh_folder(out_root, f"{base} thumbnails")
            pattern = os.path.join(out_dir, base).replace("%", "%%") + f"_%04d{ext}"
            cmd = th.batch_cmd(src, interval, vf_parts, self._get_format_args(), pattern)
            self.log(self.console, f"Batch export: every {interval:g}s → {out_dir}")
            self.run_ffmpeg(
                cmd,
                self.console,
                on_done=lambda rc: self.show_result(rc, out_dir),
                btn=self.btn_render,
                duration=self.duration,
                output=out_dir,
                inputs=[src],
                name="Thumbnails",
            )
            return
        out = self._out_var.get().strip() or self.suggest_output(src, "thumb", ext)
        out = os.path.splitext(out)[0] + ext  # the chosen format decides the extension
        out = self.confirm_output(out, [src], default_ext=ext)
        if not out:
            return
        self._out_var.set(out)
        pos = max(0.0, self._scrub_var.get())
        if pos >= self.duration - 0.5:
            pos = self.duration
        cmd = th.frame_cmd(src, pos, self.duration, vf_parts, self._get_format_args(), out)
        self.log(self.console, f"Extracting frame at {_fmt(pos)}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=0.1,
            output=out,
            inputs=[src],
            name="Thumbnail",
        )

    def _build_vf(self) -> list[str]:
        _label, size = PRESETS.get(self._size_var.get(), PRESETS["original"])
        caption = None
        if self._use_text.get() and self._text_var.get().strip():
            if getattr(self, "_tmp", None) is None:
                from core.paths import temp_dir

                self._tmp = temp_dir("thumb_")
            caption = th.Caption(
                text=self._text_var.get(),
                size=self._text_size.get() or "64",
                colour=self._text_color.get() or "white",
                place=self._text_pos.get(),
                outline=self._text_stroke.get(),
            )
        look = th.Look(
            size=size,
            brightness=self._brightness.get(),
            contrast=self._contrast.get(),
            saturation=self._saturation.get(),
            caption=caption,
        )
        return th.look_filters(look, self._tmp if caption else "")

    def _get_ext(self):
        return th.PICTURE_FORMATS[self._fmt_var.get()][1]

    def _get_format_args(self):
        return list(th.PICTURE_FORMATS[self._fmt_var.get()][2])
