"""
core/ass.py - write Advanced SubStation (ASS) scripts for animated text.

Titles, intro cards and captions are rendered by libass through FFmpeg's
``subtitles=`` filter.  libass gives real typography (any bundled font,
bold, outlines, shadows), vector shapes for bars/boxes, and animation tags
(``\\move``, ``\\fad``, ``\\t``) - far more than drawtext can do, and user text
needs no filtergraph escaping because it lives in a file.

Coordinates are in pixels of the target video: the script's PlayResX/Y is
set to the video size, so 1 unit = 1 pixel.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field

from core.timecode import Timecode

ZWSP = "​"


def escape(text: str) -> str:
    """User text -> ASS dialogue text that libass shows literally.

    Braces would open override blocks and backslash sequences (\\N, \\h…)
    are commands, so backslashes get an invisible zero-width space after
    them and braces are escaped.  Real newlines become ``\\N``."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\", "\\" + ZWSP)
    text = text.replace("{", "\\{").replace("}", "\\}")
    return text.replace("\n", "\\N")


def colour(hex_rgb: str, alpha: float = 0.0) -> str:
    """'#RRGGBB' + transparency (0 = opaque, 1 = invisible) -> '&HAABBGGRR'."""
    v = hex_rgb.strip().lstrip("#")
    if len(v) != 6:
        v = "FFFFFF"
    a = max(0, min(255, round(alpha * 255)))
    return f"&H{a:02X}{v[4:6]}{v[2:4]}{v[0:2]}".upper()


def alpha_tag(alpha: float) -> str:
    return f"&H{max(0, min(255, round(alpha * 255))):02X}&"


def ts(seconds: float) -> str:
    """Seconds -> ASS time 'H:MM:SS.cc'."""
    return f"{Timecode.of(seconds):ass}"


def rect(w: float, h: float) -> str:
    """Vector drawing of a w x h rectangle (use inside {\\p1} ... {\\p0})."""
    w, h = round(w), round(h)
    return f"m 0 0 l {w} 0 {w} {h} 0 {h}"


def box(
    w: float, h: float, *, x: float, y: float, fill: str, alpha: float = 0.0, extra: str = ""
) -> str:
    """Dialogue text for a filled rectangle at (x, y) (top-left)."""
    return (
        f"{{\\an7\\pos({round(x)},{round(y)})\\bord0\\shad0\\1c{colour(fill)}"
        f"\\1a{alpha_tag(alpha)}{extra}\\p1}}{rect(w, h)}{{\\p0}}"
    )


def word_reveal(text: str, start_ms: int, step_ms: int) -> str:
    """Words appear one by one (layout stays put: hidden words keep space)."""
    out: list[str] = []
    for i, word in enumerate((text or "").split(" ")):
        t0 = start_ms + i * step_ms
        out.append(f"{{\\alpha&HFF&\\t({t0},{t0 + 1},\\alpha&H00&)}}{escape(word)}")
    return " ".join(out)


def text_width(text: str, size: float, *, bold: bool = True) -> float:
    """Rough rendered width of *text* (for sizing boxes around it)."""
    longest = max((len(line) for line in (text or "").split("\n")), default=0)
    return longest * size * (0.6 if bold else 0.53)


@dataclass
class Style:
    name: str
    font: str = "Montserrat"
    size: int = 48
    colour: str = "#FFFFFF"
    outline_colour: str = "#000000"
    back_colour: str = "#000000"
    back_alpha: float = 0.6
    bold: bool = False
    italic: bool = False
    outline: float = 0.0
    shadow: float = 0.0
    alignment: int = 7  # numpad layout: 1-3 bottom, 4-6 middle, 7-9 top
    margin_l: int = 0
    margin_r: int = 0
    margin_v: int = 0
    border_style: int = 1  # 1 = outline + shadow, 3 = opaque box

    def line(self) -> str:
        return ",".join(
            str(x)
            for x in (
                self.name,
                self.font,
                int(self.size),
                colour(self.colour),
                colour(self.colour),
                colour(self.outline_colour),
                colour(self.back_colour, self.back_alpha),
                -1 if self.bold else 0,
                -1 if self.italic else 0,
                0,
                0,
                100,
                100,
                0,
                0,
                self.border_style,
                f"{self.outline:g}",
                f"{self.shadow:g}",
                self.alignment,
                self.margin_l,
                self.margin_r,
                self.margin_v,
                1,
            )
        )


Event = tuple[int, float, float, str, str]  # layer, start s, end s, style, text


@dataclass
class Script:
    width: int
    height: int
    styles: list[Style] = field(default_factory=list[Style])
    events: list[Event] = field(default_factory=list[Event])

    def style(self, style: Style) -> Style:
        self.styles = [s for s in self.styles if s.name != style.name] + [style]
        return style

    def add(
        self, start: float, end: float, text: str, style: str = "Default", layer: int = 0
    ) -> None:
        if end > start:
            self.events.append((layer, start, end, style, text))

    def render(self) -> str:
        styles = self.styles or [Style("Default")]
        head = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {self.width}",
            f"PlayResY: {self.height}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: None",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            *[f"Style: {s.line()}" for s in styles],
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        ev = [
            f"Dialogue: {layer},{ts(a)},{ts(b)},{st},,0,0,0,,{txt}"
            for layer, a, b, st, txt in sorted(self.events, key=lambda e: (e[1], e[0]))
        ]
        return "\n".join(head + ev) + "\n"

    def write(self, path: str) -> str:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.render())
        os.replace(tmp, path)
        return path


def fonts_dir() -> str | None:
    """Folder of bundled fonts handed to libass (``fontsdir=``)."""
    from core.filters import bundled_font_dir

    d = bundled_font_dir()
    return d if d and os.path.isdir(d) else None


BUNDLED_FAMILIES = [
    "Montserrat",
    "Open Sans",
    "Poppins",
    "Lato",
    "Raleway",
    "Bebas Neue",
    "Anton",
    "Bangers",
    "Luckiest Guy",
    "Bungee",
    "Roboto Black",
]


def subtitles_filter(path: str) -> str:
    from core.filters import subtitles_filter as _sf

    return _sf(path, fonts_dir=fonts_dir())


def families(extra: Iterable[str] = ()) -> list[str]:
    return list(dict.fromkeys([*BUNDLED_FAMILIES, *extra]))
