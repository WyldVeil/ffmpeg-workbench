"""
core/tts.py  -  Text-to-speech engines

* ``edge``   - Microsoft's online neural voices through edge-tts (bundled as
               replaceable source in libs/, LGPLv3).  Needs internet; honours
               the app's HTTP(S) proxy and refuses to run around a SOCKS proxy
               (Tor / Tails users must not be de-anonymised by a voice-over).
* ``system`` - the voices that come with the operating system, fully offline:
               Windows speech (System.Speech via the built-in PowerShell),
               espeak-ng / espeak on Linux, ``say`` on macOS.

``synthesize()`` is blocking - call it from a worker thread or a queue job.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Callable

from core import proc as _proc
from core.errors import ToolError

#: Polled while speaking; True stops it
Cancelled = Callable[[], bool] | None

logger = logging.getLogger("crossfader.tts")

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


class TTSError(ToolError):
    """A voice couldn't speak (missing engine, offline, bad text)."""


def _edge():
    try:
        from core.deps import require

        return require("edge-tts", import_name="edge_tts", auto_install=False)
    except Exception:
        logger.debug("_edge: best-effort step failed", exc_info=True)
        return None


def edge_available() -> bool:
    """Is the online engine installed?  Answered without importing it: edge-tts
    pulls in aiohttp and asyncio (~70 ms, ~22 MB) that offline users never need."""
    from core import deps

    deps.ensure_libs_on_path()
    return importlib.util.find_spec("edge_tts") is not None


