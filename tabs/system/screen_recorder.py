"""
tab_screenrecorder.py  -  Screen Recorder
Capture your desktop. Use Simple mode for instant captures
(Full Screen, Area, or Window), or Advanced mode for precise control.

Advanced mode features:
  • Global hotkey (Alt+F9) to start/stop recording without touching the app
  • Custom output folder + filename template ({date}, {time}, {counter} tokens)
  • Auto-stop after configurable duration (adds -t to FFmpeg)
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes
import os
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from enum import StrEnum
from tkinter import messagebox, ttk
from typing import ClassVar

from core import tasks
from core.encoders import VideoEncode
from core.i18n import t
from core.recorder import Recording, Region
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit.tktyped import current_tab, overlay
from tabs.kit.widgets import ChoiceBox
from tabs.widgets import FolderRow

# ─────────────────────────────────────────────────────────────────────────────
#  Region selector overlay
# ─────────────────────────────────────────────────────────────────────────────


#: the audio-device lists' entries that aren't devices (the rest are the devices' names)
NO_AUDIO = "None"
LOADING_DEVICES = "Loading devices..."


class RecordCodec(StrEnum):
    AUTO = "auto"
    X264 = "x264"
    X265 = "x265"
    NVENC_H264 = "nvenc_h264"
    NVENC_HEVC = "nvenc_hevc"
    AMF_H264 = "amf_h264"
    SVTAV1 = "svtav1"


class Chroma(StrEnum):
    STANDARD = "yuv420p"
    FULL = "yuv444p"  # every pixel's own colour: sharper text, bigger files


#: codec choice -> (what the drop-down shows, codec family, hardware)
RECORD_CODECS = {
    RecordCodec.AUTO: ("Auto (GPU when available)", "h264", "auto"),
    RecordCodec.X264: ("libx264 (CPU - High Compatibility)", "h264", "off"),
    RecordCodec.X265: ("libx265 (CPU - High Efficiency)", "hevc", "off"),
    RecordCodec.NVENC_H264: ("h264_nvenc (NVIDIA GPU)", "h264", "nvenc"),
    RecordCodec.NVENC_HEVC: ("hevc_nvenc (NVIDIA GPU)", "hevc", "nvenc"),
    RecordCodec.AMF_H264: ("h264_amf (AMD GPU)", "h264", "amf"),
    RecordCodec.SVTAV1: ("libsvtav1 (AV1 - Next Gen)", "av1", "off"),
}


class AreaSelector(tk.Toplevel):
    """Transparent overlay for clicking and dragging to select a screen region."""

    def __init__(self, master: tk.Misc, callback: Callable[[int, int, int, int], object]) -> None:
        super().__init__(master)
        overlay(self, 0.3)
        self.config(bg="black", cursor="cross")
        self.overrideredirect(True)

        self.callback = callback
        self.start_x = self.start_y = 0
        self.rect = 0  # the rectangle's canvas id, while dragging

        self.canvas = tk.Canvas(self, cursor="cross", bg="grey", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", lambda e: self.destroy())

    def on_press(self, event: tk.Event[tk.Misc]) -> None:
        self.start_x, self.start_y = event.x, event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, 1, 1, outline="red", width=px(3)
        )

    def on_drag(self, event: tk.Event[tk.Misc]) -> None:
        self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event: tk.Event[tk.Misc]) -> None:
        x1, y1 = self.start_x, self.start_y
        x2, y2 = event.x, event.y
        rx, ry = min(x1, x2), min(y1, y2)
        rw, rh = abs(x1 - x2), abs(y1 - y2)
        if rw % 2 != 0:
            rw -= 1
        if rh % 2 != 0:
            rh -= 1
        self.callback(rx, ry, rw, rh)
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
#  Window click-to-pick overlay
# ─────────────────────────────────────────────────────────────────────────────
class WindowPicker(tk.Toplevel):
    """
    Near-invisible fullscreen overlay.  Click on any window to capture its
    title.  After the overlay closes we query WindowFromPoint so the correct
    window (not the overlay) is identified.
    """

    def __init__(self, master: tk.Misc, callback: Callable[[str, Region | None], object]) -> None:
        super().__init__(master)
        self._master = master
        self.callback = callback

        overlay(self, 0.05)
        self.config(bg="#000000", cursor="hand2")
        self.overrideredirect(True)

        # Instruction banner centred on screen
        banner = tk.Frame(self, bg="#1a1a2e", padx=px(30), pady=px(20))
        banner.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            banner,
            text=t("screen_recorder.click_on_the_window_you_want_to_record"),
            font=(UI_FONT, 18, "bold"),
            bg="#1a1a2e",
            fg="white",
        ).pack()
        tk.Label(
            banner,
            text=t("screen_recorder.press_esc_to_cancel"),
            font=(UI_FONT, 10),
            bg="#1a1a2e",
            fg="#aaaaaa",
        ).pack(pady=px(6, 0))

        self.bind("<ButtonPress-1>", self._on_click)
        self.bind("<Escape>", lambda e: self.destroy())

    def _on_click(self, event: tk.Event[tk.Misc]) -> None:
        sx, sy = event.x_root, event.y_root
        # Destroy FIRST so the overlay is gone before WindowFromPoint runs
        self.destroy()
        # Give Windows a couple frames to remove the overlay from z-order
        self._master.after(120, lambda: self._resolve_window(sx, sy))

    def _resolve_window(self, sx: int, sy: int) -> None:
        if sys.platform != "win32":
            return

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        GA_ROOT = 2
        pt = POINT(sx, sy)
        hwnd = ctypes.windll.user32.WindowFromPoint(pt)
        hwnd = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)

        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value.strip()

        # Capture the window's screen rect so we can use desktop-region capture
        # (immune to title changes that happen when the user navigates tabs).
        rect = RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        x = rect.left
        y = rect.top
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        win_rect = (x, y, w, h) if (w > 0 and h > 0) else None

        if title or win_rect:
            self.callback(title, win_rect)
        else:
            messagebox.showwarning(
                t("common.warning"),
                t("screen_recorder.couldn_t_read_a_title_from_that"),
            )


# ─────────────────────────────────────────────────────────────────────────────
#  Main tab
# ─────────────────────────────────────────────────────────────────────────────
class ScreenRecorderTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.record_proc: Recording | None = None
        self.output_path = ""
        self.selected_region = None  # (x, y, w, h) - screen area
        self._window_rect: Region | None = None  # picked window bounds
        self._recording = False
        self._rec_counter = 0  # increments each recording for {counter} token
        self._hotkey_active = False

        # ── Advanced recording variables ──────────────────────────────────────
        self.fps_var = tk.StringVar(value="60")
        self.preset_var = tk.StringVar(value="superfast")
        self.crf_var = tk.IntVar(value=18)

        # Dynamic Audio Sources
        self.audio_src1_var = tk.StringVar(value=NO_AUDIO)
        self.audio_src2_var = tk.StringVar(value=NO_AUDIO)
        self.acodec_var = tk.StringVar(value="aac")

        self.container_var = tk.StringVar(value=".mp4")
        self.cursor_var = tk.BooleanVar(value=True)

        # Window-title capture (Advanced mode)
        self.window_title_var = tk.StringVar(value="")  # empty = full screen / region
        self.capture_mode_var = tk.StringVar(value="desktop")  # "desktop" | "window"

        # OBS feature 1 – global hotkey
        self.hotkey_var = tk.StringVar(value="Alt+F9")
        self.hotkey_enabled_var = tk.BooleanVar(value=False)

        # OBS feature 2 – output path / filename template
        _desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        self.out_folder_var = tk.StringVar(value=_desktop)
        self.out_template_var = tk.StringVar(value="StudioCapture_{date}_{time}")

        # OBS feature 3 – auto-stop timer
        self.autostop_var = tk.BooleanVar(value=False)
        self.autostop_hh_var = tk.StringVar(value="00")
        self.autostop_mm_var = tk.StringVar(value="05")
        self.autostop_ss_var = tk.StringVar(value="00")

        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.make_header(self, t("tab.screen_recorder"), t("screen_recorder.subtitle"), icon="🖥️")

        nb_frame = tk.Frame(self, bg=CLR["bg"])
        nb_frame.pack(fill="x", padx=px(20), pady=px(10))

        self.notebook = ttk.Notebook(nb_frame)
        self.notebook.pack(fill="x", expand=True)

        self.tab_express = tk.Frame(self.notebook, bg=CLR["bg"], padx=px(20), pady=px(30))
        self.notebook.add(self.tab_express, text=t("screen_recorder.simple"))
        self._build_express_tab()

        self.tab_studio = tk.Frame(self.notebook, bg=CLR["bg"], padx=px(20), pady=px(15))
        self.notebook.add(self.tab_studio, text=t("screen_recorder.advanced"))
        self._build_studio_tab()

        # Universal stop button
        ctl_f = tk.Frame(self, bg=CLR["bg"])
        ctl_f.pack(pady=px(10, 5))
        self.btn_stop = tk.Button(
            ctl_f,
            text=t("screen_recorder.stop_recording"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            state="disabled",
            width=25,
            command=self._stop,
        )
        self.btn_stop.pack()

        self.console = self.make_console_block(
            self, height=8, bg=CLR["bg"], padx=px(20), pady=px(5, 20)
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Express Tab
    # ─────────────────────────────────────────────────────────────────────────
    def _build_express_tab(self) -> None:
        tk.Label(
            self.tab_express,
            text=t("screen_recorder.instant_screen_capture_with_sensible_defaults"),
            font=(UI_FONT, 14, "italic"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(pady=px(0, 20))

        self.btn_start_express = tk.Button(
            self.tab_express,
            text=t("screen_recorder.quick_record_full_screen"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["red"],
            fg="white",
            height=2,
            width=40,
            cursor="hand2",
            command=self._start_express,
        )
        self.btn_start_express.pack(pady=px(5))

        self.btn_start_express_area = tk.Button(
            self.tab_express,
            text=t("screen_recorder.record_a_specific_area"),
            font=(UI_FONT, 10, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
            height=2,
            width=45,
            cursor="hand2",
            command=self._start_express_area,
        )
        self.btn_start_express_area.pack(pady=px(5))

        self.btn_start_express_window = tk.Button(
            self.tab_express,
            text=t("screen_recorder.record_a_specific_window"),
            font=(UI_FONT, 10, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
            height=2,
            width=45,
            cursor="hand2",
            command=self._start_express_window,
        )
        self.btn_start_express_window.pack(pady=px(5))

        tk.Label(
            self.tab_express,
            text=t("screen_recorder.30fps_x264_ultrafast_output_saved_to_desktop"),
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(pady=px(15, 0))

    # ─────────────────────────────────────────────────────────────────────────
    #  Pro Studio Tab
    # ─────────────────────────────────────────────────────────────────────────
    def _build_studio_tab(self) -> None:
        self._build_capture_source()
        self._build_video_settings()
        self._build_audio_devices()
        self._build_hotkey()
        self._build_output_path()
        self._build_auto_stop()
        self._build_start_button()

    def _build_capture_source(self) -> None:
        # ── Capture source ────────────────────────────────────────────────────
        src_f = tk.LabelFrame(
            self.tab_studio,
            text=f" {t('screen_recorder.capture_source_section')} ",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
            padx=px(10),
            pady=px(8),
        )
        src_f.pack(fill="x", pady=px(0, 8))

        # Row 0: desktop vs window radio
        mode_row = tk.Frame(src_f, bg=CLR["bg"])
        mode_row.pack(fill="x")
        tk.Radiobutton(
            mode_row,
            text=t("screen_recorder.full_screen_region"),
            variable=self.capture_mode_var,
            value="desktop",
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            command=self._on_mode_change,
        ).pack(side="left")
        tk.Radiobutton(
            mode_row,
            text=t("screen_recorder.specific_window"),
            variable=self.capture_mode_var,
            value="window",
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            command=self._on_mode_change,
        ).pack(side="left", padx=px(20, 0))
        self._build_region_row(src_f)
        self._build_window_row(src_f)
        self._on_mode_change()  # set initial visibility

    def _build_region_row(self, src_f: tk.LabelFrame) -> None:
        """Desktop mode: the region to record."""
        # Row 1: Region selector (visible in desktop mode)
        self.region_row = tk.Frame(src_f, bg=CLR["bg"])
        self.region_row.pack(fill="x", pady=px(6, 0))
        tk.Button(
            self.region_row,
            text=t("screen_recorder.select_area"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
            cursor="hand2",
            command=self._pick_area,
        ).pack(side="left")
        self.lbl_region = tk.Label(
            self.region_row,
            text=t("screen_recorder.current_full_screen"),
            font=(MONO_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        )
        self.lbl_region.pack(side="left", padx=px(8))
        tk.Button(
            self.region_row,
            text=t("snapshot.reset"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            relief="flat",
            cursor="hand2",
            command=self._reset_area,
        ).pack(side="left")

    def _build_window_row(self, src_f: tk.LabelFrame) -> None:
        """Window mode: the window to record."""
        # Row 2: Window picker (visible in window mode)
        self.window_row = tk.Frame(src_f, bg=CLR["bg"])
        self.window_row.pack(fill="x", pady=px(6, 0))
        tk.Button(
            self.window_row,
            text=t("screen_recorder.click_a_window"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
            cursor="hand2",
            command=self._pick_window_studio,
        ).pack(side="left")
        tk.Label(
            self.window_row,
            text=t("screen_recorder.or_type_title"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
        ).pack(side="left")
        self._win_entry = tk.Entry(
            self.window_row,
            textvariable=self.window_title_var,
            width=30,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        )
        self._win_entry.pack(side="left", padx=px(4))
        self.lbl_window = tk.Label(
            self.window_row, text="", font=(MONO_FONT, 8), bg=CLR["bg"], fg=CLR["green"]
        )
        self.lbl_window.pack(side="left", padx=px(4))

    def _build_video_settings(self) -> None:
        """Video Settings."""
        tk.Frame(self.tab_studio, bg=CLR["border"], height=px(1)).pack(fill="x", pady=px(6))
        vid_f = tk.LabelFrame(
            self.tab_studio,
            text=f" {t('screen_recorder.video_section')} ",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
            padx=px(10),
            pady=px(8),
        )
        vid_f.pack(fill="x", pady=px(0, 6))

        self.vcodec = ChoiceBox(
            vid_f, {c: spec[0] for c, spec in RECORD_CODECS.items()}, RecordCodec.AUTO, width=32
        )

        self.form_row(
            vid_f,
            0,
            t("common.codec"),
            self.vcodec,
            tk.Label(
                vid_f,
                text=t("screen_recorder.framerate"),
                bg=CLR["bg"],
                fg=CLR["fg"],
                width=10,
                anchor="e",
            ),
            ttk.Combobox(
                vid_f, textvariable=self.fps_var, width=8, values=["24", "30", "60", "120"]
            ),
            label_width=12,
            pady=4,
        )
        self._build_quality_row(vid_f)
        self._build_colour_options(vid_f)

    def _build_quality_row(self, vid_f: tk.LabelFrame) -> None:
        """Quality: the CRF slider and its value."""
        crf_row = tk.Frame(vid_f, bg=CLR["bg"])
        self.form_row(vid_f, 1, t("codec.lbl_crf_quality"), crf_row, label_width=12, pady=4)
        tk.Scale(
            crf_row,
            variable=self.crf_var,
            from_=0,
            to=51,
            orient="horizontal",
            length=px(150),
            bg=CLR["bg"],
            fg=CLR["fg"],
            highlightthickness=0,
        ).pack(side="left")
        tk.Label(
            crf_row,
            text=t("screen_recorder.lower_better_18_lossless"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left", padx=px(5))

    def _build_colour_options(self, vid_f: tk.LabelFrame) -> None:
        """Chroma, pixel format and encoder preset."""
        self.chroma = ChoiceBox(
            vid_f,
            {
                Chroma.STANDARD: "yuv420p (Standard)",
                Chroma.FULL: "yuv444p (Full Color, large file)",
            },
            Chroma.STANDARD,
            width=32,
        )

        self.form_row(
            vid_f,
            2,
            t("codec.lbl_pixel_format"),
            self.chroma,
            tk.Label(
                vid_f,
                text=t("screen_recorder.encoder_preset"),
                bg=CLR["bg"],
                fg=CLR["fg"],
                width=10,
                anchor="e",
            ),
            ttk.Combobox(
                vid_f,
                textvariable=self.preset_var,
                width=12,
                state="readonly",
                values=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"],
            ),
            label_width=12,
            pady=4,
        )

    def _build_audio_devices(self) -> None:
        """Which microphones and system sound to record."""
        # ── Audio Devices (Dynamic) ────────────────────────────────────────────
        aud_f = tk.LabelFrame(
            self.tab_studio,
            text=f" {t('screen_recorder.audio_sources_section')} ",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
            padx=px(10),
            pady=px(8),
        )
        aud_f.pack(fill="x", pady=px(0, 6))

        self.cb_audio1 = ttk.Combobox(
            aud_f, textvariable=self.audio_src1_var, width=28, state="readonly"
        )

        self.cb_audio2 = ttk.Combobox(
            aud_f, textvariable=self.audio_src2_var, width=28, state="readonly"
        )
        self.form_row(
            aud_f,
            0,
            t("screen_recorder.source_1"),
            self.cb_audio1,
            tk.Label(aud_f, text=t("screen_recorder.source_2"), bg=CLR["bg"], fg=CLR["fg"]),
            self.cb_audio2,
            pady=4,
        )

        self.form_row(
            aud_f,
            1,
            t("codec.lbl_audio"),
            ttk.Combobox(
                aud_f,
                textvariable=self.acodec_var,
                width=10,
                state="readonly",
                values=["aac", "flac", "libopus", "pcm_s16le"],
            ),
            tk.Checkbutton(
                aud_f,
                text=t("screen_recorder.capture_mouse_cursor"),
                variable=self.cursor_var,
                bg=CLR["bg"],
                fg=CLR["fg"],
                selectcolor=CLR["panel"],
            ),
            pady=8,
        )

        self.form_row(
            aud_f,
            2,
            t("screen_recorder.container"),
            ttk.Combobox(
                aud_f,
                textvariable=self.container_var,
                width=10,
                state="readonly",
                values=[".mp4", ".mkv", ".mov"],
            ),
        )

        # Start the background scan for audio hardware
        self.cb_audio1.set(LOADING_DEVICES)
        tasks.submit(self._load_audio_devices)

    def _build_hotkey(self) -> None:
        """A global hotkey to start and stop."""

        hotkey_f = tk.LabelFrame(
            self.tab_studio,
            text=f" {t('screen_recorder.hotkey_section')} ",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
            padx=px(10),
            pady=px(8),
        )
        hotkey_f.pack(fill="x", pady=px(0, 6))

        tk.Checkbutton(
            hotkey_f,
            text=t("screen_recorder.enable_global_start_stop_hotkey"),
            variable=self.hotkey_enabled_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
            command=self._on_hotkey_toggle,
        ).pack(side="left")
        tk.Label(hotkey_f, text=t("screen_recorder.hotkey"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left", padx=px(20, 4)
        )
        ttk.Combobox(
            hotkey_f,
            textvariable=self.hotkey_var,
            width=10,
            state="readonly",
            values=["Alt+F9", "Alt+F10", "Ctrl+Alt+R"],
        ).pack(side="left")
        self.lbl_hotkey_status = tk.Label(
            hotkey_f,
            text=t("screen_recorder.inactive"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        )
        self.lbl_hotkey_status.pack(side="left", padx=px(8))

    def _build_output_path(self) -> None:
        """Where recordings are saved."""

        path_f = tk.LabelFrame(
            self.tab_studio,
            text=f" {t('screen_recorder.output_path_section')} ",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
            padx=px(10),
            pady=px(8),
        )
        path_f.pack(fill="x", pady=px(0, 6))

        FolderRow(path_f, t("screen_recorder.folder"), self.out_folder_var).pack(fill="x")

        r1 = tk.Frame(path_f, bg=CLR["bg"])
        r1.pack(fill="x", pady=px(6, 0))
        tk.Label(
            r1, text=t("screen_recorder.filename"), bg=CLR["bg"], fg=CLR["fg"], width=10, anchor="e"
        ).pack(side="left")
        tk.Entry(
            r1,
            textvariable=self.out_template_var,
            width=38,
            bg=CLR["panel"],
            fg=CLR["fg"],
            insertbackground=CLR["fg"],
        ).pack(side="left", padx=px(4))
        tk.Label(
            r1,
            text=t("screen_recorder.tokens_date_time_counter"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left", padx=px(4))

    def _build_auto_stop(self) -> None:
        """Stop by itself after a set time."""

        timer_f = tk.LabelFrame(
            self.tab_studio,
            text=f" {t('screen_recorder.auto_stop_section')} ",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
            padx=px(10),
            pady=px(8),
        )
        timer_f.pack(fill="x", pady=px(0, 8))

        tk.Checkbutton(
            timer_f,
            text=t("screen_recorder.auto_stop_recording_after"),
            variable=self.autostop_var,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
        ).pack(side="left")

        def _hms_entry(var: tk.StringVar, label: str) -> tk.Entry:
            tk.Label(timer_f, text=label, bg=CLR["bg"], fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(
                side="left", padx=px(8, 2)
            )
            e = tk.Entry(
                timer_f,
                textvariable=var,
                width=3,
                bg=CLR["panel"],
                fg=CLR["fg"],
                insertbackground=CLR["fg"],
                justify="center",
            )
            e.pack(side="left")
            return e

        _hms_entry(self.autostop_hh_var, "HH:")
        _hms_entry(self.autostop_mm_var, "MM:")
        _hms_entry(self.autostop_ss_var, "SS")
        tk.Label(
            timer_f,
            text=t("screen_recorder.00_05_00_5_minutes"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left", padx=px(6))

    def _build_start_button(self) -> None:
        """Start button."""
        self.btn_start_pro = tk.Button(
            self.tab_studio,
            text=t("screen_recorder.start_studio_recording"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["red"],
            fg="white",
            cursor="hand2",
            command=self._start_pro,
        )
        self.btn_start_pro.pack(pady=px(10, 0))

    # ─────────────────────────────────────────────────────────────────────────
    #  Audio Hardware Scanning
    # ─────────────────────────────────────────────────────────────────────────
    def _load_audio_devices(self) -> None:
        """Worker thread: find audio sources (incl. the computer's own sound)."""
        from core import recorder

        sources = recorder.audio_sources()
        self.after(0, lambda: self._update_audio_dropdowns(sources))

    def _update_audio_dropdowns(self, sources: list[tuple[str, str]]) -> None:
        from core import recorder

        self._audio_ids = {label: sid for sid, label in sources}
        labels = [NO_AUDIO] + [label for _sid, label in sources]
        try:
            self.cb_audio1.config(values=labels)
            self.cb_audio2.config(values=labels)
            system = recorder.system_sound_source(sources)
            self.audio_src1_var.set(
                next((label for sid, label in sources if sid == system), NO_AUDIO)
            )
            self.audio_src2_var.set(NO_AUDIO)
        except tk.TclError:
            pass

    def _audio_choice(self, var: tk.StringVar) -> str:
        label = var.get()
        if not label or label in (NO_AUDIO, LOADING_DEVICES):
            return ""
        return getattr(self, "_audio_ids", {}).get(label, label)

    # ─────────────────────────────────────────────────────────────────────────
    #  Mode toggle (desktop vs window)
    # ─────────────────────────────────────────────────────────────────────────
    def _on_mode_change(self) -> None:
        if self.capture_mode_var.get() == "desktop":
            self.region_row.pack(fill="x", pady=px(6, 0))
            self.window_row.pack_forget()
        else:
            self.region_row.pack_forget()
            self.window_row.pack(fill="x", pady=px(6, 0))

    # ─────────────────────────────────────────────────────────────────────────
    #  Region selection
    # ─────────────────────────────────────────────────────────────────────────
    def _pick_area(self) -> None:
        AreaSelector(self, self._set_region)

    def _set_region(self, x: int, y: int, w: int, h: int) -> None:
        self.selected_region = (x, y, w, h)
        self.lbl_region.config(
            text=t("screen_recorder.w_h_at_x_y", w=w, h=h, x=x, y=y), fg=CLR["green"]
        )
        self.log(
            self.console, t("screen_recorder.target_region_w_h_at_offset_x", w=w, h=h, x=x, y=y)
        )

    def _reset_area(self) -> None:
        self.selected_region = None
        self.lbl_region.config(text=t("screen_recorder.current_full_screen"), fg=CLR["fgdim"])

    # ─────────────────────────────────────────────────────────────────────────
    #  Window click-to-pick
    # ─────────────────────────────────────────────────────────────────────────
    def _pick_window_studio(self) -> None:
        if sys.platform != "win32":
            messagebox.showinfo(t("msg.os_limitation_title"), t("msg.window_capture_windows_only"))
            return
        WindowPicker(self, self._set_window_title)

    def _set_window_title(self, title: str, rect: Region | None = None) -> None:
        self.window_title_var.set(title)
        self._window_rect = rect
        self.lbl_window.config(text=f"✔ {title[:40]}")
        self.log(self.console, t("screen_recorder.window_selected_title", title=title))

    # ─────────────────────────────────────────────────────────────────────────
    #  Output path helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_output_path(self, ext: str) -> str:
        from core import recorder

        folder = self.out_folder_var.get().strip() or recorder.default_folder()
        os.makedirs(folder, exist_ok=True)
        return recorder.recording_name(folder, self.out_template_var.get(), ext)

    # ─────────────────────────────────────────────────────────────────────────
    #  Global hotkey
    # ─────────────────────────────────────────────────────────────────────────
    _HOTKEY_MAP: ClassVar[dict[str, tuple[int, int]]] = {
        "Alt+F9": (0x0001, 0x78),  # MOD_ALT,      VK_F9
        "Alt+F10": (0x0001, 0x79),  # MOD_ALT,      VK_F10
        "Ctrl+Alt+R": (0x0003, 0x52),  # MOD_ALT|CTRL, VK_R
    }

    def _on_hotkey_toggle(self) -> None:
        if self.hotkey_enabled_var.get():
            self._start_hotkey_listener()
        else:
            self._stop_hotkey_listener()

    def _start_hotkey_listener(self) -> None:
        if sys.platform != "win32":
            messagebox.showinfo(
                t("msg.unsupported_title"), t("msg.hotkeys_windows_only"), parent=self
            )
            self.hotkey_enabled_var.set(False)
            return
        import ctypes.wintypes

        key_name = self.hotkey_var.get()
        mod, vk = self._HOTKEY_MAP.get(key_name, (0x0001, 0x78))
        self._hotkey_active = True
        result = {}
        ready = threading.Event()

        def _listen() -> None:
            # The hotkey message is posted to the thread that registered it.
            user32 = ctypes.windll.user32
            result["ok"] = bool(user32.RegisterHotKey(None, 9901, mod | 0x4000, vk))  # MOD_NOREPEAT
            result["tid"] = ctypes.windll.kernel32.GetCurrentThreadId()
            ready.set()
            if not result["ok"]:
                return
            msg = ctypes.wintypes.MSG()
            while self._hotkey_active and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312 and msg.wParam == 9901:  # WM_HOTKEY
                    self.after(0, self._hotkey_triggered)
            user32.UnregisterHotKey(None, 9901)

        tasks.start_service(_listen, name="Hotkey")
        ready.wait(2)
        if not result.get("ok"):
            self._hotkey_active = False
            messagebox.showerror(
                t("common.error"),
                t(
                    "screen_recorder.could_not_register_key_name_another_application",
                    key_name=key_name,
                ),
                parent=self,
            )
            self.hotkey_enabled_var.set(False)
            return
        self._hotkey_tid = result["tid"]
        self.lbl_hotkey_status.config(
            text=t("screen_recorder.key_name_active", key_name=key_name), fg=CLR["green"]
        )
        self.log(
            self.console, t("screen_recorder.global_hotkey_key_name_registered", key_name=key_name)
        )

    def _stop_hotkey_listener(self) -> None:
        was_active = getattr(self, "_hotkey_active", False)
        self._hotkey_active = False
        tid = getattr(self, "_hotkey_tid", None)
        if sys.platform == "win32" and tid:
            ctypes.windll.user32.PostThreadMessageW(tid, 0x0012, 0, 0)  # WM_QUIT ends GetMessage
            self._hotkey_tid = None
        with contextlib.suppress(tk.TclError):
            self.lbl_hotkey_status.config(text=t("screen_recorder.inactive"), fg=CLR["fgdim"])
        if was_active:
            self.log(self.console, t("log.screen_recorder.global_hotkey_unregistered"))

    def _hotkey_triggered(self) -> None:
        if self._recording:
            self._stop()
        else:
            # Hotkey only toggles Advanced mode when on that tab
            if current_tab(self.notebook) == 1:
                self._start_pro()
            else:
                self._start_express()

    # ─────────────────────────────────────────────────────────────────────────
    #  Express Mode logic
    # ─────────────────────────────────────────────────────────────────────────
    def _wayland_check(self) -> str | None:
        """On Wayland: the external recorder to use, '' to fall back to X11
        capture after warning, or None when the user cancelled."""
        from core import recorder

        if recorder.session() != "wayland":
            return ""
        tool = recorder.wayland_tool()
        if tool:
            return tool
        if messagebox.askyesno(
            t("screen_recorder.wayland_desktop"),
            t("screen_recorder.this_desktop_uses_wayland_which_only_lets"),
            parent=self,
        ):
            return ""
        return None

    def _express_encode(self, ext: str = ".mp4") -> VideoEncode:
        from core import encoders as enc

        return enc.video("h264", "high", speed="fastest", hw="auto", container=ext.lstrip("."))

    def _start_capture(
        self,
        mode_name: str,
        video_in: list[str],
        *,
        out_ext: str = ".mp4",
        venc: VideoEncode | None = None,
        region: Region | None = None,
        seconds: int | None = None,
        audio: list[str] | None = None,
    ) -> None:
        from core import recorder

        tool = self._wayland_check()
        if tool is None:
            return
        out = self._resolve_output_path(out_ext)
        sources = (
            audio
            if audio is not None
            else [x for x in (self._audio_choice(self.audio_src1_var),) if x]
        )
        if tool:
            want = (
                tuple(
                    ("output" if src.endswith(".monitor") or "monitor" in src else "input")
                    for src in sources
                )
                or ()
            )
            cmd = recorder.wayland_command(
                tool, out, fps=self.fps_var.get() or "60", audio=want, seconds=seconds
            )
            self.log(
                self.console,
                t(
                    "screen_recorder.wayland_recording_with_tool_choose_the_screen",
                    tool=os.path.basename(tool),
                ),
            )
        else:
            venc = venc or self._express_encode(out_ext)
            cmd = recorder.ffmpeg_command(
                video_in,
                sources,
                out,
                venc,
                audio_codec=self.acodec_var.get() or "aac",
                region_crop=region,
                seconds=seconds,
            )
        self._execute(cmd, out, mode_name, seconds)

    def _start_express(self) -> None:
        from core import recorder

        self._start_capture("Full screen", recorder.screen_input("30"))

    def _start_express_area(self) -> None:
        from core import recorder

        if recorder.session() == "wayland" and recorder.wayland_tool():
            self._start_express()  # the portal dialog lets the user pick
            return
        AreaSelector(self, self._execute_express_area)

    def _execute_express_area(self, x: int, y: int, w: int, h: int) -> None:
        from core import recorder

        if w < 16 or h < 16:
            messagebox.showwarning(
                t("common.warning"), t("screen_recorder.drag_a_larger_area_to_record"), parent=self
            )
            return
        self._start_capture(
            "Area", recorder.screen_input("30", region=(x, y, w, h)), region=(x, y, w, h)
        )

    def _start_express_window(self) -> None:
        from core import recorder

        if recorder.session() == "wayland" and recorder.wayland_tool():
            self._start_express()
            return
        if sys.platform != "win32":
            messagebox.showinfo(
                t("msg.os_limitation_title"), t("msg.click_pick_windows_only"), parent=self
            )
            return
        WindowPicker(self, self._execute_express_window)

    def _execute_express_window(self, title: str, rect: Region | None = None) -> None:
        from core import recorder

        if rect:
            video = recorder.screen_input("30", region=rect)
        else:
            video = recorder.screen_input("30", window_title=title)
        self.log(self.console, t("screen_recorder.window_title", title=title))
        self._start_capture("Window", video)

    def _start_pro(self) -> None:
        from core import encoders as enc
        from core import recorder

        ext = self.container_var.get() or ".mp4"
        fps = self.fps_var.get() or "30"
        seconds = None
        if self.autostop_var.get():
            try:
                seconds = (
                    int(self.autostop_hh_var.get() or 0) * 3600
                    + int(self.autostop_mm_var.get() or 0) * 60
                    + int(self.autostop_ss_var.get() or 0)
                ) or None
            except ValueError:
                messagebox.showwarning(
                    t("common.warning"),
                    t("screen_recorder.the_auto_stop_time_must_be_whole"),
                    parent=self,
                )
                return
        _label, codec, hw = RECORD_CODECS[self.vcodec.value]
        crf = int(self.crf_var.get())
        quality = (
            "very_high"
            if crf <= 18
            else "high"
            if crf <= 22
            else "good"
            if crf <= 26
            else "low"
            if crf <= 30
            else "lowest"
        )
        speed = {
            "ultrafast": "fastest",
            "superfast": "fastest",
            "veryfast": "fast",
            "faster": "fast",
            "fast": "balanced",
            "medium": "balanced",
            "slow": "slow",
        }.get(self.preset_var.get(), "fastest")
        ve = enc.video(codec, quality, speed=speed, hw=hw, container=ext.lstrip("."))
        if self.chroma.value is Chroma.FULL:
            enc.full_chroma(ve)
        region = window = None
        if self.capture_mode_var.get() == "window":
            window = self.window_title_var.get().strip()
            if self._window_rect:
                region = self._window_rect
            elif not window:
                messagebox.showerror(
                    t("common.error"),
                    t("screen_recorder.pick_a_window_or_type_its_title"),
                    parent=self,
                )
                return
        elif self.selected_region:
            region = self.selected_region
        video = recorder.screen_input(
            fps,
            region=region,
            cursor=self.cursor_var.get(),
            window_title=window if window and not region else "",
        )
        sources = [
            x
            for x in (
                self._audio_choice(self.audio_src1_var),
                self._audio_choice(self.audio_src2_var),
            )
            if x
        ]
        self._start_capture(
            "Advanced", video, out_ext=ext, venc=ve, region=region, seconds=seconds, audio=sources
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Execution & teardown
    # ─────────────────────────────────────────────────────────────────────────
    def _set_buttons(self, recording: bool) -> None:
        state = "disabled" if recording else "normal"
        for b in (
            self.btn_start_express,
            self.btn_start_express_area,
            self.btn_start_express_window,
        ):
            b.config(state=state)
        self.btn_start_pro.config(
            state=state,
            text=t("screen_recorder.recording")
            if recording
            else t("screen_recorder.start_studio_recording"),
        )
        self.btn_stop.config(
            state="normal" if recording else "disabled",
            bg=CLR["red"] if recording else CLR["panel"],
            fg="white" if recording else CLR["fg"],
        )

    def _execute(
        self, cmd: list[str], out: str, mode_name: str, seconds: int | None = None
    ) -> None:
        from core import recorder

        self.output_path = out
        try:
            rec = recorder.Recording(
                cmd,
                out,
                seconds=seconds,
                on_exit=lambda r, rc: self.after(0, lambda: self._ended(r, rc)),
            )
        except OSError as exc:
            messagebox.showerror(
                t("common.error"),
                t("screen_recorder.the_recorder_could_not_start_exc", exc=exc),
                parent=self,
            )
            return
        self.record_proc = rec
        self._recording = True
        self._set_buttons(True)
        self.log(
            self.console, t("screen_recorder.mode_name_recording_out", mode_name=mode_name, out=out)
        )
        self._tick()

    def _tick(self) -> None:
        rec = self.record_proc
        if rec is None or not self._recording:
            return
        m, s_ = divmod(int(rec.elapsed), 60)
        try:
            self.btn_stop.config(text=t("screen_recorder.stop_m_02d_s_02d", m=m, s_=s_))
        except tk.TclError:
            return
        self.after(500, self._tick)

    def _ended(self, rec: Recording, rc: int) -> None:
        """The recorder process finished - on request or by itself."""
        if rec is not self.record_proc and self.record_proc is not None:
            return
        self.record_proc = None
        self._recording = False
        self._set_buttons(False)
        with contextlib.suppress(tk.TclError):
            self.btn_stop.config(
                text=t("screen_recorder.stop_button")
                if t("screen_recorder.stop_button") != "screen_recorder.stop_button"
                else "■  STOP"
            )
        ok = os.path.isfile(rec.out) and os.path.getsize(rec.out) > 1024
        if ok and (rec.stopping or rc == 0 or rec.seconds):
            self.log(self.console, t("screen_recorder.saved_out", out=rec.out))
            return
        detail = rec.error_text() or f"exit code {rc}"
        self.log(self.console, t("screen_recorder.recording_failed_detail", detail=detail))
        messagebox.showerror(
            t("common.error"), "The recording stopped unexpectedly:\n\n" + detail, parent=self
        )

    def _stop(self) -> None:
        rec = self.record_proc
        if rec is None:
            return
        self.btn_stop.config(state="disabled")
        self.log(self.console, t("log.screen_recorder.stopping_flushing_file_headers"))
        tasks.submit(rec.stop, name="RecorderStop")

    def on_app_closing(self) -> None:
        """The app is quitting: finish the recording's file properly."""
        self._stop_hotkey_listener()
        rec = self.record_proc
        if rec is not None and rec.running():
            rec.stop(timeout=8)
