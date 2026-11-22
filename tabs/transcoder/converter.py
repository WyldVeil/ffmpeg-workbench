"""
tabs/transcoder/converter.py  -  Format Converter

Convert any number of files to another container or audio format.

Smart mode keeps every stream that the new format can hold exactly as it is
(no quality loss, very fast) and converts only what it cannot hold - e.g.
MKV(H.264 + DTS + SRT) -> MP4 copies the video, converts DTS to AAC and SRT to
MP4 text subtitles.  "Always re-encode" converts everything.

All audio tracks, subtitles, chapters and metadata are kept when the format
allows; outputs never overwrite anything (a free name is chosen) and each
file's result is checked - failures are reported, not counted as done.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import dialogs
from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab

FORMATS = {   # key: (label, kind)
    "mp4": ("MP4 - plays everywhere", "video"), "mkv": ("MKV - holds any codec", "video"),
    "mov": ("MOV - Apple / editing", "video"), "webm": ("WebM - web (VP9/AV1 + Opus)", "video"),
    "m4v": ("M4V - Apple devices", "video"), "ts": ("TS - broadcast stream", "video"),
    "avi": ("AVI - legacy", "video"),
    "mp3": ("MP3 audio", "audio"), "m4a": ("M4A (AAC) audio", "audio"), "wav": ("WAV audio", "audio"),
    "flac": ("FLAC audio (lossless)", "audio"), "ogg": ("OGG Vorbis audio", "audio"),
    "opus": ("Opus audio", "audio"),
}
VIDEO_KEYS = {"h264": "h264", "hevc": "hevc", "av1": "av1", "vp9": "vp9", "vp8": "vp8", "mpeg4": "mpeg4",
              "prores": "prores", "dnxhd": "dnxhr", "mjpeg": "mjpeg", "ffv1": "ffv1"}
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
SUB_CODEC = {"mp4": "mov_text", "m4v": "mov_text", "mov": "mov_text", "webm": "webvtt", "mkv": "copy"}
#: legal in MP4/MOV but unplayable on Apple devices and many TVs/players: convert instead of copying
POOR_IN_MP4 = {"vp9", "vp8", "mpeg4", "opus", "flac", "vorbis"}
AUDIO_FORMAT_ENCODE = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"], "m4a": ["-c:a", "aac", "-b:a", "256k"],
    "flac": ["-c:a", "flac", "-compression_level", "8"], "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
    "opus": ["-c:a", "libopus", "-b:a", "160k", "-ar", "48000"],
}


def stream_args(info: ff.MediaInfo, fmt: str, smart: bool = True) -> tuple[list, list, list, str]:
    """(pre-input args, output args, notes, summary) for converting *info* to *fmt*."""
    pre, args, notes, actions = [], [], [], []
    audio_only = FORMATS[fmt][1] == "audio"
    videos = [s for s in info.streams if s.type == "video" and not s.attached_pic]
    audios = [s for s in info.streams if s.type == "audio"]
    subs = [s for s in info.streams if s.type == "subtitle"]
    if audio_only:
        if not audios:
            raise ValueError("no audio")
        a = audios[0]
        key = enc.audio_key_from_probe(a.codec)
        args += ["-map", f"0:{a.index}", "-vn"]
        if fmt == "wav":
            deep = (a.bits_per_raw_sample or 16) > 16 or a.codec in ("flac", "alac", "pcm_s24le", "pcm_s32le",
                                                                      "pcm_f32le")
            args += ["-c:a", "pcm_s24le" if deep else "pcm_s16le"]
            actions.append("audio → WAV")
        elif smart and key and enc.audio_ok_for(fmt, key):
            args += ["-c:a", "copy"]
            actions.append(f"audio {a.codec} copied")
        else:
            args += AUDIO_FORMAT_ENCODE[fmt]
            actions.append(f"audio {a.codec} → {AUDIO_FORMAT_ENCODE[fmt][1]}")
        if len(audios) > 1:
            notes.append(f"{len(audios) - 1} extra audio track(s) left out (audio formats hold one).")
        return pre, args + ["-map_metadata", "0"], notes, ", ".join(actions)

    if videos:
        v = videos[0]
        key = VIDEO_KEYS.get(v.codec, v.codec)
        args += ["-map", f"0:{v.index}"]
        if smart and enc.video_ok_for(fmt, key) and not (fmt in ("mp4", "m4v", "mov") and key in POOR_IN_MP4):
            args += ["-c:v", "copy"]
            if key == "hevc" and fmt in ("mp4", "mov", "m4v"):
                args += ["-tag:v", "hvc1"]
            actions.append(f"video {v.codec} copied")
        else:
            codec = enc.CONTAINER_VIDEO.get(fmt, ("h264",))[0]
            ten = (v.bit_depth > 8) and codec != "h264"
            ve = enc.video(codec, "high", hw="auto", ten_bit=ten, container=fmt)
            pre += ve.pre_input
            args += ve.vf_args() + ve.args
            actions.append(f"video {v.codec} → {ve.encoder}")
        if len(videos) > 1:
            notes.append(f"{len(videos) - 1} extra video stream(s) left out.")
    for n, a in enumerate(audios):
        key = enc.audio_key_from_probe(a.codec)
        args += ["-map", f"0:{a.index}"]
        if smart and key and enc.audio_ok_for(fmt, key) and not (fmt in ("mp4", "m4v", "mov")
                                                                  and key in POOR_IN_MP4):
            args += [f"-c:a:{n}", "copy"]
            actions.append(f"audio {a.codec} copied")
        else:
            target = enc.default_audio_for(fmt)
            spec = enc.audio(target, container=fmt)
            args += [f"{x}:{n}" if x in ("-c:a", "-b:a", "-q:a") else x for x in spec]
            actions.append(f"audio {a.codec} → {spec[1]}")
    kept_subs = 0
    sub_codec = SUB_CODEC.get(fmt)
    for s in subs:
        if sub_codec == "copy" or (fmt == "ts" and s.codec == "dvb_subtitle"):
            args += ["-map", f"0:{s.index}", f"-c:s:{kept_subs}", "copy"]
            kept_subs += 1
        elif sub_codec and s.codec in TEXT_SUBS:
            args += ["-map", f"0:{s.index}", f"-c:s:{kept_subs}", sub_codec]
            kept_subs += 1
        else:
            notes.append(f"Subtitle track {s.index} ({s.codec}) can't go into {fmt.upper()} - left out.")
    if kept_subs:
        actions.append(f"{kept_subs} subtitle track(s)")
    if fmt == "mkv":
        args += ["-map", "0:t?", "-c:t", "copy"]          # attached fonts
    args += ["-map_metadata", "0", "-map_chapters", "0"]
    if fmt in ("mp4", "mov", "m4v"):
        args += ["-movflags", "+faststart"]
    return pre, args, notes, ", ".join(actions)


def free_name(path: str, taken: set) -> str:
    base, ext = os.path.splitext(path)
    cand, n = path, 2
    while os.path.exists(cand) or os.path.normcase(cand) in taken:
        cand = f"{base} ({n}){ext}"
        n += 1
    return cand


class FormatConverterTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_paths = []
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.format_converter"), t("converter.desc_subtitle"), icon="🔄")
        lf = tk.LabelFrame(self, text=t("section.input_files"), padx=px(10), pady=px(6))
        lf.pack(fill="both", expand=True, padx=px(20), pady=px(8))
        self.listbox = tk.Listbox(lf, height=7, font=(MONO_FONT, 10), selectmode="extended")
        sb = ttk.Scrollbar(lf, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        btn_f = tk.Frame(self)
        btn_f.pack(pady=px(4))
        for label, cmd in ((t("converter.btn_add_files"), self._add), (t("converter.btn_remove"), self._remove),
                           (t("converter.btn_clear"), self._clear)):
            tk.Button(btn_f, text=label, command=cmd, cursor="hand2", relief="flat").pack(side="left", padx=px(4))
        tk.Label(btn_f, text="(or drop files here)", fg=CLR["fgdim"]).pack(side="left", padx=px(6))

        opts = tk.LabelFrame(self, text=t("converter.sect_conversion_options"), padx=px(15), pady=px(8))
        opts.pack(fill="x", padx=px(20), pady=px(5))
        r0 = tk.Frame(opts)
        r0.pack(fill="x", pady=px(4))
        tk.Label(r0, text=t("converter.lbl_output_format"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.fmt_var = tk.StringVar(value=FORMATS["mp4"][0])
        ttk.Combobox(r0, textvariable=self.fmt_var, values=[v[0] for v in FORMATS.values()], state="readonly",
                     width=30).pack(side="left", padx=px(8))
        self.copy_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opts, text="Smart: keep streams the new format can hold as they are (fast, no quality "
                                  "loss) and convert only the rest", variable=self.copy_var).pack(anchor="w")
        r2 = tk.Frame(opts)
        r2.pack(fill="x", pady=px(4))
        tk.Label(r2, text=t("common.output_folder")).pack(side="left")
        self.out_dir_var = tk.StringVar()
        tk.Entry(r2, textvariable=self.out_dir_var, width=50).pack(side="left", padx=px(6))
        tk.Button(r2, text=t("btn.browse"), command=self._browse_dir, relief="flat").pack(side="left")
        tk.Label(r2, text=t("converter.lbl_blank_hint"), fg=CLR["fgdim"]).pack(side="left", padx=px(6))
        self.btn_render = tk.Button(self, text=t("converter.btn_convert_all"), font=(UI_FONT, 12, "bold"),
                                    bg=CLR["green"], fg="white", height=2, width=28, command=self._render)
        self.btn_render.pack(pady=px(10))
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(4))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def load_file(self, path) -> bool:
        if os.path.isfile(path) and path not in self.file_paths:
            self.file_paths.append(path)
            self.listbox.insert(tk.END, f"  {os.path.basename(path)}")
        return True

    def _add(self):
        for p in dialogs.ask_open_many("media", title="Add files", parent=self):
            self.load_file(p)

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            self.file_paths.pop(i)
            self.listbox.delete(i)

    def _clear(self):
        self.file_paths.clear()
        self.listbox.delete(0, tk.END)

    def _browse_dir(self):
        p = dialogs.ask_directory(parent=self)
        if p:
            self.out_dir_var.set(p)

    def _fmt(self) -> str:
        return next((k for k, v in FORMATS.items() if v[0] == self.fmt_var.get()), "mp4")

    def _render(self):
        if not self.file_paths:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        fmt = self._fmt()
        out_dir = self.out_dir_var.get().strip()
        if out_dir and not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as exc:
                messagebox.showwarning(t("common.warning"), f"Can't use that folder:\n{exc}", parent=self)
                return
        smart = self.copy_var.get()
        paths = self.file_paths[:]
        taken: set = set()
        jobs = []
        for src in paths:
            base = os.path.splitext(os.path.basename(src))[0]
            out = free_name(os.path.join(out_dir or os.path.dirname(src), f"{base}.{fmt}"),
                            taken | {os.path.normcase(os.path.abspath(p)) for p in paths})
            taken.add(os.path.normcase(out))
            jobs.append((src, out))

        def work(_p, _c, ctx):
            done, failed = [], []
            for i, (src, out) in enumerate(jobs):
                ctx.span(i / len(jobs), (i + 1) / len(jobs))
                ctx.log(f"[{i + 1}/{len(jobs)}] {os.path.basename(src)} → {os.path.basename(out)}")
                part = f"{os.path.splitext(out)[0]}.part{os.path.splitext(out)[1]}"
                try:
                    info = ff.probe(src)
                    pre, args, notes, summary = stream_args(info, fmt, smart)
                    for note in notes:
                        ctx.log("  " + note)
                    ctx.log(f"  {summary}")
                    ctx.run([ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *pre, "-i", src, *args, part],
                            duration=info.duration or None)
                    os.replace(part, out)
                    done.append(out)
                except ff.CancelledError:
                    _remove(part)
                    raise
                except Exception as exc:          # one bad file must not stop the batch
                    _remove(part)
                    reason = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
                    if reason == "no audio":
                        reason = "it has no audio"
                    failed.append((src, reason))
                    ctx.log(f"  ✖ failed: {reason}")
            ctx.log(f"Converted {len(done)} of {len(jobs)} file(s).")
            if failed:
                raise RuntimeError(f"{len(failed)} of {len(jobs)} file(s) failed:\n" +
                                   "\n".join(f"• {os.path.basename(s)}: {r}" for s, r in failed))
            return 0

        out_label = jobs[0][1] if len(jobs) == 1 else os.path.dirname(jobs[0][1])
        common.queue_job(self, name=f"Convert {len(jobs)} file(s) to {fmt.upper()}", out=out_label, work=work,
                         btn=self.btn_render, btn_label=t("converter.btn_convert_all"), console=self.console,
                         inputs=paths)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
