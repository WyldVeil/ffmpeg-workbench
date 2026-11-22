"""
core/ops/deinterlace.py - command builders for tabs/visuals/deinterlace.py (also used by core/cli.py).

Pure functions: values in, FFmpeg arguments out - no widgets, no Tk.
"""
from __future__ import annotations


def deinterlace_chain(mode: str, engine: str = "bwdif", order: str = "auto") -> str:
    parts = [] if order == "auto" else [f"setfield={order}"]
    if mode == "ivtc":
        parts += ["fieldmatch=order=" + ("auto" if order == "auto" else order[:3]),
                  "yadif=deint=interlaced", "decimate"]
        return ",".join(parts)
    rate = "send_field" if mode == "double" else "send_frame"
    deint = "interlaced" if mode == "smart" else "all"
    if mode == "smart":
        parts.append("idet")
    if engine == "w3fdif":
        parts.append(f"w3fdif=filter=complex:mode={'field' if mode == 'double' else 'frame'}:deint={deint}")
    elif engine == "estdif":
        parts.append(f"estdif=mode={'field' if mode == 'double' else 'frame'}:deint={deint}")
    else:
        parts.append(f"{engine}=mode={rate}:deint={deint}")
    return ",".join(p for p in parts if p)
