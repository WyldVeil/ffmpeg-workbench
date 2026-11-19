"""
main.py  -  WyldVeil's Media Editor: application shell
Application entry point. Builds the professional dark sidebar, registers
every tab, and manages page navigation, search, and preview cleanup.
"""
import os
import sys

# ── Command line, no window: ``main.py --cli ...`` or the ``...-cli`` executable ──
if __name__ == "__main__" and (sys.argv[1:2] == ["--cli"] or os.path.splitext(
        os.path.basename(sys.executable))[0].lower().endswith("-cli")):
    from core.cli import entry
    sys.exit(entry(sys.argv[1:]))

import tkinter as tk
from tkinter import messagebox, ttk
import ctypes
import subprocess
import threading
import platform
import re

from core import APP_NAME, APP_VERSION
from core.state import state as _app_state
from core.i18n import t, init as _i18n_init

# Initialise i18n before any UI is constructed
_i18n_init()

# ── Platform constants ────────────────────────────────────────────────────────
_IS_MAC = platform.system() == "Darwin"

# ── High-DPI fix (Windows only) ───────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)        # type: ignore[attr-defined]
except Exception:
    pass

# ── Skin engine ───────────────────────────────────────────────────────────────
from core.skins import (apply_skin, apply_default_options,
                         load_skin_name, get_skin, UI_FONT, MONO_FONT)

# ── Tab registry (single source of truth for all tool tabs) ──────────────────
from tabs.registry import TOOLS, PINNED, HIDDEN

# ── Sidebar width ─────────────────────────────────────────────────────────────
SB_WIDTH = 272

# ── Category header → i18n key ────────────────────────────────────────────────
_CAT_I18N = {
    "✂  CUTTING ROOM":       "cat.cutting",
    "📱  SOCIAL & FORMAT":   "cat.social",
    "🔊  AUDIO ENGINEERING": "cat.audio",
    "⚙  TRANSCODER":         "cat.transcoder",
    "🎨  COLOR & VISUALS":   "cat.visuals",
    "🛠  SYSTEM":             "cat.system",
}

def _cat_label(category: str) -> str:
    """Return the translated category header (uppercase) for display."""
    key = _CAT_I18N.get(category.strip())
    if key:
        return t(key).upper()
    return category.strip().upper()

def _tab_label(name: str) -> str:
    """Return the translated display name for a sidebar tool button."""
    import re
    slug = re.sub(r'_+', '_',
        name.lower()
            .replace(' ', '_').replace('-', '_')
            .replace('/', '_').replace('&', '').strip('_'))
    translated = t("tab." + slug)
    # t() returns the key itself when not found - fall back to original name
    return translated if not translated.startswith("tab.") else name


class PlaceholderTab(ttk.Frame):
    """Shown for any tab whose module or class failed to load."""
    def __init__(self, parent, title, error=""):
        super().__init__(parent)
        tk.Label(self, text="\u26a0  {}".format(title),
                 font=(UI_FONT, 20, "bold"), fg="#C0392B").pack(pady=(120, 10))
        msg = ("This tool could not be loaded:\n{}\n\nThe full details were written "
               "to the log file (Help \u203a Open log folder).").format(error) if error \
            else t("app.under_construction")
        tk.Label(self, text=msg, font=(UI_FONT, 11), fg="#808080",
                 wraplength=700, justify="center").pack()


