"""
core/ops/chain.py - the All-in-One Builder's planner.

A *step* is what one tool would have run on a video: its FFmpeg commands,
captured when the user pressed "Save and return" in the tool's pop-up, with
the file it read written as :data:`IN` and the file it wrote as :data:`OUT`
(or, for a tool whose job is Python code, that code).

:func:`plan` joins neighbouring steps into one FFmpeg command wherever it can
- each step's filters feed the next step's - and only starts another command
where a step can't be joined: it runs several commands or Python code, or it
uses an option this module doesn't understand.  Files handed from one command
to the next are lossless, so however the steps are mixed, the picture is only
compressed once, at the end.

    passes = plan(steps, src, out, final=encode_args, has_audio=True, workdir=tmp)

Everything here is pure except :func:`dry_run` (which runs FFmpeg for a moment
to check that a joined command works; :mod:`core.ops.chain_run` falls back to
one command per step when it doesn't).
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

IN = "⟨in⟩"  # ⟨in⟩: the file a step reads
OUT = "⟨out⟩"  # ⟨out⟩: the file a step writes

#: Containers whose files the builder encodes itself (with its Output settings);
#: a step writing anything else (a GIF, a WebM, sound, a picture) decides its own format.
VIDEO_EXTS = frozenset({".mp4", ".mkv", ".mov", ".m4v"})

#: How passes hand video to each other: lossless H.264 and FLAC in Matroska.
LOSSLESS = ["-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-c:a", "flac"]
#: For pictures with transparency, which H.264 can't keep.
LOSSLESS_ALPHA = ["-c:v", "ffv1", "-c:a", "flac"]
#: The video a step's window shows: quick to make, close enough to the real thing.
PREVIEW = [
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",
    "-crf",
    "14",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-b:a",
    "192k",
]


class Unjoinable(Exception):
    """This step's commands can't be merged with its neighbours."""


# ─────────────────────────────────────────────────────────────────────────────
#  Templates: a tool's commands with its input and output paths as tokens
# ─────────────────────────────────────────────────────────────────────────────


def templated(cmds: Sequence[Sequence[object]], src: str, out: str) -> list[list[str]]:
    """*cmds* with *src* written as IN and *out* as OUT (whole arguments, and
    inside filter strings)."""

    def sub(arg: object) -> str:
        a = str(arg)
        if out and a == out:
            return OUT
        if src and a == src:
            return IN
        if out and out in a:
            a = a.replace(out, OUT)
        if src and src in a:
            a = a.replace(src, IN)
        return a

    return [[sub(a) for a in cmd] for cmd in cmds]


def filled(cmds: Sequence[Sequence[str]], src: str, out: str) -> list[list[str]]:
    """The opposite of :func:`templated`: real paths in place of the tokens."""
    return [[a.replace(IN, src).replace(OUT, out) for a in cmd] for cmd in cmds]


# ─────────────────────────────────────────────────────────────────────────────
#  Filtergraphs
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Filter:
    """One filter in a graph: the labels feeding it, its text, the labels it makes."""

    ins: list[str]
    body: str
    outs: list[str]

    @property
    def name(self) -> str:
        return self.body.split("=", 1)[0].split("@", 1)[0].strip()


Graph = list[list[Filter]]  # chains (";"-separated) of filters (","-separated)


class GraphError(ValueError):
    pass


def _labels(text: str, i: int) -> tuple[list[str], int]:
    found: list[str] = []
    n = len(text)
    while True:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i < n and text[i] == "[":
            j = text.find("]", i)
            if j < 0:
                raise GraphError("unclosed label")
            found.append(text[i + 1 : j])
            i = j + 1
        else:
            return found, i


def _body_end(text: str, i: int) -> int:
    """Where a filter's text ends: at an unquoted, unescaped [ ] , or ;"""
    n = len(text)
    quoted = False
    while i < n:
        c = text[i]
        if quoted:
            quoted = c != "'"
        elif c == "\\":
            i += 1
        elif c == "'":
            quoted = True
        elif c in "[],;":
            break
        i += 1
    if quoted:
        raise GraphError("unclosed quote")
    return i


def parse_graph(text: str) -> Graph:
    """FFmpeg filtergraph text as chains of :class:`Filter` (quotes and
    escapes kept exactly as written)."""
    chains: Graph = []
    chain: list[Filter] = []
    i, n = 0, len(text)
    if not text.strip():
        return []
    while True:
        ins, i = _labels(text, i)
        end = _body_end(text, i)
        body = text[i:end].strip()
        outs, i = _labels(text, end)
        if not body:
            raise GraphError("empty filter")
        chain.append(Filter(ins, body, outs))
        if i >= n:
            chains.append(chain)
            return chains
        if text[i] == ",":
            i += 1
        elif text[i] == ";":
            chains.append(chain)
            chain = []
            i += 1
        else:
            raise GraphError(f"unexpected {text[i]!r}")


def render_graph(graph: Graph) -> str:
    def one(f: Filter) -> str:
        return "".join(f"[{x}]" for x in f.ins) + f.body + "".join(f"[{x}]" for x in f.outs)

    return ";".join(",".join(one(f) for f in chain) for chain in graph)


