"""
tab_specialeffects.py  ─  Special Effects Studio

Six sub-tabs covering the most popular video effects used on YouTube,
TikTok, Instagram Reels, and short-form content platforms.

Sub-tabs
────────
  1. Intros        - Fade-in, Zoom Burst, Slide In, Spin In, Split Open
  2. Outros        - Fade Out, Freeze & Zoom Out, Blur Dissolve, Iris Close
  3. Overlays      - Emoji Burst, Lower Third, Subscribe Bump, Countdown, Progress Bar
  4. Glitch & Vibe - RGB Chromatic Split, VHS Retro, Film Grain, Vignette, Light Leak
  5. Motion        - Camera Shake, Zoom Punch, Ken Burns, Whip Pan Blur, Heartbeat Pulse
  6. Branding      - Meme Caption, Cinematic Bars, Corner Logo Burn, Social Watermark

All effects use FFmpeg filter chains. The video is never cropped or
shortened unless the user explicitly chooses to trim - intro and outro
effects overlay onto the existing video so no content is lost.
"""

from __future__ import annotations

import logging
import os
import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from tkinter import messagebox, ttk
from typing import Any

from core import ffmpeg as ff
from core import filters as fl
from core import tasks
from core.encoders import VideoEncode
from core.i18n import Choices, Labels, t, t_or
from core.ops import effects as fx
from core.ops.effects import first_visible_time
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.kit.time_fields import Source, frame_button, seconds_text
from tabs.widgets import FileRow, SaveRow, scroll_body

logger = logging.getLogger("crossfader.visuals.effects")

# ─────────────────────────────────────────────────────────────────────────────
#  Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

VIDEO_TYPES = [
    (t("silence.video_files"), "*.mp4 *.mov *.mkv *.avi *.webm *.flv *.m4v"),
    (t("youtube.all_files"), t("ducker.item_2")),
]


def _probe_duration(path: str) -> float:
    """Duration of *path* in seconds, or 0.0."""
    return ff.duration_of(path)


def _probe_wh(path: str) -> tuple[int, int]:
    """Display width/height (rotation applied, even), or (1920, 1080)."""
    info = ff.try_probe(path)
    if info is None or not info.has_video:
        return 1920, 1080
    w, h = info.require_video().display_size
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


def _probe_length_and_size(src: str) -> tuple[float, tuple[int, int]]:
    return _probe_duration(src), _probe_wh(src)


# ─────────────────────────────────────────────────────────────────────────────
#  Reusable sub-widget builders
# ─────────────────────────────────────────────────────────────────────────────


def _lf(parent: tk.Misc, title: str, **kw: Any) -> tk.LabelFrame:
    return tk.LabelFrame(
        parent,
        text=f"  {title}  ",
        bg=CLR["bg"],
        fg=CLR["fg"],
        font=(UI_FONT, 9, "bold"),
        padx=px(14),
        pady=px(10),
        **kw,
    )


def _spin(
    parent: tk.Misc,
    label: str,
    var: tk.StringVar,
    from_: float,
    to: float,
    resolution: float = 0.5,
    width: int = 6,
    *,
    source: Source | None = None,
) -> tk.Frame:
    """``Label [spinbox]`` - with a 🎞 frame picker when it holds a time in the video *source()*."""
    f = tk.Frame(parent, bg=CLR["bg"])
    tk.Label(f, text=label, bg=CLR["bg"], fg=CLR["fg"], font=(UI_FONT, 10)).pack(side="left")
    tk.Spinbox(
        f,
        from_=from_,
        to=to,
        increment=resolution,
        textvariable=var,
        width=width,
        bg=CLR["panel"],
        fg=CLR["fg"],
        buttonbackground=CLR["panel"],
        insertbackground=CLR["fg"],
    ).pack(side="left", padx=px(6))
    if source is not None:
        frame_button(f, var, source, seconds_text).pack(side="left")
    return f


def _combo(
    parent: tk.Misc, label: str, var: tk.StringVar, values: list[str], width: int = 18
) -> tk.Frame:
    f = tk.Frame(parent, bg=CLR["bg"])
    tk.Label(f, text=label, bg=CLR["bg"], fg=CLR["fg"], font=(UI_FONT, 10)).pack(side="left")
    ttk.Combobox(
        f,
        textvariable=var,
        values=values,
        state="readonly",
        width=width,
    ).pack(side="left", padx=px(6))
    return f


def _hint(parent: tk.Misc, text: str) -> None:
    tk.Label(
        parent,
        text=text,
        bg=CLR["bg"],
        fg=CLR["fgdim"],
        font=(UI_FONT, 8),
        justify="left",
        wraplength=px(640),
    ).pack(anchor="w", padx=px(18), pady=px(0, 6))


# ─────────────────────────────────────────────────────────────────────────────
#  Main tab class
# ─────────────────────────────────────────────────────────────────────────────


#: Ken Burns pan (what the drop-down shows -> where the pan starts)
_KB_PANS = Choices(
    "effects.kb_pans",
    {
        "Left → Right": "left",
        "Right → Left": "right",
        "Top → Bottom": "top",
        "Bottom → Top": "bottom",
        "Centre zoom only": "centre",
    },
)


SPLIT_AXES = Labels(
    "effects.split_axes", {"vertical": "Vertical (curtain)", "horizontal": "Horizontal (book)"}
)


