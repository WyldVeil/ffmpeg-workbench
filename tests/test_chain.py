"""The All-in-One Builder's planner (core/ops/chain.py): tools' own commands,
joined into as few FFmpeg runs as possible, still do what each tool did."""

from __future__ import annotations

import os
import subprocess

import pytest

from core import ffmpeg as ff
from core.ops import chain
from core.ops.chain import IN, OUT

FF = "ffmpeg"
H264 = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p"]

# What some tools queue (from the tools themselves), as templates.
CROP = [FF, "-y", "-i", IN, "-map", "0:v:0", "-vf", "crop=640:360:10:20,setsar=1", "-map", "0:a?",
        "-c:a", "copy", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "20", "-b:v", "0", "-pix_fmt",
        "yuv420p", "-movflags", "+faststart", "-map_metadata", "0", OUT]  # fmt: skip
DENOISE = [FF, "-y", "-i", IN, "-map", "0:v:0", "-vf", "hqdn3d=4.00:3.00:6.00:4.50", "-map", "0:a?",
           "-c:a", "copy", "-c:v", "libx264", "-crf", "20", OUT]  # fmt: skip
DYNAMICS = [FF, "-y", "-i", IN, "-af", "acompressor=threshold=0.125893:ratio=3.0", "-map", "0:v?",
            "-c:v", "copy", "-map", "0:a:0", "-c:a", "aac", "-b:a", "256k", OUT]  # fmt: skip
TRIM = [FF, "-y", "-ss", "1.000", "-i", IN, "-t", "3.000", "-map", "0:v:0?", "-map", "0:a?", "-c",
        "copy", "-sn", "-dn", "-map_metadata", "0", "-map_chapters", "-1", OUT]  # fmt: skip
FREEZE = [FF, "-y", "-i", IN, "-filter_complex",
          "[0:v]setpts=PTS-STARTPTS,split[va][vb];[va]trim=end=1.0000,setpts=PTS-STARTPTS[v0];"
          "[vb]trim=start=1.0000,setpts=PTS-STARTPTS,tpad=start_mode=clone:start_duration=2.0000[v1];"
          "[v0][v1]concat=n=2:v=1:a=0[fz];[fz]null[v];[0:a:0]asetpts=PTS-STARTPTS,asplit[xa][xb];"
          "[xa]atrim=end=1.0000,asetpts=PTS-STARTPTS[a0];[xb]atrim=start=1.0000,asetpts=PTS-STARTPTS,"
          "adelay=delays=2000:all=1[a1];[a0][a1]concat=n=2:v=0:a=1[a]",
          "-map", "[v]", "-map", "[a]", "-c:a", "aac", "-c:v", "libx264", OUT]  # fmt: skip
SHORTS = [FF, "-y", "-i", IN, "-filter_complex",
          "[0:v]scale=iw*sar:ih,setsar=1,split[a][b];[a]scale=135:240,gblur=sigma=12[bg];"
          "[b]scale=270:480:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[fg];"
          "[bg]scale=270:480[bg2];[bg2][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]",
          "-map", "[v]", "-map", "0:a?", "-c:a", "copy", "-c:v", "libx264", OUT]  # fmt: skip
SYNC = [FF, "-y", "-i", IN, "-itsoffset", "0.200", "-i", IN, "-map", "0:v?", "-map", "1:a:0",
        "-map", "0:s?", "-c", "copy", "-map_metadata", "0", "-movflags", "+faststart", OUT]  # fmt: skip
MUTE_ALL = [FF, "-y", "-i", IN, "-map", "0:v:0", "-an", "-c:v", "libx264", OUT]


def logo(path: str) -> list[str]:
    return [FF, "-y", "-i", IN, "-loop", "1", "-i", path, "-filter_complex",
            "[1:v]scale=64:-1[lg];[0:v][lg]overlay=10:10:shortest=1[v]", "-map", "[v]",
            "-map", "0:a?", "-c:a", "copy", "-c:v", "libx264", OUT]  # fmt: skip


