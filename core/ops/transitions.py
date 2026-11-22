"""
core/ops/transitions.py - every transition between two clips, grouped the way a
person looks for them (the Slideshow Maker and the Crossfader).

FFmpeg's ``xfade`` has 58 built-in transitions; two more are made here as
custom expressions:

* **Fading squares** - the picture splits into squares that change over one
  by one, each fading in at its own moment;
* **Swirl** - the first picture twists into a whirlpool and unwinds as the
  second (slower to make: every pixel is computed).

Pure values: names in, ``xfade`` options out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: group -> {name: label}, in the order they are offered
GROUPS: Final[Mapping[str, Mapping[str, str]]] = {
    "Fades": {
        "fade": "Crossfade",
        "fadefast": "Crossfade (quick)",
        "fadeslow": "Crossfade (gentle)",
        "dissolve": "Dissolve",
        "fadeblack": "Through black",
        "fadewhite": "Through white",
        "fadegrays": "Through grey",
        "hblur": "Blur",
    },
    "Wipes": {
        "wipeleft": "Wipe left",
        "wiperight": "Wipe right",
        "wipeup": "Wipe up",
        "wipedown": "Wipe down",
        "wipetl": "Wipe to top left",
        "wipetr": "Wipe to top right",
        "wipebl": "Wipe to bottom left",
        "wipebr": "Wipe to bottom right",
    },
    "Slides and pushes": {
        "slideleft": "Slide left",
        "slideright": "Slide right",
        "slideup": "Slide up",
        "slidedown": "Slide down",
        "coverleft": "Cover from the right",
        "coverright": "Cover from the left",
        "coverup": "Cover from below",
        "coverdown": "Cover from above",
        "revealleft": "Reveal to the left",
        "revealright": "Reveal to the right",
        "revealup": "Reveal upwards",
        "revealdown": "Reveal downwards",
    },
    "Soft edges": {
        "smoothleft": "Soft wipe left",
        "smoothright": "Soft wipe right",
        "smoothup": "Soft wipe up",
        "smoothdown": "Soft wipe down",
        "hlwind": "Wind to the left",
        "hrwind": "Wind to the right",
        "vuwind": "Wind upwards",
        "vdwind": "Wind downwards",
    },
    "Shapes": {
        "circleopen": "Circle opening",
        "circleclose": "Circle closing",
        "circlecrop": "Circle (through black)",
        "rectcrop": "Rectangle (through black)",
        "radial": "Clock sweep",
        "vertopen": "Doors opening",
        "vertclose": "Doors closing",
        "horzopen": "Blinds opening",
        "horzclose": "Blinds closing",
        "diagtl": "Diagonal from top left",
        "diagtr": "Diagonal from top right",
        "diagbl": "Diagonal from bottom left",
        "diagbr": "Diagonal from bottom right",
    },
    "Slices": {
        "hlslice": "Slices to the left",
        "hrslice": "Slices to the right",
        "vuslice": "Slices upwards",
        "vdslice": "Slices downwards",
    },
    "Effects": {
        "squares": "Fading squares",
        "squares_big": "Fading squares (large)",
        "swirl": "Swirl (slower to make)",
        "pixelize": "Pixelate",
        "distance": "Melt",
        "squeezeh": "Squeeze sideways",
        "squeezev": "Squeeze up and down",
        "zoomin": "Zoom in",
    },
}

#: every name -> its label
LABELS: Final[Mapping[str, str]] = {
    name: label for group in GROUPS.values() for name, label in group.items()
}

#: the transitions that suit any pair of pictures (for "random"): no
#: through-black, no slow custom ones
EVERYDAY: Final[tuple[str, ...]] = (
    "fade",
    "dissolve",
    "wipeleft",
    "wiperight",
    "slideleft",
    "slideright",
    "slideup",
    "smoothleft",
    "smoothright",
    "coverleft",
    "revealright",
    "circleopen",
    "radial",
    "vertopen",
    "horzopen",
    "diagtl",
    "diagbr",
    "hlslice",
    "squares",
    "zoomin",
)

SLOW: Final[frozenset[str]] = frozenset({"swirl"})


def _squares(across: int) -> str:
    """Squares *across* the picture (about as many down as its shape gives),
    each with its own pseudo-random moment.  Positions are relative to the
    plane (X/W), so the colour planes - smaller than the brightness plane -
    get the same squares; no st()/ld(): they are shared between threads."""
    down = max(1, round(across * 9 / 16))
    h = f"(sin(floor(X/W*{across})*12.9898+floor(Y/H*{down})*78.233)*43758.5453)"
    moment = f"({h}-floor({h}))"
    mix = f"clip(((1-P)*1.6-{moment})/0.6,0,1)"  # P runs 1 -> 0 in xfade
    return f"A*(1-{mix})+B*{mix}"


def _swirl() -> str:
    """Each pixel turned about the centre by an angle that is largest in the
    middle of the picture and of the transition; the pictures swap at the peak."""
    q = "(1-P)"
    dx, dy = "(X-W/2)", "(Y-H/2)"
    angle = f"(sin(PI*{q})*5*pow(max(0,1-hypot({dx},{dy})/hypot(W/2,H/2)),2))"
    xs = f"clip(W/2+{dx}*cos({angle})-{dy}*sin({angle}),0,W-1)"
    ys = f"clip(H/2+{dx}*sin({angle})+{dy}*cos({angle}),0,H-1)"

    def at(src: str) -> str:
        planes = [f"{src}{n}({xs},{ys})" for n in range(4)]
        return f"if(eq(PLANE,0),{planes[0]},if(eq(PLANE,1),{planes[1]},if(eq(PLANE,2),{planes[2]},{planes[3]})))"

    mix = f"clip(({q}-0.35)/0.3,0,1)"
    return f"{at('a')}*(1-{mix})+{at('b')}*{mix}"


_CUSTOM: Final[Mapping[str, str]] = {
    "squares": _squares(16),
    "squares_big": _squares(6),
    "swirl": _swirl(),
}


def xfade(name: str, duration: float, offset: float, *, digits: int = 3) -> str:
    """The ``xfade=...`` filter (without its input labels) for transition *name*
    (times to *digits* decimals)."""
    timing = f"duration={duration:.{digits}f}:offset={offset:.{digits}f}"
    if name in _CUSTOM:
        return f"xfade=transition=custom:{timing}:expr='{_CUSTOM[name]}'"
    if name not in LABELS:
        raise ValueError(f"unknown transition {name!r}")
    return f"xfade=transition={name}:{timing}"