class SpecialEffectsTab(BaseTab):
    """
    Special Effects Studio - six categories of popular video effects,
    each in its own sub-tab.
    """

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._sources: list[tk.StringVar] = []  # every effect page's source video
        self._build_ui()

    # ── Shared plumbing for every effect ──────────────────────────────────
    #: Appended to every video chain: even dimensions, square pixels and
    #: 4:2:0 so H.264 output plays everywhere (odd sizes / 10-bit / RGB
    #: filters used to produce unplayable or failing renders).

    def run_ffmpeg(
        self,
        cmd: Sequence[object],
        console: tk.Text | None = None,
        on_done: Callable[[int], object] | None = None,
        btn: tk.Button | None = None,
        *,
        duration: float | None = None,
        output: str | None = None,
        inputs: Sequence[str] = (),
        name: str | None = None,
    ) -> str | None:
        """Effects that build their own command still get overwrite protection,
        playable 4:2:0 output, progress and the source as an input guard."""
        cmd = [str(c) for c in cmd]
        src = fx.first_input(cmd)
        if not inputs and src is not None:
            final = self.confirm_output(cmd[-1], [src])
            if not final:
                if btn is not None:
                    btn.config(state="normal")
                return None
            cmd[-1] = final
            fx.ensure_420(cmd)
            inputs = [src]
            if duration is None:
                duration = ff.duration_of(src) or None
        return super().run_ffmpeg(
            cmd, console, on_done, btn, duration=duration, output=output, inputs=inputs, name=name
        )

    def _fx_num(
        self, var: tk.StringVar, label: str, lo: float | None = None, hi: int | None = None
    ) -> float:
        """Parse a numeric field ('1,5', '00:12.5' accepted); raises ValueError
        with a message naming the field."""
        try:
            val = ff.parse_time(str(var.get()).strip())
        except ValueError:
            try:
                val = float(str(var.get()).replace(",", "."))
            except ValueError:
                raise ValueError(f"'{label}' must be a number.") from None
        if (lo is not None and val < lo) or (hi is not None and val > hi):
            raise ValueError(f"'{label}' must be between {lo} and {hi}.")
        return val

    def _fx_begin(
        self, src_var: tk.StringVar, out_var: tk.StringVar, suffix: str = "fx"
    ) -> tuple[str, str, ff.MediaInfo] | None:
        """Validate source/output; returns (src, out, info) or None."""
        src = src_var.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return None
        info = ff.try_probe(src)
        if info is None or not info.has_video:
            messagebox.showerror(
                t("common.error"), t("effects.not_a_readable_video_src", src=src), parent=self
            )
            return None
        out = out_var.get().strip() or self.suggest_output(src, suffix, ".mp4")
        out = self.confirm_output(out, [src])
        if not out:
            return None
        out_var.set(out)
        return src, out, info

    def _fx_run(
        self, job: RenderJob, crf_var: tk.StringVar, btn: tk.Button, console: tk.Text
    ) -> None:
        """Queue *job* encoded with x264 at the CRF in *crf_var* (the audio is
        copied when it can be)."""
        if job.audio is None:
            job = replace(job, audio=fx.effect_audio(job.info))
        if job.duration is None:
            job = replace(job, duration=fx.geometry(job.info)[3])
        venc = VideoEncode("libx264", "h264", args=fx.x264_args(str(crf_var.get())))
        common.render(self, job, encoder=venc, console=console, btn=btn)

    def _build_ui(self) -> None:
        # ── Header ────────────────────────────────────────────────────────
        make_header(
            self, t("tab.special_effects"), t("effects.top_youtube_social_media_effects"), icon="✨"
        )

        # ── Category notebook ─────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=px(12), pady=px(8))

        self._build_intros_tab(nb)
        self._build_outros_tab(nb)
        self._build_overlays_tab(nb)
        self._build_glitch_tab(nb)
        self._build_motion_tab(nb)
        self._build_branding_tab(nb)

    # ═════════════════════════════════════════════════════════════════════
    #  TAB 1 - INTROS
    # ═════════════════════════════════════════════════════════════════════

    def _build_intros_tab(self, nb: ttk.Notebook) -> None:
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.intros"))

        intro_nb = ttk.Notebook(outer)
        intro_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))

        self._build_intro_fadein(intro_nb)
        self._build_intro_zoomburst(intro_nb)
        self._build_intro_slidein(intro_nb)
        self._build_intro_spin(intro_nb)
        self._build_intro_splitopen(intro_nb)

    # ── Intro 1: Fade-In from Static Frame ───────────────────────────────

    def _build_intro_fadein(self, nb: ttk.Notebook) -> None:
        inner, self._fi_src, self._fi_out = self._effect_page(
            nb,
            t("effects.fade_in"),
            t("effects.fade_in_from_static_frame"),
            t("effects.a_still_frame_from_the_start_of"),
            "_fadein",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._fi_dur = tk.StringVar(value="2.5")
        self._fi_start = tk.StringVar(value="auto")
        self._fi_crf = tk.StringVar(value="18")

        r = _spin(opts, t("muter.fade_label"), self._fi_dur, 0.5, 10, 0.5)
        r.pack(anchor="w", pady=px(3))

        r2 = tk.Frame(opts, bg=CLR["bg"])
        r2.pack(anchor="w", pady=px(3))
        tk.Label(r2, text=t("effects.still_frame_source"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left"
        )
        for val, lbl in [("auto", "Auto (first non-black)"), ("custom", "Custom timestamp (s):")]:
            tk.Radiobutton(
                r2,
                text=lbl,
                value=val,
                variable=self._fi_start,
                bg=CLR["bg"],
                fg=CLR["fg"],
                selectcolor=CLR["panel"],
                activebackground=CLR["bg"],
            ).pack(side="left", padx=px(8))
        self._fi_custom_t = tk.StringVar(value="2.0")
        tk.Entry(
            r2,
            textvariable=self._fi_custom_t,
            width=6,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))

        r3 = _spin(opts, t("effects.crf_quality"), self._fi_crf, 12, 30, 1)
        r3.pack(anchor="w", pady=px(3))

        self._fi_btn = self.make_render_btn(
            inner, t("effects.apply_fade_in"), self._render_fadein, width=30
        )
        self._fi_btn.pack(pady=px(10))
        self._fi_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_fadein(self) -> None:
        begun = self._fx_begin(self._fi_src, self._fi_out, "fadein")
        if not begun:
            return
        src, out, info = begun
        try:
            fade = self._fx_num(self._fi_dur, t("effects.fade_duration"), 0.1, 60)
            custom = (
                None
                if self._fi_start.get() == "auto"
                else self._fx_num(self._fi_custom_t, t("chapter_markers.timestamp_col"), 0)
            )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        _w, _h, _fps, dur = fx.geometry(info)
        still_at = custom if custom is not None else first_visible_time(src)
        still_at = max(0.0, min(still_at, max(0.0, dur - 0.1)))
        self.log(self._fi_console, t("effects.still_frame_at_still_at_2f_s", still_at=still_at))
        graph = fx.fadein_graph(fade, still_at)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", graph=graph),
            self._fi_crf,
            self._fi_btn,
            self._fi_console,
        )

    # ── Intro 2: Zoom Burst ───────────────────────────────────────────────

    def _build_intro_zoomburst(self, nb: ttk.Notebook) -> None:
        inner, self._zb_src, self._zb_out = self._effect_page(
            nb,
            t("effects.zoom_burst"),
            t("effects.zoom_burst_in"),
            t("effects.the_video_starts_massively_zoomed_in_and"),
            "_zoomburst",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._zb_dur = tk.StringVar(value="1.5")
        self._zb_zoom = tk.StringVar(value="3.0")
        self._zb_crf = tk.StringVar(value="18")

        _spin(opts, t("effects.burst_duration_s"), self._zb_dur, 0.3, 5, 0.1).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("effects.starting_zoom_factor"), self._zb_zoom, 1.5, 8, 0.5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("effects.crf_quality"), self._zb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._zb_btn = self.make_render_btn(
            inner, t("effects.apply_zoom_burst"), self._render_zoomburst, width=30
        )
        self._zb_btn.pack(pady=px(10))
        self._zb_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_zoomburst(self) -> None:
        begun = self._fx_begin(self._zb_src, self._zb_out, "zoomburst")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._zb_dur, t("splicer.column_duration"), 0.1, 30)
            z = self._fx_num(self._zb_zoom, t("effects.zoom"), 1.0, 10)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, _dur = fx.geometry(info)
        n = max(1, round(d * (info.require_video().fps or 30)))
        graph = fx.zoomburst_graph(fps, h, n, w, z)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", chain=graph),
            self._zb_crf,
            self._zb_btn,
            self._zb_console,
        )

    # ── Intro 3: Slide In ─────────────────────────────────────────────────

    def _build_intro_slidein(self, nb: ttk.Notebook) -> None:
        inner, self._sl_src, self._sl_out = self._effect_page(
            nb,
            t("effects.slide_in"),
            t("effects.slide_in_2"),
            t("effects.the_video_frame_slides_onto_a_black"),
            "_slidein",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._sl_dir = tk.StringVar(value="Left")
        self._sl_dur = tk.StringVar(value="1.2")
        self._sl_crf = tk.StringVar(value="18")

        _combo(
            opts,
            t("effects.slide_from"),
            self._sl_dir,
            ["Left", "Right", "Top", "Bottom"],
            width=12,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("effects.slide_duration_s"), self._sl_dur, 0.2, 5, 0.1).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._sl_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._sl_btn = self.make_render_btn(
            inner, t("effects.apply_slide_in"), self._render_slidein, width=30
        )
        self._sl_btn.pack(pady=px(10))
        self._sl_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_slidein(self) -> None:
        begun = self._fx_begin(self._sl_src, self._sl_out, "slidein")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._sl_dur, t("splicer.column_duration"), 0.1, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        ease = f"pow(1-t/{d:.3f},2)"
        direction = self._sl_dir.get().lower()
        x, y = "0", "0"
        if "right" in direction:
            x = f"if(lt(t,{d:.3f}),W*{ease},0)"
        elif "top" in direction or "up" in direction:
            y = f"if(lt(t,{d:.3f}),-H*{ease},0)"
        elif "bottom" in direction or "down" in direction:
            y = f"if(lt(t,{d:.3f}),H*{ease},0)"
        else:
            x = f"if(lt(t,{d:.3f}),-W*{ease},0)"
        graph = fx.slidein_graph(x, y)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", graph=graph),
            self._sl_crf,
            self._sl_btn,
            self._sl_console,
        )

    # ── Intro 4: Spin In ──────────────────────────────────────────────────

    def _build_intro_spin(self, nb: ttk.Notebook) -> None:
        inner, self._sp_src, self._sp_out = self._effect_page(
            nb,
            t("effects.spin_in"),
            t("effects.spin_in_2"),
            t("effects.the_video_rotates_in_from_a_small"),
            "_spinin",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._sp_dur = tk.StringVar(value="1.5")
        self._sp_turns = tk.StringVar(value="1")
        self._sp_crf = tk.StringVar(value="18")

        _spin(opts, t("effects.spin_duration_s"), self._sp_dur, 0.3, 5, 0.1).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("effects.number_of_rotations"), self._sp_turns, 1, 5, 1).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._sp_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._sp_btn = self.make_render_btn(
            inner, t("effects.apply_spin_in"), self._render_spinin, width=30
        )
        self._sp_btn.pack(pady=px(10))
        self._sp_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_spinin(self) -> None:
        begun = self._fx_begin(self._sp_src, self._sp_out, "spinin")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._sp_dur, t("splicer.column_duration"), 0.1, 30)
            turns = self._fx_num(self._sp_turns, t("effects.turns"), 0, 20)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        k = f"if(lte(t,{d:.3f}),0.05+0.95*pow(t/{d:.3f},2),1)"
        graph = fx.spinin_graph(d, k, turns)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", graph=graph),
            self._sp_crf,
            self._sp_btn,
            self._sp_console,
        )

    # ── Intro 5: Split Open ───────────────────────────────────────────────

    def _build_intro_splitopen(self, nb: ttk.Notebook) -> None:
        inner, self._spo_src, self._spo_out = self._effect_page(
            nb,
            t("effects.split_open"),
            t("effects.split_open_2"),
            t("effects.the_screen_is_split_horizontally_top_half"),
            "_splitopen",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._spo_dur = tk.StringVar(value="1.0")
        self._spo_dir = tk.StringVar(value=SPLIT_AXES["vertical"])
        self._spo_crf = tk.StringVar(value="18")

        _spin(opts, t("effects.open_duration_s"), self._spo_dur, 0.2, 4, 0.1).pack(
            anchor="w", pady=px(3)
        )
        _combo(
            opts,
            t("effects.split_axis"),
            self._spo_dir,
            list(SPLIT_AXES.values()),
            width=22,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._spo_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._spo_btn = self.make_render_btn(
            inner, t("effects.apply_split_open"), self._render_splitopen, width=30
        )
        self._spo_btn.pack(pady=px(10))
        self._spo_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_splitopen(self) -> None:
        begun = self._fx_begin(self._spo_src, self._spo_out, "splitopen")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._spo_dur, t("splicer.column_duration"), 0.1, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, _dur = fx.geometry(info)
        trans = "vertopen" if SPLIT_AXES.code_of(self._spo_dir.get()) == "vertical" else "horzopen"
        graph = fx.splitopen_graph(d, fps, h, trans, w)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", graph=graph),
            self._spo_crf,
            self._spo_btn,
            self._spo_console,
        )

    def _build_outros_tab(self, nb: ttk.Notebook) -> None:
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.outros"))
        outro_nb = ttk.Notebook(outer)
        outro_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_outro_fadeout(outro_nb)
        self._build_outro_freezezoom(outro_nb)
        self._build_outro_irisclose(outro_nb)

    def _build_outro_fadeout(self, nb: ttk.Notebook) -> None:
        inner, self._fo_src, self._fo_out = self._effect_page(
            nb,
            t("effects.fade_out"),
            t("effects.fade_to_black"),
            t("effects.the_last_n_seconds_of_your_video"),
            "_fadeout",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._fo_dur = tk.StringVar(value="2.0")
        self._fo_crf = tk.StringVar(value="18")
        _spin(opts, t("muter.fade_label"), self._fo_dur, 0.3, 10, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._fo_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._fo_btn = self.make_render_btn(
            inner, t("effects.apply_fade_out"), self._render_fadeout, width=30
        )
        self._fo_btn.pack(pady=px(10))
        self._fo_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_fadeout(self) -> None:
        src = self._fo_src.get().strip()
        out = self._fo_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        dur = float(self._fo_dur.get())
        crf = self._fo_crf.get()
        total = _probe_duration(src)
        start = max(0, total - dur)

        cmd = fx.fadeout_cmd(crf, dur, out, src, start)
        self.run_ffmpeg(
            cmd,
            self._fo_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._fo_btn,
        )

    def _build_outro_freezezoom(self, nb: ttk.Notebook) -> None:
        inner, self._fz_src, self._fz_out = self._effect_page(
            nb,
            t("effects.freeze_zoom_out"),
            t("effects.freeze_frame_zoom_out"),
            t("effects.the_last_frame_of_the_video_is"),
            "_freezezoom",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._fz_hold = tk.StringVar(value="2.5")
        self._fz_fade = tk.StringVar(value="1.0")
        self._fz_crf = tk.StringVar(value="18")
        _spin(opts, t("effects.hold_duration_s"), self._fz_hold, 0.5, 10, 0.5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("muter.fade_label"), self._fz_fade, 0.3, 5, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._fz_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._fz_btn = self.make_render_btn(
            inner, t("effects.apply_freeze_zoom_out"), self._render_freezezoom, width=30
        )
        self._fz_btn.pack(pady=px(10))
        self._fz_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_freezezoom(self) -> None:
        begun = self._fx_begin(self._fz_src, self._fz_out, "freezezoom")
        if not begun:
            return
        src, out, info = begun
        try:
            hold = self._fx_num(self._fz_hold, t("effects.hold"), 0.1, 60)
            fade = self._fx_num(self._fz_fade, t("muter.fade"), 0, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = fx.geometry(info)
        extra = hold + fade
        audio, graph = fx.freezezoom_graph(dur, extra, fade, fps, h, hold, info, w)
        self._fx_run(
            RenderJob(
                src=src,
                out=out,
                info=info,
                name="Special Effects",
                graph=graph,
                audio=audio,
                duration=dur + extra,
            ),
            self._fz_crf,
            self._fz_btn,
            self._fz_console,
        )

    def _build_outro_irisclose(self, nb: ttk.Notebook) -> None:
        inner, self._ir_src, self._ir_out = self._effect_page(
            nb,
            t("effects.iris_close"),
            t("effects.iris_vignette_close"),
            t("effects.a_black_oval_vignette_closes_in_from"),
            "_irisclose",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._ir_dur = tk.StringVar(value="1.5")
        self._ir_crf = tk.StringVar(value="18")
        _spin(opts, t("effects.iris_duration_s"), self._ir_dur, 0.3, 6, 0.5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._ir_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._ir_btn = self.make_render_btn(
            inner, t("effects.apply_iris_close"), self._render_irisclose, width=30
        )
        self._ir_btn.pack(pady=px(10))
        self._ir_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_irisclose(self) -> None:
        begun = self._fx_begin(self._ir_src, self._ir_out, "irisclose")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._ir_dur, t("splicer.column_duration"), 0.1, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = fx.geometry(info)
        d = min(d, max(0.1, dur - 0.05))
        start = max(0.0, dur - d)
        audio, graph = fx.irisclose_graph(d, dur, fps, h, info, start, w)
        self._fx_run(
            RenderJob(
                src=src, out=out, info=info, name="Special Effects", graph=graph, audio=audio
            ),
            self._ir_crf,
            self._ir_btn,
            self._ir_console,
        )

    def _build_overlays_tab(self, nb: ttk.Notebook) -> None:
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.overlays"))
        ov_nb = ttk.Notebook(outer)
        ov_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_ov_emoji(ov_nb)
        self._build_ov_lowerthird(ov_nb)
        self._build_ov_subscribe(ov_nb)
        self._build_ov_countdown(ov_nb)
        self._build_ov_progressbar(ov_nb)

    def _build_ov_emoji(self, nb: ttk.Notebook) -> None:
        inner, self._em_src, self._em_out = self._effect_page(
            nb,
            t("effects.emoji_burst"),
            t("effects.emoji_text_burst"),
            t("effects.slam_a_large_emoji_or_short_text"),
            "_emoji",
        )

        opts = _lf(inner, t("effects.emoji_text_options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._em_text = tk.StringVar(value="😂")
        self._em_size = tk.StringVar(value="96")
        self._em_x = tk.StringVar(value="50")
        self._em_y = tk.StringVar(value="30")
        self._em_start = tk.StringVar(value="3.0")
        self._em_hold = tk.StringVar(value="2.0")
        self._em_crf = tk.StringVar(value="18")
        self._build_emoji_text_row(opts)
        self._build_emoji_timing(inner, opts)

    def _build_emoji_text_row(self, opts: tk.LabelFrame) -> None:
        """The emoji or text, its size, and where it goes on the frame."""
        r0 = tk.Frame(opts, bg=CLR["bg"])
        r0.pack(anchor="w", pady=px(3))
        tk.Label(r0, text=t("effects.text_emoji"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        tk.Entry(
            r0,
            textvariable=self._em_text,
            width=24,
            font=(UI_FONT, 14),
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(6))
        tk.Label(
            r0,
            text=t("effects.supports_emoji_ascii_latin_text"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(side="left")

        _spin(opts, t("effects.font_size_px"), self._em_size, 24, 256, 8).pack(
            anchor="w", pady=px(3)
        )

        r1 = tk.Frame(opts, bg=CLR["bg"])
        r1.pack(anchor="w", pady=px(3))
        tk.Label(r1, text=t("effects.position_x_from_left"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left"
        )
        tk.Spinbox(
            r1,
            from_=0,
            to=100,
            textvariable=self._em_x,
            width=5,
            bg=CLR["panel"],
            fg=CLR["fg"],
            buttonbackground=CLR["panel"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))
        tk.Label(r1, text=t("effects.y_from_top"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        tk.Spinbox(
            r1,
            from_=0,
            to=100,
            textvariable=self._em_y,
            width=5,
            bg=CLR["panel"],
            fg=CLR["fg"],
            buttonbackground=CLR["panel"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))

    def _build_emoji_timing(self, inner: tk.Frame, opts: tk.LabelFrame) -> None:
        """When it appears, how long it stays, quality, and the button."""
        _spin(
            opts,
            t("effects.appear_at_s"),
            self._em_start,
            0,
            3600,
            0.5,
            source=lambda: self._em_src.get().strip(),
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("effects.hold_for_s"), self._em_hold, 0.2, 30, 0.5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._em_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._em_btn = self.make_render_btn(
            inner, t("effects.burn_emoji_onto_video"), self._render_emoji, width=30
        )
        self._em_btn.pack(pady=px(10))
        self._em_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_emoji(self) -> None:
        src = self._em_src.get().strip()
        out = self._em_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        # read the settings here, on the UI thread; only the probe runs in the background
        try:
            size = int(self._em_size.get())
            xpct = float(self._em_x.get()) / 100.0
            ypct = float(self._em_y.get()) / 100.0
            start = float(self._em_start.get())
            hold = float(self._em_hold.get())
        except ValueError:
            size, xpct, ypct, start, hold = 96, 0.5, 0.3, 3.0, 2.0

        text = self._em_text.get()
        crf = self._em_crf.get()

        pop = fx.Pop(text=text, size=size, xpct=xpct, ypct=ypct, start=start, hold=hold, crf=crf)

        def _start(probed: tuple[int, int]) -> None:
            cmd = fx.emoji_cmd(src, out, probed, pop)
            self.run_ffmpeg(
                cmd,
                self._em_console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self._em_btn,
            )

        tasks.submit(lambda: _probe_wh(src), on_done=_start, name="render_emoji")

    def _build_ov_lowerthird(self, nb: ttk.Notebook) -> None:
        inner, self._lt_src, self._lt_out = self._effect_page(
            nb,
            t("effects.lower_third"),
            t("effects.lower_third_banner"),
            t("effects.a_professional_name_title_bar_that_slides"),
            "_lt",
        )

        opts = _lf(inner, t("effects.banner_options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._lt_title = tk.StringVar(value=t_or("effects.sample.john_smith", "John Smith"))
        self._lt_subtitle = tk.StringVar(
            value=t_or("effects.sample.content_creator", "Content Creator")
        )
        self._lt_color = tk.StringVar(value="#1565C0")
        self._lt_start = tk.StringVar(value="2.0")
        self._lt_hold = tk.StringVar(value="4.0")
        self._lt_crf = tk.StringVar(value="18")

        for lbl, var, w in [
            ("Title:", self._lt_title, 28),
            ("Subtitle:", self._lt_subtitle, 28),
            ("Bar colour (hex):", self._lt_color, 10),
        ]:
            r = tk.Frame(opts, bg=CLR["bg"])
            r.pack(anchor="w", pady=px(3))
            tk.Label(r, text=lbl, bg=CLR["bg"], fg=CLR["fg"], width=18, anchor="e").pack(
                side="left"
            )
            tk.Entry(
                r,
                textvariable=var,
                width=w,
                bg=CLR["panel"],
                fg=CLR["fg"],
                insertbackground=CLR["fg"],
            ).pack(side="left", padx=px(6))

        _spin(
            opts,
            t("effects.appear_at_s"),
            self._lt_start,
            0,
            3600,
            0.5,
            source=lambda: self._lt_src.get().strip(),
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("effects.hold_for_s"), self._lt_hold, 0.5, 60, 0.5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._lt_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._lt_btn = self.make_render_btn(
            inner, t("effects.burn_lower_third"), self._render_lowerthird, width=30
        )
        self._lt_btn.pack(pady=px(10))
        self._lt_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_lowerthird(self) -> None:
        begun = self._fx_begin(self._lt_src, self._lt_out, "lowerthird")
        if not begun:
            return
        src, out, info = begun
        try:
            start = self._fx_num(self._lt_start, t("btn.start"), 0)
            hold = self._fx_num(self._lt_hold, t("effects.hold"), 0.1)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, _dur = fx.geometry(info)
        bar_h = max(40, int(h * 0.14)) // 2 * 2
        y0 = h - bar_h - int(h * 0.08)
        colour = self._lt_color.get().strip().lstrip("#") or "1565C0"
        slide = f"if(lt(t-{start:.3f},0.3),{y0}+{bar_h}*(1-(t-{start:.3f})/0.3),{y0})"
        enable = f"between(t,{start:.3f},{start + hold:.3f})"
        from core.paths import temp_dir

        tmp = temp_dir("lowerthird_")
        title = fl.DrawText(
            textfile=fl.drawtext_text_file(self._lt_title.get(), tmp),
            font="sans bold",
            fontsize=int(bar_h * 0.42),
            fontcolor="white",
            x=str(int(w * 0.03)),
            y=f"{slide}+{int(bar_h * 0.1)}",
            enable=enable,
        ).filter()
        sub = fl.DrawText(
            textfile=fl.drawtext_text_file(self._lt_subtitle.get(), tmp),
            font="sans",
            fontsize=int(bar_h * 0.26),
            fontcolor="white@0.85",
            x=str(int(w * 0.03)),
            y=f"{slide}+{int(bar_h * 0.6)}",
            enable=enable,
        ).filter()
        graph = fx.lowerthird_graph(bar_h, colour, enable, fps, slide, sub, title, w)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", graph=graph),
            self._lt_crf,
            self._lt_btn,
            self._lt_console,
        )

    def _build_ov_subscribe(self, nb: ttk.Notebook) -> None:
        inner, self._sub_src, self._sub_out = self._effect_page(
            nb,
            t("effects.subscribe_bump"),
            t("effects.subscribe_like_reminder"),
            t("effects.burns_a_customisable_call_to_action_reminder"),
            "_subscribe",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._sub_text = tk.StringVar(
            value=t_or("effects.sample.like_subscribe", "👍 Like & Subscribe! 🔔")
        )
        self._sub_size = tk.StringVar(value="52")
        self._sub_pos = tk.StringVar(value="Bottom Centre")
        self._sub_start = tk.StringVar(value="5.0")
        self._sub_hold = tk.StringVar(value="4.0")
        self._sub_crf = tk.StringVar(value="18")

        r0 = tk.Frame(opts, bg=CLR["bg"])
        r0.pack(anchor="w", pady=px(3))
        tk.Label(r0, text=t("effects.message"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        tk.Entry(
            r0,
            textvariable=self._sub_text,
            width=36,
            font=(UI_FONT, 12),
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(6))

        _spin(opts, t("intro_maker.font_size_label"), self._sub_size, 24, 120, 4).pack(
            anchor="w", pady=px(3)
        )
        _combo(
            opts,
            t("side_by_side.label_position_label"),
            self._sub_pos,
            list(fx.BANNER_PLACES),
            width=16,
        ).pack(anchor="w", pady=px(3))
        _spin(
            opts,
            t("effects.appear_at_s"),
            self._sub_start,
            0,
            3600,
            0.5,
            source=lambda: self._sub_src.get().strip(),
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("effects.hold_for_s"), self._sub_hold, 0.5, 60, 0.5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._sub_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._sub_btn = self.make_render_btn(
            inner, t("effects.burn_subscribe_reminder"), self._render_subscribe, width=30
        )
        self._sub_btn.pack(pady=px(10))
        self._sub_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_subscribe(self) -> None:
        src = self._sub_src.get().strip()
        out = self._sub_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        # read the settings here, on the UI thread; only the probe runs in the background
        try:
            size = int(self._sub_size.get())
            start = float(self._sub_start.get())
            hold = float(self._sub_hold.get())
        except ValueError:
            size, start, hold = 52, 5.0, 4.0

        end = start + hold
        crf = self._sub_crf.get()
        text = self._sub_text.get()
        pos = self._sub_pos.get()

        banner = fx.Banner(text=text, size=size, start=start, end=end, position=pos, crf=crf)

        def _start(_probed: tuple[int, int]) -> None:
            cmd = fx.subscribe_cmd(src, out, banner)
            self.run_ffmpeg(
                cmd,
                self._sub_console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self._sub_btn,
            )

        tasks.submit(lambda: _probe_wh(src), on_done=_start, name="render_subscribe")

    def _build_ov_countdown(self, nb: ttk.Notebook) -> None:
        inner, self._cd_src, self._cd_out = self._effect_page(
            nb,
            t("effects.countdown"),
            t("effects.countdown_timer_overlay"),
            t("effects.burns_a_live_countdown_clock_onto_the"),
            "_countdown",
        )

        opts = _lf(inner, t("effects.countdown_options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._cd_from = tk.StringVar(value="10")
        self._cd_start = tk.StringVar(value="2.0")
        self._cd_size = tk.StringVar(value="80")
        self._cd_crf = tk.StringVar(value="18")

        _spin(opts, t("effects.count_from_s"), self._cd_from, 3, 3600, 1).pack(
            anchor="w", pady=px(3)
        )
        _spin(
            opts,
            t("effects.start_at_s"),
            self._cd_start,
            0,
            3600,
            0.5,
            source=lambda: self._cd_src.get().strip(),
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("intro_maker.font_size_label"), self._cd_size, 24, 200, 8).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._cd_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._cd_btn = self.make_render_btn(
            inner, t("effects.burn_countdown"), self._render_countdown, width=30
        )
        self._cd_btn.pack(pady=px(10))
        self._cd_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_countdown(self) -> None:
        src = self._cd_src.get().strip()
        out = self._cd_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        try:
            count_from = int(self._cd_from.get())
            start = float(self._cd_start.get())
            size = int(self._cd_size.get())
        except ValueError:
            count_from, start, size = 10, 2.0, 80

        crf = self._cd_crf.get()
        end = start + count_from

        # drawtext with a time-based expression that counts down
        cmd = fx.countdown_cmd(count_from, crf, end, out, size, src, start)
        self.run_ffmpeg(
            cmd,
            self._cd_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._cd_btn,
        )

    def _build_ov_progressbar(self, nb: ttk.Notebook) -> None:
        inner, self._pb_src, self._pb_out = self._effect_page(
            nb,
            t("effects.progress_bar"),
            t("effects.video_progress_bar"),
            t("effects.adds_a_youtube_style_thin_progress_bar"),
            "_progress",
        )

        opts = _lf(inner, t("effects.bar_options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._pb_color = tk.StringVar(value="FF0000")
        self._pb_height = tk.StringVar(value="8")
        self._pb_pos = tk.StringVar(value="Bottom")
        self._pb_crf = tk.StringVar(value="18")

        r0 = tk.Frame(opts, bg=CLR["bg"])
        r0.pack(anchor="w", pady=px(3))
        tk.Label(r0, text=t("effects.bar_colour_hex_no"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left"
        )
        tk.Entry(
            r0,
            textvariable=self._pb_color,
            width=10,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(6))

        _spin(opts, t("effects.bar_height_px"), self._pb_height, 2, 40, 2).pack(
            anchor="w", pady=px(3)
        )
        _combo(
            opts, t("side_by_side.label_position_label"), self._pb_pos, ["Bottom", "Top"], width=10
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._pb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._pb_btn = self.make_render_btn(
            inner, t("effects.burn_progress_bar"), self._render_progressbar, width=30
        )
        self._pb_btn.pack(pady=px(10))
        self._pb_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_progressbar(self) -> None:
        begun = self._fx_begin(self._pb_src, self._pb_out, "progress")
        if not begun:
            return
        src, out, info = begun
        try:
            bar_h = int(self._fx_num(self._pb_height, t("region_blur.height_col"), 1, 200))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = fx.geometry(info)
        colour = self._pb_color.get().strip().lstrip("#") or "FF0000"
        y = 0 if self._pb_pos.get() == "Top" else h - bar_h
        graph = fx.progressbar_graph(bar_h, colour, dur, fps, w, y)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", graph=graph),
            self._pb_crf,
            self._pb_btn,
            self._pb_console,
        )

    def _build_glitch_tab(self, nb: ttk.Notebook) -> None:
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.glitch_vibe"))
        gl_nb = ttk.Notebook(outer)
        gl_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_gl_rgb(gl_nb)
        self._build_gl_vhs(gl_nb)
        self._build_gl_grain(gl_nb)
        self._build_gl_vignette(gl_nb)
        self._build_gl_lightleak(gl_nb)

    def _build_gl_rgb(self, nb: ttk.Notebook) -> None:
        inner, self._rgb_src, self._rgb_out = self._effect_page(
            nb,
            t("effects.rgb_glitch"),
            t("effects.rgb_chromatic_aberration_glitch"),
            t("effects.splits_the_red_green_and_blue_channels"),
            "_rgb",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._rgb_offset = tk.StringVar(value="6")
        self._rgb_start = tk.StringVar(value="0")
        self._rgb_dur = tk.StringVar(value="whole video")
        self._rgb_crf = tk.StringVar(value="18")

        _spin(opts, t("effects.channel_offset_px"), self._rgb_offset, 1, 40, 1).pack(
            anchor="w", pady=px(3)
        )

        r = tk.Frame(opts, bg=CLR["bg"])
        r.pack(anchor="w", pady=px(3))
        tk.Label(r, text=t("effects.apply_from_s"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        tk.Entry(
            r,
            textvariable=self._rgb_start,
            width=8,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))
        frame_button(r, self._rgb_start, lambda: self._rgb_src.get().strip(), seconds_text).pack(
            side="left"
        )
        tk.Label(r, text=t("effects.duration_s_or_whole_video"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left"
        )
        tk.Entry(
            r,
            textvariable=self._rgb_dur,
            width=12,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))
        _spin(opts, "CRF:", self._rgb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._rgb_btn = self.make_render_btn(
            inner, t("effects.apply_rgb_glitch"), self._render_rgb, width=30
        )
        self._rgb_btn.pack(pady=px(10))
        self._rgb_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_rgb(self) -> None:
        src = self._rgb_src.get().strip()
        out = self._rgb_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        try:
            offset = int(self._rgb_offset.get())
            start = float(self._rgb_start.get())
        except ValueError:
            offset, start = 6, 0.0

        crf = self._rgb_crf.get()
        dur_str = self._rgb_dur.get().strip()
        whole = dur_str.lower() == "whole video"
        total = _probe_duration(src)
        end = total if whole else (start + float(dur_str))

        enable = f"between(t,{start},{end})"

        # Split channels, offset R right, B left, keep G centre
        # Then blend with addition
        cmd = fx.rgb_cmd(crf, enable, offset, out, src)
        self.run_ffmpeg(
            cmd,
            self._rgb_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._rgb_btn,
        )

    def _build_gl_vhs(self, nb: ttk.Notebook) -> None:
        inner, self._vhs_src, self._vhs_out = self._effect_page(
            nb,
            t("effects.vhs_retro"),
            t("effects.vhs_retro_effect"),
            t("effects.simulates_a_degraded_vhs_tape_colour_bleed"),
            "_vhs",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._vhs_strength = tk.StringVar(value="Medium")
        self._vhs_crf = tk.StringVar(value="18")

        _combo(
            opts,
            t("lut.intensity_label"),
            self._vhs_strength,
            ["Subtle", "Medium", "Heavy"],
            width=10,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._vhs_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._vhs_btn = self.make_render_btn(
            inner, t("effects.apply_vhs_effect"), self._render_vhs, width=30
        )
        self._vhs_btn.pack(pady=px(10))
        self._vhs_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_vhs(self) -> None:
        src = self._vhs_src.get().strip()
        out = self._vhs_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        crf = self._vhs_crf.get()
        level = self._vhs_strength.get()

        # Noise strength / chroma blur / scanline opacity by level
        params = {
            "Subtle": (15, 1, 1.5, 0.10),
            "Medium": (35, 2, 2.5, 0.18),
            "Heavy": (60, 3, 4.0, 0.28),
        }
        noise, chroma_blur, luma_blur, scan_alpha = params.get(level, params["Medium"])
        luma_blur = min(float(luma_blur), 2.0)  # unsharp accepts -2..5

        cmd = fx.vhs_cmd(chroma_blur, crf, luma_blur, noise, out, scan_alpha, src)
        self.run_ffmpeg(
            cmd,
            self._vhs_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._vhs_btn,
        )

    def _build_gl_grain(self, nb: ttk.Notebook) -> None:
        inner, self._gr_src, self._gr_out = self._effect_page(
            nb,
            t("effects.film_grain"),
            t("effects.film_grain_cinematic_noise"),
            t("effects.adds_organic_temporal_film_grain_used_by"),
            "_grain",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._gr_amount = tk.StringVar(value="20")
        self._gr_crf = tk.StringVar(value="18")
        _spin(opts, t("effects.grain_amount_0_100"), self._gr_amount, 1, 100, 5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._gr_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._gr_btn = self.make_render_btn(
            inner, t("effects.apply_film_grain"), self._render_grain, width=30
        )
        self._gr_btn.pack(pady=px(10))
        self._gr_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_grain(self) -> None:
        src = self._gr_src.get().strip()
        out = self._gr_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        try:
            amount = int(self._gr_amount.get())
        except ValueError:
            amount = 20

        crf = self._gr_crf.get()
        cmd = fx.grain_cmd(amount, crf, out, src)
        self.run_ffmpeg(
            cmd,
            self._gr_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._gr_btn,
        )

    def _build_gl_vignette(self, nb: ttk.Notebook) -> None:
        inner, self._vi_src, self._vi_out = self._effect_page(
            nb,
            t("effects.vignette"),
            t("effects.cinematic_vignette"),
            t("effects.darkens_the_edges_of_the_frame_drawing"),
            "_vignette",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._vi_angle = tk.StringVar(value="0.628")
        self._vi_crf = tk.StringVar(value="18")
        _spin(
            opts, t("effects.strength_angle_0_1_1_5"), self._vi_angle, 0.1, 1.5, 0.05, width=6
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._vi_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._vi_btn = self.make_render_btn(
            inner, t("effects.apply_vignette"), self._render_vignette, width=30
        )
        self._vi_btn.pack(pady=px(10))
        self._vi_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_vignette(self) -> None:
        src = self._vi_src.get().strip()
        out = self._vi_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        angle = self._vi_angle.get()
        crf = self._vi_crf.get()
        cmd = fx.vignette_cmd(angle, crf, out, src)
        self.run_ffmpeg(
            cmd,
            self._vi_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._vi_btn,
        )

    def _build_gl_lightleak(self, nb: ttk.Notebook) -> None:
        inner, self._ll_src, self._ll_out = self._effect_page(
            nb,
            t("effects.light_leak"),
            t("effects.light_leak_lens_flare"),
            t("effects.simulates_a_burst_of_warm_light_sweeping"),
            "_lightleak",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._ll_start = tk.StringVar(value="2.0")
        self._ll_dur = tk.StringVar(value="1.0")
        self._ll_color = tk.StringVar(value="Warm Orange")
        self._ll_crf = tk.StringVar(value="18")

        _spin(
            opts,
            t("effects.appear_at_s"),
            self._ll_start,
            0,
            3600,
            0.5,
            source=lambda: self._ll_src.get().strip(),
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("gif_maker.duration_label"), self._ll_dur, 0.2, 5, 0.2).pack(
            anchor="w", pady=px(3)
        )
        _combo(
            opts,
            t("effects.light_colour"),
            self._ll_color,
            ["Warm Orange", "Cool White", "Golden Yellow", "Soft Pink", "Electric Blue"],
            width=16,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._ll_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._ll_btn = self.make_render_btn(
            inner, t("effects.apply_light_leak"), self._render_lightleak, width=30
        )
        self._ll_btn.pack(pady=px(10))
        self._ll_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_lightleak(self) -> None:
        begun = self._fx_begin(self._ll_src, self._ll_out, "lightleak")
        if not begun:
            return
        src, out, info = begun
        try:
            start = self._fx_num(self._ll_start, t("btn.start"), 0)
            d = self._fx_num(self._ll_dur, t("splicer.column_duration"), 0.1, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = fx.geometry(info)
        colour = {
            "Warm Orange": "FFA040",
            "Cool White": "E8F0FF",
            "Golden Yellow": "FFD24A",
            "Soft Pink": "FF8FB8",
            "Electric Blue": "40A0FF",
        }.get(self._ll_color.get(), "FFA040")
        half = d / 2
        graph = fx.lightleak_graph(colour, dur, fps, h, half, start, w)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", graph=graph),
            self._ll_crf,
            self._ll_btn,
            self._ll_console,
        )

    def _build_motion_tab(self, nb: ttk.Notebook) -> None:
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.motion"))
        mo_nb = ttk.Notebook(outer)
        mo_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_mo_shake(mo_nb)
        self._build_mo_zoompunch(mo_nb)
        self._build_mo_kenburns(mo_nb)
        self._build_mo_whippan(mo_nb)

    def _build_mo_shake(self, nb: ttk.Notebook) -> None:
        inner, self._sh_src, self._sh_out = self._effect_page(
            nb,
            t("effects.camera_shake"),
            t("effects.camera_shake_2"),
            t("effects.simulates_an_unstabilised_handheld_camera_apply"),
            "_shake",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._sh_amp = tk.StringVar(value="10")
        self._sh_speed = tk.StringVar(value="Normal")
        self._sh_start = tk.StringVar(value="0")
        self._sh_dur = tk.StringVar(value="whole video")
        self._sh_crf = tk.StringVar(value="18")

        _spin(opts, t("effects.amplitude_px"), self._sh_amp, 2, 60, 2).pack(anchor="w", pady=px(3))
        _combo(
            opts,
            t("effects.shake_speed"),
            self._sh_speed,
            ["Slow", "Normal", "Fast", "Violent"],
            width=10,
        ).pack(anchor="w", pady=px(3))
        r = tk.Frame(opts, bg=CLR["bg"])
        r.pack(anchor="w", pady=px(3))
        tk.Label(r, text=t("common.start_s"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        tk.Entry(
            r,
            textvariable=self._sh_start,
            width=8,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))
        frame_button(r, self._sh_start, lambda: self._sh_src.get().strip(), seconds_text).pack(
            side="left"
        )
        tk.Label(r, text=t("effects.duration_s_whole_video"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left"
        )
        tk.Entry(
            r,
            textvariable=self._sh_dur,
            width=14,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))
        _spin(opts, "CRF:", self._sh_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._sh_btn = self.make_render_btn(
            inner, t("effects.apply_camera_shake"), self._render_shake, width=30
        )
        self._sh_btn.pack(pady=px(10))
        self._sh_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_shake(self) -> None:
        src = self._sh_src.get().strip()
        out = self._sh_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        # read the settings here, on the UI thread; only the probe runs in the background
        try:
            amp = int(self._sh_amp.get())
            start = float(self._sh_start.get())
        except ValueError:
            amp, start = 10, 0.0

        crf = self._sh_crf.get()
        speed_map = {"Slow": (7, 13), "Normal": (19, 31), "Fast": (37, 53), "Violent": (61, 79)}
        freq1, freq2 = speed_map.get(self._sh_speed.get(), (19, 31))

        dur_str = self._sh_dur.get().strip()
        try:
            span = None if dur_str.lower() == "whole video" else float(dur_str)
        except ValueError:
            messagebox.showwarning(
                t("common.warning"),
                t("effects.duration_must_be_seconds_or_whole_video", dur_str=dur_str),
            )
            return

        shake = fx.Shake(amp=amp, start=start, span=span, freq1=freq1, freq2=freq2, crf=crf)

        def _start(probed: tuple[float, tuple[int, int]]) -> None:
            total, size = probed
            cmd = fx.shake_cmd(src, out, size, total, shake)
            self.run_ffmpeg(
                cmd,
                self._sh_console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self._sh_btn,
            )

        tasks.submit(partial(_probe_length_and_size, src), on_done=_start, name="render_shake")

    def _build_mo_zoompunch(self, nb: ttk.Notebook) -> None:
        inner, self._zp_src, self._zp_out = self._effect_page(
            nb,
            t("effects.zoom_punch"),
            t("effects.zoom_punch_impact_zoom"),
            t("effects.a_sudden_very_fast_zoom_in_at"),
            "_zoompunch",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._zp_at = tk.StringVar(value="5.0")
        self._zp_zoom = tk.StringVar(value="1.4")
        self._zp_attack = tk.StringVar(value="0.1")
        self._zp_hold = tk.StringVar(value="0.3")
        self._zp_release = tk.StringVar(value="0.5")
        self._zp_crf = tk.StringVar(value="18")

        _spin(
            opts,
            t("effects.punch_at_s"),
            self._zp_at,
            0,
            3600,
            0.5,
            source=lambda: self._zp_src.get().strip(),
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("effects.zoom_factor"), self._zp_zoom, 1.1, 4, 0.05).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("effects.attack_s"), self._zp_attack, 0.03, 0.5, 0.02).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("effects.hold_at_peak_s"), self._zp_hold, 0.05, 2, 0.05).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("effects.release_ease_s"), self._zp_release, 0.1, 3, 0.1).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._zp_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._zp_btn = self.make_render_btn(
            inner, t("effects.apply_zoom_punch"), self._render_zoompunch, width=30
        )
        self._zp_btn.pack(pady=px(10))
        self._zp_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_zoompunch(self) -> None:
        begun = self._fx_begin(self._zp_src, self._zp_out, "zoompunch")
        if not begun:
            return
        src, out, info = begun
        try:
            at = self._fx_num(self._zp_at, "At", 0)
            z = self._fx_num(self._zp_zoom, t("effects.zoom"), 1.0, 5)
            a = self._fx_num(self._zp_attack, t("effects.attack"), 0.01, 10)
            hold = self._fx_num(self._zp_hold, t("effects.hold"), 0, 10)
            rel = self._fx_num(self._zp_release, t("effects.release"), 0.01, 10)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, _dur = fx.geometry(info)
        t1, t2, t3 = at + a, at + a + hold, at + a + hold + rel
        zexpr = (
            f"if(lt(it,{at:.3f}),1,if(lt(it,{t1:.3f}),1+({z}-1)*(it-{at:.3f})/{a:.3f},"
            f"if(lt(it,{t2:.3f}),{z},if(lt(it,{t3:.3f}),{z}-({z}-1)*(it-{t2:.3f})/{rel:.3f},1))))"
        )
        graph = fx.zoompunch_graph(fps, h, w, zexpr)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", chain=graph),
            self._zp_crf,
            self._zp_btn,
            self._zp_console,
        )

    def _build_mo_kenburns(self, nb: ttk.Notebook) -> None:
        inner, self._kb_src, self._kb_out = self._effect_page(
            nb,
            t("effects.ken_burns"),
            t("effects.ken_burns_pan_zoom"),
            t("effects.a_slow_cinematic_pan_and_zoom_across"),
            "_kenburns",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._kb_zoom_start = tk.StringVar(value="1.0")
        self._kb_zoom_end = tk.StringVar(value="1.15")
        self._kb_dir = tk.StringVar(value=next(iter(_KB_PANS)))
        self._kb_crf = tk.StringVar(value="18")

        _spin(opts, t("effects.zoom_start"), self._kb_zoom_start, 1.0, 2.0, 0.05).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("effects.zoom_end"), self._kb_zoom_end, 1.0, 2.0, 0.05).pack(
            anchor="w", pady=px(3)
        )
        _combo(
            opts,
            t("effects.pan_direction"),
            self._kb_dir,
            list(_KB_PANS),
            width=18,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._kb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._kb_btn = self.make_render_btn(
            inner, t("effects.apply_ken_burns"), self._render_kenburns, width=30
        )
        self._kb_btn.pack(pady=px(10))
        self._kb_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_kenburns(self) -> None:
        begun = self._fx_begin(self._kb_src, self._kb_out, "kenburns")
        if not begun:
            return
        src, out, info = begun
        try:
            z0 = self._fx_num(self._kb_zoom_start, t("effects.start_zoom"), 1.0, 5)
            z1 = self._fx_num(self._kb_zoom_end, t("effects.end_zoom"), 1.0, 5)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = fx.geometry(info)
        p = f"it/{max(dur, 0.1):.3f}"
        d = _KB_PANS.get(self._kb_dir.get(), "centre")
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        if d == "left":
            x = f"(iw-iw/zoom)*{p}"
        elif d == "right":
            x = f"(iw-iw/zoom)*(1-{p})"
        elif d == "top":
            y = f"(ih-ih/zoom)*{p}"
        elif d == "bottom":
            y = f"(ih-ih/zoom)*(1-{p})"
        graph = fx.kenburns_graph(fps, h, p, w, x, y, z0, z1)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", chain=graph),
            self._kb_crf,
            self._kb_btn,
            self._kb_console,
        )

    def _build_mo_whippan(self, nb: ttk.Notebook) -> None:
        inner, self._wp_src, self._wp_out = self._effect_page(
            nb,
            t("effects.whip_pan_blur"),
            t("effects.whip_pan_motion_blur"),
            t("effects.adds_an_extreme_horizontal_or_vertical_motion"),
            "_whippan",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._wp_at = tk.StringVar(value="5.0")
        self._wp_dur = tk.StringVar(value="0.3")
        self._wp_dir = tk.StringVar(value="Horizontal")
        self._wp_str = tk.StringVar(value="40")
        self._wp_crf = tk.StringVar(value="18")

        _spin(
            opts,
            t("effects.apply_at_s"),
            self._wp_at,
            0,
            3600,
            0.5,
            source=lambda: self._wp_src.get().strip(),
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("gif_maker.duration_label"), self._wp_dur, 0.1, 2, 0.05).pack(
            anchor="w", pady=px(3)
        )
        _combo(opts, t("zoom.direction"), self._wp_dir, ["Horizontal", "Vertical"], width=12).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, t("effects.blur_strength"), self._wp_str, 5, 120, 5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._wp_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._wp_btn = self.make_render_btn(
            inner, t("effects.apply_whip_pan_blur"), self._render_whippan, width=30
        )
        self._wp_btn.pack(pady=px(10))
        self._wp_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_whippan(self) -> None:
        begun = self._fx_begin(self._wp_src, self._wp_out, "whippan")
        if not begun:
            return
        src, out, info = begun
        try:
            at = self._fx_num(self._wp_at, "At", 0)
            d = self._fx_num(self._wp_dur, t("splicer.column_duration"), 0.01, 10)
            strength = int(self._fx_num(self._wp_str, t("region_blur.strength_col"), 1, 500))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, _fps, _dur = fx.geometry(info)
        horizontal = self._wp_dir.get() != "Vertical"
        size = max(1, min(strength, (w if horizontal else h) // 2 - 1))
        sx, sy = (size, 1) if horizontal else (1, size)
        graph = fx.whippan_graph(at, d, sx, sy)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", chain=graph),
            self._wp_crf,
            self._wp_btn,
            self._wp_console,
        )

    def _build_branding_tab(self, nb: ttk.Notebook) -> None:
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.branding"))
        br_nb = ttk.Notebook(outer)
        br_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_br_meme(br_nb)
        self._build_br_cinebars(br_nb)
        self._build_br_watermark(br_nb)

    def _build_br_meme(self, nb: ttk.Notebook) -> None:
        inner, self._mc_src, self._mc_out = self._effect_page(
            nb,
            t("effects.meme_caption"),
            t("effects.impact_meme_caption"),
            t("effects.burns_large_white_impact_style_text_with"),
            "_meme",
        )

        opts = _lf(inner, t("effects.caption_options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._mc_top = tk.StringVar(
            value=t_or("effects.sample.when_you_realise", "WHEN YOU REALISE")
        )
        self._mc_bot = tk.StringVar(
            value=t_or("effects.sample.it_was_that_easy", "IT WAS THAT EASY")
        )
        self._mc_size = tk.StringVar(value="72")
        self._mc_start = tk.StringVar(value="0")
        self._mc_hold = tk.StringVar(value="whole video")
        self._mc_crf = tk.StringVar(value="18")

        for lbl, var in [("Top text:", self._mc_top), ("Bottom text:", self._mc_bot)]:
            r = tk.Frame(opts, bg=CLR["bg"])
            r.pack(anchor="w", pady=px(3))
            tk.Label(r, text=lbl, bg=CLR["bg"], fg=CLR["fg"], width=16, anchor="e").pack(
                side="left"
            )
            tk.Entry(
                r,
                textvariable=var,
                width=40,
                bg=CLR["panel"],
                fg=CLR["fg"],
                insertbackground=CLR["fg"],
                font=(UI_FONT, 11),
            ).pack(side="left", padx=px(6))

        _spin(opts, t("intro_maker.font_size_label"), self._mc_size, 24, 180, 4).pack(
            anchor="w", pady=px(3)
        )
        r2 = tk.Frame(opts, bg=CLR["bg"])
        r2.pack(anchor="w", pady=px(3))
        tk.Label(r2, text=t("pip.show_from_label"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        tk.Entry(
            r2,
            textvariable=self._mc_start,
            width=8,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))
        frame_button(r2, self._mc_start, lambda: self._mc_src.get().strip(), seconds_text).pack(
            side="left"
        )
        tk.Label(r2, text=t("effects.duration_s_whole_video"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left"
        )
        tk.Entry(
            r2,
            textvariable=self._mc_hold,
            width=14,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))
        _spin(opts, "CRF:", self._mc_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._mc_btn = self.make_render_btn(
            inner, t("effects.burn_meme_captions"), self._render_meme, width=30
        )
        self._mc_btn.pack(pady=px(10))
        self._mc_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_meme(self) -> None:
        src = self._mc_src.get().strip()
        out = self._mc_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        try:
            size = int(self._mc_size.get())
            start = float(self._mc_start.get())
        except ValueError:
            size, start = 72, 0.0

        crf = self._mc_crf.get()
        top = self._mc_top.get()
        bot = self._mc_bot.get()
        dur_s = self._mc_hold.get().strip()
        total = _probe_duration(src)
        whole = dur_s.lower() == "whole video"
        end = total if whole else (start + float(dur_s))
        enable = f"between(t,{start},{end})"

        filters: list[str] = []
        for caption, y in ((top, "20"), (bot, "h-text_h-20")):
            if caption.strip():
                filters.append(
                    fl.DrawText(
                        textfile=fl.drawtext_text_file(caption),
                        font="impact",
                        fontsize=size,
                        fontcolor="white",
                        x="(w-text_w)/2",
                        y=y,
                        borderw=4,
                        bordercolor="black",
                        enable=enable,
                    ).filter()
                )

        if not filters:
            messagebox.showwarning(t("common.warning"), t("effects.enter_at_least_one_caption"))
            return

        cmd = fx.meme_cmd(crf, filters, out, src)
        self.run_ffmpeg(
            cmd,
            self._mc_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._mc_btn,
        )

    def _build_br_cinebars(self, nb: ttk.Notebook) -> None:
        inner, self._cb_src, self._cb_out = self._effect_page(
            nb,
            t("effects.cinematic_bars"),
            t("effects.cinematic_black_bars"),
            t("effects.adds_letterbox_bars_to_simulate_a_2"),
            "_cinebars",
        )

        opts = _lf(inner, t("section.options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._cb_ratio = tk.StringVar(value="2.39:1 (Scope)")
        self._cb_anim = tk.BooleanVar(value=True)
        self._cb_adur = tk.StringVar(value="1.0")
        self._cb_crf = tk.StringVar(value="18")

        _combo(
            opts,
            t("effects.aspect_ratio"),
            self._cb_ratio,
            ["2.39:1 (Scope)", "2.35:1 (Scope)", "2.20:1", "1.85:1 (Flat)"],
            width=18,
        ).pack(anchor="w", pady=px(3))
        r = tk.Frame(opts, bg=CLR["bg"])
        r.pack(anchor="w", pady=px(3))
        tk.Checkbutton(
            r,
            text=t("effects.animate_bars_sliding_in"),
            variable=self._cb_anim,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            activebackground=CLR["bg"],
        ).pack(side="left")
        _spin(opts, t("pip.animation_duration_label"), self._cb_adur, 0.2, 3, 0.2).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._cb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._cb_btn = self.make_render_btn(
            inner, t("effects.apply_cinematic_bars"), self._render_cinebars, width=30
        )
        self._cb_btn.pack(pady=px(10))
        self._cb_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_cinebars(self) -> None:
        begun = self._fx_begin(self._cb_src, self._cb_out, "cinebars")
        if not begun:
            return
        src, out, info = begun
        w, h, fps, _dur = fx.geometry(info)
        try:
            ratio = float(self._cb_ratio.get().split(":")[0])
            ad = self._fx_num(self._cb_adur, t("titles.animation"), 0.05, 30)
        except (ValueError, IndexError) as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        bar = max(0, int((h - w / ratio) / 2))
        if bar < 2:
            messagebox.showinfo(
                t("common.warning"),
                t("effects.this_video_is_already_as_wide_as"),
                parent=self,
            )
            return
        if self._cb_anim.get():
            ytop = f"if(lt(t,{ad:.3f}),-h+h*t/{ad:.3f},0)"
            ybot = f"if(lt(t,{ad:.3f}),H-h*t/{ad:.3f},H-h)"
        else:
            ytop, ybot = "0", "H-h"
        graph = fx.cinebars_graph(bar, fps, h, w, ybot, ytop)
        self._fx_run(
            RenderJob(src=src, out=out, info=info, name="Special Effects", graph=graph),
            self._cb_crf,
            self._cb_btn,
            self._cb_console,
        )

    def _build_br_watermark(self, nb: ttk.Notebook) -> None:
        inner, self._wm_src, self._wm_out = self._effect_page(
            nb,
            t("effects.social_watermark"),
            t("effects.social_media_watermark"),
            t("effects.burns_a_persistent_channel_handle_watermark_onto"),
            "_watermark",
        )

        opts = _lf(inner, t("effects.watermark_options"))
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._wm_text = tk.StringVar(value="@YourChannel")
        self._wm_size = tk.StringVar(value="38")
        self._wm_pos = tk.StringVar(value="Bottom Right")
        self._wm_alpha = tk.StringVar(value="0.70")
        self._wm_crf = tk.StringVar(value="18")

        r0 = tk.Frame(opts, bg=CLR["bg"])
        r0.pack(anchor="w", pady=px(3))
        tk.Label(r0, text=t("effects.watermark_text"), bg=CLR["bg"], fg=CLR["fg"]).pack(side="left")
        tk.Entry(
            r0,
            textvariable=self._wm_text,
            width=28,
            font=(UI_FONT, 12),
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(6))

        _spin(opts, t("intro_maker.font_size_label"), self._wm_size, 12, 100, 2).pack(
            anchor="w", pady=px(3)
        )
        _combo(
            opts,
            t("side_by_side.label_position_label"),
            self._wm_pos,
            ["Bottom Right", "Bottom Left", "Top Right", "Top Left", "Centre"],
            width=14,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, t("effects.opacity_0_1_1_0"), self._wm_alpha, 0.1, 1.0, 0.05, width=6).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._wm_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._wm_btn = self.make_render_btn(
            inner, t("watermark.burn_watermark"), self._render_watermark, width=30
        )
        self._wm_btn.pack(pady=px(10))
        self._wm_console = self.make_console_block(inner, height=7, bg=CLR["bg"])

    def _render_watermark(self) -> None:
        src = self._wm_src.get().strip()
        out = self._wm_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), t("effects.please_set_an_output_path"))
            return

        try:
            size = int(self._wm_size.get())
            alpha = float(self._wm_alpha.get())
        except ValueError:
            size, alpha = 38, 0.70

        crf = self._wm_crf.get()
        text = self._wm_text.get()
        pos = self._wm_pos.get()
        margin = 24

        pos_map = {
            "Bottom Right": (f"w-text_w-{margin}", f"h-text_h-{margin}"),
            "Bottom Left": (str(margin), f"h-text_h-{margin}"),
            "Top Right": (f"w-text_w-{margin}", str(margin)),
            "Top Left": (str(margin), str(margin)),
            "Centre": ("(w-text_w)/2", "(h-text_h)/2"),
        }
        x_e, y_e = pos_map.get(pos, pos_map["Bottom Right"])

        filter_str = fl.DrawText(
            textfile=fl.drawtext_text_file(text),
            font="sans bold",
            fontsize=size,
            fontcolor=f"white@{alpha}",
            x=x_e,
            y=y_e,
            shadowx=2,
            shadowy=2,
            shadowcolor=f"black@{min(alpha + 0.2, 1.0)}",
        ).filter()

        cmd = fx.watermark_cmd(crf, filter_str, out, src)
        self.run_ffmpeg(
            cmd,
            self._wm_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._wm_btn,
        )

    # ═════════════════════════════════════════════════════════════════════
    #  Shared helpers used by all render methods
    # ═════════════════════════════════════════════════════════════════════

    def load_file(self, path: str) -> bool:
        """A dropped video becomes the source on every effect's page."""
        if dialogs.kind_of(path) != "video":
            return False
        for var in self._sources:
            var.set(path)
        return True

    def _effect_page(
        self, nb: ttk.Notebook, tab: str, title: str, hint: str, suffix: str
    ) -> tuple[tk.Frame, tk.StringVar, tk.StringVar]:
        """One effect's tab: its title and what it does, the source video and
        where the result goes (suggested from the source with *suffix*).
        Returns (the frame to add options to, source var, output var)."""
        f = ttk.Frame(nb)
        nb.add(f, text=tab)
        inner = scroll_body(f)
        tk.Label(
            inner, text=title, font=(UI_FONT, 12, "bold"), bg=CLR["bg"], fg=CLR["accent"]
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(inner, hint)
        src, out = tk.StringVar(), tk.StringVar()
        self._sources.append(src)
        FileRow(
            inner, t("common.source_video"), src, on_change=partial(self._suggest_out, out, suffix)
        ).pack(fill="x", padx=px(18), pady=px(12, 4))
        SaveRow(inner, t("common.output_file"), out).pack(fill="x", padx=px(18), pady=px(4))
        return inner, src, out

    @staticmethod
    def _suggest_out(out_var: tk.StringVar, suffix: str, src: str) -> None:
        """Name the output after a new source, unless one is already chosen."""
        if not out_var.get():
            out_var.set(os.path.splitext(src)[0] + suffix + ".mp4")