@pytest.fixture(autouse=True)
def _filters():
    chain._known["audio"] = frozenset(
        {"acompressor", "asetpts", "asplit", "atrim", "adelay", "concat", "volume", "anull"}
    )
    yield
    chain._known.pop("audio", None)


# ── Filtergraph text ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "crop=640:360:10:20,setsar=1",
        "[0:v]split[a][b];[a]crop=10:10[c];[b][c]overlay",
        "drawtext=text='a\\, b [c]; d':x=10,format=yuv420p",
        "subtitles=filename='C\\:/x/y.srt'",
        "eq=brightness=0.1 , hue=s=0",
    ],
)
def test_graph_round_trip(text):
    graph = chain.parse_graph(text)
    again = chain.parse_graph(chain.render_graph(graph))
    assert chain.render_graph(again) == chain.render_graph(graph)
    assert next(f.body for c in graph for f in c)


def test_quoted_brackets_are_not_labels():
    graph = chain.parse_graph("drawtext=text='[x]';[a]null[b]")
    assert graph[0][0].ins == [] and graph[0][0].outs == []
    assert graph[1][0].ins == ["a"] and graph[1][0].outs == ["b"]


def test_templated_round_trip():
    src, out = "/v/a.mp4", "/v/a.mp4_out.mp4"
    cmds = [["ffmpeg", "-i", src, "-vf", f"movie={src}", out]]
    tpl = chain.templated(cmds, src, out)
    assert tpl == [["ffmpeg", "-i", IN, "-vf", f"movie={IN}", OUT]]
    assert chain.filled(tpl, "/n/b.mkv", "/n/c.mp4")[0][2] == "/n/b.mkv"


# ── Fragments ────────────────────────────────────────────────────────────────


def test_simple_chain_fragment():
    fr = chain.fragment(CROP)
    assert fr.video == "@V" and fr.audio == "pass"
    assert "-c:v" in fr.codec and fr.out_opts == ["-map_metadata", "0"]


def test_audio_only_step_passes_the_picture():
    fr = chain.fragment(DYNAMICS)
    assert fr.video == "pass" and fr.audio == "@A" and fr.uses_audio
    assert not chain.fragment(CROP).uses_audio  # passing sound on needs none


def test_reading_the_input_twice_is_not_joined():
    with pytest.raises(chain.Unjoinable):
        chain.fragment(SYNC)


def test_unknown_option_is_not_joined():
    with pytest.raises(chain.Unjoinable):
        chain.fragment([FF, "-i", IN, "-frames:v", "1", OUT])


def test_plan_joins_and_splits():
    steps = [
        chain.Step("crop", [CROP]),
        chain.Step("sync", [SYNC]),
        chain.Step("denoise", [DENOISE]),
        chain.Step("dyn", [DYNAMICS]),
    ]
    passes = chain.plan(
        steps, "/v/in.mp4", "/v/out.mp4", final=chain.Encode(tuple(H264)), has_audio=True,
        workdir="/w",
    )  # fmt: skip
    assert [p.steps for p in passes] == [[0], [1], [2, 3]]
    assert passes[0].writes.endswith(".mkv") and "-qp" in passes[0].cmds[0]
    assert passes[1].cmds[0].count("copy") == 1  # a copy-only step is left as it is
    assert passes[2].writes == "/v/out.mp4" and "libx264" in passes[2].cmds[0]


def test_python_step_reads_its_own_input_path():
    steps = [
        chain.Step("crop", [CROP]),
        chain.Step("speed", None, run=object(), in_path="/w/speed-in.mkv", out_path="/w/speed.mp4"),
    ]
    passes = chain.plan(
        steps, "/v/in.mp4", "/v/out.mp4", final=chain.Encode(tuple(H264)), has_audio=True,
        workdir="/w",
    )  # fmt: skip
    assert passes[0].writes == "/w/speed-in.mkv"
    assert passes[1].run is not None and passes[1].final


