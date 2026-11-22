"""
tabs/social/slideshow.py  -  Slideshow Maker

Photos to video: smooth Ken Burns motion (zoom in/out, pans, random), fill /
fit / blurred-background framing for mixed portrait and landscape photos,
xfade transitions, per-slide captions (libass, any bundled font) and
background music that is looped, trimmed and faded to the slideshow.

Each slide is rendered once (pre-upscaled zoompan for jitter-free motion),
then everything is joined in one pass with the transitions and captions.
"""

from __future__ import annotations

import os
import random
import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from core import ass
from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body

IMAGE_EXTS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
    ".gif",
)
TRANSITIONS = {
    "fade": "Crossfade",
    "dissolve": "Dissolve",
    "fadeblack": "Fade through black",
    "fadewhite": "Fade through white",
    "slideleft": "Slide left",
    "slideright": "Slide right",
    "slideup": "Slide up",
    "smoothleft": "Smooth left",
    "wipeleft": "Wipe left",
    "circleopen": "Circle open",
    "radial": "Radial",
    "zoomin": "Zoom in",
    "pixelize": "Pixelize",
    "random": "Random (varies)",
    "cut": "None (cut)",
}
MOTIONS = {
    "zoom_in": "Zoom in",
    "zoom_out": "Zoom out",
    "pan_right": "Pan right",
    "pan_left": "Pan left",
    "pan_up": "Pan up",
    "pan_down": "Pan down",
    "random": "Random (varies per slide)",
    "static": "None (still)",
}
FITS = {
    "fill": "Fill the frame (crop edges)",
    "blur": "Fit with blurred background",
    "bars": "Fit with black bars",
}
RESOLUTIONS = {
    "1920×1080 (YouTube)": (1920, 1080),
    "3840×2160 (4K)": (3840, 2160),
    "1280×720 (HD)": (1280, 720),
    "1080×1920 (Reels / Shorts)": (1080, 1920),
    "1080×1080 (square)": (1080, 1080),
}
CAPTION_POS = {"bottom": "Bottom", "top": "Top", "lower_third": "Lower third (left)"}


@dataclass
class Slide:
    path: str
    caption: str = ""


def natural_key(path: str):
    return [
        int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", os.path.basename(path))
    ]


def motion_filter(motion: str, w: int, h: int, fps: str, frames: int, zoom: float = 1.2) -> str:
    """Ken Burns on ONE input frame: expressions use the output frame number."""
    d = max(frames - 1, 1)
    p = f"(on/{d})"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        z, x, y = f"1+{zoom - 1:.4f}*{p}", cx, cy
    elif motion == "zoom_out":
        z, x, y = f"{zoom:.4f}-{zoom - 1:.4f}*{p}", cx, cy
    elif motion == "pan_right":
        z, x, y = f"{zoom:.4f}", f"(iw-iw/zoom)*{p}", cy
    elif motion == "pan_left":
        z, x, y = f"{zoom:.4f}", f"(iw-iw/zoom)*(1-{p})", cy
    elif motion == "pan_down":
        z, x, y = f"{zoom:.4f}", cx, f"(ih-ih/zoom)*{p}"
    elif motion == "pan_up":
        z, x, y = f"{zoom:.4f}", cx, f"(ih-ih/zoom)*(1-{p})"
    else:
        z, x, y = "1", "0", "0"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}"


