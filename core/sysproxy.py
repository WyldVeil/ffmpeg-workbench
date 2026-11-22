"""
core/sysproxy.py  -  "What does this computer say to use?"

Finds the proxy the operating system / desktop is configured with, for a
given URL, the same way browsers do:

* Windows  - Internet Settings in the registry: fixed proxy (all protocols or
             per protocol "http=...;https=...;socks=..."), the bypass list
             (incl. <local>), an auto-config (PAC) script and the
             "automatically detect settings" (WPAD) switch.
* macOS    - ``scutil --proxy`` (HTTP/HTTPS/SOCKS, PAC, exceptions).
* Linux/BSD, in order:
    1. the desktop portal's ProxyResolver (GNOME, KDE... incl. PAC/WPAD;
       also what Flatpak apps get),
    2. environment variables (http_proxy, https_proxy, all_proxy, no_proxy,
       any capitalisation) - Xfce, LXQt, NixOS, WSL, corporate login scripts,
    3. libproxy's ``proxy`` command (openSUSE and others),
    4. GNOME settings (also Ubuntu, Fedora, Cinnamon, MATE, Budgie, Deepin),
    5. KDE Plasma 5/6 ``kioslaverc`` (manual, PAC, WPAD and "use the
       environment" modes, both value formats).

PAC scripts are run by the bundled Deno with no permissions at all (no
network, files or environment); the DNS answers a script may ask for are
looked up here and handed in.  Results are cached for a few minutes.

Everything returns proxy URLs in preference order, e.g.
``["http://proxy.corp:3128", "direct://"]``; ``direct://`` means connect
without a proxy.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from core import proc as _proc

logger = logging.getLogger("crossfader.sysproxy")

DIRECT = "direct://"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
_NO_WINDOW = 0x08000000 if IS_WIN else 0


@dataclass
class SystemProxy:
    """The computer's proxy configuration (no per-URL decision yet)."""
    source: str = "none"                 # where it came from, for the UI
    mode: str = "direct"                 # direct | manual | pac | wpad | resolver
    proxies: dict = field(default_factory=dict)   # scheme -> proxy URL ("http", "https", "socks", "all")
    bypass: list = field(default_factory=list)    # no-proxy patterns
    pac_url: str = ""
    note: str = ""

    def describe(self) -> str:
        if self.mode == "direct":
            return "No proxy is set on this computer."
        if self.mode in ("pac", "wpad"):
            what = self.pac_url or "found automatically on the network (WPAD)"
            return f"Automatic configuration script: {what}  ({self.source})"
        if self.mode == "resolver":
            return f"Decided per address by the desktop ({self.source})"
        shown = ", ".join(f"{k}: {hide_auth(v)}" for k, v in self.proxies.items())
        return f"{shown}  ({self.source})"


def hide_auth(url: str) -> str:
    return re.sub(r"//[^/@]+@", "//•••@", url)


def _run(cmd: list, timeout: float = 4.0) -> str | None:
    try:
        r = _proc.run(cmd, timeout=timeout)
    except (OSError, _proc.ProcessError):
        return None
    return r.stdout if r.returncode == 0 else None


# ── Parsing helpers (pure, unit-tested) ───────────────────────────────────────

def normalise_proxy(value: str, default_scheme: str = "http") -> str:
    """'host:port', 'http://host:port', 'http://host port' (KDE) -> scheme://host:port"""
    v = (value or "").strip().strip("'\"")
    if not v:
        return ""
    m = re.match(r"^([a-z0-9+]+)://(.+)$", v, re.I)
    scheme, rest = (m.group(1).lower(), m.group(2)) if m else (default_scheme, v)
    rest = rest.strip().rstrip("/")
    if " " in rest and ":" not in rest.rsplit("@", 1)[-1]:      # KDE "host port"
        host, _sp, port = rest.rpartition(" ")
        rest = f"{host.strip()}:{port.strip()}"
    if scheme == "socks":
        scheme = "socks5h"
    elif scheme == "socks5":
        scheme = "socks5h"                                        # resolve names at the proxy (no DNS leak)
    return f"{scheme}://{rest}"


