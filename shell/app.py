"""
shell/app.py - the main window: puts the shell's parts together.

Composition, not inheritance (Effective Python item 54 and Robust Python
ch. 12): the sidebar, status bar, pages, menus, shortcuts, file handling and
tool versions are separate objects, each in its own module, wired together
here.  The window itself only routes between them and offers pages the small
:class:`~tabs.kit.contracts.Shell` interface.
"""
from __future__ import annotations

import logging
import os
import platform
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core import (
    APP_NAME,
    APP_VERSION,
    capabilities,
    encoders,
    logs,
    proc,
    settings,
    tasks,
    tools,
    ui,
)
from core import scaling as _scaling
from core.ffmpeg import version_of
from core.hardware import open_in_explorer
from core.paths import log_dir, resource_path
from core.queue_manager import RenderQueueManager
from core.scaling import px
from core.skins import UI_FONT, get_skin, load_skin_name, save_skin_name
from core.tools import Category, ToolId
from shell import chime, menus
from shell.files import FileOpener
from shell.pages import PageHost
from shell.shortcuts import SHORTCUTS_HELP, Shortcuts
from shell.sidebar import Sidebar
from shell.statusbar import StatusBar
from shell.versions import ToolVersions
from shell.wheel import WheelRouter
from tabs.kit import labels
from tabs.kit.contracts import ClosingAware, Previewing
from tabs.kit.theme import apply_default_options, apply_skin, apply_ttk_styles

logger = logging.getLogger("crossfader.shell")


