"""Start page ("What do you want to do?") and its catalogue."""
import os
import subprocess

import pytest

from core import catalog, settings
from core import ffmpeg as ff
from core.tools import ToolId

# ── catalogue ────────────────────────────────────────────────────────────────

def test_every_tool_is_described_and_reachable_from_a_goal():
    from tabs.registry import PINNED, TOOLS
    names = {n for items in TOOLS.values() for n, _c in items} | {PINNED[0]}
    assert set(catalog.TOOLS) == names
    in_goals = {t for g in catalog.GOALS for t in g.tools}
    assert names <= in_goals, names - in_goals
    assert all(info.blurb and info.icon for info in catalog.TOOLS.values())
    assert len({g.key for g in catalog.GOALS}) == len(catalog.GOALS)


@pytest.mark.parametrize("query,first", [
    ("make it smaller", "Universal Downsizer"), ("my video is too big", "Universal Downsizer"),
    ("vertical for tiktok", "The Shortifier"), ("background noise in my voice", "Voice Isolation"),
    ("subtitles", "Auto-Subtitles"), ("captions", "Auto-Subtitles"), ("remove gps location", "Metadata Cleaner"),
    ("put videos side by side", "Side-by-Side"), ("slow motion", "Framerate Interpolator"),
    ("louder", "Loudness Normalizer"), ("shaky", "Deshaker"), ("stabilize", "Deshaker"),
    ("black bars", "Auto-Cropper"), ("sideways", "Rotate & Flip"), ("gif", "Pro GIF Maker"),
    ("download from youtube", "Video Downloader"), ("blur a face", "Privacy Blur"), ("green screen", "Green Screen Keyer"),
    ("color correction", "Basic Color Corrector"), ("record my screen", "Screen Recorder"), ("logo", "Image Watermark"),
    ("remove pauses", "Silence Remover"), ("60fps", "Framerate Interpolator"), ("upscale to 4k", "Resolution Scaler"),
])
def test_search_understands_everyday_words(query, first):
    assert catalog.search(query)[0] == first


def test_search_ignores_filler():
    assert catalog.search("the") == [] and catalog.search("") == [] and catalog.search("qwzx") == []


@pytest.mark.ffmpeg
def test_suggestions_follow_the_file(media, tmp_path):
    tagged = str(tmp_path / "phone.mp4")
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-i", media["h264"], "-c", "copy",
                    "-metadata", "location=+51.5074-000.1278/", tagged], check=True)
    tips = catalog.suggest(ff.probe(tagged))
    assert tips[0][0] == "Metadata Cleaner" and "GPS" in tips[0][1]
    assert "The Shortifier" in [n for n, _r in tips]                      # landscape -> vertical version
    audio = [n for n, _r in catalog.suggest(ff.probe(media["mp3"]))]
    assert "Loudness Normalizer" in audio and "The Shortifier" not in audio
    still = [n for n, _r in catalog.suggest(ff.probe(media["png"]))]
    assert still[0] == "Slideshow Maker"
    assert catalog.describe(ff.probe(media["h264_1080p25"])).startswith("Video · 1920×1080 · 25 fps · 0:05")


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.mark.gui
def test_opens_on_start_by_default(ui):
    app = ui.app
    settings.START_PAGE.set("start")
    assert app.first_page() is ToolId.START
    settings.START_PAGE.set("home")
    assert app.first_page() is ToolId.HOME
    ui.open("Denoise")
    settings.START_PAGE.set("last")
    assert app.first_page() is ToolId.DENOISE
    assert settings.RECENT_TOOLS.get()[0] == "denoise"          # stored by id, not by name
    settings.set("start_page", "start")


@pytest.mark.gui
def test_goals_lead_to_tools(ui):
    page = ui.open("Start")
    page.clear_search()
    page.show_goal(None)
    ui.pump(0.2)
    cards = [w for w in page._results.winfo_children()[-1].winfo_children() if getattr(w, "_goal", None)]
    assert len(cards) == len(catalog.GOALS)
    smaller = next(c for c in cards if c._goal == "smaller")
    smaller.event_generate("<Button-1>")                     # a real click on the card
    ui.pump(0.2)
    rows = [w for w in page._results.winfo_children()[-1].winfo_children() if getattr(w, "_tool", None)]
    assert [r._tool for r in rows] == list(catalog.goal("smaller").tools)
    rows[0].event_generate("<Button-1>")
    ui.pump(0.3)
    assert ui.app.current_page is ToolId.UNIVERSAL_DOWNSIZER


@pytest.mark.gui
def test_search_box(ui):
    page = ui.open("Start")
    page.search("too big")
    ui.pump(0.2)
    rows = [w for w in page._results.winfo_children()[-1].winfo_children() if getattr(w, "_tool", None)]
    assert rows and rows[0]._tool == "Universal Downsizer"
    page._search_enter()
    ui.pump(0.3)
    assert ui.app.current_page is ToolId.UNIVERSAL_DOWNSIZER
    page = ui.open("Start")
    page.clear_search()
    assert page._query() == ""


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_dropped_file_gets_suggestions_and_opens_in_the_tool(ui, media, tmp_path):
    tagged = str(tmp_path / "holiday.mp4")
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-i", media["h264_b"], "-c", "copy",
                    "-metadata", "location=+48.8584+002.2945/", tagged], check=True)
    page = ui.open("Start")
    assert ui.app.open_paths([tagged])                       # what a drag-and-drop does
    ui.pump(0.3)
    assert ui.app.current_page is ToolId.START and page._file[0] == tagged
    rows = [w for grid in page._suggest_frame.winfo_children() for w in grid.winfo_children()
            if getattr(w, "_tool", None)]
    assert rows[0]._tool == "Metadata Cleaner"
    rows[0].event_generate("<Button-1>")
    ui.pump(0.4)
    assert ui.app.current_page is ToolId.METADATA_CLEANER
    assert tagged in ui.app.page(ToolId.METADATA_CLEANER).files      # the file came along
    page.clear_file()
    ui.app.page(ToolId.METADATA_CLEANER)._clear()


@pytest.mark.gui
def test_recent_tools_and_home_link(ui):
    ui.open("Sharpen")
    page = ui.open("Start")
    texts = [w.cget("text") for w in page._recent.winfo_children()]
    assert any("Sharpen" in tx for tx in texts)
    home = ui.open("Home")
    links = [w for w in home.winfo_children()[0].winfo_children()[0].winfo_children()
             if "Start" in str(w.cget("text"))]
    assert links
    links[0].event_generate("<Button-1>")
    ui.pump(0.2)
    assert ui.app.current_page is ToolId.START


@pytest.mark.gui
def test_settings_choice_is_saved(ui):
    page = ui.open("Settings")
    page._start_var.set(page._start_choices["last"])
    page._save()
    assert settings.get("start_page") == "last"
    page._start_var.set(page._start_choices["start"])
    page._save()
    assert settings.get("start_page") == "start"


@pytest.mark.gui
def test_top_bar_has_start_and_home(ui):
    assert "Start" in ui.app.start_button.cget("text") and "Home" in ui.app.home_button.cget("text")
    ui.app.start_button.invoke()
    ui.pump(0.1)
    assert ui.app.current_page is ToolId.START
    assert os.path.basename(__file__)
