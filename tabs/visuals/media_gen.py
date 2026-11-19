"""
tabs/visuals/media_gen.py  -  Media Generator

Synthetic media for testing, calibration and B-roll: SMPTE/EBU colour bars,
moving test patterns, a classic countdown leader with "2-pop", solid
colours, TV static, gradients and generative patterns - with line-up tones,
noise or silence, at any common resolution and frame rate.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, scroll_body
from tabs import common

PATTERNS = {
    "smpte_hd": "SMPTE HD colour bars",
    "smpte": "SMPTE colour bars",
    "ebu": "EBU colour bars (75%)",
    "testsrc": "Moving test pattern",
    "countdown": "Countdown leader (with 2-pop)",
    "color": "Solid colour",
    "black": "Black",
    "static": "TV static",
    "gradient": "Animated gradient",
    "mandelbrot": "Mandelbrot zoom",
    "life": "Game of Life",
    "none": "No video (audio file only)",
}
AUDIO = {
    "tone18": "1 kHz tone at −18 dBFS (EBU line-up)",
    "tone20": "1 kHz tone at −20 dBFS (SMPTE line-up)",
    "tone": "Sine tone at a chosen frequency",
    "pop": "2-pop beep (for countdown leaders)",
    "pink": "Pink noise",
    "white": "White noise",
    "silence": "Silence",
    "none": "No audio",
}
SIZES = {
    "3840×2160 (4K UHD)": (3840, 2160), "2560×1440 (QHD)": (2560, 1440),
    "1920×1080 (Full HD)": (1920, 1080), "1280×720 (HD)": (1280, 720),
    "1080×1920 (vertical 9:16)": (1080, 1920), "1080×1080 (square)": (1080, 1080),
    "720×576 (PAL SD)": (720, 576), "720×480 (NTSC SD)": (720, 480),
}
RATES = {"23.976": "24000/1001", "24": "24", "25": "25", "29.97": "30000/1001", "30": "30",
         "50": "50", "59.94": "60000/1001", "60": "60"}
AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus")


def _fps_float(rate: str) -> float:
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


def video_source(pattern: str, w: int, h: int, rate: str, dur: float, colour: str = "202020",
                 textfile: str | None = None) -> str:
    """A lavfi graph producing the pattern (single output)."""
    common_opts = f"size={w}x{h}:rate={rate}:duration={dur:.3f}"
    if pattern == "smpte_hd":
        return f"smptehdbars={common_opts}"
    if pattern == "smpte":
        return f"smptebars={common_opts}"
    if pattern == "ebu":
        return f"pal75bars={common_opts}"
    if pattern == "testsrc":
        return f"testsrc2={common_opts}"
    if pattern == "color":
        return f"color=c=0x{colour}:{common_opts}"
    if pattern == "black":
        return f"color=c=black:{common_opts}"
    if pattern == "static":
        return (f"color=c=0x808080:{common_opts},format=yuv420p,"
                f"noise=c0s=100:c0f=t+u")
    if pattern == "gradient":
        return (f"gradients={common_opts}:speed=0.015:nb_colors=4:type=radial:"
                f"c0=0x1E3A8A:c1=0x7C3AED:c2=0xDB2777:c3=0xF59E0B")
    if pattern == "mandelbrot":
        return f"mandelbrot=size={w}x{h}:rate={rate},trim=duration={dur:.3f}"
    if pattern == "life":
        return (f"life=size={max(16, w // 4)}x{max(16, h // 4)}:rate={rate}:mold=10:ratio=0.1:"
                f"life_color=0x00FF88:death_color=0x802040:mold_color=0x202040,"
                f"scale={w}:{h}:flags=neighbor,trim=duration={dur:.3f}")
    if pattern == "countdown":
        return countdown_source(w, h, rate, dur, textfile)
    raise ValueError(f"unknown pattern {pattern}")


def countdown_length(dur: float) -> int:
    return max(3, int(round(dur)))


def countdown_source(w: int, h: int, rate: str, dur: float, textfile: str | None) -> str:
    """Academy-style leader: N…3 with a clock sweep, one frame of "2", then black."""
    n = countdown_length(dur)
    frame = 1.0 / _fps_float(rate)
    cut = n - 2 + frame
    hw, hh = max(2, w // 4 * 2), max(2, h // 4 * 2)     # sweep drawn at half size
    sweep = ("geq=lum='st(0,hypot(X-W/2,Y-H/2));st(1,atan2(X-W/2,H/2-Y));"
             "st(1,if(lt(ld(1),0),ld(1)+2*PI,ld(1)));"
             "if(lt(abs(ld(0)-H*0.42),2)+lt(abs(ld(0)-H*0.34),1.5)+lt(abs(X-W/2),1)+lt(abs(Y-H/2),1),"
             "225,if(lt(ld(0),H*0.42)*lt(ld(1),mod(T,1)*2*PI),150,70))'")
    number = fl.drawtext_filter(textfile=textfile, font="sans bold", fontsize=int(h * 0.36),
                                fontcolor="white", x="(w-text_w)/2", y="(h-text_h)/2",
                                expansion="normal", enable=f"lt(t,{cut:.4f})")
    return (f"color=c=black:size={hw}x{hh}:rate={rate}:duration={n},format=gray,{sweep},"
            f"scale={w}:{h}:flags=bicubic,format=yuv420p,{number},"
            f"drawbox=c=black:t=fill:enable='gte(t,{cut:.4f})'")


def audio_source(kind: str, dur: float, *, freq: float = 1000.0, pop_at: float = 0.0,
                 frame: float = 0.04) -> str | None:
    """A lavfi graph producing a stereo, 48 kHz signal (exact levels: the mono
    generators are duplicated to both channels, not down-mixed by -3 dB)."""
    stereo = ",pan=stereo|c0=c0|c1=c0"
    if kind == "none":
        return None
    if kind == "tone18":       # FFmpeg's sine is 1/8 of full scale = -18.06 dBFS peak
        return f"sine=frequency=1000:sample_rate=48000:duration={dur:.3f}{stereo}"
    if kind == "tone20":
        return f"sine=frequency=1000:sample_rate=48000:duration={dur:.3f},volume=-1.94dB{stereo}"
    if kind == "tone":
        return f"sine=frequency={freq:g}:sample_rate=48000:duration={dur:.3f}{stereo}"
    if kind == "pop":
        return (f"sine=frequency=1000:sample_rate=48000:duration={frame:.4f},"
                f"adelay=delays={pop_at * 1000:.0f}:all=1,apad=whole_dur={dur:.3f}{stereo}")
    if kind in ("pink", "white"):
        return (f"anoisesrc=color={kind}:amplitude=0.25:sample_rate=48000:duration={dur:.3f}"
                f"{stereo}")
    return f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={dur:.3f}"


class MediaGeneratorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.colour = "202020"
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("mgen.title"),
                         "Colour bars, test patterns, countdown leaders, line-up tones and more.",
                         icon="🎨")
        body = scroll_body(self)
        f = tk.LabelFrame(body, text="  What to generate  ", padx=12, pady=8)
        f.pack(fill="x", padx=20, pady=(10, 4))

        def row(r, label, widget):
            tk.Label(f, text=label, anchor="w", width=16).grid(row=r, column=0, sticky="w", pady=3)
            widget.grid(row=r, column=1, sticky="w", pady=3)

        self.pattern_var = tk.StringVar(value=PATTERNS["smpte_hd"])
        pb = ttk.Combobox(f, textvariable=self.pattern_var, values=list(PATTERNS.values()),
                          state="readonly", width=34)
        pb.bind("<<ComboboxSelected>>", lambda _e: self._pattern_changed())
        row(0, t("mgen.visual_pattern"), pb)
        self.colour_btn = tk.Button(f, text="  Colour…  ", bg="#202020", fg="white", relief="flat",
                                    command=self._pick_colour)
        self.colour_btn.grid(row=0, column=2, sticky="w", padx=8)
        self.size_var = tk.StringVar(value="1920×1080 (Full HD)")
        row(1, "Resolution:", ttk.Combobox(f, textvariable=self.size_var, values=list(SIZES),
                                           state="readonly", width=34))
        self.rate_var = tk.StringVar(value="25")
        row(2, "Frame rate:", ttk.Combobox(f, textvariable=self.rate_var, values=list(RATES),
                                           state="readonly", width=10))
        self.dur_var = tk.StringVar(value="10")
        row(3, t("mgen.duration_seconds"), tk.Entry(f, textvariable=self.dur_var, width=10))
        self.audio_var = tk.StringVar(value=AUDIO["tone18"])
        ab = ttk.Combobox(f, textvariable=self.audio_var, values=list(AUDIO.values()),
                          state="readonly", width=34)
        ab.bind("<<ComboboxSelected>>", lambda _e: self._audio_changed())
        row(4, "Audio:", ab)
        self.freq_var = tk.StringVar(value="440")
        self.freq_row = tk.Frame(f)
        tk.Label(self.freq_row, text="Hz").pack(side="right")
        tk.Entry(self.freq_row, textvariable=self.freq_var, width=8).pack(side="right")
        self.freq_row.grid(row=4, column=2, sticky="w", padx=8)
        self.hint = tk.Label(f, text="", fg=CLR["fgdim"], font=(UI_FONT, 8), justify="left",
                             wraplength=640)
        self.hint.grid(row=5, column=0, columnspan=3, sticky="w")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=12, pady=8)
        out_lf.pack(fill="x", padx=20, pady=6)
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=self._suggest_name)
        self.out_row.pack(fill="x")
        tk.Label(out_lf, text="  ℹ  .mp4/.mkv = H.264, .mov = ProRes, .webm = VP9; "
                              ".wav/.flac/.mp3 = audio only.",
                 fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(anchor="w")

        self.btn_render = tk.Button(body, text=t("mgen.btn_generate"), font=(UI_FONT, 11, "bold"),
                                    bg=CLR["green"], fg="white", width=24, cursor="hand2",
                                    command=self._render)
        self.btn_render.pack(pady=10)
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._pattern_changed()
        self._audio_changed()

    # ── helpers ──────────────────────────────────────────────────────────────

    def pattern(self) -> str:
        return next((k for k, v in PATTERNS.items() if v == self.pattern_var.get()), "smpte_hd")

    def audio(self) -> str:
        return next((k for k, v in AUDIO.items() if v == self.audio_var.get()), "none")

    def _suggest_name(self) -> str:
        from core.paths import user_videos_dir
        ext = ".wav" if self.pattern() == "none" else ".mp4"
        return os.path.join(user_videos_dir(), f"{self.pattern()}{ext}")

    def _pattern_changed(self):
        p = self.pattern()
        if p == "color":
            self.colour_btn.grid()
        else:
            self.colour_btn.grid_remove()
        if p == "countdown":
            self.audio_var.set(AUDIO["pop"])
            self.hint.config(text="Counts down from the duration (at least 3 s) to 2, with one "
                                  "frame of '2' and a 1 kHz pop, then black - the classic "
                                  "sync leader.")
        elif p == "none":
            self.hint.config(text="Only the audio is written - choose a .wav, .flac or .mp3 "
                                  "output file.")
        else:
            self.hint.config(text="")
        self._audio_changed()

    def _audio_changed(self):
        if self.audio() == "tone":
            self.freq_row.grid()
        else:
            self.freq_row.grid_remove()

    def _pick_colour(self):
        res = colorchooser.askcolor(f"#{self.colour}", parent=self)
        if res and res[1]:
            self.colour = res[1].lstrip("#").upper()
            self.colour_btn.config(bg=res[1])

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self):
        pattern, audio = self.pattern(), self.audio()
        try:
            dur = common.number(self.dur_var.get(), "Duration", 0.1, 3600)
            freq = common.number(self.freq_var.get(), "Frequency", 10, 22000) if audio == "tone" else 1000
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        if pattern == "none" and audio == "none":
            messagebox.showwarning(t("common.warning"), "Choose a pattern or an audio signal.",
                                   parent=self)
            return
        out = self.out_row.path or self._suggest_name()
        ext = os.path.splitext(out)[1].lower()
        if pattern == "none" and ext not in AUDIO_EXTS:
            out = os.path.splitext(out)[0] + ".wav"
        out = self.confirm_output(out, [], default_ext=".wav" if pattern == "none" else ".mp4")
        if not out:
            return
        self.out_row.set(out)
        ext = os.path.splitext(out)[1].lower()
        audio_only = pattern == "none" or ext in AUDIO_EXTS
        w, h = SIZES.get(self.size_var.get(), (1920, 1080))
        rate = RATES.get(self.rate_var.get(), "25")
        frame = 1.0 / _fps_float(rate)
        if pattern == "countdown":
            dur = float(countdown_length(dur))

        cmd = [ff.ffmpeg_path(), "-y"]
        maps = []
        textfile = None
        if not audio_only:
            if pattern == "countdown":
                n = countdown_length(dur)
                textfile = fl.drawtext_text_file(f"%{{eif:{n}-trunc(t):d}}")
            cmd += ["-f", "lavfi", "-i", video_source(pattern, w, h, rate, dur, self.colour, textfile)]
            maps += ["-map", "0:v"]
        asrc = audio_source(audio, dur, freq=freq, pop_at=max(0.0, dur - 2.0), frame=frame)
        if asrc:
            cmd += ["-f", "lavfi", "-i", asrc]
            maps += ["-map", f"{0 if audio_only else 1}:a"]
        cmd += maps
        container = ext.lstrip(".") or "mp4"
        if not audio_only:
            codec = {"mov": "prores", "webm": "vp9"}.get(container, "h264")
            venc = enc.video(codec, "high", hw="off", container=container)
            cmd += venc.args + ["-t", f"{dur:.3f}"]
        if asrc:
            if container in ("wav",):
                cmd += ["-c:a", "pcm_s24le"]
            elif container == "mov":
                cmd += ["-c:a", "pcm_s24le"]
            else:
                cmd += enc.audio(enc.default_audio_for(container), container=container,
                                 bitrate="256k")
            cmd += ["-t", f"{dur:.3f}"]
        cmd.append(out)
        self.log(self.console, f"Generating {self.pattern_var.get()} · {w}×{h} @ {self.rate_var.get()} fps"
                               f" · {dur:g}s")

        def done(rc):
            if textfile:
                try:
                    os.remove(textfile)
                except OSError:
                    pass
            self.show_result(rc, out)

        self.run_ffmpeg(cmd, self.console, on_done=done, btn=self.btn_render,
                        btn_label=t("mgen.btn_generate"), duration=dur, output=out,
                        name="Media Generator")