def _relabel(graph: Graph, rename: Callable[[str], str]) -> Graph:
    return [
        [Filter([rename(x) for x in f.ins], f.body, [rename(x) for x in f.outs]) for f in c]
        for c in graph
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  One step's command as a joinable fragment
# ─────────────────────────────────────────────────────────────────────────────

# Labels inside a fragment:  @v / @a   the step's input picture / sound
#                            @V / @A   what the step makes
#                            @x<k>:<spec>  the step's own extra input k (0-based)
#                            #<name>   the step's own inner labels
_V, _A, _VO, _AO = "@v", "@a", "@V", "@A"

_SKIP_GLOBAL_FLAGS = frozenset({"-y", "-n", "-hide_banner", "-nostdin", "-stats", "-nostats"})
_SKIP_GLOBAL_ARGS = frozenset({"-loglevel", "-v", "-progress", "-stats_period"})
#: Global options a joined command must keep (GPU devices for Vulkan filters).
_KEEP_GLOBAL_ARGS = frozenset({"-init_hw_device", "-filter_hw_device"})
_HW_UPLOAD_ARGS = frozenset({"-vaapi_device"})
_MAIN_INPUT_SEEK = frozenset({"-ss", "-to", "-t"})
_MAIN_INPUT_DROP_ARGS = frozenset(
    {"-hwaccel", "-hwaccel_device", "-analyzeduration", "-probesize", "-thread_queue_size"}
)

#: Output options about encoding: dropped when joined (the builder encodes once),
#: kept as the format of a step that decides its own (a GIF, a WebM...).
_CODEC_ARGS = frozenset(
    {
        "-c",
        "-codec",
        "-c:v",
        "-c:v:0",
        "-vcodec",
        "-codec:v",
        "-c:a",
        "-c:a:0",
        "-acodec",
        "-codec:a",
        "-crf",
        "-preset",
        "-tune",
        "-profile",
        "-profile:v",
        "-profile:a",
        "-level",
        "-level:v",
        "-b:v",
        "-b:v:0",
        "-b:a",
        "-b:a:0",
        "-ab",
        "-maxrate",
        "-bufsize",
        "-minrate",
        "-cq",
        "-rc",
        "-qp",
        "-qp_i",
        "-qp_p",
        "-qp_b",
        "-q:v",
        "-qscale:v",
        "-q:a",
        "-qscale:a",
        "-x264-params",
        "-x264opts",
        "-x265-params",
        "-svtav1-params",
        "-movflags",
        "-tag:v",
        "-tag:a",
        "-g",
        "-bf",
        "-keyint_min",
        "-sc_threshold",
        "-spatial-aq",
        "-temporal-aq",
        "-aq-strength",
        "-global_quality",
        "-quality",
        "-usage",
        "-deadline",
        "-cpu-used",
        "-row-mt",
        "-tile-columns",
        "-frame-parallel",
        "-auto-alt-ref",
        "-lag-in-frames",
        "-look_ahead",
        "-rc-lookahead",
        "-compression_level",
        "-lossless",
        "-vbr",
        "-application",
        "-cutoff",
        "-aac_coder",
        "-strict",
        "-max_muxing_queue_size",
        "-pix_fmt",
        "-vprofile",
        "-vendor",
        "-bits_per_mb",
        "-reserve_rate",
        "-alpha_bits",
        "-threads",
        "-f",
        "-video_track_timescale",
        "-write_tmcd",
        "-loop",
        "-final_delay",
        "-gifflags",
        "-update",
        "-b_ref_mode",
        "-multipass",
        "-aq_mode",
        "-enable-tpl-la",
        "-sample_fmt",
    }
)
_CODEC_FLAGS = frozenset({"-sn", "-dn"})
#: Output options a joined command keeps as they are (the last step's win).
_KEEP_ARGS_PREFIX = ("-metadata", "-disposition")
_KEEP_ARGS = frozenset({"-map_metadata", "-map_chapters", "-fps_mode", "-vsync"})
_KEEP_FLAGS = frozenset({"-shortest"})
_VF = frozenset({"-vf", "-filter:v", "-filter:v:0"})
_AF = frozenset({"-af", "-filter:a", "-filter:a:0"})
_FC = frozenset({"-filter_complex", "-lavfi"})
_LAYOUTS = {"1": "mono", "2": "stereo", "6": "5.1", "8": "7.1"}

_known: dict[str, frozenset[str]] = {}


def audio_filters() -> frozenset[str]:
    """Names of the filters that make sound, as FFmpeg says (learned in the
    background at start-up; until then, guessed from names)."""
    if "audio" in _known:
        return _known["audio"]
    from core import capabilities, ui

    names = capabilities.peek("audio_filters")
    if not names and not (ui.installed() and ui.on_main_thread()):
        names = capabilities.get("audio_filters")  # a worker may wait for it
    if names:
        _known["audio"] = frozenset(names)
        return _known["audio"]
    return frozenset()


def _is_audio(f: Filter) -> bool:
    name = f.name
    return name in audio_filters() or (name.startswith("a") and name not in _VIDEO_A_NAMES)


# video filters whose names start with "a" (when FFmpeg's own list isn't known)
_VIDEO_A_NAMES = frozenset(
    {
        "alphamerge",
        "alphaextract",
        "amplify",
        "atadenoise",
        "avgblur",
        "avgblur_opencl",
        "avgblur_vulkan",
        "addroi",
        "ass",
    }
)


@dataclass
class Fragment:
    """One step as part of a joined command."""

    graph: Graph
    extras: list[list[str]]  # each extra input: its options then "-i", path
    globals_: list[str]  # global options it needs (GPU devices)
    out_opts: list[str]  # kept output options (metadata, -shortest...)
    codec: list[str]  # its encoding options (used when it decides the format)
    video: str | None  # @V (it makes a picture), "pass" (picture unchanged) or None (none)
    audio: str | None
    seek: list[str]  # -ss/-to/-t on its input (kept as input options when first)
    uses_audio: bool = False


@dataclass
class _Opts:
    globals_: list[str] = field(default_factory=list[str])
    main_seek: list[str] = field(default_factory=list[str])
    extras: list[list[str]] = field(default_factory=list[list[str]])
    vf: str | None = None
    af: str | None = None
    fc: str | None = None
    maps: list[str] = field(default_factory=list[str])
    out_opts: list[str] = field(default_factory=list[str])
    codec: list[str] = field(default_factory=list[str])
    post_v: list[str] = field(default_factory=list[str])  # filters for -r/-s/-aspect
    post_a: list[str] = field(default_factory=list[str])  # filters for -ar/-ac
    out_seek: list[str] = field(default_factory=list[str])
    no_v: bool = False
    no_a: bool = False
    vaapi: bool = False


def _value(cmd: list[str], i: int) -> str:
    if i + 1 >= len(cmd):
        raise Unjoinable(f"{cmd[i]} without a value")
    return cmd[i + 1]


def _read_inputs(cmd: list[str], o: _Opts) -> int:
    """Read global and input options up to the last -i; return where output options start."""
    last_input = max((k for k, a in enumerate(cmd) if a == "-i"), default=-1)
    if last_input < 0:
        raise Unjoinable("no input")
    pending: list[str] = []
    seen_main = False
    i = 1
    while i <= last_input + 1:
        a = cmd[i]
        if a == "-i":
            seen_main = _take_input(o, _value(cmd, i), pending, seen_main) or seen_main
            pending = []
            i += 2
            continue
        used = _global(o, a, cmd, i)
        if used:
            i += used
        elif a.startswith("-"):
            # an input option: the ones tools use all take a value
            pending += [a, _value(cmd, i)]
            i += 2
        else:
            raise Unjoinable(f"stray argument {a!r}")
    if not seen_main:
        raise Unjoinable("doesn't read its input")
    if pending:
        raise Unjoinable("options after the last input")
    return last_input + 2


def _take_input(o: _Opts, path: str, pending: list[str], seen_main: bool) -> bool:
    """One -i and its options; True when it is the step's own input."""
    if path != IN:
        if IN in path:
            raise Unjoinable("its input inside another input")
        o.extras.append([*pending, "-i", path])
        return False
    if seen_main:
        raise Unjoinable("reads its input twice")
    if o.extras:
        raise Unjoinable("its input isn't the first one")
    for flag, value in zip(pending[::2], pending[1::2], strict=False):
        if flag in _MAIN_INPUT_SEEK:
            o.main_seek += [flag, value]
        elif flag not in _MAIN_INPUT_DROP_ARGS:
            raise Unjoinable(f"input option {flag}")
    return True


def _global(o: _Opts, a: str, cmd: list[str], i: int) -> int:
    """How many arguments a global option at *i* takes (0: it isn't one)."""
    if a in _SKIP_GLOBAL_FLAGS:
        return 1
    if a in _SKIP_GLOBAL_ARGS:
        return 2
    if a in _KEEP_GLOBAL_ARGS:
        o.globals_ += [a, _value(cmd, i)]
        return 2
    if a in _HW_UPLOAD_ARGS:
        o.vaapi = True
        return 2
    return 0


def _read_outputs(cmd: list[str], start: int, o: _Opts) -> None:
    if cmd[-1] != OUT:
        raise Unjoinable("doesn't write its output last")
    i, end = start, len(cmd) - 1
    while i < end:
        if _flag(o, cmd[i]):
            i += 1
            continue
        if i + 1 >= end:
            raise Unjoinable(f"{cmd[i]} without a value")
        _option(o, cmd[i], cmd[i + 1])
        i += 2


def _flag(o: _Opts, a: str) -> bool:
    """An output option without a value; False if *a* isn't one."""
    if a == "-an":
        o.no_a = True
    elif a == "-vn":
        o.no_v = True
    elif a in _CODEC_FLAGS:
        o.codec.append(a)
    elif a in _KEEP_FLAGS:
        o.out_opts.append(a)
    else:
        return False
    return True


def _channels(n: str) -> str:
    layout = _LAYOUTS.get(n)
    if layout is None:
        raise Unjoinable("unusual channel count")
    return f"aformat=channel_layouts={layout}"


#: Output options that stand for a filter: (picture or sound, the filter).
_AS_FILTER: dict[str, tuple[str, Callable[[str], str]]] = {
    "-r": ("v", lambda v: f"fps={v}"),
    "-s": ("v", lambda v: "scale=" + v.replace("x", ":")),
    "-aspect": ("v", lambda v: "setdar=" + v.replace(":", "/")),
    "-ar": ("a", lambda v: f"aresample={v}"),
    "-ac": ("a", _channels),
}


def _once(o: _Opts, attr: str, value: str) -> None:
    if getattr(o, attr) is not None:
        raise Unjoinable(f"two {attr} options")
    setattr(o, attr, value)


def _option(o: _Opts, a: str, value: str) -> None:
    """An output option with its value."""
    if a in _VF:
        _once(o, "vf", value)
    elif a in _AF:
        _once(o, "af", value)
    elif a in _FC:
        _once(o, "fc", value)
    elif a == "-map":
        o.maps.append(value)
    elif a in _CODEC_ARGS:
        o.codec += [a, value]
    elif a in _KEEP_ARGS or a.startswith(_KEEP_ARGS_PREFIX):
        o.out_opts += [a, value]
    elif a in ("-ss", "-t", "-to"):
        o.out_seek += [a, value]
    elif a in _AS_FILTER:
        kind, make = _AS_FILTER[a]
        (o.post_v if kind == "v" else o.post_a).append(make(value))
    else:
        raise Unjoinable(f"output option {a}")


def _trim(seek: list[str], audio: bool) -> str:
    """-ss/-to/-t as a trim filter (times in seconds or [[h:]m:]s)."""
    opts = dict(zip(seek[::2], seek[1::2], strict=False))
    parts: list[str] = []
    if "-ss" in opts:
        parts.append(f"start={_seconds(opts['-ss'])}")
    if "-to" in opts:
        parts.append(f"end={_seconds(opts['-to'])}")
    elif "-t" in opts:
        parts.append(f"duration={_seconds(opts['-t'])}")
    if not parts:
        return ""
    if audio:
        return "atrim=" + ":".join(parts) + ",asetpts=PTS-STARTPTS"
    return "trim=" + ":".join(parts) + ",setpts=PTS-STARTPTS"


def _seconds(text: str) -> str:
    t = text.strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", t):
        return t
    m = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", t)
    if not m:
        raise Unjoinable(f"time {text!r}")
    h, mi, s = m.groups()
    return f"{int(h or 0) * 3600 + int(mi) * 60 + float(s):g}"


_STREAM_REF = re.compile(r"(\d+)(?::([vas]))?(?::(\d+))?$")


def _ref(label: str, extras: int) -> str | None:
    """A graph label that names an input stream, in fragment terms."""
    m = _STREAM_REF.match(label)
    if not m:
        return None
    idx, kind, sub = int(m.group(1)), m.group(2) or "v", m.group(3)
    if idx == 0:
        if sub not in (None, "0"):
            raise Unjoinable("uses a second stream of its input")
        if kind == "s":
            raise Unjoinable("uses subtitles of its input")
        return _V if kind == "v" else _A
    if idx > extras:
        raise Unjoinable("names an input it doesn't have")
    return f"@x{idx - 1}:" + label.split(":", 1)[1] if ":" in label else f"@x{idx - 1}:v"


def _complex(o: _Opts) -> tuple[Graph, dict[str, str]]:
    """The step's -filter_complex in fragment labels, and its outputs by kind."""
    graph = parse_graph(o.fc or "")
    extras = len(o.extras)
    mapped = {m.strip("[]") for m in o.maps if m.startswith("[")}

    def rename(label: str) -> str:
        ref = _ref(label, extras)
        if ref is not None:
            return ref
        return label if label.startswith(("@", "#")) else "#" + label

    graph = _relabel(graph, rename)
    made: dict[str, str] = {}
    produced = {x for c in graph for f in c for x in f.outs}
    consumed = {x for c in graph for f in c for x in f.ins}
    for chain in graph:
        first, last = chain[0], chain[-1]
        if not first.ins:  # FFmpeg feeds it the first unused input of its kind
            first.ins = [_A if _is_audio(first) else _V]
        if not last.outs:  # unlabeled ends go to the output
            kind = "a" if _is_audio(last) else "v"
            if kind in made:
                raise Unjoinable("two unlabeled outputs of one kind")
            last.outs = [f"#out_{kind}"]
            made[kind] = f"#out_{kind}"
    for label in produced - consumed:
        name = label[1:] if label.startswith("#") else label
        if name not in mapped:
            continue
        maker = next(f for c in graph for f in c if label in f.outs)
        kind = "a" if _is_audio(maker) else "v"
        if kind in made and made[kind] != label:
            raise Unjoinable("makes two pictures or two sounds")
        made[kind] = label
    return graph, made


def _simple(text: str, src: str, out: str) -> Graph:
    """A -vf/-af chain fed by *src*, ending in *out*."""
    graph = parse_graph(text)
    if not graph:
        return []

    def rename(label: str) -> str:
        if label == "in":
            return src
        if label == "out":
            return out
        return "#s_" + label

    graph = _relabel(graph, rename)
    has_in = any(src in f.ins for c in graph for f in c)
    has_out = any(out in f.outs for c in graph for f in c)
    if not has_in:
        if graph[0][0].ins:
            raise Unjoinable("filter chain without an input")
        graph[0][0].ins = [src]
    if not has_out:
        if graph[-1][-1].outs:
            raise Unjoinable("filter chain without an output")
        graph[-1][-1].outs = [out]
    return graph


def _source(maps: list[str], kind: str, extras: int, made: dict[str, str]) -> str | None:
    """Where the step's picture (kind "v") or sound ("a") comes from: a graph
    label, the input (@v/@a), an extra input, or None (it has none)."""
    found: list[str] = []
    for m in maps:
        spec = m.rstrip("?")
        if spec.startswith("-"):
            raise Unjoinable("negative map")
        if spec.startswith("["):
            label = spec.strip("[]")
            label = label if label.startswith(("@", "#")) else "#" + label
            if made.get(kind) == label:
                found.append(label)
            continue
        m2 = _STREAM_REF.match(spec)
        if not m2:
            raise Unjoinable(f"map {m}")
        idx, what = int(m2.group(1)), m2.group(2)
        if what not in (None, kind):
            continue
        if what is None and spec != m2.group(1):
            raise Unjoinable(f"map {m}")
        if m2.group(3) not in (None, "0"):
            raise Unjoinable("maps a second stream")
        if idx == 0:
            found.append(_V if kind == "v" else _A)
        elif idx <= extras:
            found.append(f"@x{idx - 1}:{kind}")
        else:
            raise Unjoinable("maps an input it doesn't have")
    if len(found) > 1:
        raise Unjoinable("maps two streams of one kind")
    return found[0] if found else None


def fragment(cmd: Sequence[str], *, has_audio: bool = True) -> Fragment:
    """*cmd* (a template) as a :class:`Fragment`, or :class:`Unjoinable`.
    Without *has_audio* (the video has no sound by then), what the step does
    to its input's sound is left out, as FFmpeg would."""
    cmd = [str(a) for a in cmd]
    if not cmd or not os.path.basename(cmd[0]).lower().startswith("ffmpeg"):
        raise Unjoinable("not an FFmpeg command")
    o = _Opts()
    _read_outputs(cmd, _read_inputs(cmd, o), o)
    if o.fc is not None and (o.vf is not None or o.af is not None):
        raise Unjoinable("filter graph and filter chain together")
    try:
        graph, made = _complex(o) if o.fc is not None else ([], {})
    except GraphError as exc:
        raise Unjoinable(f"filter graph: {exc}") from exc
    extras = len(o.extras)
    if o.maps:
        v_src = None if o.no_v else _source(o.maps, "v", extras, made)
        a_src = None if o.no_a else _source(o.maps, "a", extras, made)
    else:
        v_src = None if o.no_v else made.get("v", _V)
        a_src = None if o.no_a else made.get("a", _A)
    if not has_audio and a_src == _A:
        a_src, o.af, o.post_a = None, None, []
    try:
        graph += _post(o, v_src, a_src)
    except GraphError as exc:
        raise Unjoinable(f"filter chain: {exc}") from exc
    if o.vaapi:
        _drop_upload(graph)
    if o.out_seek:
        graph += _out_trim(o.out_seek, v_src, a_src, graph)
    video, audio = _ends(graph, v_src, a_src)
    uses_audio = any(_A in f.ins for c in graph for f in c)
    return Fragment(
        graph, o.extras, o.globals_, o.out_opts, o.codec, video, audio, o.main_seek, uses_audio
    )


def _post(o: _Opts, v_src: str | None, a_src: str | None) -> Graph:
    """-vf/-af and the filters standing for -r/-s/-ar/-ac, after the step's source."""
    out: Graph = []
    for text, src, extra, end in ((o.vf, v_src, o.post_v, "#vf"), (o.af, a_src, o.post_a, "#af")):
        chain = ",".join(p for p in (text, *extra) if p)
        if not chain:
            continue
        if src is None:
            raise Unjoinable("filters a stream it doesn't keep")
        if src.startswith("#") and o.fc is not None and text:
            raise Unjoinable("filter chain on a graph's output")
        out += _simple(chain, src, end)
    return out


def _out_trim(seek: list[str], v_src: str | None, a_src: str | None, graph: Graph) -> Graph:
    """Output-side -ss/-t/-to as trims after everything else."""
    ends = {x for c in graph for f in c for x in f.outs}
    out: Graph = []
    for src, audio, end in ((v_src, False, "#vf"), (a_src, True, "#af")):
        if src is None:
            continue
        cur = end if end in ends else src
        if cur == end:  # rename the chain's end so the trim can follow it
            for c in graph:
                for f in c:
                    f.outs = [x + "_pre" if x == end else x for x in f.outs]
            cur = end + "_pre"
        out.append([Filter([cur], _trim(seek, audio), [end])])
    return out


def _ends(graph: Graph, v_src: str | None, a_src: str | None) -> tuple[str | None, str | None]:
    """Name the step's results @V/@A (or "pass": that stream goes through untouched)."""
    ends = {x for c in graph for f in c for x in f.outs}
    result: list[str | None] = []
    for src, end, final in ((v_src, "#vf", _VO), (a_src, "#af", _AO)):
        if src is None:
            result.append(None)
            continue
        label = end if end in ends else src
        if label in (_V, _A):
            result.append("pass")
            continue
        if label.startswith("@x"):
            graph.append([Filter([label], "null" if final == _VO else "anull", [final])])
        else:
            for c in graph:
                for f in c:
                    f.outs = [final if x == label else x for x in f.outs]
        result.append(final)
    return result[0], result[1]


def _drop_upload(graph: Graph) -> None:
    """VAAPI encoding ends a chain with format=nv12,hwupload: not wanted mid-way."""
    for chain in graph:
        if chain[-1].name != "hwupload":
            continue
        outs = chain[-1].outs
        cut = len(chain) - 1
        if cut > 0 and chain[cut - 1].name == "format":
            cut -= 1
        if cut == 0:
            chain[:] = [Filter(chain[0].ins, "null", outs)]
        else:
            del chain[cut:]
            chain[-1].outs = outs


# ─────────────────────────────────────────────────────────────────────────────
#  Joining fragments into one command
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Encode:
    """How a command writes its result: *args* (output options), *pre_input*
    (global options before the inputs) and *suffix* (filters for the end of
    the picture's chain, e.g. VAAPI's hwupload)."""

    args: tuple[str, ...]
    pre_input: tuple[str, ...] = ()
    suffix: str = ""


LOSSLESS_ENCODE = Encode(tuple(LOSSLESS))


def join(
    frags: Sequence[Fragment],
    src: str,
    out: str,
    encode: Encode,
    *,
    has_audio: bool,
    ffmpeg: str = "ffmpeg",
    keep_metadata: bool = True,
) -> list[str]:
    """One command doing every fragment in turn on *src*, written to *out*."""
    if not frags:
        raise ValueError("nothing to join")
    joiner = _Joiner(src, frags[0].seek, has_audio, keep_metadata)
    for k, fr in enumerate(frags):
        joiner.add(k, fr)
    return joiner.command(ffmpeg, encode, out)


class _Joiner:
    """The command being built: its inputs, its graph, and the labels of the
    picture (*v*) and sound (*a*) as the steps so far leave them."""

    def __init__(self, src: str, seek: list[str], has_audio: bool, keep_metadata: bool) -> None:
        self.inputs: list[str] = [*seek, "-i", src]
        self.globals_: list[tuple[str, str]] = []
        self.graph: Graph = []
        self.out_opts: list[str] = ["-map_metadata", "0"] if keep_metadata else []
        self.v: str | None = "0:v:0"
        self.a: str | None = "0:a:0" if has_audio else None
        self.n_inputs = 1

    def add(self, k: int, fr: Fragment) -> None:
        if fr.uses_audio and self.a is None:
            raise Unjoinable("needs sound the video no longer has")
        for pair in zip(fr.globals_[::2], fr.globals_[1::2], strict=False):
            if pair not in self.globals_:
                self.globals_.append(pair)
        base = self.n_inputs
        for extra in fr.extras:
            self.inputs += extra
        self.n_inputs += len(fr.extras)
        local, video, audio = self._seeked(k, fr)
        tag = f"s{k}"
        uses = {x: sum(f.ins.count(x) for c in local for f in c) for x in (_V, _A)}
        names = {
            _V: self._copies(self.v, uses[_V], tag, "split"),
            _A: self._copies(self.a, uses[_A], tag, "asplit"),
        }

        def rename(x: str) -> str:
            if x in (_V, _A):
                return names[x].pop(0)
            if x in (_VO, _AO):
                return f"{tag}{x[1]}"
            if x.startswith("@x"):
                idx, spec = x[2:].split(":", 1)
                return f"{base + int(idx)}:{spec}"
            return f"{tag}_{x.lstrip('#')}"

        self.graph += _relabel(local, rename)
        self.v = _advance(self.v, video, uses[_V], self.graph, "nullsink", rename)
        self.a = _advance(self.a, audio, uses[_A], self.graph, "anullsink", rename)
        self.out_opts += fr.out_opts

    def _seeked(self, k: int, fr: Fragment) -> tuple[Graph, str | None, str | None]:
        """A later step's trim of its input happens in the graph (the first
        step's stays a fast seek on the file)."""
        if k == 0 or not fr.seek:
            return fr.graph, fr.video, fr.audio
        local = [
            [Filter([_V], _trim(fr.seek, False), ["#seek_v"])],
            *_relabel(fr.graph, lambda x: "#seek_v" if x == _V else x),
        ]
        if self.a is not None:
            local = [
                [Filter([_A], _trim(fr.seek, True), ["#seek_a"])],
                *_relabel(local, lambda x: "#seek_a" if x == _A else x),
            ]
        video = "#seek_v" if fr.video == "pass" else fr.video
        audio = "#seek_a" if fr.audio == "pass" and self.a is not None else fr.audio
        return local, video, audio

    def _copies(self, cur: str | None, n: int, tag: str, splitter: str) -> list[str]:
        """Names for *n* reads of *cur*: a filter's result can only be read once,
        so it is split into copies."""
        if cur is None or n == 0:
            return []
        if _is_stream(cur) or n == 1:
            return [cur] * n
        outs = [f"{tag}{splitter[0]}{i}" for i in range(n)]
        self.graph.append([Filter([cur], f"{splitter}={n}", outs)])
        return outs

    def command(self, ffmpeg: str, encode: Encode, out: str) -> list[str]:
        v = self.v
        if encode.suffix and v is not None:
            self.graph.append([Filter([v], encode.suffix, ["vout"])])
            v = "vout"
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            *(x for g in self.globals_ for x in g),
            *encode.pre_input,
            *self.inputs,
        ]
        if self.graph:
            cmd += ["-filter_complex", render_graph(self.graph)]
        for label in (v, self.a):
            if label is not None:
                cmd += ["-map", label if _is_stream(label) else f"[{label}]"]
        return [*cmd, *encode.args, *self.out_opts, out]


