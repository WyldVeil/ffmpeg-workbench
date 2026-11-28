"""
core/hdr_keep.py - HDR video stays right whatever tool re-encodes it.

Phones record HDR (HDR10, HLG, Dolby Vision) by default.  A tool that
re-encodes such a video with ordinary 8-bit settings used to write an 8-bit
file still labelled HDR: banded skies and shadows, and wrong colours on many
players and sites.  Every FFmpeg command the app runs passes through
:func:`adapt` (from ``ffmpeg.prepare_ffmpeg_cmd``), which applies one rule:

* the output codec can carry HDR (HEVC, AV1, VP9): encode in 10-bit, so the
  HDR - and its mastering / light-level information - is kept;
* otherwise (H.264, MPEG-4...): convert it to normal SDR video with proper
  tone mapping, labelled BT.709, the HDR information removed.

Commands that already deal with HDR (the HDR to SDR tool, Codec, Downsizer,
Snapshot: tone mapping in their filters, or a 10-bit format already) are
left as they are, as are stream copies, audio-only jobs and anything without
an HDR video input.
"""

from __future__ import annotations

from collections.abc import Callable

#: Encoders whose codec carries HDR, and the 10-bit pixel format each takes.
TEN_BIT: dict[str, str] = {
    "libx265": "yuv420p10le",
    "libsvtav1": "yuv420p10le",
    "libaom-av1": "yuv420p10le",
    "librav1e": "yuv420p10le",
    "libvpx-vp9": "yuv420p10le",
    "hevc_nvenc": "p010le",
    "av1_nvenc": "p010le",
    "hevc_qsv": "p010le",
    "av1_qsv": "p010le",
    "hevc_amf": "p010le",
    "av1_amf": "p010le",
    "hevc_videotoolbox": "p010le",
    "hevc_vaapi": "",  # VAAPI converts in the filter chain (format=p010,hwupload)
    "av1_vaapi": "",
}
#: Encoders that keep whatever comes in (intermediate / archive codecs): left alone.
LEAVE = {
    "prores_ks",
    "prores",
    "dnxhd",
    "ffv1",
    "copy",
    "png",
    "mjpeg",
    "rawvideo",
    "gif",
    "libwebp",
}
#: Filters that show a command already handles HDR itself.
HANDLED = (
    "tonemap",
    "libplacebo",
    "zscale",
    "colorspace=",
    "setparams=color",
    "sidedata=mode=delete",
)
TEN_BIT_FORMATS = ("10le", "10be", "p010", "p016", "12le", "16le")

_SDR_TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def _value(cmd: list[str], *flags: str) -> tuple[int, str] | None:
    """The last occurrence of any of *flags* and its value."""
    for i in range(len(cmd) - 2, -1, -1):
        if cmd[i] in flags:
            return i, cmd[i + 1]
    return None


def _first_video_input(cmd: list[str]) -> str | None:
    """The first file given with -i that isn't a generator or a pipe."""
    for i, arg in enumerate(cmd[:-1]):
        if arg != "-i":
            continue
        path = cmd[i + 1]
        fmt = cmd[i - 1] if i >= 2 and cmd[i - 2] == "-f" else ""
        if path in ("-", "pipe:", "pipe:0") or fmt in ("lavfi", "concat") or path.endswith(".txt"):
            continue
        return path
    return None


def adapt(cmd: list[str], is_hdr: Callable[[str], str | None]) -> list[str]:
    """*cmd* adjusted for an HDR source (see the module notes).  *is_hdr(path)*
    returns the source's transfer ("smpte2084" / "arib-std-b67") or None."""
    enc = _value(cmd, "-c:v", "-vcodec", "-codec:v")
    if enc is None or enc[1] in LEAVE or "-vn" in cmd:
        return cmd
    encoder = enc[1]
    graph = _value(cmd, "-filter_complex", "-lavfi")
    chain = _value(cmd, "-vf", "-filter:v")
    filters = (graph[1] if graph else "") + (chain[1] if chain else "")
    if any(h in filters for h in HANDLED):
        return cmd
    src = _first_video_input(cmd)
    transfer = is_hdr(src) if src else None
    if not transfer:
        return cmd
    out = list(cmd)
    pix = _value(out, "-pix_fmt")
    if encoder in TEN_BIT:
        return _keep(out, encoder, pix, chain, transfer)
    return _to_sdr(out, pix, chain, graph)


def _keep(
    cmd: list[str],
    encoder: str,
    pix: tuple[int, str] | None,
    chain: tuple[int, str] | None,
    transfer: str,
) -> list[str]:
    """Encode in 10-bit, tagged as the source is."""
    if pix and any(f in pix[1] for f in TEN_BIT_FORMATS):
        return cmd  # already 10-bit
    ten = TEN_BIT[encoder]
    if ten:
        if pix:
            cmd[pix[0] + 1] = ten
        else:
            cmd[-1:-1] = ["-pix_fmt", ten]
    if chain and "format=nv12,hwupload" in chain[1]:  # VAAPI
        cmd[chain[0] + 1] = chain[1].replace("format=nv12,hwupload", "format=p010,hwupload")
    if "-color_trc" not in cmd:
        cmd[-1:-1] = [
            "-color_primaries", "bt2020", "-color_trc", transfer, "-colorspace", "bt2020nc",
        ]  # fmt: skip
    return cmd


def _to_sdr(
    cmd: list[str],
    pix: tuple[int, str] | None,
    chain: tuple[int, str] | None,
    graph: tuple[int, str] | None,
) -> list[str]:
    """Tone-map to SDR at the end of the picture's filters."""
    from core.filters import hdr_to_sdr_chain

    out_fmt = "yuv420p"
    if pix and pix[1] in ("yuv422p", "yuv444p"):
        out_fmt = pix[1]
    tone = hdr_to_sdr_chain(out_fmt=out_fmt)
    if graph is not None:
        mapped = next(
            (
                (i, cmd[i + 1])
                for i in range(len(cmd) - 1)
                if cmd[i] == "-map" and cmd[i + 1].startswith("[")
            ),
            None,
        )
        if mapped is None:
            return cmd  # the video comes straight from an input: nothing to hang it on
        i, label = mapped
        sdr = label[:-1] + "_sdr]"
        cmd[graph[0] + 1] = f"{graph[1]};{label}{tone}{sdr}"
        cmd[i + 1] = sdr
    elif chain is not None:
        vf = chain[1]
        for upload in ("format=nv12,hwupload", "format=p010,hwupload"):  # VAAPI uploads last
            if vf.endswith(upload):
                head = vf[: -len(upload)].rstrip(",")
                vf = f"{head},{tone},{upload}" if head else f"{tone},{upload}"
                break
        else:
            vf = f"{vf},{tone}" if vf else tone
        cmd[chain[0] + 1] = vf
    else:
        cmd[-1:-1] = ["-vf", tone]
    for flag in ("-color_primaries", "-color_trc", "-colorspace"):
        found = _value(cmd, flag)
        if found:
            del cmd[found[0] : found[0] + 2]
    cmd[-1:-1] = _SDR_TAGS
    return cmd
