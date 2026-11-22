"""
core/ops/duck.py - the Music Ducker (tabs/audio/ducker.py): music under a video's
voice, turned down whenever the voice speaks (side-chain compression).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from core import ffmpeg as ff


@dataclass(frozen=True, slots=True, kw_only=True)
class Ducking:
    threshold: float = 0.05  # voice level that starts ducking (linear)
    ratio: float = 8.0
    attack: float = 20.0  # ms
    release: float = 300.0  # ms
    music_volume: float = 0.6
    loop_music: bool = False  # repeat the music to fill the video
    normalise: bool = False  # loudness-normalise the mix

    def clamped(self) -> Ducking:
        """Within sidechaincompress's ranges."""
        return replace(
            self,
            threshold=max(0.000976, min(1.0, self.threshold)),
            ratio=max(1.0, min(20.0, self.ratio)),
            attack=max(0.01, min(2000.0, self.attack)),
            release=max(0.01, min(9000.0, self.release)),
        )


def duck_graph(d: Ducking, duration: float) -> tuple[str, str]:
    """(filter_complex, label of the sound).  Both branches in the same layout and
    rate (amix takes the first input's layout, which turned stereo music mono
    whenever the voice was mono); the voice is split so it is both mixed and the
    side-chain key; the music is bounded to the video; the mix is not halved."""
    st = "aformat=sample_rates=48000:channel_layouts=stereo"
    parts = [
        f"[0:a:0]{st},asplit=2[voice][key]",
        f"[1:a:0]{st},volume={d.music_volume:.3f},apad,atrim=duration={duration:.3f}[music]",
        f"[music][key]sidechaincompress=threshold={d.threshold}:ratio={d.ratio}:attack={d.attack}:release={d.release}[ducked]",
        "[voice][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[mixed]",
    ]
    if d.normalise:
        parts.append("[mixed]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aout]")
        return ";".join(parts), "[aout]"
    return ";".join(parts), "[mixed]"


def duck_cmd(src: str, music: str, out: str, duration: float, d: Ducking) -> list[str]:
    """*src* (picture copied) with *music* ducked under its voice."""
    graph, sound = duck_graph(d, duration)
    loop = ["-stream_loop", "-1"] if d.loop_music else []
    cmd = [ff.ffmpeg_path(), "-y", "-i", src, *loop, "-i", music, "-filter_complex", graph]
    cmd += ["-map", "0:v?", "-map", sound, "-c:v", "copy", "-c:a", "aac", "-b:a", "256k"]
    return [*cmd, "-t", f"{duration:.3f}", "-movflags", "+faststart", out]