def _is_stream(label: str) -> bool:
    return bool(_STREAM_REF.match(label))


def _advance(
    cur: str | None,
    made: str | None,
    used: int,
    graph: Graph,
    sink: str,
    rename: Callable[[str], str],
) -> str | None:
    """The picture (or sound) after a step: what it made, the same one, or none
    (a filter result nobody reads any more is ended with a sink)."""
    if made == "pass":
        return cur
    if made is None:
        if cur is not None and not _is_stream(cur) and used == 0:
            graph.append([Filter([cur], sink, [])])
        return None
    return rename(made)


# ─────────────────────────────────────────────────────────────────────────────
#  The plan
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Step:
    """One step as the planner sees it.

    *cmds* is the tool's commands (a template) or None when it runs Python
    code (*run*); *in_path* is the file that code reads (it is written there
    first) and *out_path* the file it writes; *out_ext* is what kind of file
    the step makes ("" = video, which the builder encodes)."""

    name: str
    cmds: list[list[str]] | None = None
    run: Any = None
    out_ext: str = ".mp4"
    in_path: str = ""
    out_path: str = ""
    alone: bool = False  # never join it (set when a joined command failed)
    own_format: bool = False  # its own encoding is the point (a target size, a GIF...)

    @property
    def decides_format(self) -> bool:
        return self.own_format or self.out_ext.lower() not in VIDEO_EXTS


