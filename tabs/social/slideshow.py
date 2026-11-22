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
from core.encoders import VideoEncode
from core.i18n import t
from core.ops import slideshow as ss
from core.ops.slideshow import timeline
from core.queue_manager import TaskContext
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, SaveRow, scroll_body

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


@dataclass(frozen=True)
class _Slideshow:
    """Each photo into a clip, then one pass joining them (transitions,
    captions, music)."""

    paths: list[str]
    captions: list[str]  # empty: no captions
    motions: list[str]
    xfades: list[str]
    look: ss.Look
    timing: tuple[list[float], float, float]  # starts, seconds per photo, transition
    total: float
    venc: VideoEncode
    container: str
    music: str
    volume: float
    caption_style: tuple[str, str]  # font, position

    def run(self, _p: object, _c: object, ctx: TaskContext) -> int:
        tmp = ctx.tempdir()
        n = len(self.paths)
        cmd = [ff.ffmpeg_path(), "-y", *self.venc.pre_input]
        for i, path in enumerate(self.paths):
            ctx.log(f"Slide {i + 1}/{n}: {os.path.basename(path)}")
            clip = os.path.join(tmp, f"s{i:04d}.mp4")
            ctx.span(0.7 * i / n, 0.7 * (i + 1) / n).run(
                ss.slide_cmd(path, self.motions[i], self.look, clip), duration=self.timing[1]
            )
            cmd += ["-i", clip]
        starts, _dur, td = self.timing
        parts = ss.join_graph(n, td, self.xfades, starts)
        vout = "[xf]"
        if self.captions:
            font, position = self.caption_style
            script = ss.caption_script(self.captions, self.look, self.timing, font, position)
            parts.append(
                f"[xf]{ass.subtitles_filter(script.write(os.path.join(tmp, 'captions.ass')))}[cv]"
            )
            vout = "[cv]"
        if self.venc.filter_suffix:
            parts.append(f"{vout}{self.venc.filter_suffix}[venc]")
            vout = "[venc]"
        amap: list[str] = []
        if self.music:
            cmd += ["-stream_loop", "-1", "-i", self.music]
            parts.append(ss.music_filter(n, self.total, self.volume))
            amap = [
                "-map",
                "[a]",
                *enc.audio(enc.default_audio_for(self.container), container=self.container),
            ]
        cmd += ["-filter_complex", ";".join(parts), "-map", vout, *amap, *self.venc.args]
        ctx.span(0.7, 1.0).run([*cmd, "-t", f"{self.total:.3f}", ctx.output()], duration=self.total)
        return 0


@dataclass
class Slide:
    path: str
    caption: str = ""


def natural_key(path: str):
    return [
        int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", os.path.basename(path))
    ]


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
        self.out_row = SaveRow(out_lf, t("common.output_file"), suggest=self._suggest)
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

        job = _Slideshow(
            paths=[sl.path for sl in slides],
            captions=[sl.caption for sl in slides] if captions else [],
            motions=motions,
            xfades=xfades,
            look=ss.Look(w, h, fps, frames, fit, zoom),
            timing=(starts, dur, td),
            total=total,
            venc=venc,
            container=container,
            music=music if music and os.path.isfile(music) else "",
            volume=volume,
            caption_style=(cap_font, cap_pos),
        )

        self.log(
            self.console, f"{len(slides)} photos · {ff.format_time(total)} · {w}×{h} @ {fps} fps"
        )
        common.queue_job(
            self,
            common.Job(
                name="Slideshow",
                out=out,
                work=job.run,
                duration=total,
                inputs=[s.path for s in slides] + ([music] if music else []),
            ),
            btn=self.btn_render,
            console=self.console,
        )
