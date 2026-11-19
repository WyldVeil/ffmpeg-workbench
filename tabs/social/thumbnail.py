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

import tkinter as tk
from tkinter import messagebox, ttk
from core import dialogs
import os

from tabs.base_tab import BaseTab, CLR, UI_FONT, MONO_FONT
from core.hardware import (    launch_preview,
)
from core.i18n import t


def _fmt(seconds):
    m, s = divmod(max(0, seconds), 60)
    return f"{int(m):02d}:{s:05.2f}"


PRESETS = {           # stable id: (label, (width, height)); (0, 0) = original size
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

    def _build_ui(self):
        self.make_header(self, t("tab.thumbnail_maker"),
                         t("thumbnail.subtitle"),
                         icon="🖼")

        # ── Source ────────────────────────────────────────────────────────
        sf = tk.Frame(self)
        sf.pack(fill="x", padx=20, pady=(14, 4))
        tk.Label(sf, text=t("common.source_video"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._src_var = tk.StringVar()
        tk.Entry(sf, textvariable=self._src_var, width=55, relief="flat",
                 font=(UI_FONT, 10)).pack(side="left", padx=8)
        self._src_var.trace_add("write", lambda *_a: self.after(400, self._src_typed))
        tk.Button(sf, text=t("btn.browse"), command=self._browse, cursor="hand2",
                  relief="flat", font=(UI_FONT, 9)).pack(side="left")
        self._dur_lbl = tk.Label(sf, text="", fg=CLR["fgdim"], font=(MONO_FONT, 9))
        self._dur_lbl.pack(side="left", padx=10)

        # ── Frame selection ───────────────────────────────────────────────
        frame_lf = tk.LabelFrame(self, text=f"  {t('thumbnail.frame_section')}  ", padx=15, pady=10,
                                 font=(UI_FONT, 9, "bold"))
        frame_lf.pack(fill="x", padx=20, pady=8)

        fr1 = tk.Frame(frame_lf)
        fr1.pack(fill="x", pady=3)
        tk.Label(fr1, text=t("thumbnail.scrub_label"), font=(UI_FONT, 10, "bold"),
                 width=14, anchor="e").pack(side="left")
        self._scrub_var = tk.DoubleVar(value=0.0)
        self._scrub = tk.Scale(fr1, variable=self._scrub_var, from_=0, to=100,
                               resolution=0.01, orient="horizontal", length=420,
                               bg=CLR["panel"], fg=CLR["fg"], highlightthickness=0,
                               command=self._on_scrub)
        self._scrub.pack(side="left", padx=8)
        self._time_lbl = tk.Label(fr1, text="00:00.00", fg=CLR["accent"],
                                  font=(MONO_FONT, 10, "bold"), width=10)
        self._time_lbl.pack(side="left")

        fr2 = tk.Frame(frame_lf)
        fr2.pack(fill="x", pady=3)
        tk.Label(fr2, text="", width=14).pack(side="left")
        tk.Button(fr2, text=f"👁 {t('thumbnail.preview_button')}", bg=CLR["accent"], fg="white",
                  font=(UI_FONT, 9), cursor="hand2",
                  command=self._preview_frame).pack(side="left", padx=4)
        tk.Button(fr2, text=t("thumbnail.1s"), bg=CLR["panel"], fg=CLR["fg"],
                  font=(UI_FONT, 9), width=5, cursor="hand2",
                  command=lambda: self._nudge(-1.0)).pack(side="left", padx=2)
        tk.Button(fr2, text=t("thumbnail.0_1s"), bg=CLR["panel"], fg=CLR["fg"],
                  font=(UI_FONT, 9), width=6, cursor="hand2",
                  command=lambda: self._nudge(-0.1)).pack(side="left", padx=2)
        tk.Button(fr2, text=t("thumbnail.0_1s_2"), bg=CLR["panel"], fg=CLR["fg"],
                  font=(UI_FONT, 9), width=6, cursor="hand2",
                  command=lambda: self._nudge(0.1)).pack(side="left", padx=2)
        tk.Button(fr2, text=t("thumbnail.1s_2"), bg=CLR["panel"], fg=CLR["fg"],
                  font=(UI_FONT, 9), width=5, cursor="hand2",
                  command=lambda: self._nudge(1.0)).pack(side="left", padx=2)

        # ── Size preset ───────────────────────────────────────────────────
        size_lf = tk.LabelFrame(self, text=f"  {t('thumbnail.output_size_section')}  ", padx=15, pady=8,
                                font=(UI_FONT, 9, "bold"))
        size_lf.pack(fill="x", padx=20, pady=6)

        self._size_var = tk.StringVar(value="yt_hd")
        sz_row = tk.Frame(size_lf)
        sz_row.pack(fill="x")
        for key, (label, _wh) in PRESETS.items():
            tk.Radiobutton(sz_row, text=label, variable=self._size_var,
                           value=key, font=(UI_FONT, 9)).pack(anchor="w")

        # ── Image adjustments ─────────────────────────────────────────────
        adj_lf = tk.LabelFrame(self, text=f"  {t('thumbnail.adjustments_section')}  ",
                               padx=15, pady=8, font=(UI_FONT, 9, "bold"))
        adj_lf.pack(fill="x", padx=20, pady=6)

        ar1 = tk.Frame(adj_lf)
        ar1.pack(fill="x", pady=2)

        tk.Label(ar1, text=t("thumbnail.brightness_label"), font=(UI_FONT, 10), width=12,
                 anchor="e").pack(side="left")
        self._brightness = tk.DoubleVar(value=0.0)
        tk.Scale(ar1, variable=self._brightness, from_=-0.5, to=0.5,
                 resolution=0.02, orient="horizontal", length=150,
                 bg=CLR["panel"], fg=CLR["fg"],
                 highlightthickness=0).pack(side="left", padx=4)

        tk.Label(ar1, text=t("thumbnail.contrast_label"), font=(UI_FONT, 10)).pack(side="left", padx=(12, 0))
        self._contrast = tk.DoubleVar(value=1.0)
        tk.Scale(ar1, variable=self._contrast, from_=0.5, to=2.0,
                 resolution=0.05, orient="horizontal", length=150,
                 bg=CLR["panel"], fg=CLR["fg"],
                 highlightthickness=0).pack(side="left", padx=4)

        tk.Label(ar1, text=t("thumbnail.saturation_label"), font=(UI_FONT, 10)).pack(side="left", padx=(12, 0))
        self._saturation = tk.DoubleVar(value=1.0)
        tk.Scale(ar1, variable=self._saturation, from_=0.0, to=3.0,
                 resolution=0.05, orient="horizontal", length=150,
                 bg=CLR["panel"], fg=CLR["fg"],
                 highlightthickness=0).pack(side="left", padx=4)

        # ── Text overlay ──────────────────────────────────────────────────
        txt_lf = tk.LabelFrame(self, text=f"  {t('thumbnail.text_overlay_section')}  ",
                               padx=15, pady=8, font=(UI_FONT, 9, "bold"))
        txt_lf.pack(fill="x", padx=20, pady=6)

        self._use_text = tk.BooleanVar(value=False)
        tk.Checkbutton(txt_lf, text=t("thumbnail.add_text_checkbox"), variable=self._use_text,
                       font=(UI_FONT, 10)).pack(anchor="w")

        tr1 = tk.Frame(txt_lf)
        tr1.pack(fill="x", pady=3)
        tk.Label(tr1, text=t("thumbnail.text_label"), font=(UI_FONT, 10), width=10,
                 anchor="e").pack(side="left")
        self._text_var = tk.StringVar(value="")
        tk.Entry(tr1, textvariable=self._text_var, width=40, relief="flat",
                 font=(UI_FONT, 11)).pack(side="left", padx=6)

        tr2 = tk.Frame(txt_lf)
        tr2.pack(fill="x", pady=3)
        tk.Label(tr2, text=t("thumbnail.text_size_label"), font=(UI_FONT, 10), width=10,
                 anchor="e").pack(side="left")
        self._text_size = tk.StringVar(value="64")
        tk.Entry(tr2, textvariable=self._text_size, width=5, relief="flat",
                 font=(UI_FONT, 10)).pack(side="left", padx=6)
        tk.Label(tr2, text=t("thumbnail.text_color_label"), font=(UI_FONT, 10)).pack(side="left", padx=(12, 0))
        self._text_color = tk.StringVar(value="white")
        ttk.Combobox(tr2, textvariable=self._text_color, width=10,
                     values=["white", "yellow", "red", "black", "cyan", "lime"],
                     state="readonly").pack(side="left", padx=6)
        tk.Label(tr2, text=t("thumbnail.text_position_label"), font=(UI_FONT, 10)).pack(side="left", padx=(12, 0))
        self._text_pos = tk.StringVar(value="center")
        ttk.Combobox(tr2, textvariable=self._text_pos, width=12,
                     values=["center", "top", "bottom", "top-left", "top-right"],
                     state="readonly").pack(side="left", padx=6)

        self._text_stroke = tk.BooleanVar(value=True)
        tk.Checkbutton(tr2, text=t("thumbnail.stroke_checkbox"), variable=self._text_stroke,
                       font=(UI_FONT, 10)).pack(side="left", padx=12)

        # ── Batch mode ────────────────────────────────────────────────────
        batch_lf = tk.LabelFrame(self, text=f"  {t('thumbnail.batch_section')}  ", padx=15, pady=8,
                                 font=(UI_FONT, 9, "bold"))
        batch_lf.pack(fill="x", padx=20, pady=6)

        br = tk.Frame(batch_lf)
        br.pack(fill="x")
        self._batch_mode = tk.BooleanVar(value=False)
        tk.Checkbutton(br, text=t("thumbnail.extract_every_label"), variable=self._batch_mode,
                       font=(UI_FONT, 10)).pack(side="left")
        self._batch_interval = tk.StringVar(value="10")
        tk.Entry(br, textvariable=self._batch_interval, width=5, relief="flat",
                 font=(UI_FONT, 10)).pack(side="left", padx=4)
        tk.Label(br, text=t("thumbnail.seconds_to_folder_hint"),
                 fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(side="left")

        # ── Output ────────────────────────────────────────────────────────
        of = tk.Frame(self)
        of.pack(fill="x", padx=20, pady=6)
        tk.Label(of, text=t("encode_queue.output_label"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._out_var = tk.StringVar()
        tk.Entry(of, textvariable=self._out_var, width=55, relief="flat",
                 font=(UI_FONT, 10)).pack(side="left", padx=8)
        tk.Button(of, text=t("common.save_as"), command=self._browse_out, cursor="hand2",
                  relief="flat", font=(UI_FONT, 9)).pack(side="left")

        fmt_f = tk.Frame(self)
        fmt_f.pack(fill="x", padx=20, pady=2)
        tk.Label(fmt_f, text=t("common.format"), font=(UI_FONT, 10)).pack(side="left")
        self._fmt_var = tk.StringVar(value="PNG")
        for v in ["PNG", "JPEG (q95)", "JPEG (q85)", "WebP"]:
            tk.Radiobutton(fmt_f, text=v, variable=self._fmt_var, value=v,
                           font=(UI_FONT, 10)).pack(side="left", padx=8)

        # ── Run ───────────────────────────────────────────────────────────
        bf = tk.Frame(self)
        bf.pack(pady=8)
        self._btn_run = tk.Button(
            bf, text="🖼  " + t("tab.thumbnail_maker"), font=(UI_FONT, 12, "bold"),
            bg=CLR["green"], fg="white", height=2, width=26,
            cursor="hand2", command=self._render)
        self._btn_run.pack()

        # ── Console ───────────────────────────────────────────────────────
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=20, pady=(4, 10))
        self.console, csb = self.make_console(cf, height=4)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── Callbacks ──────────────────────────────────────────────────────
    def _browse(self):
        p = dialogs.ask_open("video", parent=self)
        if p:
            self._src_var.set(p)
            self._src_typed()

    def _src_typed(self):
        p = self._src_var.get().strip()
        if p and os.path.isfile(p) and p != getattr(self, "file_path", ""):
            from core import ffmpeg as ff
            info = ff.try_probe(p)
            if info is None or not info.has_video:
                return
            self.file_path = p
            self.duration = info.duration
            self._fps = info.video.fps or 30.0
            self._dur_lbl.config(text=_fmt(self.duration))
            self._scrub.config(to=max(0.1, self.duration))

    def _browse_out(self):
        p = dialogs.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("WebP", "*.webp")])
        if p:
            self._out_var.set(p)

    def _on_scrub(self, val):
        try:
            self._time_lbl.config(text=_fmt(float(val)))
        except (ValueError, tk.TclError):
            pass

    def _nudge(self, delta):
        pos = max(0.0, min(self._scrub_var.get() + delta, self.duration))
        self._scrub_var.set(pos)
        self._time_lbl.config(text=_fmt(pos))

    def _preview_frame(self):
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        pos = self._scrub_var.get()
        if self.preview_proc:
            try:
                self.preview_proc.terminate()
            except Exception:
                pass
        self.preview_proc = launch_preview(self.file_path, start_time=pos)

    def _render(self):
        from core import ffmpeg as ff
        src = self._src_var.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        if src != getattr(self, "file_path", ""):
            self._src_typed()
        ffmpeg = ff.ffmpeg_path()
        ext = self._get_ext()
        vf_parts = self._build_vf()
        if self._batch_mode.get():
            try:
                interval = float(self._batch_interval.get())
                if not 0 < interval < max(self.duration, 0.1):
                    raise ValueError
            except ValueError:
                messagebox.showwarning(t("common.warning"), "The interval must be a number of seconds "
                                       "shorter than the video.", parent=self)
                return
            out_root = dialogs.ask_directory(title="Choose a folder for the thumbnails", parent=self)
            if not out_root:
                return
            from tabs.social.frame_extractor import fresh_folder
            base = os.path.splitext(os.path.basename(src))[0]
            out_dir = fresh_folder(out_root, f"{base} thumbnails")
            pattern = os.path.join(out_dir, base).replace("%", "%%") + f"_%04d{ext}"
            cmd = [ffmpeg, "-y", "-i", src, "-an", "-vf", ",".join([f"fps=1/{interval:g}", *vf_parts]),
                   *self._get_format_args(), pattern]
            self.log(self.console, f"Batch export: every {interval:g}s → {out_dir}")
            self.run_ffmpeg(cmd, self.console, on_done=lambda rc: self.show_result(rc, out_dir),
                            btn=self._btn_run, btn_label="🖼  EXPORT THUMBNAIL", duration=self.duration,
                            output=out_dir, inputs=[src], name="Thumbnails")
            return
        out = self._out_var.get().strip() or self.suggest_output(src, "thumb", ext)
        out = os.path.splitext(out)[0] + ext                 # the chosen format decides the extension
        out = self.confirm_output(out, [src], default_ext=ext)
        if not out:
            return
        self._out_var.set(out)
        pos = max(0.0, self._scrub_var.get())
        if pos >= self.duration - 0.5:
            # the very end: decode the last half second and keep overwriting the image,
            # so whatever the timestamps say, the final frame is what remains
            pos = self.duration
            cmd = [ffmpeg, "-y", "-sseof", "-0.5", "-i", src, "-an"]
        else:
            cmd = [ffmpeg, "-y", "-ss", ff.ts(pos), "-i", src, "-frames:v", "1", "-an"]
        if vf_parts:
            cmd += ["-vf", ",".join(vf_parts)]
        cmd += self._get_format_args() + ["-update", "1", out]
        self.log(self.console, f"Extracting frame at {_fmt(pos)}")
        self.run_ffmpeg(cmd, self.console, on_done=lambda rc: self.show_result(rc, out),
                        btn=self._btn_run, btn_label="🖼  EXPORT THUMBNAIL", duration=0.1,
                        output=out, inputs=[src], name="Thumbnail")

    def _build_vf(self):
        parts = []

        # Size
        _label, (w, h) = PRESETS.get(self._size_var.get(), PRESETS["original"])
        if w > 0 and h > 0:
            parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black")

        # Adjustments
        br = self._brightness.get()
        ct = self._contrast.get()
        sat = self._saturation.get()
        if br != 0.0 or ct != 1.0 or sat != 1.0:
            parts.append(f"eq=brightness={br}:contrast={ct}:saturation={sat}")

        # Text overlay (from a UTF-8 file: quotes, %, colons are all safe)
        if self._use_text.get() and self._text_var.get().strip():
            from core import filters as fl
            if getattr(self, "_tmp", None) is None:
                from core.paths import temp_dir
                self._tmp = temp_dir("thumb_")
            textfile = os.path.join(self._tmp, "caption.txt")
            with open(textfile, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self._text_var.get())
            x, y = {"center": ("(w-text_w)/2", "(h-text_h)/2"), "top": ("(w-text_w)/2", "h*0.05"),
                    "bottom": ("(w-text_w)/2", "h-text_h-h*0.05"), "top-left": ("w*0.04", "h*0.05"),
                    "top-right": ("w-text_w-w*0.04", "h*0.05")}.get(self._text_pos.get(),
                                                                    ("(w-text_w)/2", "(h-text_h)/2"))
            parts.append(fl.drawtext_filter(
                textfile=textfile, font="impact", fontsize=self._text_size.get() or "64",
                fontcolor=self._text_color.get() or "white", x=x, y=y,
                borderw=4 if self._text_stroke.get() else 0, bordercolor="black"))

        return parts

    def _get_ext(self):
        fmt = self._fmt_var.get()
        if "PNG" in fmt:
            return ".png"
        elif "WebP" in fmt:
            return ".webp"
        else:
            return ".jpg"

    def _get_format_args(self):
        fmt = self._fmt_var.get()
        if "PNG" in fmt:
            return ["-c:v", "png"]
        elif "q95" in fmt:
            return ["-c:v", "mjpeg", "-q:v", "2"]
        elif "q85" in fmt:
            return ["-c:v", "mjpeg", "-q:v", "5"]
        elif "WebP" in fmt:
            return ["-c:v", "libwebp", "-quality", "90"]
        return ["-c:v", "png"]