@dataclass
class Pass:
    """One program run of the plan: FFmpeg *cmd* joining *steps*, a step's
    own *cmds*, or a Python step's *run*; it reads *reads* and writes *writes*."""

    steps: list[int]
    reads: str
    writes: str
    cmds: list[list[str]] = field(default_factory=list[list[str]])
    run: Any = None
    final: bool = False
    note: str = ""  # why this step runs on its own
    own_format: bool = False  # the step's encoding is kept (it decides the format)
    made: str = ""  # where a Python step's job writes, when it ignores the plan's path


def fragments(steps: Sequence[Step], has_audio: bool = True) -> list[Fragment | None]:
    """Each step's fragment, or None when it must run on its own (for a video
    with sound throughout, unless *has_audio* is False)."""
    return [fragment_of(s, has_audio) for s in steps]


def fragment_of(step: Step, has_audio: bool) -> Fragment | None:
    """*step*'s fragment for a video with (or without) sound, or None."""
    if step.alone or step.cmds is None or len(step.cmds) != 1:
        return None
    try:
        fr = fragment(step.cmds[0], has_audio=has_audio)
    except Unjoinable:
        return None
    return None if fr.uses_audio and not has_audio else fr


def why_alone(step: Step) -> str:
    """A short reason (an English key's tail) for a step that runs on its own."""
    if step.cmds is None:
        return "own_job"
    if len(step.cmds) != 1:
        return "several_commands"
    return "own_options"