def parse_windows_proxy_server(value: str) -> dict:
    """ProxyServer: 'host:port' or 'http=h:p;https=h:p;socks=h:p'."""
    out = {}
    value = (value or "").strip()
    if not value:
        return out
    if "=" not in value:
        out["all"] = normalise_proxy(value)
        return out
    for part in value.split(";"):
        if "=" in part:
            key, _eq, addr = part.partition("=")
            key = key.strip().lower()
            if addr.strip():
                out[key] = normalise_proxy(addr, "socks5h" if key == "socks" else "http")
    return out


def parse_scutil(text: str) -> SystemProxy:
    """macOS ``scutil --proxy`` output."""
    d, exceptions, in_list = {}, [], False
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("ExceptionsList") and "<array>" in s:
            in_list = True
            continue
        if in_list:
            if s == "}":
                in_list = False
            elif ":" in s:
                exceptions.append(s.split(":", 1)[1].strip())
            continue
        if ":" in s:
            k, _c, v = s.partition(":")
            d[k.strip()] = v.strip()
    sp = SystemProxy(source="macOS network settings", bypass=exceptions)
    if d.get("ProxyAutoConfigEnable") == "1" and d.get("ProxyAutoConfigURLString"):
        sp.mode, sp.pac_url = "pac", d["ProxyAutoConfigURLString"]
        return sp
    if d.get("ProxyAutoDiscoveryEnable") == "1":
        sp.mode = "wpad"
        return sp
    for key, scheme, out in (("HTTP", "http", "http"), ("HTTPS", "http", "https"), ("SOCKS", "socks5h", "socks")):
        if d.get(f"{key}Enable") == "1" and d.get(f"{key}Proxy"):
            sp.proxies[out] = f"{scheme}://{d[f'{key}Proxy']}:{d.get(f'{key}Port', '8080')}"
    if sp.proxies:
        sp.mode = "manual"
    return sp


def parse_gsettings(text: str) -> SystemProxy:
    """``gsettings list-recursively org.gnome.system.proxy`` output."""
    vals = {}
    for line in (text or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].startswith("org.gnome.system.proxy"):
            vals[f"{parts[0]}.{parts[1]}"] = parts[2].strip()

    def s(key):
        v = vals.get(key, "")
        v = re.sub(r"^(uint32|int32)\s+", "", v)
        return v.strip("'\"")

    sp = SystemProxy(source="desktop proxy settings (GNOME)")
    mode = s("org.gnome.system.proxy.mode")
    hosts = vals.get("org.gnome.system.proxy.ignore-hosts", "")
    sp.bypass = re.findall(r"'([^']*)'", hosts)
    if mode == "auto":
        url = s("org.gnome.system.proxy.autoconfig-url")
        sp.mode, sp.pac_url = ("pac", url) if url else ("wpad", "")
        return sp
    if mode != "manual":
        return sp
    auth = ""
    if s("org.gnome.system.proxy.http.use-authentication") == "true":
        user = s("org.gnome.system.proxy.http.authentication-user")
        pwd = s("org.gnome.system.proxy.http.authentication-password")
        if user:
            from urllib.parse import quote
            auth = quote(user, safe="") + (":" + quote(pwd, safe="") if pwd else "") + "@"
    for key, scheme, out in (("http", "http", "http"), ("https", "http", "https"), ("socks", "socks5h", "socks")):
        host, port = s(f"org.gnome.system.proxy.{key}.host"), s(f"org.gnome.system.proxy.{key}.port")
        if host and port and port != "0":
            sp.proxies[out] = f"{scheme}://{auth if key != 'socks' else ''}{host}:{port}"
    if s("org.gnome.system.proxy.use-same-proxy") == "true" and "http" in sp.proxies and "https" not in sp.proxies:
        sp.proxies["https"] = sp.proxies["http"]
    if sp.proxies:
        sp.mode = "manual"
    return sp


