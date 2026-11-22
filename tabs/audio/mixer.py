"""
tab_audiomixer.py  ─  Multi-track Audio Mixer
Mix up to 8 audio tracks (narration, music, SFX, etc.) over a video,
each with independent volume, pan, fade-in/out, and time offset.

This is the DaVinci Resolve Fairlight use case for creators:
  • Layer a voice-over on top of a video
  • Add background music at reduced volume
  • Add sound effects at specific timestamps
  • Pan audio left/right for spatial effect
  • Each track has a visual fader

The result is the original video re-encoded with the new mixed audio.
"""

import os
import tkinter as tk
from collections.abc import Callable
from enum import StrEnum
from tkinter import messagebox, ttk
from typing import Any

from core.i18n import t
from core.ops import mixer as mx
from core.ops.shared import AUDIO_ONLY_EXTS
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.kit.widgets import ChoiceBox

MAX_TRACKS = 8

TRACK_TYPES = [
    t("mixer.narration_voice"),
    t("mixer.background_music"),
    t("mixer.sound_effect"),
    t("mixer.instrumental"),
    t("mixer.ambient_room_tone"),
    t("mixer.original_video_audio"),
    t("mixer.interview"),
    t("mixer.other"),
]


class MixOutput(StrEnum):
    AAC_256 = "aac256"
    AAC_320 = "aac320"
    MP3 = "mp3"
    WAV = "wav"
    FLAC = "flac"


MIX_OUTPUTS = {
    MixOutput.AAC_256: "AAC 256k  (keep video)",
    MixOutput.AAC_320: "AAC 320k  (keep video)",
    MixOutput.MP3: "MP3 320k  (audio only)",
    MixOutput.WAV: "WAV  (audio only)",
    MixOutput.FLAC: "FLAC  (audio only)",
}
#: the outputs without the video -> their file extension
AUDIO_ONLY = {MixOutput.MP3: ".mp3", MixOutput.WAV: ".wav", MixOutput.FLAC: ".flac"}


class Track:
    def __init__(self, idx):
        self.idx = idx
        self.path = ""
        self.path_var = tk.StringVar()
        self.type_var = tk.StringVar(value=TRACK_TYPES[idx % len(TRACK_TYPES)])
        self.vol_var = tk.DoubleVar(value=1.0)
        self.pan_var = tk.DoubleVar(value=0.0)  # -1 = full left, +1 = full right
        self.offset_var = tk.StringVar(value="0")  # seconds into video
        self.fadein_var = tk.StringVar(value="0")
        self.fadeout_var = tk.StringVar(value="0")
        self.mute_var = tk.BooleanVar(value=False)
        self.solo_var = tk.BooleanVar(value=False)
        self.row: tk.Frame | None = None  # its row in the track list, once drawn
        self.full_path = ""
        self.loop_var = tk.BooleanVar(value=False)
        self.enabled = tk.BooleanVar(value=True)


class AudioMixerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.video_path = ""
        self.tracks = []
        self._build_ui()

    def _build_ui(self):
        self._build_vid()
        self._build_track_grid()
        self._build_master_output()

    def _build_vid(self):
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        hdr_inner = tk.Frame(hdr, bg=CLR["panel"])
        hdr_inner.pack(fill="x", padx=px(20), pady=px(14, 13))
        tk.Label(
            hdr_inner,
            text="🎚  " + t("tab.audio_mixer"),
            font=(UI_FONT, 15, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
        ).pack(side="left")
        tk.Label(
            hdr_inner,
            text=t("mixer.subtitle"),
            font=(UI_FONT, 10),
            bg=CLR["panel"],
            fg=CLR["fgdim"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=px(16, 0))
        tk.Frame(self, bg=CLR["border"], height=px(1)).pack(fill="x")

        # Video source
        vid_f = tk.Frame(self)
        vid_f.pack(fill="x", padx=px(16), pady=px(6))
        tk.Label(vid_f, text=t("common.source_video"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.vid_var = tk.StringVar()
        tk.Entry(vid_f, textvariable=self.vid_var, width=56, relief="flat").pack(
            side="left", padx=px(8)
        )
        tk.Button(
            vid_f, text=t("btn.browse"), command=self._browse_video, cursor="hand2", relief="flat"
        ).pack(side="left")
        self.vid_dur_lbl = tk.Label(vid_f, text="", fg=CLR["fgdim"])
        self.vid_dur_lbl.pack(side="left", padx=px(8))

        # Keep original video audio?
        va_row = tk.Frame(self)
        va_row.pack(anchor="w", padx=px(16), pady=px(2))
        self.keep_vid_audio_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            va_row, text=t("mixer.include_original_checkbox"), variable=self.keep_vid_audio_var
        ).pack(side="left")
        self.vid_audio_vol_var = tk.DoubleVar(value=0.5)
        tk.Label(va_row, text=f"  {t('mixer.volume_label')}").pack(side="left", padx=px(12, 0))
        tk.Scale(
            va_row,
            variable=self.vid_audio_vol_var,
            from_=0,
            to=2,
            resolution=0.05,
            orient="horizontal",
            length=px(120),
        ).pack(side="left")

    def _build_track_grid(self):
        """Track grid."""
        # ── Track grid ────────────────────────────────────────────────────
        tk.Frame(self, bg="#333", height=px(1)).pack(fill="x", padx=px(16), pady=px(4))

        # Column headers
        hdr_f = tk.Frame(self)
        hdr_f.pack(fill="x", padx=px(16))
        for txt, w in [
            (t("mixer.track_col"), 18),
            (t("mixer.source_file_col"), 24),
            (t("mixer.track_type_col"), 20),
            ("Vol", 12),
            (t("mixer.pan_col"), 12),
            (t("mixer.start_col"), 8),
            (t("mixer.fade_up_col"), 6),
            (t("mixer.fade_down_col"), 6),
            (t("mixer.loop_col"), 5),
            ("Mute", 5),
            ("", 4),
        ]:
            tk.Label(
                hdr_f, text=txt, width=w, font=(UI_FONT, 8, "bold"), fg=CLR["fgdim"], anchor="w"
            ).pack(side="left", padx=px(1))

        # Scrollable track rows
        track_canvas = tk.Canvas(self, height=px(300), highlightthickness=0)
        track_sb = ttk.Scrollbar(self, orient="vertical", command=track_canvas.yview)
        self.track_frame = tk.Frame(track_canvas)
        track_canvas.create_window((0, 0), window=self.track_frame, anchor="nw")
        self.track_frame.bind(
            "<Configure>", lambda e: track_canvas.configure(scrollregion=track_canvas.bbox("all"))
        )
        track_canvas.configure(yscrollcommand=track_sb.set)
        track_canvas.pack(side="left", fill="both", expand=True, padx=px(16, 0))
        track_sb.pack(side="right", fill="y")

        # Add track button - must exist before _add_track() is called
        add_row = tk.Frame(self)
        add_row.pack(anchor="w", padx=px(16), pady=px(4))
        tk.Button(
            add_row,
            text=t("mixer.add_track_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=self._add_track,
        ).pack(side="left", padx=px(4))
        tk.Button(
            add_row,
            text=t("mixer.remove_last_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=self._remove_last_track,
        ).pack(side="left", padx=px(4))
        self.track_count_lbl = tk.Label(
            add_row, text=t("mixer.0_tracks"), fg=CLR["fgdim"], font=(UI_FONT, 9)
        )
        self.track_count_lbl.pack(side="left", padx=px(12))

        # Add initial 3 tracks (track_count_lbl now exists)
        for _ in range(3):
            self._add_track()

    def _build_master_output(self):
        """Master output."""
        # ── Master output ─────────────────────────────────────────────────
        tk.Frame(self, bg="#333", height=px(1)).pack(fill="x", padx=px(16), pady=px(4))
        master_f = tk.Frame(self)
        master_f.pack(fill="x", padx=px(16), pady=px(4))
        tk.Label(
            master_f, text=f"🎚  {t('mixer.master_volume_label')}", font=(UI_FONT, 10, "bold")
        ).pack(side="left")
        self.master_vol_var = tk.DoubleVar(value=1.0)
        tk.Scale(
            master_f,
            variable=self.master_vol_var,
            from_=0,
            to=2.0,
            resolution=0.05,
            orient="horizontal",
            length=px(200),
        ).pack(side="left", padx=px(8))
        self.master_lbl = tk.Label(master_f, text="100%", width=5, fg=CLR["accent"])
        self.master_lbl.pack(side="left")
        self.master_vol_var.trace_add(
            "write",
            lambda *_: self.master_lbl.config(text=f"{int(self.master_vol_var.get() * 100)}%"),
        )

        norm_row = tk.Frame(self)
        norm_row.pack(anchor="w", padx=px(16), pady=px(2))
        self.normalize_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            norm_row, text=t("mixer.normalize_checkbox"), variable=self.normalize_var
        ).pack(side="left")
        tk.Label(norm_row, text=f"  {t('mixer.output_format_label')}").pack(
            side="left", padx=px(16, 0)
        )
        self.out_fmt = ChoiceBox(norm_row, MIX_OUTPUTS, MixOutput.AAC_256, width=24)
        self.out_fmt.pack(side="left", padx=px(4))

        # Output file
        of = tk.Frame(self)
        of.pack(fill="x", padx=px(16), pady=px(6))
        tk.Label(of, text=t("common.output_file"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.out_var = tk.StringVar()
        tk.Entry(of, textvariable=self.out_var, width=62, relief="flat").pack(
            side="left", padx=px(8)
        )
        tk.Button(
            of, text=t("common.save_as"), command=self._browse_out, cursor="hand2", relief="flat"
        ).pack(side="left")

        self.btn_render = tk.Button(
            self,
            text=t("mixer.mix_button"),
            font=(UI_FONT, 12, "bold"),
            bg="#1565C0",
            fg="white",
            height=2,
            command=self._render,
        )
        self.btn_render.pack(pady=px(8), padx=px(16), fill="x")

        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=px(16), pady=px(4))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ─────────────────────────────────────────────────────────────────────
    def _add_track(self):
        if len(self.tracks) >= MAX_TRACKS:
            messagebox.showinfo("Max tracks", f"Maximum {MAX_TRACKS} tracks supported.")
            return
        trk = Track(len(self.tracks))
        self.tracks.append(trk)
        self._draw_track_row(trk)
        self.track_count_lbl.config(text=f"{len(self.tracks)} tracks")

    def _draw_track_row(self, trk: Track):
        row = tk.Frame(self.track_frame, relief="groove", bd=px(1))
        row.pack(fill="x", pady=px(2))
        trk.row = row

        # Track number + enable
        head = tk.Frame(row, width=px(140))
        head.pack(side="left")
        tk.Label(
            head, text=f"T{trk.idx + 1}", width=3, font=(UI_FONT, 9, "bold"), fg=CLR["accent"]
        ).pack(side="left")
        tk.Checkbutton(head, variable=trk.enabled).pack(side="left")

        # File browse
        file_entry = tk.Entry(row, textvariable=trk.path_var, width=22, font=(MONO_FONT, 8))
        file_entry.pack(side="left", padx=px(2))

        def _browse(tr=trk):
            p = dialogs.askopenfilename(
                filetypes=[
                    (t("mixer.audio_video"), "*.mp3 *.wav *.aac *.flac *.ogg *.mp4 *.mov"),
                    ("All", t("ducker.item_2")),
                ]
            )
            if p:
                tr.path = p
                tr.path_var.set(p)
                tr.full_path = p

        tk.Button(row, text="…", width=2, command=_browse, cursor="hand2", relief="flat").pack(
            side="left", padx=px(1)
        )

        # Type
        ttk.Combobox(
            row, textvariable=trk.type_var, values=TRACK_TYPES, state="readonly", width=16
        ).pack(side="left", padx=px(2))

        # Volume fader (the centrepiece)
        vol_f = tk.Frame(row)
        vol_f.pack(side="left", padx=px(2))
        tk.Scale(
            vol_f,
            variable=trk.vol_var,
            from_=0,
            to=2,
            resolution=0.05,
            orient="horizontal",
            length=px(90),
        ).pack()
        vol_lbl = tk.Label(vol_f, text="100%", width=4, fg=CLR["accent"], font=(UI_FONT, 7))
        vol_lbl.pack()
        trk.vol_var.trace_add(
            "write",
            lambda *_, label=vol_lbl, v=trk.vol_var: label.config(text=f"{int(v.get() * 100)}%"),
        )

        # Pan
        pan_f = tk.Frame(row)
        pan_f.pack(side="left", padx=px(2))
        tk.Scale(
            pan_f,
            variable=trk.pan_var,
            from_=-1,
            to=1,
            resolution=0.05,
            orient="horizontal",
            length=px(80),
        ).pack()
        pan_lbl = tk.Label(pan_f, text="C", width=3, fg=CLR["fgdim"], font=(UI_FONT, 7))
        pan_lbl.pack()
        trk.pan_var.trace_add(
            "write",
            lambda *_, label=pan_lbl, v=trk.pan_var: label.config(
                text="L" if v.get() < -0.1 else ("R" if v.get() > 0.1 else "C")
            ),
        )

        # Offset, fade in, fade out
        for var, w in [(trk.offset_var, 6), (trk.fadein_var, 4), (trk.fadeout_var, 4)]:
            tk.Entry(row, textvariable=var, width=w, font=(MONO_FONT, 8)).pack(
                side="left", padx=px(2)
            )

        # Loop, Mute
        tk.Checkbutton(row, variable=trk.loop_var, text="↻", font=(UI_FONT, 8)).pack(side="left")
        tk.Checkbutton(
            row, variable=trk.mute_var, text="M", font=(UI_FONT, 8, "bold"), fg=CLR["red"]
        ).pack(side="left")

    def _remove_last_track(self):
        if self.tracks:
            trk = self.tracks.pop()
            trk.row.destroy()
            self.track_count_lbl.config(text=f"{len(self.tracks)} tracks")

    def _browse_video(self):
        p = dialogs.askopenfilename(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", t("ducker.item_2"))]
        )
        if p:
            self.video_path = p
            self.vid_var.set(p)
            from core.hardware import get_video_duration

            dur = get_video_duration(p)
            m, s = divmod(int(dur), 60)
            self.vid_dur_lbl.config(text=f"{m}m {s}s", fg=CLR["fgdim"])
            base = os.path.splitext(p)[0]
            self.out_var.set(base + "_mixed.mp4")

    def _browse_out(self):
        ext = AUDIO_ONLY.get(self.out_fmt.value, ".mp4")
        p = dialogs.asksaveasfilename(defaultextension=ext, filetypes=[("Media", f"*{ext}")])
        if p:
            self.out_var.set(p)

    # ── Render ────────────────────────────────────────────────────────────
    def _track_specs(
        self, active: list[Any], path_of: Callable[[Any], str]
    ) -> list[mx.Track] | None:
        """Every active track as a spec; None (after saying why) when one can't be used."""
        from core import ffmpeg as ff

        specs: list[mx.Track] = []
        for trk in active:
            path = path_of(trk)
            tinfo = ff.try_probe(path)
            if tinfo is None or not tinfo.has_audio:
                messagebox.showwarning(
                    t("common.warning"),
                    f"Track {trk.idx + 1} has no readable audio:\n{path}",
                    parent=self,
                )
                return None
            try:
                offset = ff.parse_time(trk.offset_var.get() or "0")
                fade_in, fade_out = (
                    float(trk.fadein_var.get() or 0),
                    float(trk.fadeout_var.get() or 0),
                )
            except ValueError:
                messagebox.showwarning(
                    t("common.warning"),
                    f"Track {trk.idx + 1}: start and fades must be numbers of seconds.",
                    parent=self,
                )
                return None
            volume, pan = float(trk.vol_var.get()), float(trk.pan_var.get())
            specs.append(
                mx.Track(
                    path, tinfo.duration, offset, fade_in, fade_out, volume, pan, trk.loop_var.get()
                )
            )
        return specs

    def _render(self):
        from tabs import common

        video = (self.vid_var.get() or getattr(self, "video_path", "")).strip()
        info = common.check_source(self, video)
        if info is None:
            return

        def track_path(trk):
            typed = trk.path_var.get().strip()
            return typed if os.path.isfile(typed) else getattr(trk, "full_path", trk.path)

        active = [
            trk
            for trk in self.tracks
            if trk.enabled.get() and not trk.mute_var.get() and track_path(trk)
        ]
        if not active and not self.keep_vid_audio_var.get():
            messagebox.showwarning(
                t("mixer.no_tracks_title"), t("mixer.no_tracks_message"), parent=self
            )
            return
        dur = info.duration
        tracks = self._track_specs(active, track_path)
        if tracks is None:
            return
        keep_own = self.keep_vid_audio_var.get() and info.has_audio
        if not tracks and not keep_own:
            messagebox.showwarning(
                t("mixer.no_tracks_title"), t("mixer.no_tracks_message"), parent=self
            )
            return
        master = mx.Master(
            video_volume=float(self.vid_audio_vol_var.get()) if keep_own else None,
            volume=float(self.master_vol_var.get()),
            normalise=self.normalize_var.get(),
        )
        sources = len(tracks) + (1 if keep_own else 0)
        fmt = self.out_fmt.value
        audio_only = fmt in AUDIO_ONLY
        out = self.out_var.get().strip() or self.suggest_output(video, "mixed", ".mp4")
        if audio_only and not out.lower().endswith(AUDIO_ONLY_EXTS):
            out = os.path.splitext(out)[0] + AUDIO_ONLY[fmt]
        out = self.confirm_output(out, [video] + [track_path(x) for x in active])
        if not out:
            return
        self.out_var.set(out)
        bitrate = "320k" if fmt is MixOutput.AAC_320 else "256k"
        cmd = mx.mix_cmd(video, tracks, master, dur, out, audio_only=audio_only, bitrate=bitrate)
        self.log(self.console, f"Mixing {sources} source(s) → {os.path.basename(out)}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=dur,
            output=out,
            inputs=[video] + [track_path(x) for x in active],
            name="Audio Mixer",
        )