def plan(
    steps: Sequence[Step],
    src: str,
    out: str,
    *,
    final: Encode,
    has_audio: bool,
    workdir: str,
    alpha: bool = False,
    ffmpeg: str | None = None,
) -> list[Pass]:
    """The passes doing *steps* on *src*, the last writing *out* (with the
    app's FFmpeg unless *ffmpeg* names another)."""
    if not steps:
        return []
    if ffmpeg is None:
        from core import ffmpeg as ff

        ffmpeg = ff.ffmpeg_path()
    steps = [_with_ffmpeg(s, ffmpeg) for s in steps]
    planner = _Planner(steps, src, out, final, workdir, alpha, ffmpeg)
    return planner.passes(has_audio)


@dataclass
class _Planner:
    steps: list[Step]
    src: str
    out: str
    final: Encode
    workdir: str
    alpha: bool
    ffmpeg: str

    def passes(self, has_audio: bool) -> list[Pass]:
        lossless = Encode(tuple(LOSSLESS_ALPHA if self.alpha else LOSSLESS))
        out: list[Pass] = []
        reads, audio, i, n = self.src, has_audio, 0, len(self.steps)
        while i < n:
            step = self.steps[i]
            first = fragment_of(step, audio)
            if first is None:
                last = i == n - 1
                writes = self.out if last else _middle(self.workdir, i, step, self.steps)
                out.append(_alone(step, i, reads, writes, last, self.final, lossless))
                audio = _keeps_audio(step, audio)
            else:
                group, parts, audio_in, audio = self._group(i, first, audio)
                out.append(self._joined(group, parts, reads, audio_in, lossless))
                i = group[-1]
            reads = out[-1].writes
            i += 1
        return out

    def _group(
        self, i: int, first: Fragment, audio: bool
    ) -> tuple[list[int], list[Fragment], bool, bool]:
        """Steps from *i* that join: up to one that can't, or one deciding the format."""
        group, parts, audio_in = [i], [first], audio
        audio = _after(first, audio)
        while i + 1 < len(self.steps) and not self.steps[i].decides_format:
            nxt = fragment_of(self.steps[i + 1], audio)
            if nxt is None:
                break
            i += 1
            group.append(i)
            parts.append(nxt)
            audio = _after(nxt, audio)
        return group, parts, audio_in, audio

    def _joined(
        self, group: list[int], parts: list[Fragment], reads: str, audio_in: bool, lossless: Encode
    ) -> Pass:
        last = group[-1] == len(self.steps) - 1
        end = self.steps[group[-1]]
        writes = self.out if last else _middle(self.workdir, group[-1], end, self.steps)
        if end.decides_format:
            enc = Encode(tuple(parts[-1].codec))
        else:
            enc = self.final if last else lossless
        cmd = join(parts, reads, writes, enc, has_audio=audio_in, ffmpeg=self.ffmpeg)
        return Pass(group, reads, writes, [cmd], final=last)


