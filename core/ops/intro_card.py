"""
core/ops/intro_card.py - title cards (intros and outros), for tabs/social/intro_maker.py.

Pure functions: values in, a subtitle script and FFmpeg arguments out - no
widgets, no Tk.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import ass
from core import ffmpeg as ff


@dataclass(frozen=True, slots=True, kw_only=True)
class Card:
    """A title card's text and how it moves.  *scale* sizes the text (1.0 =
    medium); *animation*: "fade", "slide", "zoom", "typewriter" or "static";
    *position*: "centre", "lower" (third) or "top"."""

    title: str
    subtitle: str = ""
    font: str = "Montserrat"
    scale: float = 1.0
    colour: str = "#FFFFFF"
    accent: str = "#FFB300"
    animation: str = "fade"
    position: str = "centre"


def card_script(card: Card, width: int, height: int, duration: float) -> ass.Script:
    """The card's text as a subtitle script for a *width* x *height* card of *duration* s."""
    title, subtitle, font, scale = card.title, card.subtitle, card.font, card.scale
    colour, accent, animation, position = card.colour, card.accent, card.animation, card.position
    sc = ass.Script(width, height)
    u = min(width, height) / 1080 * scale
    s1, s2 = round(96 * u), round(44 * u)
    sc.style(
        ass.Style(
            "Title",
            font=font,
            size=s1,
            bold=True,
            colour=colour,
            shadow=3 * u,
            back_colour="#000000",
            back_alpha=0.45,
        )
    )
    sc.style(
        ass.Style(
            "Sub",
            font=font,
            size=s2,
            colour=colour,
            shadow=2 * u,
            back_colour="#000000",
            back_alpha=0.5,
        )
    )
    if position == "lower":
        an, x, y1 = 4, width * 0.07, height * 0.70
    elif position == "top":
        an, x, y1 = 8, width / 2, height * 0.12
    else:
        an, x, y1 = 5, width / 2, height / 2 - (s2 * 0.75 if subtitle else 0)
    y2 = y1 + s1 * (0.85 if an == 5 or an == 4 else 1.15)
    end = max(0.2, duration)
    out_ms = min(600, int(duration * 250))

    def anim(y: float, delay: int) -> str:
        if animation == "slide":
            return (
                f"\\move({x:.0f},{y + 70 * u:.0f},{x:.0f},{y:.0f},{delay},{delay + 600})"
                f"\\fad({300 + delay},{out_ms})"
            )
        if animation == "zoom":
            return (
                f"\\pos({x:.0f},{y:.0f})\\fscx70\\fscy70\\t({delay},{delay + 900},"
                f"\\fscx100\\fscy100)\\fad({250 + delay},{out_ms})"
            )
        if animation == "static":
            return f"\\pos({x:.0f},{y:.0f})"
        if animation == "typewriter":
            return f"\\pos({x:.0f},{y:.0f})\\fad(0,{out_ms})"
        return f"\\pos({x:.0f},{y:.0f})\\fad({500 + delay},{out_ms})"

    def body(text: str, start_ms: int) -> str:
        if animation != "typewriter":
            return ass.escape(text)
        step = max(25, min(90, int(duration * 450 / max(1, len(text)))))
        return "".join(
            f"{{\\alpha&HFF&\\t({start_ms + i * step},{start_ms + i * step + 1},"
            f"\\alpha&H00&)}}{ass.escape(ch)}"
            for i, ch in enumerate(text)
        )

    if title.strip():
        sc.add(0, end, f"{{\\an{an}{anim(y1, 0)}}}{body(title, 0)}", "Title", 2)
    if subtitle.strip():
        delay = 250 if animation != "typewriter" else int(duration * 450)
        sc.add(
            0,
            end,
            f"{{\\an{an}{anim(y2, 250)}\\3c{ass.colour(accent)}}}{body(subtitle, delay)}",
            "Sub",
            2,
        )
    return sc


@dataclass(frozen=True, slots=True)
class Background:
    """Behind the card: *kind* "colour", "black", "gradient" (*colour* to
    *colour2* in *direction*) or "file" (a picture or video at *path*)."""

    kind: str = "colour"
    colour: str = "#202020"
    colour2: str = "#7C3AED"
    direction: str = "horizontal"
    path: str = ""


def background_input(bg: Background, w: int, h: int, fps: str, dur: float) -> tuple[list[str], str]:
    """(input args, filter applied to input 0) for the card background."""
    kind, colour, colour2, direction, path = bg.kind, bg.colour, bg.colour2, bg.direction, bg.path
    size = f"size={w}x{h}:rate={fps}:duration={dur:.3f}"
    c1, c2 = colour.lstrip("#"), colour2.lstrip("#")
    if kind == "gradient":
        coords = {
            "horizontal": f"x0=0:y0=0:x1={w}:y1=0",
            "vertical": f"x0=0:y0=0:x1=0:y1={h}",
            "diagonal": f"x0=0:y0=0:x1={w}:y1={h}",
            "radial": f"x0={w // 2}:y0={h // 2}:x1={w}:y1={h}",
        }[direction]
        typ = ":type=radial" if direction == "radial" else ""
        return (
            [
                "-f",
                "lavfi",
                "-i",
                f"gradients={size}:c0=0x{c1}:c1=0x{c2}:nb_colors=2:speed=0:{coords}{typ}",
            ],
            "format=yuv420p",
        )
    if kind == "file" and path:
        info = ff.try_probe(path)
        fit = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
            f"fps={fps},format=yuv420p"
        )
        if info is not None and info.is_image:
            return ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", path], fit
        return ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", path], fit
    col = "000000" if kind == "black" else c1
    return ["-f", "lavfi", "-i", f"color=c=0x{col}:{size}"], "format=yuv420p"
