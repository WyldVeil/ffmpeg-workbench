"""
tab_youtubedownloader.py  ─  Video Downloader & Media Extractor

Front-end UI for the third-party yt-dlp tool. Provides real-time progress,
audio extraction, SponsorBlock, chapter-aware trimming, batch/playlist
queue, subtitle download, live stream recording, cookies/auth, download
history, format filtering, and complete FFmpeg encode settings.

NOTE: yt-dlp is independent open-source software. FFmpeg Workbench
merely launches yt-dlp as a subprocess; it does not host, distribute, or
modify it. See the on-screen disclaimer in _build_disclaimer() for the
legal terms presented to the user.

Requirements
------------
  yt-dlp must be available as one of:
    1. bin/yt-dlp.exe  (drop it next to ffmpeg.exe - recommended)
    2. yt-dlp on the system PATH  (pip install yt-dlp)
"""

import contextlib
import json
import logging
import os
import re
import tempfile
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from functools import partial
from tkinter import messagebox, ttk

from core import proc as _proc
from core import tasks
from core import ytdlp as ytdlp_core
from core.errors import ToolError
from core.i18n import t
from core.ops import youtube as yto
from core.queue_manager import Hooks
from core.scaling import px
from core.timecode import Timecode
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.kit.time_fields import RangePicker
from tabs.kit.widgets import ChoiceBox
from tabs.widgets import SaveRow

logger = logging.getLogger("crossfader.social.youtube")

# ─────────────────────────────────────────────────────────────────────────────
#  Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fmt_dur(seconds) -> str:
    return f"{Timecode.of(seconds):H.0}"


_X264_PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]


#: the format list's filter (radio value -> what it shows); numbers are heights
_FORMAT_FILTERS = {
    "all": "All",
    "2160": "4K",
    "1080": "1080p",
    "720": "720p",
    "480": "480p",
    "audio": "Audio only",
}


_DL_PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+\s*\S+)\s+at\s+(\S+)\s+ETA\s+(\S+)"
)

_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):([\d.]+)")


def _history_file() -> str:
    """The download history, with the app's other settings."""
    from core.paths import config_dir

    return os.path.join(config_dir(), "download_history.json")


class YouTubeDownloaderTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        # Bug fix: Removed self.configure(bg=CLR["bg"]) to prevent ttk styling crash

        self._video_info: dict = {}
        self._format_options: list[tuple[str, str]] = []
        self._thumb_photo = None
        self._active_proc = None
        self._batch_running = False
        self._record_proc = None
        self._record_start: float = 0.0
        self._history: list[dict] = []
        self._history_visible = True
        self._batch_visible = False
        # Last value we auto-suggested into the "Save to" box. Used to detect
        # whether the user has manually changed it: if the box still matches
        # the auto-suggestion, a new fetch is allowed to refresh it; if the
        # user typed/browsed something else, we leave it alone.
        self._last_auto_out: str = ""

        self._load_history()
        self._build_ui()
        self.after(200, self._check_ytdlp)

    # ─────────────────────────────────────────────────────────────────────────
    #  History persistence
    # ─────────────────────────────────────────────────────────────────────────

    def _load_history(self):
        try:
            if os.path.isfile(_history_file()):
                with open(_history_file(), encoding="utf-8") as f:
                    self._history = json.load(f)
        except (OSError, ValueError):
            self._history = []

    def _save_history(self):
        try:
            with open(_history_file(), "w", encoding="utf-8") as f:
                json.dump(self._history, f, indent=2, ensure_ascii=False)
        except (OSError, ValueError):
            pass

    def _append_history(self, title: str, duration: str, url: str, file_path: str):
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "title": title,
            "duration": duration,
            "url": url,
            "file": file_path,
        }
        self._history.append(entry)
        self._save_history()
        self.after(0, self._refresh_history_tree)

    # ─────────────────────────────────────────────────────────────────────────
    #  UI Build
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Tails: a clear warning while downloads bypass Tor (Settings → Network)
        self._route_banner = tk.Label(
            self,
            text="⚠  DIRECT CONNECTION - NOT ANONYMOUS: downloads are bypassing "
            "Tor for this session (Settings → Network to turn off)",
            bg="#B71C1C",
            fg="white",
            font=(UI_FONT, 10, "bold"),
            pady=px(6),
        )
        self._poll_route_banner()
        # Outer scrollable frame
        outer = tk.Frame(self, bg=CLR["bg"])
        outer.pack(fill="both", expand=True)
        self._outer_frame = outer

        canvas_scroll = tk.Canvas(outer, bg=CLR["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas_scroll.yview)
        canvas_scroll.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas_scroll.pack(side="left", fill="both", expand=True)

        self._scroll_frame = tk.Frame(canvas_scroll, bg=CLR["bg"])
        self._scroll_win = canvas_scroll.create_window(
            (0, 0), window=self._scroll_frame, anchor="nw"
        )

        def _on_configure(event):
            canvas_scroll.configure(scrollregion=canvas_scroll.bbox("all"))
            canvas_scroll.itemconfig(self._scroll_win, width=canvas_scroll.winfo_width())

        self._scroll_frame.bind("<Configure>", _on_configure)
        canvas_scroll.bind(
            "<Configure>", lambda e: canvas_scroll.itemconfig(self._scroll_win, width=e.width)
        )

        root = self._scroll_frame

        # 1. Header
        self.make_header(
            root,
            t("tab.youtube_downloader"),
            t("youtube.tab_description"),
            icon="▶",
        )

        # 1b. Legal / third-party disclaimer (always visible)
        self._build_disclaimer(root)

        # 2. URL row
        self._build_url_row(root)

        # 3. Info panel
        self._build_info_panel(root)

        # 4. Settings notebook
        self._build_settings_notebook(root)

        # 5. Time range / chapters
        self._build_time_range(root)

        # 6. Output row
        self._build_output_row(root)

        # 7. Progress row
        self._build_progress_row(root)

        # 8. Action buttons
        self._build_action_buttons(root)

        # 9. Batch panel (hidden by default)
        self._build_batch_panel(root)

        # 10. Console
        self.console = self.make_console_block(
            root, height=5, bg=CLR["bg"], padx=px(20), pady=px(0, 6)
        )

        # 11. History panel
        self._build_history_panel(root)

    # ── Legal disclaimer ─────────────────────────────────────────────────────

    def _build_disclaimer(self, root):
        """
        Always-visible legal notice. Distances FFmpeg Workbench from
        the third-party yt-dlp tool and the platforms it interacts with,
        and shifts compliance/copyright responsibility onto the user.

        Kept in English by design: legal language can shift meaning when
        translated, and this notice exists to document the user's accepted
        terms in a single canonical form.
        """
        outer = tk.Frame(
            root,
            bg=CLR["bg"],
            highlightthickness=px(1),
            highlightbackground=CLR["orange"],
        )
        outer.pack(fill="x", padx=px(20), pady=px(10, 4))

        inner = tk.Frame(outer, bg=CLR["bg"])
        inner.pack(fill="x", padx=px(12), pady=px(8))

        tk.Label(
            inner,
            text="⚠  Important Notice: Third-Party Tool & User Responsibility",
            font=(UI_FONT, 10, "bold"),
            bg=CLR["bg"],
            fg=CLR["orange"],
            anchor="w",
        ).pack(fill="x")

        body = (
            "This feature is a user-interface front-end for "
            "yt-dlp, an independent open-source command-line program "
            "developed and maintained by third parties. FFmpeg "
            "Workbench is NOT affiliated with, endorsed by, or responsible "
            "for yt-dlp, any website or platform yt-dlp may interact "
            "with, or any content downloaded through it. yt-dlp is "
            "executed as a separate process; FFmpeg Workbench neither hosts nor "
            "modifies it.\n\n"
            "By using this feature you acknowledge and agree that:\n"
            "  •  You are solely responsible for complying with the "
            "Terms of Service, robots.txt, and acceptable-use policies "
            "of any website or platform you access.\n"
            "  •  You will only download content that you own, that is "
            "in the public domain, or for which you have explicit "
            "permission from the rightsholder (including, where "
            "applicable, fair use / fair dealing in your jurisdiction).\n"
            "  •  You are solely responsible for complying with all "
            "applicable copyright, intellectual-property, privacy, "
            "broadcasting, and computer-misuse laws in your "
            "jurisdiction and the jurisdiction of the content's origin.\n"
            "  •  You will not use this software to circumvent technical "
            "protection measures, paywalls, geographic restrictions, or "
            "DRM where doing so is unlawful.\n"
            '  •  This software is provided "AS IS", WITHOUT WARRANTY '
            "OF ANY KIND, express or implied. To the maximum extent "
            "permitted by law, the authors of FFmpeg Workbench disclaim "
            "all liability for any direct, indirect, incidental, "
            "special, consequential, or punitive damages arising out "
            "of or relating to your use of this feature, including "
            "without limitation any claim by a rightsholder, platform "
            "operator, regulator, or other third party.\n\n"
            "If you do not agree to these terms, do not use this "
            "feature."
        )
        tk.Label(
            inner,
            text=body,
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            anchor="w",
            justify="left",
            wraplength=px(900),
        ).pack(fill="x", pady=px(4, 0))

    # ── URL row ──────────────────────────────────────────────────────────────

    def _build_url_row(self, root):
        url_f = tk.Frame(root, bg=CLR["bg"])
        url_f.pack(fill="x", padx=px(20), pady=px(14, 8))

        tk.Label(
            url_f, text=t("youtube.url"), font=(UI_FONT, 10, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")

        self._url_var = tk.StringVar()
        self._url_entry = tk.Entry(
            url_f,
            textvariable=self._url_var,
            width=52,
            relief="flat",
            font=(UI_FONT, 10),
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            highlightthickness=px(1),
            highlightbackground=CLR["border"],
            highlightcolor=CLR["accent"],
        )
        self._url_entry.pack(side="left", padx=px(8))
        self._url_entry.bind("<<Paste>>", self._on_paste)

        # Playlist Toggle
        self._playlist_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            url_f,
            text="Playlist",
            variable=self._playlist_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
            cursor="hand2",
        ).pack(side="left", padx=px(0, 8))

        def _btn(text, cmd, bg=None):
            b = tk.Button(
                url_f,
                text=text,
                relief="flat",
                font=(UI_FONT, 9),
                cursor="hand2",
                bg=bg or CLR["panel"],
                fg=CLR["fg"] if not bg else "white",
                activebackground=CLR["accent"],
                activeforeground="white",
                padx=px(8),
                pady=px(3),
                command=cmd,
            )
            b.pack(side="left", padx=px(0, 5))
            return b

        self._btn_fetch = _btn("🔍 Fetch Info", self._on_fetch)
        self._btn_preview = _btn("▶ Preview", self._preview, bg=CLR["accent"])
        _btn("🌐 Browser", self._open_browser)
        self._btn_record = tk.Button(
            url_f,
            text=t("youtube.record"),
            relief="flat",
            font=(UI_FONT, 9),
            cursor="hand2",
            bg=CLR["red"],
            fg="white",
            activebackground=CLR["pink"],
            activeforeground="white",
            padx=px(8),
            pady=px(3),
            state="disabled",
            command=self._start_record,
        )
        self._btn_record.pack(side="left", padx=px(0, 5))

    # ── Info panel ───────────────────────────────────────────────────────────

    def _build_info_panel(self, root):
        info_lf = tk.LabelFrame(
            root,
            text=t("youtube.video_info"),
            padx=px(10),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            bd=px(1),
            relief="solid",
        )
        info_lf.pack(fill="x", padx=px(20), pady=px(0, 8))

        self._thumb_canvas = tk.Canvas(
            info_lf,
            width=px(256),
            height=px(144),
            bg="#0A0A0A",
            highlightthickness=px(1),
            highlightbackground=CLR["border"],
        )
        self._thumb_canvas.pack(side="left", padx=px(0, 14))
        self._thumb_canvas.create_text(
            px(128), px(72), text="...", fill=CLR["fgdim"], font=(UI_FONT, 13)
        )

        meta_right = tk.Frame(info_lf, bg=CLR["bg"])
        meta_right.pack(side="left", fill="both", expand=True, anchor="n")

        self._title_var = tk.StringVar(value="-")
        self._dur_var = tk.StringVar(value="-")
        self._channel_var = tk.StringVar(value="-")
        self._views_var = tk.StringVar(value="-")
        self._live_var = tk.StringVar(value="")

        for lbl_text, var in [
            ("Title:", self._title_var),
            ("Duration:", self._dur_var),
            ("Channel:", self._channel_var),
            ("Views:", self._views_var),
        ]:
            row = tk.Frame(meta_right, bg=CLR["bg"])
            row.pack(fill="x", pady=px(2))
            tk.Label(
                row,
                text=lbl_text,
                font=(UI_FONT, 9, "bold"),
                width=10,
                anchor="e",
                bg=CLR["bg"],
                fg=CLR["fgdim"],
            ).pack(side="left")
            tk.Label(
                row,
                textvariable=var,
                font=(UI_FONT, 9),
                wraplength=px(400),
                justify="left",
                anchor="w",
                bg=CLR["bg"],
                fg=CLR["fg"],
            ).pack(side="left", padx=px(6))

        live_row = tk.Frame(meta_right, bg=CLR["bg"])
        live_row.pack(fill="x", pady=px(2))
        self._live_badge = tk.Label(
            live_row,
            textvariable=self._live_var,
            font=(UI_FONT, 9, "bold"),
            bg=CLR["red"],
            fg="white",
            padx=px(6),
            pady=px(1),
        )

        thumb_btn = tk.Button(
            meta_right,
            text=t("youtube.save_thumbnail"),
            relief="flat",
            font=(UI_FONT, 9),
            cursor="hand2",
            bg=CLR["panel"],
            fg=CLR["fg"],
            activebackground=CLR["accent"],
            activeforeground="white",
            command=self._save_thumbnail,
        )
        thumb_btn.pack(anchor="w", pady=px(6, 0))

    # ── Settings notebook ────────────────────────────────────────────────────

    def _build_settings_notebook(self, root):
        nb_frame = tk.Frame(root, bg=CLR["bg"])
        nb_frame.pack(fill="x", padx=px(20), pady=px(0, 8))

        style = ttk.Style()
        try:
            style.configure("YT.TNotebook", background=CLR["bg"])
            style.configure("YT.TNotebook.Tab", font=(UI_FONT, 9), padding=px(8, 4))
        except Exception:
            logger.debug("_build_settings_notebook: best-effort step failed", exc_info=True)

        self._settings_nb = ttk.Notebook(nb_frame)
        self._settings_nb.pack(fill="x")

        # Tab 1: Quality
        self._tab_quality = tk.Frame(self._settings_nb, bg=CLR["bg"], padx=px(10), pady=px(8))
        self._settings_nb.add(self._tab_quality, text="Quality")
        self._build_quality_tab(self._tab_quality)

        # Tab 2: Encode
        self._tab_encode = tk.Frame(self._settings_nb, bg=CLR["bg"], padx=px(10), pady=px(8))
        self._settings_nb.add(self._tab_encode, text="Encode")
        self._build_encode_tab(self._tab_encode)

        # Tab 3: Audio Only
        self._tab_audio = tk.Frame(self._settings_nb, bg=CLR["bg"], padx=px(10), pady=px(8))
        self._settings_nb.add(self._tab_audio, text=t("youtube.audio_only"))
        self._build_audio_tab(self._tab_audio)

        # Tab 4: Subtitles & Meta (Upgraded)
        self._tab_subs = tk.Frame(self._settings_nb, bg=CLR["bg"], padx=px(10), pady=px(8))
        self._settings_nb.add(self._tab_subs, text=t("youtube.subs_meta"))
        self._build_subs_tab(self._tab_subs)

        # Tab 5: SponsorBlock
        self._tab_sponsor = tk.Frame(self._settings_nb, bg=CLR["bg"], padx=px(10), pady=px(8))
        self._settings_nb.add(self._tab_sponsor, text="SponsorBlock")
        self._build_sponsorblock_tab(self._tab_sponsor)

        # Tab 6: Auth / Network (Upgraded)
        self._tab_auth = tk.Frame(self._settings_nb, bg=CLR["bg"], padx=px(10), pady=px(8))
        self._settings_nb.add(self._tab_auth, text=t("youtube.network_advanced"))
        self._build_auth_tab(self._tab_auth)

    def _build_quality_tab(self, parent):
        # Format filter radios
        filter_row = tk.Frame(parent, bg=CLR["bg"])
        filter_row.pack(fill="x", pady=px(0, 6))
        tk.Label(
            filter_row,
            text=t("youtube.filter"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left")

        self._filter_var = tk.StringVar(value="all")
        for code, label in _FORMAT_FILTERS.items():
            tk.Radiobutton(
                filter_row,
                text=label,
                variable=self._filter_var,
                value=code,
                font=(UI_FONT, 9),
                bg=CLR["bg"],
                fg=CLR["fg"],
                activebackground=CLR["bg"],
                selectcolor=CLR["panel"],
                command=self._apply_format_filter,
            ).pack(side="left", padx=px(4))

        # Quality combobox + size estimate
        q_row = tk.Frame(parent, bg=CLR["bg"])
        q_row.pack(fill="x", pady=px(0, 6))
        tk.Label(
            q_row, text=t("common.quality"), font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self._format_cb = ttk.Combobox(q_row, state="readonly", width=58, font=(UI_FONT, 9))
        self._format_cb["values"] = ["(fetch video info first)"]
        self._format_cb.current(0)
        self._format_cb.pack(side="left", padx=px(8))
        self._format_cb.bind("<<ComboboxSelected>>", self._on_format_selected)

        self._size_var = tk.StringVar(value="")
        tk.Label(
            q_row, textvariable=self._size_var, font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["orange"]
        ).pack(side="left")

        # Download mode radios
        mode_lf = tk.LabelFrame(
            parent,
            text=f"  {t('youtube.download_mode_section')}  ",
            font=(UI_FONT, 8, "bold"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            bd=px(1),
            relief="solid",
            padx=px(10),
            pady=px(6),
        )
        mode_lf.pack(fill="x", pady=px(4, 0))

        self._dl_mode = tk.StringVar(value="copy")
        modes = [
            ("copy", t("youtube.direct_copy_remux_to_chosen_container_no_re_enco")),
            ("encode", t("youtube.re_encode_download_best_ffmpeg_with_encode_setti")),
            ("audio", t("youtube.audio_only_yt_dlp_x_with_chosen_audio_format_bit")),
        ]
        for val, label in modes:
            tk.Radiobutton(
                mode_lf,
                text=label,
                variable=self._dl_mode,
                value=val,
                font=(UI_FONT, 9),
                bg=CLR["bg"],
                fg=CLR["fg"],
                activebackground=CLR["bg"],
                selectcolor=CLR["panel"],
            ).pack(anchor="w", pady=px(1))

    def _build_encode_tab(self, parent):
        _combo, _row, col_left, col_right = self._build_encode_tab_vcodec(parent)
        crf_row = self._build_encode_tab_res(col_left, col_right)
        self._build_encode_tab_crf(_combo, _row, col_right, crf_row)

    def _build_encode_tab_vcodec(self, parent):
        def _row(p, lbl, widget_factory, pady=3):
            r = tk.Frame(p, bg=CLR["bg"])
            r.pack(fill="x", pady=px(pady))
            tk.Label(
                r, text=lbl, font=(UI_FONT, 9), width=18, anchor="e", bg=CLR["bg"], fg=CLR["fgdim"]
            ).pack(side="left")
            widget_factory(r)
            return r

        def _combo(parent_row, var, values, width=22):
            cb = ttk.Combobox(
                parent_row,
                textvariable=var,
                values=values,
                state="readonly",
                width=width,
                font=(UI_FONT, 9),
            )
            cb.pack(side="left", padx=px(6))
            return cb

        col_left = tk.Frame(parent, bg=CLR["bg"])
        col_left.pack(side="left", fill="both", expand=True, padx=px(0, 16))
        col_right = tk.Frame(parent, bg=CLR["bg"])
        col_right.pack(side="left", fill="both", expand=True)

        # Left column
        self._vcodec_var = tk.StringVar(value="libx264")
        _row(
            col_left,
            "Video codec:",
            lambda p: _combo(p, self._vcodec_var, yto.VIDEO_CODECS),
        )

        self._acodec_var = tk.StringVar(value="aac")
        _row(
            col_left,
            "Audio codec:",
            lambda p: _combo(
                p, self._acodec_var, ["aac", "libmp3lame", "flac", "libopus", "libvorbis", "copy"]
            ),
        )

        self._container_var = tk.StringVar(value="mp4")
        _row(
            col_left,
            "Container:",
            lambda p: _combo(p, self._container_var, ["mp4", "mkv", "webm", "mov"], width=12),
        )

        self._preset_var = tk.StringVar(value="fast")
        _row(
            col_left,
            "Preset:",
            lambda p: _combo(p, self._preset_var, _X264_PRESETS, width=14),
        )
        return _combo, _row, col_left, col_right

    def _show_custom_res(self, _e=None):
        if self._res.value is yto.Resolution.CUSTOM:
            self._custom_res_entry.pack(side="left", padx=px(4))
        else:
            self._custom_res_entry.pack_forget()

    def _build_encode_tab_res(self, col_left, col_right):
        res_row = tk.Frame(col_left, bg=CLR["bg"])
        res_row.pack(fill="x", pady=px(3))
        tk.Label(
            res_row,
            text=t("common.resolution"),
            font=(UI_FONT, 9),
            width=18,
            anchor="e",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left")
        sizes = {r: r.value.replace("x", "×") for r in yto.Resolution}
        sizes |= {
            yto.Resolution.ORIGINAL: "Original",
            yto.Resolution.CUSTOM: t("youtube.smart_reframe_custom"),
        }
        self._res = ChoiceBox(
            res_row, sizes, yto.Resolution.ORIGINAL, width=14, on_change=self._show_custom_res
        )
        self._res.configure(font=(UI_FONT, 9))
        self._res.pack(side="left", padx=px(6))
        self._custom_res_var = tk.StringVar(value="1280×720")
        self._custom_res_entry = tk.Entry(
            res_row,
            textvariable=self._custom_res_var,
            width=10,
            relief="flat",
            font=(UI_FONT, 9),
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
        )

        # Right column
        crf_row = tk.Frame(col_right, bg=CLR["bg"])
        crf_row.pack(fill="x", pady=px(3))
        tk.Label(
            crf_row,
            text=t("youtube.crf_0_51"),
            font=(UI_FONT, 9),
            width=16,
            anchor="e",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left")
        return crf_row

    def _build_encode_tab_crf(self, _combo, _row, col_right, crf_row):
        self._crf_var = tk.IntVar(value=18)
        crf_scale = tk.Scale(
            crf_row,
            variable=self._crf_var,
            from_=0,
            to=51,
            orient="horizontal",
            length=px(120),
            bg=CLR["panel"],
            fg=CLR["fg"],
            highlightthickness=0,
            command=lambda v: self._crf_entry_var.set(str(int(float(v)))),
        )
        crf_scale.pack(side="left", padx=px(4))
        self._crf_entry_var = tk.StringVar(value="18")
        crf_entry = tk.Entry(
            crf_row,
            textvariable=self._crf_entry_var,
            width=4,
            relief="flat",
            font=(UI_FONT, 9),
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
        )
        crf_entry.pack(side="left", padx=px(4))
        crf_entry.bind(
            "<Return>",
            lambda e: self._crf_var.set(max(0, min(51, int(self._crf_entry_var.get() or 18)))),
        )

        self._abitrate_var = tk.StringVar(value="192k")
        _row(
            col_right,
            "Audio bitrate:",
            lambda p: _combo(
                p, self._abitrate_var, ["320k", "256k", "192k", "128k", "96k", "64k"], width=10
            ),
        )

        self._asamplerate_var = tk.StringVar(value="48000")
        _row(
            col_right,
            "Sample rate:",
            lambda p: _combo(p, self._asamplerate_var, ["48000", "44100", "22050"], width=10),
        )

    def _build_audio_tab(self, parent):
        r1 = tk.Frame(parent, bg=CLR["bg"])
        r1.pack(fill="x", pady=px(4))
        tk.Label(
            r1, text=t("tts.audio_format_label"), font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self._audio_fmt_var = tk.StringVar(value="mp3")
        ttk.Combobox(
            r1,
            textvariable=self._audio_fmt_var,
            values=["mp3", "aac", "flac", "opus", "wav", "m4a"],
            state="readonly",
            width=10,
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(8))

        r2 = tk.Frame(parent, bg=CLR["bg"])
        r2.pack(fill="x", pady=px(4))
        tk.Label(
            r2, text=t("youtube.audio_quality"), font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        self._audio_quality_var = tk.StringVar(value="0")
        ttk.Combobox(
            r2,
            textvariable=self._audio_quality_var,
            values=[
                t("youtube.youtube_0_best"),
                "2",
                "4",
                t("youtube.youtube_5_medium"),
                "7",
                t("youtube.youtube_9_worst"),
            ],
            state="readonly",
            width=14,
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(8))

        r3 = tk.Frame(parent, bg=CLR["bg"])
        r3.pack(fill="x", pady=px(4))
        self._embed_thumb_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            r3,
            text=t("youtube.embed_thumbnail"),
            variable=self._embed_thumb_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left")
        self._add_metadata_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            r3,
            text=t("youtube.add_metadata_tags"),
            variable=self._add_metadata_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left", padx=px(16))

    def _build_subs_tab(self, parent):
        # Subtitles logic
        r1 = tk.Frame(parent, bg=CLR["bg"])
        r1.pack(fill="x", pady=px(4))
        self._dl_subs_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r1,
            text=t("youtube.download_subtitles"),
            variable=self._dl_subs_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left")

        r2 = tk.Frame(parent, bg=CLR["bg"])
        r2.pack(fill="x", pady=px(4))
        tk.Label(
            r2, text=t("common.language"), font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left")
        self._sub_lang_var = tk.StringVar(value="en")
        self._sub_lang_cb = ttk.Combobox(
            r2,
            textvariable=self._sub_lang_var,
            values=["en", "es", "fr", "de", "ja", "zh", "pt", "ar"],
            width=14,
            font=(UI_FONT, 9),
        )
        self._sub_lang_cb.pack(side="left", padx=px(6))

        self._sub_auto_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r2,
            text=t("youtube.include_auto_generated"),
            variable=self._sub_auto_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left", padx=px(10))

        r3 = tk.Frame(parent, bg=CLR["bg"])
        r3.pack(fill="x", pady=px(4))
        tk.Label(
            r3, text=t("common.format"), font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left")
        self._sub_fmt_var = tk.StringVar(value="srt")
        ttk.Combobox(
            r3,
            textvariable=self._sub_fmt_var,
            values=["srt", "vtt", "ass"],
            state="readonly",
            width=8,
            font=(UI_FONT, 9),
        ).pack(side="left", padx=px(6))

        r4 = tk.Frame(parent, bg=CLR["bg"])
        r4.pack(fill="x", pady=px(4))
        self._embed_subs_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r4,
            text=t("youtube.embed_subtitles_in_container"),
            variable=self._embed_subs_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left")
        self._burn_subs_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r4,
            text=t("youtube.burn_into_video_hardcode"),
            variable=self._burn_subs_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left", padx=px(16))

        # Metadata & Chapters (Upgraded features)
        tk.Frame(parent, bg=CLR["border"], height=px(1)).pack(fill="x", pady=px(8))
        tk.Label(
            parent,
            text=t("youtube.metadata_extra"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(anchor="w", pady=px(0, 4))

        meta_r = tk.Frame(parent, bg=CLR["bg"])
        meta_r.pack(fill="x")
        self._embed_chapters_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            meta_r,
            text=t("youtube.embed_chapters"),
            variable=self._embed_chapters_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left")

        self._embed_metadata_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            meta_r,
            text=t("youtube.embed_video_metadata"),
            variable=self._embed_metadata_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left", padx=px(16))

    def _build_sponsorblock_tab(self, parent):
        tk.Label(
            parent,
            text=t("youtube.skip_remove_these_segment_types"),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(anchor="w", pady=px(0, 6))

        cats = [
            ("sponsor", "Sponsor"),
            ("intro", "Intro"),
            ("outro", "Outro"),
            ("selfpromo", "Self-promo"),
            ("interaction", t("youtube.interaction_reminder")),
            ("preview", t("youtube.preview_recap")),
            ("music_offtopic", t("youtube.non_music_section")),
        ]
        self._sb_vars: dict[str, tk.BooleanVar] = {}
        grid = tk.Frame(parent, bg=CLR["bg"])
        grid.pack(fill="x")
        for i, (key, label) in enumerate(cats):
            var = tk.BooleanVar(value=(key == "sponsor"))
            self._sb_vars[key] = var
            tk.Checkbutton(
                grid,
                text=label,
                variable=var,
                font=(UI_FONT, 9),
                bg=CLR["bg"],
                fg=CLR["fg"],
                activebackground=CLR["bg"],
                selectcolor=CLR["panel"],
            ).grid(row=i // 4, column=i % 4, sticky="w", padx=px(8), pady=px(2))

        action_row = tk.Frame(parent, bg=CLR["bg"])
        action_row.pack(fill="x", pady=px(8, 0))
        tk.Label(
            action_row, text=t("youtube.action"), font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left")
        self._sb_action_var = tk.StringVar(value="remove")
        tk.Radiobutton(
            action_row,
            text=t("youtube.remove_segments"),
            variable=self._sb_action_var,
            value="remove",
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left", padx=px(8))
        tk.Radiobutton(
            action_row,
            text=t("youtube.mark_as_chapters"),
            variable=self._sb_action_var,
            value="mark",
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left", padx=px(4))

    def _build_auth_tab(self, parent):
        def _labeled_entry(parent_row, lbl, var, width=32, show=None):
            r = tk.Frame(parent_row, bg=CLR["bg"])
            r.pack(fill="x", pady=px(3))
            tk.Label(
                r, text=lbl, font=(UI_FONT, 9), width=14, anchor="e", bg=CLR["bg"], fg=CLR["fgdim"]
            ).pack(side="left")
            kw = {"show": show} if show else {}
            tk.Entry(
                r,
                textvariable=var,
                width=width,
                relief="flat",
                font=(UI_FONT, 9),
                bg=CLR["input_bg"],
                fg=CLR["input_fg"],
                insertbackground=CLR["accent"],
                highlightthickness=px(1),
                highlightbackground=CLR["border"],
                highlightcolor=CLR["accent"],
                **kw,
            ).pack(side="left", padx=px(6))
            return r

        self._cookies_var = tk.StringVar()
        self._username_var = tk.StringVar()
        self._password_var = tk.StringVar()
        self._ratelimit_var = tk.StringVar()
        self._proxy_var = tk.StringVar()
        self._custom_args_var = tk.StringVar()

        # Cookies file row
        cookies_row = tk.Frame(parent, bg=CLR["bg"])
        cookies_row.pack(fill="x", pady=px(3))
        tk.Label(
            cookies_row,
            text=t("youtube.cookies_txt"),
            font=(UI_FONT, 9),
            width=14,
            anchor="e",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(side="left")
        tk.Entry(
            cookies_row,
            textvariable=self._cookies_var,
            width=28,
            relief="flat",
            font=(UI_FONT, 9),
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
            highlightthickness=px(1),
            highlightbackground=CLR["border"],
            highlightcolor=CLR["accent"],
        ).pack(side="left", padx=px(6))
        tk.Button(
            cookies_row,
            text=t("btn.browse"),
            relief="flat",
            font=(UI_FONT, 9),
            cursor="hand2",
            bg=CLR["panel"],
            fg=CLR["fg"],
            activebackground=CLR["accent"],
            activeforeground="white",
            command=self._browse_cookies,
        ).pack(side="left")

        _labeled_entry(parent, "Username:", self._username_var)
        _labeled_entry(parent, "Password:", self._password_var, show="*")
        _labeled_entry(parent, "Rate limit:", self._ratelimit_var, width=14)
        tk.Label(
            parent,
            text=t("youtube.e_g_1m_500k_leave_blank_for_unlimited"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(anchor="w", padx=px(20))
        _labeled_entry(parent, "Proxy:", self._proxy_var, width=28)
        tk.Label(
            parent,
            text=t("youtube.e_g_socks5_127_0_0_1_1080"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(anchor="w", padx=px(20))

        tk.Frame(parent, bg=CLR["border"], height=px(1)).pack(fill="x", pady=px(6))

        # Advanced: Aria2c & Custom arguments
        aria_row = tk.Frame(parent, bg=CLR["bg"])
        aria_row.pack(fill="x", pady=px(3))
        self._use_aria2c_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            aria_row,
            text=t("youtube.use_aria2c_fast_external_downloader_requires_ari"),
            variable=self._use_aria2c_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
        ).pack(side="left", padx=px(4))

        _labeled_entry(parent, "Custom yt-dlp args:", self._custom_args_var, width=40)
        tk.Label(
            parent,
            text=t("youtube.e_g_write_comments_dateafter_now_7days"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(anchor="w", padx=px(20))

    # ── Time range / chapters ────────────────────────────────────────────────

    def _build_time_range(self, root):
        trim_lf = tk.LabelFrame(
            root,
            text=t("youtube.time_range"),
            padx=px(10),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            bd=px(1),
            relief="solid",
        )
        trim_lf.pack(fill="x", padx=px(20), pady=px(0, 8))

        left_f = tk.Frame(trim_lf, bg=CLR["bg"])
        left_f.pack(side="left", fill="both", expand=True)

        right_f = tk.Frame(trim_lf, bg=CLR["bg"])
        right_f.pack(side="right", fill="y", padx=px(16, 0))

        # Trim controls
        trim_row = tk.Frame(left_f, bg=CLR["bg"])
        trim_row.pack(fill="x")
        self._trim_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            trim_row,
            text=t("youtube.trim_section_only"),
            variable=self._trim_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fg"],
            activebackground=CLR["bg"],
            selectcolor=CLR["panel"],
            command=self._on_trim_toggle,
        ).pack(side="left")

        tk.Label(
            trim_row, text=t("youtube.from"), font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left", padx=px(16, 0))
        self._section = RangePicker(
            trim_row, start="00:00:00", end="00:00:00", joiner=t("youtube.to")
        )
        self._section.pack(side="left", padx=px(4))
        self._start_var, self._end_var = self._section.start.var, self._section.end.var
        self._start_ent, self._end_ent = self._section.start.entry, self._section.end.entry
        for entry in (self._start_ent, self._end_ent):
            entry.config(state="disabled", font=(MONO_FONT, 9), width=10)

        tk.Label(
            trim_row, text=t("youtube.hh_mm_ss"), font=(UI_FONT, 8), bg=CLR["bg"], fg=CLR["fgdim"]
        ).pack(side="left", padx=px(4, 0))

        # Chapters listbox
        tk.Label(
            right_f,
            text=t("youtube.chapters_click_to_set_trim_range"),
            font=(UI_FONT, 8),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(anchor="w")
        ch_box_f = tk.Frame(right_f, bg=CLR["bg"])
        ch_box_f.pack(fill="both", expand=True)
        self._chapters_lb = tk.Listbox(
            ch_box_f,
            height=4,
            width=44,
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            font=(MONO_FONT, 8),
            selectbackground=CLR["accent"],
            selectforeground="white",
            relief="flat",
            highlightthickness=px(1),
            highlightbackground=CLR["border"],
        )
        ch_sb = ttk.Scrollbar(ch_box_f, orient="vertical", command=self._chapters_lb.yview)
        self._chapters_lb.configure(yscrollcommand=ch_sb.set)
        self._chapters_lb.pack(side="left", fill="both", expand=True)
        ch_sb.pack(side="right", fill="y")
        self._chapters_lb.bind("<<ListboxSelect>>", self._on_chapter_select)
        self._chapters_data: list[dict] = []

    # ── Output row ───────────────────────────────────────────────────────────

    def _build_output_row(self, root):
        self._out_var = tk.StringVar()
        out_row = SaveRow(root, t("youtube.save_to"), self._out_var)
        out_row.pack(fill="x", padx=px(20), pady=px(0, 8))
        tk.Button(
            out_row,
            text=t("youtube.open_folder"),
            relief="flat",
            cursor="hand2",
            command=self._open_output_folder,
        ).pack(side="left", padx=px(6, 0))

    # ── Progress row ─────────────────────────────────────────────────────────

    def _build_progress_row(self, root):
        prog_f = tk.Frame(root, bg=CLR["bg"])
        prog_f.pack(fill="x", padx=px(20), pady=px(0, 6))
        self._progress = ttk.Progressbar(
            prog_f, orient="horizontal", mode="determinate", length=px(100)
        )
        self._progress.pack(side="left", fill="x", expand=True, padx=px(0, 10))
        self._prog_status_var = tk.StringVar(value="")
        tk.Label(
            prog_f,
            textvariable=self._prog_status_var,
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["orange"],
            width=38,
            anchor="w",
        ).pack(side="left")

    # ── Action buttons ───────────────────────────────────────────────────────

    def _build_action_buttons(self, root):
        btn_f = tk.Frame(root, bg=CLR["bg"])
        btn_f.pack(pady=px(8))

        self._btn_dl = self.make_render_btn(btn_f, t("youtube.download"), self._download, width=20)
        self._btn_dl.pack(side="left", padx=px(6))

        self._btn_cancel = tk.Button(
            btn_f,
            text=t("youtube.cancel"),
            font=(UI_FONT, 10),
            bg=CLR["red"],
            fg="white",
            height=2,
            width=12,
            cursor="hand2",
            relief="flat",
            state="disabled",
            activebackground=CLR["pink"],
            activeforeground="white",
            command=self._cancel_download,
        )
        self._btn_cancel.pack(side="left", padx=px(6))

        self._btn_batch_toggle = tk.Button(
            btn_f,
            text=t("youtube.batch_mode"),
            font=(UI_FONT, 10),
            bg=CLR["panel"],
            fg=CLR["fg"],
            height=2,
            width=14,
            cursor="hand2",
            relief="flat",
            activebackground=CLR["accent"],
            activeforeground="white",
            command=self._toggle_batch,
        )
        self._btn_batch_toggle.pack(side="left", padx=px(6))

    # ── Batch panel ──────────────────────────────────────────────────────────

    def _build_batch_panel(self, root):
        self._batch_frame = tk.LabelFrame(
            root,
            text=t("youtube.batch_queue"),
            padx=px(10),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            bd=px(1),
            relief="solid",
        )
        # Do NOT pack yet - hidden by default

        # URL textarea
        url_area_f = tk.Frame(self._batch_frame, bg=CLR["bg"])
        url_area_f.pack(fill="x", pady=px(0, 6))
        tk.Label(
            url_area_f,
            text=t("youtube.paste_urls_one_per_line"),
            font=(UI_FONT, 9),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
        ).pack(anchor="w")
        self._batch_text = tk.Text(
            url_area_f,
            height=4,
            width=70,
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            font=(MONO_FONT, 9),
            relief="flat",
            insertbackground=CLR["accent"],
            highlightthickness=px(1),
            highlightbackground=CLR["border"],
        )
        self._batch_text.pack(fill="x")

        batch_ctrl_f = tk.Frame(self._batch_frame, bg=CLR["bg"])
        batch_ctrl_f.pack(fill="x", pady=px(4, 6))
        tk.Button(
            batch_ctrl_f,
            text=t("youtube.add_urls"),
            relief="flat",
            font=(UI_FONT, 9),
            cursor="hand2",
            bg=CLR["panel"],
            fg=CLR["fg"],
            activebackground=CLR["accent"],
            activeforeground="white",
            command=self._batch_add_urls,
        ).pack(side="left", padx=px(0, 6))
        tk.Button(
            batch_ctrl_f,
            text="Clear",
            relief="flat",
            font=(UI_FONT, 9),
            cursor="hand2",
            bg=CLR["panel"],
            fg=CLR["fg"],
            activebackground=CLR["red"],
            activeforeground="white",
            command=self._batch_clear,
        ).pack(side="left")

        # Queue treeview
        tree_f = tk.Frame(self._batch_frame, bg=CLR["bg"])
        tree_f.pack(fill="both", expand=True)
        cols = ("Title", "URL", "Status")
        self._batch_tree = ttk.Treeview(tree_f, columns=cols, show="headings", height=5)
        for col in cols:
            self._batch_tree.heading(col, text=col)
        self._batch_tree.column("Title", width=px(220))
        self._batch_tree.column("URL", width=px(280))
        self._batch_tree.column("Status", width=px(120))
        b_sb = ttk.Scrollbar(tree_f, orient="vertical", command=self._batch_tree.yview)
        self._batch_tree.configure(yscrollcommand=b_sb.set)
        self._batch_tree.pack(side="left", fill="both", expand=True)
        b_sb.pack(side="right", fill="y")

        batch_act_f = tk.Frame(self._batch_frame, bg=CLR["bg"])
        batch_act_f.pack(fill="x", pady=px(6, 0))
        self._btn_dl_all = tk.Button(
            batch_act_f,
            text=t("youtube.download_all"),
            relief="flat",
            font=(UI_FONT, 10, "bold"),
            cursor="hand2",
            bg=CLR["green"],
            fg="white",
            activebackground=CLR["accent"],
            activeforeground="white",
            command=self._batch_download_all,
        )
        self._btn_dl_all.pack(side="left", padx=px(0, 8))
        self._btn_stop_batch = tk.Button(
            batch_act_f,
            text=t("youtube.stop_batch"),
            relief="flat",
            font=(UI_FONT, 10),
            cursor="hand2",
            bg=CLR["red"],
            fg="white",
            activebackground=CLR["pink"],
            activeforeground="white",
            state="disabled",
            command=self._batch_stop,
        )
        self._btn_stop_batch.pack(side="left")

    # ── History panel ────────────────────────────────────────────────────────

    def _build_history_panel(self, root):
        hist_hdr = tk.Frame(root, bg=CLR["panel"])
        hist_hdr.pack(fill="x", padx=px(20), pady=px(8, 0))
        self._hist_toggle_btn = tk.Button(
            hist_hdr,
            text=t("youtube.download_history"),
            font=(UI_FONT, 9, "bold"),
            relief="flat",
            cursor="hand2",
            bg=CLR["panel"],
            fg=CLR["fg"],
            activebackground=CLR["panel"],
            activeforeground=CLR["accent"],
            command=self._toggle_history,
            anchor="w",
        )
        self._hist_toggle_btn.pack(side="left", fill="x", expand=True, padx=px(4), pady=px(4))
        tk.Button(
            hist_hdr,
            text=t("youtube.clear_history"),
            relief="flat",
            font=(UI_FONT, 8),
            cursor="hand2",
            bg=CLR["panel"],
            fg=CLR["fgdim"],
            activebackground=CLR["red"],
            activeforeground="white",
            command=self._clear_history,
        ).pack(side="right", padx=px(6))

        self._hist_body = tk.Frame(root, bg=CLR["bg"])
        self._hist_body.pack(fill="x")

        hist_cols = ("Date", "Title", "Duration", "File")
        self._hist_tree = ttk.Treeview(
            self._hist_body, columns=hist_cols, show="headings", height=5
        )
        for col in hist_cols:
            self._hist_tree.heading(col, text=col)
        self._hist_tree.column("Date", width=px(130))
        self._hist_tree.column("Title", width=px(260))
        self._hist_tree.column("Duration", width=px(80))
        self._hist_tree.column("File", width=px(220))
        hist_sb = ttk.Scrollbar(self._hist_body, orient="vertical", command=self._hist_tree.yview)
        self._hist_tree.configure(yscrollcommand=hist_sb.set)
        self._hist_tree.pack(side="left", fill="both", expand=True)
        hist_sb.pack(side="right", fill="y")
        self._hist_tree.bind("<Double-1>", self._on_history_dclick)
        self._refresh_history_tree()

    # ─────────────────────────────────────────────────────────────────────────
    #  Startup check
    # ─────────────────────────────────────────────────────────────────────────

    def _check_ytdlp(self):
        p = ytdlp_core.ytdlp_path()
        if p:
            tasks.submit(
                partial(ytdlp_core.version, p),
                on_done=partial(self._ytdlp_found, p),
                on_error=lambda _e: self.log_tagged(self.console, "✔ yt-dlp found.", "success"),
                name="yt-dlp version",
            )
        else:
            self.log_tagged(
                self.console,
                "⚠  yt-dlp not found.  Options:\n"
                "   • pip install yt-dlp\n"
                "   • Download yt-dlp.exe → place in bin/ next to ffmpeg.exe\n"
                "   • https://github.com/yt-dlp/yt-dlp/releases\n",
                "warn",
            )

    def on_app_closing(self) -> None:
        """The app is quitting: stop a live-stream recording (the file is kept)."""
        _proc.kill_tree(self._record_proc)

    def _ytdlp_found(self, path: str, version: str) -> None:
        self.log_tagged(self.console, f"✔ yt-dlp found (v{version}).", "success")
        setter = getattr(
            self.winfo_toplevel(), "set_ytdlp_status", None
        )  # the main window's status bar
        if setter is not None:
            setter(version, path)

    # ─────────────────────────────────────────────────────────────────────────
    #  Event handlers - simple
    # ─────────────────────────────────────────────────────────────────────────

    def _on_trim_toggle(self):
        state = "normal" if self._trim_var.get() else "disabled"
        self._start_ent.config(state=state)
        self._end_ent.config(state=state)

    def _open_browser(self):
        url = self._url_var.get().strip()
        if url:
            webbrowser.open(url)

    def _browse_cookies(self):
        p = dialogs.askopenfilename(
            filetypes=[
                (t("youtube.text_cookies"), "*.txt"),
                (t("youtube.all_files"), t("ducker.item_2")),
            ]
        )
        if p:
            self._cookies_var.set(p)

    def _open_output_folder(self):
        p = self._out_var.get().strip()
        if p:
            folder = os.path.dirname(p) if not os.path.isdir(p) else p
            if os.path.isdir(folder):
                from core.hardware import open_in_explorer

                open_in_explorer(folder)

    def _on_paste(self, event):
        self.after(200, self._auto_fetch_after_paste)

    def _auto_fetch_after_paste(self):
        url = self._url_var.get().strip()
        if url and (
            "youtube.com" in url
            or "youtu.be" in url
            or "vimeo.com" in url
            or "twitch.tv" in url
            or "twitter.com" in url
            or "tiktok.com" in url
        ):
            self._on_fetch()

    def _on_format_selected(self, event=None):
        idx = self._format_cb.current()
        if 0 <= idx < len(self._format_options):
            label, _ = self._format_options[idx]
            m = re.search(r"~([\d.]+\s*\w+)", label)
            if m:
                self._size_var.set(f"~{m.group(1)}")
            else:
                self._size_var.set("")

    def _on_chapter_select(self, event=None):
        sel = self._chapters_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self._chapters_data):
            chap = self._chapters_data[idx]
            self._trim_var.set(True)
            self._on_trim_toggle()
            self._start_var.set(_fmt_dur(chap.get("start_time", 0)))
            self._end_var.set(_fmt_dur(chap.get("end_time", 0)))

    def _toggle_batch(self):
        self._batch_visible = not self._batch_visible
        if self._batch_visible:
            self._batch_frame.pack(fill="x", padx=px(20), pady=px(0, 8))
        else:
            self._batch_frame.pack_forget()

    def _toggle_history(self):
        self._history_visible = not self._history_visible
        if self._history_visible:
            self._hist_body.pack(fill="x")
            self._hist_toggle_btn.config(text=t("youtube.download_history"))
        else:
            self._hist_body.pack_forget()
            self._hist_toggle_btn.config(text="▶  " + t("tab.youtube_downloader"))

    def _save_thumbnail(self):
        if self._thumb_photo is None:
            messagebox.showwarning(t("common.warning"), "Fetch video info first.")
            return
        p = dialogs.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")]
        )
        if not p:
            return
        data = getattr(self, "_thumb_bytes", None)
        if not data:
            messagebox.showwarning(t("common.warning"), "Fetch video info first.", parent=self)
            return

        res = _proc.run(
            yto.thumbnail_save_cmd(p),
            timeout=30,
            text=False,
            input=data,
        )
        if res.returncode == 0 and os.path.isfile(p):
            self.log_tagged(self.console, f"✔ Thumbnail saved: {p}", "success")
        else:
            messagebox.showerror(
                t("common.error"),
                "Could not save the thumbnail:\n"
                + (res.stderr or b"").decode("utf-8", "replace")[-300:],
                parent=self,
            )

    # ─────────────────────────────────────────────────────────────────────────
    #  Format filter
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_format_filter(self):
        flt = self._filter_var.get()
        if not self._format_options:
            return

        def shown(label: str) -> bool:
            if flt == "all":
                return True
            if flt == "audio":
                return "audio" in label.lower() or "(video only" not in label.lower()
            return f"{flt}p" in label  # yt-dlp's format names say "1080p"

        filtered = [label for label, _fmt_str in self._format_options if shown(label)]
        if not filtered:
            filtered = [self._format_options[0][0]]
        self._format_cb["values"] = filtered
        self._format_cb.current(0)
        self._size_var.set("")

    # ─────────────────────────────────────────────────────────────────────────
    #  Fetch video info
    # ─────────────────────────────────────────────────────────────────────────

    def _on_fetch(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning(t("common.warning"), "Paste a video URL first.")
            return
        ytdlp = ytdlp_core.ytdlp_path()
        if not ytdlp:
            messagebox.showerror(
                t("common.error"),
                "yt-dlp is required.\n\n"
                "Install:  pip install yt-dlp\n"
                "or place yt-dlp.exe in bin/ next to ffmpeg.exe",
            )
            return
        self._btn_fetch.config(state="disabled", text=t("youtube.fetching"))
        self.log(self.console, f"↳ Fetching info: {url}")
        tasks.submit(
            partial(ytdlp_core.fetch_info, ytdlp, url, playlist=bool(self._playlist_var.get())),
            on_done=self._fetched,
            on_error=self._fetch_failed,
            name="yt-dlp info",
        )

    def _fetched(self, info: dict) -> None:
        self._btn_fetch.config(state="normal", text=t("youtube.fetch_info"))
        self._populate_info(info)

    def _fetch_failed(self, exc: BaseException) -> None:
        self._btn_fetch.config(state="normal", text=t("youtube.fetch_info"))
        if isinstance(exc, _proc.ProcessTimeout):
            msg = "⚠ Timed out fetching video info."
        elif isinstance(exc, ToolError):
            msg = f"⚠ {exc}"
        else:
            msg = f"⚠ Fetch error: {exc}"
        self.log_tagged(self.console, msg, "error")

    def _populate_info(self, info: dict):
        self._video_info = info
        title = info.get("title") or "Unknown"
        dur = info.get("duration") or 0
        channel = info.get("uploader") or info.get("channel") or "-"
        views = info.get("view_count")
        is_live = bool(info.get("is_live"))

        self._title_var.set(title)
        self._dur_var.set(_fmt_dur(dur))
        self._channel_var.set(channel)
        self._views_var.set(f"{views:,}" if isinstance(views, int) else "-")

        # Live badge + record button
        if is_live:
            self._live_var.set("  ⏺ LIVE  ")
            self._live_badge.pack(side="left", padx=px(4))
            self._btn_record.config(state="normal")
            self.log_tagged(self.console, "⏺ Live stream detected.", "warn")
        else:
            self._live_var.set("")
            self._live_badge.pack_forget()
            self._btn_record.config(state="disabled")

        self._populate_formats(info.get("formats", []))
        self._populate_chapters(info.get("chapters") or [])
        self._populate_subtitle_langs(info)

        # Auto-suggest output filename. Refresh on every fetch unless the
        # user has manually edited the field since our last suggestion -
        # otherwise the first download's name would stick for every URL
        # afterwards.
        current = self._out_var.get().strip()
        if (not current) or current == self._last_auto_out:
            safe = "".join(c if c.isalnum() or c in " _-." else "_" for c in title)[:64]
            dl_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            if self._playlist_var.get():
                new_out = os.path.join(dl_dir, "%(title)s.%(ext)s")
            else:
                new_out = os.path.join(dl_dir, safe.strip("_") + ".mp4")
            self._out_var.set(new_out)
            self._last_auto_out = new_out

        if dur:
            self._end_var.set(_fmt_dur(dur))

        thumb_url = info.get("thumbnail")
        if thumb_url:
            tasks.submit(partial(self._load_thumbnail, thumb_url))

        self.log_tagged(self.console, f"✔ {title}  [{_fmt_dur(dur)}]", "success")

    def _populate_formats(self, formats: list):
        if not formats:
            return

        vfmts = sorted(
            [f for f in formats if (f.get("vcodec") or "none") != "none" and f.get("height")],
            key=lambda f: (f.get("height", 0), f.get("fps") or 0),
            reverse=True,
        )

        self._format_options = [
            (
                "⭐  Best available  (auto-select highest quality video + audio)",
                "bestvideo+bestaudio/best",
            ),
        ]
        seen: set = set()
        for f in vfmts:
            fid = f.get("format_id", "?")
            h = f.get("height", 0)
            fps = int(f.get("fps") or 0)
            ext = f.get("ext", "?")
            vc = (f.get("vcodec") or "?").split(".")[0]
            ac = f.get("acodec") or "none"
            fs = f.get("filesize") or f.get("filesize_approx")

            key = (h, fps, ext, vc)
            if key in seen:
                continue
            seen.add(key)

            has_audio = ac and ac != "none"
            fps_s = f"  {fps}fps" if fps else ""
            size_s = f"  ~{fs / 1_000_000:.0f} MB" if fs else ""
            aud_s = "  +audio" if has_audio else "  (video only, audio auto-added)"
            label = f"{h}p{fps_s}  ·  {ext}  ·  {vc}{aud_s}{size_s}  [id {fid}]"
            fmt_str = fid if has_audio else f"{fid}+bestaudio/best"
            self._format_options.append((label, fmt_str))

        self._format_cb["values"] = [o[0] for o in self._format_options]
        self._format_cb.current(0)
        self._size_var.set("")

    def _populate_chapters(self, chapters: list):
        self._chapters_data = chapters
        self._chapters_lb.delete(0, tk.END)
        for ch in chapters:
            st = _fmt_dur(ch.get("start_time", 0))
            title = ch.get("title", "")
            self._chapters_lb.insert(tk.END, f"{st}  {title}")

    def _populate_subtitle_langs(self, info: dict):
        manual_langs = set(info.get("subtitles", {}).keys())
        auto_langs = set(info.get("automatic_captions", {}).keys())
        all_langs = sorted(manual_langs | auto_langs)
        labeled = []
        for lang in all_langs:
            if lang in manual_langs:
                labeled.append(lang)
            else:
                labeled.append(f"(auto) {lang}")
        if labeled:
            self._sub_lang_cb["values"] = labeled
            if "en" in manual_langs:
                self._sub_lang_var.set("en")
            elif labeled:
                self._sub_lang_var.set(labeled[0])

    def _load_thumbnail(self, thumb_url: str):
        """Worker thread: fetch the thumbnail (through the app's network settings)
        and let FFmpeg decode it - Tk shows PNG natively, so no Pillow needed."""
        import base64

        from core import network as _net

        try:
            with _net.urlopen(thumb_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                data = resp.read()
            self._thumb_bytes = data
            tw, th = px(256), px(144)  # the thumbnail box, in screen pixels
            res = _proc.run(
                yto.thumbnail_preview_cmd(tw, th),
                timeout=20,
                text=False,
                input=data,
            )
            if res.returncode != 0 or not res.stdout:
                raise RuntimeError(
                    (res.stderr or b"").decode("utf-8", "replace").strip()[-80:] or "decode failed"
                )
            png = base64.b64encode(res.stdout).decode("ascii")
            self.after(0, lambda: self._set_thumbnail(tk.PhotoImage(master=self, data=png)))
        except Exception as exc:
            logger.debug("_load_thumbnail: best-effort step failed", exc_info=True)
            err = str(exc) or type(exc).__name__
            self.after(0, lambda e=err: self._set_thumbnail_message(f"Thumbnail failed:\n{e[:80]}"))
            self.after(
                0,
                lambda e=err: self.log_tagged(self.console, f"⚠ Thumbnail load error: {e}", "warn"),
            )

    def _set_thumbnail(self, photo):
        self._thumb_photo = photo
        self._thumb_canvas.delete("all")
        self._thumb_canvas.create_image(0, 0, anchor="nw", image=photo)

    def _set_thumbnail_message(self, msg: str):
        """Render a short status message into the thumbnail canvas."""
        try:
            self._thumb_canvas.delete("all")
            self._thumb_canvas.create_text(
                px(128),
                px(72),
                text=msg,
                fill=CLR["fgdim"],
                font=(UI_FONT, 9),
                justify="center",
                width=px(240),
            )
        except Exception:
            logger.debug("_set_thumbnail_message: best-effort step failed", exc_info=True)

    # ─────────────────────────────────────────────────────────────────────────
    #  Preview
    # ─────────────────────────────────────────────────────────────────────────

    def _preview(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning(t("common.warning"), "Paste a video URL first.")
            return
        ytdlp = ytdlp_core.ytdlp_path()
        if not ytdlp:
            self._open_browser()
            return
        self._btn_preview.config(state="disabled", text=t("youtube.loading"))
        self.log(self.console, t("log.youtube.resolving_stream_for_preview"))

        def _worker():
            try:
                from core import network as _net

                playlist = self._playlist_var.get()
                route = _net.tool_proxy(url)
                if route and route.startswith("socks"):
                    self.after(
                        0,
                        self.log_tagged,
                        self.console,
                        "▶ Preview isn't possible through a SOCKS/Tor proxy (the player can't use "
                        "one and would connect around it). Download the video instead.",
                        "warn",
                    )
                    return
                r = _proc.run(
                    yto.stream_url_argv(ytdlp, url, playlist=playlist),
                    timeout=25,
                    env=_net.subprocess_env(url=url),
                )
                lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
                if not lines:
                    self.after(
                        0,
                        lambda: self.log_tagged(
                            self.console, "⚠ Could not resolve stream URL for preview.", "error"
                        ),
                    )
                    return
                stream_url = lines[0]
                self.preview_proc = _proc.spawn(
                    yto.stream_preview_cmd(stream_url),
                    env=_net.subprocess_env(),
                )
                self.after(0, lambda: self.log_tagged(self.console, "▶ Playing in ffplay.", "info"))
            except Exception as exc:
                logger.debug("_worker: best-effort step failed", exc_info=True)
                self.after(
                    0, lambda e=exc: self.log_tagged(self.console, f"⚠ Preview error: {e}", "error")
                )
            finally:
                self.after(
                    0, lambda: self._btn_preview.config(state="normal", text=t("youtube.preview"))
                )

        tasks.submit(_worker)

    # ─────────────────────────────────────────────────────────────────────────
    #  Live recording
    # ─────────────────────────────────────────────────────────────────────────

    def _start_record(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning(t("common.warning"), "Paste a live stream URL first.")
            return
        ytdlp = ytdlp_core.ytdlp_path()
        if not ytdlp:
            messagebox.showerror(t("common.error"), "Install yt-dlp first.")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        out_template = os.path.join(out_dir, f"recording_{ts}.%(ext)s")

        from core import network as _net

        try:
            cmd = yto.live_record_argv(ytdlp, url, out_template)
        except OSError as exc:
            messagebox.showwarning(t("common.warning"), _net.explain_error(exc), parent=self)
            return

        self.log(self.console, f"⏺ Starting live recording → {out_template}")
        self._btn_record.config(state="disabled", text=t("youtube.recording"))
        self._record_start = time.time()

        def _worker():
            try:
                self._record_proc = _proc.spawn(
                    cmd,
                    stdout=_proc.PIPE,
                    stderr=_proc.STDOUT,
                    text=True,
                    bufsize=1,
                    env=_net.subprocess_env(),
                )
                for line in _proc.lines(self._record_proc):
                    stripped = line.rstrip()
                    if stripped:
                        elapsed = int(time.time() - self._record_start)
                        h, r = divmod(elapsed, 3600)
                        m, s = divmod(r, 60)
                        et = f"[{h:02d}:{m:02d}:{s:02d}]"
                        self.after(0, lambda msg=f"{et} {stripped}": self.log(self.console, msg))
            except Exception as exc:
                logger.debug("_worker: best-effort step failed", exc_info=True)
                self.after(
                    0, lambda e=exc: self.log_tagged(self.console, f"⚠ Record error: {e}", "error")
                )
            finally:
                self.after(
                    0, lambda: self._btn_record.config(state="normal", text=t("youtube.record"))
                )
                self._record_proc = None

        tasks.start_service(_worker, name="worker")

    # ─────────────────────────────────────────────────────────────────────────
    #  yt-dlp streaming subprocess
    # ─────────────────────────────────────────────────────────────────────────

    def _stream_ytdlp(
        self,
        cmd: list,
        on_line=None,
        on_progress=None,
        on_done=None,
        url: str = "https://www.youtube.com/",
    ):
        """Run yt-dlp; parse progress lines; call callbacks. Blocks until done.
        Behind a Tor proxy, a block (bot check, 403/429) is retried through
        fresh Tor circuits - as many as set under Settings → Network."""
        rc = self._run_with_fresh_circuits(cmd, url, on_line=on_line, on_progress=on_progress)
        if on_done:
            self.after(0, lambda c=rc: on_done(c))

    def _run_with_fresh_circuits(
        self, cmd: list, url: str, *, on_line=None, on_progress=None, batch: bool = False
    ) -> int:
        from core import network as _net

        tor = _net.tor_like_proxy(cmd)
        retries = _net.tor_retries() if tor else 0
        attempt = 0
        while True:
            rc, tail = self._run_ytdlp_once(
                cmd, url, on_line=on_line, on_progress=on_progress, batch=batch
            )
            if rc != 0 and tor and attempt >= retries and retries and _net.looks_blocked(tail):
                from core import sysproxy

                hint = (
                    "Tails can download directly for this session instead: Settings → Network → Tails."
                    if sysproxy.special_system() == "tails"
                    else "Raise the number of fresh-circuit retries, or choose another route, under Settings → "
                    "Network."
                )
                self.after(
                    0,
                    self.log_tagged,
                    self.console,
                    f"The site still blocked Tor after {retries} fresh circuits. {hint}",
                    "warn",
                )
            if (
                rc == 0
                or tor is None
                or attempt >= retries
                or getattr(self, "_cancel_requested", False)
                or not _net.looks_blocked(tail)
            ):
                return rc
            attempt += 1
            self.after(
                0,
                self.log_tagged,
                self.console,
                f"↻ The site blocked this Tor exit - trying a fresh Tor circuit ({attempt}/{retries})…",
                "warn",
            )
            cmd = yto.with_proxy(cmd, _net.fresh_circuit(tor, attempt))

    def _run_ytdlp_once(
        self, cmd: list, url: str, *, on_line=None, on_progress=None, batch: bool = False
    ):
        """(returncode, last output lines)"""
        from collections import deque

        from core import network as _net
        from core.ytdlp import redact

        tail = deque(maxlen=40)
        prefix = "BATCH CMD: " if batch else "CMD: "
        self.after(
            0, lambda: self.log(self.console, prefix + " ".join(str(c) for c in redact(cmd)))
        )
        try:
            proc = _proc.spawn(
                cmd,
                stdout=_proc.PIPE,
                stderr=_proc.STDOUT,
                text=True,
                bufsize=1,
                env=_net.subprocess_env(url=url),
            )
            self._active_proc = proc
            for line in _proc.lines(proc):
                stripped = line.rstrip()
                if not stripped:
                    continue
                m = _DL_PROGRESS_RE.search(stripped)
                if m and (on_progress or batch):
                    pct = float(m.group(1))
                    size, spd, eta = m.group(2).strip(), m.group(3).strip(), m.group(4).strip()
                    if batch:
                        self.after(
                            0, lambda p_=pct, s_=spd: self._on_progress_update(p_, "-", s_, "-")
                        )
                    elif on_progress is not None:
                        self.after(
                            0, lambda p_=pct, sz=size, sp=spd, et=eta: on_progress(p_, sz, sp, et)
                        )
                else:
                    tail.append(stripped)
                    if on_line is not None:
                        self.after(0, lambda s_=stripped, show=on_line: show(s_))
                    else:
                        self.after(0, lambda s_=stripped: self.log(self.console, s_))
            rc = proc.returncode
        except Exception as exc:
            logger.debug("_run_ytdlp_once: best-effort step failed", exc_info=True)
            self.after(
                0, lambda e=exc: self.log_tagged(self.console, f"⚠ yt-dlp error: {e}", "error")
            )
            rc = 1
        finally:
            self._active_proc = None
        return rc, list(tail)

    def _on_progress_update(self, pct: float, size: str, speed: str, eta: str):
        self._progress["value"] = pct
        self._prog_status_var.set(f"{pct:.1f}%  {size}  {speed}/s  ETA {eta}")

    def _poll_route_banner(self):
        try:
            from core import tails

            on = tails.direct_enabled()
        except Exception:
            logger.debug("_poll_route_banner: best-effort step failed", exc_info=True)
            on = False
        try:
            if on and not self._route_banner.winfo_ismapped():
                outer = getattr(self, "_outer_frame", None)
                self._route_banner.pack(
                    fill="x", **({"before": outer} if outer is not None else {})
                )
            elif not on and self._route_banner.winfo_ismapped():
                self._route_banner.pack_forget()
            self.after(1500, self._poll_route_banner)
        except tk.TclError:
            pass

    def _cancel_download(self):
        # yt-dlp typically spawns ffmpeg (and optionally aria2c) as child
        # processes. proc.terminate() only kills yt-dlp itself, leaving the
        # children running and the download effectively continuing. Kill
        # the whole process tree instead.
        self._cancel_requested = True  # also stops fresh-circuit retries
        proc = self._active_proc
        if proc:
            try:
                self._kill_process_tree(proc)
            except Exception as exc:
                logger.debug("_cancel_download: best-effort step failed", exc_info=True)
                self.log_tagged(self.console, f"⚠ Cancel error: {exc}", "warn")
        self._batch_running = False
        self.log_tagged(self.console, "⏹ Download cancelled.", "warn")
        self._reset_download_ui()

    def _kill_process_tree(self, proc):
        """Stop *proc* and every child it started (never our own process
        group - the old killpg() on POSIX terminated the whole app)."""
        from core import ffmpeg as _ff

        _ff.kill_tree(proc)

    def _reset_download_ui(self):
        self._btn_dl.config(state="normal", text=t("youtube.download"))
        self._btn_cancel.config(state="disabled")
        self._progress["value"] = 0
        self._prog_status_var.set("")

    # ─────────────────────────────────────────────────────────────────────────
    #  Build extra yt-dlp args
    # ─────────────────────────────────────────────────────────────────────────

    def _build_common_ytdlp_args(self) -> list[str]:
        """The tab's own options, after the app-wide network route (later wins)."""
        return yto.common_args(
            yto.Options(
                cookies=self._cookies_var.get().strip(),
                username=self._username_var.get().strip(),
                password=self._password_var.get().strip(),
                rate_limit=self._ratelimit_var.get().strip(),
                proxy=self._proxy_var.get().strip(),
                aria2c=self._use_aria2c_var.get(),
                chapters=self._embed_chapters_var.get(),
                metadata=self._embed_metadata_var.get(),
                sponsorblock=tuple(k for k, v in self._sb_vars.items() if v.get()),
                sponsorblock_mark=self._sb_action_var.get() == "mark",
                custom=self._custom_args_var.get().strip(),
            )
        )

    def _build_trim_args(self) -> list | None:
        """Returns trim args or None. Returns False-y empty list if no trim."""
        if not self._trim_var.get():
            return []
        try:
            s0, s1 = self._section.span()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc))
            return None
        if s1 is None:
            messagebox.showwarning(t("common.warning"), "Say where the section ends.")
            return None
        return yto.section_args(s0, s1)

    # ─────────────────────────────────────────────────────────────────────────
    #  FFmpeg encode command builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_encode_cmd(self, inp: str, out: str, sub_path: str = "") -> list[str]:
        encode = yto.Encode(
            vcodec=self._vcodec_var.get(),
            crf=int(self._crf_var.get()),
            preset=self._preset_var.get(),
            container=self._container_var.get(),
            resolution=self._res.value,
            custom_size=self._custom_res_var.get(),
            acodec=self._acodec_var.get(),
            abitrate=self._abitrate_var.get(),
            sample_rate=self._asamplerate_var.get(),
        )
        return yto.encode_cmd(inp, out, encode, sub_path)

    # ─────────────────────────────────────────────────────────────────────────
    #  Main download entry point
    # ─────────────────────────────────────────────────────────────────────────

    def _download(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning(t("common.warning"), "Paste a video URL first.")
            return
        out = self._out_var.get().strip()
        if not out:
            out = dialogs.asksaveasfilename(
                defaultextension=".mp4",
                filetypes=[
                    ("MP4", "*.mp4"),
                    ("MKV", "*.mkv"),
                    (t("youtube.all_files"), t("ducker.item_2")),
                ],
            )
        if not out:
            return
        self._out_var.set(out)

        ytdlp = ytdlp_core.ytdlp_path()
        if not ytdlp:
            messagebox.showerror(
                t("common.error"),
                "Install:  pip install yt-dlp\nor place yt-dlp.exe in bin/ next to ffmpeg.exe",
            )
            return

        trim_args = self._build_trim_args()
        if trim_args is None:
            return
        try:
            yto.check_route(ytdlp, url)  # offline? strict proxy not set up? say so now
        except OSError as exc:
            from core import network as _net

            messagebox.showwarning(t("common.warning"), _net.explain_error(exc), parent=self)
            return

        mode = self._dl_mode.get()
        self._cancel_requested = False
        self._btn_dl.config(state="disabled", text=t("youtube.downloading"))
        self._btn_cancel.config(state="normal")
        self._progress["value"] = 0
        self._prog_status_var.set("Starting…")

        if mode == "audio":
            tasks.submit(partial(self._run_audio_only, url, out, ytdlp, trim_args))
        elif mode == "encode":
            tasks.submit(partial(self._run_reencode, url, out, ytdlp, trim_args))
        else:
            tasks.submit(partial(self._run_direct_copy, url, out, ytdlp, trim_args))

    # ── Direct copy ──────────────────────────────────────────────────────────

    def _run_direct_copy(self, url: str, out: str, ytdlp: str, trim_args: list):
        idx = self._format_cb.current()
        fmt_str = (
            self._format_options[idx][1]
            if 0 <= idx < len(self._format_options)
            else "bestvideo+bestaudio/best"
        )
        container = self._container_var.get()

        cmd = yto.download_argv(ytdlp, url, fmt_str, container, playlist=self._playlist_var.get())
        if self._dl_subs_var.get():
            cmd += yto.subtitle_args(
                self._sub_lang_var.get().replace("(auto) ", ""),
                self._sub_fmt_var.get(),
                auto=self._sub_auto_var.get(),
                embed=self._embed_subs_var.get(),
            )

        cmd += self._build_common_ytdlp_args()
        cmd += trim_args
        cmd += yto.output_args(out, url)

        self._stream_ytdlp(
            cmd,
            on_line=lambda s: self.log(self.console, s),
            on_progress=self._on_progress_update,
            on_done=lambda rc: self._on_single_done(rc, out),
            url=url,
        )

    # ── Audio only ───────────────────────────────────────────────────────────

    def _run_audio_only(self, url: str, out: str, ytdlp: str, trim_args: list):
        audio_fmt = self._audio_fmt_var.get()
        quality = self._audio_quality_var.get().split()[0]

        # Derive output path with audio extension
        base = os.path.splitext(out)[0]
        audio_out = base + "." + audio_fmt

        cmd = yto.audio_argv(
            ytdlp,
            url,
            audio_fmt,
            quality,
            playlist=self._playlist_var.get(),
            thumbnail=self._embed_thumb_var.get(),
            metadata=self._add_metadata_var.get(),
        )

        cmd += self._build_common_ytdlp_args()
        cmd += trim_args
        cmd += yto.output_args(audio_out, url)

        # Mirror the post-processed audio path back into the box, and treat
        # it as still auto-managed so the next fetched URL refreshes it.
        def _sync_audio_out(p=audio_out):
            self._out_var.set(p)
            self._last_auto_out = p

        self.after(0, _sync_audio_out)
        self._stream_ytdlp(
            cmd,
            on_line=lambda s: self.log(self.console, s),
            on_progress=self._on_progress_update,
            on_done=lambda rc: self._on_single_done(rc, audio_out),
            url=url,
        )

    # ── Re-encode ────────────────────────────────────────────────────────────

    def _run_reencode(self, url: str, out: str, ytdlp: str, trim_args: list):
        tmp = os.path.join(tempfile.gettempdir(), f"_cf_yt_tmp_{os.getpid()}.mkv")

        cmd = yto.download_argv(ytdlp, url, yto.BEST, "mkv", playlist=self._playlist_var.get())
        cmd += self._build_common_ytdlp_args()
        cmd += trim_args
        cmd += yto.output_args(tmp, url)

        # Phase 1: download
        self.after(0, lambda: self.log(self.console, t("log.youtube.downloading_best_quality")))

        def _dl_done(rc: int):
            if rc != 0:
                self.log_tagged(self.console, f"⚠ Download failed (code {rc})", "error")
                self._reset_download_ui()
                return
            self.log(self.console, t("log.youtube.re_encoding_with_ffmpeg"))
            self._btn_dl.config(text=t("youtube.encoding"))
            self._progress["value"] = 0
            self._prog_status_var.set("Encoding…")

            # Phase 2: optional sub burn-in
            sub_path = ""
            if self._dl_subs_var.get() and self._burn_subs_var.get():
                sub_path = self._download_sub_for_burn(url, ytdlp)

            enc_cmd = self._build_encode_cmd(tmp, out, sub_path=sub_path)
            tasks.submit(partial(self._run_ffmpeg_encode, enc_cmd, tmp, out))

        self._stream_ytdlp(
            cmd,
            on_line=lambda s: self.log(self.console, s),
            on_progress=self._on_progress_update,
            on_done=_dl_done,
        )

    def _download_sub_for_burn(self, url: str, ytdlp: str) -> str:
        """Download subtitle to a temp file and return the path."""
        lang = self._sub_lang_var.get().replace("(auto) ", "")
        sub_fmt = self._sub_fmt_var.get()
        tmp_dir = tempfile.mkdtemp(prefix="cf_subs_")
        tmp_base = os.path.join(tmp_dir, "sub")

        from core import network as _net

        cmd = yto.subtitles_only_argv(
            ytdlp,
            url,
            lang,
            sub_fmt,
            tmp_base,
            playlist=self._playlist_var.get(),
            auto=self._sub_auto_var.get(),
        )
        try:
            _proc.run(cmd, timeout=30, text=False, env=_net.subprocess_env())
        except Exception:
            logger.debug("_download_sub_for_burn: best-effort step failed", exc_info=True)
        # Find the downloaded sub file
        for fname in os.listdir(tmp_dir):
            if fname.startswith("sub") and fname.endswith(("." + sub_fmt, ".srt", ".vtt", ".ass")):
                return os.path.join(tmp_dir, fname)
        return ""

    def _run_ffmpeg_encode(self, cmd: list, tmp: str, out: str):
        total_dur = self._video_info.get("duration", 0) or 0

        def _worker(progress_cb, cancel_fn):
            try:
                proc = _proc.spawn(
                    cmd, stdout=_proc.PIPE, stderr=_proc.STDOUT, text=True, bufsize=1
                )
                self._active_proc = proc
                for line in _proc.lines(proc):
                    if cancel_fn():
                        proc.terminate()
                        self._active_proc = None
                        return -1
                    stripped = line.rstrip()
                    if not stripped:
                        continue
                    m = _FFMPEG_TIME_RE.search(stripped)
                    if m and total_dur > 0:
                        elapsed = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                        pct = min(100.0, (elapsed / total_dur) * 100)
                        self.after(
                            0, lambda p=pct, t=elapsed: self._on_encode_progress(p, t, total_dur)
                        )
                    else:
                        progress_cb(stripped)
                rc = proc.returncode
            except Exception as exc:
                logger.debug("_worker: best-effort step failed", exc_info=True)
                self.after(
                    0, lambda e=exc: self.log_tagged(self.console, f"⚠ Encode error: {e}", "error")
                )
                rc = 1
            finally:
                self._active_proc = None
            return rc

        def _on_done(task_id, rc):
            with contextlib.suppress(OSError):
                os.remove(tmp)
            self.after(0, lambda c=rc: self._on_single_done(c, out))

        self.enqueue_render(
            f"YT Encode: {os.path.basename(out)}",
            output_path=out,
            work=_worker,
            hooks=Hooks(
                on_progress=lambda tid, line: self.after(
                    0, lambda s=line: self.log(self.console, s)
                ),
                on_complete=_on_done,
            ),
        )

    def _on_encode_progress(self, pct: float, elapsed: float, total: float):
        self._progress["value"] = pct
        remaining = max(0, total - elapsed)
        h, r = divmod(int(remaining), 3600)
        m, s = divmod(r, 60)
        self._prog_status_var.set(f"Encoding {pct:.1f}%  ETA {h:02d}:{m:02d}:{s:02d}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Post-download
    # ─────────────────────────────────────────────────────────────────────────

    def _on_single_done(self, rc: int, out_path: str):
        self._reset_download_ui()
        if rc == 0:
            self._progress["value"] = 100
            self._prog_status_var.set("✔ Complete!")
            self.log_tagged(self.console, f"✔ Saved: {out_path}", "success")
            title = self._title_var.get()
            duration = self._dur_var.get()
            url = self._url_var.get().strip()
            self._append_history(title, duration, url, out_path)
            self.show_result(0, out_path)
        else:
            self._prog_status_var.set(f"✘ Error (code {rc})")
            self.log_tagged(self.console, f"⚠ Failed (code {rc})", "error")

    # ─────────────────────────────────────────────────────────────────────────
    #  Batch queue
    # ─────────────────────────────────────────────────────────────────────────

    def _batch_add_urls(self):
        raw = self._batch_text.get("1.0", tk.END).strip()
        urls = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        for url in urls:
            self._batch_tree.insert("", tk.END, values=("-", url, "Queued"))
        self._batch_text.delete("1.0", tk.END)

    def _batch_clear(self):
        for item in self._batch_tree.get_children():
            self._batch_tree.delete(item)

    def _batch_download_all(self):
        items = self._batch_tree.get_children()
        if not items:
            messagebox.showwarning(t("common.warning"), "Add URLs to the queue first.")
            return
        ytdlp = ytdlp_core.ytdlp_path()
        if not ytdlp:
            messagebox.showerror(t("common.error"), "Install yt-dlp first.")
            return
        self._batch_running = True
        self._cancel_requested = False
        self._btn_dl_all.config(state="disabled")
        self._btn_stop_batch.config(state="normal")
        tasks.submit(partial(self._batch_worker, items, ytdlp))

    def _batch_status(self, item, status):
        """Set the status column of a batch row (its URL and title stay)."""
        url, title = self._batch_tree.item(item, "values")[:2]
        self._batch_tree.item(item, values=(url, title, status))

    def _batch_worker(self, items, ytdlp: str):
        for item in items:
            if not self._batch_running:
                break
            vals = self._batch_tree.item(item, "values")
            url = vals[1] if vals else ""
            if not url:
                continue

            self.after(0, self._batch_status, item, "Fetching…")

            # Quick title fetch
            title = "-"
            try:
                from core import network as _net

                r = _proc.run(
                    yto.title_argv(ytdlp, url),
                    timeout=20,
                    env=_net.subprocess_env(url=url),
                )
                if r.returncode == 0:
                    title = r.stdout.strip().splitlines()[0][:60]
            except Exception:
                logger.debug("_batch_worker: best-effort step failed", exc_info=True)

            self.after(
                0,
                lambda i=item, t=title: self._batch_tree.item(
                    i, values=(t, self._batch_tree.item(i, "values")[1], "Downloading…")
                ),
            )

            dl_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            out_path = os.path.join(dl_dir, "%(title)s.%(ext)s")

            try:
                cmd = yto.download_argv(
                    ytdlp, url, yto.BEST, self._container_var.get(), playlist=True
                )
            except OSError as exc:
                from core import network as _net

                self.after(
                    0, lambda e=exc: self.log_tagged(self.console, _net.explain_error(e), "error")
                )
                break
            cmd += self._build_common_ytdlp_args()
            cmd += yto.output_args(out_path, url)

            rc = self._run_ytdlp_sync_batch(cmd, url=url)
            status = "✔ Done" if rc == 0 else f"✘ Error ({rc})"

            self.after(
                0,
                lambda i=item, t=title, s=status: self._batch_tree.item(
                    i, values=(t, self._batch_tree.item(i, "values")[1], s)
                ),
            )
            if rc == 0:
                self._append_history(title, "-", url, dl_dir)

        self.after(0, self._batch_finished)

    def _run_ytdlp_sync_batch(self, cmd: list, url: str = "https://www.youtube.com/") -> int:
        return self._run_with_fresh_circuits(cmd, url, batch=True)

    def _batch_stop(self):
        self._batch_running = False
        proc = self._active_proc
        if proc:
            try:
                self._kill_process_tree(proc)
            except Exception:
                logger.debug("_batch_stop: best-effort step failed", exc_info=True)
        self.log_tagged(self.console, "⏹ Batch stopped.", "warn")

    def _batch_finished(self):
        self._batch_running = False
        self._btn_dl_all.config(state="normal")
        self._btn_stop_batch.config(state="disabled")
        self._progress["value"] = 0
        self._prog_status_var.set("Batch complete.")
        self.log_tagged(self.console, "✔ Batch download finished.", "success")

    # ─────────────────────────────────────────────────────────────────────────
    #  History
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_history_tree(self):
        for item in self._hist_tree.get_children():
            self._hist_tree.delete(item)
        for entry in reversed(self._history[-200:]):
            ts = entry.get("ts", "")
            title = entry.get("title", "")
            dur = entry.get("duration", "")
            fpath = entry.get("file", "")
            self._hist_tree.insert("", tk.END, values=(ts, title[:50], dur, fpath), tags=("entry",))

    def _on_history_dclick(self, event):
        sel = self._hist_tree.selection()
        if not sel:
            return
        vals = self._hist_tree.item(sel[0], "values")
        fpath = vals[3] if vals and len(vals) > 3 else ""
        if not fpath:
            return
        from core.hardware import open_in_explorer, reveal_file

        if os.path.isfile(fpath):
            reveal_file(fpath)
        elif os.path.isdir(fpath):
            open_in_explorer(fpath)

    def _clear_history(self):
        if messagebox.askyesno(t("msg.clear_history_title"), t("msg.delete_all_history")):
            self._history = []
            self._save_history()
            self._refresh_history_tree()
