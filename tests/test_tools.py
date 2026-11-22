"""The tool list: declared once (core/tools.py), built by tabs/registry.py."""

from __future__ import annotations

from core import catalog, tools
from core.tools import Category, ToolId
from tabs import registry


def test_every_tool_has_exactly_one_page_and_one_spec():
    assert set(registry.PAGES) == set(ToolId) == set(tools.BY_ID)
    assert len({s.name for s in tools.SPECS}) == len(tools.SPECS)  # names are unique too


def test_every_openable_tool_explains_itself():
    for s in tools.SPECS:
        if s.category is not Category.HIDDEN:
            assert s.icon and s.blurb and s.words, s.name


def test_ids_are_stable_slugs():
    for tid in ToolId:
        assert tid.value.isidentifier() and tid.value.islower(), tid


def test_old_names_are_understood():
    assert tools.parse("Quick Trimmer") is ToolId.QUICK_TRIMMER
    assert tools.parse("trimmer") is ToolId.QUICK_TRIMMER
    assert tools.parse("A tool that was removed") is None
    assert tools.parse_all(["Media Inspector", "inspector?", "media_inspector", "trimmer"]) == [
        ToolId.MEDIA_INSPECTOR,
        ToolId.QUICK_TRIMMER,
    ]


def test_the_name_keyed_views_agree_with_the_specs():
    assert sum(len(v) for v in registry.TOOLS.values()) == sum(s.in_sidebar for s in tools.SPECS)
    assert registry.PINNED[0] == catalog.AIO
    assert set(registry.HIDDEN) == {"Settings", "Home", "Start"}
    for goal in catalog.GOALS:
        assert all(name in catalog.TOOLS for name in goal.tools), goal.key
