"""tabs/kit/labels.py - what the window calls tools and sidebar sections, in the
person's language (the English names in core/tools.py are the fallback)."""
from __future__ import annotations

import re

from core.i18n import t
from core.tools import Category, ToolId, spec

_SECTION_KEYS: dict[Category, str] = {
    Category.CUTTING: "cat.cutting", Category.SOCIAL: "cat.social", Category.AUDIO: "cat.audio",
    Category.TRANSCODER: "cat.transcoder", Category.VISUALS: "cat.visuals", Category.SYSTEM: "cat.system",
}


def section(category: Category) -> str:
    """A sidebar heading, translated and in capitals."""
    key = _SECTION_KEYS.get(category)
    return (t(key) if key else category.value.strip()).upper()


def tool(tid: ToolId) -> str:
    """A tool's name as shown, translated when a translation exists."""
    name = spec(tid).name
    slug = re.sub(r"_+", "_", name.lower().replace(" ", "_").replace("-", "_").replace("/", "_")
                  .replace("&", "").strip("_"))
    translated = t("tab." + slug)
    return name if translated.startswith("tab.") else translated