def parse_kioslaverc(text: str, env: Mapping[str, str] | None = None) -> SystemProxy:
    """KDE ``kioslaverc`` [Proxy Settings] (Plasma 5 and 6)."""
    env = os.environ if env is None else env
    assert env is not None
    section, vals = "", {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Proxy Settings" and "=" in line:
            k, _e, v = line.partition("=")
            vals[re.sub(r"\[.*\]$", "", k.strip())] = v.strip()
    sp = SystemProxy(source="desktop proxy settings (KDE)")
    kind = vals.get("ProxyType", "0")
    sp.bypass = [x.strip() for x in vals.get("NoProxyFor", "").split(",") if x.strip()]
    if kind == "2":
        sp.mode, sp.pac_url = "pac", vals.get("Proxy Config Script", "")
        return sp
    if kind == "3":
        sp.mode = "wpad"
        return sp
    if kind not in ("1", "4"):
        return sp
    for key, out in (("httpProxy", "http"), ("httpsProxy", "https"), ("socksProxy", "socks")):
        v = vals.get(key, "")
        if kind == "4":                                   # values are environment variable names
            v = env.get(v, "") or env.get(v.lower(), "")
        if v:
            sp.proxies[out] = normalise_proxy(v, "socks5h" if out == "socks" else "http")
    if kind == "4" and not sp.bypass:
        sp.bypass = [x.strip() for x in env.get(vals.get("NoProxyFor", "NO_PROXY"), "").split(",") if x.strip()]
    if sp.proxies:
        sp.mode = "manual"
    return sp


def from_environment(env: Mapping[str, str] | None = None) -> SystemProxy:
    env = os.environ if env is None else env
    assert env is not None

    def get(name):
        return env.get(name) or env.get(name.upper()) or ""

    sp = SystemProxy(source="environment variables")
    for var, out in (("http_proxy", "http"), ("https_proxy", "https"), ("all_proxy", "all")):
        if get(var):
            sp.proxies[out] = normalise_proxy(get(var))
    sp.bypass = [x.strip() for x in get("no_proxy").split(",") if x.strip()]
    if sp.proxies:
        sp.mode = "manual"
    return sp


def parse_pac_result(result: str) -> list:
    """'PROXY a:1; SOCKS5 b:2; DIRECT' -> ['http://a:1', 'socks5h://b:2', 'direct://']"""
    out = []
    for part in (result or "").split(";"):
        bits = part.strip().split()
        if not bits:
            continue
        kind = bits[0].upper()
        addr = bits[1] if len(bits) > 1 else ""
        if kind == "DIRECT":
            out.append(DIRECT)
        elif kind in ("PROXY", "HTTP") and addr:
            out.append(f"http://{addr}")
        elif kind == "HTTPS" and addr:
            out.append(f"https://{addr}")
        elif kind in ("SOCKS", "SOCKS4") and addr:
            out.append(f"socks4a://{addr}")
        elif kind == "SOCKS5" and addr:
            out.append(f"socks5h://{addr}")
    return out or [DIRECT]


def bypassed(url: str, patterns: list) -> bool:
    """no_proxy / exception-list matching: hosts, .domain, *.wildcards, CIDR,
    host:port, '*' (everything) and Windows' <local> (names without dots)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    for raw in patterns:
        p = raw.strip().lower()
        if not p:
            continue
        if p == "*":
            return True
        if p == "<local>":
            if "." not in host and ip is None:
                return True
            continue
        if re.match(r"^\[?[0-9a-f:.]+\]?(/\d+)?$", p) and ("/" in p or ip is not None):
            try:
                if ip is not None and ip in ipaddress.ip_network(p.strip("[]"), strict=False):
                    return True
            except ValueError:
                pass
            continue
        pport = None
        m = re.match(r"^(.*):(\d+)$", p)
        if m and not p.startswith("["):
            p, pport = m.group(1), int(m.group(2))
        if pport is not None and port is not None and pport != port:
            continue
        p = p.lstrip("*").lstrip(".")
        if not p:
            return True
        if host == p or host.endswith("." + p):
            return True
        if "*" in p and re.fullmatch(p.replace(".", r"\.").replace("*", ".*"), host):
            return True
    return False


def choose(sp: SystemProxy, url: str) -> list:
    """Proxies for *url* from a manual configuration."""
    if bypassed(url, sp.bypass):
        return [DIRECT]
    scheme = urlsplit(url).scheme.lower()
    for key in ((scheme, "all", "socks") if scheme in ("http", "https") else ("all", "socks")):
        if sp.proxies.get(key):
            return [sp.proxies[key]]
    if scheme == "https" and sp.proxies.get("http"):
        return [sp.proxies["http"]]
    return [DIRECT]


# ── Discovery per platform ────────────────────────────────────────────────────

def _windows() -> SystemProxy:
    sp = SystemProxy(source="Windows Internet settings")
    if sys.platform != "win32":                  # (tells the type checker winreg exists below)
        return sp
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
    except OSError:
        return sp

    def val(name, default=None):
        try:
            return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return default

    pac = val("AutoConfigURL", "") or ""
    autodetect = False
    try:
        conn = winreg.OpenKey(key, r"Connections")
        blob = winreg.QueryValueEx(conn, "DefaultConnectionSettings")[0]
        autodetect = bool(blob and len(blob) > 8 and blob[8] & 0x08)
    except OSError:
        pass
    sp.bypass = [x for x in (val("ProxyOverride", "") or "").split(";") if x.strip()]
    if pac:
        sp.mode, sp.pac_url = "pac", pac
        return sp
    if val("ProxyEnable", 0):
        sp.proxies = parse_windows_proxy_server(val("ProxyServer", "") or "")
        if sp.proxies:
            sp.mode = "manual"
            return sp
    if autodetect:
        sp.mode = "wpad"
    return sp


def _mac() -> SystemProxy:
    out = _run(["scutil", "--proxy"])
    return parse_scutil(out) if out else SystemProxy()


def portal_lookup(url: str) -> list | None:
    """Ask the desktop portal (GNOME, KDE, Flatpak...) - None when there is no portal."""
    gdbus = shutil.which("gdbus")
    if not gdbus or not os.environ.get("DBUS_SESSION_BUS_ADDRESS", "x"):
        return None
    out = _run([gdbus, "call", "--session", "--dest", "org.freedesktop.portal.Desktop", "--object-path",
                "/org/freedesktop/portal/desktop", "--method", "org.freedesktop.portal.ProxyResolver.Lookup", url],
               timeout=6)
    if not out:
        return None
    found = re.findall(r"'([^']+)'", out)
    return [DIRECT if p.startswith("direct") else normalise_proxy(p) for p in found] or None


def libproxy_lookup(url: str) -> list | None:
    tool = shutil.which("proxy")
    if not tool:
        return None
    out = _run([tool, url], timeout=8)
    if not out:
        return None
    items = [x.strip() for x in re.split(r"[\s,]+", out) if "://" in x]
    return [DIRECT if p.startswith("direct") else normalise_proxy(p) for p in items] or None


def _linux_static() -> SystemProxy:
    env = from_environment()
    if env.mode != "direct":
        return env
    gs = shutil.which("gsettings")
    if gs:
        out = _run([gs, "list-recursively", "org.gnome.system.proxy"])
        if out:
            sp = parse_gsettings(out)
            if sp.mode != "direct":
                return sp
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "kioslaverc")
    if os.path.isfile(cfg):
        try:
            with open(cfg, encoding="utf-8", errors="replace") as fh:
                sp = parse_kioslaverc(fh.read())
            if sp.mode != "direct":
                return sp
        except OSError:
            pass
    return SystemProxy()


_lock = threading.Lock()
_static_cache: tuple = (0.0, None)
_url_cache: dict = {}
_TTL = 300.0


def system_config(refresh: bool = False) -> SystemProxy:
    """The computer's proxy configuration (cached for 5 minutes)."""
    global _static_cache
    with _lock:
        stamp, cached = _static_cache
        if cached is not None and not refresh and time.monotonic() - stamp < _TTL:
            return cached
    if IS_WIN:
        sp = _windows()
    elif IS_MAC:
        sp = _mac()
    else:
        sp = _linux_static()
    env = from_environment()
    if sp.mode == "direct" and env.mode != "direct":
        sp = env
    with _lock:
        _static_cache = (time.monotonic(), sp)
    return sp


def proxies_for(url: str, *, refresh: bool = False) -> list:
    """Proxy URLs to try for *url*, best first (``direct://`` = no proxy)."""
    host_key = (urlsplit(url).scheme, (urlsplit(url).hostname or "").lower())
    with _lock:
        hit = _url_cache.get(host_key)
        if hit and not refresh and time.monotonic() - hit[0] < _TTL:
            return list(hit[1])
    result = None
    if not IS_WIN and not IS_MAC:
        env = from_environment()
        if env.mode != "direct":
            result = choose(env, url)
        else:
            result = portal_lookup(url)
            if result is not None and result == [DIRECT]:
                result = None                                 # the portal may not see KDE/GNOME files
            if result is None:
                result = libproxy_lookup(url)
                if result == [DIRECT]:
                    result = None
    if result is None:
        sp = system_config(refresh)
        if sp.mode == "manual":
            result = choose(sp, url)
        elif sp.mode in ("pac", "wpad"):
            result = [DIRECT] if bypassed(url, sp.bypass) else pac_proxies(sp, url)
        else:
            result = [DIRECT]
    with _lock:
        _url_cache[host_key] = (time.monotonic(), list(result))
    return result


# ── PAC / WPAD ────────────────────────────────────────────────────────────────

_PAC_PRELUDE = r"""
function isPlainHostName(h){return h.indexOf('.')<0}
function dnsDomainIs(h,d){return h.length>=d.length && h.substring(h.length-d.length).toLowerCase()===d.toLowerCase()}
function localHostOrDomainIs(h,hd){return h===hd || hd.lastIndexOf(h+'.',0)===0}
function dnsResolve(h){return (h in __job.dns) ? __job.dns[h] : null}
function dnsResolveEx(h){return (h in __job.dns) ? __job.dns[h] : ''}
function isResolvable(h){return dnsResolve(h)!==null}
function isResolvableEx(h){return isResolvable(h)}
function myIpAddress(){return __job.myip}
function myIpAddressEx(){return __job.myip}
function dnsDomainLevels(h){return h.split('.').length-1}
function __ip(a){var p=a.split('.');if(p.length!==4)return null;var n=0;for(var i=0;i<4;i++){var x=parseInt(p[i],10);if(isNaN(x)||x<0||x>255)return null;n=n*256+x}return n}
function isInNet(h,pat,mask){var a=/^\d+\.\d+\.\d+\.\d+$/.test(h)?h:dnsResolve(h);if(!a)return false;var x=__ip(a),p=__ip(pat),m=__ip(mask);if(x===null||p===null||m===null)return false;return ((x&m)>>>0)===((p&m)>>>0)}
function isInNetEx(h,prefix){var a=/^\d+\.\d+\.\d+\.\d+$/.test(h)?h:dnsResolve(h);if(!a)return false;var s=prefix.split('/');var bits=parseInt(s[1],10);var m=bits===0?0:(0xFFFFFFFF<<(32-bits))>>>0;return ((__ip(a)&m)>>>0)===((__ip(s[0])&m)>>>0)}
function shExpMatch(s,e){var r=new RegExp('^'+e.replace(/[.+^${}()|[\]\\]/g,'\\$&').replace(/\*/g,'.*').replace(/\?/g,'.')+'$');return r.test(s)}
function sortIpAddressList(l){return l}
function getClientVersion(){return '1.0'}
var __days=['SUN','MON','TUE','WED','THU','FRI','SAT'];
function weekdayRange(a,b,gmt){var d=new Date();var t=(gmt==='GMT'||b==='GMT')?d.getUTCDay():d.getDay();var x=__days.indexOf(a),y=(b&&b!=='GMT')?__days.indexOf(b):x;return x<=y?(t>=x&&t<=y):(t>=x||t<=y)}
function dateRange(){return true}
function timeRange(){var a=Array.prototype.slice.call(arguments);var gmt=a[a.length-1]==='GMT';if(gmt)a.pop();var d=new Date();var h=gmt?d.getUTCHours():d.getHours();if(a.length===1)return h===a[0];if(a.length>=2)return a[0]<=a[1]?(h>=a[0]&&h<a[1]):(h>=a[0]||h<a[1]);return true}
function alert(){}
"""

_PAC_RUNNER = r"""
const __text = new TextDecoder().decode(await new Response(Deno.stdin.readable).arrayBuffer());
const __job = JSON.parse(__text);
const __f = new Function('__job', __PRELUDE__ + '\n' + __job.pac + '\nreturn FindProxyForURL(__job.url, __job.host);');
console.log(String(__f(__job)));
"""

_pac_cache: dict = {}


def _deno() -> str | None:
    try:
        from core.hardware import find_binary
        return find_binary("deno")
    except Exception:
        logger.debug("_deno: best-effort step failed", exc_info=True)
        return shutil.which("deno")


def _my_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))             # TEST-NET: no packet is sent for UDP connect
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _resolve(host: str) -> str | None:
    try:
        return str(socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0])
    except (OSError, IndexError, UnicodeError):
        return None


