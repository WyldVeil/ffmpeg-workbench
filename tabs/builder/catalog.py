"""
tabs/builder/catalog.py - which tools can be a step in the All-in-One Builder.

A step tool takes one video and makes one file from it.  Tools that combine
several clips, make many files, record or download aren't steps.

* ``OWN_FORMAT`` - tools whose encoding is the point (a GIF, a WebM, a target
  size, sound only): they decide the file the builder makes when last, and
  their output settings stay visible in their pop-up.
* ``PLACED`` - tools whose settings are positions or times on the picture:
  when an earlier step changes, they are flagged for a check.
* ``ONE_VIDEO`` - tools whose settings only make sense for the video they
  were set up on (the Text Blurrer's picks): a recipe used on another video
  asks for them to be set up again.
"""

from __future__ import annotations

from typing import Final

from core.tools import Category, ToolId, spec
from tabs.kit import labels

QUICK: Final = "quick"  # the builder's own "Quick adjustments" step

STEP_TOOLS: Final[tuple[ToolId, ...]] = (
    # Cutting room
    ToolId.QUICK_TRIMMER,
    ToolId.MANUAL_CROP,
    ToolId.ROTATE_AND_FLIP,
    ToolId.FREEZE_FRAME,
    ToolId.VIDEO_REVERSER,
    ToolId.CLIP_LOOPER,
    ToolId.SMART_REFRAME,
    # Social & format
    ToolId.SHORTIFIER,
    ToolId.AUTO_CROPPER,
    ToolId.WATERMARKER,
    ToolId.IMAGE_WATERMARK,
    ToolId.HARD_SUBBER,
    ToolId.ANIMATED_TITLES,
    ToolId.INTRO_OUTRO_MAKER,
    ToolId.PRO_GIF_MAKER,
    ToolId.WEBM_MAKER,
    ToolId.THUMBNAIL_MAKER,
    # Audio
    ToolId.SILENCE_REMOVER,
    ToolId.LOUDNESS_NORMALIZER,
    ToolId.AUDIO_DYNAMICS,
    ToolId.AUDIO_SYNC_SHIFTER,
    ToolId.MUTER,
    ToolId.AUDIO_REPLACER,
    ToolId.AUDIO_MIXER,
    ToolId.MUSIC_DUCKER,
    ToolId.VOICE_ISOLATION,
    ToolId.VOICE_CHANGER,
    ToolId.LAUGH_TRACK_REMOVER,
    ToolId.TTS_VOICE_OVER,
    ToolId.AUDIO_EXTRACTOR,
    ToolId.AUDIO_CONVERTER,
    # Transcoder
    ToolId.RESOLUTION_SCALER,
    ToolId.FRAMERATE_INTERPOLATOR,
    ToolId.HDR_TO_SDR,
    ToolId.UNIVERSAL_DOWNSIZER,
    ToolId.FORMAT_CONVERTER,
    # Colour & visuals
    ToolId.BASIC_COLOR_CORRECTOR,
    ToolId.LUT_APPLICATOR,
    ToolId.COLOUR_MATCH,
    ToolId.SPECIAL_EFFECTS,
    ToolId.SPEED_RAMPER,
    ToolId.DESHAKER,
    ToolId.DENOISE,
    ToolId.DEINTERLACE,
    ToolId.SHARPEN,
    ToolId.GREEN_SCREEN_KEYER,
    ToolId.PICTURE_IN_PICTURE,
    ToolId.PRIVACY_BLUR,
    ToolId.TEXT_BLURRER,
    ToolId.ANIMATED_ZOOM,
)

OWN_FORMAT: Final = frozenset(
    {
        ToolId.PRO_GIF_MAKER,
        ToolId.WEBM_MAKER,
        ToolId.THUMBNAIL_MAKER,
        ToolId.AUDIO_EXTRACTOR,
        ToolId.AUDIO_CONVERTER,
        ToolId.UNIVERSAL_DOWNSIZER,
        ToolId.FORMAT_CONVERTER,
    }
)

PLACED: Final = frozenset(
    {
        ToolId.QUICK_TRIMMER,
        ToolId.MANUAL_CROP,
        ToolId.FREEZE_FRAME,
        ToolId.CLIP_LOOPER,
        ToolId.WATERMARKER,
        ToolId.IMAGE_WATERMARK,
        ToolId.HARD_SUBBER,
        ToolId.ANIMATED_TITLES,
        ToolId.THUMBNAIL_MAKER,
        ToolId.MUTER,
        ToolId.PICTURE_IN_PICTURE,
        ToolId.PRIVACY_BLUR,
        ToolId.TEXT_BLURRER,
        ToolId.ANIMATED_ZOOM,
        ToolId.SPEED_RAMPER,
        ToolId.TTS_VOICE_OVER,
    }
)

ONE_VIDEO: Final = frozenset({ToolId.TEXT_BLURRER})


def kind_of(kind: str) -> ToolId | None:
    """The tool a step kind names (None for the builder's own quick step)."""
    return None if kind == QUICK else ToolId(kind)


def name(kind: str) -> str:
    """A step kind's name, as the person reads it."""
    from core.i18n import t

    tool = kind_of(kind)
    return labels.tool(tool) if tool is not None else t("builder.quick")


def icon(kind: str) -> str:
    tool = kind_of(kind)
    return spec(tool).icon if tool is not None else "🎛"


def menu() -> list[tuple[str, list[str]]]:
    """The "Add a step" menu: (section heading, step kinds), sidebar order."""
    groups: dict[Category, list[str]] = {}
    for tool in STEP_TOOLS:
        groups.setdefault(spec(tool).category, []).append(tool.value)
    order = [
        Category.CUTTING,
        Category.SOCIAL,
        Category.AUDIO,
        Category.TRANSCODER,
        Category.VISUALS,
    ]
    return [(labels.menu_section(c), groups[c]) for c in order if c in groups]


def known(kind: str) -> bool:
    """True for the kinds a step can be (a recipe may name others)."""
    return kind == QUICK or kind in {t.value for t in STEP_TOOLS}


def own_format(kind: str) -> bool:
    return kind_of(kind) in OWN_FORMAT


def placed(kind: str) -> bool:
    return kind == QUICK or kind_of(kind) in PLACED


def one_video(kind: str) -> bool:
    return kind_of(kind) in ONE_VIDEO
