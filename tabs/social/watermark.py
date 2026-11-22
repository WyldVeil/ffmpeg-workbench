"""
tabs/social/watermark.py  -  Watermarker (text)

Burn a text watermark: any corner, centre or a custom spot, rotated if you
like, or moving - bouncing around like the DVD logo, or hopping to a new
place every few seconds.  Show it all the time or only at chosen times,
with soft fades.  Rendered by libass (core/ass.py), so any text works and
sizes scale with the video.
"""

from __future__ import annotations

import os
import random
import shutil
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from core import ass
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

PLACES = {
    "tl": "Top left",
    "tc": "Top centre",
    "tr": "Top right",
    "ml": "Middle left",
    "center": "Centre",
    "mr": "Middle right",
    "bl": "Bottom left",
    "bc": "Bottom centre",
    "br": "Bottom right",
    "custom": "Custom spot (%)",
    "bounce": "Bouncing (DVD style)",
    "jump": "Jumping around",
}
_ANCHOR = {
    "tl": (7, 0, 0),
    "tc": (8, 0.5, 0),
    "tr": (9, 1, 0),
    "ml": (4, 0, 0.5),
    "center": (5, 0.5, 0.5),
    "mr": (6, 1, 0.5),
    "bl": (1, 0, 1),
    "bc": (2, 0.5, 1),
    "br": (3, 1, 1),
}


def parse_ranges(text: str) -> list:
    """'0-5, 20-25.5, 1:00-1:10' -> [(0, 5), (20, 25.5), (60, 70)]."""
    out = []
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        a, sep, b = part.partition("-")
        if not sep:
            raise ValueError(f"'{part}' is not a range like 5-10.")
        start, end = ff.parse_time(a.strip()), ff.parse_time(b.strip())
        if end <= start:
            raise ValueError(f"In '{part}' the end must be after the start.")
        out.append((start, end))
    return sorted(out)


def anchor_xy(key: str, w: int, h: int, margin: float) -> tuple:
    an, fx, fy = _ANCHOR[key]
    x = margin + (w - 2 * margin) * fx
    y = margin + (h - 2 * margin) * fy
    return an, x, y


def bounce_path(duration: float, w: int, h: int, tw: float, th: float, speed: float, seed: int = 3):
    """[(t0, t1, x0, y0, x1, y1)] straight segments of a box bouncing off the edges (top-left coords)."""
    rng = random.Random(seed)
    tw, th = min(tw, w * 0.9), min(th, h * 0.9)
    x, y = rng.uniform(0, max(w - tw, 1)), rng.uniform(0, max(h - th, 1))
    vx, vy = speed * 0.8, speed * 0.6
    tm, segs = 0.0, []
    while tm < duration and len(segs) < 5000:
        tx = ((w - tw - x) / vx if vx > 0 else -x / vx) if vx else float("inf")
        ty = ((h - th - y) / vy if vy > 0 else -y / vy) if vy else float("inf")
        dt = max(min(tx, ty, duration - tm), 0.001)
        nx, ny = x + vx * dt, y + vy * dt
        segs.append((tm, tm + dt, x, y, nx, ny))
        x, y, tm = nx, ny, tm + dt
        if abs(dt - tx) < 1e-6:
            vx = -vx
        if abs(dt - ty) < 1e-6:
            vy = -vy
    return segs