class App(tk.Tk):
    def __init__(self):
        # className sets WM_CLASS so Linux desktops match the .desktop file.
        super().__init__(className="wyldveil-media-editor")
        self._apply_ui_scale()
        try:
            from core.paths import resource_path
            self._icons = [tk.PhotoImage(master=self, file=resource_path("assets", "icons", f"app_{n}.png"))
                           for n in (256, 128, 64, 48, 32, 16)]
            self.iconphoto(True, *self._icons)
        except tk.TclError:
            pass

        # Thread-safe UI dispatch, logging and crash reporting come first so
        # that everything constructed below can rely on them.
        from core import encoders as _encoders, logs as _logs, ui as _ui
        _ui.install(self)
        _logs.setup(debug="--debug" in sys.argv)
        _logs.install_excepthooks(self)
        _encoders.detect_in_background()

        self.title(f"{APP_NAME}  \u00b7  {APP_VERSION}")
        self.geometry("1480x920")
        self.minsize(1100, 700)

        # ── Global state ──────────────────────────────────────────────────────
        self.debug_mode = tk.BooleanVar(value=False)
        self._ytdlp_path = None
        # Latest known versions (set by background fetches). Used by the
        # status-bar click handlers to decide whether to offer an update or
        # just confirm the local copy is current.
        self._ytdlp_latest_version:  str | None = None
        self._ffmpeg_local_version:  str | None = None
        self._ytdlp_local_version:   str | None = None

        # ── Load skin FIRST - cascade options before any tab widget is created ─
        self._skin_data = get_skin(load_skin_name())
        apply_default_options(self, self._skin_data)

        # ── ttk Style ─────────────────────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("clam")
        from core.skins import _apply_ttk_styles
        _apply_ttk_styles(self._skin_data)

        # ── Status bar (at the very bottom, before content) ───────────────────
        self._build_status_bar()

        # ── Root frame that holds sidebar + content ───────────────────────────
        root_frame = tk.Frame(self, bg=self._skin_data["sb_bg"])
        root_frame.pack(fill="both", expand=True)

        self.sidebar_container = tk.Frame(
            root_frame, bg=self._skin_data["sb_bg"], width=SB_WIDTH)
        self.sidebar_container.pack(side="left", fill="y")
        self.sidebar_container.pack_propagate(False)

        # 1-pixel separator between sidebar and content
        tk.Frame(root_frame,
                 bg=self._skin_data.get("border", "#2C2C2C"),
                 width=1).pack(side="left", fill="y")

        self.content_container = tk.Frame(
            root_frame, bg=self._skin_data["content"])
        self.content_container.pack(side="right", fill="both", expand=True)

        # Persistent top bar with a Home button - always visible on top of
        # every tab so the user can return to the launch screen at any time.
        skin = self._skin_data
        self._content_top_bar = tk.Frame(
            self.content_container, bg=skin["panel"], height=32)
        self._content_top_bar.pack(side="top", fill="x")
        self._content_top_bar.pack_propagate(False)

        self._home_btn = tk.Button(
            self._content_top_bar, text="🏠  Home",
            font=(UI_FONT, 9, "bold"),
            bg=skin["panel"], fg=skin["fg"],
            activebackground=skin.get("sb_hover", "#2A2A2A"),
            activeforeground="#FFFFFF",
            relief="flat", cursor="hand2",
            bd=0, padx=14, pady=4,
            command=lambda: self.show_page("Home"))
        self._home_btn.pack(side="left", padx=10, pady=4)

        tk.Frame(self.content_container,
                 bg=skin.get("border", "#2C2C2C"),
                 height=1).pack(side="top", fill="x")

        # Inner content area where the active tab is actually packed.
        # Tabs were previously parented to content_container directly;
        # now they go inside content_area which leaves the top bar
        # untouched between page swaps.
        self._content_area = tk.Frame(
            self.content_container, bg=skin["content"])
        self._content_area.pack(side="top", fill="both", expand=True)

        self.pages           = {}   # name → tab instance (None until first visit)
        self._tab_classes    = {}   # name → tab class (for lazy init)
        self.current_page    = None
        self._btn_refs       = {}
        self._indicator_refs = {}
        self._tool_rows      = []   # (name_lower, row_frame) for search
        self._cat_sections   = {}   # category → (hdr_row, items_frame, expanded_var)

        self._build_menubar()
        self._build_sidebar()

        # ── Mousewheel ────────────────────────────────────────────────────────
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>",   self._on_mousewheel)
        self.bind_all("<Button-5>",   self._on_mousewheel)

        # Commit the sidebar scrollregion after the window is fully laid out.
        # <Configure> bindings fire during construction before the canvas is
        # mapped, so bbox("all") may return None at that point.
        def _fix_scrollregion():
            try:
                self._inner.update_idletasks()
                self._sidebar_canvas.configure(
                    scrollregion=self._sidebar_canvas.bbox("all"))
            except Exception:
                pass
        self.after_idle(_fix_scrollregion)

        # ── Apply full skin ───────────────────────────────────────────────────
        apply_skin(
            self,
            self.sidebar_container,
            self.content_container,
            self._btn_refs,
            self._inner,
            indicator_refs=self._indicator_refs,
        )

        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        # ── Initialise global render queue ────────────────────────────────────
        from core.queue_manager import RenderQueueManager
        _qmgr = RenderQueueManager.get_instance()
        _qmgr.set_tk_root(self)
        _qmgr.register_update_callback(self._update_queue_status)

        self.show_page("Home")

        # ── Drag-and-drop file loading ────────────────────────────────────────
        self._init_drag_drop()

        # ── Probe FFmpeg in background ────────────────────────────────────────
        threading.Thread(target=self._check_ffmpeg, daemon=True).start()

        # ── Startup chime (optional) ──────────────────────────────────────────
        from core import settings as _settings
        if _settings.get("play_startup_sound", True) and not os.environ.get("CROSSFADER_NO_SOUND"):
            threading.Thread(target=self._play_intro_sound, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    #  Status bar & Updates
    # ─────────────────────────────────────────────────────────────────────────
    def _build_status_bar(self):
        skin = self._skin_data
        SB   = skin["sb_bg"]
        FDM  = skin["fgdim"]
        BDR  = skin.get("border", "#2C2C2C")

        bar = tk.Frame(self, bg=SB, height=26)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=BDR, height=1).pack(side="top", fill="x")
        self._status_bar_frame = bar

        inner = tk.Frame(bar, bg=SB)
        inner.pack(fill="both", expand=True, padx=12)

        self._status_dot = tk.Label(inner, text="●", bg=SB,
                                     fg=skin["green"], font=(UI_FONT, 7))
        self._status_dot.pack(side="left", pady=5)

        self._status_lbl = tk.Label(inner, text="  " + t("app.status.ready"),
                                     bg=SB, fg=FDM, font=(UI_FONT, 8))
        self._status_lbl.pack(side="left")

        tk.Frame(inner, bg=BDR, width=1).pack(side="left", fill="y", pady=5, padx=10)

        self._status_tool = tk.Label(inner, text="", bg=SB, fg=FDM, font=(UI_FONT, 8))
        self._status_tool.pack(side="left")

        # System Updates Section (Right Side)
        tk.Frame(inner, bg=BDR, width=1).pack(side="right", fill="y", pady=5, padx=10)
        
        # FFmpeg Status (Clickable for global updates)
        self._status_ffmpeg = tk.Label(inner, text=t("app.status.ffmpeg_checking"),
                                        bg=SB, fg=FDM, font=(UI_FONT, 8, "bold"), cursor="hand2")
        self._status_ffmpeg.pack(side="right", padx=6)
        self._status_ffmpeg.bind("<Button-1>", lambda e: self._prompt_ffmpeg_update())

        tk.Frame(inner, bg=BDR, width=1).pack(side="right", fill="y", pady=5, padx=10)

        # YT-DLP Status (Hidden by default, shown on YouTube tab)
        self._status_ytdlp = tk.Label(inner, text="", bg=SB, fg=skin["accent"],
                                      font=(UI_FONT, 8, "bold"), cursor="hand2")
        # Pack hidden initially
        self._status_ytdlp.bind("<Button-1>", lambda e: self._update_ytdlp_process())

        # ── Render progress bar ───────────────────────────────────────────────
        tk.Frame(inner, bg=BDR, width=1).pack(side="left", fill="y", pady=5, padx=10)

        self._progress_frame = tk.Frame(inner, bg=SB)
        # Hidden by default - shown during renders
        self._progress_bar = ttk.Progressbar(
            self._progress_frame, mode="determinate",
            length=140, maximum=100)
        self._progress_bar.pack(side="left", pady=6)
        self._progress_pct = tk.Label(self._progress_frame, text="",
                                       bg=SB, fg=skin["accent"],
                                       font=(MONO_FONT, 8, "bold"))
        self._progress_pct.pack(side="left", padx=(4, 0))
        self._render_duration = 0.0   # total duration for progress calc

        # ── Queue Status indicator (always visible) ───────────────────────────
        tk.Frame(inner, bg=BDR, width=1).pack(side="left", fill="y", pady=5, padx=10)
        self._queue_status_lbl = tk.Label(
            inner, text=t("app.queue.idle"),
            bg=SB, fg=FDM, font=(UI_FONT, 8, "bold"), cursor="hand2")
        self._queue_status_lbl.pack(side="left", padx=4)
        self._queue_status_lbl.bind(
            "<Button-1>", lambda e: self.show_page("Encode Queue"))

    def _play_intro_sound(self):
        """Generate and play a soft startup chime using stdlib only (no extra deps).

        A short C-major arpeggio (C5→E5→G5→C6) followed by a sustained dyad,
        rendered as a 44.1 kHz 16-bit mono WAV in memory and played asynchronously.
        """
        import wave, struct, math, io
        RATE = 44100

        def _note(freq, dur, vol=0.28):
            n = int(RATE * dur)
            out = []
            for i in range(n):
                t = i / RATE
                # ADSR-style: fast attack, smooth exponential decay
                env = (1.0 - math.exp(-t * 55.0)) * math.exp(-t * 5.5)
                # Fundamental + soft 2nd harmonic for warmth
                s = math.sin(2 * math.pi * freq * t) + 0.18 * math.sin(4 * math.pi * freq * t)
                out.append(env * vol * s / 1.18)
            return out

        # Ascending arpeggio: C5 E5 G5 C6
        samples: list = []
        for freq, dur in [(523.25, 0.13), (659.25, 0.13), (783.99, 0.14), (1046.50, 0.17)]:
            samples.extend(_note(freq, dur))

        # Soft sustained dyad (C5 + G5) as a gentle tail
        n_tail = int(RATE * 0.45)
        for i in range(n_tail):
            t = i / RATE
            env = (1.0 - math.exp(-t * 18.0)) * math.exp(-t * 3.2)
            s = (math.sin(2 * math.pi * 523.25 * t) +
                 math.sin(2 * math.pi * 783.99 * t)) * 0.5
            samples.append(env * 0.22 * s)

        # Encode as 16-bit mono WAV
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(b''.join(
                struct.pack('<h', max(-32767, min(32767, int(s * 32767))))
                for s in samples
            ))
        wav_data = buf.getvalue()

        try:
            if sys.platform == 'win32':
                import winsound
                winsound.PlaySound(wav_data, winsound.SND_MEMORY)
            elif sys.platform == 'darwin':
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                    f.write(wav_data)
                    tmp = f.name
                subprocess.run(['afplay', tmp], timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try: os.remove(tmp)
                except Exception: pass
        except Exception:
            pass

    # ── Version helpers (network) ────────────────────────────────────────────
    # ── Tool versions & updates ──────────────────────────────────────────────
    @staticmethod
    def _vtuple(v):
        """'9.0.2' / '2026.10.26' -> (9, 0, 2) / (2026, 10, 26); None if not a release."""
        m = re.match(r"^v?(\d+(?:\.\d+)+)", str(v or "").strip())
        return tuple(int(x) for x in m.group(1).split(".")) if m else None

    def _ui(self, fn, *args, **kwargs):
        from core import ui as _ui
        _ui.call_soon(fn, *args, **kwargs)

    def _apply_ui_scale(self):
        """Honour HiDPI scaling on Linux, where Tk ignores the desktop's scale
        factor (Windows uses per-monitor DPI awareness).  The ``ui_scale``
        setting (e.g. 1.5) overrides detection."""
        from core import settings as _settings
        scale = None
        try:
            scale = float(_settings.get("ui_scale", "auto"))
        except (TypeError, ValueError):
            scale = None
        if scale is None and sys.platform.startswith("linux"):
            for var in ("GDK_SCALE", "QT_SCALE_FACTOR"):
                try:
                    val = float(os.environ.get(var, ""))
                except ValueError:
                    continue
                if val > 1.0:
                    scale = val
                    break
            if scale is None:
                try:
                    out = subprocess.run(["xrdb", "-query"], capture_output=True, text=True,
                                         timeout=3).stdout
                    m = re.search(r"Xft\.dpi:\s*([\d.]+)", out)
                    if m and float(m.group(1)) > 100:
                        scale = float(m.group(1)) / 96.0
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if scale and 0.75 <= scale <= 4 and abs(scale - 1.0) > 0.01:
            self.tk.call("tk", "scaling", 96.0 * scale / 72.0)
            self._ui_scale = scale
        else:
            self._ui_scale = 1.0

    def _check_ffmpeg(self):
        """Background: describe the FFmpeg in use (pinned - no online check)."""
        from core import updater
        from core.ffmpeg import version_of
        from core.hardware import find_binary
        skin = self._skin_data
        path = find_binary("ffmpeg")
        if not path:
            self._ffmpeg_local_version = None
            self._ui(self._status_ffmpeg.config, text=t("app.status.ffmpeg_missing_click_install"),
                     fg=skin["red"], cursor="hand2")
            return
        local = version_of(path)
        managed = updater.is_app_managed(path)
        self._ffmpeg_local_version, self._ffmpeg_managed = local, managed
        self._ui(self._status_ffmpeg.config, text=f"FFmpeg {local} ({'bundled' if managed else 'your own copy'})",
                 fg=skin["fgdim"], cursor="hand2")

    def _prompt_ffmpeg_update(self):
        from core.hardware import find_binary
        path = find_binary("ffmpeg") or "not found"
        if getattr(self, "_ffmpeg_managed", True):
            msg = (f"This app ships FFmpeg {self._ffmpeg_local_version or ''}, the build every tool is tested "
                   "with. It is updated together with the app, so a new FFmpeg can never break a tool.\n\n"
                   f"In use: {path}")
        else:
            msg = (f"A different FFmpeg ({self._ffmpeg_local_version or 'unknown version'}) is in use:\n{path}\n\n"
                   "Tools are tested with the bundled FFmpeg; choose 'Bundled' under Advanced Settings › FFmpeg "
                   "if something doesn't work.")
        messagebox.showinfo("FFmpeg", msg, parent=self)

    # ── yt-dlp status / update ───────────────────────────────────────────────
    def set_ytdlp_status(self, version: str, bin_path: str, force: bool = False):
        """Called by the Video Downloader once it has resolved a yt-dlp binary
        (force: the person asked, so check even if automatic checks are off)."""
        self._ytdlp_path = bin_path
        self._ytdlp_local_version = version
        self._status_ytdlp.config(text=f"yt-dlp {version} (checking…)",
                                  fg=self._skin_data["fgdim"], cursor="watch")
        threading.Thread(target=self._compare_ytdlp_versions, args=(version, force), daemon=True).start()

    def _compare_ytdlp_versions(self, local: str, force: bool = False):
        from core import updater
        skin = self._skin_data
        latest = updater.latest_ytdlp_release(force=force)
        self._ytdlp_latest_version = latest
        lt, nt = self._vtuple(local), self._vtuple(latest)
        if nt is None or lt is None:
            self._ui(self._status_ytdlp.config, text=f"yt-dlp {local}", fg=skin["fgdim"], cursor="hand2")
        elif nt > lt:
            self._ui(self._status_ytdlp.config, text=f"yt-dlp {local} → {latest} (click to update)",
                     fg=skin["orange"], cursor="hand2")
        else:
            self._ui(self._status_ytdlp.config, text=f"yt-dlp {local} (up to date)",
                     fg=skin["green"], cursor="arrow")

    def _update_ytdlp_process(self):
        from core import updater
        lt = self._vtuple(self._ytdlp_local_version)
        nt = self._vtuple(self._ytdlp_latest_version)
        if lt and nt and nt <= lt:
            messagebox.showinfo(t("msg.ytdlp_update_title"),
                                f"yt-dlp {self._ytdlp_local_version} is already up to date.", parent=self)
            return
        if self._ytdlp_path and not updater.is_app_managed(self._ytdlp_path):
            if not messagebox.askyesno(
                    t("msg.ytdlp_update_title"),
                    "This yt-dlp ({p}) was installed by your system.\n\nDownload the latest official "
                    "yt-dlp for this app instead? (Your system copy is left untouched.)".format(
                        p=self._ytdlp_path), parent=self):
                return
        elif not messagebox.askyesno(t("msg.ytdlp_update_title"), t("msg.ytdlp_update_confirm"), parent=self):
            return
        self._status_ytdlp.config(text=t("app.status.ytdlp_updating"), fg=self._skin_data["orange"])
        self.set_status(t("app.status.ytdlp_updating"))

        def _task():
            ok = updater.install_ytdlp(lambda msg: self._ui(self.set_status, msg))
            if ok:
                from core.hardware import find_binary
                from core.ffmpeg import capture
                path = find_binary("yt-dlp")
                try:
                    ver = capture([path, "--version"], timeout=60).stdout.strip()
                except Exception:
                    ver = "?"
                self._ui(self.set_ytdlp_status, ver, path)
                self._ui(self.set_status, t("msg.ytdlp_update_complete"), self._skin_data["green"])
            else:
                self._ui(self.set_status, t("msg.ytdlp_update_failed"), self._skin_data["red"])
                self._ui(self._status_ytdlp.config, text=t("app.status.ytdlp_update_failed"))

        threading.Thread(target=_task, daemon=True).start()

    def _update_queue_status(self):
        """Refresh the queue label, progress bar and ETA from the render queue."""
        try:
            from core.queue_manager import RenderQueueManager
            q = RenderQueueManager.get_instance()
            tasks = q.get_all_tasks()
            skin = self._skin_data
            active = [x for x in tasks if x.status == "active"]
            pending = [x for x in tasks if x.status == "pending"]
            done = sum(1 for x in tasks if x.status == "done")
            failed = sum(1 for x in tasks if x.status == "failed")
            cancelled = sum(1 for x in tasks if x.status == "cancelled")
            if active or pending:
                parts = []
                if active:
                    parts.append(f"{len(active)} active")
                if pending:
                    parts.append(f"{len(pending)} pending")
                self._queue_status_lbl.config(text=t("app.queue.busy", n=" | ".join(parts)), fg=skin["orange"])
                self._progress_frame.pack(side="left", padx=(4, 0))
                frac = q.overall_progress()
                if frac is None:
                    self._progress_bar.config(mode="indeterminate")
                    if not getattr(self, "_indeterminate", False):
                        self._progress_bar.start(12)
                        self._indeterminate = True
                    self._progress_pct.config(text="")
                else:
                    if getattr(self, "_indeterminate", False):
                        self._progress_bar.stop()
                        self._indeterminate = False
                    self._progress_bar.config(mode="determinate")
                    self._progress_bar["value"] = frac * 100
                    eta = max((x.eta or 0) for x in active) if active else 0
                    eta_txt = ""
                    if eta and eta > 1:
                        m, sec = divmod(int(eta), 60)
                        eta_txt = f"  ≈{m}:{sec:02d} left" if m else f"  ≈{sec}s left"
                    self._progress_pct.config(text=f"{frac * 100:.0f}%{eta_txt}")
                self._schedule_queue_refresh()
            else:
                self._hide_progress()
                bits = []
                if done:
                    bits.append(f"{done} done ✓")
                if failed:
                    bits.append(f"{failed} failed")
                if cancelled:
                    bits.append(f"{cancelled} cancelled")
                if bits:
                    self._queue_status_lbl.config(text="Queue: " + " | ".join(bits),
                                                  fg=skin["red"] if failed else skin["green"])
                else:
                    self._queue_status_lbl.config(text=t("app.queue.idle"), fg=skin["fgdim"])
        except Exception:
            import logging
            logging.getLogger("crossfader.shell").exception("queue status refresh failed")

    def _schedule_queue_refresh(self):
        """While jobs run, refresh the bar a few times a second (ETA ticks down)."""
        if getattr(self, "_queue_refresh_pending", False):
            return
        self._queue_refresh_pending = True

        def _tick():
            self._queue_refresh_pending = False
            self._update_queue_status()
        self.after(400, _tick)

    def _hide_progress(self):
        try:
            if getattr(self, "_indeterminate", False):
                self._progress_bar.stop()
                self._indeterminate = False
            self._progress_frame.pack_forget()
            self._progress_bar["value"] = 0
            self._progress_pct.config(text="")
        except Exception:
            pass

    def update_render_progress(self, line):
        """Kept for older tabs; progress now comes from the render queue."""
        return None

    def set_status(self, message, color=None):
        skin = self._skin_data
        c = color or skin["fgdim"]
        try:
            self._status_lbl.config(text=f"  {message}", fg=c)
            if any(w in message for w in ("Processing", "Rendering", "Encoding", "Updating", "Downloading")):
                self._status_dot.config(fg=skin["orange"])
            elif any(w in message.lower() for w in ("done", "complete", "success")):
                self._status_dot.config(fg=skin["green"])
            elif any(w in message.lower() for w in ("error", "failed", "fail")):
                self._status_dot.config(fg=skin["red"])
            else:
                self._status_dot.config(fg=skin["green"])
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Skin
    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_skin_cache(self):
        self._skin_data = get_skin(load_skin_name())

    def _apply_skin_live(self, name):
        from core.skins import save_skin_name
        save_skin_name(name)
        self._refresh_skin_cache()
        skin = self._skin_data
        apply_default_options(self, skin)
        apply_skin(self, self.sidebar_container, self.content_container,
                   self._btn_refs, self._inner, indicator_refs=self._indicator_refs)
        self._skin_menu_var.set(name)
        if self.current_page and self.current_page in self._indicator_refs:
            try:
                self._indicator_refs[self.current_page].config(bg=skin["accent"])
            except Exception:
                pass
        # Recolour status bar
        try:
            SB = skin["sb_bg"]
            for w in self._status_bar_frame.winfo_children():
                try:
                    w.config(bg=SB)
                    for c in w.winfo_children():
                        try: c.config(bg=SB)
                        except Exception: pass
                except Exception:
                    pass
            self._status_bar_frame.config(bg=SB)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Mousewheel
    # ─────────────────────────────────────────────────────────────────────────
    def _on_mousewheel(self, event):
        # Cross-platform delta mapping - 3 units per notch matches typical
        # desktop scroll speed (Windows default is 3 lines per notch).
        if event.num == 4:
            delta = 3
        elif event.num == 5:
            delta = -3
        elif _IS_MAC:
            delta = -event.delta * 3
        else:
            delta = int(-event.delta / 120) * 3

        # If the cursor is anywhere inside the sidebar, scroll the sidebar canvas
        # directly - the parent-walk below can miss it when tabs contain canvases.
        try:
            sc = self.sidebar_container
            if (sc.winfo_rootx() <= event.x_root < sc.winfo_rootx() + sc.winfo_width()
                    and sc.winfo_rooty() <= event.y_root < sc.winfo_rooty() + sc.winfo_height()):
                self._sidebar_canvas.yview_scroll(delta, "units")
                return
        except Exception:
            pass

        # Content area: walk up the widget tree to find the nearest scrollable canvas.
        widget = event.widget
        while widget is not None:
            if isinstance(widget, tk.Canvas):
                try:
                    if widget.cget("yscrollcommand"):
                        widget.yview_scroll(delta, "units")
                        return
                except Exception:
                    pass
            try:
                widget = widget.master
            except Exception:
                break

    # ─────────────────────────────────────────────────────────────────────────
    #  Menu bar
    # ─────────────────────────────────────────────────────────────────────────
    def _build_menubar(self):
        skin = self._skin_data
        mb = tk.Menu(self, bg=skin["panel"], fg=skin["fg"],
                     activebackground=skin["accent"],
                     activeforeground="#FFFFFF",
                     relief="flat", bd=0)
        self.config(menu=mb)

        def _mksub():
            return tk.Menu(mb, tearoff=0,
                           bg=skin["panel"], fg=skin["fg"],
                           activebackground=skin["accent"],
                           activeforeground="#FFFFFF",
                           relief="flat", bd=0)

        m_file = _mksub()
        mb.add_cascade(label="File", menu=m_file)
        m_file.add_command(label="Home",
                           command=lambda: self.show_page("Home"))
        m_file.add_separator()
        m_file.add_command(label="Open video file…",
                           command=self._menu_open_file, accelerator="Ctrl+O")
        m_file.add_separator()
        self._recent_menu = _mksub()
        m_file.add_cascade(label="Recent Files", menu=self._recent_menu)
        self._rebuild_recent_menu()
        m_file.add_separator()
        m_file.add_command(label="Open output folder…",
                           command=self._menu_open_output_folder)
        m_file.add_separator()
        m_file.add_command(label="Exit", command=self._on_closing,
                           accelerator="Alt+F4")

        m_edit = _mksub()
        mb.add_cascade(label="Edit", menu=m_edit)
        m_edit.add_command(label="Quick Settings",
                           command=lambda: self.show_page("Settings"))
        m_edit.add_command(label="Advanced Settings",
                           command=lambda: self.show_page("Advanced Settings"))
        m_edit.add_separator()
        m_edit.add_checkbutton(label="Debug mode", variable=self.debug_mode)

        # Tool menus are generated from the registry (translated, always complete).
        for category, items in TOOLS.items():
            m_cat = _mksub()
            mb.add_cascade(label=_cat_label(category).title(), menu=m_cat)
            for name, _cls in items:
                m_cat.add_command(label=_tab_label(name), command=lambda n=name: self.show_page(n))

        m_view = _mksub()
        mb.add_cascade(label="View", menu=m_view)

        m_skins = tk.Menu(m_view, tearoff=0,
                          bg=skin["panel"], fg=skin["fg"],
                          activebackground=skin["accent"],
                          activeforeground="#FFFFFF")
        m_view.add_cascade(label="Theme", menu=m_skins)
        from core.skins import SKINS
        self._skin_menu_var = tk.StringVar(value=load_skin_name())
        for sn in SKINS:
            m_skins.add_radiobutton(label=sn, variable=self._skin_menu_var,
                                     value=sn,
                                     command=lambda n=sn: self._apply_skin_live(n))

        m_view.add_separator()
        m_view.add_command(label="Jump to top of sidebar",
                           command=self._sidebar_scroll_top)
        m_view.add_command(label="Expand all categories",
                           command=lambda: self._set_all_categories(True))
        m_view.add_command(label="Collapse all categories",
                           command=lambda: self._set_all_categories(False))

        m_help = tk.Menu(mb, tearoff=0, bg=skin["panel"], fg=skin["fg"],
                         activebackground=skin["accent"], activeforeground="#FFFFFF")
        mb.add_cascade(label="Help", menu=m_help)
        m_help.add_command(label="Keyboard Shortcuts", command=self._menu_shortcuts)
        m_help.add_command(label="System Info", command=self._menu_system_info)
        m_help.add_command(label="Check for tool updates", command=self._menu_check_updates)
        m_help.add_command(label="Open log folder", command=self._menu_open_logs)
        m_help.add_separator()
        m_help.add_command(label=f"About {APP_NAME}", command=self._menu_about)

        self._menubar = mb
        self.bind("<Control-o>", lambda e: self._menu_open_file())

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        self.bind("<space>",     self._kb_play_pause)
        self.bind("i",          self._kb_set_in)
        self.bind("o",          self._kb_set_out)
        self.bind("<Home>",     self._kb_go_start)
        self.bind("<End>",      self._kb_go_end)
        self.bind("<Left>",     self._kb_nudge_left)
        self.bind("<Right>",    self._kb_nudge_right)
        self.bind("<Control-s>", self._kb_render)
        self.bind("<Escape>",   self._kb_stop_preview)

    # ── Keyboard shortcut handlers ───────────────────────────────────────────

    def _kb_focused_on_entry(self):
        """True when focus is in a text input or on a button (don't hijack typing
        or Space-to-press)."""
        try:
            w = self.focus_get()
        except (KeyError, tk.TclError):
            return False
        return isinstance(w, (tk.Entry, tk.Text, tk.Spinbox, tk.Listbox, ttk.Entry, ttk.Combobox,
                              ttk.Spinbox, tk.Button, ttk.Button, tk.Checkbutton, ttk.Checkbutton,
                              tk.Radiobutton, ttk.Radiobutton, tk.Scale, ttk.Scale))

    def _kb_play_pause(self, event=None):
        if self._kb_focused_on_entry():
            return
        page = self.pages.get(self.current_page)
        if not page:
            return
        # Try launching preview at current playhead
        if hasattr(page, "preview_proc") and page.preview_proc:
            try:
                page.preview_proc.terminate()
                page.preview_proc = None
            except Exception:
                pass
            return
        # Launch preview
        fp = getattr(page, "file_path", "")
        if not fp or not os.path.isfile(fp):
            return
        start = 0.0
        if hasattr(page, "_timeline"):
            start = page._timeline.get_playhead()
        elif hasattr(page, "_scrub"):
            try:
                start = float(page._scrub.get())
            except (ValueError, tk.TclError):
                pass
        from core.hardware import launch_preview
        page.preview_proc = launch_preview(fp, start_time=start)

    def _kb_set_in(self, event=None):
        if self._kb_focused_on_entry():
            return
        page = self.pages.get(self.current_page)
        if not page:
            return
        if hasattr(page, "_timeline") and page._timeline._show_handles:
            tl = page._timeline
            tl._start = tl._playhead
            tl._draw()
            tl._fire_change()
        elif hasattr(page, "_start_var") and hasattr(page, "_scrub"):
            try:
                from tabs.cutting.trimmer import fmt_time
                page._start_var.set(fmt_time(float(page._scrub.get())))
                if hasattr(page, "_apply_typed_times"):
                    page._apply_typed_times()
            except Exception:
                pass

    def _kb_set_out(self, event=None):
        if self._kb_focused_on_entry():
            return
        page = self.pages.get(self.current_page)
        if not page:
            return
        if hasattr(page, "_timeline") and page._timeline._show_handles:
            tl = page._timeline
            tl._end = tl._playhead
            tl._draw()
            tl._fire_change()
        elif hasattr(page, "_end_var") and hasattr(page, "_scrub"):
            try:
                from tabs.cutting.trimmer import fmt_time
                page._end_var.set(fmt_time(float(page._scrub.get())))
                if hasattr(page, "_apply_typed_times"):
                    page._apply_typed_times()
            except Exception:
                pass

    def _kb_go_start(self, event=None):
        if self._kb_focused_on_entry():
            return
        page = self.pages.get(self.current_page)
        if page and hasattr(page, "_timeline"):
            page._timeline.set_playhead(0.0)
        elif page and hasattr(page, "_scrub"):
            page._scrub.set(0.0)

    def _kb_go_end(self, event=None):
        if self._kb_focused_on_entry():
            return
        page = self.pages.get(self.current_page)
        if page and hasattr(page, "_timeline"):
            page._timeline.set_playhead(page._timeline._duration)
        elif page and hasattr(page, "_scrub"):
            dur = getattr(page, "duration", 0)
            page._scrub.set(dur)

    def _kb_nudge_left(self, event=None):
        if self._kb_focused_on_entry():
            return
        page = self.pages.get(self.current_page)
        if page and hasattr(page, "_timeline"):
            tl = page._timeline
            tl.set_playhead(max(0, tl._playhead - 1.0))
        elif page and hasattr(page, "_scrub"):
            try:
                page._scrub.set(max(0, float(page._scrub.get()) - 1.0))
            except (ValueError, tk.TclError):
                pass

    def _kb_nudge_right(self, event=None):
        if self._kb_focused_on_entry():
            return
        page = self.pages.get(self.current_page)
        if page and hasattr(page, "_timeline"):
            tl = page._timeline
            tl.set_playhead(min(tl._duration, tl._playhead + 1.0))
        elif page and hasattr(page, "_scrub"):
            try:
                dur = getattr(page, "duration", 9999)
                page._scrub.set(min(dur, float(page._scrub.get()) + 1.0))
            except (ValueError, tk.TclError):
                pass

    def _kb_render(self, event=None):
        page = self.pages.get(self.current_page)
        if not page:
            return
        # Find the render/export button and invoke it
        for attr in ("_btn_trim", "btn_render", "_btn_render", "btn_export",
                     "_btn_export", "_btn_run", "btn_run"):
            btn = getattr(page, attr, None)
            if btn and isinstance(btn, tk.Button):
                try:
                    btn.invoke()
                except Exception:
                    pass
                return "break"

    def _kb_stop_preview(self, event=None):
        self._stop_active_previews(closing=False)

    # ── Menu callbacks ────────────────────────────────────────────────────────
    def _menu_open_file(self):
        from core import dialogs
        paths = dialogs.ask_open_many("media", title="Open media files", parent=self)
        if paths:
            self.open_paths(paths)

    def open_paths(self, paths):
        """Load files into the current tool (drag-and-drop, File > Open, Recent
        files, command line).  Tools that can't take them hand over to the
        Media Inspector."""
        import logging
        paths = [os.path.abspath(p) for p in paths if p and os.path.isfile(p)]
        if not paths:
            return False
        for p in paths:
            self._add_recent_file(p)
        page = self.pages.get(self.current_page)
        accepted = False
        if page is not None and self.current_page not in ("Home", "Settings") and hasattr(page, "load_file"):
            for p in paths:
                try:
                    accepted = bool(page.load_file(p)) or accepted
                except Exception:
                    logging.getLogger("crossfader.shell").exception("load_file failed in %s", self.current_page)
        if not accepted:
            self.show_page("Media Inspector")
            insp = self.pages.get("Media Inspector")
            if insp is not None and hasattr(insp, "load_file"):
                accepted = bool(insp.load_file(paths[0]))
        if accepted:
            self.title(f"{APP_NAME}  \u00b7  {_tab_label(self.current_page)}  \u00b7  {os.path.basename(paths[0])}")
            self.set_status("Loaded: {}".format(os.path.basename(paths[0])), color=self._skin_data["green"])
        return accepted

    def _menu_open_output_folder(self):
        from core.hardware import open_in_explorer
        folder = ""
        page = self.pages.get(self.current_page)
        if page:
            for attr in ("_out_var", "out_var", "out_path_var"):
                if hasattr(page, attr):
                    try:
                        val = getattr(page, attr).get().strip()
                        if val:
                            candidate = (os.path.dirname(val)
                                         if not os.path.isdir(val) else val)
                            if os.path.exists(candidate):
                                folder = candidate
                                break
                    except Exception:
                        pass
        if not folder:
            from core.settings import load_settings
            folder = load_settings().get("default_output_folder", "")
        if not folder or not os.path.exists(folder):
            from tkinter import filedialog
            folder = filedialog.askdirectory(title="Choose folder to open")
        if folder and os.path.exists(folder):
            open_in_explorer(folder)

    def _sidebar_scroll_top(self):
        try:
            self._sidebar_canvas.yview_moveto(0)
        except Exception:
            pass

    def _set_all_categories(self, expanded: bool):
        for cat, (hdr_row, items_frame, exp_var) in self._cat_sections.items():
            try:
                arrow = hdr_row._arrow_lbl
                if expanded:
                    items_frame.pack(fill="x", after=hdr_row)
                    exp_var.set(True)
                    arrow.config(text="▾")
                else:
                    items_frame.pack_forget()
                    exp_var.set(False)
                    arrow.config(text="▸")
            except Exception:
                pass
        try:
            self._inner.update_idletasks()
            self._sidebar_canvas.configure(
                scrollregion=self._sidebar_canvas.bbox("all"))
        except Exception:
            pass

    def _menu_system_info(self):
        self.show_page("Advanced Settings")
        page = self.pages.get("Advanced Settings")
        if page is not None and hasattr(page, "show_system_info"):
            page.show_system_info()

    def _menu_shortcuts(self):
        messagebox.showinfo("Keyboard Shortcuts",
            "PLAYBACK\n"
            "  Space          Play / pause preview\n"
            "  Escape         Stop preview\n\n"
            "NAVIGATION\n"
            "  Home           Go to start\n"
            "  End            Go to end\n"
            "  Left / Right   Nudge playhead \u00b11 sec\n\n"
            "EDITING\n"
            "  I              Set IN point at playhead\n"
            "  O              Set OUT point at playhead\n"
            "  Ctrl+S         Start render / export\n\n"
            "FILE\n"
            "  Ctrl+O         Open file\n"
            "  Drag & Drop    Drop files onto window\n")

    def _menu_open_logs(self):
        from core.hardware import open_in_explorer
        from core.paths import log_dir
        open_in_explorer(log_dir())

    def _menu_check_updates(self):
        threading.Thread(target=self._check_ffmpeg, daemon=True).start()
        from core.hardware import find_binary
        yt = find_binary("yt-dlp")
        if yt:
            def _ver():
                from core.ffmpeg import capture
                try:
                    v = capture([yt, "--version"], timeout=60).stdout.strip()
                except Exception:
                    return
                self._ui(self.set_ytdlp_status, v, yt, True)
                self._ui(self._status_ytdlp.pack, side="right", padx=(0, 6), before=self._status_ffmpeg)
            threading.Thread(target=_ver, daemon=True).start()

    def _menu_about(self):
        from core.ffmpeg import version_of
        n_tools = sum(len(v) for v in TOOLS.values()) + 1
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME}  \u00b7  version {APP_VERSION}\n\n"
            "A free, self-contained video and audio toolkit powered by FFmpeg.\n\n"
            f"{n_tools} tools  \u00b7  FFmpeg {version_of()}  \u00b7  "
            f"{platform.system()} {platform.machine()}\n\n"
            "https://github.com/WyldVeil/wyldveils-media-editor\n"
            "Third-party licences: see THIRD_PARTY_NOTICES.md", parent=self)

    # ─────────────────────────────────────────────────────────────────────────
    #  Sidebar
    # ─────────────────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        skin   = self._skin_data
        SB_BG  = skin["sb_bg"]
        ACCENT = skin["accent"]
        BORDER = skin.get("border", "#2C2C2C")

        # ── Static header (logo + AIO pinned + search) ─────────────────────────
        static_top = tk.Frame(self.sidebar_container, bg=SB_BG)
        static_top.pack(side="top", fill="x")

        # Wordmark
        logo_f = tk.Frame(static_top, bg=SB_BG)
        logo_f.pack(fill="x", padx=16, pady=(20, 0))

        badge = tk.Canvas(logo_f, width=38, height=34, bg=SB_BG,
                           highlightthickness=0)
        badge.pack(side="left", padx=(0, 12))
        try:
            from core.paths import resource_path
            self._badge_img = tk.PhotoImage(master=self, file=resource_path("assets", "icons", "app_32.png"))
            badge.create_image(19, 17, image=self._badge_img)
        except tk.TclError:
            badge.create_rectangle(0, 0, 38, 34, fill=ACCENT, outline="")

        nf = tk.Frame(logo_f, bg=SB_BG)
        nf.pack(side="left", anchor="w")
        tk.Label(nf, text="WYLDVEIL",
                 font=(UI_FONT, 11, "bold"),
                 bg=SB_BG, fg="#CCCCCC").pack(anchor="w")
        tk.Label(nf, text=t("app.subtitle"),
                 font=(UI_FONT, 8), bg=SB_BG, fg="#424242").pack(anchor="w")

        tk.Frame(static_top, bg=BORDER, height=1).pack(fill="x",
                                                         padx=14, pady=(16, 0))

        # All-in-One pinned  (lazy - initialized on first click)
        _aio_key = "⚡ " + PINNED[0]
        self._tab_classes[_aio_key] = PINNED[1]
        self.pages[_aio_key] = None

        aio_row = tk.Frame(static_top, bg=SB_BG)
        aio_row.pack(fill="x", padx=6, pady=(8, 5))
        aio_ind = tk.Frame(aio_row, bg=SB_BG, width=3)
        aio_ind.pack(side="left", fill="y")
        aio_ind.pack_propagate(False)
        aio_btn = tk.Button(
            aio_row, text="   ⚡  {}".format(t("tab.all_in_one")),
            font=(UI_FONT, 10, "bold"),
            bg=SB_BG, fg=ACCENT, relief="flat", anchor="w",
            cursor="hand2",
            activebackground=skin["sb_hover"], activeforeground="#FFFFFF",
            command=lambda n=_aio_key: self.show_page(n),
            bd=0, padx=0, pady=5,
        )
        aio_btn.pack(side="left", fill="x", expand=True)
        aio_btn.bind("<Enter>",
                     lambda e, b=aio_btn: b.config(bg=self._skin_data["sb_hover"]))
        aio_btn.bind("<Leave>",
                     lambda e, b=aio_btn, n=_aio_key: b.config(
                         bg=(self._skin_data["sb_active"]
                             if self.current_page == n
                             else self._skin_data["sb_bg"])))
        self._btn_refs[_aio_key]       = aio_btn
        self._indicator_refs[_aio_key] = aio_ind

        tk.Frame(static_top, bg=BORDER, height=1).pack(fill="x",
                                                         padx=14, pady=(4, 0))

        # Search box
        srch_outer = tk.Frame(static_top, bg=SB_BG)
        srch_outer.pack(fill="x", padx=12, pady=(10, 8))

        i_bg = skin.get("input_bg", "#1E1E1E")
        srch_bdr = tk.Frame(srch_outer, bg=BORDER)
        srch_bdr.pack(fill="x")
        srch_inner = tk.Frame(srch_bdr, bg=i_bg)
        srch_inner.pack(fill="x", padx=1, pady=1)

        tk.Label(srch_inner, text="⌕", bg=i_bg, fg="#484848",
                 font=(UI_FONT, 11)).pack(side="left", padx=(7, 0), pady=3)

        self._search_var = tk.StringVar()
        self._search_entry = tk.Entry(
            srch_inner, textvariable=self._search_var,
            bg=i_bg, fg="#808080",
            insertbackground=ACCENT,
            relief="flat", bd=0, font=(UI_FONT, 9),
            highlightthickness=0,
        )
        self._search_entry.pack(side="left", fill="x", expand=True,
                                 padx=(4, 6), pady=4)
        self._search_ph = t("app.search_placeholder")
        self._search_entry.insert(0, self._search_ph)
        self._search_entry.bind("<FocusIn>",  self._on_search_focus)
        self._search_entry.bind("<FocusOut>", self._on_search_blur)
        self._search_var.trace_add("write", self._filter_sidebar)

        self._search_clear = tk.Label(srch_inner, text="✕",
                                       bg=i_bg, fg="#484848",
                                       font=(UI_FONT, 8), cursor="hand2")
        self._search_clear.pack(side="right", padx=(0, 6))
        self._search_clear.pack_forget()
        self._search_clear.bind("<Button-1>", self._clear_search)

        tk.Frame(static_top, bg=BORDER, height=1).pack(fill="x", padx=14)

        # ── Scrollable tools ──────────────────────────────────────────────────
        scroll_f = tk.Frame(self.sidebar_container, bg=SB_BG)
        scroll_f.pack(fill="both", expand=True)

        canvas = tk.Canvas(scroll_f, bg=SB_BG,
                           highlightthickness=0, yscrollincrement=20)
        self._sidebar_canvas = canvas
        vsb = ttk.Scrollbar(scroll_f, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=SB_BG)
        self._inner = inner

        _iw = SB_WIDTH - 18
        canvas.create_window((0, 0), window=inner, anchor="nw", width=_iw)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=vsb.set)

        canvas.unbind_class("Canvas", "<<FocusIn>>")

        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # ── Tool definitions (sourced from tabs/registry.py) ─────────────────
        for category, items in TOOLS.items():
            expanded = tk.BooleanVar(value=True)

            # Category header
            hdr_row = tk.Frame(inner, bg=SB_BG, cursor="hand2")
            hdr_row.pack(fill="x", pady=(10, 0))

            arrow_lbl = tk.Label(hdr_row, text="▾", bg=SB_BG,
                                  fg="#484848", font=(UI_FONT, 7),
                                  cursor="hand2")
            arrow_lbl.pack(side="left", padx=(14, 0), pady=2)
            hdr_row._arrow_lbl = arrow_lbl

            cat_lbl = tk.Label(hdr_row,
                                text=_cat_label(category),
                                font=(UI_FONT, 7, "bold"),
                                bg=SB_BG, fg=skin["sb_cat"],
                                anchor="w", cursor="hand2")
            cat_lbl.pack(side="left", padx=(5, 4), pady=2,
                          fill="x", expand=True)

            count_bg = skin.get("input_bg", "#1E1E1E")
            tk.Label(hdr_row, text=str(len(items)),
                     bg=count_bg, fg="#4A4A4A",
                     font=(UI_FONT, 7),
                     padx=5, pady=1).pack(side="right", padx=(0, 12), pady=4)

            items_frame = tk.Frame(inner, bg=SB_BG)
            items_frame.pack(fill="x")

            self._cat_sections[category] = (hdr_row, items_frame, expanded)

            def _make_toggle(ef=items_frame, al=arrow_lbl, ev=expanded, hr=hdr_row):
                def toggle(*_):
                    if ev.get():
                        ef.pack_forget()
                        ev.set(False)
                        al.config(text="▸")
                    else:
                        ef.pack(fill="x", after=hr)
                        ev.set(True)
                        al.config(text="▾")
                    inner.update_idletasks()
                    canvas.configure(scrollregion=canvas.bbox("all"))
                return toggle

            _tog = _make_toggle()
            for w in (hdr_row, cat_lbl, arrow_lbl):
                w.bind("<Button-1>", _tog)

            # Tool buttons  (tabs are lazy - instantiated on first visit)
            for name, tab_cls in items:
                self._tab_classes[name] = tab_cls
                self.pages[name] = None  # sentinel; initialized in show_page()

                row_f = tk.Frame(items_frame, bg=SB_BG)
                row_f.pack(fill="x", padx=6, pady=0)

                ind = tk.Frame(row_f, bg=SB_BG, width=3)
                ind.pack(side="left", fill="y")
                ind.pack_propagate(False)

                btn = tk.Button(
                    row_f,
                    text="    {}".format(_tab_label(name)),
                    font=(UI_FONT, 9),
                    bg=SB_BG, fg=skin["sb_btn"],
                    relief="flat", anchor="w",
                    cursor="hand2",
                    activebackground=skin["sb_hover"],
                    activeforeground="#FFFFFF",
                    command=lambda n=name: self.show_page(n),
                    bd=0, padx=0, pady=3,
                )
                btn.pack(side="left", fill="x", expand=True)
                btn.bind("<Enter>",
                         lambda e, b=btn: b.config(
                             bg=self._skin_data["sb_hover"]))
                btn.bind("<Leave>",
                         lambda e, b=btn, n=name: b.config(
                             bg=(self._skin_data["sb_active"]
                                 if self.current_page == n
                                 else self._skin_data["sb_bg"])))

                self._btn_refs[name]       = btn
                self._indicator_refs[name] = ind
                self._tool_rows.append((name.casefold() + " " + _tab_label(name).casefold(), row_f))

        tk.Frame(inner, bg=SB_BG, height=20).pack()

        # Footer
        footer = tk.Frame(self.sidebar_container, bg=SB_BG)
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x")
        fi = tk.Frame(footer, bg=SB_BG)
        fi.pack(fill="x", padx=14, pady=7)
        tk.Label(fi, text="v{}".format(APP_VERSION),
                 bg=SB_BG, fg="#343434",
                 font=(UI_FONT, 8)).pack(side="left")
        self.debug_cb = tk.Checkbutton(
            fi, text="DEBUG", variable=self.debug_mode,
            bg=SB_BG, fg="#343434", selectcolor=SB_BG,
            activebackground=SB_BG, font=(UI_FONT, 8), cursor="hand2")
        self.debug_cb.pack(side="right")

        # Hidden pages (not in sidebar) - also lazy
        for name, tab_cls in HIDDEN.items():
            if name not in self.pages:
                self._tab_classes[name] = tab_cls
                self.pages[name] = None

    # ── Search ────────────────────────────────────────────────────────────────
    def _on_search_focus(self, event):
        if self._search_entry.get() == self._search_ph:
            self._search_entry.delete(0, tk.END)
            self._search_entry.config(
                fg=self._skin_data.get("input_fg", "#E2E2E2"))

    def _on_search_blur(self, event):
        if not self._search_entry.get():
            self._search_entry.insert(0, self._search_ph)
            self._search_entry.config(fg="#808080")
            self._search_clear.pack_forget()

    def _clear_search(self, event=None):
        self._search_entry.delete(0, tk.END)
        self._on_search_blur(None)
        self._filter_sidebar()

    def _filter_sidebar(self, *_):
        # Debounce: cancel any pending filter and schedule a new one
        if hasattr(self, '_search_after_id') and self._search_after_id:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(80, self._do_filter_sidebar)

    def _do_filter_sidebar(self):
        self._search_after_id = None
        query = self._search_var.get().strip().lower()
        searching = bool(query) and query != self._search_ph.lower()

        if searching:
            self._search_clear.pack(side="right", padx=(0, 6))
        else:
            self._search_clear.pack_forget()

        # First pass: show/hide each tool row
        visible_parents = set()
        for tool_name, row_f in self._tool_rows:
            match = not searching or query in tool_name
            try:
                if match:
                    row_f.pack(fill="x", padx=6, pady=0)
                    # Identify which items_frame this belongs to
                    visible_parents.add(row_f.master)
                else:
                    row_f.pack_forget()
            except Exception:
                pass

        # Second pass: show/hide category headers
        for cat, (hdr_row, items_frame, exp_var) in self._cat_sections.items():
            if searching:
                if items_frame in visible_parents:
                    hdr_row.pack(fill="x", pady=(10, 0))
                    items_frame.pack(fill="x", after=hdr_row)
                else:
                    hdr_row.pack_forget()
                    items_frame.pack_forget()
            else:
                hdr_row.pack(fill="x", pady=(10, 0))
                if exp_var.get():
                    items_frame.pack(fill="x", after=hdr_row)
                else:
                    items_frame.pack_forget()

        try:
            self._inner.update_idletasks()
            self._sidebar_canvas.configure(
                scrollregion=self._sidebar_canvas.bbox("all"))
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Logging
    # ─────────────────────────────────────────────────────────────────────────
    def log_debug(self, message):
        if self.debug_mode.get():
            print("[DEBUG] {}".format(message))

    # ─────────────────────────────────────────────────────────────────────────
    #  Navigation
    # ─────────────────────────────────────────────────────────────────────────
    def show_page(self, page_name):
        self._stop_active_previews(closing=False)
        skin = self._skin_data
        stale = self.pages.get(page_name)
        if isinstance(stale, PlaceholderTab) and page_name != self.current_page:
            stale.destroy()
            self.pages[page_name] = None      # retry loading the tool

        if self.pages.get(page_name) is None and page_name in self._tab_classes:
            try:
                self.pages[page_name] = self._tab_classes[page_name](
                    self._content_area)
            except Exception as exc:
                import logging
                logging.getLogger("crossfader.shell").exception("Could not load tool %r", page_name)
                self.pages[page_name] = PlaceholderTab(
                    self._content_area, page_name, f"{type(exc).__name__}: {exc}")
            # The Home tab needs a way to navigate to other tabs. Inject
            # the navigator after construction (avoids passing the App
            # instance into the tab constructor).
            page = self.pages[page_name]
            if hasattr(page, "set_navigator"):
                try:
                    page.set_navigator(self.show_page)
                except Exception:
                    pass

        if page_name not in self.pages or self.pages[page_name] is None:
            return

        # Handle Contextual Status Bar items
        if page_name == "Video Downloader":
            self._status_ytdlp.pack(side="right", padx=(0, 6), before=self._status_ffmpeg)
        else:
            self._status_ytdlp.pack_forget()

        if self.current_page and self.current_page in self._btn_refs:
            self._btn_refs[self.current_page].config(
                bg=skin["sb_bg"], fg=skin["sb_btn"], font=(UI_FONT, 9))
        if self.current_page and self.current_page in self._indicator_refs:
            try: self._indicator_refs[self.current_page].config(bg=skin["sb_bg"])
            except Exception: pass

        if self.current_page and self.pages.get(self.current_page) is not None:
            self.pages[self.current_page].pack_forget()

        self.pages[page_name].pack(fill="both", expand=True)
        self.current_page = page_name
        _app_state.set("active_tab", page_name)

        if page_name in self._btn_refs:
            self._btn_refs[page_name].config(
                bg=skin["sb_active"], fg=skin["accent"], font=(UI_FONT, 9, "bold"))
        if page_name in self._indicator_refs:
            try: self._indicator_refs[page_name].config(bg=skin["accent"])
            except Exception: pass

        self.title(f"{APP_NAME}  \u00b7  {_tab_label(page_name)}")
        try: self._status_tool.config(text=_tab_label(page_name))
        except Exception: pass

        self._scroll_to_active(page_name)

    def _scroll_to_active(self, page_name):
        try:
            if page_name not in self._btn_refs:
                return
            btn = self._btn_refs[page_name]
            self._sidebar_canvas.update_idletasks()
            total_h = self._sidebar_canvas.bbox("all")[3]
            if not total_h:
                return
            canvas_h = self._sidebar_canvas.winfo_height()
            y = btn.winfo_rooty() - self._inner.winfo_rooty()
            cur_top, cur_bot = self._sidebar_canvas.yview()
            frac = y / max(total_h, 1)
            if frac < cur_top:
                self._sidebar_canvas.yview_moveto(max(0, frac - 0.05))
            elif frac + 30 / total_h > cur_bot:
                self._sidebar_canvas.yview_moveto(
                    min(1, frac - canvas_h / total_h + 0.05))
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Cleanup
    # ─────────────────────────────────────────────────────────────────────────
    def _stop_active_previews(self, closing=False):
        """
        Kill only preview (ffplay) processes.
        If closing=True, also kill interactive screen-recording processes.
        """
        # Attributes that hold ffplay preview handles
        PREVIEW_ATTRS = ("preview_proc",)
        
        # Attributes that hold interactive recording handles (screen recorder,
        # live-stream capture). We only kill these when the app closes, allowing
        # them to continue in the background if the user switches tabs.
        RECORD_ATTRS  = ("record_proc", "_record_proc")

        for page in self.pages.values():
            if page is None:
                continue

            for attr in PREVIEW_ATTRS:
                p = getattr(page, attr, None)
                if p and hasattr(p, "terminate"):
                    try:
                        p.terminate()
                        setattr(page, attr, None)
                    except Exception:
                        pass

            if closing:
                for attr in RECORD_ATTRS:
                    p = getattr(page, attr, None)
                    if p and hasattr(p, "terminate"):
                        try:
                            p.terminate()
                            setattr(page, attr, None)
                        except Exception:
                            pass

    # ─────────────────────────────────────────────────────────────────────────
    #  Drag-and-drop
    # ─────────────────────────────────────────────────────────────────────────
    def _init_drag_drop(self):
        """Drag-and-drop of files onto the window (Windows, Linux X11/XWayland, macOS)."""
        import logging
        self._dnd_ok = False
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
            TkinterDnD._require(self)
            for widget in (self, self.content_container, self._content_area):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
            self._dnd_ok = True
        except Exception as exc:
            logging.getLogger("crossfader.shell").info("Drag-and-drop unavailable: %s", exc)

    def _on_drop(self, event):
        try:
            paths = list(self.tk.splitlist(event.data))
        except tk.TclError:
            paths = [event.data]
        paths = [p[7:] if p.startswith("file://") else p for p in paths]
        self.after(10, lambda: self.open_paths(paths))
        return getattr(event, "action", "copy")

    def _handle_drop(self, file_list):
        """Backwards-compatible entry point (list of str/bytes paths)."""
        self.open_paths([f.decode("utf-8", "replace") if isinstance(f, bytes) else str(f) for f in file_list])

    # ─────────────────────────────────────────────────────────────────────────
    #  Recent files
    # ─────────────────────────────────────────────────────────────────────────
    _MAX_RECENT = 10

    def _load_recent_files(self):
        from core.settings import load_settings
        return load_settings().get("recent_files", [])

    def _add_recent_file(self, path):
        from core import settings as _settings
        path = os.path.normpath(path)
        with _settings.transaction() as data:
            recent = [r for r in data.get("recent_files", []) if os.path.normpath(r) != path]
            data["recent_files"] = ([path] + recent)[:self._MAX_RECENT]
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        if not hasattr(self, "_recent_menu"):
            return
        menu = self._recent_menu
        menu.delete(0, tk.END)
        recent = self._load_recent_files()
        if not recent:
            menu.add_command(label="(no recent files)", state="disabled")
            return
        for path in recent:
            basename = os.path.basename(path)
            menu.add_command(
                label=basename,
                command=lambda p=path: self._open_recent(p))
        menu.add_separator()
        menu.add_command(label="Clear Recent Files",
                         command=self._clear_recent_files)

    def _open_recent(self, path):
        if not os.path.isfile(path):
            messagebox.showwarning("File Not Found", f"File no longer exists:\n{path}", parent=self)
            return
        self.open_paths([path])

    def _clear_recent_files(self):
        from core import settings as _settings
        _settings.set("recent_files", [])
        self._rebuild_recent_menu()

    def _on_closing(self):
        """Confirm before abandoning running jobs; never leave FFmpeg orphaned."""
        from core import ffmpeg as _ff
        from core.queue_manager import RenderQueueManager
        q = RenderQueueManager.get_instance()
        busy = q.active_tasks()
        if busy:
            if not messagebox.askyesno(
                    "Jobs still running",
                    f"{len(busy)} render job(s) are still running or waiting.\n\n"
                    "Stop them and quit? Unfinished files will be removed.", parent=self):
                return
            q.cancel_all()
        for page in self.pages.values():
            hook = getattr(page, "on_app_closing", None)
            if callable(hook):
                try:
                    hook()
                except Exception:
                    pass
        self._stop_active_previews(closing=True)
        _ff.kill_all()
        self.destroy()