def system_engine() -> str | None:
    """Path or name of the offline engine, None when there is none."""
    if IS_WIN:
        return shutil.which("powershell") or os.path.join(
            os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
    if IS_MAC:
        return shutil.which("say")
    return shutil.which("espeak-ng") or shutil.which("espeak")


def system_engine_label() -> str:
    eng = system_engine()
    if not eng:
        return ""
    if IS_WIN:
        return "Windows voice"
    if IS_MAC:
        return "macOS voice"
    return os.path.basename(eng)


def _pct(value: str) -> float:
    """'+10%' / '-5%' / '+3Hz' -> 10.0 / -5.0 / 3.0"""
    try:
        return float(str(value).strip().rstrip("%").replace("Hz", "").replace("hz", ""))
    except ValueError:
        return 0.0


VOICE_SERVICE = "https://speech.platform.bing.com/"


def edge_proxy() -> str | None:
    """The route to the voice service under the app's network settings (incl.
    the computer's own proxy / auto-config); refuses SOCKS rather than going
    around it."""
    from core import network

    try:
        network.check_online()
        url = network.tool_proxy(VOICE_SERVICE)
    except OSError as exc:
        raise TTSError(network.explain_error(exc)) from exc
    if not url:
        return None
    if url.lower().startswith("socks"):
        raise TTSError(
            "The online voices can't go through a SOCKS proxy (such as Tor), and they "
            "won't connect around it either. Use the offline system voice, or an HTTP proxy."
        )
    return url


def synthesize(
    text: str,
    out_path: str,
    *,
    engine: str = "edge",
    voice: str = "en-GB-RyanNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
    subtitles: bool = False,
    cancelled: Cancelled = None,
) -> str | None:
    """Speak *text* into *out_path* (mp3 for edge, wav for system).

    Returns SRT text (sentence cues) when *subtitles* and the engine gives
    timings, otherwise None."""
    text = (text or "").strip()
    if not text:
        raise TTSError("There is no text to speak.")
    if engine == "edge":
        return _edge_synth(text, out_path, voice, rate, pitch, volume, subtitles, cancelled)
    _system_synth(text, out_path, rate, pitch, volume, cancelled)
    return None


def _edge_synth(
    text: str,
    out_path: str,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
    subtitles: bool,
    cancelled: Cancelled,
) -> str | None:
    import asyncio

    edge_tts = _edge()
    if edge_tts is None:
        raise TTSError("The online voice engine (edge-tts) is not available in this copy.")
    proxy = edge_proxy()
    try:  # the app's trust settings (company certificates...)
        import edge_tts.communicate as _comm

        from core import network

        _comm._SSL_CTX = network.ssl_context()  # noqa: SLF001 - edge-tts offers no option for it
    except Exception:
        logger.debug("_edge_synth: best-effort step failed", exc_info=True)

    async def run():
        comm = edge_tts.Communicate(
            text,
            voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
            boundary="SentenceBoundary",
            proxy=proxy,
            connect_timeout=15,
            receive_timeout=90,
        )
        subs = edge_tts.SubMaker()
        got_audio = False
        with open(out_path, "wb") as fh:
            async for chunk in comm.stream():
                if cancelled and cancelled():
                    raise TTSError("Cancelled.")
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                    got_audio = True
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    subs.feed(chunk)
        if not got_audio:
            raise TTSError("The voice service returned no audio.")
        return subs.get_srt() if subtitles else None

    try:
        return asyncio.run(run())
    except TTSError:
        raise
    except Exception as exc:  # aiohttp / service errors -> one readable message
        name = type(exc).__name__
        if "ClientConnector" in name or "Timeout" in name or isinstance(exc, OSError):
            raise TTSError(
                "Could not reach the online voice service - check the internet connection "
                f"or proxy settings, or use the offline system voice.\n({exc})"
            ) from exc
        raise TTSError(f"The online voice service failed: {exc}") from exc


def _run(cmd: list[str], cancelled: Cancelled, timeout: float = 600) -> None:
    proc = _proc.spawn(cmd, stdin=_proc.DEVNULL, stdout=_proc.PIPE, stderr=_proc.PIPE)
    try:
        waited = 0.0
        while True:
            try:
                out, err = proc.communicate(timeout=0.5)
                break
            except _proc.ProcessTimeout:
                waited += 0.5
                if (cancelled and cancelled()) or waited > timeout:
                    proc.kill()
                    proc.communicate()
                    raise TTSError(
                        "Cancelled." if cancelled and cancelled() else "The voice engine timed out."
                    ) from None
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        msg = (err or out or b"").decode("utf-8", "replace").strip().splitlines()
        raise TTSError(
            "The system voice failed: " + (msg[-1] if msg else f"exit code {proc.returncode}")
        )


def _system_synth(
    text: str, out_path: str, rate: str, pitch: str, volume: str, cancelled: Cancelled
) -> None:
    eng = system_engine()
    if not eng:
        raise TTSError(
            "No offline voice was found. On Linux install espeak-ng "
            "(e.g. 'sudo apt install espeak-ng' or 'sudo pacman -S espeak-ng')."
        )
    r, p, v = _pct(rate), _pct(pitch), _pct(volume)
    with tempfile.TemporaryDirectory(prefix="wme_tts_") as tmp:
        txt = os.path.join(tmp, "text.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write(text)
        if IS_WIN:

            def q(s: str) -> str:
                return "'" + s.replace("'", "''") + "'"

            ps = os.path.join(tmp, "speak.ps1")
            with open(ps, "w", encoding="utf-8-sig") as fh:
                fh.write(
                    "Add-Type -AssemblyName System.Speech\n"
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                    f"$s.Rate = {max(-10, min(10, round(r / 10)))}\n"
                    f"$s.Volume = {max(0, min(100, round(100 + v)))}\n"
                    f"$s.SetOutputToWaveFile({q(out_path)})\n"
                    f"$s.Speak([IO.File]::ReadAllText({q(txt)}, [Text.Encoding]::UTF8))\n"
                    "$s.Dispose()\n"
                )
            _run(
                [eng, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps],
                cancelled,
            )
        elif IS_MAC:
            aiff = os.path.join(tmp, "speech.aiff")
            _run([eng, "-r", str(round(180 * (1 + r / 100))), "-o", aiff, "-f", txt], cancelled)
            from core import ffmpeg as ff

            _run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-i", aiff, out_path], cancelled)
        else:
            _run(
                [
                    eng,
                    "-w",
                    out_path,
                    "-s",
                    str(max(80, min(450, round(175 * (1 + r / 100))))),
                    "-p",
                    str(max(0, min(99, round(50 + p * 2)))),
                    "-a",
                    str(max(0, min(200, round(100 + v)))),
                    "-f",
                    txt,
                ],
                cancelled,
            )
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 100:
        raise TTSError("The system voice produced no audio.")
