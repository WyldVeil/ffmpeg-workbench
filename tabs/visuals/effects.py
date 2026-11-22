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

import logging
import os
import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core import tasks
from core.hardware import (
    get_binary_path,
)
from core.i18n import t
from core.scaling import px
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit import dialogs

logger = logging.getLogger("crossfader.visuals.effects")

# ─────────────────────────────────────────────────────────────────────────────
#  Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

VIDEO_TYPES = [
    (t("silence.video_files"), "*.mp4 *.mov *.mkv *.avi *.webm *.flv *.m4v"),
    (t("youtube.all_files"), t("ducker.item_2")),
]


def _ffmpeg():
    return get_binary_path("ffmpeg")


def _ffprobe():
    return get_binary_path("ffprobe")


def _probe_duration(path):
    """Duration of *path* in seconds, or 0.0."""
    return ff.duration_of(path)


def _probe_wh(path):
    """Display width/height (rotation applied, even), or (1920, 1080)."""
    info = ff.try_probe(path)
    if info is None or not info.has_video:
        return 1920, 1080
    w, h = info.require_video().display_size
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


def _first_visible_time(path, scan=10.0):
    """End of any black opening (blackdetect), or 0.0; fast, single pass."""
    try:
        r = ff.capture(
            [
                ff.ffmpeg_path(),
                "-hide_banner",
                "-nostdin",
                "-t",
                f"{scan:.1f}",
                "-i",
                path,
                "-vf",
                "blackdetect=d=0.05:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "-",
            ],
            timeout=60,
        )
        import re as _re

        m = _re.search(r"black_start:0(?:\.0+)?\s+black_end:([\d.]+)", r.stderr or "")
        return float(m.group(1)) + 0.05 if m else 0.0
    except Exception:
        logger.debug("_first_visible_time: best-effort step failed", exc_info=True)
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  Reusable sub-widget builders
# ─────────────────────────────────────────────────────────────────────────────


def _make_scrollable(parent):
    """Return (canvas, inner_frame) - a vertically scrollable container."""
    canvas = tk.Canvas(parent, highlightthickness=0, bg=CLR["bg"])
    sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=CLR["bg"])
    canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    return canvas, inner


def _src_row(parent, src_var, browse_cmd):
    """Standard source-file browse row."""
    f = tk.Frame(parent, bg=CLR["bg"])
    f.pack(fill="x", padx=px(18), pady=px(12, 4))
    tk.Label(
        f, text=t("common.source_video"), font=(UI_FONT, 10, "bold"), bg=CLR["bg"], fg=CLR["fg"]
    ).pack(side="left")
    tk.Entry(
        f, textvariable=src_var, width=56, bg=CLR["panel"], fg=CLR["fg"], insertbackground=CLR["fg"]
    ).pack(side="left", padx=px(8))
    tk.Button(f, text=t("btn.browse"), command=browse_cmd, bg=CLR["panel"], fg=CLR["fg"]).pack(
        side="left"
    )


def _out_row(parent, out_var, browse_cmd):
    """Standard output-file save row."""
    f = tk.Frame(parent, bg=CLR["bg"])
    f.pack(fill="x", padx=px(18), pady=px(4))
    tk.Label(
        f, text=t("common.output_file"), font=(UI_FONT, 10, "bold"), bg=CLR["bg"], fg=CLR["fg"]
    ).pack(side="left")
    tk.Entry(
        f, textvariable=out_var, width=60, bg=CLR["panel"], fg=CLR["fg"], insertbackground=CLR["fg"]
    ).pack(side="left", padx=px(8))
    tk.Button(f, text=t("common.save_as"), command=browse_cmd, bg=CLR["panel"], fg=CLR["fg"]).pack(
        side="left"
    )


def _run_btn(parent, text, command, color=None):
    color = color or CLR["accent"]
    btn = tk.Button(
        parent,
        text=text,
        font=(UI_FONT, 12, "bold"),
        bg=color,
        fg="black",
        height=2,
        width=30,
        cursor="hand2",
        command=command,
    )
    btn.pack(pady=px(10))
    return btn


def _console_block(parent, height=7):
    f = tk.Frame(parent, bg=CLR["bg"])
    f.pack(fill="both", expand=True, padx=px(18), pady=px(4, 12))
    c, sb = BaseTab.make_console(f, height=height)
    c.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return c


def _lf(parent, title, **kw):
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


def _spin(parent, label, var, from_, to, resolution=0.5, width=6):
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
    return f


def _combo(parent, label, var, values, width=18):
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


def _hint(parent, text):
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