def _with_ffmpeg(step: Step, ffmpeg: str) -> Step:
    """*step* with its commands run by this FFmpeg (a recipe may come from another computer)."""
    if not step.cmds:
        return step
    cmds = [[ffmpeg if _is_ffmpeg(c[0]) else c[0], *c[1:]] for c in step.cmds if c]
    return dataclasses.replace(step, cmds=cmds)


def _is_ffmpeg(arg: str) -> bool:
    return os.path.splitext(os.path.basename(arg))[0].lower() == "ffmpeg"


def filter_names(cmds: Sequence[Sequence[str]]) -> list[str]:
    """The filters a step's commands use, in order (to describe it)."""
    names: list[str] = []
    for cmd in cmds:
        for k, arg in enumerate(cmd[:-1]):
            if arg in _VF or arg in _AF or arg in _FC:
                try:
                    graph = parse_graph(cmd[k + 1])
                except GraphError:
                    continue
                for f in (f for c in graph for f in c):
                    if f.name not in names:
                        names.append(f.name)
    return names


def _after(fr: Fragment | None, audio: bool) -> bool:
    if fr is None:
        return audio
    return (audio and fr.audio is not None) or (fr.audio is not None and fr.audio != "pass")


def _keeps_audio(step: Step, audio: bool) -> bool:
    if step.cmds and any("-an" in c for c in step.cmds[-1:]):
        return False
    return audio


