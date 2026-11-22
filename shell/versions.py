"""
shell/versions.py - the FFmpeg and yt-dlp in use, shown in the status bar.

FFmpeg is pinned (it ships with the app), so it is only described.  yt-dlp
has to keep up with the sites it downloads from, so its version is compared
with the latest release and an update offered.  Every check runs on a
worker (core.tasks); only the results touch the window.
"""

from __future__ import annotations

import re
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from tkinter import messagebox

from core import proc, tasks, updater
from core.ffmpeg import version_of
from core.hardware import find_binary
from core.i18n import t
from shell.statusbar import StatusBar


def version_tuple(version: str | None) -> tuple[int, ...] | None:
    """``'9.0.2'`` / ``'2026.10.26'`` -> (9, 0, 2) / (2026, 10, 26); None if not a release."""
    m = re.match(r"^v?(\d+(?:\.\d+)+)", str(version or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


@dataclass(frozen=True, slots=True)
class FfmpegInUse:
    path: str | None
    version: str | None = None
    bundled: bool = True


def describe_ffmpeg() -> FfmpegInUse:
    """Worker: which FFmpeg is in use."""
    path = find_binary("ffmpeg")
    if not path:
        return FfmpegInUse(None)
    return FfmpegInUse(path, version_of(path), updater.is_app_managed(path))


def ytdlp_version(path: str) -> str:
    """Worker: the version yt-dlp reports."""
    return proc.run([path, "--version"], timeout=60).stdout.strip()


class ToolVersions:
    def __init__(self, window: tk.Tk, bar: StatusBar, set_status: Callable[..., object]) -> None:
        self._window = window
        self._bar = bar
        self._set_status = set_status
        self._ffmpeg = FfmpegInUse(None)
        self._ytdlp_path: str | None = None
        self._ytdlp_local: str | None = None
        self._ytdlp_latest: str | None = None

    # ── FFmpeg ────────────────────────────────────────────────────────────────
    def check_ffmpeg(self) -> None:
        tasks.submit(describe_ffmpeg, on_done=self._show_ffmpeg, name="ffmpeg version")

    def _show_ffmpeg(self, found: FfmpegInUse) -> None:
        self._ffmpeg = found
        skin = self._bar.skin
        if found.path is None:
            self._bar.ffmpeg.config(
                text=t("app.status.ffmpeg_missing_click_install"), fg=skin["red"]
            )
        else:
            where = "bundled" if found.bundled else "your own copy"
            self._bar.ffmpeg.config(text=f"FFmpeg {found.version} ({where})", fg=skin["fgdim"])

    def explain_ffmpeg(self) -> None:
        found = self._ffmpeg
        path = found.path or "not found"
        if found.bundled:
            msg = (
                f"This app ships FFmpeg {found.version or ''}, the build every tool is tested with. It is "
                "updated together with the app, so a new FFmpeg can never break a tool.\n\n"
                f"In use: {path}"
            )
        else:
            msg = (
                f"A different FFmpeg ({found.version or 'unknown version'}) is in use:\n{path}\n\n"
                "Tools are tested with the bundled FFmpeg; choose 'Bundled' under Advanced Settings › FFmpeg "
                "if something doesn't work."
            )
        messagebox.showinfo("FFmpeg", msg, parent=self._window)

    # ── yt-dlp ────────────────────────────────────────────────────────────────
    def set_ytdlp(self, version: str, path: str, force: bool = False) -> None:
        """The Video Downloader found yt-dlp (*force*: the person asked, so check
        for a newer one even when automatic checks are off)."""
        self._ytdlp_path, self._ytdlp_local = path, version
        self._bar.ytdlp.config(
            text=f"yt-dlp {version} (checking…)", fg=self._bar.skin["fgdim"], cursor="watch"
        )
        tasks.submit(
            partial(updater.latest_ytdlp_release, force=force),
            on_done=partial(self._compared, version),
            name="yt-dlp latest",
        )

    def _compared(self, local: str, latest: str | None) -> None:
        self._ytdlp_latest = latest
        skin = self._bar.skin
        have, newest = version_tuple(local), version_tuple(latest)
        if have is None or newest is None:
            self._bar.ytdlp.config(text=f"yt-dlp {local}", fg=skin["fgdim"], cursor="hand2")
        elif newest > have:
            self._bar.ytdlp.config(
                text=f"yt-dlp {local} → {latest} (click to update)",
                fg=skin["orange"],
                cursor="hand2",
            )
        else:
            self._bar.ytdlp.config(
                text=f"yt-dlp {local} (up to date)", fg=skin["green"], cursor="arrow"
            )

    def check_all(self) -> None:
        """Help › Check for tool updates."""
        self.check_ffmpeg()
        path = find_binary("yt-dlp")
        if path:
            tasks.submit(
                partial(ytdlp_version, path),
                name="yt-dlp version",
                on_done=lambda v: (self.set_ytdlp(v, path, force=True), self._bar.show_ytdlp(True)),
            )

    def update_ytdlp(self) -> None:
        have, newest = version_tuple(self._ytdlp_local), version_tuple(self._ytdlp_latest)
        if have and newest and newest <= have:
            messagebox.showinfo(
                t("msg.ytdlp_update_title"),
                f"yt-dlp {self._ytdlp_local} is already up to date.",
                parent=self._window,
            )
            return
        if self._ytdlp_path and not updater.is_app_managed(self._ytdlp_path):
            if not messagebox.askyesno(
                t("msg.ytdlp_update_title"),
                f"This yt-dlp ({self._ytdlp_path}) was installed by your system.\n\nDownload the latest "
                "official yt-dlp for this app instead? (Your system copy is left untouched.)",
                parent=self._window,
            ):
                return
        elif not messagebox.askyesno(
            t("msg.ytdlp_update_title"), t("msg.ytdlp_update_confirm"), parent=self._window
        ):
            return
        self._bar.ytdlp.config(text=t("app.status.ytdlp_updating"), fg=self._bar.skin["orange"])
        self._set_status(t("app.status.ytdlp_updating"))
        tasks.submit(self._install, on_done=self._installed, name="yt-dlp update")

    def _install(self) -> tuple[str, str] | None:
        """Worker: download yt-dlp; (version, path) when it worked."""
        from core import ui

        if not updater.install_ytdlp(lambda msg: ui.call_soon(self._set_status, msg)):
            return None
        path = find_binary("yt-dlp")
        if not path:
            return None
        try:
            return ytdlp_version(path), path
        except proc.ProcessError:
            return "?", path

    def _installed(self, result: tuple[str, str] | None) -> None:
        skin = self._bar.skin
        if result is None:
            self._set_status(t("msg.ytdlp_update_failed"), skin["red"])
            self._bar.ytdlp.config(text=t("app.status.ytdlp_update_failed"))
            return
        self.set_ytdlp(*result)
        self._set_status(t("msg.ytdlp_update_complete"), skin["green"])
