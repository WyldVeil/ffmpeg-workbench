"""
tabs/social/intro_maker.py  -  Intro / Outro Maker

Make a title card (bumper) and optionally put it before and/or after a video.

* Backgrounds: solid colour, real gradients (linear or radial), black, or any
  image / video (cover-fitted, looped).
* Title + subtitle in any bundled font with fade, slide, zoom or typewriter
  animation (rendered by libass - see core/ass.py), fade from/to black.
* Optional music, trimmed and faded to the card length.
* Attaching uses one normalising concat pass (core/concat.py): the card is
  made at the video's own size and frame rate, audio is kept on both parts,
  and mismatched inputs can no longer corrupt the result.
"""

from __future__ import annotations

import base64
import os
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk
from typing import Literal

from core import ass, concat, tasks
from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from core.ops.intro_card import Background, Card, background_input, card_script
from core.ops.shared import frame_rate
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, SaveRow, scroll_body

BACKGROUNDS = {
    "solid": "Solid colour",
    "gradient": "Gradient",
    "black": "Black",
    "file": "Image or video file",
}
GRADIENTS = {
    "horizontal": "Left → right",
    "vertical": "Top → bottom",
    "diagonal": "Diagonal",
    "radial": "Radial (from centre)",
}
ANIMATIONS = {
    "fade": "Fade in / out",
    "slide": "Slide up",
    "zoom": "Zoom in",
    "typewriter": "Typewriter",
    "static": "No animation",
}
POSITIONS = {"centre": "Centre", "lower": "Lower third", "top": "Top"}
SIZES = {"Medium": 1.0, "Large": 1.3, "Huge": 1.7, "Small": 0.75}
RESOLUTIONS = {
    "1920×1080 (Full HD)": (1920, 1080),
    "3840×2160 (4K)": (3840, 2160),
    "1280×720 (HD)": (1280, 720),
    "1080×1920 (vertical 9:16)": (1080, 1920),
    "1080×1080 (square)": (1080, 1080),
}
ATTACH = {
    "none": "Just make the card",
    "before": "Put it before the video (intro)",
    "after": "Put it after the video (outro)",
    "both": "Before and after",
}


class IntroMakerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.colour = "#1E1E2E"
        self.colour2 = "#7C3AED"
        self.text_colour = "#FFFFFF"
        self.accent = "#FFB300"
        self._photo = None
        self._preview_job = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        body, design, row = self._build_header()
        self._build_text(design, row)
        self._build_attach_to_a_video_optional(body, design, row)
        self._build_render(body)

    def _build_header(self):
        self.make_header(
            self,
            t("tab.intro_outro_maker"),
            "Title cards with animated text - on their own or attached to a video.",
            icon="🎬",
        )
        body = scroll_body(self)

        top = tk.Frame(body)
        top.pack(fill="x", padx=px(20), pady=px(10, 4))
        left = tk.Frame(top)
        left.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(
            top,
            width=px(384),
            height=px(216),
            bg="#000000",
            highlightthickness=px(1),
            highlightbackground=CLR["border"],
        )
        self.canvas.pack(side="right", padx=px(10, 0))

        design = tk.LabelFrame(left, text="  Card  ", padx=px(10), pady=px(6))
        design.pack(fill="x")

        def row(r, label, *widgets):
            tk.Label(design, text=label, anchor="w", width=13).grid(
                row=r, column=0, sticky="w", pady=px(2)
            )
            for i, w in enumerate(widgets):
                w.grid(row=r, column=1 + i, sticky="w", padx=px(0, 6), pady=px(2))

        self.title_var = tk.StringVar(value="My Channel")
        self.sub_var = tk.StringVar(value="Subscribe for more")
        row(0, "Title:", tk.Entry(design, textvariable=self.title_var, width=34))
        row(1, "Subtitle:", tk.Entry(design, textvariable=self.sub_var, width=34))
        self.font_var = tk.StringVar(value="Montserrat")
        self.size_var = tk.StringVar(value="Medium")
        row(
            2,
            "Font:",
            ttk.Combobox(
                design,
                textvariable=self.font_var,
                values=ass.families(),
                state="readonly",
                width=16,
            ),
            ttk.Combobox(
                design, textvariable=self.size_var, values=list(SIZES), state="readonly", width=8
            ),
        )
        self.anim_var = tk.StringVar(value=ANIMATIONS["fade"])
        self.pos_var = tk.StringVar(value=POSITIONS["centre"])
        row(
            3,
            "Animation:",
            ttk.Combobox(
                design,
                textvariable=self.anim_var,
                values=list(ANIMATIONS.values()),
                state="readonly",
                width=16,
            ),
            ttk.Combobox(
                design,
                textvariable=self.pos_var,
                values=list(POSITIONS.values()),
                state="readonly",
                width=12,
            ),
        )
        return body, design, row

    def _build_text(self, design, row):
        self.text_btn = tk.Button(
            design,
            text="Text colour",
            relief="flat",
            command=lambda: self._pick("text_colour", self.text_btn),
        )
        self.accent_btn = tk.Button(
            design,
            text="Subtitle outline",
            relief="flat",
            command=lambda: self._pick("accent", self.accent_btn),
        )
        row(4, "Colours:", self.text_btn, self.accent_btn)
        self.bg_var = tk.StringVar(value=BACKGROUNDS["gradient"])
        self.grad_var = tk.StringVar(value=GRADIENTS["diagonal"])
        bg_box = ttk.Combobox(
            design,
            textvariable=self.bg_var,
            values=list(BACKGROUNDS.values()),
            state="readonly",
            width=16,
        )
        bg_box.bind("<<ComboboxSelected>>", lambda _e: self._bg_changed())
        self.grad_box = ttk.Combobox(
            design,
            textvariable=self.grad_var,
            values=list(GRADIENTS.values()),
            state="readonly",
            width=16,
        )
        row(5, "Background:", bg_box, self.grad_box)
        self.c1_btn = tk.Button(
            design,
            text="Colour 1",
            relief="flat",
            command=lambda: self._pick("colour", self.c1_btn),
        )
        self.c2_btn = tk.Button(
            design,
            text="Colour 2",
            relief="flat",
            command=lambda: self._pick("colour2", self.c2_btn),
        )
        row(6, "", self.c1_btn, self.c2_btn)
        self.bg_row = FileRow(
            design,
            "Background file:",
            kind="visual",
            label_width=17,
            width=30,
            on_change=lambda _p: self._schedule(),
        )
        self.bg_row.grid(row=7, column=0, columnspan=4, sticky="we")
        self.dur_var = tk.StringVar(value="5")
        self.fps_var = tk.StringVar(value="30")
        row(
            8,
            "Length (s):",
            tk.Entry(design, textvariable=self.dur_var, width=6),
            ttk.Combobox(
                design,
                textvariable=self.fps_var,
                values=["24", "25", "30", "50", "60"],
                state="readonly",
                width=5,
            ),
        )

    def _build_attach_to_a_video_optional(self, body, design, row):
        self.res_var = tk.StringVar(value=next(iter(RESOLUTIONS)))
        row(
            9,
            "Size:",
            ttk.Combobox(
                design,
                textvariable=self.res_var,
                values=list(RESOLUTIONS),
                state="readonly",
                width=26,
            ),
        )
        self.fade_var = tk.BooleanVar(value=True)
        row(
            10,
            "",
            tk.Checkbutton(
                design, text="Fade from / to black", variable=self.fade_var, command=self._schedule
            ),
        )
        self.music_row = FileRow(
            design, "Music (optional):", kind="audio", label_width=17, width=30
        )
        self.music_row.grid(row=11, column=0, columnspan=4, sticky="we")
        for var in (
            self.title_var,
            self.sub_var,
            self.font_var,
            self.size_var,
            self.anim_var,
            self.pos_var,
            self.grad_var,
            self.dur_var,
            self.res_var,
        ):
            var.trace_add("write", lambda *_a: self._schedule())

        att = tk.LabelFrame(body, text="  Attach to a video (optional)  ", padx=px(10), pady=px(6))
        att.pack(fill="x", padx=px(20), pady=px(6))
        self.attach_var = tk.StringVar(value=ATTACH["none"])
        ttk.Combobox(
            att,
            textvariable=self.attach_var,
            values=list(ATTACH.values()),
            state="readonly",
            width=34,
        ).pack(anchor="w")
        self.main_row = FileRow(att, "Main video:", label_width=13)
        self.main_row.pack(fill="x", pady=px(4, 0))
        tk.Label(
            att,
            text="  ℹ  When attaching, the card is made at the video's own size and frame rate.",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores"))
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(out_lf, t("common.output_file"), suggest=self._suggest)

    def _build_render(self, body):
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="🎬  CREATE",
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
        self._paint_buttons()
        self._bg_changed()

    def _paint_buttons(self):
        for btn, col in (
            (self.text_btn, self.text_colour),
            (self.accent_btn, self.accent),
            (self.c1_btn, self.colour),
            (self.c2_btn, self.colour2),
        ):
            r, g, b = (int(col.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            btn.config(bg=col, fg="black" if r * 299 + g * 587 + b * 114 > 128000 else "white")

    def _pick(self, attr, _btn):
        res = colorchooser.askcolor(getattr(self, attr), parent=self)
        if res and res[1]:
            setattr(self, attr, res[1].upper())
            self._paint_buttons()
            self._schedule()

    def _bg_changed(self):
        kind = self.bg_kind()
        states: dict[str, tuple[Literal["normal", "disabled"], ...]] = {
            "gradient": ("normal", "normal", "normal"),
            "solid": ("disabled", "normal", "disabled"),
            "black": ("disabled", "disabled", "disabled"),
            "file": ("disabled", "disabled", "disabled"),
        }
        state = states[kind]
        self.grad_box.config(state="readonly" if state[0] == "normal" else "disabled")
        self.c1_btn.config(state=state[1])
        self.c2_btn.config(state=state[2])
        self.bg_row.entry.config(state="normal" if kind == "file" else "disabled")
        self._schedule()

    def _suggest(self):
        main = self.main_row.path
        if main and self.attach_key() != "none":
            return self.suggest_output(
                main, "with_intro" if self.attach_key() != "after" else "with_outro"
            )
        from core.paths import user_videos_dir

        return os.path.join(user_videos_dir(), "title_card.mp4")

    # ── settings ─────────────────────────────────────────────────────────────

    def _key(self, table, var, default):
        return next((k for k, v in table.items() if v == var.get()), default)

    def bg_kind(self):
        return self._key(BACKGROUNDS, self.bg_var, "solid")

    def attach_key(self):
        return self._key(ATTACH, self.attach_var, "none")

    def card_command(self, w, h, fps, dur, ass_path, out, *, venc=None, preview_at=None):
        """FFmpeg command that renders the card (or one preview PNG to stdout)."""
        bg = Background(
            self.bg_kind(),
            self.colour,
            self.colour2,
            self._key(GRADIENTS, self.grad_var, "horizontal"),
            self.bg_row.path,
        )
        args, base = background_input(bg, w, h, fps, dur)
        chain = f"[0:v]{base},{ass.subtitles_filter(ass_path)}"
        if self.fade_var.get():
            f = min(0.6, dur / 4)
            chain += f",fade=t=in:st=0:d={f:.2f},fade=t=out:st={dur - f:.3f}:d={f:.2f}"
        cmd = [ff.ffmpeg_path(), "-y", *args]
        if preview_at is not None:
            pw, ph = px(384), px(216)  # the preview box, in screen pixels
            return [
                *cmd,
                "-filter_complex",
                chain + f",scale={pw}:{ph}:force_original_aspect_ratio=decrease[v]",
                "-map",
                "[v]",
                "-ss",
                f"{preview_at:.3f}",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "-",
            ]
        music = self.music_row.path
        if music and os.path.isfile(music):
            cmd += ["-i", music]
            fo = min(1.5, dur / 3)
            chain += (
                f"[v];[1:a:0]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=out:st={dur - fo:.3f}:d={fo:.2f},aresample=48000[a]"
            )
            amap = ["-map", "[a]"]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            chain += "[v]"
            amap = ["-map", "1:a"]
        venc = venc or enc.video("h264", "very_high", hw="off", container="mp4")
        return [
            *cmd,
            "-filter_complex",
            chain,
            "-map",
            "[v]",
            *amap,
            *venc.args,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{dur:.3f}",
            out,
        ]

    def settings(self):
        dur = common.number(self.dur_var.get(), "Length", 0.5, 120)
        title = self.title_var.get()
        if not title.strip() and not self.sub_var.get().strip():
            raise ValueError("Enter a title or subtitle.")
        return dur

    def script(self, w, h, dur):
        card = Card(
            title=self.title_var.get(),
            subtitle=self.sub_var.get(),
            font=self.font_var.get(),
            scale=SIZES.get(self.size_var.get(), 1.0),
            colour=self.text_colour,
            accent=self.accent,
            animation=self._key(ANIMATIONS, self.anim_var, "fade"),
            position=self._key(POSITIONS, self.pos_var, "centre"),
        )
        return card_script(card, w, h, dur)

    # ── preview ──────────────────────────────────────────────────────────────

    def _schedule(self):
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(400, self._preview)

    def _preview(self):
        self._preview_job = None
        try:
            dur = self.settings()
        except ValueError:
            return
        w, h = RESOLUTIONS.get(self.res_var.get(), (1920, 1080))
        from core.paths import temp_dir

        work = temp_dir("intro_preview_")
        path = self.script(w, h, dur).write(os.path.join(work, "card.ass"))
        at = min(dur * 0.6, 2.5)
        cmd = self.card_command(w, h, int(self.fps_var.get() or 30), dur, path, "-", preview_at=at)

        def work_fn():
            try:
                r = ff.capture(cmd, timeout=60, text=False)
                return r.stdout if r.returncode == 0 and r.stdout.startswith(b"\x89PNG") else None
            finally:
                import shutil

                shutil.rmtree(work, ignore_errors=True)

        def show(png):
            if not png or not self.winfo_exists():
                return
            self._photo = tk.PhotoImage(master=self, data=base64.b64encode(png).decode("ascii"))
            self.canvas.delete("all")
            self.canvas.create_image(px(192), px(108), image=self._photo)

        tasks.submit(work_fn, on_done=show, name="IntroPreview")

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self):
        try:
            dur = self.settings()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        mode = self.attach_key()
        main = self.main_row.path
        main_info = None
        if mode != "none":
            main_info = common.check_source(self, main, what="Main video")
            if main_info is None:
                return
        if (
            self.bg_kind() == "file"
            and common.check_source(self, self.bg_row.path, need_video=True, what="Background")
            is None
        ):
            return
        out = self.out_row.path or self._suggest()
        out = self.confirm_output(
            out, [p for p in (main, self.bg_row.path, self.music_row.path) if p]
        )
        if not out:
            return
        self.out_row.set(out)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        if main_info is not None:
            w, h = common.even_size(main_info)
            fps = frame_rate(main_info)
        else:
            w, h = RESOLUTIONS.get(self.res_var.get(), (1920, 1080))
            fps = self.fps_var.get() or "30"
        script = self.script(w, h, dur)
        total = dur * (2 if mode == "both" else 1) + (main_info.duration if main_info else 0)

        def work(_progress, _cancelled, ctx):
            tmp = ctx.tempdir()
            ass_path = script.write(os.path.join(tmp, "card.ass"))
            if mode == "none":
                ctx.run(
                    self.card_command(w, h, fps, dur, ass_path, ctx.output(), venc=venc),
                    duration=dur,
                )
                return 0
            card = os.path.join(tmp, "card.mp4")
            ctx.span(0.0, 0.15).run(self.card_command(w, h, fps, dur, ass_path, card), duration=dur)
            pieces = [concat.Piece.of(main)]
            if mode in ("before", "both"):
                pieces.insert(0, concat.Piece.of(card))
            if mode in ("after", "both"):
                pieces.append(concat.Piece.of(card))
            argv, seconds = concat.filter_join_command(
                pieces,
                ctx.output(),
                size=(w, h),
                fps=fps,
                venc=venc,
                audio_codec=enc.default_audio_for(container),
            )
            ctx.span(0.15, 1.0).run(argv, duration=seconds)
            return 0

        self.log(
            self.console,
            f"Card {w}×{h} @ {fps} fps, {dur:g}s"
            + (f" → {ATTACH[mode].lower()}" if mode != "none" else ""),
        )
        common.queue_job(
            self,
            common.Job(
                name="Intro/Outro",
                out=out,
                work=work,
                duration=total,
                inputs=[p for p in (main, self.bg_row.path, self.music_row.path) if p],
            ),
            btn=self.btn_render,
            console=self.console,
        )
