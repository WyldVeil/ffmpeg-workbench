"""The Video Downloader's default format (shareable H.264 + AAC) and video
links pasted on the Start page (they open the downloader and download)."""

from __future__ import annotations

from dataclasses import replace

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


# ── re-encoding after a download (found untested by a mutation pass) ───────


def test_hevc_is_tagged_hvc1_only_inside_mp4_and_mov():
    assert "hvc1" in yto.video_codec_args("libx265", 23, "fast", "MP4")
    assert "hvc1" not in yto.video_codec_args("libx265", 23, "fast", "mkv")
    assert "hvc1" not in yto.video_codec_args("libx264", 23, "fast", "mp4")
    assert "yuv420p" in yto.video_codec_args("libx264", 23, "fast", "mp4")
    assert "yuv420p" not in yto.video_codec_args("prores_ks", 23, "fast", "mov")


def test_the_size_choices_become_scale_filters():
    r = yto.Resolution
    assert yto.scale_filter(r.ORIGINAL, "") is None
    assert yto.scale_filter(r.CUSTOM, " 1280 × 720 ") == "scale=1280:720"
    assert yto.scale_filter(r.CUSTOM, "1280") is None
    assert yto.scale_filter(r.CUSTOM, "1x2x3") is None
    fitted = yto.scale_filter(r.R720, "")
    assert fitted is not None and fitted.startswith("scale=1280:720:")


def test_subtitles_are_burned_only_from_a_file_that_exists(tmp_path):
    srt = tmp_path / "a.srt"
    assert yto.subtitle_filter("") is None
    assert yto.subtitle_filter(str(srt)) is None
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    assert yto.subtitle_filter(str(srt))


def test_audio_settings_follow_the_codec():
    assert yto.audio_args("copy", "192k", "48000") == ["-c:a", "copy"]
    assert yto.audio_args("aac", "192k", "48000") == ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    assert yto.audio_args("pcm_s16le", "192k", "44100") == ["-c:a", "pcm_s16le", "-ar", "44100"]
    assert yto.audio_args("flac", "192k", "48000") == ["-c:a", "flac"]


def test_the_re_encode_command(tmp_path):
    e = yto.Encode(
        vcodec="libx264",
        crf=20,
        preset="fast",
        container="mp4",
        resolution=yto.Resolution.R720,
        custom_size="",
        acodec="aac",
        abitrate="160k",
        sample_rate="48000",
    )
    cmd = yto.encode_cmd("in.mkv", "out.mp4", e)
    assert cmd[-2:] == ["out.mp4", "-y"] and "+faststart" in cmd and "-vf" in cmd
    mkv = yto.encode_cmd("in.mkv", "out.mkv", replace(e, container="mkv"))
    assert "+faststart" not in mkv
    plain = replace(e, resolution=yto.Resolution.ORIGINAL)
    assert "-vf" not in yto.encode_cmd("in.mkv", "out.mp4", plain)


def test_a_retry_through_another_proxy_changes_only_the_last_one():
    cmd = ["yt-dlp", "--proxy", "http://a:1", "-f", "b", "--proxy", "http://b:2", "URL"]
    assert yto.with_proxy(cmd, "socks5://c:3") == [
        "yt-dlp", "--proxy", "http://a:1", "-f", "b", "--proxy", "socks5://c:3", "URL",
    ]  # fmt: skip


def test_an_unknown_site_is_not_taken_for_one_video():
    assert yto.is_one_video("https://example.com/watch?v=abc") is False


def test_extras_are_off_unless_asked(monkeypatch):
    monkeypatch.setattr(yto, "base_argv", lambda *a, **k: ["yt-dlp"])
    plain = yto.audio_argv("yt-dlp", "U", "mp3", "0", playlist=False)
    assert "--embed-thumbnail" not in plain and "--add-metadata" not in plain
    both = yto.audio_argv("yt-dlp", "U", "mp3", "0", playlist=False, thumbnail=True, metadata=True)
    assert "--embed-thumbnail" in both and "--add-metadata" in both
    subs = yto.subtitles_only_argv("yt-dlp", "U", "en", "srt", "out", playlist=False)
    assert "--write-auto-sub" not in subs
    assert "--write-auto-sub" in yto.subtitles_only_argv(
        "yt-dlp", "U", "en", "srt", "out", playlist=False, auto=True
    )


def test_the_tabs_own_options(tmp_path):
    assert yto.common_args(yto.Options()) == []
    assert yto.common_args(yto.Options(cookies=str(tmp_path / "missing.txt"))) == []
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert yto.common_args(yto.Options(cookies=str(jar))) == ["--cookies", str(jar)]
    marked = yto.common_args(yto.Options(sponsorblock=("sponsor", "intro")))
    assert marked == ["--sponsorblock-mark", "sponsor,intro"]
    cut = yto.common_args(yto.Options(sponsorblock=("sponsor",), sponsorblock_mark=False))
    assert cut == ["--sponsorblock-remove", "sponsor"]
    assert "aria2c" in yto.common_args(yto.Options(aria2c=True))
    assert yto.common_args(yto.Options(chapters=True, metadata=True)) == [
        "--embed-chapters",
        "--embed-metadata",
    ]


def test_the_proxy_swap_works_when_the_proxy_comes_last():
    assert yto.with_proxy(["yt-dlp", "--proxy", "a", "--proxy", "b"], "c") == [
        "yt-dlp", "--proxy", "a", "--proxy", "c",
    ]  # fmt: skip