def build_script(
    *,
    text,
    w,
    h,
    duration,
    place,
    font="Montserrat",
    size=64,
    colour="#FFFFFF",
    opacity=1.0,
    bold=True,
    italic=False,
    shadow=True,
    outline=True,
    angle=0.0,
    custom=(50.0, 50.0),
    every=3.0,
    random_every=None,
    ranges=None,
    fade=True,
    speed=180.0,
    seed=7,
) -> ass.Script:
    sc = ass.Script(w, h)
    u = min(w, h) / 1080
    px = max(8, round(size * u))
    sc.style(
        ass.Style(
            "WM",
            font=font,
            size=px,
            colour=colour,
            bold=bold,
            italic=italic,
            outline=(2.5 * u if outline else 0),
            shadow=(2 * u if shadow else 0),
            back_alpha=0.5,
        )
    )
    body = ass.escape(text)
    alpha = f"\\alpha{ass.alpha_tag(1 - opacity)}"
    windows = ranges or [(0.0, duration)]
    fade_tag = "\\fad(300,300)" if fade else ""
    margin = 30 * u
    tw, th = ass.text_width(text, px, bold=bold), px * 1.2

    def add(a, b, tags):
        for ra, rb in windows:
            s, e = max(a, ra), min(b, rb)
            if e - s > 0.04:
                fades = fade_tag if (s == ra or e == rb) and fade else ""
                sc.add(s, e, f"{{{tags}{alpha}{fades}}}{body}", "WM")

    rot = f"\\frz{-angle:g}" if angle else ""
    if place == "bounce":
        for a, b, x0, y0, x1, y1 in bounce_path(duration, w, h, tw, th, speed * u, seed):
            add(a, b, f"\\an7\\move({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})")
    elif place == "jump":
        rng = random.Random(seed)
        spots = [k for k in _ANCHOR if k != "center"]
        tm, last = 0.0, None
        while tm < duration:
            step = rng.uniform(*random_every) if random_every else every
            key = rng.choice([k for k in spots if k != last])
            last = key
            an, x, y = anchor_xy(key, w, h, margin)
            add(tm, min(tm + step, duration), f"\\an{an}\\pos({x:.0f},{y:.0f}){rot}")
            tm += step
    elif place == "custom":
        x, y = w * custom[0] / 100, h * custom[1] / 100
        add(0, duration, f"\\an5\\pos({x:.0f},{y:.0f}){rot}")
    else:
        an, x, y = anchor_xy(place, w, h, margin)
        add(0, duration, f"\\an{an}\\pos({x:.0f},{y:.0f}){rot}")
    return sc


class TextBurnerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.colour = "#FFFFFF"
        self._info = None
        self._job = None
        self._pdir = None
        self._pn = 0
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.watermarker"),
            "Burn a text watermark - fixed, rotated, "
            "bouncing or jumping, all the time or only at chosen moments.",
            icon="©",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.compare = CompareView(body, 400, 225, after_label="With watermark")
        self.compare.pack(pady=px(4))

        ty = tk.LabelFrame(body, text="  Text  ", padx=px(12), pady=px(6))
        ty.pack(fill="x", padx=px(20), pady=px(4))
        self.text_var = tk.StringVar(value="© My Channel")
        tk.Entry(ty, textvariable=self.text_var, width=40).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        self.font_var = tk.StringVar(value="Montserrat")
        ttk.Combobox(
            ty, textvariable=self.font_var, values=ass.families(), state="readonly", width=16
        ).grid(row=1, column=0, sticky="w", pady=px(4))
        self.size_var = tk.IntVar(value=48)
        tk.Scale(
            ty,
            variable=self.size_var,
            from_=12,
            to=200,
            orient="horizontal",
            length=px(160),
            label="Size",
            command=lambda _v: self._changed(),
        ).grid(row=1, column=1, sticky="w")
        self.opacity_var = tk.DoubleVar(value=0.8)
        tk.Scale(
            ty,
            variable=self.opacity_var,
            from_=0.1,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=px(140),
            label="Opacity",
            command=lambda _v: self._changed(),
        ).grid(row=1, column=2, sticky="w")
        flags = tk.Frame(ty)
        flags.grid(row=2, column=0, columnspan=3, sticky="w")
        self.bold_var, self.italic_var = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        self.shadow_var, self.outline_var = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        for text, var in (
            ("Bold", self.bold_var),
            ("Italic", self.italic_var),
            ("Shadow", self.shadow_var),
            ("Outline", self.outline_var),
        ):
            tk.Checkbutton(flags, text=text, variable=var, command=self._changed).pack(side="left")
        self.col_btn = tk.Button(
            flags, text="Colour…", relief="flat", bg=self.colour, command=self._pick
        )
        self.col_btn.pack(side="left", padx=px(8))

        pos = tk.LabelFrame(body, text="  Position & motion  ", padx=px(12), pady=px(6))
        pos.pack(fill="x", padx=px(20), pady=px(4))
        self.place_var = tk.StringVar(value=PLACES["br"])
        pb = ttk.Combobox(
            pos,
            textvariable=self.place_var,
            values=list(PLACES.values()),
            state="readonly",
            width=24,
        )
        pb.grid(row=0, column=0, sticky="w")
        pb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.angle_var = tk.StringVar(value="0")
        self.cx_var, self.cy_var = tk.StringVar(value="50"), tk.StringVar(value="50")
        self.every_var, self.rand_var = tk.StringVar(value="3"), tk.BooleanVar(value=False)
        row = tk.Frame(pos)
        row.grid(row=1, column=0, sticky="w", pady=px(4))
        for label, var, w in (
            ("Angle°", self.angle_var, 5),
            ("X %", self.cx_var, 5),
            ("Y %", self.cy_var, 5),
            ("Jump every (s)", self.every_var, 5),
        ):
            tk.Label(row, text=label).pack(side="left", padx=px(6, 2))
            tk.Entry(row, textvariable=var, width=w).pack(side="left")
            var.trace_add("write", lambda *_a: self._changed())
        tk.Checkbutton(
            row, text="random (1-2× that)", variable=self.rand_var, command=self._changed
        ).pack(side="left", padx=px(6))

        tm = tk.LabelFrame(body, text="  When  ", padx=px(12), pady=px(6))
        tm.pack(fill="x", padx=px(20), pady=px(4))
        self.always_var = tk.BooleanVar(value=True)
        tk.Radiobutton(
            tm, text="The whole video", value=True, variable=self.always_var, command=self._changed
        ).grid(row=0, column=0, sticky="w")
        tk.Radiobutton(
            tm, text="Only at:", value=False, variable=self.always_var, command=self._changed
        ).grid(row=1, column=0, sticky="w")
        self.ranges_var = tk.StringVar(value="0-5, 20-25")
        tk.Entry(tm, textvariable=self.ranges_var, width=30).grid(row=1, column=1, sticky="w")
        tk.Label(tm, text="e.g. 0-5, 1:20-1:30", fg=CLR["fgdim"], font=(UI_FONT, 8)).grid(
            row=1, column=2
        )
        self.fade_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            tm, text="Fade in and out", variable=self.fade_var, command=self._changed
        ).grid(row=2, column=0, sticky="w")
        for var in (self.text_var, self.font_var, self.ranges_var):
            var.trace_add("write", lambda *_a: self._changed())

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(
            out_lf,
            t("common.output_file"),
            save=True,
            suggest=lambda: self.suggest_output(self.src_row.path, "watermarked"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="©  BURN WATERMARK",
            font=(UI_FONT, 12, "bold"),
            bg="#E91E63",
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

    def _pick(self):
        res = colorchooser.askcolor(self.colour, parent=self)
        if res and res[1]:
            self.colour = res[1].upper()
            self.col_btn.config(bg=self.colour)
            self._changed()

    def script(self, w, h, duration) -> ass.Script:
        place = next((k for k, v in PLACES.items() if v == self.place_var.get()), "br")
        every = common.number(self.every_var.get() or "3", "Jump every", 0.2, 3600)
        return build_script(
            text=self.text_var.get(),
            w=w,
            h=h,
            duration=duration,
            place=place,
            font=self.font_var.get(),
            size=int(self.size_var.get()),
            colour=self.colour,
            opacity=float(self.opacity_var.get()),
            bold=self.bold_var.get(),
            italic=self.italic_var.get(),
            shadow=self.shadow_var.get(),
            outline=self.outline_var.get(),
            angle=common.number(self.angle_var.get() or "0", "Angle", -360, 360),
            custom=(
                common.number(self.cx_var.get() or "50", "X %", 0, 100),
                common.number(self.cy_var.get() or "50", "Y %", 0, 100),
            ),
            every=every,
            random_every=(every, every * 2) if self.rand_var.get() else None,
            ranges=None if self.always_var.get() else parse_ranges(self.ranges_var.get()),
            fade=self.fade_var.get(),
        )

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "watermarked"))

    def _changed(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._refresh)

    def _refresh(self):
        self._job = None
        info = self._info
        if info is None or not info.has_video or not self.text_var.get().strip():
            return
        try:
            w, h = common.even_size(info)
            sc = self.script(w, h, info.duration)
        except ValueError:
            return
        if self._pdir is None:
            from core.paths import temp_dir

            self._pdir = temp_dir("wm_preview_")
        self._pn += 1
        path = sc.write(os.path.join(self._pdir, f"p{self._pn}.ass"))
        self.compare.set_filter(ass.subtitles_filter(path), keep_time=True)

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        if not self.text_var.get().strip():
            messagebox.showwarning(t("common.warning"), "Type the watermark text.", parent=self)
            return
        w, h = common.even_size(info)
        try:
            sc = self.script(w, h, info.duration)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "watermarked")
        if not out:
            return
        from core.paths import temp_dir

        work = temp_dir("wm_")
        path = sc.write(os.path.join(work, "watermark.ass"))

        def done(rc):
            shutil.rmtree(work, ignore_errors=True)
            self.show_result(rc, out)

        common.render(
            self,
            src=src,
            info=info,
            out=out,
            chain=ass.subtitles_filter(path),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            btn_label="©  BURN WATERMARK",
            name="Watermarker",
            on_done=done,
        )

    def load_file(self, path=None):
        if path:
            self.src_var.set(path)
            return True
        return False

    def on_app_closing(self):
        if self._pdir:
            shutil.rmtree(self._pdir, ignore_errors=True)
