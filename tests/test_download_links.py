"""The Video Downloader's default format (shareable H.264 + AAC) and video
links pasted on the Start page (they open the downloader and download)."""

from __future__ import annotations

import pytest

from core.ops import youtube as yto
from core.tools import ToolId

# ── links ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,one",
    [
        ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", True),
        ("  https://youtu.be/aqz-KE-bpKQ?t=30  ", True),
        ("youtube.com/watch?v=aqz-KE-bpKQ", True),
        ("https://m.youtube.com/watch?v=aqz-KE-bpKQ&list=PL123", True),
        ("https://music.youtube.com/watch?v=aqz-KE-bpKQ", True),
        ("https://www.youtube.com/shorts/abc123", True),
        ("https://x.com/someone/status/1234567890", True),
        ("https://twitter.com/someone/status/1234567890", True),
        ("https://www.tiktok.com/@someone/video/7123456789", True),
        ("https://vimeo.com/76979871", True),
        ("https://www.twitch.tv/videos/123456", True),
        ("https://www.youtube.com/playlist?list=PL123", False),
        ("https://www.youtube.com/@somechannel", False),
        ("https://x.com/someone", False),
        ("https://www.tiktok.com/@someone", False),
        ("https://www.twitch.tv/somestreamer", False),
    ],
)
def test_video_links_are_recognised_and_told_apart_from_collections(text, one):
    link = yto.site_link(text)
    assert link is not None and link.startswith("https://")
    assert yto.is_one_video(link) is one


@pytest.mark.parametrize(
    "text",
    [
        "too big",
        "youtube",
        "download from youtube",
        "https://youtube.com/",
        "x.com",
        "https://example.com/watch?v=abc",
        "https://notyoutube.com/watch?v=abc",
        "ftp://youtube.com/watch?v=abc",
        "https://youtube.com/watch?v=a b",
    ],
)
def test_searches_and_other_sites_are_not_links(text):
    assert yto.site_link(text) is None


# ── the format ───────────────────────────────────────────────────────────────


def test_the_default_prefers_h264_with_aac_and_falls_back_to_the_best():
    choices = yto.SHARE.split("/")
    assert "vcodec~='^(avc|h264)'" in choices[0] and "acodec~='^(mp4a|aac)'" in choices[0]
    assert choices[-2:] == ["bv*+ba", "b"]  # a site without H.264 still downloads
    assert all("[height<=720]" in c for c in yto.share_format(720).split("/"))


def test_h264_video_only_formats_get_aac_sound():
    assert yto.with_audio("137", "avc1").startswith("137+ba[acodec~='^(mp4a|aac)']/")
    assert yto.with_audio("248", "vp9") == "248+ba/b"


def test_the_command_line_downloads_the_shareable_format(monkeypatch, tmp_path):
    from core import cli, network, ytdlp
    from core import proc as _proc

    seen: list[list[str]] = []

    def fake_run(argv, **_kw):
        seen.append(list(argv))
        return _proc.ProcResult(tuple(argv), 0, "", "", 0.0)

    monkeypatch.setattr(ytdlp, "ytdlp_path", lambda: "/opt/yt-dlp")
    monkeypatch.setattr(ytdlp, "base_argv", lambda *a, **k: ["/opt/yt-dlp"])
    monkeypatch.setattr(network, "subprocess_env", lambda **_k: None)
    monkeypatch.setattr(_proc, "run", fake_run)
    cli.main(["download", "https://youtu.be/aqz-KE-bpKQ", "-o", str(tmp_path), "--json"])
    argv = seen[-1]
    assert argv[argv.index("-f") + 1] == yto.SHARE


# ── the pages ────────────────────────────────────────────────────────────────

_INFO = {
    "title": "Big Buck Bunny",
    "duration": 60,
    "formats": [
        {
            "format_id": "137",
            "vcodec": "avc1.640028",
            "acodec": "none",
            "height": 1080,
            "ext": "mp4",
        },
        {"format_id": "248", "vcodec": "vp9", "acodec": "none", "height": 1080, "ext": "webm"},
        {
            "format_id": "136",
            "vcodec": "avc1.4d401f",
            "acodec": "none",
            "height": 720,
            "ext": "mp4",
        },
    ],
}


@pytest.fixture
def downloader(ui, monkeypatch):
    from core import ytdlp

    monkeypatch.setattr(ytdlp, "ytdlp_path", lambda: "/opt/yt-dlp")
    monkeypatch.setattr(ytdlp, "fetch_info", lambda *_a, **_k: dict(_INFO))
    tab = ui.open("Video Downloader")
    monkeypatch.setattr(tab, "_load_thumbnail", lambda _url: None)
    return tab


@pytest.mark.gui
def test_the_quality_list_starts_with_the_shareable_choice(ui, downloader):
    tab = downloader
    tab._url_var.set("https://youtu.be/aqz-KE-bpKQ")
    tab._on_fetch()
    ui.wait_for(lambda: len(tab._format_options) > 2)
    assert tab._format_cb.current() == 0 and tab._chosen_format() == yto.SHARE
    # a filtered list: the row chosen is the row downloaded (it used to be
    # looked up by position in the whole list)
    tab._filter_var.set("720")
    tab._apply_format_filter()
    assert tab._format_cb.get().startswith("720p")
    assert tab._chosen_format().startswith("136+ba[acodec~=")


@pytest.mark.gui
def test_a_link_pasted_on_start_opens_the_downloader_and_downloads(ui, downloader, monkeypatch):
    tab = downloader
    started: list[str] = []
    monkeypatch.setattr(tab, "_download", lambda: started.append(tab._url_var.get()))
    start = ui.open("Start")
    start.clear_search()
    start._search.focus_force()
    start._search_focus()
    start.clipboard_clear()
    start.clipboard_append("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
    start._search.event_generate("<<Paste>>")  # a real paste (Ctrl+V)
    ui.wait_for(lambda: bool(started))
    assert ui.app.current_page is ToolId.VIDEO_DOWNLOADER
    assert started == ["https://www.youtube.com/watch?v=aqz-KE-bpKQ"]
    assert tab._title_var.get() == "Big Buck Bunny"
    assert start._query() == ""  # the search box is left empty


@pytest.mark.gui
def test_a_playlist_link_is_fetched_but_not_downloaded(ui, downloader, monkeypatch):
    tab = downloader
    started: list[str] = []
    monkeypatch.setattr(tab, "_download", lambda: started.append("x"))
    start = ui.open("Start")
    start.search("https://www.youtube.com/playlist?list=PL123")
    start._search_enter()
    ui.wait_for(lambda: tab._title_var.get() == "Big Buck Bunny")
    ui.pump(0.2)
    assert ui.app.current_page is ToolId.VIDEO_DOWNLOADER and not started


@pytest.mark.gui
def test_the_link_starts_the_download_with_the_shareable_format(
    ui, downloader, monkeypatch, tmp_path
):
    tab = downloader
    cmds: list[list[str]] = []
    monkeypatch.setattr(tab, "_stream_ytdlp", lambda cmd, **_k: cmds.append(list(cmd)))
    monkeypatch.setattr(yto, "base_argv", lambda *a, **k: ["/opt/yt-dlp"])
    tab._url_var.set("https://youtu.be/aqz-KE-bpKQ")
    tab._on_fetch()
    ui.wait_for(lambda: len(tab._format_options) > 2)
    tab._run_direct_copy("https://youtu.be/aqz-KE-bpKQ", str(tmp_path / "x.mp4"), "/opt/yt-dlp", [])
    cmd = cmds[-1]
    assert cmd[cmd.index("-f") + 1] == yto.SHARE
