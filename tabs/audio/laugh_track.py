"""
tab_laughtrack.py  ─  Laugh Track Remover

Remove or reduce canned laugh tracks from sitcoms using either
classic FFmpeg DSP filters, or state-of-the-art AI Stem Separation.
"""

from __future__ import annotations

import os
import shutil
import sys
import tkinter as tk
from tkinter import messagebox

from core import proc as _proc
from core.hardware import detect_gpu
from core.i18n import t
from core.ops import laugh_track as lt
from core.queue_manager import Hooks, TaskContext
from core.scaling import px
from core.timecode import Timecode
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.kit.tktyped import linked_scrollbar
from tabs.widgets import SaveRow, output_box


def _fmt(seconds: float) -> str:
    return f"{Timecode.of(seconds):.2}"


class LaughTrackRemoverTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.file_path = ""
        self.duration = 0.0
        self.preview_proc = None
        self._ai_cancel_fn = None  # set when AI task is running in the queue

        # Determine paths
        self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.bin_dir = os.path.join(self.base_dir, "bin")
        os.makedirs(self.bin_dir, exist_ok=True)

        # We assume Windows for the standalone exe download in this implementation
        self.ai_binary = os.path.join(self.bin_dir, "audio-separator.exe")

        self._build_ui()
        self._check_ai_tools()

    def _out_ext(self) -> str:
        return ".mp4" if self._export_mode.get() == "video" else ".wav"

    def _build_ui(self) -> None:
        self._build_header()
        self._build_source()
        self._build_engine_selector()
        instr_text, txt_container = self._build_engine_container_frame()
        self._build_ai_model_f(instr_text, txt_container)
        self._build_1_noise_profile()
        self._build_2_center_channel()
        self._build_3_frequency_sculpt()
        self._build_4_smart_gate()
        self._build_output()
        self._build_action_buttons()
        self._build_console()

    def _build_header(self) -> None:
        self.make_header(self, t("tab.laugh_track_remover"), t("laugh_track.subtitle"))

    def _build_source(self) -> None:
        """Source."""
        sf = tk.Frame(self)
        sf.pack(fill="x", padx=px(20), pady=px(10, 4))
        tk.Label(sf, text=t("common.source_file"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._src_var = tk.StringVar()
        tk.Entry(sf, textvariable=self._src_var, width=55, relief="flat", font=(UI_FONT, 10)).pack(
            side="left", padx=px(8)
        )
        tk.Button(
            sf,
            text=t("btn.browse"),
            command=self._browse,
            cursor="hand2",
            relief="flat",
            font=(UI_FONT, 9),
        ).pack(side="left")
        self._info_lbl = tk.Label(sf, text="", fg=CLR.get("fgdim", "#888888"), font=(MONO_FONT, 9))
        self._info_lbl.pack(side="left", padx=px(10))

    def _build_engine_selector(self) -> None:
        """Engine Selector."""
        eng_f = tk.Frame(self)
        eng_f.pack(fill="x", padx=px(20), pady=px(4, 8))
        tk.Label(eng_f, text=t("laugh_track.engine_section"), font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )

        self._engine_var = tk.StringVar(value="ffmpeg")
        tk.Radiobutton(
            eng_f,
            text=t("laugh_track.ffmpeg_option"),
            variable=self._engine_var,
            value="ffmpeg",
            font=(UI_FONT, 10),
            command=self._toggle_engine_ui,
        ).pack(side="left", padx=px(10))
        tk.Radiobutton(
            eng_f,
            text=t("laugh_track.ai_stem_option"),
            variable=self._engine_var,
            value="ai",
            font=(UI_FONT, 10, "bold"),
            fg=CLR.get("accent", "#0078D7"),
            command=self._toggle_engine_ui,
        ).pack(side="left")

    def _build_engine_container_frame(self) -> tuple[str, tk.Frame]:
        """Engine Container Frame."""
        # This prevents Tkinter layout crashes when swapping sections
        self.engine_container = tk.Frame(self)
        self.engine_container.pack(fill="x", pady=0)

        # ==================================================================
        # AI LAUGHTER REMOVAL SECTION
        # ==================================================================
        # 1. Added "Experimental" to the label frame
        self.ai_lf = tk.LabelFrame(
            self.engine_container,
            text=f"  {t('laugh_track.ai_experimental_option')}  ",
            padx=px(15),
            pady=px(10),
            font=(UI_FONT, 9, "bold"),
            fg=CLR.get("accent", "#0078D7"),
        )

        self.ai_status_lbl = tk.Label(
            self.ai_lf, text=t("laugh_track.checking_for_ai_tools"), font=(UI_FONT, 10, "bold")
        )
        self.ai_status_lbl.pack(anchor="w", pady=px(0, 5))

        self.ai_instructions_f = tk.Frame(self.ai_lf)

        # 2. Updated and expanded the instruction text
        # Dynamically detect hardware to provide the correct installation commands
        gpu_type = detect_gpu()

        if gpu_type == "nvidia":
            install_cmds = (
                "1. Install the Nvidia CUDA version of PyTorch (Run in terminal):\n"
                "   pip uninstall torch -y\n"
                "   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121\n\n"
                "2. Install the AI separator tool:\n"
                "   pip install audio-separator[gpu]"
            )
        elif gpu_type == "amd":
            install_cmds = (
                "1. Install the AMD DirectML version of PyTorch (Run in terminal):\n"
                "   pip uninstall torch -y\n"
                "   pip install torch-directml\n\n"
                "2. Install the AI separator tool for AMD:\n"
                "   pip install audio-separator[env]\n"
                "   pip install onnxruntime-directml"
            )
        else:
            install_cmds = (
                "1. Install the standard CPU version of PyTorch (Run in terminal):\n"
                "   pip uninstall torch -y\n"
                "   pip install torch torchvision torchaudio\n\n"
                "2. Install the AI separator tool:\n"
                "   pip install audio-separator[cpu]"
            )

        instr_text = (
            "To use this experimental AI engine, you must install the required packages:\n\n"
            f"{install_cmds}\n\n"
            "3. (Optional) For maximum stability, locate the generated 'audio-separator.exe'\n"
            "   and copy it directly into this application's /bin/ folder."
        )

        # Create a container frame specifically for the text box and its scrollbar
        txt_container = tk.Frame(self.ai_instructions_f, bg=CLR.get("panel", "#222222"))
        return instr_text, txt_container

    def _build_ai_model_f(self, instr_text: str, txt_container: tk.Frame) -> None:
        txt_container.pack(anchor="w", pady=px(5))  # <-- Removed fill="x"

        # Create the text widget
        txt = tk.Text(
            txt_container,
            height=7,
            width=86,
            bg=CLR.get("panel", "#222222"),
            fg=CLR.get("fg", "#FFFFFF"),
            font=(MONO_FONT, 9),
            relief="flat",
        )
        txt.insert("1.0", instr_text)
        txt.config(state="disabled")
        txt.pack(side="left")
        linked_scrollbar(txt_container, txt).pack(side="right", fill="y")

        tk.Button(
            self.ai_instructions_f,
            text=t("laugh_track.i_have_installed_the_tools_check_again"),
            bg=CLR.get("panel", "#222222"),
            fg=CLR.get("fg", "#FFFFFF"),
            font=(UI_FONT, 9),
            cursor="hand2",
            command=self._check_ai_tools,
        ).pack(anchor="w", pady=px(5))

        self.ai_model_f = tk.Frame(self.ai_lf)
        tk.Label(self.ai_model_f, text=t("common.model"), font=(UI_FONT, 10)).pack(
            side="left", pady=px(5)
        )
        self._ai_model = tk.StringVar(value="mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt")
        tk.Entry(self.ai_model_f, textvariable=self._ai_model, width=50, font=(MONO_FONT, 10)).pack(
            side="left", padx=px(8), pady=px(5)
        )
        tk.Label(
            self.ai_model_f,
            text=t("laugh_track.requires_internet_on_first_run"),
            fg=CLR.get("fgdim", "#888888"),
            font=(UI_FONT, 9),
        ).pack(side="left", pady=px(5))

        # ==================================================================
        # FFMPEG TOOLS SECTION
        # ==================================================================
        self.ffmpeg_lf = tk.LabelFrame(
            self.engine_container,
            text=t("laugh_track.ffmpeg_tools_enable_one_or_more_they_stack"),
            padx=px(15),
            pady=px(10),
            font=(UI_FONT, 9, "bold"),
            fg=CLR.get("fgdim", "#888888"),
        )

    def _build_1_noise_profile(self) -> None:
        """1. Noise Profile."""
        # --- 1. Noise Profile ---
        self._use_profile = tk.BooleanVar(value=True)
        np_header = tk.Frame(self.ffmpeg_lf)
        np_header.pack(fill="x", pady=px(0, 2))
        tk.Checkbutton(
            np_header,
            text=t("laugh_track.spectral_section"),
            variable=self._use_profile,
            font=(UI_FONT, 10, "bold"),
            command=self._toggle_sections,
        ).pack(side="left")

        self._profile_f = tk.Frame(self.ffmpeg_lf)
        pr1 = tk.Frame(self._profile_f)
        pr1.pack(fill="x", pady=px(2))
        tk.Label(pr1, text=t("laugh_track.laugh_sample_start_label"), width=18, anchor="e").pack(
            side="left"
        )
        self._sample_start = tk.StringVar(value="0.0")
        tk.Entry(pr1, textvariable=self._sample_start, width=10).pack(side="left", padx=px(6))
        tk.Label(pr1, text=t("laugh_track.laugh_sample_end_label")).pack(side="left", padx=px(8, 0))
        self._sample_end = tk.StringVar(value="3.0")
        tk.Entry(pr1, textvariable=self._sample_end, width=10).pack(side="left", padx=px(6))

        pr2 = tk.Frame(self._profile_f)
        pr2.pack(fill="x", pady=px(4))
        tk.Label(pr2, text=t("laugh_track.reduction_strength_label"), width=18, anchor="e").pack(
            side="left"
        )
        self._nr_strength = tk.DoubleVar(value=20.0)
        tk.Scale(
            pr2, variable=self._nr_strength, from_=6, to=50, resolution=1, orient="horizontal"
        ).pack(side="left", padx=px(6))

        pr3 = tk.Frame(self._profile_f)
        pr3.pack(fill="x", pady=px(2))
        tk.Label(pr3, text=t("laugh_track.noise_floor"), width=18, anchor="e").pack(side="left")
        self._noise_floor = tk.DoubleVar(value=-30.0)
        tk.Scale(
            pr3, variable=self._noise_floor, from_=-60, to=-10, resolution=1, orient="horizontal"
        ).pack(side="left", padx=px(6))

    def _build_2_center_channel(self) -> None:
        """2. Center Channel."""
        # --- 2. Center Channel ---
        self._use_center = tk.BooleanVar(value=False)
        cc_header = tk.Frame(self.ffmpeg_lf)
        cc_header.pack(fill="x", pady=px(0, 2))
        tk.Checkbutton(
            cc_header,
            text=t("laugh_track.center_channel_section"),
            variable=self._use_center,
            font=(UI_FONT, 10, "bold"),
            command=self._toggle_sections,
        ).pack(side="left")

        self._center_f = tk.Frame(self.ffmpeg_lf)
        cr1 = tk.Frame(self._center_f)
        cr1.pack(fill="x", pady=px(2))
        tk.Label(cr1, text=t("laugh_track.center_strength_label"), width=18, anchor="e").pack(
            side="left"
        )
        self._center_mix = tk.DoubleVar(value=0.8)
        tk.Scale(
            cr1, variable=self._center_mix, from_=0.5, to=1.0, resolution=0.05, orient="horizontal"
        ).pack(side="left", padx=px(6))

        self._stereo_cancel = tk.BooleanVar(value=False)
        tk.Checkbutton(
            self._center_f, text=t("laugh_track.aggressive_checkbox"), variable=self._stereo_cancel
        ).pack(anchor="w", padx=px(120))

    def _build_3_frequency_sculpt(self) -> None:
        """3. Frequency Sculpt."""
        # --- 3. Frequency Sculpt ---
        self._use_eq = tk.BooleanVar(value=False)
        eq_header = tk.Frame(self.ffmpeg_lf)
        eq_header.pack(fill="x", pady=px(0, 2))
        tk.Checkbutton(
            eq_header,
            text=t("laugh_track.frequency_section"),
            variable=self._use_eq,
            font=(UI_FONT, 10, "bold"),
            command=self._toggle_sections,
        ).pack(side="left")

        self._eq_f = tk.Frame(self.ffmpeg_lf)
        er1 = tk.Frame(self._eq_f)
        er1.pack(fill="x", pady=px(2))
        tk.Label(er1, text=t("laugh_track.low_cut_label"), width=18, anchor="e").pack(side="left")
        self._eq_low = tk.StringVar(value="300")
        tk.Entry(er1, textvariable=self._eq_low, width=6).pack(side="left", padx=px(4))
        tk.Label(er1, text=t("laugh_track.high_cut_label")).pack(side="left", padx=px(12, 0))
        self._eq_high = tk.StringVar(value="5000")
        tk.Entry(er1, textvariable=self._eq_high, width=6).pack(side="left", padx=px(4))

        er2 = tk.Frame(self._eq_f)
        er2.pack(fill="x", pady=px(2))
        tk.Label(er2, text=t("laugh_track.cut_depth_label"), width=18, anchor="e").pack(side="left")
        self._eq_depth = tk.DoubleVar(value=8.0)
        tk.Scale(
            er2, variable=self._eq_depth, from_=2, to=24, resolution=1, orient="horizontal"
        ).pack(side="left", padx=px(6))

    def _build_4_smart_gate(self) -> None:
        """4. Smart Gate."""
        # --- 4. Smart Gate ---
        self._use_gate = tk.BooleanVar(value=False)
        gt_header = tk.Frame(self.ffmpeg_lf)
        gt_header.pack(fill="x", pady=px(0, 2))
        tk.Checkbutton(
            gt_header,
            text=t("laugh_track.smart_gate_section"),
            variable=self._use_gate,
            font=(UI_FONT, 10, "bold"),
            command=self._toggle_sections,
        ).pack(side="left")

        self._gate_f = tk.Frame(self.ffmpeg_lf)
        gr1 = tk.Frame(self._gate_f)
        gr1.pack(fill="x", pady=px(2))
        tk.Label(gr1, text=t("laugh_track.gate_threshold_label"), width=18, anchor="e").pack(
            side="left"
        )
        self._gate_thresh = tk.DoubleVar(value=-18.0)
        tk.Scale(
            gr1, variable=self._gate_thresh, from_=-40, to=-6, resolution=1, orient="horizontal"
        ).pack(side="left", padx=px(6))

        gr2 = tk.Frame(self._gate_f)
        gr2.pack(fill="x", pady=px(2))
        tk.Label(gr2, text=t("laugh_track.compression_ratio_label"), width=18, anchor="e").pack(
            side="left"
        )
        self._gate_ratio = tk.DoubleVar(value=4.0)
        tk.Scale(
            gr2, variable=self._gate_ratio, from_=2, to=20, resolution=0.5, orient="horizontal"
        ).pack(side="left", padx=px(6))

    def _build_output(self) -> None:
        """Output."""
        out_lf = output_box(self)

        self._out_var = tk.StringVar()
        SaveRow(
            out_lf, t("common.output_file"), self._out_var, kind="media", ext=self._out_ext
        ).pack(fill="x", pady=px(2))

        of2 = tk.Frame(out_lf)
        of2.pack(fill="x", pady=px(4))
        self._export_mode = tk.StringVar(value="video")
        tk.Radiobutton(
            of2, text=t("laugh_track.video_option"), variable=self._export_mode, value="video"
        ).pack(side="left", padx=px(0, 20))
        tk.Radiobutton(
            of2, text=t("laugh_track.audio_option"), variable=self._export_mode, value="audio"
        ).pack(side="left")

    def _build_action_buttons(self) -> None:
        """Action buttons."""
        bf = tk.Frame(self)
        bf.pack(pady=px(10))
        self._btn_preview = tk.Button(
            bf,
            text=t("laugh_track.preview_button"),
            bg=CLR.get("panel", "#222222"),
            fg=CLR.get("fg", "#FFFFFF"),
            width=18,
            cursor="hand2",
            command=self._preview_result,
        )
        self._btn_preview.pack(side="left", padx=px(8))

        self.btn_render = self.make_render_btn(
            bf, t("laugh_track.remove_button"), self._render, width=28
        )
        self.btn_render.pack(side="left", padx=px(8))

    def _build_console(self) -> None:
        """Console."""
        self.console = self.make_console_block(self, height=6, padx=px(20), pady=px(4, 10))

        # ==================================================================
        # Initialize UI layout states AFTER all widgets are fully created!
        # ==================================================================
        self._toggle_sections()
        self._toggle_engine_ui()

    # ── UI Toggles ────────────────────────────────────────────────────
    def _toggle_engine_ui(self) -> None:
        """Show/hide sections safely inside the container."""
        if self._engine_var.get() == "ai":
            self.ffmpeg_lf.pack_forget()
            self.ai_lf.pack(fill="x", padx=px(20), pady=px(4))
            self._btn_preview.config(state="disabled")
        else:
            self.ai_lf.pack_forget()
            self.ffmpeg_lf.pack(fill="x", padx=px(20), pady=px(4))
            self._btn_preview.config(state="normal")

    def _toggle_sections(self) -> None:
        """Unpack all and repack in order to maintain layout integrity."""
        for f in [self._profile_f, self._center_f, self._eq_f, self._gate_f]:
            f.pack_forget()

        for var, frame in [
            (self._use_profile, self._profile_f),
            (self._use_center, self._center_f),
            (self._use_eq, self._eq_f),
            (self._use_gate, self._gate_f),
        ]:
            if var.get():
                frame.pack(fill="x", padx=px(20), pady=px(2))

    # ── AI Tools Management ───────────────────────────────────────────
    def _find_ai_cli(self) -> str | None:
        """Prioritizes the bundled /bin/ exe, then falls back to hunting system paths."""
        import glob

        # --- DEV OVERRIDE ---
        if os.environ.get("CROSSFADER_NO_AI_TEST") == "1":
            return None
        # --------------------

        # 1. THE BUNDLED /bin/ EXE (Priority #1)
        if hasattr(self, "ai_binary") and os.path.exists(self.ai_binary):
            return self.ai_binary

        # 2. Standard PATH check
        if shutil.which("audio-separator"):
            return shutil.which("audio-separator")

        # 3. Prepare search directories (Fallback)
        search_dirs = [
            os.path.join(os.path.dirname(sys.executable), "Scripts"),
            os.path.join(os.path.dirname(sys.executable), "bin"),
        ]

        # 4. Aggressive AppData hunting (Fallback for dev environments)
        if os.name == "nt":
            localappdata = os.environ.get("LOCALAPPDATA", "")
            appdata = os.environ.get("APPDATA", "")

            if localappdata:
                search_dirs.extend(
                    glob.glob(
                        os.path.join(
                            localappdata,
                            "Packages",
                            "PythonSoftware*",
                            "LocalCache",
                            "local-packages",
                            "Python3*",
                            "Scripts",
                        )
                    )
                )
            if appdata:
                search_dirs.extend(
                    glob.glob(os.path.join(appdata, "Python", "Python3*", "Scripts"))
                )

        # 5. Check all discovered fallback paths
        exe_name = "audio-separator.exe" if os.name == "nt" else "audio-separator"
        for d in search_dirs:
            candidate = os.path.join(d, exe_name)
            if os.path.exists(candidate):
                return candidate

        return None

    def _check_ai_tools(self) -> None:
        """Checks for the CLI wrapper using the wildcard hunter."""
        if self._find_ai_cli():
            self.ai_status_lbl.config(
                text=t("laugh_track.ai_tools_detected_ready"), fg=CLR.get("green", "#4CAF50")
            )
            self.ai_instructions_f.pack_forget()
            self.ai_model_f.pack(fill="x", pady=px(5))
        else:
            self.ai_status_lbl.config(text=t("laugh_track.ai_tools_missing"), fg="red")
            self.ai_model_f.pack_forget()
            self.ai_instructions_f.pack(fill="x", pady=px(5))

    # ── File browsing ─────────────────────────────────────────────────
    def _browse(self) -> None:
        p = dialogs.askopenfilename(
            filetypes=[("Media", "*.mp4 *.mkv *.mp3 *.wav *.aac"), ("All", t("ducker.item_2"))]
        )
        if p:
            self._load(p)

    def on_source_loaded(self, path: str) -> None:
        """Drag and drop / File > Open land here (the shared loader set the path)."""
        self._load(path)

    def _load(self, p: str) -> None:
        from core import ffmpeg as ff

        info = ff.try_probe(p)
        self.file_path = p
        if self._src_var.get() != p:
            self._src_var.set(p)
        self.duration = info.duration if info else 0.0
        self._channels = info.require_audio().channels if info is not None and info.audio else 2
        layout = info.require_audio().channel_layout if info is not None and info.audio else ""
        self._info_lbl.config(
            text=f"{_fmt(self.duration)}   {layout or str(self._channels) + ' ch'}"
        )

    # ── Render Logic ──────────────────────────────────────────────────
    def _cleanup(self) -> lt.Cleanup:
        return lt.Cleanup(
            profile=self._use_profile.get(),
            reduction=self._nr_strength.get(),
            noise_floor=self._noise_floor.get(),
            centre=self._use_center.get(),
            centre_mix=float(self._center_mix.get()),
            channels=getattr(self, "_channels", 2),
            eq=self._use_eq.get(),
            eq_depth=self._eq_depth.get(),
            gate=self._use_gate.get(),
            gate_threshold=self._gate_thresh.get(),
            gate_ratio=self._gate_ratio.get(),
        )

    def _build_af(self) -> str | None:
        """The combined audio filter for the FFmpeg engine (None: nothing ticked)."""
        return lt.filter_chain(self._cleanup())

    def _preview_result(self) -> None:
        if not self.file_path or self._engine_var.get() == "ai":
            return
        af = self._build_af()
        if not af:
            return
        cmd = lt.preview_cmd(self.file_path, af, t("laugh_track.ffmpeg_preview"))
        self.preview_proc = _proc.spawn(cmd)

    def _render(self) -> None:
        typed = self._src_var.get().strip()
        if typed and os.path.isfile(typed) and typed != self.file_path:
            self._load(typed)
        if not self.file_path or not os.path.isfile(self.file_path):
            messagebox.showwarning(t("waveform.no_file_title"), t("common.no_input"), parent=self)
            return
        ext = ".mp4" if self._export_mode.get() == "video" else ".wav"
        out = self._out_var.get().strip() or self.suggest_output(self.file_path, "no_laughs", ext)
        out = self.confirm_output(out, [self.file_path], default_ext=ext)
        if not out:
            return
        self._out_var.set(out)

        self.btn_render.config(state="disabled")

        if self._engine_var.get() == "ffmpeg":
            self._render_ffmpeg(out)
        else:
            _out = out

            def _ai_worker_fn(progress_cb: object, cancel_fn: object, ctx: TaskContext) -> int:
                return self._render_ai(_out, ctx)

            def _done(_tid: str, rc: int) -> None:
                self.btn_render.config(state="normal")
                self.last_task = self.task(_tid)
                self.show_result(rc, _out)

            self.enqueue_render(
                "Laugh Track (AI)",
                output_path=_out,
                work=_ai_worker_fn,
                inputs=[self.file_path],
                hooks=Hooks(
                    on_progress=lambda _t, line: self.log(self.console, line), on_complete=_done
                ),
            )

    def _render_ffmpeg(self, out: str) -> None:
        af = self._build_af()
        if not af:
            self.btn_render.config(state="normal")
            return

        cmd = lt.render_cmd(self.file_path, af, out, video=self._export_mode.get() == "video")
        self.log(self.console, f"Running FFmpeg engine with filters: {af}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=self.duration or None,
            output=out,
            inputs=[self.file_path],
            name="Laugh Track",
        )

    def _render_ai(self, out: str, ctx: TaskContext) -> int:
        """AI stem separation (audio-separator) inside a private job folder."""
        ai_cli = self._find_ai_cli()
        if not ai_cli:
            raise RuntimeError(
                "The audio-separator program was not found - see the install notes above."
            )
        job = lt.AiSeparation(
            src=self.file_path,
            cli=ai_cli,
            model=self._ai_model.get().strip(),
            video=self._export_mode.get() == "video",
            duration=self.duration or None,
        )
        return job.run(ctx, out)
