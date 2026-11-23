"""
core/ops/chapters.py  -  Chapter lists: reading them from text, YouTube's rules,
and the timestamps they are written with (tabs/social/chapter_markers.py).
"""

from __future__ import annotations

import re

from core import ffmpeg as ff


def fmt_yt(secs: float) -> str:
    """Format seconds as M:SS or H:MM:SS for YouTube."""
    secs = int(secs)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_srt_tc(secs: float) -> str:
    """Format seconds as HH:MM:SS,000 for SRT."""
    h = int(secs) // 3600
    m = (int(secs) % 3600) // 60
    s = int(secs) % 60
    ms = min(999, round((secs - int(secs)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class Chapter:
    """A chapter: where it starts and its title."""

    def __init__(self, time_secs: float, title: str, auto: bool = False) -> None:
        self.time = time_secs
        self.title = title
        self.auto = auto  # auto-numbered: renumbered after every change


def parse_chapter_text(content: str) -> list[tuple[float, str]]:
    """[(seconds, title)] from SRT/VTT cues or 'H:MM:SS Title' / 'MM:SS - Title' lines."""
    out: list[tuple[float, str]] = []
    cue = re.compile(r"(\d+:\d{2}:\d{2}(?:[.,]\d{1,3})?)\s*-->.*\n(.+)")
    found = cue.findall(content)
    if found:
        for tc, title in found:
            out.append((_parse_tc(tc), re.sub(r"<[^>]+>", "", title).strip()))
        return out
    line_re = re.compile(r"^\s*\(?((?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\)?\s*[-–—:|]?\s*(.*)$")
    for line in content.splitlines():
        m = line_re.match(line)
        if m and m.group(2).strip():
            out.append((_parse_tc(m.group(1)), m.group(2).strip()))
    return out


def _parse_tc(tc: str) -> float:
    return ff.parse_time(tc.replace(",", "."))


def youtube_issues(chapters: list[Chapter], duration: float) -> list[str]:
    """Why YouTube would reject these chapters (empty list = fine)."""
    issues: list[str] = []
    if len(chapters) < 3:
        issues.append(f"YouTube needs at least 3 chapters (you have {len(chapters)}).")
    if chapters and int(chapters[0].time) != 0:
        issues.append("The first chapter must start at 0:00.")
    secs = [int(c.time) for c in chapters]
    if len(set(secs)) != len(secs):
        issues.append("Two chapters share the same second.")
    for i, c in enumerate(chapters):
        end = chapters[i + 1].time if i + 1 < len(chapters) else (duration or c.time + 10)
        if end - c.time < 10:
            issues.append(f"'{c.title}' is shorter than 10 seconds.")
            break
    return issues