def _middle(workdir: str, i: int, step: Step, steps: Sequence[Step]) -> str:
    """Where pass output *i* is kept for the next pass: the next Python step's
    own input path when it reads one, else a lossless file (or the step's own
    format, when it decides one)."""
    nxt = steps[i + 1] if i + 1 < len(steps) else None
    inside = nxt is not None and os.path.abspath(nxt.in_path).startswith(
        os.path.abspath(workdir) + os.sep
    )
    if nxt is not None and nxt.cmds is None and inside:
        return nxt.in_path  # a Python step reads the file it was set up with
    ext = step.out_ext if step.decides_format else ".mkv"
    return os.path.join(workdir, f"step{i + 1}{ext}")


def _alone(
    step: Step, i: int, reads: str, writes: str, last: bool, final: Encode, lossless: Encode
) -> Pass:
    note = why_alone(step)
    if step.cmds is None:
        return Pass(
            [i], reads, writes, run=step.run, final=last, note=note, own_format=step.decides_format
        )
    cmds = filled(step.cmds, reads, writes)
    if not step.decides_format:
        cmds[-1] = reencode(cmds[-1], final if last else lossless)
        if not last:
            writes_mkv = os.path.splitext(writes)[0] + ".mkv"
            cmds = [[writes_mkv if a == writes else a for a in c] for c in cmds]
            writes = writes_mkv
    return Pass([i], reads, writes, cmds, final=last, note=note, own_format=step.decides_format)