def run_pac(pac_text: str, url: str, *, timeout: float = 10.0) -> list:
    """Evaluate a PAC script for *url* in a permission-less Deno sandbox."""
    deno = _deno()
    if not deno:
        raise RuntimeError("The JavaScript runtime needed for auto-config scripts is missing.")
    host = urlsplit(url).hostname or ""
    job = {"pac": pac_text, "url": url, "host": host, "myip": _my_ip(), "dns": {}}
    if host:
        ip = _resolve(host)
        if ip:
            job["dns"][host] = ip
    import tempfile
    with tempfile.TemporaryDirectory(prefix="wme_pac_") as tmp:
        script = os.path.join(tmp, "pac.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_PAC_RUNNER.replace("__PRELUDE__", json.dumps(_PAC_PRELUDE)))
        try:
            r = _proc.run([deno, "run", "--quiet", "--no-prompt", "--no-config", "--no-lock", "--no-remote",
                                "--no-npm", script], timeout=timeout, input=json.dumps(job), env={**os.environ, "NO_COLOR": "1", "DENO_NO_UPDATE_CHECK": "1"})
        except _proc.ProcessTimeout:
            raise RuntimeError("The auto-config script took too long.") from None
    if r.returncode != 0:
        raise RuntimeError("The auto-config script failed: " + (r.stderr or "").strip().splitlines()[-1:][0]
                           if (r.stderr or "").strip() else "The auto-config script failed.")
    return parse_pac_result(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "")


def _wpad_candidates() -> list:
    """http://wpad.<each parent of this machine's DNS domain>/wpad.dat, then http://wpad/wpad.dat."""
    domains = []
    fqdn = socket.getfqdn()
    if "." in fqdn:
        domains.append(fqdn.split(".", 1)[1])
    if not IS_WIN and os.path.isfile("/etc/resolv.conf"):
        try:
            with open("/etc/resolv.conf", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith(("search", "domain")):
                        domains += line.split()[1:]
        except OSError:
            pass
    out = []
    for d in dict.fromkeys(domains):
        labels = d.strip(".").split(".")
        for i in range(len(labels) - 1):                   # stop before the top-level domain
            out.append(f"http://wpad.{'.'.join(labels[i:])}/wpad.dat")
    out.append("http://wpad/wpad.dat")
    return list(dict.fromkeys(out))


def _fetch_pac(url: str) -> str:
    """Download a PAC script directly (it normally lives on the local network)."""
    import urllib.request
    if url.startswith("file://"):
        with open(url[7:], encoding="utf-8", errors="replace") as fh:
            return fh.read()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as r:
        return r.read(1_000_000).decode("utf-8", "replace")


def pac_proxies(sp: SystemProxy, url: str) -> list:
    key = sp.pac_url or "wpad"
    text = _pac_cache.get(key)
    if text is None:
        sources = [sp.pac_url] if sp.pac_url else _wpad_candidates()
        for src in sources:
            try:
                text = _fetch_pac(src)
                if "FindProxyForURL" in text:
                    if not sp.pac_url:
                        sp.pac_url = src
                    break
                text = None
            except (OSError, ValueError):
                text = None
        _pac_cache[key] = text or ""
    if not text:
        return [DIRECT]                                   # like browsers: no script found -> direct
    try:
        return run_pac(text, url)
    except RuntimeError:
        return [DIRECT]


# ── Special systems ──────────────────────────────────────────────────────────

def os_release() -> dict:
    out = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    k, _e, v = line.strip().partition("=")
                    if k:
                        out[k] = v.strip().strip('"')
            break
        except OSError:
            continue
    return out


def special_system() -> str:
    """'tails', 'whonix' or '' - systems whose networking is Tor by design."""
    if IS_WIN or IS_MAC:
        return ""
    rel = os_release()
    if rel.get("ID", "").lower() == "tails" or os.path.exists("/etc/amnesia/version"):
        return "tails"
    if os.path.exists("/usr/share/anon-ws-base-files/workstation") or os.path.exists("/etc/whonix_version") or \
            "whonix" in rel.get("ID", "").lower():
        return "whonix"
    return ""
