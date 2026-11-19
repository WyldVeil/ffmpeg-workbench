"""
tab_batch.py  ─  Batch Processor
Apply the same FFmpeg operation to an entire folder of videos at once.
Supports: re-encode, resize, extract audio, convert format.
Progress is shown per-file in the console.
"""
import tkinter as tk
from tkinter import messagebox, ttk
from core import dialogs
import os
import threading

from tabs.base_tab import BaseTab, CLR, UI_FONT, MONO_FONT
from core import ffmpeg as ff
from core.i18n import t


# (id, label) - looked up by position, so translations can never mix them up
OPERATION_LIST = [
    ("reencode", t("batch.re_encode_h_264")),
    ("to_mp4", t("batch.convert_to_mp4")),
    ("audio_mp3", t("batch.extract_audio_mp3")),
    ("audio_aac", t("batch.extract_audio_aac")),
    ("resize", t("batch.resize_video")),
    ("loudness", t("batch.normalise_loudness")),
    ("strip_meta", t("webm_maker.strip_metadata_checkbox")),
]
OPERATIONS = {label: key for key, label in OPERATION_LIST}      # kept for older callers
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v", ".mts", ".m2ts", ".ts", ".mxf", ".wmv")

RESOLUTIONS = ["3840x2160", "2560x1440", "1920x1080", "1280x720",
               "854x480", "640x360"]


class BatchTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.input_files: list[str] = []
        self._cancel_flag = threading.Event()
        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        hdr_inner = tk.Frame(hdr, bg=CLR["panel"])
        hdr_inner.pack(fill="x", padx=20, pady=(14, 13))
        tk.Label(hdr_inner, text="🗂  " + t("tab.batch_processor"), font=(UI_FONT, 15, "bold"),
                 bg=CLR["panel"], fg=CLR["accent"]).pack(side="left")
        tk.Label(hdr_inner, text=t("batch.desc_subtitle"),
                 font=(UI_FONT, 10), bg=CLR["panel"],
                 fg=CLR["fgdim"]).pack(side="left", padx=(16, 0))
        tk.Frame(self, bg=CLR["border"], height=1).pack(fill="x")

        # ── Input files ───────────────────────────────────────────────────
        inp_lf = tk.LabelFrame(self, text=t("section.input_files"), padx=12, pady=8)
        inp_lf.pack(fill="both", expand=True, padx=20, pady=8)

        self.listbox = tk.Listbox(
            inp_lf, selectmode="extended", font=(MONO_FONT, 9),
            height=10, bg=CLR["bg"], fg=CLR["fg"],
            selectbackground=CLR["accent"], selectforeground="black",
            activestyle="dotbox")
        sb = ttk.Scrollbar(inp_lf, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btn_f = tk.Frame(self); btn_f.pack(pady=4)
        for txt, cmd, bg in [
            ("➕ Add Files",     self._add_files,  "#3A3A3A"),
            ("📁 Add Folder",    self._add_folder, "#3A3A3A"),
            ("🗑 Remove Sel.",   self._remove_sel, "#3A3A3A"),
            ("🧹 Clear All",     self._clear,      "#3A3A3A"),
        ]:
            tk.Button(btn_f, text=txt, bg=bg, fg=CLR["fg"], width=14,
                      command=cmd).pack(side="left", padx=4)

        self._count_lbl = tk.Label(self, text=t("batch.0_files_queued"),
                                    fg=CLR["fgdim"], font=(UI_FONT, 9))
        self._count_lbl.pack()

        # ── Operation ─────────────────────────────────────────────────────
        op_lf = tk.LabelFrame(self, text=f"  {t('batch.operation_section')}  ", padx=14, pady=10)
        op_lf.pack(fill="x", padx=20, pady=4)

        op_row = tk.Frame(op_lf); op_row.pack(fill="x", pady=4)
        tk.Label(op_row, text=t("batch.operation"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._op_var = tk.StringVar(value=list(OPERATIONS.keys())[0])
        op_cb = ttk.Combobox(op_row, textvariable=self._op_var,
                              values=list(OPERATIONS.keys()),
                              state="readonly", width=28)
        op_cb.pack(side="left", padx=8)
        op_cb.bind("<<ComboboxSelected>>", self._on_op_change)

        # Dynamic options frame
        self._op_opts = tk.Frame(op_lf)
        self._op_opts.pack(fill="x", pady=4)

        # CRF / preset (for re-encode)
        self._enc_row = tk.Frame(self._op_opts)
        tk.Label(self._enc_row, text=t("common.crf")).pack(side="left")
        self._crf_var = tk.StringVar(value="18")
        tk.Entry(self._enc_row, textvariable=self._crf_var, width=4, relief="flat").pack(side="left", padx=4)
        tk.Label(self._enc_row, text=t("rotate_flip.preset")).pack(side="left")
        self._preset_var = tk.StringVar(value="fast")
        ttk.Combobox(self._enc_row, textvariable=self._preset_var,
                     values=["ultrafast","fast","medium","slow"],
                     state="readonly", width=10).pack(side="left", padx=4)
        self._enc_row.pack(fill="x", pady=2)

        # Resolution (for resize)
        self._res_row = tk.Frame(self._op_opts)
        tk.Label(self._res_row, text=t("batch.target_resolution")).pack(side="left")
        self._res_var = tk.StringVar(value="1920x1080")
        ttk.Combobox(self._res_row, textvariable=self._res_var,
                     values=RESOLUTIONS, state="readonly", width=12).pack(side="left", padx=4)
        tk.Label(self._res_row, text=t("batch.scale_down_keeps_aspect_ratio"),
                 fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(side="left")

        # ── Output folder ─────────────────────────────────────────────────
        out_lf = tk.LabelFrame(self, text=t("section.output"), padx=14, pady=8)
        out_lf.pack(fill="x", padx=20, pady=4)

        of = tk.Frame(out_lf); of.pack(fill="x", pady=4)
        tk.Label(of, text=t("common.output_folder"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._out_var = tk.StringVar()
        tk.Entry(of, textvariable=self._out_var, width=52, relief="flat").pack(side="left", padx=8)
        tk.Button(of, text=t("btn.browse"), command=self._browse_out, cursor="hand2", relief="flat").pack(side="left")
        tk.Label(out_lf, text=t("batch.output_files_are_saved_as_originalname_batch_ext"),
                 fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(anchor="w")

        # ── Controls ─────────────────────────────────────────────────────
        ctrl_f = tk.Frame(self); ctrl_f.pack(pady=8)
        self._btn_run = tk.Button(
            ctrl_f, text=t("batch.process_all_files"),
            font=(UI_FONT, 12, "bold"), bg=CLR["green"], fg="white",
            height=2, width=24, command=self._run)
        self._btn_run.pack(side="left", padx=8)
        tk.Button(ctrl_f, text=t("batch.cancel"),
                  bg=CLR["red"], fg="white", width=10,
                  command=self._cancel).pack(side="left", padx=4)
        self._prog_lbl = tk.Label(ctrl_f, text="", fg=CLR["accent"],
                                   font=(UI_FONT, 10))
        self._prog_lbl.pack(side="left", padx=12)

        # Console
        cf = tk.Frame(self); cf.pack(fill="both", expand=True, padx=20, pady=4)
        self.console, csb = self.make_console(cf, height=8)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

        self._on_op_change()

    # ─── File management ──────────────────────────────────────────────────
    def _add_files(self):
        for p in dialogs.ask_open_many("media", title="Add files", parent=self):
            if p not in self.input_files:
                self.input_files.append(p)
                self.listbox.insert(tk.END, f"  {os.path.basename(p)}")
        self._update_count()

    def _add_folder(self):
        folder = dialogs.askdirectory()
        if not folder: return
        for fn in sorted(os.listdir(folder)):
            if os.path.splitext(fn)[1].lower() in VIDEO_EXTS + AUDIO_EXTS:
                full = os.path.join(folder, fn)
                if full not in self.input_files:
                    self.input_files.append(full)
                    self.listbox.insert(tk.END, f"  {fn}")
        self._update_count()

    def _remove_sel(self):
        for i in reversed(self.listbox.curselection()):
            self.input_files.pop(i)
            self.listbox.delete(i)
        self._update_count()

    def _clear(self):
        self.input_files.clear()
        self.listbox.delete(0, tk.END)
        self._update_count()

    def _update_count(self):
        n = len(self.input_files)
        self._count_lbl.config(text=f"{n} file{'s' if n != 1 else ''} queued")

    def _browse_out(self):
        p = dialogs.askdirectory()
        if p: self._out_var.set(p)

    def load_file(self, path) -> bool:
        if os.path.isfile(path) and path not in self.input_files:
            self.input_files.append(path)
            self.listbox.insert(tk.END, f"  {os.path.basename(path)}")
            self._update_count()
        return True

    def _cancel(self):
        tid = getattr(self, "_batch_tid", None)
        if tid:
            self.cancel_render(tid)          # stops the running file at once
            self.log(self.console, "Cancelling…")

    # ─── Operation UI ─────────────────────────────────────────────────────
    def _op(self) -> str:
        labels = [label for _k, label in OPERATION_LIST]
        try:
            return OPERATION_LIST[labels.index(self._op_var.get())][0]
        except ValueError:
            return "reencode"

    def _on_op_change(self, *_):
        op = self._op()
        self._enc_row.pack_forget()
        self._res_row.pack_forget()
        if op in ("reencode", "to_mp4"):
            self._enc_row.pack(fill="x", pady=2)
        elif op == "resize":
            self._res_row.pack(fill="x", pady=2)
            self._enc_row.pack(fill="x", pady=2)

    # ─── Render ──────────────────────────────────────────────────────────
    def _run(self):
        from tabs import common
        if not self.input_files:
            messagebox.showwarning(t("common.warning"), "Add at least one file to process.", parent=self)
            return
        out_dir = self._out_var.get().strip() or dialogs.askdirectory(title="Select output folder")
        if not out_dir:
            return
        self._out_var.set(out_dir)
        try:
            os.makedirs(out_dir, exist_ok=True)
            crf = int(common.number(self._crf_var.get(), "Quality (CRF)", 0, 51))
        except (OSError, ValueError) as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        op = self._op()
        opts = {"crf": crf, "preset": self._preset_var.get(), "res": self._res_var.get()}
        files = self.input_files[:]
        taken = {os.path.normcase(os.path.abspath(f)) for f in files}

        def work(_p, _c, ctx):
            done, skipped, failed = 0, [], []
            for i, src in enumerate(files):
                ctx.span(i / len(files), (i + 1) / len(files))
                ctx.log(f"[{i + 1}/{len(files)}] {os.path.basename(src)}")
                try:
                    info = ff.probe(src)
                    plan = batch_plan(op, info, src, out_dir, opts, taken)
                    if isinstance(plan, str):
                        skipped.append((src, plan))
                        ctx.log(f"  skipped: {plan}")
                        continue
                    out, steps = plan
                    taken.add(os.path.normcase(out))
                    part = f"{os.path.splitext(out)[0]}.part{os.path.splitext(out)[1]}"
                    try:
                        for step in steps:
                            argv = step(ctx) if callable(step) else step
                            ctx.run([a if a != "{OUT}" else part for a in argv], duration=info.duration or None,
                                    tail=200000)
                        os.replace(part, out)
                    finally:
                        if os.path.exists(part):
                            os.remove(part)
                    done += 1
                    ctx.log(f"  ✓ {os.path.basename(out)}")
                except ff.CancelledError:
                    raise
                except Exception as exc:
                    reason = (str(exc).strip().splitlines() or [type(exc).__name__])[-1]
                    failed.append((src, reason))
                    ctx.log(f"  ✖ {reason}")
            ctx.log(f"Done: {done} · skipped {len(skipped)} · failed {len(failed)}")
            if failed:
                raise RuntimeError(f"{len(failed)} of {len(files)} file(s) failed:\n" +
                                   "\n".join(f"• {os.path.basename(s_)}: {r}" for s_, r in failed[:12]))
            return 0

        self._batch_tid = common.queue_job(self, name=f"Batch: {len(files)} file(s)", out=out_dir, work=work,
                                           btn=self._btn_run, btn_label=t("batch.process_all_files"),
                                           console=self.console, inputs=files)


def batch_plan(op, info, src, out_dir, opts, taken):
    """(output path, [argv | callable(ctx) -> argv]) or a reason string to skip.
    '{OUT}' in an argv stands for the temporary output file."""
    from core import encoders as enc
    from core import loudness
    from tabs.transcoder.converter import free_name, stream_args
    base = os.path.splitext(os.path.basename(src))[0]
    src_ext = os.path.splitext(src)[1].lower()
    ffm = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-y"]

    def name(suffix, ext):
        return free_name(os.path.join(out_dir, f"{base}{suffix}{ext}"), taken)

    if op in ("audio_mp3", "audio_aac"):
        if not info.has_audio:
            return "no audio"
        mp3 = op == "audio_mp3"
        out = name("", ".mp3" if mp3 else ".m4a")
        codec = ["-c:a", "libmp3lame", "-q:a", "2"] if mp3 else ["-c:a", "aac", "-b:a", "192k"]
        return out, [ffm + ["-i", src, "-map", "0:a:0", "-vn", *codec, "-map_metadata", "0", "{OUT}"]]
    if op == "loudness":
        if not info.has_audio:
            return "no audio to normalise"
        ext = src_ext if src_ext in (".mp4", ".mkv", ".mov", ".webm", ".m4a", ".mp3", ".flac", ".wav") else ".mkv"
        out = name("_loud", ext)
        rate = info.audio.sample_rate or 48000
        container = ext.lstrip(".")

        def second_pass(ctx):
            res = ctx.run(loudness.measure_cmd(src, -16.0, -1.5, 11.0), duration=info.duration or None, tail=200000)
            meas = loudness.parse_measurement(res.log_tail)
            acodec = enc.default_audio_for(container) if container not in ("wav", "flac", "mp3") else \
                {"wav": "pcm16", "flac": "flac", "mp3": "mp3"}[container]
            vargs = ["-map", "0:v?", "-c:v", "copy"]
            if info.has_video and info.video.codec == "hevc" and container in ("mp4", "mov"):
                vargs += ["-tag:v", "hvc1"]
            return ffm + ["-i", src, *vargs, "-map", "0:a:0", "-af",
                          loudness.normalise_filter(-16.0, -1.5, 11.0, meas, rate),
                          *enc.audio(acodec, "192k", container=container), "-map", "0:s?", "-c:s", "copy",
                          "-map_metadata", "0", "{OUT}"]
        return out, [second_pass]
    if op == "strip_meta":
        out = name("_clean", src_ext or ".mkv")
        keep = []
        for st in info.streams:
            lang = st.tags.get("language")
            if lang:
                keep += [f"-metadata:s:{st.index}", f"language={lang}"]
        return out, [ffm + ["-i", src, "-map", "0", "-c", "copy", "-map_metadata", "-1", "-map_chapters", "-1",
                            *keep, "-fflags", "+bitexact", "{OUT}"]]
    if not info.has_video:
        return "no video"
    if op == "to_mp4":
        out = name("", ".mp4") if src_ext != ".mp4" else name("_converted", ".mp4")
        pre, args, _notes, _summary = stream_args(info, "mp4", True)
        return out, [ffm + pre + ["-i", src, *args, "{OUT}"]]
    crf = opts["crf"]
    quality = "very_high" if crf <= 18 else "high" if crf <= 22 else "good" if crf <= 26 else \
        "low" if crf <= 30 else "lowest"
    speed = {"ultrafast": "fastest", "superfast": "fastest", "veryfast": "fast", "faster": "fast", "fast": "fast",
             "medium": "balanced", "slow": "slow", "slower": "slowest", "veryslow": "slowest"}.get(
        opts["preset"], "balanced")
    ve = enc.video("h264", quality, speed=speed, hw="auto", container="mp4")
    vf = []
    suffix = "_h264"
    if op == "resize":
        bw, bh = (int(x) for x in opts["res"].split("x"))
        w, h = info.video.display_size
        if h > w:
            bw, bh = bh, bw                       # portrait videos get a portrait box
        if w <= bw and h <= bh:
            return "already that size or smaller"
        vf.append(f"scale={bw}:{bh}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1")
        suffix = f"_{min(bw, bh)}p"
    out = name(suffix, ".mp4")
    audio = ["-map", "0:a?", "-c:a", "aac", "-b:a", "192k"]
    return out, [ffm + ve.pre_input + ["-i", src, "-map", "0:v:0", *ve.vf_args(",".join(vf) or None), *ve.args,
                                       *audio, "-map_metadata", "0", "{OUT}"]]