def frame_filter(fit: str, w: int, h: int) -> str:
    """Frame the photo at 2x the output size (smooth zoompan sub-pixel steps)."""
    W, H = 2 * w, 2 * h
    if fit == "blur":
        return (
            f"split[a][b];[a]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
            f"crop={W // 8}:{H // 8},gblur=sigma=6,eq=brightness=-0.08,scale={W}:{H}[bg];"
            f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    if fit == "bars":
        return (
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"


def timeline(n: int, dur: float, trans: float) -> tuple:
    """(start time of each slide, total length) with overlapping transitions."""
    starts = [i * (dur - trans) for i in range(n)]
    return starts, (starts[-1] + dur) if n else 0.0


class SlideshowMakerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.slides: list = []
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.slideshow_maker"),
            "Photos to video with motion, transitions, captions and music.",
            icon="🖼",
        )
        body = scroll_body(self)
        top = tk.Frame(body)
        top.pack(fill="x", padx=px(20), pady=px(10, 4))

        left = tk.Frame(top)
        left.pack(side="left", fill="both", expand=True)
        cols = ("n", "file", "caption")
        self.tree = ttk.Treeview(
            left, columns=cols, show="headings", height=10, selectmode="browse"
        )
        for c, label, w in (("n", "#", 40), ("file", "Photo", 260), ("caption", "Caption", 260)):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=px(w), anchor="w", stretch=c != "n")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.config(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._selected())

        btns = tk.Frame(top)
        btns.pack(side="left", fill="y", padx=px(8, 0))
        for text, cmd in (
            ("➕ Add photos", self._add_images),
            ("📁 Add folder", self._add_folder),
            ("⬆ Up", lambda: self._move(-1)),
            ("⬇ Down", lambda: self._move(1)),
            ("🗑 Remove", self._remove),
            ("🧹 Clear", self._clear),
            ("🔤 Sort by name", self._sort),
        ):
            tk.Button(
                btns, text=text, relief="flat", anchor="w", width=14, cursor="hand2", command=cmd
            ).pack(fill="x", pady=px(1))
        self.count_lbl = tk.Label(btns, text="0 slides", fg=CLR["fgdim"])
        self.count_lbl.pack(pady=px(6, 0))

        cap = tk.Frame(body)
        cap.pack(fill="x", padx=px(20), pady=px(2))
        tk.Label(cap, text="Caption for the selected photo:").pack(side="left")
        self.cap_var = tk.StringVar()
        e = tk.Entry(cap, textvariable=self.cap_var, width=50)
        e.pack(side="left", padx=px(6), fill="x", expand=True)
        e.bind("<Return>", lambda _e: self._save_caption())
        tk.Button(cap, text="Set", relief="flat", command=self._save_caption).pack(side="left")

        opt = tk.LabelFrame(body, text="  Slideshow  ", padx=px(10), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(6))

        def row(r, label, *widgets):
            tk.Label(opt, text=label, anchor="w", width=16).grid(
                row=r, column=0, sticky="w", pady=px(2)
            )
            for i, w in enumerate(widgets):
                w.grid(row=r, column=1 + i, sticky="w", padx=px(0, 8), pady=px(2))

        self.dur_var = tk.StringVar(value="4")
        self.trans_dur_var = tk.StringVar(value="1.0")
        row(
            0,
            "Seconds per photo:",
            tk.Entry(opt, textvariable=self.dur_var, width=6),
            tk.Label(opt, text="Transition (s):"),
            tk.Entry(opt, textvariable=self.trans_dur_var, width=6),
        )
        self.trans_var = tk.StringVar(value=TRANSITIONS["fade"])
        self.motion_var = tk.StringVar(value=MOTIONS["random"])
        row(
            1,
            "Transition:",
            ttk.Combobox(
                opt,
                textvariable=self.trans_var,
                values=list(TRANSITIONS.values()),
                state="readonly",
                width=22,
            ),
            tk.Label(opt, text="Motion:"),
            ttk.Combobox(
                opt,
                textvariable=self.motion_var,
                values=list(MOTIONS.values()),
                state="readonly",
                width=24,
            ),
        )
        self.fit_var = tk.StringVar(value=FITS["blur"])
        self.zoom_var = tk.DoubleVar(value=1.2)
        row(
            2,
            "Framing:",
            ttk.Combobox(
                opt,
                textvariable=self.fit_var,
                values=list(FITS.values()),
                state="readonly",
                width=28,
            ),
            tk.Label(opt, text="Motion amount:"),
            tk.Scale(
                opt,
                variable=self.zoom_var,
                from_=1.05,
                to=1.6,
                resolution=0.05,
                orient="horizontal",
                length=px(120),
            ),
        )
        self.res_var = tk.StringVar(value=next(iter(RESOLUTIONS)))
        self.fps_var = tk.StringVar(value="30")
        row(
            3,
            "Size:",
            ttk.Combobox(
                opt, textvariable=self.res_var, values=list(RESOLUTIONS), state="readonly", width=28
            ),
            tk.Label(opt, text="FPS:"),
            ttk.Combobox(
                opt,
                textvariable=self.fps_var,
                values=["24", "25", "30", "50", "60"],
                state="readonly",
                width=5,
            ),
        )
        self.cap_on = tk.BooleanVar(value=True)
        self.cap_font = tk.StringVar(value="Montserrat")
        self.cap_pos = tk.StringVar(value=CAPTION_POS["bottom"])
        row(
            4,
            "Captions:",
            tk.Checkbutton(opt, text="Show", variable=self.cap_on),
            ttk.Combobox(
                opt, textvariable=self.cap_font, values=ass.families(), state="readonly", width=14
            ),
            ttk.Combobox(
                opt,
                textvariable=self.cap_pos,
                values=list(CAPTION_POS.values()),
                state="readonly",
                width=16,
            ),
        )
        self.music_row = FileRow(opt, "Music:", kind="audio", label_width=16, width=40)
        self.music_row.grid(row=5, column=0, columnspan=5, sticky="we")
        self.music_vol = tk.DoubleVar(value=0.9)
        row(
            6,
            "Music volume:",
            tk.Scale(
                opt,
                variable=self.music_vol,
                from_=0.1,
                to=1.5,
                resolution=0.05,
                orient="horizontal",
                length=px(140),
            ),
        )

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True, suggest=self._suggest)
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="🎞  CREATE SLIDESHOW",
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

    # ── slide list ───────────────────────────────────────────────────────────

    def _refresh_list(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.slides):
            self.tree.insert(
                "", "end", iid=str(i), values=(i + 1, os.path.basename(s.path), s.caption)
            )
        if select is not None and 0 <= select < len(self.slides):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))
        self.count_lbl.config(
            text=f"{len(self.slides)} slide{'s' if len(self.slides) != 1 else ''}"
        )

    def _index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add_paths(self, paths):
        new = [
            p for p in paths if os.path.splitext(p)[1].lower() in IMAGE_EXTS and os.path.isfile(p)
        ]
        self.slides += [Slide(p) for p in new]
        self._refresh_list(len(self.slides) - 1 if new else None)
        return len(new)

    def load_file(self, path):
        return self.add_paths([path]) > 0

    def _add_images(self):
        self.add_paths(self.pick_sources("image"))

    def _add_folder(self):
        from tabs.kit import dialogs

        d = dialogs.ask_directory(parent=self)
        if d:
            files = sorted((os.path.join(d, f) for f in os.listdir(d)), key=natural_key)
            if not self.add_paths(files):
                messagebox.showinfo(
                    t("common.warning"), "No photos found in that folder.", parent=self
                )

    def _move(self, delta):
        i = self._index()
        if i is None or not 0 <= i + delta < len(self.slides):
            return
        self.slides[i], self.slides[i + delta] = self.slides[i + delta], self.slides[i]
        self._refresh_list(i + delta)

    def _remove(self):
        i = self._index()
        if i is not None:
            del self.slides[i]
            self._refresh_list(min(i, len(self.slides) - 1))

    def _clear(self):
        self.slides.clear()
        self._refresh_list()

    def _sort(self):
        self.slides.sort(key=lambda s: natural_key(s.path))
        self._refresh_list()

    def _selected(self):
        i = self._index()
        self.cap_var.set(self.slides[i].caption if i is not None else "")

    def _save_caption(self):
        i = self._index()
        if i is not None:
            self.slides[i].caption = self.cap_var.get()
            self._refresh_list(i)

    def _suggest(self):
        if self.slides:
            folder = os.path.dirname(self.slides[0].path)
            return os.path.join(folder, "slideshow.mp4")
        from core.paths import user_videos_dir

        return os.path.join(user_videos_dir(), "slideshow.mp4")

    # ── render ───────────────────────────────────────────────────────────────

    def _key(self, table, var, default):
        return next((k for k, v in table.items() if v == var.get()), default)

    def _render(self):
        if len(self.slides) < 1:
            messagebox.showwarning(t("common.warning"), "Add some photos first.", parent=self)
            return
        try:
            dur = common.number(self.dur_var.get(), "Seconds per photo", 0.5, 600)
            trans_key = self._key(TRANSITIONS, self.trans_var, "fade")
            td = (
                0.0
                if trans_key == "cut" or len(self.slides) < 2
                else common.number(self.trans_dur_var.get(), "Transition", 0.1, 10)
            )
            if td >= dur / 2:
                raise ValueError(
                    f"The transition ({td:g}s) must be shorter than half the time per "
                    f"photo ({dur / 2:g}s)."
                )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        missing = [s.path for s in self.slides if not os.path.isfile(s.path)]
        if missing:
            messagebox.showwarning(
                t("common.warning"), f"Missing photo:\n{missing[0]}", parent=self
            )
            return
        out = self.out_row.path or self._suggest()
        out = self.confirm_output(
            out,
            [s.path for s in self.slides] + ([self.music_row.path] if self.music_row.path else []),
        )
        if not out:
            return
        self.out_row.set(out)
        w, h = RESOLUTIONS.get(self.res_var.get(), (1920, 1080))
        fps = self.fps_var.get() or "30"
        frames = max(2, round(dur * float(fps)))
        fit = self._key(FITS, self.fit_var, "blur")
        motion = self._key(MOTIONS, self.motion_var, "random")
        zoom = float(self.zoom_var.get())
        rng = random.Random(len(self.slides))
        choices = [m for m in MOTIONS if m not in ("random", "static")]
        motions = [rng.choice(choices) if motion == "random" else motion for _ in self.slides]
        xf_choices = [k for k in TRANSITIONS if k not in ("random", "cut")]
        xfades = [
            rng.choice(xf_choices) if trans_key == "random" else trans_key for _ in self.slides[1:]
        ]
        starts, total = timeline(len(self.slides), dur, td)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        slides = list(self.slides)
        music = self.music_row.path
        captions = self.cap_on.get() and any(s.caption.strip() for s in slides)
        cap_font, cap_pos = self.cap_font.get(), self._key(CAPTION_POS, self.cap_pos, "bottom")
        volume = float(self.music_vol.get())

        def work(_p, _c, ctx):
            tmp = ctx.tempdir()
            clips = []
            n = len(slides)
            for i, s in enumerate(slides):
                ctx.log(f"Slide {i + 1}/{n}: {os.path.basename(s.path)}")
                clip = os.path.join(tmp, f"s{i:04d}.mp4")
                graph = (
                    f"[0:v]{frame_filter(fit, w, h)},"
                    f"{motion_filter(motions[i], w, h, fps, frames, zoom)},format=yuv420p[v]"
                )
                ctx.span(0.7 * i / n, 0.7 * (i + 1) / n).run(
                    [
                        ff.ffmpeg_path(),
                        "-y",
                        "-i",
                        s.path,
                        "-filter_complex",
                        graph,
                        "-map",
                        "[v]",
                        "-frames:v",
                        str(frames),
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "14",
                        "-pix_fmt",
                        "yuv420p",
                        clip,
                    ],
                    duration=dur,
                )
                clips.append(clip)
            cmd = [ff.ffmpeg_path(), "-y", *venc.pre_input]
            for c in clips:
                cmd += ["-i", c]
            parts, last = [], "[0:v]"
            if n == 1:
                parts.append("[0:v]null[xf]")
            elif td <= 0:
                parts.append("".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[xf]")
            else:
                for i in range(1, n):
                    label = "[xf]" if i == n - 1 else f"[x{i}]"
                    parts.append(
                        f"{last}[{i}:v]xfade=transition={xfades[i - 1]}:duration={td:.3f}:"
                        f"offset={starts[i]:.3f}{label}"
                    )
                    last = label
            vout = "[xf]"
            if captions:
                sc = ass.Script(w, h)
                u = min(w, h) / 1080
                sc.style(
                    ass.Style(
                        "Cap",
                        font=cap_font,
                        size=round(46 * u),
                        bold=True,
                        outline=2.5 * u,
                        shadow=1.5 * u,
                        back_alpha=0.5,
                        alignment={"top": 8, "lower_third": 1}.get(cap_pos, 2),
                        margin_l=round(60 * u),
                        margin_r=round(60 * u),
                        margin_v=round((h * 0.12) if cap_pos == "lower_third" else 50 * u),
                    )
                )
                for i, s in enumerate(slides):
                    if s.caption.strip():
                        a = starts[i] + (td / 2 if i else 0)
                        b = starts[i] + dur - (td / 2 if i < n - 1 else 0)
                        sc.add(a, b, f"{{\\fad(300,300)}}{ass.escape(s.caption)}", "Cap")
                path = sc.write(os.path.join(tmp, "captions.ass"))
                parts.append(f"[xf]{ass.subtitles_filter(path)}[cv]")
                vout = "[cv]"
            if venc.filter_suffix:
                parts.append(f"{vout}{venc.filter_suffix}[venc]")
                vout = "[venc]"
            amap = []
            if music and os.path.isfile(music):
                cmd += ["-stream_loop", "-1", "-i", music]
                fo = min(3.0, total / 4)
                parts.append(
                    f"[{n}:a:0]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={volume:.2f},"
                    f"afade=t=in:st=0:d=0.5,afade=t=out:st={total - fo:.3f}:d={fo:.2f}[a]"
                )
                amap = [
                    "-map",
                    "[a]",
                    *enc.audio(enc.default_audio_for(container), container=container),
                ]
            cmd += [
                "-filter_complex",
                ";".join(parts),
                "-map",
                vout,
                *amap,
                *venc.args,
                "-t",
                f"{total:.3f}",
                ctx.output(),
            ]
            ctx.span(0.7, 1.0).run(cmd, duration=total)
            return 0

        self.log(
            self.console, f"{len(slides)} photos · {ff.format_time(total)} · {w}×{h} @ {fps} fps"
        )
        common.queue_job(
            self,
            name="Slideshow",
            out=out,
            work=work,
            btn=self.btn_render,
            btn_label="🎞  CREATE SLIDESHOW",
            console=self.console,
            duration=total,
            inputs=[s.path for s in slides] + ([music] if music else []),
        )