def _self_test(app) -> int:
    """Smoke-test an installation: tools resolve and run, every tool page loads."""
    import tempfile
    from core import ffmpeg as _ff
    from core.hardware import find_binary
    problems = []
    for tool in ("ffmpeg", "ffprobe"):
        path = find_binary(tool)
        print(f"{tool:8} {path} -> {_ff.version_of(path) if path else 'MISSING'}")
        if not path:
            problems.append(f"{tool} missing")
    out = os.path.join(tempfile.mkdtemp(prefix="selftest_"), "t.mp4")
    res = _ff.run([_ff.ffmpeg_path(), "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:d=1",
                   "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-shortest", out])
    info = _ff.try_probe(out)
    ok = res.ok and info is not None and info.has_video and info.has_audio
    print("encode  ", "ok" if ok else res.error_summary)
    if not ok:
        problems.append("test encode failed")
    for name in list(app._tab_classes):
        try:
            app.show_page(name)
            app.update()
            if isinstance(app.pages.get(name), PlaceholderTab):
                problems.append(f"tool failed to load: {name}")
        except Exception as exc:
            problems.append(f"{name}: {exc}")
    print(f"pages    {len(app._tab_classes)} checked")
    for prob in problems:
        print("PROBLEM:", prob)
    return 1 if problems else 0


def main(argv=None) -> int:
    import argparse
    import multiprocessing
    multiprocessing.freeze_support()      # PyInstaller on Windows
    parser = argparse.ArgumentParser(prog="wyldveil-media-editor", description=APP_NAME)
    parser.add_argument("files", nargs="*", help="media files to open")
    parser.add_argument("--debug", action="store_true", help="verbose logging to the console")
    parser.add_argument("--self-test", action="store_true", help="check the installation and exit")
    parser.add_argument("--no-ai", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cli", action="store_true",
                        help="command-line mode, no window (see AGENTS.md); must come first")
    args, _unknown = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if args.no_ai:
        os.environ["CROSSFADER_NO_AI_TEST"] = "1"
    if args.self_test:
        os.environ.setdefault("CROSSFADER_NO_SOUND", "1")
        os.environ.setdefault("CROSSFADER_NO_NETWORK", "1")
    app = App()
    if args.self_test:
        app.withdraw()
        code = _self_test(app)
        app.destroy()
        return code
    if args.files:
        app.after(400, lambda: app.open_paths(args.files))
    app.mainloop()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sys.exit(main())
