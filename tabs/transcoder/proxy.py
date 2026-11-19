"""
tabs/transcoder/proxy.py  -  Proxy Generator

Lightweight copies of camera files for smooth editing, made the way editors
expect them: short GOP or intra-frame codecs, even sizes, every audio track
as PCM in MOV, timecode and metadata kept, names that relink easily
(same name + suffix, or the same name in a "Proxy" folder).  Each file is
checked; failures are reported and never counted as done.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import dialogs
from core import ffmpeg as ff
from core.i18n import t
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.transcoder.converter import free_name

PRESETS = {   # key: (label, ext, video args, audio mode)
    "prores": ("ProRes Proxy (.mov) - Premiere, Resolve, Final Cut", ".mov",
               ["-c:v", "prores_ks", "-profile:v", "0", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"], "pcm"),
    "dnxhr": ("DNxHR LB (.mov) - Avid, Resolve", ".mov",
              ["-c:v", "dnxhd", "-profile:v", "dnxhr_lb", "-pix_fmt", "yuv422p"], "pcm"),
    "h264": ("H.264 short-GOP (.mp4) - small, any editor", ".mp4",
             ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-tune", "fastdecode", "-g", "12",
              "-bf", "0", "-pix_fmt", "yuv420p"], "aac"),
    "mjpeg": ("MJPEG draft (.mov) - very light to decode", ".mov",
              ["-c:v", "mjpeg", "-q:v", "6", "-pix_fmt", "yuvj422p"], "pcm"),
}
SIZES = {"half": ("Half size", 0.5), "quarter": ("Quarter size", 0.25), "720": ("720p", 720),
         "540": ("540p", 540), "same": ("Same size", 1.0)}
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".braw", ".r3d", ".mpg", ".webm", ".m4v")


def scale_filter(size_key: str) -> str:
    val = SIZES[size_key][1]
    if size_key == "same":
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
    if isinstance(val, float):
        return f"scale=trunc(iw*{val}/2)*2:trunc(ih*{val}/2)*2,setsar=1"
    return f"scale=-2:'min({val},ih)',setsar=1"


def proxy_args(info: ff.MediaInfo, preset: str, size_key: str, audio: bool) -> list:
    _label, _ext, video, amode = PRESETS[preset]
    args = ["-map", "0:v:0", "-vf", scale_filter(size_key), *video]
    if audio:
        for n, a in enumerate(s for s in info.streams if s.type == "audio"):
            args += ["-map", f"0:{a.index}"]
            args += [f"-c:a:{n}", "pcm_s16le"] if amode == "pcm" else [f"-c:a:{n}", "aac", f"-b:a:{n}", "160k"]
    else:
        args += ["-an"]
    tc = info.tags.get("timecode") or next((s.tags.get("timecode") for s in info.streams
                                            if s.tags.get("timecode")), None)
    if tc:
        args += ["-timecode", str(tc)]
    args += ["-map_metadata", "0"]
    if preset == "h264":
        args += ["-movflags", "+faststart"]
    return args


class ProxyGenTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_paths = []
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.proxy_generator"), t("proxy.subtitle"), icon="⚙")
        lf = tk.LabelFrame(self, text=t("section.input_files"), padx=10, pady=6)
        lf.pack(fill="both", expand=True, padx=20, pady=8)
        self.listbox = tk.Listbox(lf, height=7, font=(MONO_FONT, 10), selectmode="extended")
        sb = ttk.Scrollbar(lf, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        bf = tk.Frame(self)
        bf.pack(pady=4)
        for label, cmd in ((t("converter.btn_add_files"), self._add), ("Add a folder…", self._add_folder),
                           (t("converter.btn_remove"), self._remove), (t("converter.btn_clear"), self._clear)):
            tk.Button(bf, text=label, command=cmd, relief="flat", cursor="hand2").pack(side="left", padx=4)

        opt = tk.LabelFrame(self, text=f"  {t('proxy.proxy_options_section')}  ", padx=15, pady=8)
        opt.pack(fill="x", padx=20, pady=5)
        r0 = tk.Frame(opt)
        r0.pack(fill="x", pady=3)
        tk.Label(r0, text=t("proxy.preset_label"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.preset_var = tk.StringVar(value=PRESETS["prores"][0])
        ttk.Combobox(r0, textvariable=self.preset_var, values=[v[0] for v in PRESETS.values()], state="readonly",
                     width=50).pack(side="left", padx=8)
        r1 = tk.Frame(opt)
        r1.pack(fill="x", pady=3)
        tk.Label(r1, text="Size:").pack(side="left")
        self.size_var = tk.StringVar(value=SIZES["half"][0])
        ttk.Combobox(r1, textvariable=self.size_var, values=[v[0] for v in SIZES.values()], state="readonly",
                     width=12).pack(side="left", padx=8)
        self.audio_var = tk.BooleanVar(value=True)
        tk.Checkbutton(r1, text="Include every audio track", variable=self.audio_var).pack(side="left", padx=12)
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=3)
        self.place_var = tk.StringVar(value="folder")
        tk.Radiobutton(r2, text="Same name in a “Proxy” folder next to each file (easy relinking)",
                       variable=self.place_var, value="folder").pack(anchor="w")
        r3 = tk.Frame(r2)
        r3.pack(anchor="w")
        tk.Radiobutton(r3, text="Next to the originals, with the suffix", variable=self.place_var,
                       value="suffix").pack(side="left")
        self.suffix_var = tk.StringVar(value="_proxy")
        tk.Entry(r3, textvariable=self.suffix_var, width=12).pack(side="left", padx=4)
        r4 = tk.Frame(r2)
        r4.pack(anchor="w")
        tk.Radiobutton(r4, text="In this folder:", variable=self.place_var, value="dir").pack(side="left")
        self.out_dir_var = tk.StringVar()
        tk.Entry(r4, textvariable=self.out_dir_var, width=44).pack(side="left", padx=4)
        tk.Button(r4, text=t("btn.browse"), relief="flat",
                  command=lambda: self.out_dir_var.set(dialogs.ask_directory(parent=self) or
                                                       self.out_dir_var.get())).pack(side="left")
        self.btn_render = tk.Button(self, text=t("proxy.render_button"), font=(UI_FONT, 12, "bold"),
                                    bg=CLR["green"], fg="white", height=2, width=28, command=self._render)
        self.btn_render.pack(pady=10)
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=20, pady=4)
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def load_file(self, path) -> bool:
        if os.path.isfile(path) and path not in self.file_paths:
            self.file_paths.append(path)
            self.listbox.insert(tk.END, f"  {os.path.basename(path)}")
        return True

    def _add(self):
        for p in dialogs.ask_open_many("video", title="Add camera files", parent=self):
            self.load_file(p)

    def _add_folder(self):
        folder = dialogs.ask_directory(parent=self)
        if not folder:
            return
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if name.lower().endswith(VIDEO_EXTS) and not name.lower().startswith(".") and os.path.isfile(path):
                self.load_file(path)

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            self.file_paths.pop(i)
            self.listbox.delete(i)

    def _clear(self):
        self.file_paths.clear()
        self.listbox.delete(0, tk.END)

    def _target(self, src: str, ext: str) -> str:
        base = os.path.splitext(os.path.basename(src))[0]
        place = self.place_var.get()
        if place == "folder":
            return os.path.join(os.path.dirname(src), "Proxy", base + ext)
        if place == "dir":
            folder = self.out_dir_var.get().strip()
            if not folder:
                raise ValueError("Choose the folder for the proxies.")
            return os.path.join(folder, base + ext)
        suffix = self.suffix_var.get().strip()
        if not suffix:
            raise ValueError("Enter a suffix (or choose another place) so the originals are never replaced.")
        return os.path.join(os.path.dirname(src), base + suffix + ext)

    def _render(self):
        if not self.file_paths:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        preset = next((k for k, v in PRESETS.items() if v[0] == self.preset_var.get()), "prores")
        size_key = next((k for k, v in SIZES.items() if v[0] == self.size_var.get()), "half")
        ext = PRESETS[preset][1]
        sources = {os.path.normcase(os.path.abspath(p)) for p in self.file_paths}
        jobs, taken = [], set()
        try:
            for src in self.file_paths:
                out = free_name(self._target(src, ext), taken | sources)
                taken.add(os.path.normcase(out))
                jobs.append((src, out))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        audio = self.audio_var.get()

        def work(_p, _c, ctx):
            done, failed = 0, []
            for i, (src, out) in enumerate(jobs):
                ctx.span(i / len(jobs), (i + 1) / len(jobs))
                ctx.log(f"[{i + 1}/{len(jobs)}] {os.path.basename(src)} → {out}")
                part = f"{os.path.splitext(out)[0]}.part{ext}"
                try:
                    info = ff.probe(src)
                    if not info.has_video:
                        raise ValueError("no video in this file")
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    ctx.run([ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-i", src,
                             *proxy_args(info, preset, size_key, audio), part], duration=info.duration or None)
                    os.replace(part, out)
                    done += 1
                except ff.CancelledError:
                    _remove(part)
                    raise
                except Exception as exc:
                    _remove(part)
                    reason = (str(exc).strip().splitlines() or [type(exc).__name__])[-1]
                    failed.append((src, reason))
                    ctx.log(f"  ✖ failed: {reason}")
            ctx.log(f"{done} of {len(jobs)} proxies made.")
            if failed:
                raise RuntimeError(f"{len(failed)} of {len(jobs)} file(s) failed:\n" +
                                   "\n".join(f"• {os.path.basename(s)}: {r}" for s, r in failed))
            return 0

        common.queue_job(self, name=f"Proxies ({len(jobs)} files)", out=os.path.dirname(jobs[0][1]), work=work,
                         btn=self.btn_render, btn_label=t("proxy.render_button"), console=self.console,
                         inputs=list(self.file_paths))


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
