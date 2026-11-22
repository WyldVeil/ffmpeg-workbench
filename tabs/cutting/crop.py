"""
tabs/cutting/crop.py  -  Manual Crop

Drag a crop rectangle over the real picture (or type exact numbers), lock
it to a common aspect ratio, or let "Detect black bars" find letterboxing
automatically.  Optionally scale the result.  Sizes are always the size as
displayed, so rotated phone videos crop correctly.
"""

from __future__ import annotations

import base64
import collections
import re
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops.crop import fit_aspect
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body

ASPECTS = [
    ("free", "Free", None),
    ("16:9", "16:9 (YouTube)", (16, 9)),
    ("9:16", "9:16 (Shorts / Reels)", (9, 16)),
    ("1:1", "1:1 (square)", (1, 1)),
    ("4:5", "4:5 (Instagram)", (4, 5)),
    ("4:3", "4:3 (classic)", (4, 3)),
    ("21:9", "21:9 (cinema)", (21, 9)),
]


def parse_cropdetect(text: str):
    """Most frequent crop=W:H:X:Y suggestion in cropdetect output, or None."""
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", text or "")
    if not found:
        return None
    (w, h, x, y), _n = collections.Counter(found).most_common(1)[0]
    return int(x), int(y), int(w), int(h)


class ManualCropTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.vid_w, self.vid_h = 0, 0
        self.duration = 0.0
        self._cx, self._cy, self._cw, self._ch = (tk.IntVar(value=0) for _ in range(4))
        self._drag = None
        self._photo = None
        self._frame_job = None
        self._frame_gen = 0
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.make_header(self, t("tab.manual_crop"), t("crop.subtitle"), icon="🔲")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], font=(MONO_FONT, 9))
        self.info_lbl.pack(anchor="w", padx=px(24))

        main = tk.Frame(body)
        main.pack(fill="both", expand=True, padx=px(20), pady=px(6))
        left = tk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(
            left,
            bg="#000000",
            width=px(560),
            height=px(315),
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_frame())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))
        self.pos_var = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(
            left, from_=0, to=1, variable=self.pos_var, command=lambda _v: self._schedule_frame()
        )
        self.slider.pack(fill="x", pady=px(4, 0))
        framestep.slider(self.slider, lambda: framestep.fps_of(self.src_row.path))

        ctrl = tk.Frame(main, width=px(290))
        ctrl.pack(side="right", fill="y", padx=px(10, 0))
        ar = tk.LabelFrame(
            ctrl, text=f"  {t('crop.aspect_ratio_section')}  ", padx=px(8), pady=px(4)
        )
        ar.pack(fill="x")
        self.aspect_var = tk.StringVar(value="free")
        for key, label, _ratio in ASPECTS:
            tk.Radiobutton(
                ar, text=label, value=key, variable=self.aspect_var, command=self._aspect_changed
            ).pack(anchor="w")
        num = tk.LabelFrame(
            ctrl, text=f"  {t('crop.crop_region_section')}  ", padx=px(8), pady=px(4)
        )
        num.pack(fill="x", pady=px(6))
        for label, var in (
            ("X:", self._cx),
            ("Y:", self._cy),
            ("Width:", self._cw),
            ("Height:", self._ch),
        ):
            row = tk.Frame(num)
            row.pack(fill="x", pady=px(1))
            tk.Label(row, text=label, width=8, anchor="e").pack(side="left")
            e = tk.Entry(row, textvariable=var, width=8, font=(MONO_FONT, 10))
            e.pack(side="left", padx=px(4))
            e.bind("<Return>", lambda _e: self._clamp_and_draw())
            e.bind("<FocusOut>", lambda _e: self._clamp_and_draw())
        bt = tk.Frame(num)
        bt.pack(fill="x", pady=px(4))
        tk.Button(bt, text="Reset", relief="flat", command=self._reset).pack(side="left")
        self.detect_btn = tk.Button(
            bt, text="Detect black bars", relief="flat", command=self._detect_bars
        )
        self.detect_btn.pack(side="left", padx=px(6))
        sc = tk.LabelFrame(
            ctrl, text=f"  {t('crop.scale_output_section')}  ", padx=px(8), pady=px(4)
        )
        sc.pack(fill="x")
        self.scale_var = tk.StringVar(value="keep")
        tk.Radiobutton(
            sc, text="Keep the cropped size", value="keep", variable=self.scale_var
        ).pack(anchor="w")
        sr = tk.Frame(sc)
        sr.pack(fill="x")
        tk.Radiobutton(sr, text="Scale to", value="scale", variable=self.scale_var).pack(
            side="left"
        )
        self.sw_var, self.sh_var = tk.StringVar(value="1080"), tk.StringVar(value="1920")
        tk.Entry(sr, textvariable=self.sw_var, width=6).pack(side="left", padx=px(2))
        tk.Label(sr, text="×").pack(side="left")
        tk.Entry(sr, textvariable=self.sh_var, width=6).pack(side="left", padx=px(2))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(
            out_lf,
            t("common.output_file"),
            save=True,
            suggest=lambda: self.suggest_output(self.src_row.path, "cropped"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text=t("crop.export_button"),
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
        self.console, csb = self.make_console(cf, height=4)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── geometry ─────────────────────────────────────────────────────────────

    def _view(self):
        """(scale, offset_x, offset_y) mapping video pixels onto the canvas."""
        cw, ch = max(self.canvas.winfo_width(), 2), max(self.canvas.winfo_height(), 2)
        if not self.vid_w:
            return 1.0, 0.0, 0.0
        s = min(cw / self.vid_w, ch / self.vid_h)
        return s, (cw - self.vid_w * s) / 2, (ch - self.vid_h * s) / 2

    def rect(self):
        return self._cx.get(), self._cy.get(), self._cw.get(), self._ch.get()

    def set_rect(self, x, y, w, h):
        w = max(16, min(int(w), self.vid_w))
        h = max(16, min(int(h), self.vid_h))
        x = max(0, min(int(x), self.vid_w - w))
        y = max(0, min(int(y), self.vid_h - h))
        for var, val in zip((self._cx, self._cy, self._cw, self._ch), (x, y, w, h), strict=False):
            var.set(val)
        self._draw()

    def _clamp_and_draw(self):
        try:
            self.set_rect(*self.rect())
        except (tk.TclError, ValueError):
            self._reset()

    # ── drawing ──────────────────────────────────────────────────────────────

    def _schedule_frame(self):
        self._draw()
        if self._frame_job is not None:
            self.after_cancel(self._frame_job)
        self._frame_job = self.after(200, self._load_frame)

    def _load_frame(self):
        self._frame_job = None
        path = self.src_row.path
        if not path or not self.vid_w:
            return
        s, _ox, _oy = self._view()
        w, h = max(2, int(self.vid_w * s)), max(2, int(self.vid_h * s))
        self._frame_gen += 1
        gen, at = self._frame_gen, float(self.pos_var.get())

        def work():
            png = ff.grab_frame_png(path, at, width=w, height=h)
            self.call_ui(self._show_frame, gen, png)

        tasks.submit(work, name="CropFrame", pool="preview")

    def _show_frame(self, gen, png):
        if gen != self._frame_gen or not png:
            return
        try:
            self._photo = tk.PhotoImage(master=self, data=base64.b64encode(png).decode("ascii"))
        except tk.TclError:
            self._photo = None
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("all")
        if not self.vid_w:
            c.create_text(
                c.winfo_width() // 2,
                c.winfo_height() // 2,
                text="Choose a video",
                fill="#777777",
                font=(UI_FONT, 11),
            )
            return
        s, ox, oy = self._view()
        if self._photo is not None:
            c.create_image(ox, oy, image=self._photo, anchor="nw")
        else:
            c.create_rectangle(ox, oy, ox + self.vid_w * s, oy + self.vid_h * s, fill="#222222")
        x, y, w, h = self.rect()
        x1, y1, x2, y2 = ox + x * s, oy + y * s, ox + (x + w) * s, oy + (y + h) * s
        X2, Y2 = ox + self.vid_w * s, oy + self.vid_h * s
        for box in ((ox, oy, X2, y1), (ox, y2, X2, Y2), (ox, y1, x1, y2), (x2, y1, X2, y2)):
            c.create_rectangle(*box, fill="#000000", stipple="gray50", outline="")
        c.create_rectangle(x1, y1, x2, y2, outline=CLR["accent"], width=px(2))
        for i in (1, 2):
            c.create_line(
                x1 + (x2 - x1) * i / 3,
                y1,
                x1 + (x2 - x1) * i / 3,
                y2,
                fill="#FFFFFF",
                dash=px(2, 4),
                width=px(1),
            )
            c.create_line(
                x1,
                y1 + (y2 - y1) * i / 3,
                x2,
                y1 + (y2 - y1) * i / 3,
                fill="#FFFFFF",
                dash=px(2, 4),
                width=px(1),
            )
        for hx, hy in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
            c.create_rectangle(
                hx - px(6),
                hy - px(6),
                hx + px(6),
                hy + px(6),
                fill=CLR["accent"],
                outline="white",
                width=px(1),
            )
        c.create_text(
            (x1 + x2) / 2,
            max(y1 - px(10), px(8)),
            text=f"{w}×{h}",
            fill=CLR["accent"],
            font=(MONO_FONT, 9, "bold"),
        )

    # ── mouse ────────────────────────────────────────────────────────────────

    def _press(self, ev):
        if not self.vid_w:
            return
        s, ox, oy = self._view()
        x, y, w, h = self.rect()
        x1, y1, x2, y2 = ox + x * s, oy + y * s, ox + (x + w) * s, oy + (y + h) * s

        def near(a, b):
            return abs(a - b) < px(10)

        corners = {"tl": (x1, y1), "tr": (x2, y1), "bl": (x1, y2), "br": (x2, y2)}
        kind = next(
            (k for k, (cx, cy) in corners.items() if near(ev.x, cx) and near(ev.y, cy)), None
        )
        if kind is None and x1 < ev.x < x2 and y1 < ev.y < y2:
            kind = "move"
        self._drag = (kind, ev.x, ev.y, (x, y, w, h)) if kind else None

    def _move(self, ev):
        if not self._drag:
            return
        kind, sx, sy, (x, y, w, h) = self._drag
        s, _ox, _oy = self._view()
        dx, dy = (ev.x - sx) / s, (ev.y - sy) / s
        ratio = next((r for k, _l, r in ASPECTS if k == self.aspect_var.get()), None)
        if kind == "move":
            self.set_rect(x + dx, y + dy, w, h)
            return
        left, top, right, bottom = x, y, x + w, y + h
        if "l" in kind:
            left = min(max(0, left + dx), right - 16)
        if "r" in kind:
            right = max(min(self.vid_w, right + dx), left + 16)
        if "t" in kind:
            top = min(max(0, top + dy), bottom - 16)
        if "b" in kind:
            bottom = max(min(self.vid_h, bottom + dy), top + 16)
        nw, nh = right - left, bottom - top
        if ratio:
            nh = nw * ratio[1] / ratio[0]
            if "t" in kind:
                top = bottom - nh
        self.set_rect(left, top, nw, nh)

    # ── actions ──────────────────────────────────────────────────────────────

    def _source_changed(self, path):
        info = ff.try_probe(path)
        if info is None or not info.has_video:
            self.vid_w = self.vid_h = 0
            self.info_lbl.config(text="Not a readable video.")
            self._draw()
            return
        self.vid_w, self.vid_h = info.require_video().display_size
        self.duration = info.duration
        self.info_lbl.config(
            text=f"{ff.format_time(self.duration)}  ·  {self.vid_w}×{self.vid_h}"
            + (
                f"  (rotated {info.require_video().rotation}°)"
                if info.require_video().rotation
                else ""
            )
        )
        self.slider.configure(to=max(self.duration, 0.01))
        self.pos_var.set(min(self.duration * 0.25, 30))
        self._photo = None
        self._reset()
        self._schedule_frame()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "cropped"))

    def _reset(self):
        self.aspect_var.set("free")
        if self.vid_w:
            self.set_rect(0, 0, self.vid_w, self.vid_h)

    def _aspect_changed(self):
        ratio = next((r for k, _l, r in ASPECTS if k == self.aspect_var.get()), None)
        if ratio and self.vid_w:
            self.set_rect(*fit_aspect(self.vid_w, self.vid_h, *ratio))

    def _detect_bars(self):
        path = self.src_row.path
        if not path or not self.vid_w:
            return
        start = max(0.0, min(self.duration * 0.1, 30.0))
        cmd = [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-ss",
            ff.ts(start),
            "-t",
            "20",
            "-i",
            path,
            "-an",
            "-sn",
            "-vf",
            "cropdetect=limit=24:round=2:reset=0",
            "-f",
            "null",
            "-",
        ]
        self.detect_btn.config(state="disabled", text="Detecting…")

        def work():
            return parse_cropdetect(ff.capture(cmd, timeout=120).stderr)

        def done(found):
            self.detect_btn.config(state="normal", text="Detect black bars")
            if not found:
                messagebox.showinfo(t("common.warning"), "No black bars found.", parent=self)
                return
            self.aspect_var.set("free")
            self.set_rect(*found)
            self.log(
                self.console,
                f"Black bars detected: picture is {found[2]}×{found[3]} at "
                f"({found[0]},{found[1]}).",
            )

        tasks.submit(work, on_done=done, on_error=lambda _e: done(None), name="CropDetect")

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        x, y, w, h = self.rect()
        w, h = w // 2 * 2, h // 2 * 2
        chain = f"crop={w}:{h}:{x}:{y}"
        if self.scale_var.get() == "scale":
            try:
                sw = int(common.number(self.sw_var.get(), "Width", 16, 8192)) // 2 * 2
                sh = int(common.number(self.sh_var.get(), "Height", 16, 8192)) // 2 * 2
            except ValueError as exc:
                messagebox.showwarning(t("common.warning"), str(exc), parent=self)
                return
            chain += (
                f",scale={sw}:{sh}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2"
            )
        chain += ",setsar=1"
        out = common.output_for(self, self.out_row, src, "cropped")
        if not out:
            return
        self.log(self.console, f"Cropping to {w}×{h} at ({x},{y})")
        common.render(
            self,
            src=src,
            info=info,
            out=out,
            chain=chain,
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            btn_label=t("crop.export_button"),
            name="Manual Crop",
        )