def test_format_step_decides_the_encoding():
    gif = [FF, "-y", "-i", IN, "-vf", "fps=10,scale=160:-1", "-loop", "0", "-f", "gif", OUT]
    steps = [chain.Step("crop", [CROP]), chain.Step("gif", [gif], out_ext=".gif")]
    passes = chain.plan(
        steps, "/v/in.mp4", "/v/out.gif", final=chain.Encode(tuple(H264)), has_audio=True,
        workdir="/w",
    )  # fmt: skip
    assert len(passes) == 1
    cmd = passes[0].cmds[0]
    assert "gif" in cmd and "libx264" not in cmd


# ── Real runs ────────────────────────────────────────────────────────────────


def _run(cmd: list[str]) -> None:
    cmd = [ff.ffmpeg_path() if cmd[0] == FF else cmd[0], *cmd[1:]]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, ff.format_cmd(cmd) + "\n" + r.stderr[-3000:]


def _plan(steps, src, out, workdir):
    return chain.plan(
        [chain.Step(n, [c]) if not isinstance(c, chain.Step) else c for n, c in steps],
        src,
        out,
        final=chain.Encode(tuple([*H264, "-c:a", "aac"])),
        has_audio=True,
        workdir=str(workdir),
    )


@pytest.mark.ffmpeg
def test_joined_command_runs(media, outdir):
    out = str(outdir / "joined.mp4")
    passes = _plan(
        [
            ("trim", TRIM),
            ("crop", CROP),
            ("freeze", FREEZE),
            ("dyn", DYNAMICS),
            ("denoise", DENOISE),
        ],
        media["h264"],
        out,
        outdir,
    )
    assert len(passes) == 1
    cmd = passes[0].cmds[0]
    assert cmd.index("-ss") < cmd.index("-i")  # the first step's trim stays a fast seek
    _run(cmd)
    info = ff.probe(out, use_cache=False)
    assert (info.width, info.height) == (640, 360)
    assert info.duration == pytest.approx(3 + 2, abs=0.25)  # 3 s kept, 2 s frozen
    assert info.has_audio


@pytest.mark.ffmpeg
def test_steps_reusing_the_picture_get_a_copy(media, outdir, tmp_path):
    png = str(tmp_path / "logo.png")
    _run([FF, "-y", "-f", "lavfi", "-i", "color=red:s=100x50", "-frames:v", "1", png])
    out = str(outdir / "twice.mp4")
    passes = _plan(
        [("logo", logo(png)), ("shorts", SHORTS), ("logo2", logo(png)), ("trim", TRIM)],
        media["h264"],
        out,
        outdir,
    )
    assert len(passes) == 1
    cmd = passes[0].cmds[0]
    assert cmd.count("-i") == 3  # the video and the logo twice, numbered in turn
    _run(cmd)
    info = ff.probe(out, use_cache=False)
    assert (info.width, info.height) == (270, 480)
    assert info.duration == pytest.approx(3, abs=0.25)


@pytest.mark.ffmpeg
def test_whole_plan_runs_with_a_pass_on_its_own(media, outdir):
    out = str(outdir / "split.mp4")
    passes = _plan(
        [("crop", CROP), ("sync", SYNC), ("mute", MUTE_ALL), ("denoise", DENOISE)],
        media["h264"],
        out,
        outdir,
    )
    assert [p.steps for p in passes] == [[0], [1], [2, 3]]
    for p in passes:
        for cmd in p.cmds:
            _run(cmd)
    info = ff.probe(out, use_cache=False)
    assert (info.width, info.height) == (640, 360) and not info.has_audio
    first = ff.probe(passes[0].writes, use_cache=False)
    assert first.require_video().codec == "h264"
    assert os.path.splitext(passes[0].writes)[1] == ".mkv"


@pytest.mark.ffmpeg
def test_dry_run_checks_a_command(media, outdir):
    passes = _plan(
        [("crop", CROP), ("denoise", DENOISE)], media["h264"], str(outdir / "x.mp4"), outdir
    )
    _run(chain.dry_run_cmd(passes[0].cmds[0]))
    assert not os.path.exists(outdir / "x.mp4")