def reencode(cmd: Sequence[str], encode: Encode) -> list[str]:
    """*cmd* with its encoding options swapped for *encode*'s - unless it only
    copies streams (then nothing is re-encoded and it stays as it is)."""
    cmd = list(cmd)
    opts = dict(itertools.pairwise(cmd[1:-1]))
    copies = opts.get("-c") == "copy" or (
        opts.get("-c:v") == "copy" and opts.get("-c:a") in ("copy", None)
    )
    if copies:
        return cmd
    out = [cmd[0], *encode.pre_input]
    i = 1
    last = max((k for k, a in enumerate(cmd) if a == "-i"), default=0)
    while i < len(cmd) - 1:
        a = cmd[i]
        if (i > last + 1 and a in _CODEC_ARGS) or a in _HW_UPLOAD_ARGS:
            i += 2
            continue
        if a in _VF and i + 1 < len(cmd) - 1:  # the tool's VAAPI upload isn't wanted now
            chain_text = re.sub(r",?(format=(nv12|p010|vaapi)\s*,\s*)?hwupload$", "", cmd[i + 1])
            out += [a, chain_text or "null"]
            i += 2
            continue
        out.append(a)
        i += 1
    if encode.suffix:
        out = _with_suffix(out, encode.suffix)
    return [*out, *encode.args, cmd[-1]]


def _with_suffix(cmd: list[str], suffix: str) -> list[str]:
    for flag in ("-vf", "-filter:v"):
        if flag in cmd:
            k = cmd.index(flag) + 1
            cmd[k] = f"{cmd[k]},{suffix}"
            return cmd
    if "-filter_complex" in cmd:
        return cmd  # a graph's own output: left to the encoder's auto-upload
    return [*cmd, "-vf", suffix]


def describe(passes: Sequence[Pass]) -> tuple[int, int, list[int]]:
    """(programs run, encodes that compress, steps that run on their own)."""
    alone = [p.steps[0] for p in passes if len(p.steps) == 1 and (p.note or p.run is not None)]
    return len(passes), 1 if passes else 0, alone


def dry_run_cmd(cmd: Sequence[str], seconds: float = 0.5) -> list[str]:
    """*cmd* changed to check it works: a moment of it, written nowhere."""
    head = list(cmd[:-1])
    return [*head, "-t", f"{seconds:g}", "-f", "null", "-"]
