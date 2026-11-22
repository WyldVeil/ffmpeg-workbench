"""
tab_batch.py  ─  Batch Processor
Apply the same FFmpeg operation to an entire folder of videos at once.
Supports: re-encode, resize, extract audio, convert format.
Progress is shown per-file in the console.
"""

import logging
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from core.i18n import t
from core.ops import batch as batch_ops
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs

logger = logging.getLogger("crossfader.system.batch")

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
OPERATIONS = {label: key for key, label in OPERATION_LIST}  # kept for older callers
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff")
VIDEO_EXTS = (
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".flv",
    ".m4v",
    ".mts",
    ".m2ts",
    ".ts",
    ".mxf",
    ".wmv",
)

RESOLUTIONS = ["3840x2160", "2560x1440", "1920x1080", "1280x720", "854x480", "640x360"]


class BatchTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.input_files: list[str] = []
        self._cancel_flag = threading.Event()
        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        make_header(self, t("tab.batch_processor"), t("batch.desc_subtitle"), icon="🗂")
        self._build_input_files()
        self._build_operation()
        self._build_output_folder()
        self._build_controls()

    def _build_input_files(self):
        """Input files."""
        # ── Input files ───────────────────────────────────────────────────
        inp_lf = tk.LabelFrame(self, text=t("section.input_files"), padx=px(12), pady=px(8))
        inp_lf.pack(fill="both", expand=True, padx=px(20), pady=px(8))

        self.listbox = tk.Listbox(
            inp_lf,
            selectmode="extended",
            font=(MONO_FONT, 9),
            height=10,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectbackground=CLR["accent"],
            selectforeground="black",
            activestyle="dotbox",
        )
        sb = ttk.Scrollbar(inp_lf, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btn_f = tk.Frame(self)
        btn_f.pack(pady=px(4))
        for txt, cmd, bg in [
            ("➕ Add Files", self._add_files, "#3A3A3A"),
            ("📁 Add Folder", self._add_folder, "#3A3A3A"),
            ("🗑 Remove Sel.", self._remove_sel, "#3A3A3A"),
            ("🧹 Clear All", self._clear, "#3A3A3A"),
        ]:
            tk.Button(btn_f, text=txt, bg=bg, fg=CLR["fg"], width=14, command=cmd).pack(
                side="left", padx=px(4)
            )

        self._count_lbl = tk.Label(
            self, text=t("batch.0_files_queued"), fg=CLR["fgdim"], font=(UI_FONT, 9)
        )
        self._count_lbl.pack()

    def _build_operation(self):
        """Operation."""
        # ── Operation ─────────────────────────────────────────────────────
        op_lf = tk.LabelFrame(
            self, text=f"  {t('batch.operation_section')}  ", padx=px(14), pady=px(10)
        )
        op_lf.pack(fill="x", padx=px(20), pady=px(4))

        op_row = tk.Frame(op_lf)
        op_row.pack(fill="x", pady=px(4))
        tk.Label(op_row, text=t("batch.operation"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._op_var = tk.StringVar(value=next(iter(OPERATIONS.keys())))
        op_cb = ttk.Combobox(
            op_row,
            textvariable=self._op_var,
            values=list(OPERATIONS.keys()),
            state="readonly",
            width=28,
        )
        op_cb.pack(side="left", padx=px(8))
        op_cb.bind("<<ComboboxSelected>>", self._on_op_change)

        # Dynamic options frame
        self._op_opts = tk.Frame(op_lf)
        self._op_opts.pack(fill="x", pady=px(4))

        # CRF / preset (for re-encode)
        self._enc_row = tk.Frame(self._op_opts)
        tk.Label(self._enc_row, text=t("common.crf")).pack(side="left")
        self._crf_var = tk.StringVar(value="18")
        tk.Entry(self._enc_row, textvariable=self._crf_var, width=4, relief="flat").pack(
            side="left", padx=px(4)
        )
        tk.Label(self._enc_row, text=t("rotate_flip.preset")).pack(side="left")
        self._preset_var = tk.StringVar(value="fast")
        ttk.Combobox(
            self._enc_row,
            textvariable=self._preset_var,
            values=["ultrafast", "fast", "medium", "slow"],
            state="readonly",
            width=10,
        ).pack(side="left", padx=px(4))
        self._enc_row.pack(fill="x", pady=px(2))

        # Resolution (for resize)
        self._res_row = tk.Frame(self._op_opts)
        tk.Label(self._res_row, text=t("batch.target_resolution")).pack(side="left")
        self._res_var = tk.StringVar(value="1920x1080")
        ttk.Combobox(
            self._res_row,
            textvariable=self._res_var,
            values=RESOLUTIONS,
            state="readonly",
            width=12,
        ).pack(side="left", padx=px(4))
        tk.Label(
            self._res_row,
            text=t("batch.scale_down_keeps_aspect_ratio"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(side="left")

    def _build_output_folder(self):
        """Output folder."""
        # ── Output folder ─────────────────────────────────────────────────
        out_lf = tk.LabelFrame(self, text=t("section.output"), padx=px(14), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(4))

        of = tk.Frame(out_lf)
        of.pack(fill="x", pady=px(4))
        tk.Label(of, text=t("common.output_folder"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._out_var = tk.StringVar()
        tk.Entry(of, textvariable=self._out_var, width=52, relief="flat").pack(
            side="left", padx=px(8)
        )
        tk.Button(
            of, text=t("btn.browse"), command=self._browse_out, cursor="hand2", relief="flat"
        ).pack(side="left")
        tk.Label(
            out_lf,
            text=t("batch.output_files_are_saved_as_originalname_batch_ext"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

    def _build_controls(self):
        """Controls."""
        # ── Controls ─────────────────────────────────────────────────────
        ctrl_f = tk.Frame(self)
        ctrl_f.pack(pady=px(8))
        self.btn_render = self.make_render_btn(
            ctrl_f, t("batch.process_all_files"), self._run, width=24
        )
        self.btn_render.pack(side="left", padx=px(8))
        tk.Button(
            ctrl_f,
            text=t("batch.cancel"),
            bg=CLR["red"],
            fg="white",
            width=10,
            command=self._cancel,
        ).pack(side="left", padx=px(4))
        self._prog_lbl = tk.Label(ctrl_f, text="", fg=CLR["accent"], font=(UI_FONT, 10))
        self._prog_lbl.pack(side="left", padx=px(12))

        # Console
        self.console = self.make_console_block(self, height=8, padx=px(20), pady=px(4))

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
        if not folder:
            return
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
        if p:
            self._out_var.set(p)

    def load_file(self, path) -> bool:
        if os.path.isfile(path) and path not in self.input_files:
            self.input_files.append(path)
            self.listbox.insert(tk.END, f"  {os.path.basename(path)}")
            self._update_count()
        return True

    def _cancel(self):
        tid = getattr(self, "_batch_tid", None)
        if tid:
            self.cancel_render(tid)  # stops the running file at once
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
            self._enc_row.pack(fill="x", pady=px(2))
        elif op == "resize":
            self._res_row.pack(fill="x", pady=px(2))
            self._enc_row.pack(fill="x", pady=px(2))

    # ─── Render ──────────────────────────────────────────────────────────
    def _run(self):
        from tabs import common

        if not self.input_files:
            messagebox.showwarning(
                t("common.warning"), "Add at least one file to process.", parent=self
            )
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

        job = batch_ops.Batch(op, files, out_dir, opts, taken)
        self._batch_tid = common.queue_job(
            self,
            common.Job(
                name=f"Batch: {len(files)} file(s)", out=out_dir, work=job.run, inputs=files
            ),
            btn=self.btn_render,
            console=self.console,
        )