class App(tk.Tk):
    def __init__(self) -> None:
        _scaling.prepare()
        super().__init__(className="wyldveil-media-editor")      # WM_CLASS: matches the .desktop file
        _scaling.apply(self)
        self._set_icons()
        # thread-safe UI calls, logging and crash reports first: everything below relies on them
        ui.install(self)
        logs.setup(debug="--debug" in sys.argv)
        logs.install_excepthooks(self)
        encoders.detect_in_background()
        capabilities.warm()                          # what this FFmpeg can do, learned off the UI thread

        self.title(f"{APP_NAME}  ·  {APP_VERSION}")
        self._size_window()
        self._recent: list[ToolId] = tools.parse_all(settings.RECENT_TOOLS.get())
        self._recent_job: str | None = None
        self.debug_mode = tk.BooleanVar(master=self, value=False)
        self.skin = get_skin(load_skin_name())
        apply_default_options(self, self.skin)        # before any page widget exists
        ttk.Style().theme_use("clam")
        apply_ttk_styles(self.skin)

        self.statusbar = StatusBar(self, self.skin, on_queue_click=lambda: self.show_page(ToolId.ENCODE_QUEUE),
                                   on_ffmpeg_click=lambda: self.versions.explain_ffmpeg(),
                                   on_ytdlp_click=lambda: self.versions.update_ytdlp())
        self.statusbar.pack(side="bottom", fill="x")
        self.versions = ToolVersions(self, self.statusbar, self.set_status)

        body = tk.Frame(self, bg=self.skin["sb_bg"])
        body.pack(fill="both", expand=True)
        self.sidebar = Sidebar(body, self.skin, on_select=self.show_page, debug_mode=self.debug_mode)
        self.sidebar.pack(side="left", fill="y")
        tk.Frame(body, bg=self.skin.get("border", "#2C2C2C"), width=px(1)).pack(side="left", fill="y")
        self.content = tk.Frame(body, bg=self.skin["content"])
        self.content.pack(side="right", fill="both", expand=True)
        self._build_top_bar()
        area = tk.Frame(self.content, bg=self.skin["content"])
        area.pack(side="top", fill="both", expand=True)
        self.pages = PageHost(area, self.show_page)

        menubar = menus.build(self, self.skin, menus.MenuActions(
            show=self.show_page, open_files=lambda: self.files.ask(), open_output_folder=self._open_output_folder,
            quit=self.quit_app, set_theme=self.apply_theme, scroll_sidebar_top=self.sidebar.scroll_top,
            expand_sidebar=self.sidebar.expand_all, shortcuts=self._show_shortcuts,
            system_info=self._show_system_info, check_updates=lambda: self.versions.check_all(),
            open_logs=lambda: open_in_explorer(log_dir()), about=self._show_about),
            debug_mode=self.debug_mode, theme=load_skin_name())
        self._theme_var = menubar.theme
        self.files = FileOpener(self, menubar.recent, current=lambda: (self.pages.current, self.current()),
                                show=self.show_page, loaded=self._file_loaded)
        self.bind("<Control-o>", lambda _e: self.files.ask())
        self.shortcuts = Shortcuts(self, self.current, self.stop_previews)
        self.wheel = WheelRouter(self, lambda: (self.sidebar, self.sidebar.canvas))

        self.apply_theme(load_skin_name())
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        queue = RenderQueueManager.get_instance()
        queue.set_tk_root(self)
        queue.register_update_callback(self.statusbar.queue_changed)

        self.show_page(self.first_page())
        self.files.accept_drops(self, self.content, area)
        if settings.PLAY_STARTUP_SOUND.get() and not os.environ.get("CROSSFADER_NO_SOUND"):
            tasks.submit(chime.play, name="chime")

    # ── Building ──────────────────────────────────────────────────────────────
    def _set_icons(self) -> None:
        try:
            self._icons = [tk.PhotoImage(master=self, file=resource_path("assets", "icons", f"app_{n}.png"))
                           for n in (256, 128, 64, 48, 32, 16)]
            self.iconphoto(True, *self._icons)
        except tk.TclError:
            logger.info("window icons not found")

    def _size_window(self) -> None:
        """1480x920 design pixels (bigger on HiDPI screens), never bigger than the screen."""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(px(1480), int(sw * 0.94)), min(px(920), int(sh * 0.9))
        self.geometry(f"{w}x{h}")
        self.minsize(min(px(1100), w), min(px(700), h))

    def _build_top_bar(self) -> None:
        """Start and Home, always one click away above every page."""
        skin = self.skin
        bar = tk.Frame(self.content, bg=skin["panel"], height=px(32))
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)
        def button(text: str, tool: ToolId) -> tk.Button:
            return tk.Button(bar, text=text, font=(UI_FONT, 9, "bold"), bg=skin["panel"], fg=skin["fg"],
                             activebackground=skin.get("sb_hover", "#2A2A2A"), activeforeground="#FFFFFF",
                             relief="flat", cursor="hand2", bd=0, padx=px(14), pady=px(4),
                             command=lambda: self.show_page(tool))
        self.start_button = button("🧭  Start", ToolId.START)
        self.start_button.pack(side="left", padx=px(10, 0), pady=px(4))
        self.home_button = button("🏠  Home", ToolId.HOME)
        self.home_button.pack(side="left", padx=px(4), pady=px(4))
        tk.Frame(self.content, bg=skin.get("border", "#2C2C2C"), height=px(1)).pack(side="top", fill="x")

    # ── The Shell contract (tabs/kit/contracts.py) ────────────────────────────
    def show_page(self, tool: ToolId | str) -> tk.Misc | None:
        """Open *tool* (an id, or its English name) and return its page."""
        tid = tool if isinstance(tool, ToolId) else tools.parse(tool)
        if tid is None:
            logger.warning("no such tool: %r", tool)
            return None
        self.stop_previews()
        page = self.pages.show(tid)
        self.statusbar.show_ytdlp(tid is ToolId.VIDEO_DOWNLOADER)
        self.sidebar.set_active(tid)
        self._remember_tool(tid)
        label = labels.tool(tid)
        self.title(f"{APP_NAME}  ·  {label}")
        self.statusbar.set_tool(label)
        return page

    def current(self) -> tk.Misc | None:
        return self.pages.get(self.pages.current) if self.pages.current is not None else None

    @property
    def current_page(self) -> ToolId | None:
        return self.pages.current

    def page(self, tool: ToolId | str) -> tk.Misc | None:
        """*tool*'s page if it has been opened (never builds one)."""
        tid = tool if isinstance(tool, ToolId) else tools.parse(tool)
        return self.pages.get(tid) if tid is not None else None

    def set_status(self, message: str, color: str | None = None) -> None:
        self.statusbar.set_status(message, color)

    def log_debug(self, message: str) -> None:
        if self.debug_mode.get():
            print(f"[DEBUG] {message}")

    def apply_theme(self, name: str) -> None:
        if name != load_skin_name():
            save_skin_name(name)
        self.skin = apply_skin(self, sidebar=self.sidebar, content=self.content, stars_behind=self.sidebar.inner)
        self.sidebar.recolour(self.skin)
        self.statusbar.recolour(self.skin)
        self.versions.check_ffmpeg()                  # describes FFmpeg in the theme's colours
        self._theme_var.set(name)

    # ── Used by tools and the command line ────────────────────────────────────
    def open_paths(self, paths: list[str]) -> bool:
        return self.files.open(paths)

    def set_ytdlp_status(self, version: str, path: str, force: bool = False) -> None:
        """The Video Downloader found yt-dlp."""
        self.versions.set_ytdlp(version, path, force)

    def first_page(self) -> ToolId:
        """Settings > "Start the app on"."""
        choice = settings.START_PAGE.get()
        if choice == "home":
            return ToolId.HOME
        if choice == "last":
            recent = self._recent or tools.parse_all(settings.RECENT_TOOLS.get())
            if recent:
                return recent[0]
        return ToolId.START

    def _remember_tool(self, tool: ToolId) -> None:
        """Recently used tools, newest first (the Start page lists them).  Saved
        after two quiet seconds rather than on every click (High Performance
        Python: one settings write per page switch was the app's only disk churn)."""
        if tools.spec(tool).category is Category.HIDDEN:
            return
        self._recent = [tool, *(t for t in self._recent if t is not tool)][:8]
        if self._recent_job is not None:
            self.after_cancel(self._recent_job)
        self._recent_job = self.after(2000, self._save_recent_tools)

    def recent_tools(self) -> list[ToolId]:
        """Recently used tools, newest first (saved to settings after a quiet moment)."""
        return list(self._recent)

    def _save_recent_tools(self) -> None:
        self._recent_job = None
        settings.RECENT_TOOLS.set([t.value for t in self._recent])

    def _file_loaded(self, path: str) -> None:
        tool = self.pages.current
        name = labels.tool(tool) if tool is not None else ""
        self.title(f"{APP_NAME}  ·  {name}  ·  {os.path.basename(path)}")
        self.set_status(f"Loaded: {os.path.basename(path)}", color=self.skin["green"])

    def stop_previews(self) -> None:
        for page in self.pages.built():
            if isinstance(page, Previewing):
                page.stop_preview()

    # ── Menu commands ─────────────────────────────────────────────────────────
    def _open_output_folder(self) -> None:
        folder = ""
        page = self.current()
        for attr in ("_out_var", "out_var", "out_path_var"):      # the usual names for a tool's output box
            var = getattr(page, attr, None)
            if isinstance(var, tk.Variable):
                value = str(var.get()).strip()
                candidate = value if os.path.isdir(value) else os.path.dirname(value)
                if value and os.path.exists(candidate):
                    folder = candidate
                    break
        folder = folder or settings.DEFAULT_OUTPUT_FOLDER.get()
        if not folder or not os.path.exists(folder):
            folder = filedialog.askdirectory(title="Choose folder to open", parent=self)
        if folder and os.path.exists(folder):
            open_in_explorer(folder)

    def _show_shortcuts(self) -> None:
        messagebox.showinfo("Keyboard Shortcuts", SHORTCUTS_HELP, parent=self)

    def _show_system_info(self) -> None:
        page = self.show_page(ToolId.ADVANCED_SETTINGS)
        show = getattr(page, "show_system_info", None)
        if callable(show):
            show()

    def _show_about(self) -> None:
        count = sum(1 for s in tools.SPECS if s.category is not Category.HIDDEN)
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME}  ·  version {APP_VERSION}\n\n"
            "A free, self-contained video and audio toolkit powered by FFmpeg.\n\n"
            f"{count} tools  ·  FFmpeg {version_of()}  ·  {platform.system()} {platform.machine()}\n\n"
            "https://github.com/WyldVeil/wyldveils-media-editor\n"
            "Third-party licences: see THIRD_PARTY_NOTICES.md", parent=self)

    # ── Quitting ──────────────────────────────────────────────────────────────
    def quit_app(self) -> None:
        """Ask before abandoning running jobs; never leave a program running."""
        queue = RenderQueueManager.get_instance()
        busy = queue.active_tasks()
        if busy:
            if not messagebox.askyesno(
                    "Jobs still running", f"{len(busy)} render job(s) are still running or waiting.\n\n"
                    "Stop them and quit? Unfinished files will be removed.", parent=self):
                return
            queue.cancel_all()
        for page in self.pages.built():
            if isinstance(page, ClosingAware):
                try:
                    page.on_app_closing()
                except Exception:                    # one page's clean-up failing must not stop the others
                    logger.exception("on_app_closing failed in %s", type(page).__name__)
        self.stop_previews()
        if self._recent_job is not None:
            self._save_recent_tools()
        tasks.shutdown()                             # queued background jobs are dropped...
        proc.kill_all()                              # ...and running ones end with their programs
        self.destroy()
