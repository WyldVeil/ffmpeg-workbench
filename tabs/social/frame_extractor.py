"""
tabs/social/frame_extractor.py  -  Frame Extractor

Pull stills out of a video - scrub the preview to find the frame you want.

  1. This frame   - the frame under the preview slider (or a typed time)
  2. Every N sec  - frames at a regular interval across a range
  3. Scene starts - the first frame of every scene (minimum scene length
                    respected, first scene included)
  4. Contact sheet - a grid of evenly spaced frames, each labelled with its
                    own timestamp, with a header line

Formats: PNG, JPEG and WebP (quality 1-100 means what it says), TIFF.
Image sets go to a fresh folder per run, so earlier exports are never mixed
with or overwritten by new ones.
"""

from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.ops.frame_extractor import quality_args
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab, add_tooltip
from tabs.widgets import FileRow, FramePreview, scroll_body

FORMATS = {"png": "PNG (lossless)", "jpg": "JPEG", "webp": "WebP", "tiff": "TIFF (print)"}
MODES = {
    "single": "This frame",
    "interval": "Every N seconds",
    "scenes": "Scene starts",
    "sheet": "Contact sheet",
}


def times_select(times, fps: float) -> str:
    """select= expression picking the frame nearest to each time."""
    half = 0.5 / max(fps, 1.0)
    return "+".join(f"between(t,{tm - half:.4f},{tm + half - 1e-4:.4f})" for tm in times)


def keep_scenes(cuts, min_len: float) -> list:
    """Scene start times: 0 plus each cut at least *min_len* after the last kept."""
    kept = [0.0]
    for c in sorted(cuts):
        if c - kept[-1] >= min_len:
            kept.append(c)
    return kept


def fresh_folder(parent: str, base: str) -> str:
    path = os.path.join(parent, base)
    n = 2
    while os.path.exists(path) and os.listdir(path):
        path = os.path.join(parent, f"{base} ({n})")
        n += 1
    os.makedirs(path, exist_ok=True)
    return path


class FrameExtractorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.frame_extractor"),
            t("frame_extractor.subtitle")
            if t("frame_extractor.subtitle") != "frame_extractor.subtitle"
            else "Thumbnails, storyboards and contact sheets.",
            icon="🖼",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 480, 270)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0.0)
        bar = tk.Frame(pv)
        bar.pack(fill="x")
        self.slider = ttk.Scale(
            bar,
            from_=0,
            to=1,
            variable=self.pos_var,
            length=px(400),
            command=lambda _v: self._moved(),
        )
        self.slider.pack(side="left", fill="x", expand=True)
        framestep.slider(self.slider, self._fps)
        for label, frames, seconds, tip in (
            ("◀◀", 0, -1, "One second back (Shift+,)"),
            ("◀", -1, 0, "One frame back (,)"),
            ("▶", 1, 0, "One frame forward (.)"),
            ("▶▶", 0, 1, "One second forward (Shift+.)"),
        ):
            b = tk.Button(
                bar,
                text=label,
                relief="flat",
                width=3,
                command=lambda f=frames, s=seconds: framestep.step(self.slider, f, s),
            )
            b.pack(side="left")
            add_tooltip(b, tip)

        opts = tk.LabelFrame(body, text="  Extract  ", padx=px(12), pady=px(8))
        opts.pack(fill="x", padx=px(20), pady=px(6))
        self.mode_var = tk.StringVar(value="single")
        mrow = tk.Frame(opts)
        mrow.pack(fill="x")
        for key, label in MODES.items():
            tk.Radiobutton(
                mrow, text=label, value=key, variable=self.mode_var, command=self._mode_changed
            ).pack(side="left", padx=px(6))

        self.panels = {}
        p = self.panels["single"] = tk.Frame(opts)
        tk.Label(p, text="Time:").pack(side="left")
        self.time_var = tk.StringVar(value="0")
        tk.Entry(p, textvariable=self.time_var, width=14).pack(side="left", padx=px(6))
        tk.Label(
            p, text="(follows the slider; type m:ss.ms to jump)", fg=CLR["fgdim"], font=(UI_FONT, 8)
        ).pack(side="left")
        self.time_var.trace_add("write", lambda *_a: self._time_typed())

        p = self.panels["interval"] = tk.Frame(opts)
        self.every_var = tk.StringVar(value="1")
        self.from_var = tk.StringVar(value="0")
        self.to_var = tk.StringVar(value="")
        for label, var, w in (
            ("Every (s):", self.every_var, 6),
            ("From:", self.from_var, 10),
            ("To (empty = end):", self.to_var, 10),
        ):
            tk.Label(p, text=label).pack(side="left", padx=px(8, 2))
            tk.Entry(p, textvariable=var, width=w).pack(side="left")

        p = self.panels["scenes"] = tk.Frame(opts)
        tk.Label(p, text="Sensitivity:").pack(side="left")
        self.scene_var = tk.DoubleVar(value=0.3)
        tk.Scale(
            p,
            variable=self.scene_var,
            from_=0.05,
            to=0.8,
            resolution=0.05,
            orient="horizontal",
            length=px(180),
        ).pack(side="left", padx=px(6))
        tk.Label(p, text="(lower = more scenes)   Min. scene (s):").pack(side="left")
        self.min_scene_var = tk.StringVar(value="2")
        tk.Entry(p, textvariable=self.min_scene_var, width=5).pack(side="left", padx=px(4))

        p = self.panels["sheet"] = tk.Frame(opts)
        self.cols_var, self.rows_var, self.tile_w_var = (
            tk.StringVar(value="5"),
            tk.StringVar(value="4"),
            tk.StringVar(value="320"),
        )
        for label, var in (
            ("Columns:", self.cols_var),
            ("Rows:", self.rows_var),
            ("Thumb width:", self.tile_w_var),
        ):
            tk.Label(p, text=label).pack(side="left", padx=px(8, 2))
            tk.Entry(p, textvariable=var, width=5).pack(side="left")
        self.stamp_var = tk.BooleanVar(value=True)
        tk.Checkbutton(p, text="Timestamps", variable=self.stamp_var).pack(side="left", padx=px(8))

        frow = tk.Frame(opts)
        frow.pack(fill="x", pady=px(8, 0))
        tk.Label(frow, text="Format:").pack(side="left")
        self.fmt_var = tk.StringVar(value=FORMATS["png"])
        ttk.Combobox(
            frow,
            textvariable=self.fmt_var,
            values=list(FORMATS.values()),
            state="readonly",
            width=16,
        ).pack(side="left", padx=px(6))
        tk.Label(frow, text="Quality (1-100):").pack(side="left", padx=px(10, 2))
        self.quality_var = tk.IntVar(value=92)
        tk.Scale(
            frow, variable=self.quality_var, from_=1, to=100, orient="horizontal", length=px(160)
        ).pack(side="left")

        drow = tk.Frame(opts)
        drow.pack(fill="x", pady=px(6, 0))
        tk.Label(drow, text="Save to folder:", width=14, anchor="w").pack(side="left")
        self.dir_var = tk.StringVar()
        tk.Entry(drow, textvariable=self.dir_var, width=50).pack(
            side="left", padx=px(6), fill="x", expand=True
        )
        tk.Button(drow, text=t("btn.browse"), relief="flat", command=self._pick_dir).pack(
            side="left"
        )

        self.btn_render = tk.Button(
            body,
            text=t("frame_extractor.extract_button"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=24,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._mode_changed()

    # ── interaction ──────────────────────────────────────────────────────────

    def _mode_changed(self):
        for p in self.panels.values():
            p.pack_forget()
        self.panels[self.mode_var.get()].pack(fill="x", pady=px(6, 0))

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.pos_var.set(min(dur * 0.1, 10.0))
        self.preview.load(path, dur, float(self.pos_var.get()))
        self._moved()
        if not self.dir_var.get().strip():
            from core.outputs import default_output_folder

            self.dir_var.set(default_output_folder(path))

    def _fps(self) -> float:
        return (self._info.require_video().fps if self._info and self._info.video else 0) or 30.0

    def _moved(self):
        pos = float(self.pos_var.get())
        self._syncing = True
        self.time_var.set(ff.format_time(pos))
        self._syncing = False
        self.preview.show(pos)

    def _time_typed(self):
        if getattr(self, "_syncing", False):
            return
        try:
            pos = ff.parse_time(self.time_var.get())
        except ValueError:
            return
        self.pos_var.set(pos)
        self.preview.show(pos)

    def _pick_dir(self):
        from tabs.kit import dialogs

        d = dialogs.ask_directory(parent=self)
        if d:
            self.dir_var.set(d)

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        self._info = info
        ext = next((k for k, v in FORMATS.items() if v == self.fmt_var.get()), "png")
        q = quality_args(ext, self.quality_var.get())
        folder = self.dir_var.get().strip() or os.path.dirname(src)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(t("common.error"), f"Cannot use {folder}:\n{exc}", parent=self)
            return
        base = os.path.splitext(os.path.basename(src))[0]
        mode = self.mode_var.get()
        try:
            if mode == "single":
                self._single(src, info, folder, base, ext, q)
            elif mode == "interval":
                self._interval(src, info, folder, base, ext, q)
            elif mode == "scenes":
                self._scenes(src, info, folder, base, ext, q)
            else:
                self._sheet(src, info, folder, base, ext, q)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)

    def _single(self, src, info, folder, base, ext, q):
        at = ff.parse_time(self.time_var.get())
        at = max(0.0, min(at, max(0.0, info.duration - 1.0 / self._fps())))
        stamp = ff.format_time(at).replace(":", "-")
        from core.outputs import unique_path

        out = unique_path(os.path.join(folder, f"{base}_{stamp}.{ext}"))
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            "-ss",
            ff.ts(at),
            "-i",
            src,
            "-frames:v",
            "1",
            "-an",
            *q,
            "-update",
            "1",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=0.1,
            output=out,
            inputs=[src],
            name="Frame Extractor",
        )

    def _interval(self, src, info, folder, base, ext, q):
        every = common.number(self.every_var.get(), "Every", 0.01, 3600)
        start = ff.parse_time(self.from_var.get() or "0")
        end = ff.parse_time(self.to_var.get()) if self.to_var.get().strip() else info.duration
        if end <= start:
            raise ValueError("'To' must be after 'From'.")
        out_dir = fresh_folder(folder, f"{base} frames")
        # image2 patterns treat every '%' as a format code - escape real ones
        pattern = os.path.join(out_dir, base).replace("%", "%%") + f"_%05d.{ext}"
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            "-ss",
            ff.ts(start),
            "-to",
            ff.ts(end),
            "-i",
            src,
            "-an",
            "-vf",
            f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{every - 0.5 / self._fps():.4f})'",
            "-fps_mode",
            "passthrough",
            *q,
            pattern,
        ]
        self.log(self.console, f"One frame every {every:g}s → {out_dir}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out_dir),
            btn=self.btn_render,
            duration=end - start,
            output=out_dir,
            inputs=[src],
            name="Frame Extractor",
        )

    def _scenes(self, src, info, folder, base, ext, q):
        threshold = float(self.scene_var.get())
        min_len = common.number(self.min_scene_var.get() or "0", "Min. scene", 0, 3600)
        out_dir = fresh_folder(folder, f"{base} scenes")
        fps = self._fps()
        dur = info.duration

        def work(_p, _c, ctx):
            tmp = ctx.tempdir()
            ctx.span(0.0, 0.7).run(
                [
                    ff.ffmpeg_path(),
                    "-i",
                    src,
                    "-an",
                    "-sn",
                    "-vf",
                    f"scale=320:-2,select='gt(scene,{threshold:.3f})',metadata=print:file=cuts.txt",
                    "-fps_mode",
                    "passthrough",
                    "-f",
                    "null",
                    "-",
                ],
                duration=dur,
                cwd=tmp,
            )
            try:
                with open(os.path.join(tmp, "cuts.txt"), encoding="utf-8", errors="replace") as fh:
                    cuts = [float(m) for m in re.findall(r"pts_time:\s*([\d.]+)", fh.read())]
            except OSError:
                cuts = []
            starts = keep_scenes(set(cuts), min_len)
            ctx.log(
                f"{len(starts)} scene(s): "
                + ", ".join(ff.format_time(s) for s in starts[:20])
                + (" …" if len(starts) > 20 else "")
            )
            ctx.span(0.7, 1.0).run(
                [
                    ff.ffmpeg_path(),
                    "-y",
                    "-i",
                    src,
                    "-an",
                    "-sn",
                    "-vf",
                    f"select='{times_select(starts, fps)}'",
                    "-fps_mode",
                    "passthrough",
                    *q,
                    os.path.join(out_dir, base).replace("%", "%%") + f"_scene_%04d.{ext}",
                ],
                duration=dur,
            )
            return 0

        self.log(self.console, f"Finding scenes (sensitivity {threshold:.2f}) → {out_dir}")
        common.queue_job(
            self,
            common.Job(
                name="Scene frames", out=out_dir, work=work, duration=dur * 1.3, inputs=[src]
            ),
            btn=self.btn_render,
            console=self.console,
        )

    def _sheet(self, src, info, folder, base, ext, q):
        cols = int(common.number(self.cols_var.get(), "Columns", 1, 20))
        rows = int(common.number(self.rows_var.get(), "Rows", 1, 20))
        tile_w = int(common.number(self.tile_w_var.get(), "Thumb width", 64, 1920)) // 2 * 2
        n = cols * rows
        dur = info.duration
        times = [dur * (i + 0.5) / n for i in range(n)]
        from core.outputs import unique_path
        from core.paths import temp_dir

        out = unique_path(os.path.join(folder, f"{base}_contact_sheet.{ext}"))
        work = temp_dir("sheet_")
        chain = [f"select='{times_select(times, self._fps())}'", f"scale={tile_w}:-2"]
        if self.stamp_var.get():
            chain.append(
                fl.DrawText(
                    textfile=fl.drawtext_text_file("%{pts:hms}", work),
                    font="mono",
                    fontsize=max(12, tile_w // 18),
                    fontcolor="white",
                    box=True,
                    boxcolor="black@0.55",
                    boxborderw=4,
                    x="w-text_w-6",
                    y="h-text_h-6",
                    expansion="normal",
                ).filter()
            )
        pad = 6
        chain.append(f"tile={cols}x{rows}:padding={pad}:margin={pad}:color=0x181818")
        head = max(28, tile_w // 9)
        v = info.require_video()
        title = (
            f"{os.path.basename(src)}   ·   {ff.format_time(dur)}   ·   "
            f"{v.display_size[0]}×{v.display_size[1]}   ·   {v.fps:.3g} fps"
        )
        chain.append(f"pad=iw:ih+{head}:0:{head}:color=0x181818")
        chain.append(
            fl.DrawText(
                textfile=fl.drawtext_text_file(title, work),
                font="sans",
                fontsize=int(head * 0.5),
                fontcolor="white",
                x="12",
                y=f"({head}-text_h)/2",
            ).filter()
        )
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            src,
            "-an",
            "-sn",
            "-vf",
            ",".join(chain),
            "-fps_mode",
            "passthrough",
            "-frames:v",
            "1",
            *q,
            "-update",
            "1",
            out,
        ]

        def done(rc):
            import shutil

            shutil.rmtree(work, ignore_errors=True)
            self.show_result(rc, out)

        self.log(self.console, f"Contact sheet {cols}×{rows} → {out}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=done,
            btn=self.btn_render,
            duration=dur,
            output=out,
            inputs=[src],
            name="Contact sheet",
        )
