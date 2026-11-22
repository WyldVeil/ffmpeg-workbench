"""
core/ops/titles.py - animated title cards (tabs/social/titles.py): lower thirds,
chapter titles, name tags, kinetic pop-ups and subtitle strips.

Pure functions: cards in, a libass subtitle script out (core/ass.py) - no
widgets, no Tk.  Layout scales with the video size, so 720p, 4K and vertical
videos all look right.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core import ass


@dataclass
class Card:
    style: str = "lower_third"
    line1: str = "Title Text"
    line2: str = ""
    start: float = 1.0
    duration: float = 4.0
    accent: str = "#1565C0"


@dataclass(frozen=True)
class _Shot:
    """One card on a W x H frame: scale *u*, its times, fade lengths, escaped text."""

    W: float
    H: float
    u: float
    a_ms: int
    s: float
    e: float
    fin: int
    fout: int
    l1: str
    l2: str


def _chapter(sc: ass.Script, c: Card, k: _Shot) -> None:
    W, H, u, s, e = k.W, k.H, k.u, k.s, k.e
    s1, s2 = round(84 * u), round(40 * u)
    sc.add(
        s,
        e,
        ass.box(W, H, x=0, y=0, fill="#000000", alpha=0.45, extra=f"\\fad({k.fin},{k.fout})"),
        "Box",
        0,
    )
    ty = H / 2 - (s2 * 0.9 if c.line2 else 0)
    sc.add(
        s,
        e,
        f"{{\\an5\\pos({W / 2:.0f},{ty:.0f})\\fs{s1}\\fsp{2 * u:.1f}"
        f"\\fad({k.fin},{k.fout})\\fscx92\\fscy92\\t(0,{k.a_ms * 2},\\fscx100\\fscy100)}}{k.l1}",
        "Title",
        2,
    )
    line_w = min(W * 0.5, max(ass.text_width(c.line1, s1) * 0.6, 120 * u))
    sc.add(
        s,
        e,
        f"{{\\an8\\pos({W / 2:.0f},{ty + s1 * 0.62:.0f})\\bord0\\shad0"
        f"\\1c{ass.colour(c.accent)}\\fad({k.fin},{k.fout})\\fscx0"
        f"\\t(0,{k.a_ms * 2},\\fscx100)\\p1}}{ass.rect(line_w, max(3, 6 * u))}{{\\p0}}",
        "Box",
        1,
    )
    if c.line2:
        sc.add(
            s,
            e,
            f"{{\\an8\\pos({W / 2:.0f},{ty + s1 * 0.62 + 18 * u:.0f})\\fs{s2}\\fad({k.fin + 150},{k.fout})}}{k.l2}",
            "Sub",
            2,
        )


def _kinetic(sc: ass.Script, c: Card, k: _Shot) -> None:
    W, H, u, s, e = k.W, k.H, k.u, k.s, k.e
    s1, s2 = round(104 * u), round(44 * u)
    ty = H / 2 - (s2 if c.line2 else 0)
    sc.add(
        s,
        e,
        f"{{\\an5\\pos({W / 2:.0f},{ty:.0f})\\fs{s1}\\bord{5 * u:.1f}"
        f"\\3c{ass.colour(c.accent)}\\fad(0,{k.fout})\\fscx0\\fscy0"
        f"\\t(0,180,\\fscx115\\fscy115)\\t(180,320,\\fscx100\\fscy100)}}{k.l1}",
        "Title",
        2,
    )
    if c.line2:
        sc.add(
            s,
            e,
            f"{{\\an8\\pos({W / 2:.0f},{ty + s1 * 0.62:.0f})\\fs{s2}\\fad(0,{k.fout})}}{ass.word_reveal(c.line2, 350, 170)}",
            "Sub",
            2,
        )


def _strip(sc: ass.Script, c: Card, k: _Shot) -> None:
    W, H, u, s, e = k.W, k.H, k.u, k.s, k.e
    s1 = round(40 * u)
    bh = s1 * (1.0 + 1.25 * (2 if c.line2 else 1))
    sc.add(
        s,
        e,
        ass.box(W, bh, x=0, y=H - bh, fill="#000000", alpha=0.35, extra=f"\\fad({k.fin},{k.fout})"),
        "Box",
        0,
    )
    text = k.l1 + (f"\\N{{\\fs{round(s1 * 0.8)}\\b0}}{k.l2}" if c.line2 else "")
    sc.add(
        s,
        e,
        f"{{\\an5\\pos({W / 2:.0f},{H - bh / 2:.0f})\\fs{s1}\\fad({k.fin},{k.fout})}}{text}",
        "Title",
        2,
    )


def _lower_third(sc: ass.Script, c: Card, k: _Shot) -> None:
    """The sliding bar (or, for name tags, a corner box with an accent stripe)."""
    W, H, u, s, e, a_ms = k.W, k.H, k.u, k.s, k.e, k.a_ms
    tag = c.style == "name_tag"
    s1, s2 = round((40 if tag else 54) * u), round((26 if tag else 34) * u)
    pad, stripe = (18 if tag else 28) * u, (10 if tag else 0) * u
    text_w = max(ass.text_width(c.line1, s1), ass.text_width(c.line2, s2, bold=False))
    bw = min(W * 0.9, max(W * (0.18 if tag else 0.3), text_w + 2 * pad + stripe))
    bh = pad * 1.3 + s1 * 1.15 + (s2 * 1.3 if c.line2 else 0)
    x0, y0 = W * 0.04, (H * 0.06 if tag else H - bh - H * 0.08)
    fill, falpha = ("#111111", 0.15) if tag else (c.accent, 0.08)
    slide = f"\\an7\\move({-bw:.0f},{y0:.0f},{x0:.0f},{y0:.0f},0,{a_ms})\\bord0"
    sc.add(
        s,
        e,
        f"{{{slide}\\shad0\\1c{ass.colour(fill)}\\1a{ass.alpha_tag(falpha)}\\fad(0,{k.fout})\\p1}}{ass.rect(bw, bh)}{{\\p0}}",
        "Box",
        0,
    )
    if stripe:
        sc.add(
            s,
            e,
            f"{{{slide}\\shad0\\1c{ass.colour(c.accent)}\\fad(0,{k.fout})\\p1}}{ass.rect(stripe, bh)}{{\\p0}}",
            "Box",
            1,
        )
    tx, ty = x0 + pad + stripe, y0 + pad * 0.65
    sc.add(
        s,
        e,
        f"{{\\an7\\move({tx - 60 * u:.0f},{ty:.0f},{tx:.0f},{ty:.0f},80,{a_ms + 80})\\fs{s1}\\fad({k.fin},{k.fout})}}{k.l1}",
        "Title",
        2,
    )
    if c.line2:
        ty2 = ty + s1 * 1.18
        sc.add(
            s,
            e,
            f"{{\\an7\\move({tx - 60 * u:.0f},{ty2:.0f},{tx:.0f},{ty2:.0f},160,{a_ms + 160})\\fs{s2}\\fad({k.fin + 120},{k.fout})}}{k.l2}",
            "Sub",
            2,
        )


_STYLES: dict[str, Callable[[ass.Script, Card, _Shot], None]] = {
    "chapter": _chapter,
    "kinetic": _kinetic,
    "strip": _strip,
}


def build_script(
    cards: Sequence[Card],
    width: int,
    height: int,
    *,
    font: str = "Montserrat",
    scale: float = 1.0,
    anim: float = 0.35,
) -> ass.Script:
    """ASS script with every card laid out for a width x height video."""
    sc = ass.Script(width, height)
    u = min(width, height) / 1080 * scale
    sc.style(ass.Style("Box", font=font, size=20, bold=False))
    sc.style(
        ass.Style(
            "Title",
            font=font,
            size=round(54 * u),
            bold=True,
            shadow=2 * u,
            back_colour="#000000",
            back_alpha=0.5,
        )
    )
    sc.style(
        ass.Style(
            "Sub",
            font=font,
            size=round(34 * u),
            colour="#EEEEEE",
            shadow=1.5 * u,
            back_colour="#000000",
            back_alpha=0.5,
        )
    )
    a_ms = int(anim * 1000)
    for c in cards:
        if c.duration <= 0 or not (c.line1.strip() or c.line2.strip()):
            continue
        fade = min(a_ms, int(c.duration * 300))
        shot = _Shot(
            width,
            height,
            u,
            a_ms,
            c.start,
            c.start + c.duration,
            fade,
            fade,
            ass.escape(c.line1),
            ass.escape(c.line2),
        )
        _STYLES.get(c.style, _lower_third)(sc, c, shot)
    return sc
