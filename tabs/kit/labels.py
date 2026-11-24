"""tabs/kit/labels.py - what the window calls tools and sidebar sections, in the
person's language (the English names in core/tools.py are the fallback)."""

from __future__ import annotations

import re
import unicodedata

from core.i18n import t
from core.tools import Category, ToolId, spec

_SECTION_KEYS: dict[Category, str] = {
    Category.CUTTING: "cat.cutting",
    Category.SOCIAL: "cat.social",
    Category.AUDIO: "cat.audio",
    Category.TRANSCODER: "cat.transcoder",
    Category.VISUALS: "cat.visuals",
    Category.SYSTEM: "cat.system",
}


def _section_text(category: Category) -> str:
    key = _SECTION_KEYS.get(category)
    return t(key) if key else category.value.strip()


def capitals(text: str) -> str:
    """*text* in capitals.  Greek capitals drop their accents (tonos), as Greek
    is typeset; other letters keep theirs (Czech Č, Hungarian Á)."""
    upper = unicodedata.normalize("NFD", text.upper())
    return unicodedata.normalize("NFC", re.sub("(?<=[\u0370-\u03ff])\u0301", "", upper))


def section(category: Category) -> str:
    """A sidebar heading, translated and in capitals."""
    return capitals(_section_text(category))


def menu_section(category: Category) -> str:
    """The same heading for the menu bar: as the translation writes it, or
    title-cased when it is written in capitals (capitals lose Greek accents,
    so title-casing them back can't restore them)."""
    text = _section_text(category)
    return text.title() if text.isupper() else text


def tool(tid: ToolId) -> str:
    """A tool's name as shown, translated when a translation exists."""
    name = spec(tid).name
    slug = re.sub(
        r"_+",
        "_",
        name.lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace("&", "")
        .strip("_"),
    )
    translated = t("tab." + slug)
    return name if translated.startswith("tab.") else translated
