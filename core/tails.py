"""
core/tails.py  -  Tails: an optional direct (non-Tor) route for downloads

Tails sends everything through Tor and blocks any other connection.  Its
Unsafe Browser is the deliberate exception: when it is enabled at the
Welcome Screen, Tails creates a network namespace called "clearnet" with
direct internet access, and its launcher runs the browser inside it - as
root: ``ip netns exec clearnet sudo -u amnesia -- bwrap ...`` with a DNS
resolver that works outside Tor (/etc/resolv-over-clearnet.conf) bound over
/etc/resolv.conf.  See tails.net/contribute/design/Unsafe_Browser/ and
tailslib/netnsdrop.py in the Tails source.

This module does the same for yt-dlp only (fetching video information and
downloading), when the person asks for it:

* it must be enabled at the Welcome Screen for this session: an
  administration password and the Unsafe Browser;
* the password is passed to sudo on its standard input - never on a command
  line, never written anywhere - and kept in memory only if the person asks;
* it lasts for this session of the app only and is never saved;
* everything else the app does stays on Tor.

Untested on real Tails by the developers so far - built to mirror Tails' own
launcher; reports exactly what is missing when it can't be used.
"""
from __future__ import annotations

import os
import shutil

from core import proc as _proc
from core import sysproxy

NETNS = "clearnet"
NETNS_PATH = f"/run/netns/{NETNS}"
RESOLV = "/etc/resolv-over-clearnet.conf"
USER = "amnesia"

_remembered: bytearray | None = None


def is_tails() -> bool:
    return sysproxy.special_system() == "tails"


def _sudo(args: list, password: str | None = None, timeout: float = 20) -> _proc.ProcResult[str]:
    cmd = ["sudo", "-S", "-p", "", *args] if password is not None else ["sudo", "-n", *args]
    return _proc.run(cmd, timeout=timeout, input=(password + "\n") if password is not None else None)


def sudo_state() -> str:
    """'ok' (already authorised), 'password' (allowed with the password) or 'denied'."""
    if not shutil.which("sudo"):
        return "denied"
    try:
        r = _sudo(["-v"])
    except (OSError, _proc.ProcessError):
        return "denied"
    if r.returncode == 0:
        return "ok"
    err = (r.stderr or "").lower()
    if "password is required" in err or "a password is required" in err:
        return "password"
    return "denied"


def problems() -> list:
    """What stops the direct route from working, as sentences ([] = ready)."""
    if not is_tails():
        return ["This is only for Tails."]
    out = []
    if not os.path.exists(NETNS_PATH) or not os.path.exists(RESOLV):
        out.append("The Unsafe Browser is not enabled. Restart Tails and, in the Welcome Screen, open "
                   "Additional Settings → Unsafe Browser → Enable.")
    if sudo_state() == "denied":
        out.append("No administration password is set. Restart Tails and, in the Welcome Screen, set one under "
                   "Additional Settings → Administration Password.")
    for tool in ("ip", "bwrap"):
        if not shutil.which(tool) and not os.path.exists(f"/usr/bin/{tool}") and not os.path.exists(f"/sbin/{tool}"):
            out.append(f"The '{tool}' program is missing.")
    return out


def enable(password: str, remember: bool = False) -> tuple:
    """(ok, message).  Checks the password with sudo and opens the route."""
    global _remembered
    issues = problems()
    if issues:
        return False, "\n".join(issues)
    try:
        r = _sudo(["-v"], password=password)
    except (OSError, _proc.ProcessError) as exc:
        return False, f"sudo could not run: {exc}"
    if r.returncode != 0:
        return False, "That administration password was not accepted."
    _remembered = bytearray(password.encode("utf-8")) if remember else None
    from core import network
    network.set_tails_direct(True)
    return True, "Direct downloads are on for this session. Everything else still goes through Tor."


def disable() -> None:
    global _remembered
    from core import network
    network.set_tails_direct(False)
    if _remembered is not None:
        for i in range(len(_remembered)):
            _remembered[i] = 0
    _remembered = None
    try:
        _proc.run(["sudo", "-K"], timeout=10, text=False)     # forget sudo's cached login
    except (OSError, _proc.ProcessError):
        pass


def direct_enabled() -> bool:
    from core import network
    return network.tails_direct() and is_tails()


def _refresh() -> None:
    """Renew sudo's cached login with the remembered password, if any."""
    if _remembered is not None and sudo_state() != "ok":
        try:
            _sudo(["-v"], password=_remembered.decode("utf-8"))
        except (OSError, _proc.ProcessError):
            pass


def wrap(argv: list) -> list:
    """Run *argv* in Tails' clearnet namespace as the normal user - the same
    steps as Tails' Unsafe Browser launcher."""
    _refresh()
    return ["sudo", "-n", "ip", "netns", "exec", NETNS, "sudo", "-u", USER, "-H", "--",
            "bwrap", "--bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--bind", RESOLV, "/etc/resolv.conf",
            "--", *argv]