class SpecialEffectsTab(BaseTab):
    """
    Special Effects Studio - six categories of popular video effects,
    each in its own sub-tab.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self._build_ui()
        self._add_frame_pickers()

    #: the effects' "at" times, each picked from frames of its own source video
    PICKED_TIMES = (
        "_em_start",
        "_lt_start",
        "_sub_start",
        "_cd_start",
        "_rgb_start",
        "_ll_start",
        "_sh_start",
        "_zp_at",
        "_wp_at",
        "_mc_start",
    )

    def _add_frame_pickers(self):
        from tabs.widgets import add_pickers

        self._pickers = 0
        for name in self.PICKED_TIMES:
            src = getattr(self, name.rsplit("_", 1)[0] + "_src")
            self._pickers += add_pickers(
                self,
                [getattr(self, name)],
                lambda s=src: s.get().strip(),
                fmt=lambda t: f"{t:.3f}".rstrip("0").rstrip(".") or "0",
            )

    # ── Shared plumbing for every effect ──────────────────────────────────
    #: Appended to every video chain: even dimensions, square pixels and
    #: 4:2:0 so H.264 output plays everywhere (odd sizes / 10-bit / RGB
    #: filters used to produce unplayable or failing renders).
    FX_GUARD = "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p"

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
        if not inputs and "-i" in cmd:
            src = cmd[cmd.index("-i") + 1]
            final = self.confirm_output(cmd[-1], [src])
            if not final:
                if btn is not None:
                    btn.config(state="normal")
                return None
            cmd[-1] = final
            if "libx264" in cmd and "-pix_fmt" not in cmd:
                cmd[-1:-1] = ["-pix_fmt", "yuv420p"]
            inputs = [src]
            if duration is None:
                duration = ff.duration_of(src) or None
        return super().run_ffmpeg(
            cmd, console, on_done, btn, duration=duration, output=output, inputs=inputs, name=name
        )

    def _fx_num(self, var, label, lo=None, hi=None):
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

    def _fx_begin(self, src_var, out_var, suffix="fx"):
        """Validate source/output; returns (src, out, info) or None."""
        src = src_var.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return None
        info = ff.try_probe(src)
        if info is None or not info.has_video:
            messagebox.showerror(t("common.error"), f"Not a readable video:\n{src}", parent=self)
            return None
        out = out_var.get().strip() or self.suggest_output(src, suffix, ".mp4")
        out = self.confirm_output(out, [src])
        if not out:
            return None
        out_var.set(out)
        return src, out, info

    @staticmethod
    def _fx_geom(info):
        """(even width, even height, exact fps string, duration) of a source."""
        v = info.require_video()
        w, h = v.display_size
        w, h = max(2, w // 2 * 2), max(2, h // 2 * 2)
        fps = v.raw.get("avg_frame_rate") or "30/1"
        if fps in ("0/0", "") or not v.fps or v.fps > 240:
            fps = "30/1"
        dur = v.duration if v.duration > 0.05 else info.duration
        return w, h, fps, dur

    def _fx_venc(self, crf_var):
        crf = str(crf_var.get()).strip() or "18"
        if not crf.isdigit():
            crf = "18"
        return [
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ]

    def _fx_audio(self, info, copy=True):
        if not info.has_audio:
            return ["-an"]
        if copy and (info.require_audio().codec in ("aac", "mp3", "ac3", "opus", "alac", "flac")):
            return ["-map", "0:a:0", "-c:a", "copy"]
        return ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]

    def _fx_run(
        self,
        src,
        out,
        info,
        graph,
        crf_var,
        btn,
        label,
        console,
        *,
        complex_graph=True,
        pre_inputs=(),
        audio=None,
        duration=None,
        extra=(),
    ):
        """Build and queue the render: [inputs] graph [audio] libx264 out."""
        cmd = [ff.ffmpeg_path(), "-y", *pre_inputs, "-i", src]
        if complex_graph:
            cmd += ["-filter_complex", graph, "-map", "[v]"]
        else:
            cmd += ["-vf", graph, "-map", "0:v:0"]
        cmd += list(audio if audio is not None else self._fx_audio(info))
        cmd += [*extra, *self._fx_venc(crf_var), out]
        self.log(console, f"Rendering {os.path.basename(out)}…")
        self.run_ffmpeg(
            cmd,
            console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=btn,
            duration=duration or self._fx_geom(info)[3],
            output=out,
            inputs=[src],
            name="Special Effects",
        )

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="✨  " + t("tab.special_effects"),
            font=(UI_FONT, 16, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
        ).pack(side="left", padx=px(20), pady=px(12))
        tk.Label(
            hdr,
            text=t("effects.top_youtube_social_media_effects"),
            bg=CLR["panel"],
            fg=CLR["fgdim"],
        ).pack(side="left")

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

    def _build_intros_tab(self, nb):
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

    def _build_intro_fadein(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  Fade-In  ")
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.fade_in_from_static_frame"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "A still frame from the start of your video fades out over the live "
            "footage, so you see the image dissolve into the actual video. "
            "No frames are lost. The tool automatically finds the first "
            "non-black frame for you.",
        )

        self._fi_src = tk.StringVar()
        self._fi_out = tk.StringVar()
        _src_row(
            inner, self._fi_src, lambda: self._browse_src(self._fi_src, self._fi_out, "_fadein")
        )
        _out_row(inner, self._fi_out, lambda: self._browse_out(self._fi_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._fi_dur = tk.StringVar(value="2.5")
        self._fi_start = tk.StringVar(value="auto")
        self._fi_crf = tk.StringVar(value="18")

        r = _spin(opts, "Fade duration (s):", self._fi_dur, 0.5, 10, 0.5)
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

        r3 = _spin(opts, "CRF (quality):", self._fi_crf, 12, 30, 1)
        r3.pack(anchor="w", pady=px(3))

        self._fi_btn = _run_btn(inner, "✨  APPLY FADE-IN", self._render_fadein, CLR["accent"])
        self._fi_console = _console_block(inner)

    def _render_fadein(self):
        label = "✨  APPLY FADE-IN"
        begun = self._fx_begin(self._fi_src, self._fi_out, "fadein")
        if not begun:
            return
        src, out, info = begun
        try:
            fade = self._fx_num(self._fi_dur, "Fade duration", 0.1, 60)
            custom = (
                None
                if self._fi_start.get() == "auto"
                else self._fx_num(self._fi_custom_t, "Timestamp", 0)
            )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        _w, _h, _fps, dur = self._fx_geom(info)
        still_at = custom if custom is not None else _first_visible_time(src)
        still_at = max(0.0, min(still_at, max(0.0, dur - 0.1)))
        self.log(self._fi_console, f"  Still frame at {still_at:.2f}s")
        graph = (
            f"[0:v]setpts=PTS-STARTPTS,split[m][s];"
            f"[s]trim=start={still_at:.3f},setpts=PTS-STARTPTS,trim=end_frame=1,"
            f"loop=loop=-1:size=1,trim=duration={fade:.3f},setpts=PTS-STARTPTS,"
            f"format=rgba,fade=t=out:st=0:d={fade:.3f}:alpha=1[ov];"
            f"[m][ov]overlay=eof_action=pass,{self.FX_GUARD}[v]"
        )
        self._fx_run(src, out, info, graph, self._fi_crf, self._fi_btn, label, self._fi_console)

    # ── Intro 2: Zoom Burst ───────────────────────────────────────────────

    def _build_intro_zoomburst(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.zoom_burst"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.zoom_burst_in"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "The video starts massively zoomed-in and rapidly pulls back to "
            "full frame, like a camera snap-zoom. Great for high-energy openers. "
            "Uses zoompan filter; original video is untouched after the burst ends.",
        )

        self._zb_src = tk.StringVar()
        self._zb_out = tk.StringVar()
        _src_row(
            inner, self._zb_src, lambda: self._browse_src(self._zb_src, self._zb_out, "_zoomburst")
        )
        _out_row(inner, self._zb_out, lambda: self._browse_out(self._zb_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._zb_dur = tk.StringVar(value="1.5")
        self._zb_zoom = tk.StringVar(value="3.0")
        self._zb_crf = tk.StringVar(value="18")

        _spin(opts, "Burst duration (s):", self._zb_dur, 0.3, 5, 0.1).pack(anchor="w", pady=px(3))
        _spin(opts, "Starting zoom factor (×):", self._zb_zoom, 1.5, 8, 0.5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF (quality):", self._zb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._zb_btn = _run_btn(inner, "🔍  APPLY ZOOM BURST", self._render_zoomburst, CLR["green"])
        self._zb_console = _console_block(inner)

    def _render_zoomburst(self):
        label = "\U0001f50d  APPLY ZOOM BURST"
        begun = self._fx_begin(self._zb_src, self._zb_out, "zoomburst")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._zb_dur, "Duration", 0.1, 30)
            z = self._fx_num(self._zb_zoom, "Zoom", 1.0, 10)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, _dur = self._fx_geom(info)
        n = max(1, round(d * (info.require_video().fps or 30)))
        graph = (
            f"zoompan=z='if(lte(on,{n}),{z}-({z}-1)*on/{n},1)':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},{self.FX_GUARD}"
        )
        self._fx_run(
            src,
            out,
            info,
            graph,
            self._zb_crf,
            self._zb_btn,
            label,
            self._zb_console,
            complex_graph=False,
        )

    # ── Intro 3: Slide In ─────────────────────────────────────────────────

    def _build_intro_slidein(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.slide_in"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.slide_in_2"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "The video frame slides onto a black background from any edge. "
            "After the animation the video plays normally at full size.",
        )

        self._sl_src = tk.StringVar()
        self._sl_out = tk.StringVar()
        _src_row(
            inner, self._sl_src, lambda: self._browse_src(self._sl_src, self._sl_out, "_slidein")
        )
        _out_row(inner, self._sl_out, lambda: self._browse_out(self._sl_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._sl_dir = tk.StringVar(value="Left")
        self._sl_dur = tk.StringVar(value="1.2")
        self._sl_crf = tk.StringVar(value="18")

        _combo(
            opts, "Slide from:", self._sl_dir, ["Left", "Right", "Top", "Bottom"], width=12
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "Slide duration (s):", self._sl_dur, 0.2, 5, 0.1).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._sl_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._sl_btn = _run_btn(inner, "↔  APPLY SLIDE-IN", self._render_slidein, CLR["orange"])
        self._sl_console = _console_block(inner)

    def _render_slidein(self):
        label = "↔  APPLY SLIDE-IN"
        begun = self._fx_begin(self._sl_src, self._sl_out, "slidein")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._sl_dur, "Duration", 0.1, 30)
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
        graph = (
            f"[0:v]setpts=PTS-STARTPTS,split[a][b];[a]drawbox=c=black:t=fill[bg];"
            f"[bg][b]overlay=x='{x}':y='{y}':eval=frame,{self.FX_GUARD}[v]"
        )
        self._fx_run(src, out, info, graph, self._sl_crf, self._sl_btn, label, self._sl_console)

    # ── Intro 4: Spin In ──────────────────────────────────────────────────

    def _build_intro_spin(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.spin_in"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.spin_in_2"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "The video rotates in from a small, spinning state and expands "
            "to fill the frame. A popular dramatic opener. "
            "Achieved via rotate + scale expressions on the first N frames.",
        )

        self._sp_src = tk.StringVar()
        self._sp_out = tk.StringVar()
        _src_row(
            inner, self._sp_src, lambda: self._browse_src(self._sp_src, self._sp_out, "_spinin")
        )
        _out_row(inner, self._sp_out, lambda: self._browse_out(self._sp_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._sp_dur = tk.StringVar(value="1.5")
        self._sp_turns = tk.StringVar(value="1")
        self._sp_crf = tk.StringVar(value="18")

        _spin(opts, "Spin duration (s):", self._sp_dur, 0.3, 5, 0.1).pack(anchor="w", pady=px(3))
        _spin(opts, "Number of rotations:", self._sp_turns, 1, 5, 1).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._sp_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._sp_btn = _run_btn(inner, "🌀  APPLY SPIN-IN", self._render_spinin, CLR["pink"])
        self._sp_console = _console_block(inner)

    def _render_spinin(self):
        label = "\U0001f300  APPLY SPIN-IN"
        begun = self._fx_begin(self._sp_src, self._sp_out, "spinin")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._sp_dur, "Duration", 0.1, 30)
            turns = self._fx_num(self._sp_turns, "Turns", 0, 20)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        k = f"if(lte(t,{d:.3f}),0.05+0.95*pow(t/{d:.3f},2),1)"
        graph = (
            f"[0:v]setpts=PTS-STARTPTS,split[a][b];[a]drawbox=c=black:t=fill[bg];"
            f"[b]format=rgba,rotate=a='if(lte(t,{d:.3f}),{turns}*2*PI*(1-pow(t/{d:.3f},2)),0)':c=none,"
            f"scale=w='max(2,trunc(iw*{k}/2)*2)':h='max(2,trunc(ih*{k}/2)*2)':eval=frame[s];"
            f"[bg][s]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame,{self.FX_GUARD}[v]"
        )
        self._fx_run(src, out, info, graph, self._sp_crf, self._sp_btn, label, self._sp_console)

    # ── Intro 5: Split Open ───────────────────────────────────────────────

    def _build_intro_splitopen(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.split_open"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.split_open_2"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "The screen is split horizontally. Top half slides up and bottom half "
            "slides down, revealing the video. The cinematic curtain reveal. "
            "Achieved with two crop+overlay layers animating in opposite directions.",
        )

        self._spo_src = tk.StringVar()
        self._spo_out = tk.StringVar()
        _src_row(
            inner,
            self._spo_src,
            lambda: self._browse_src(self._spo_src, self._spo_out, "_splitopen"),
        )
        _out_row(inner, self._spo_out, lambda: self._browse_out(self._spo_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._spo_dur = tk.StringVar(value="1.0")
        self._spo_dir = tk.StringVar(value="Vertical (curtain)")
        self._spo_crf = tk.StringVar(value="18")

        _spin(opts, "Open duration (s):", self._spo_dur, 0.2, 4, 0.1).pack(anchor="w", pady=px(3))
        _combo(
            opts,
            "Split axis:",
            self._spo_dir,
            ["Vertical (curtain)", "Horizontal (book)"],
            width=22,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._spo_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._spo_btn = _run_btn(inner, "↕  APPLY SPLIT-OPEN", self._render_splitopen, "#9C27B0")
        self._spo_console = _console_block(inner)

    def _render_splitopen(self):
        label = "↕  APPLY SPLIT-OPEN"
        begun = self._fx_begin(self._spo_src, self._spo_out, "splitopen")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._spo_dur, "Duration", 0.1, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, _dur = self._fx_geom(info)
        trans = "vertopen" if "Vertical" in self._spo_dir.get() else "horzopen"
        graph = (
            f"color=c=black:s={w}x{h}:r={fps}:d={d:.3f},format=yuv420p,setsar=1,settb=AVTB[blk];"
            f"[0:v]setpts=PTS-STARTPTS,scale={w}:{h},fps={fps},format=yuv420p,setsar=1,settb=AVTB[vid];"
            f"[blk][vid]xfade=transition={trans}:duration={d:.3f}:offset=0,{self.FX_GUARD}[v]"
        )
        self._fx_run(src, out, info, graph, self._spo_crf, self._spo_btn, label, self._spo_console)

    def _build_outros_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.outros"))
        outro_nb = ttk.Notebook(outer)
        outro_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_outro_fadeout(outro_nb)
        self._build_outro_freezezoom(outro_nb)
        self._build_outro_irisclose(outro_nb)

    def _build_outro_fadeout(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.fade_out"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.fade_to_black"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "The last N seconds of your video fade smoothly to black. "
            "Audio fades out in parallel. Clean and professional.",
        )

        self._fo_src = tk.StringVar()
        self._fo_out = tk.StringVar()
        _src_row(
            inner, self._fo_src, lambda: self._browse_src(self._fo_src, self._fo_out, "_fadeout")
        )
        _out_row(inner, self._fo_out, lambda: self._browse_out(self._fo_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._fo_dur = tk.StringVar(value="2.0")
        self._fo_crf = tk.StringVar(value="18")
        _spin(opts, "Fade duration (s):", self._fo_dur, 0.3, 10, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._fo_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._fo_btn = _run_btn(inner, "⬛  APPLY FADE-OUT", self._render_fadeout, "#455A64")
        self._fo_console = _console_block(inner)

    def _render_fadeout(self):
        src = self._fo_src.get().strip()
        out = self._fo_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
            return

        dur = float(self._fo_dur.get())
        crf = self._fo_crf.get()
        total = _probe_duration(src)
        start = max(0, total - dur)

        cmd = [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-vf",
            f"fade=type=out:st={start}:d={dur}",
            "-af",
            f"afade=type=out:st={start}:d={dur}",
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-movflags",
            "+faststart",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self._fo_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._fo_btn,
        )

    def _build_outro_freezezoom(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.freeze_zoom_out"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.freeze_frame_zoom_out"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "The last frame of the video is frozen, then slowly zooms out and "
            "fades to black. A classic cinematic ending.",
        )

        self._fz_src = tk.StringVar()
        self._fz_out = tk.StringVar()
        _src_row(
            inner, self._fz_src, lambda: self._browse_src(self._fz_src, self._fz_out, "_freezezoom")
        )
        _out_row(inner, self._fz_out, lambda: self._browse_out(self._fz_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._fz_hold = tk.StringVar(value="2.5")
        self._fz_fade = tk.StringVar(value="1.0")
        self._fz_crf = tk.StringVar(value="18")
        _spin(opts, "Hold duration (s):", self._fz_hold, 0.5, 10, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "Fade duration (s):", self._fz_fade, 0.3, 5, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._fz_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._fz_btn = _run_btn(
            inner, "🎞  APPLY FREEZE & ZOOM OUT", self._render_freezezoom, "#37474F"
        )
        self._fz_console = _console_block(inner)

    def _render_freezezoom(self):
        label = "\U0001f39e  APPLY FREEZE & ZOOM OUT"
        begun = self._fx_begin(self._fz_src, self._fz_out, "freezezoom")
        if not begun:
            return
        src, out, info = begun
        try:
            hold = self._fx_num(self._fz_hold, "Hold", 0.1, 60)
            fade = self._fx_num(self._fz_fade, "Fade", 0, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = self._fx_geom(info)
        extra = hold + fade
        graph = (
            f"[0:v]setpts=PTS-STARTPTS,trim=duration={dur:.3f},tpad=stop_mode=clone:stop_duration={extra:.3f},"
            f"zoompan=z='if(gte(it,{dur:.3f}),max(1,1.1-0.1*(it-{dur:.3f})/{max(hold, 0.1):.3f}),1)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps}"
            + (f",fade=t=out:st={dur + hold:.3f}:d={fade:.3f}" if fade > 0 else "")
            + f",{self.FX_GUARD}[v]"
        )
        audio = ["-an"]
        if info.has_audio:
            af = f"apad=pad_dur={extra:.3f}" + (
                f",afade=t=out:st={dur + hold:.3f}:d={fade:.3f}" if fade > 0 else ""
            )
            audio = ["-map", "0:a:0", "-af", af, "-c:a", "aac", "-b:a", "192k"]
        self._fx_run(
            src,
            out,
            info,
            graph,
            self._fz_crf,
            self._fz_btn,
            label,
            self._fz_console,
            audio=audio,
            duration=dur + extra,
        )

    def _build_outro_irisclose(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.iris_close"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.iris_vignette_close"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "A black oval vignette closes in from the edges to the centre "
            "like a classic iris wipe, beloved in cinema and vintage content.",
        )

        self._ir_src = tk.StringVar()
        self._ir_out = tk.StringVar()
        _src_row(
            inner, self._ir_src, lambda: self._browse_src(self._ir_src, self._ir_out, "_irisclose")
        )
        _out_row(inner, self._ir_out, lambda: self._browse_out(self._ir_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._ir_dur = tk.StringVar(value="1.5")
        self._ir_crf = tk.StringVar(value="18")
        _spin(opts, "Iris duration (s):", self._ir_dur, 0.3, 6, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._ir_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._ir_btn = _run_btn(inner, "⭕  APPLY IRIS CLOSE", self._render_irisclose, "#263238")
        self._ir_console = _console_block(inner)

    def _render_irisclose(self):
        label = "⭕  APPLY IRIS CLOSE"
        begun = self._fx_begin(self._ir_src, self._ir_out, "irisclose")
        if not begun:
            return
        src, out, info = begun
        try:
            d = self._fx_num(self._ir_dur, "Duration", 0.1, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = self._fx_geom(info)
        d = min(d, max(0.1, dur - 0.05))
        start = max(0.0, dur - d)
        graph = (
            f"[0:v]setpts=PTS-STARTPTS,trim=duration={dur:.3f},scale={w}:{h},fps={fps},"
            f"format=yuv420p,setsar=1,settb=AVTB[v0];"
            f"color=c=black:s={w}x{h}:r={fps}:d={d:.3f},format=yuv420p,setsar=1,settb=AVTB[blk];"
            f"[v0][blk]xfade=transition=circleclose:duration={d:.3f}:offset={start:.3f},{self.FX_GUARD}[v]"
        )
        audio = ["-an"]
        if info.has_audio:
            audio = [
                "-map",
                "0:a:0",
                "-af",
                f"afade=t=out:st={start:.3f}:d={d:.3f}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        self._fx_run(
            src, out, info, graph, self._ir_crf, self._ir_btn, label, self._ir_console, audio=audio
        )

    def _build_overlays_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.overlays"))
        ov_nb = ttk.Notebook(outer)
        ov_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_ov_emoji(ov_nb)
        self._build_ov_lowerthird(ov_nb)
        self._build_ov_subscribe(ov_nb)
        self._build_ov_countdown(ov_nb)
        self._build_ov_progressbar(ov_nb)

    def _build_ov_emoji(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.emoji_burst"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.emoji_text_burst"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Slam a large emoji or short text onto the video at a specific "
            "timestamp with a pop-in scale animation. "
            "Set the position (% of frame), appear time, and hold duration.",
        )

        self._em_src = tk.StringVar()
        self._em_out = tk.StringVar()
        _src_row(
            inner, self._em_src, lambda: self._browse_src(self._em_src, self._em_out, "_emoji")
        )
        _out_row(inner, self._em_out, lambda: self._browse_out(self._em_out))

        opts = _lf(inner, "Emoji / Text Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._em_text = tk.StringVar(value="😂")
        self._em_size = tk.StringVar(value="96")
        self._em_x = tk.StringVar(value="50")
        self._em_y = tk.StringVar(value="30")
        self._em_start = tk.StringVar(value="3.0")
        self._em_hold = tk.StringVar(value="2.0")
        self._em_crf = tk.StringVar(value="18")

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

        _spin(opts, "Font size (px):", self._em_size, 24, 256, 8).pack(anchor="w", pady=px(3))

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

        _spin(opts, "Appear at (s):", self._em_start, 0, 3600, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "Hold for (s):", self._em_hold, 0.2, 30, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._em_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._em_btn = _run_btn(
            inner, "😂  BURN EMOJI ONTO VIDEO", self._render_emoji, CLR["orange"]
        )
        self._em_console = _console_block(inner)

    def _render_emoji(self):
        src = self._em_src.get().strip()
        out = self._em_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
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

        def _start(probed):
            w, h = probed
            end = start + hold

            # x/y as absolute pixels, centred on the chosen %
            x_px = int(w * xpct)
            y_px = int(h * ypct)

            # Pop-in: over the first 0.2 s the emoji grows from nothing to full size
            scale_expr = f"if(lt(t-{start},0.2),max(1,{size}*(t-{start})/0.2),{size})"

            filter_str = fl.DrawText(
                textfile=fl.drawtext_text_file(text),
                font="emoji",
                fontsize=scale_expr,
                fontcolor="white",
                x=f"{x_px}-text_w/2",
                y=f"{y_px}-text_h/2",
                shadowx=3,
                shadowy=3,
                shadowcolor="black@0.7",
                enable=f"between(t,{start},{end})",
            ).filter()

            cmd = [
                _ffmpeg(),
                "-y",
                "-i",
                src,
                "-vf",
                filter_str,
                "-c:v",
                "libx264",
                "-crf",
                crf,
                "-preset",
                "fast",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                out,
            ]
            self.run_ffmpeg(
                cmd,
                self._em_console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self._em_btn,
            )

        tasks.submit(lambda: _probe_wh(src), on_done=_start, name="render_emoji")

    def _build_ov_lowerthird(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.lower_third"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.lower_third_banner"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "A professional name/title bar that slides up from the bottom. "
            "Used for speaker IDs, location captions, product names, "
            "and news-style graphics.",
        )

        self._lt_src = tk.StringVar()
        self._lt_out = tk.StringVar()
        _src_row(inner, self._lt_src, lambda: self._browse_src(self._lt_src, self._lt_out, "_lt"))
        _out_row(inner, self._lt_out, lambda: self._browse_out(self._lt_out))

        opts = _lf(inner, "Banner Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._lt_title = tk.StringVar(value="John Smith")
        self._lt_subtitle = tk.StringVar(value="Content Creator")
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

        _spin(opts, "Appear at (s):", self._lt_start, 0, 3600, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "Hold for (s):", self._lt_hold, 0.5, 60, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._lt_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._lt_btn = _run_btn(inner, "📛  BURN LOWER THIRD", self._render_lowerthird, "#1565C0")
        self._lt_console = _console_block(inner)

    def _render_lowerthird(self):
        label = "\U0001f4db  BURN LOWER THIRD"
        begun = self._fx_begin(self._lt_src, self._lt_out, "lowerthird")
        if not begun:
            return
        src, out, info = begun
        try:
            start = self._fx_num(self._lt_start, "Start", 0)
            hold = self._fx_num(self._lt_hold, "Hold", 0.1)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, _dur = self._fx_geom(info)
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
        graph = (
            f"color=c=0x{colour}@0.85:s={w}x{bar_h}:r={fps},format=rgba[bar];"
            f"[0:v][bar]overlay=x=0:y='{slide}':enable='{enable}':shortest=1,"
            f"{title},{sub},{self.FX_GUARD}[v]"
        )
        self._fx_run(src, out, info, graph, self._lt_crf, self._lt_btn, label, self._lt_console)

    def _build_ov_subscribe(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.subscribe_bump"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.subscribe_like_reminder"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Burns a customisable call-to-action reminder onto your video. "
            '"Like & Subscribe!", "Follow for more", etc. '
            "Animates in with a bounce and fades out cleanly.",
        )

        self._sub_src = tk.StringVar()
        self._sub_out = tk.StringVar()
        _src_row(
            inner,
            self._sub_src,
            lambda: self._browse_src(self._sub_src, self._sub_out, "_subscribe"),
        )
        _out_row(inner, self._sub_out, lambda: self._browse_out(self._sub_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._sub_text = tk.StringVar(value="👍 Like & Subscribe! 🔔")
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

        _spin(opts, "Font size:", self._sub_size, 24, 120, 4).pack(anchor="w", pady=px(3))
        _combo(
            opts,
            "Position:",
            self._sub_pos,
            ["Bottom Centre", "Top Centre", "Bottom Left", "Bottom Right"],
            width=16,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "Appear at (s):", self._sub_start, 0, 3600, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "Hold for (s):", self._sub_hold, 0.5, 60, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._sub_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._sub_btn = _run_btn(
            inner, "🔔  BURN SUBSCRIBE REMINDER", self._render_subscribe, "#FF0000"
        )
        self._sub_console = _console_block(inner)

    def _render_subscribe(self):
        src = self._sub_src.get().strip()
        out = self._sub_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
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

        def _start(probed):
            _w, _h = probed

            pos_map = {
                "Bottom Centre": ("(w-text_w)/2", "h-text_h-40"),
                "Top Centre": ("(w-text_w)/2", "40"),
                "Bottom Left": ("40", "h-text_h-40"),
                "Bottom Right": ("w-text_w-40", "h-text_h-40"),
            }
            x_expr, y_expr = pos_map.get(pos, pos_map["Bottom Centre"])

            filter_str = fl.DrawText(
                textfile=fl.drawtext_text_file(text),
                font="sans bold",
                fontsize=size,
                fontcolor="white",
                x=x_expr,
                y=y_expr,
                shadowx=3,
                shadowy=3,
                shadowcolor="black@0.8",
                box=True,
                boxcolor="black@0.45",
                boxborderw=8,
                enable=f"between(t,{start},{end})",
            ).filter()

            cmd = [
                _ffmpeg(),
                "-y",
                "-i",
                src,
                "-vf",
                filter_str,
                "-c:v",
                "libx264",
                "-crf",
                crf,
                "-preset",
                "fast",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                out,
            ]
            self.run_ffmpeg(
                cmd,
                self._sub_console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self._sub_btn,
            )

        tasks.submit(lambda: _probe_wh(src), on_done=_start, name="render_subscribe")

    def _build_ov_countdown(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  Countdown  ")
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.countdown_timer_overlay"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Burns a live countdown clock onto the video starting at "
            "your chosen value. Great for reaction videos, cooking timers, "
            "challenges, and live event coverage.",
        )

        self._cd_src = tk.StringVar()
        self._cd_out = tk.StringVar()
        _src_row(
            inner, self._cd_src, lambda: self._browse_src(self._cd_src, self._cd_out, "_countdown")
        )
        _out_row(inner, self._cd_out, lambda: self._browse_out(self._cd_out))

        opts = _lf(inner, "Countdown Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._cd_from = tk.StringVar(value="10")
        self._cd_start = tk.StringVar(value="2.0")
        self._cd_size = tk.StringVar(value="80")
        self._cd_crf = tk.StringVar(value="18")

        _spin(opts, "Count from (s):", self._cd_from, 3, 3600, 1).pack(anchor="w", pady=px(3))
        _spin(opts, "Start at (s):", self._cd_start, 0, 3600, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "Font size:", self._cd_size, 24, 200, 8).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._cd_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._cd_btn = _run_btn(inner, "⏱  BURN COUNTDOWN", self._render_countdown, "#E53935")
        self._cd_console = _console_block(inner)

    def _render_countdown(self):
        src = self._cd_src.get().strip()
        out = self._cd_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
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
        filter_str = (
            "drawtext="
            f"text='%{{eif\\:{count_from}-(t-{start})\\:d}}':"
            f"fontsize={size}:"
            "fontcolor=white:"
            "x=(w-text_w)/2:y=(h-text_h)/2:"
            "shadowcolor=black@0.8:shadowx=4:shadowy=4:"
            "box=1:boxcolor=black@0.5:boxborderw=12:"
            f"enable='between(t,{start},{end})'"
        )

        cmd = [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-vf",
            filter_str,
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self._cd_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._cd_btn,
        )

    def _build_ov_progressbar(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.progress_bar"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.video_progress_bar"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Adds a YouTube-style thin progress bar at the top or bottom that "
            "fills from left to right as the video plays. "
            "Viewers love knowing how far through the video they are.",
        )

        self._pb_src = tk.StringVar()
        self._pb_out = tk.StringVar()
        _src_row(
            inner, self._pb_src, lambda: self._browse_src(self._pb_src, self._pb_out, "_progress")
        )
        _out_row(inner, self._pb_out, lambda: self._browse_out(self._pb_out))

        opts = _lf(inner, "Bar Options")
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

        _spin(opts, "Bar height (px):", self._pb_height, 2, 40, 2).pack(anchor="w", pady=px(3))
        _combo(opts, "Position:", self._pb_pos, ["Bottom", "Top"], width=10).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._pb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._pb_btn = _run_btn(inner, "▶  BURN PROGRESS BAR", self._render_progressbar, "#E53935")
        self._pb_console = _console_block(inner)

    def _render_progressbar(self):
        label = "▶  BURN PROGRESS BAR"
        begun = self._fx_begin(self._pb_src, self._pb_out, "progress")
        if not begun:
            return
        src, out, info = begun
        try:
            bar_h = int(self._fx_num(self._pb_height, "Height", 1, 200))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = self._fx_geom(info)
        colour = self._pb_color.get().strip().lstrip("#") or "FF0000"
        y = 0 if self._pb_pos.get() == "Top" else h - bar_h
        graph = (
            f"color=c=0x{colour}:s={w}x{bar_h}:r={fps}[bar];"
            f"[0:v][bar]overlay=x='-w+w*t/{max(dur, 0.1):.3f}':y={y}:shortest=1,{self.FX_GUARD}[v]"
        )
        self._fx_run(src, out, info, graph, self._pb_crf, self._pb_btn, label, self._pb_console)

    def _build_glitch_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.glitch_vibe"))
        gl_nb = ttk.Notebook(outer)
        gl_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_gl_rgb(gl_nb)
        self._build_gl_vhs(gl_nb)
        self._build_gl_grain(gl_nb)
        self._build_gl_vignette(gl_nb)
        self._build_gl_lightleak(gl_nb)

    def _build_gl_rgb(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  RGB Glitch  ")
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.rgb_chromatic_aberration_glitch"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Splits the Red, Green, and Blue channels and offsets them "
            "horizontally to create the classic chromatic aberration / "
            "cyberpunk glitch look seen everywhere on YouTube. "
            "Apply to a section or the entire video.",
        )

        self._rgb_src = tk.StringVar()
        self._rgb_out = tk.StringVar()
        _src_row(
            inner, self._rgb_src, lambda: self._browse_src(self._rgb_src, self._rgb_out, "_rgb")
        )
        _out_row(inner, self._rgb_out, lambda: self._browse_out(self._rgb_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._rgb_offset = tk.StringVar(value="6")
        self._rgb_start = tk.StringVar(value="0")
        self._rgb_dur = tk.StringVar(value="whole video")
        self._rgb_crf = tk.StringVar(value="18")

        _spin(opts, "Channel offset (px):", self._rgb_offset, 1, 40, 1).pack(anchor="w", pady=px(3))

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
        tk.Label(r, text="  Duration (s, or 'whole video'):", bg=CLR["bg"], fg=CLR["fg"]).pack(
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

        self._rgb_btn = _run_btn(inner, "🌈  APPLY RGB GLITCH", self._render_rgb, "#9C27B0")
        self._rgb_console = _console_block(inner)

    def _render_rgb(self):
        src = self._rgb_src.get().strip()
        out = self._rgb_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
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
        filter_str = (
            "[0:v]split=3[r0][g0][b0];"
            "[r0]geq=r='r(X,Y)':g=0:b=0[r];"
            "[g0]geq=r=0:g='g(X,Y)':b=0[g];"
            "[b0]geq=r=0:g=0:b='b(X,Y)'[b];"
            f"[r]pad=iw+{offset}:ih:0:0:color=black[rp];"
            f"[b]pad=iw+{offset}:ih:{offset}:0:color=black[bp];"
            f"[g]pad=iw+{offset}:ih:{offset // 2}:0:color=black[gp];"
            "[rp][gp]blend=all_mode=addition[rg];"
            "[rg][bp]blend=all_mode=addition,"
            f"crop=iw-{offset}:ih:0:0"
            "[glitch];"
            f"[0:v][glitch]overlay=0:0:enable='{enable}'[v]"
        )

        cmd = [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-filter_complex",
            filter_str,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self._rgb_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._rgb_btn,
        )

    def _build_gl_vhs(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.vhs_retro"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.vhs_retro_effect"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Simulates a degraded VHS tape: colour bleed, noise, "
            "scanlines, and edge distortion. Popular for nostalgia, "
            "horror, lo-fi, and vaporwave aesthetics.",
        )

        self._vhs_src = tk.StringVar()
        self._vhs_out = tk.StringVar()
        _src_row(
            inner, self._vhs_src, lambda: self._browse_src(self._vhs_src, self._vhs_out, "_vhs")
        )
        _out_row(inner, self._vhs_out, lambda: self._browse_out(self._vhs_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._vhs_strength = tk.StringVar(value="Medium")
        self._vhs_crf = tk.StringVar(value="18")

        _combo(
            opts, "Intensity:", self._vhs_strength, ["Subtle", "Medium", "Heavy"], width=10
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._vhs_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._vhs_btn = _run_btn(inner, "📼  APPLY VHS EFFECT", self._render_vhs, "#546E7A")
        self._vhs_console = _console_block(inner)

    def _render_vhs(self):
        src = self._vhs_src.get().strip()
        out = self._vhs_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
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

        filter_str = (
            # Chroma blur simulates colour bleed
            "split=2[main][cb];"
            f"[cb]hue=s=1.3,boxblur=luma_radius={chroma_blur}:chroma_radius={chroma_blur}[cblur];"
            "[main][cblur]blend=all_mode=overlay:all_opacity=0.3[blended];"
            # Luma softness
            f"[blended]unsharp=3:3:-{luma_blur}[soft];"
            # Noise (grain)
            f"[soft]noise=alls={noise}:allf=t[noisy];"
            # Scanlines
            "[noisy]drawgrid="
            "width=0:height=2:thickness=1:"
            f"color=black@{scan_alpha}[v]"
        )

        cmd = [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-filter_complex",
            filter_str,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self._vhs_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._vhs_btn,
        )

    def _build_gl_grain(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.film_grain"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.film_grain_cinematic_noise"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Adds organic, temporal film grain. Used by cinematographers to "
            "add texture, hide compression artefacts, and give footage a "
            "cinematic / analogue feel.",
        )

        self._gr_src = tk.StringVar()
        self._gr_out = tk.StringVar()
        _src_row(
            inner, self._gr_src, lambda: self._browse_src(self._gr_src, self._gr_out, "_grain")
        )
        _out_row(inner, self._gr_out, lambda: self._browse_out(self._gr_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._gr_amount = tk.StringVar(value="20")
        self._gr_crf = tk.StringVar(value="18")
        _spin(opts, "Grain amount (0-100):", self._gr_amount, 1, 100, 5).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._gr_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._gr_btn = _run_btn(inner, "🎞  APPLY FILM GRAIN", self._render_grain, "#5D4037")
        self._gr_console = _console_block(inner)

    def _render_grain(self):
        src = self._gr_src.get().strip()
        out = self._gr_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
            return

        try:
            amount = int(self._gr_amount.get())
        except ValueError:
            amount = 20

        crf = self._gr_crf.get()
        cmd = [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-vf",
            f"noise=alls={amount}:allf=t+u",
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self._gr_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._gr_btn,
        )

    def _build_gl_vignette(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  Vignette  ")
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.cinematic_vignette"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Darkens the edges of the frame, drawing the eye to the centre. "
            "One of the most universally used effects in cinema and YouTube.",
        )

        self._vi_src = tk.StringVar()
        self._vi_out = tk.StringVar()
        _src_row(
            inner, self._vi_src, lambda: self._browse_src(self._vi_src, self._vi_out, "_vignette")
        )
        _out_row(inner, self._vi_out, lambda: self._browse_out(self._vi_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._vi_angle = tk.StringVar(value="0.628")
        self._vi_crf = tk.StringVar(value="18")
        _spin(opts, "Strength (angle 0.1–1.5):", self._vi_angle, 0.1, 1.5, 0.05, width=6).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._vi_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._vi_btn = _run_btn(inner, "🔲  APPLY VIGNETTE", self._render_vignette, "#37474F")
        self._vi_console = _console_block(inner)

    def _render_vignette(self):
        src = self._vi_src.get().strip()
        out = self._vi_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
            return

        angle = self._vi_angle.get()
        crf = self._vi_crf.get()
        cmd = [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-vf",
            f"vignette=angle={angle}",
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self._vi_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._vi_btn,
        )

    def _build_gl_lightleak(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.light_leak"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.light_leak_lens_flare"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Simulates a burst of warm light sweeping across the frame, "
            "as if a lens flare or light leak was caught on a film camera. "
            "Great for transitions and dream-like sequences.",
        )

        self._ll_src = tk.StringVar()
        self._ll_out = tk.StringVar()
        _src_row(
            inner, self._ll_src, lambda: self._browse_src(self._ll_src, self._ll_out, "_lightleak")
        )
        _out_row(inner, self._ll_out, lambda: self._browse_out(self._ll_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._ll_start = tk.StringVar(value="2.0")
        self._ll_dur = tk.StringVar(value="1.0")
        self._ll_color = tk.StringVar(value="Warm Orange")
        self._ll_crf = tk.StringVar(value="18")

        _spin(opts, "Appear at (s):", self._ll_start, 0, 3600, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "Duration (s):", self._ll_dur, 0.2, 5, 0.2).pack(anchor="w", pady=px(3))
        _combo(
            opts,
            "Light colour:",
            self._ll_color,
            ["Warm Orange", "Cool White", "Golden Yellow", "Soft Pink", "Electric Blue"],
            width=16,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._ll_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._ll_btn = _run_btn(inner, "✨  APPLY LIGHT LEAK", self._render_lightleak, "#FF6F00")
        self._ll_console = _console_block(inner)

    def _render_lightleak(self):
        label = "✨  APPLY LIGHT LEAK"
        begun = self._fx_begin(self._ll_src, self._ll_out, "lightleak")
        if not begun:
            return
        src, out, info = begun
        try:
            start = self._fx_num(self._ll_start, "Start", 0)
            d = self._fx_num(self._ll_dur, "Duration", 0.1, 30)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = self._fx_geom(info)
        colour = {
            "Warm Orange": "FFA040",
            "Cool White": "E8F0FF",
            "Golden Yellow": "FFD24A",
            "Soft Pink": "FF8FB8",
            "Electric Blue": "40A0FF",
        }.get(self._ll_color.get(), "FFA040")
        half = d / 2
        graph = (
            f"gradients=s={w}x{h}:r={fps}:c0=0x{colour}:c1=0x000000:x0=0:y0=0:x1={w}:y1={h // 3}:"
            f"nb_colors=2:speed=0:d={dur:.3f},format=gbrp,"
            f"fade=t=in:st={start:.3f}:d={half:.3f}:color=black,"
            f"fade=t=out:st={start + half:.3f}:d={half:.3f}:color=black[leak];"
            f"[0:v]setpts=PTS-STARTPTS,scale={w}:{h},format=gbrp[base];"
            f"[base][leak]blend=all_mode=screen:shortest=1,{self.FX_GUARD}[v]"
        )
        self._fx_run(src, out, info, graph, self._ll_crf, self._ll_btn, label, self._ll_console)

    def _build_motion_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.motion"))
        mo_nb = ttk.Notebook(outer)
        mo_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_mo_shake(mo_nb)
        self._build_mo_zoompunch(mo_nb)
        self._build_mo_kenburns(mo_nb)
        self._build_mo_whippan(mo_nb)

    def _build_mo_shake(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.camera_shake"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.camera_shake_2"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Simulates an unstabilised handheld camera. Apply to impact moments, "
            "action sequences, or jump scares. Amplitude controls jitter size. "
            "Frame edges are cropped slightly to hide the shake border.",
        )

        self._sh_src = tk.StringVar()
        self._sh_out = tk.StringVar()
        _src_row(
            inner, self._sh_src, lambda: self._browse_src(self._sh_src, self._sh_out, "_shake")
        )
        _out_row(inner, self._sh_out, lambda: self._browse_out(self._sh_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._sh_amp = tk.StringVar(value="10")
        self._sh_speed = tk.StringVar(value="Normal")
        self._sh_start = tk.StringVar(value="0")
        self._sh_dur = tk.StringVar(value="whole video")
        self._sh_crf = tk.StringVar(value="18")

        _spin(opts, "Amplitude (px):", self._sh_amp, 2, 60, 2).pack(anchor="w", pady=px(3))
        _combo(
            opts, "Shake speed:", self._sh_speed, ["Slow", "Normal", "Fast", "Violent"], width=10
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
        tk.Label(r, text="  Duration (s / 'whole video'):", bg=CLR["bg"], fg=CLR["fg"]).pack(
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

        self._sh_btn = _run_btn(inner, "📳  APPLY CAMERA SHAKE", self._render_shake, "#B71C1C")
        self._sh_console = _console_block(inner)

    def _render_shake(self):
        src = self._sh_src.get().strip()
        out = self._sh_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
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
                t("common.warning"), f"Duration must be seconds or 'Whole video': {dur_str!r}"
            )
            return

        def _start(probed):
            total, (w, h) = probed
            end = total if span is None else start + span
            enable = f"between(t,{start},{end})"

            # Use crop to hide black edges introduced by the translate
            cw = w - amp * 2
            ch = h - amp * 2

            filter_str = (
                f"crop={cw}:{ch}:"
                f"x='if({enable},{amp}+{amp}*sin(t*{freq1}),{amp})':"
                f"y='if({enable},{amp}+{amp}*cos(t*{freq2}),{amp})',"
                f"scale={w}:{h},setsar=1,format=yuv420p"
            )

            cmd = [
                _ffmpeg(),
                "-y",
                "-i",
                src,
                "-vf",
                filter_str,
                "-c:v",
                "libx264",
                "-crf",
                crf,
                "-preset",
                "fast",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                out,
            ]
            self.run_ffmpeg(
                cmd,
                self._sh_console,
                on_done=lambda rc: self.show_result(rc, out),
                btn=self._sh_btn,
            )

        tasks.submit(
            lambda: (_probe_duration(src), _probe_wh(src)), on_done=_start, name="render_shake"
        )

    def _build_mo_zoompunch(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.zoom_punch"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.zoom_punch_impact_zoom"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "A sudden very fast zoom-in at a specific timestamp, synced to an "
            "impact, beat drop, or punchline. A favourite for meme edits, "
            "gaming highlights, and react content.",
        )

        self._zp_src = tk.StringVar()
        self._zp_out = tk.StringVar()
        _src_row(
            inner, self._zp_src, lambda: self._browse_src(self._zp_src, self._zp_out, "_zoompunch")
        )
        _out_row(inner, self._zp_out, lambda: self._browse_out(self._zp_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._zp_at = tk.StringVar(value="5.0")
        self._zp_zoom = tk.StringVar(value="1.4")
        self._zp_attack = tk.StringVar(value="0.1")
        self._zp_hold = tk.StringVar(value="0.3")
        self._zp_release = tk.StringVar(value="0.5")
        self._zp_crf = tk.StringVar(value="18")

        _spin(opts, "Punch at (s):", self._zp_at, 0, 3600, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "Zoom factor (×):", self._zp_zoom, 1.1, 4, 0.05).pack(anchor="w", pady=px(3))
        _spin(opts, "Attack (s):", self._zp_attack, 0.03, 0.5, 0.02).pack(anchor="w", pady=px(3))
        _spin(opts, "Hold at peak (s):", self._zp_hold, 0.05, 2, 0.05).pack(anchor="w", pady=px(3))
        _spin(opts, "Release / ease (s):", self._zp_release, 0.1, 3, 0.1).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._zp_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._zp_btn = _run_btn(inner, "👊  APPLY ZOOM PUNCH", self._render_zoompunch, "#D32F2F")
        self._zp_console = _console_block(inner)

    def _render_zoompunch(self):
        label = "\U0001f44a  APPLY ZOOM PUNCH"
        begun = self._fx_begin(self._zp_src, self._zp_out, "zoompunch")
        if not begun:
            return
        src, out, info = begun
        try:
            at = self._fx_num(self._zp_at, "At", 0)
            z = self._fx_num(self._zp_zoom, "Zoom", 1.0, 5)
            a = self._fx_num(self._zp_attack, "Attack", 0.01, 10)
            hold = self._fx_num(self._zp_hold, "Hold", 0, 10)
            rel = self._fx_num(self._zp_release, "Release", 0.01, 10)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, _dur = self._fx_geom(info)
        t1, t2, t3 = at + a, at + a + hold, at + a + hold + rel
        zexpr = (
            f"if(lt(it,{at:.3f}),1,if(lt(it,{t1:.3f}),1+({z}-1)*(it-{at:.3f})/{a:.3f},"
            f"if(lt(it,{t2:.3f}),{z},if(lt(it,{t3:.3f}),{z}-({z}-1)*(it-{t2:.3f})/{rel:.3f},1))))"
        )
        graph = (
            f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
            f"s={w}x{h}:fps={fps},{self.FX_GUARD}"
        )
        self._fx_run(
            src,
            out,
            info,
            graph,
            self._zp_crf,
            self._zp_btn,
            label,
            self._zp_console,
            complex_graph=False,
        )

    def _build_mo_kenburns(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  Ken Burns  ")
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.ken_burns_pan_zoom"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "A slow, cinematic pan and zoom across the entire video. "
            "Named after the documentary filmmaker. Ideal for drone footage, "
            "landscape shots, interview B-roll, and still photo slideshows.",
        )

        self._kb_src = tk.StringVar()
        self._kb_out = tk.StringVar()
        _src_row(
            inner, self._kb_src, lambda: self._browse_src(self._kb_src, self._kb_out, "_kenburns")
        )
        _out_row(inner, self._kb_out, lambda: self._browse_out(self._kb_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._kb_zoom_start = tk.StringVar(value="1.0")
        self._kb_zoom_end = tk.StringVar(value="1.15")
        self._kb_dir = tk.StringVar(value="Left → Right")
        self._kb_crf = tk.StringVar(value="18")

        _spin(opts, "Zoom start (×):", self._kb_zoom_start, 1.0, 2.0, 0.05).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "Zoom end (×):", self._kb_zoom_end, 1.0, 2.0, 0.05).pack(anchor="w", pady=px(3))
        _combo(
            opts,
            "Pan direction:",
            self._kb_dir,
            ["Left → Right", "Right → Left", "Top → Bottom", "Bottom → Top", "Centre zoom only"],
            width=18,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._kb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._kb_btn = _run_btn(inner, "🎥  APPLY KEN BURNS", self._render_kenburns, "#1B5E20")
        self._kb_console = _console_block(inner)

    def _render_kenburns(self):
        label = "\U0001f3a5  APPLY KEN BURNS"
        begun = self._fx_begin(self._kb_src, self._kb_out, "kenburns")
        if not begun:
            return
        src, out, info = begun
        try:
            z0 = self._fx_num(self._kb_zoom_start, "Start zoom", 1.0, 5)
            z1 = self._fx_num(self._kb_zoom_end, "End zoom", 1.0, 5)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, fps, dur = self._fx_geom(info)
        p = f"it/{max(dur, 0.1):.3f}"
        d = self._kb_dir.get()
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        if d.startswith("Left"):
            x = f"(iw-iw/zoom)*{p}"
        elif d.startswith("Right"):
            x = f"(iw-iw/zoom)*(1-{p})"
        elif d.startswith("Top"):
            y = f"(ih-ih/zoom)*{p}"
        elif d.startswith("Bottom"):
            y = f"(ih-ih/zoom)*(1-{p})"
        graph = (
            f"zoompan=z='{z0}+({z1}-{z0})*{p}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps},"
            f"{self.FX_GUARD}"
        )
        self._fx_run(
            src,
            out,
            info,
            graph,
            self._kb_crf,
            self._kb_btn,
            label,
            self._kb_console,
            complex_graph=False,
        )

    def _build_mo_whippan(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.whip_pan_blur"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.whip_pan_motion_blur"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Adds an extreme horizontal or vertical motion blur over a short "
            "window, simulating a fast camera whip pan. "
            "Perfect for transitions between scenes, used on YouTube by "
            "MrBeast, MKBHD, Linus Tech Tips, and almost every vlogger.",
        )

        self._wp_src = tk.StringVar()
        self._wp_out = tk.StringVar()
        _src_row(
            inner, self._wp_src, lambda: self._browse_src(self._wp_src, self._wp_out, "_whippan")
        )
        _out_row(inner, self._wp_out, lambda: self._browse_out(self._wp_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._wp_at = tk.StringVar(value="5.0")
        self._wp_dur = tk.StringVar(value="0.3")
        self._wp_dir = tk.StringVar(value="Horizontal")
        self._wp_str = tk.StringVar(value="40")
        self._wp_crf = tk.StringVar(value="18")

        _spin(opts, "Apply at (s):", self._wp_at, 0, 3600, 0.5).pack(anchor="w", pady=px(3))
        _spin(opts, "Duration (s):", self._wp_dur, 0.1, 2, 0.05).pack(anchor="w", pady=px(3))
        _combo(opts, "Direction:", self._wp_dir, ["Horizontal", "Vertical"], width=12).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "Blur strength:", self._wp_str, 5, 120, 5).pack(anchor="w", pady=px(3))
        _spin(opts, "CRF:", self._wp_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._wp_btn = _run_btn(inner, "💨  APPLY WHIP PAN BLUR", self._render_whippan, "#0D47A1")
        self._wp_console = _console_block(inner)

    def _render_whippan(self):
        label = "\U0001f4a8  APPLY WHIP PAN BLUR"
        begun = self._fx_begin(self._wp_src, self._wp_out, "whippan")
        if not begun:
            return
        src, out, info = begun
        try:
            at = self._fx_num(self._wp_at, "At", 0)
            d = self._fx_num(self._wp_dur, "Duration", 0.01, 10)
            strength = int(self._fx_num(self._wp_str, "Strength", 1, 500))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        w, h, _fps, _dur = self._fx_geom(info)
        horizontal = self._wp_dir.get() != "Vertical"
        size = max(1, min(strength, (w if horizontal else h) // 2 - 1))
        sx, sy = (size, 1) if horizontal else (1, size)
        graph = (
            f"avgblur=sizeX={sx}:sizeY={sy}:enable='between(t,{at:.3f},{at + d:.3f})',"
            f"{self.FX_GUARD}"
        )
        self._fx_run(
            src,
            out,
            info,
            graph,
            self._wp_crf,
            self._wp_btn,
            label,
            self._wp_console,
            complex_graph=False,
        )

    def _build_branding_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("effects.branding"))
        br_nb = ttk.Notebook(outer)
        br_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self._build_br_meme(br_nb)
        self._build_br_cinebars(br_nb)
        self._build_br_watermark(br_nb)

    def _build_br_meme(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.meme_caption"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.impact_meme_caption"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Burns large white Impact-style text with black outline onto the "
            "video: top caption, bottom caption, or both. The classic meme "
            "format used on every reaction and highlight channel.",
        )

        self._mc_src = tk.StringVar()
        self._mc_out = tk.StringVar()
        _src_row(inner, self._mc_src, lambda: self._browse_src(self._mc_src, self._mc_out, "_meme"))
        _out_row(inner, self._mc_out, lambda: self._browse_out(self._mc_out))

        opts = _lf(inner, "Caption Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))

        self._mc_top = tk.StringVar(value="WHEN YOU REALISE")
        self._mc_bot = tk.StringVar(value="IT WAS THAT EASY")
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

        _spin(opts, "Font size:", self._mc_size, 24, 180, 4).pack(anchor="w", pady=px(3))
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
        tk.Label(r2, text="  Duration (s / 'whole video'):", bg=CLR["bg"], fg=CLR["fg"]).pack(
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

        self._mc_btn = _run_btn(inner, "🗯  BURN MEME CAPTIONS", self._render_meme, "#F57F17")
        self._mc_console = _console_block(inner)

    def _render_meme(self):
        src = self._mc_src.get().strip()
        out = self._mc_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
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

        filters = []
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
            messagebox.showwarning(t("common.warning"), "Enter at least one caption.")
            return

        cmd = [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-vf",
            ",".join(filters),
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self._mc_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._mc_btn,
        )

    def _build_br_cinebars(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.cinematic_bars"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.cinematic_black_bars"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Adds letterbox bars to simulate a 2.39:1 or 2.35:1 anamorphic "
            "widescreen cinema aspect ratio. Optionally animate them sliding "
            "in from the edges for a dramatic reveal.",
        )

        self._cb_src = tk.StringVar()
        self._cb_out = tk.StringVar()
        _src_row(
            inner, self._cb_src, lambda: self._browse_src(self._cb_src, self._cb_out, "_cinebars")
        )
        _out_row(inner, self._cb_out, lambda: self._browse_out(self._cb_out))

        opts = _lf(inner, "Options")
        opts.pack(fill="x", padx=px(18), pady=px(8))
        self._cb_ratio = tk.StringVar(value="2.39:1")
        self._cb_anim = tk.BooleanVar(value=True)
        self._cb_adur = tk.StringVar(value="1.0")
        self._cb_crf = tk.StringVar(value="18")

        _combo(
            opts,
            "Aspect ratio:",
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
        _spin(opts, "Animation duration (s):", self._cb_adur, 0.2, 3, 0.2).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._cb_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._cb_btn = _run_btn(inner, "🎬  APPLY CINEMATIC BARS", self._render_cinebars, "#1A237E")
        self._cb_console = _console_block(inner)

    def _render_cinebars(self):
        label = "\U0001f3ac  APPLY CINEMATIC BARS"
        begun = self._fx_begin(self._cb_src, self._cb_out, "cinebars")
        if not begun:
            return
        src, out, info = begun
        w, h, fps, _dur = self._fx_geom(info)
        try:
            ratio = float(self._cb_ratio.get().split(":")[0])
            ad = self._fx_num(self._cb_adur, "Animation", 0.05, 30)
        except (ValueError, IndexError) as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        bar = max(0, int((h - w / ratio) / 2))
        if bar < 2:
            messagebox.showinfo(
                t("common.warning"),
                "This video is already as wide as the chosen ratio - no bars needed.",
                parent=self,
            )
            return
        if self._cb_anim.get():
            ytop = f"if(lt(t,{ad:.3f}),-h+h*t/{ad:.3f},0)"
            ybot = f"if(lt(t,{ad:.3f}),H-h*t/{ad:.3f},H-h)"
        else:
            ytop, ybot = "0", "H-h"
        graph = (
            f"color=c=black:s={w}x{bar}:r={fps}[tb];color=c=black:s={w}x{bar}:r={fps}[bb];"
            f"[0:v]scale={w}:{h}[base];"
            f"[base][tb]overlay=x=0:y='{ytop}':shortest=1[o1];"
            f"[o1][bb]overlay=x=0:y='{ybot}':shortest=1,{self.FX_GUARD}[v]"
        )
        self._fx_run(src, out, info, graph, self._cb_crf, self._cb_btn, label, self._cb_console)

    def _build_br_watermark(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("effects.social_watermark"))
        _, inner = _make_scrollable(f)

        tk.Label(
            inner,
            text=t("effects.social_media_watermark"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["bg"],
            fg=CLR["accent"],
        ).pack(anchor="w", padx=px(18), pady=px(14, 2))
        _hint(
            inner,
            "Burns a persistent channel handle / watermark onto the entire "
            "video. Supports @ prefixes, emojis, and custom positioning. "
            "Essential for short-form content repurposed across platforms.",
        )

        self._wm_src = tk.StringVar()
        self._wm_out = tk.StringVar()
        _src_row(
            inner, self._wm_src, lambda: self._browse_src(self._wm_src, self._wm_out, "_watermark")
        )
        _out_row(inner, self._wm_out, lambda: self._browse_out(self._wm_out))

        opts = _lf(inner, "Watermark Options")
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

        _spin(opts, "Font size:", self._wm_size, 12, 100, 2).pack(anchor="w", pady=px(3))
        _combo(
            opts,
            "Position:",
            self._wm_pos,
            ["Bottom Right", "Bottom Left", "Top Right", "Top Left", "Centre"],
            width=14,
        ).pack(anchor="w", pady=px(3))
        _spin(opts, "Opacity (0.1–1.0):", self._wm_alpha, 0.1, 1.0, 0.05, width=6).pack(
            anchor="w", pady=px(3)
        )
        _spin(opts, "CRF:", self._wm_crf, 12, 30, 1).pack(anchor="w", pady=px(3))

        self._wm_btn = _run_btn(inner, "©  BURN WATERMARK", self._render_watermark, "#455A64")
        self._wm_console = _console_block(inner)

    def _render_watermark(self):
        src = self._wm_src.get().strip()
        out = self._wm_out.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if not out:
            messagebox.showwarning(t("common.warning"), "Please set an output path.")
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

        cmd = [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-vf",
            filter_str,
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self._wm_console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._wm_btn,
        )

    # ═════════════════════════════════════════════════════════════════════
    #  Shared helpers used by all render methods
    # ═════════════════════════════════════════════════════════════════════

    def _browse_src(self, src_var, out_var, suffix):
        p = dialogs.askopenfilename(filetypes=VIDEO_TYPES)
        if not p:
            return
        src_var.set(p)
        base = os.path.splitext(p)[0]
        if not out_var.get():
            out_var.set(base + suffix + ".mp4")

    @staticmethod
    def _browse_out(out_var):
        p = dialogs.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[
                (t("effects.mp4_video"), "*.mp4"),
                (t("youtube.all_files"), t("ducker.item_2")),
            ],
        )
        if p:
            out_var.set(p)
