"""
core/network.py  -  How the app reaches the internet

One place that decides, for every connection the app or its tools make:

* the route - follow this computer's settings (default; see core/sysproxy.py:
  fixed proxies, PAC/WPAD scripts, desktop and environment settings), no
  proxy at all, a proxy of your own (HTTP or SOCKS, with sign-in), or
  offline (nothing goes out);
* strict mode - never connect without the proxy (Tor and similar): anything
  that can't use it refuses instead of quietly going direct;
* trust - the operating system's certificates (company / school security
  products re-sign HTTPS with their own root, which lives there), plus an
  optional extra certificate file;
* mirrors for blocked download sites (Hugging Face models, GitHub releases);
* a wrapper command for VPN split tunnelling and similar tools.

Everything that touches the network uses these helpers: ``urlopen`` for the
app's own downloads, ``build_yt_dlp_args`` / ``subprocess_env`` for yt-dlp
(and the Deno runtime it starts).  Proxy passwords are never put on a
command line.
"""
from __future__ import annotations

import contextlib
import logging
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from core import errors, sysproxy
from core import settings as _settings

logger = logging.getLogger("crossfader.network")

DIRECT = sysproxy.DIRECT

# ── Settings ──────────────────────────────────────────────────────────────────
#: Every network setting and its default (declared, with its type, in core.settings).
DEFAULTS = {s.key: s.default for s in _settings.NETWORK}


def _manual(scheme: str, port: str, strict: bool = False, host: str = "127.0.0.1") -> dict:
    return {"network.mode": "manual", "network.proxy_enabled": True, "network.proxy_scheme": scheme,
            "network.proxy_host": host, "network.proxy_port": port, "network.strict": strict}


#: Common situations, most common first: (id, title, explanation, settings)
SITUATIONS = [
    ("auto", "Automatic - follow this computer's settings",
     "Uses whatever Windows, macOS or your Linux desktop is set to: no proxy, a fixed proxy, or an "
     "automatic configuration script (common in companies and universities).",
     {"network.mode": "auto", "network.proxy_enabled": False, "network.strict": False}),
    ("direct", "No proxy - connect directly",
     "Ignore any proxy settings on this computer.",
     {"network.mode": "direct", "network.proxy_enabled": False, "network.strict": False}),
    ("clash", "Clash / Clash for Windows / Mihomo (127.0.0.1:7890)",
     "Local proxy app common in China and elsewhere.", _manual("http", "7890")),
    ("clash_verge", "Clash Verge Rev (127.0.0.1:7897)", "Clash Verge's mixed port.", _manual("http", "7897")),
    ("v2rayn", "v2rayN / Xray (127.0.0.1:10809)", "v2rayN's local HTTP port.", _manual("http", "10809")),
    ("v2raya", "v2rayA (127.0.0.1:20171)", "v2rayA's local HTTP port.", _manual("http", "20171")),
    ("shadowsocks", "Shadowsocks (SOCKS 127.0.0.1:1080)", "Shadowsocks clients' local SOCKS port.",
     _manual("socks5h", "1080")),
    ("nekoray", "NekoRay / NekoBox / sing-box (127.0.0.1:2080)", "Mixed port of sing-box based apps.",
     _manual("socks5h", "2080")),
    ("hiddify", "Hiddify (127.0.0.1:12334)", "Hiddify's mixed port.", _manual("socks5h", "12334")),
    ("privoxy", "Privoxy (127.0.0.1:8118)", "Local filtering proxy.", _manual("http", "8118")),
    ("tor_browser", "Tor Browser running (127.0.0.1:9150)",
     "Send everything through the Tor Browser's Tor; nothing goes out without it. Some sites block Tor.",
     _manual("socks5h", "9150", strict=True)),
    ("tor", "Tor service (127.0.0.1:9050)",
     "Send everything through a Tor service on this computer; nothing goes out without it.",
     _manual("socks5h", "9050", strict=True)),
    ("offline", "Offline - never use the network",
     "For air-gapped or metered machines: no downloads, no update checks.",
     {"network.mode": "offline"}),
]
PRESETS = {sid: values for sid, _t, _d, values in SITUATIONS}
PRESETS["system"] = PRESETS["auto"]          # legacy ids
PRESETS["local_http"] = _manual("http", "8080")

#: Local proxy apps to look for on this computer: port -> label
LOCAL_PORTS = {7890: "Clash / Mihomo", 7897: "Clash Verge", 7891: "Clash (SOCKS)", 10809: "v2rayN (HTTP)",
               10808: "v2rayN (SOCKS)", 20171: "v2rayA (HTTP)", 20170: "v2rayA (SOCKS)", 1080: "SOCKS proxy",
               2080: "NekoRay / sing-box", 12334: "Hiddify", 8118: "Privoxy", 3128: "Squid / local proxy",
               8080: "Local proxy", 8888: "Local debugging proxy", 1087: "V2rayU (HTTP)", 1086: "V2rayU (SOCKS)",
               6152: "Surge", 9050: "Tor", 9150: "Tor Browser"}


class OfflineError(errors.OfflineError):
    """The app is set to work offline."""


class SocksUnavailable(errors.NetworkError):
    """A SOCKS proxy is configured but SOCKS support is missing."""


# ── Load / save ───────────────────────────────────────────────────────────────

def get() -> dict:
    """Every network.* setting with saved values over the defaults."""
    s = _settings.load_settings()
    out = dict(DEFAULTS)
    for k, default in DEFAULTS.items():
        if k in s:
            v = s[k]
            out[k] = default if (isinstance(default, str) and v == "") else v
    if "network.mode" not in s and s.get("network.proxy_enabled"):
        out["network.mode"] = "manual"                    # settings from before 2.0
    return out


def reset_key(key):
    if key in DEFAULTS:
        _settings.update({key: DEFAULTS[key]})


def reset_all():
    _settings.update(dict(DEFAULTS))


def apply_preset(preset_id):
    p = PRESETS.get(preset_id)
    if not p:
        return {}
    _settings.update(dict(p))
    return p


def mode(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else get()
    m = (cfg.get("network.mode") or "auto").lower()
    if m == "auto" and cfg.get("network.proxy_enabled") and cfg.get("network.proxy_port"):
        m = "manual"
    return m if m in ("auto", "direct", "manual", "offline") else "auto"


def is_offline(cfg: dict | None = None) -> bool:
    return os.environ.get("CROSSFADER_NO_NETWORK") == "1" or mode(cfg) == "offline"


def check_online(cfg: dict | None = None) -> None:
    if is_offline(cfg):
        raise OfflineError("Offline mode is on (Settings → Network), so nothing was downloaded.")


# ── Tails: Tor unless the user opened the direct route for this session ──────
_tails_direct = False


def set_tails_direct(enabled: bool) -> None:
    """Session-only switch set by core/tails.py after the user agreed."""
    global _tails_direct
    _tails_direct = bool(enabled)


def tails_direct() -> bool:
    return _tails_direct


def _effective(cfg: dict) -> dict:
    """Settings after systems with fixed networking are taken into account."""
    if sysproxy.special_system() == "tails":
        cfg = dict(cfg)
        cfg.update(_manual("socks5h", "9050", strict=True))   # Tails only lets Tor out
    return cfg


# ── Proxy decisions ───────────────────────────────────────────────────────────

def proxy_url(cfg: dict | None = None) -> str | None:
    """The manual proxy "scheme://[user:pass@]host:port", or None."""
    cfg = _effective(cfg if cfg is not None else get())
    if mode(cfg) != "manual":
        return None
    scheme = (cfg.get("network.proxy_scheme") or "http").lower().strip()
    host = (cfg.get("network.proxy_host") or "").strip()
    port = str(cfg.get("network.proxy_port") or "").strip()
    if not host or not port:
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"                                 # IPv6 literal
    user = (cfg.get("network.proxy_user") or "").strip()
    pwd = (cfg.get("network.proxy_pass") or "").strip()
    auth = ""
    if user:
        auth = quote(user, safe="") + (":" + quote(pwd, safe="") if pwd else "") + "@"
    return f"{scheme}://{auth}{host}:{port}"


def strict(cfg: dict | None = None) -> bool:
    cfg = _effective(cfg if cfg is not None else get())
    return bool(cfg.get("network.strict")) and mode(cfg) == "manual"


def proxies_for(url: str, cfg: dict | None = None) -> list:
    """Proxies to try for *url*, best first (``direct://`` = no proxy)."""
    cfg = _effective(cfg if cfg is not None else get())
    m = mode(cfg)
    if m == "offline":
        raise OfflineError("Offline mode is on.")
    if m == "direct":
        return [DIRECT]
    if m == "manual":
        pu = proxy_url(cfg)
        if not pu:
            return [DIRECT] if not cfg.get("network.strict") else []
        bypass = [x for x in (cfg.get("network.no_proxy") or "").split(",") if x.strip()]
        if not cfg.get("network.strict") and sysproxy.bypassed(url, bypass):
            return [DIRECT]
        return [pu]
    return sysproxy.proxies_for(url)


def describe(cfg: dict | None = None, url: str = "https://www.youtube.com/") -> str:
    """One line for the UI: what happens to a connection right now."""
    cfg = cfg if cfg is not None else get()
    special = sysproxy.special_system()
    if special == "tails":
        return ("Tails: everything goes through Tor" +
                (" - direct downloads allowed for this session" if _tails_direct else ""))
    if special == "whonix":
        return "Whonix: the gateway sends everything through Tor"
    m = mode(cfg)
    if m == "offline":
        return "Offline - the app won't use the network"
    if m == "direct":
        return "Direct connection (no proxy)"
    try:
        found = proxies_for(url, cfg)
    except OSError as exc:
        return str(exc)
    shown = ", ".join("direct" if p == DIRECT else sysproxy.hide_auth(p) for p in found) or "nothing (blocked)"
    if m == "manual":
        return f"Through {shown}" + (" only (strict)" if strict(cfg) else "")
    return f"Following this computer: {shown}"


# ── Certificates ──────────────────────────────────────────────────────────────

LINUX_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",                  # Debian, Ubuntu, Mint, Arch, Gentoo, Alpine, Tails
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",   # Fedora, RHEL, CentOS, Rocky, Alma
    "/etc/pki/tls/certs/ca-bundle.crt",                    # older Fedora / RHEL
    "/var/lib/ca-certificates/ca-bundle.pem",              # openSUSE (newer)
    "/etc/ssl/ca-bundle.pem",                              # openSUSE / SLES
    "/etc/ssl/cert.pem",                                   # Alpine, BSDs, macOS
    "/etc/pki/tls/cacert.pem",                             # OpenELEC
    "/usr/local/share/certs/ca-root-nss.crt",              # FreeBSD
    "/etc/certs/ca-certificates.crt",                      # Solus
)


def system_ca_file() -> str | None:
    """The distribution's certificate bundle (Linux/BSD), honouring SSL_CERT_FILE."""
    env = os.environ.get("SSL_CERT_FILE")
    if env and os.path.isfile(env):
        return env
    if sysproxy.IS_WIN:
        return None
    for path in LINUX_CA_BUNDLES:
        if os.path.isfile(path) and os.path.getsize(path) > 1000:
            return path
    return None


def _truststore():
    try:
        from core.deps import require
        return require("truststore")
    except Exception:
        logger.debug("_truststore: best-effort step failed", exc_info=True)
        return None


def ssl_context(cfg: dict | None = None) -> ssl.SSLContext:
    """TLS settings for the app's own downloads."""
    cfg = cfg if cfg is not None else get()
    if cfg.get("network.verify_ssl", True) is False:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ctx = None
    if cfg.get("network.system_certs", True):
        ts = _truststore()
        if ts is not None:
            try:
                ctx = ts.SSLContext(ssl.PROTOCOL_TLS_CLIENT)      # the OS's own verifier
            except Exception:
                logger.debug("ssl_context: best-effort step failed", exc_info=True)
                ctx = None
        if ctx is None:
            ctx = ssl.create_default_context()                    # Windows: loads the system store
            ca = system_ca_file()
            if ca:
                with contextlib.suppress(OSError, ssl.SSLError):
                    ctx.load_verify_locations(cafile=ca)
    if ctx is None:
        ctx = ssl.create_default_context()
    extra = (cfg.get("network.extra_ca") or "").strip()
    if extra:
        try:
            ctx.load_verify_locations(cafile=extra)
        except (OSError, ssl.SSLError, NotImplementedError, AttributeError):
            try:                                            # plain OpenSSL context + system bundle + extra
                ctx = ssl.create_default_context()
                ca = system_ca_file()
                if ca:
                    ctx.load_verify_locations(cafile=ca)
                ctx.load_verify_locations(cafile=extra)
            except (OSError, ssl.SSLError) as exc:
                raise OSError(f"The extra certificate file can't be used: {exc}") from exc
    return ctx


def ca_bundle_for_tools(cfg: dict | None = None) -> str | None:
    """A certificate file for command-line tools (yt-dlp, curl...): the system's
    bundle plus the extra certificate, or None to leave them as they are."""
    cfg = cfg if cfg is not None else get()
    if not cfg.get("network.system_certs", True) and not cfg.get("network.extra_ca"):
        return None
    base = system_ca_file() if cfg.get("network.system_certs", True) else None
    extra = (cfg.get("network.extra_ca") or "").strip()
    if not extra or not os.path.isfile(extra):
        return base
    try:
        from core.paths import cache_dir
        merged = os.path.join(cache_dir(), "ca-bundle.pem")
        parts = []
        for path in (base, extra):
            if path and os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    parts.append(fh.read().strip())
        text = "\n".join(parts) + "\n"
        old = None
        if os.path.isfile(merged):
            with open(merged, encoding="utf-8", errors="replace") as fh:
                old = fh.read()
        if old != text:
            with open(merged, "w", encoding="utf-8") as fh:
                fh.write(text)
        return merged
    except OSError:
        return base or extra


# ── The app's own downloads ───────────────────────────────────────────────────

def _socks_handler(pu: str):
    try:
        import socks  # PySocks
        from sockshandler import SocksiPyHandler
    except ImportError:
        try:
            from core.deps import require
            if require("PySocks", import_name="socks") is None:
                return None
            import socks
            from sockshandler import SocksiPyHandler
        except Exception:
            logger.debug("_socks_handler: best-effort step failed", exc_info=True)
            return None
    u = urlsplit(pu)
    kind = socks.SOCKS4 if u.scheme.startswith("socks4") else socks.SOCKS5
    return SocksiPyHandler(kind, u.hostname or "127.0.0.1", u.port or 1080, rdns=u.scheme.endswith(("h", "4a")),
                           username=u.username, password=u.password)


def _opener(proxy: str, ctx: ssl.SSLContext):
    handlers: list = [urllib.request.HTTPSHandler(context=ctx)]
    if proxy == DIRECT:
        handlers.append(urllib.request.ProxyHandler({}))
    elif proxy.startswith("socks"):
        h = _socks_handler(proxy)
        if h is None:
            raise SocksUnavailable("A SOCKS proxy is set, but SOCKS support is missing from this copy - "
                                   "nothing was sent. Use an HTTP proxy, or reinstall the app.")
        handlers += [urllib.request.ProxyHandler({}), h]
    else:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def _is_connect_failure(exc: Exception) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ConnectionError, socket.timeout, TimeoutError, socket.gaierror)) or \
        isinstance(exc, (ConnectionError, socket.timeout, TimeoutError))


def urlopen(url, timeout=None, headers=None, cfg=None, data=None):
    """``urllib.request.urlopen`` that follows the network settings: route
    (incl. PAC fall-backs), strict mode, certificates, User-Agent, offline."""
    cfg = cfg if cfg is not None else get()
    check_online(cfg)
    if timeout is None:
        try:
            timeout = float(cfg.get("network.socket_timeout") or 60)
        except (TypeError, ValueError):
            timeout = 60.0
    req_headers = {}
    ua = (cfg.get("network.user_agent") or "").strip()
    if ua:
        req_headers["User-Agent"] = ua
    if headers:
        req_headers.update(headers)
    if isinstance(url, urllib.request.Request):
        req = url
        for k, v in req_headers.items():
            req.add_header(k, v)
    else:
        req = urllib.request.Request(url, headers=req_headers, data=data)
    full = req.full_url
    if cfg.get("network.allow_http", True) is False and full.lower().startswith("http://"):
        raise ValueError("Plain HTTP downloads are blocked by your network settings.")
    routes = proxies_for(full, cfg)
    if not routes:
        raise OSError("The proxy is not set up, and strict mode doesn't allow connecting without it.")
    ctx = ssl_context(cfg)
    last = None
    for i, route in enumerate(routes):
        # urllib rewrites a Request when it sends it through a proxy, so every
        # attempt gets a fresh copy (otherwise a fall-back would still go to the
        # first proxy).
        attempt = urllib.request.Request(full, data=req.data, headers=dict(req.header_items()),
                                         method=req.get_method())
        try:
            return _opener(route, ctx).open(attempt, timeout=timeout)
        except urllib.error.HTTPError:
            raise                                          # the route worked; the server answered
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            if i + 1 < len(routes) and _is_connect_failure(exc):
                continue                                   # PAC "PROXY a; PROXY b; DIRECT" fall-back
            raise
    raise last if last else OSError("No route")


def mirror_url(url: str, cfg: dict | None = None) -> str:
    """Rewrite a Hugging Face / GitHub download link to the configured mirror."""
    cfg = cfg if cfg is not None else get()
    hf = (cfg.get("network.hf_mirror") or "").strip().rstrip("/")
    if hf and url.startswith("https://huggingface.co/"):
        return hf + url[len("https://huggingface.co"):]
    gh = (cfg.get("network.github_mirror") or "").strip()
    if gh and url.startswith(("https://github.com/", "https://objects.githubusercontent.com/",
                              "https://raw.githubusercontent.com/")):
        return (gh if gh.endswith("/") else gh + "/") + url
    return url


# ── Command-line tools (yt-dlp and the Deno it starts) ────────────────────────

def wrapper_prefix(cfg: dict | None = None) -> list:
    """Command to put in front of network tools (split tunnelling etc.)."""
    cfg = cfg if cfg is not None else get()
    text = (cfg.get("network.wrapper") or "").strip()
    if not text:
        return []
    import shlex
    lex = shlex.shlex(text, posix=True)
    lex.whitespace_split, lex.escape, lex.commenters = True, "", ""   # keep Windows backslashes
    return list(lex)


def tool_proxy(url: str = "https://www.youtube.com/", cfg: dict | None = None) -> str | None:
    """The single proxy a command-line tool should use for *url*: '' = direct,
    None = leave the tool's own defaults."""
    cfg = cfg if cfg is not None else get()
    routes = proxies_for(url, cfg)
    if not routes:
        raise OSError("Strict mode is on but no proxy is set up.")
    first = routes[0]
    return "" if first == DIRECT else first


def build_yt_dlp_args(cfg=None, url: str = "https://www.youtube.com/"):
    """yt-dlp flags for the network settings.  A proxy that needs a password
    is passed through the environment (see subprocess_env), never here."""
    cfg = cfg if cfg is not None else get()
    check_online(cfg)
    args = []
    pu = tool_proxy(url, cfg)
    if pu is not None and not urlsplit(pu or "http://x").username:
        args += ["--proxy", pu]                            # '' forces a direct connection
    rate = (cfg.get("network.bandwidth_limit") or "").strip()
    if rate:
        args += ["--limit-rate", rate]
    so = str(cfg.get("network.socket_timeout") or "").strip()
    if so:
        args += ["--socket-timeout", so]
    ipv = (cfg.get("network.ip_version") or "auto").lower()
    if ipv == "ipv4":
        args += ["--force-ipv4"]
    elif ipv == "ipv6":
        args += ["--force-ipv6"]
    src = (cfg.get("network.source_address") or "").strip()
    if src:
        args += ["--source-address", src]
    ua = (cfg.get("network.user_agent") or "").strip()
    if ua:
        args += ["--user-agent", ua]
    if cfg.get("network.verify_ssl", True) is False:
        args += ["--no-check-certificates"]
    elif cfg.get("network.system_certs", True) and (sysproxy.IS_WIN or ca_bundle_for_tools(cfg)):
        args += ["--compat-options", "no-certifi"]          # use the OS / distribution certificates
    return args


def subprocess_env(extra=None, cfg=None, url: str = "https://www.youtube.com/"):
    """Environment for network tools: proxy (incl. any password), certificates
    for yt-dlp, Deno and OpenSSL-based tools."""
    cfg = cfg if cfg is not None else get()
    from core.procenv import child_env
    env = child_env()
    if extra:
        env.update(extra)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    try:
        pu = tool_proxy(url, cfg) if not is_offline(cfg) else None
    except OSError:
        pu = None
    if pu:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[k] = pu
        if pu.startswith("socks"):
            env["ALL_PROXY"] = env["all_proxy"] = pu
    no_proxy = (cfg.get("network.no_proxy") or "").strip()
    if no_proxy and not strict(cfg):
        env["NO_PROXY"] = env["no_proxy"] = no_proxy
    if cfg.get("network.verify_ssl", True) is not False:
        bundle = ca_bundle_for_tools(cfg)
        if bundle and (not sysproxy.IS_WIN or cfg.get("network.extra_ca")):
            env["SSL_CERT_FILE"] = bundle
        if cfg.get("network.system_certs", True):
            env["DENO_TLS_CA_STORE"] = "system,mozilla"
        if cfg.get("network.extra_ca"):
            env["DENO_CERT"] = cfg["network.extra_ca"]
    return env


# ── Diagnostics ───────────────────────────────────────────────────────────────

def explain_error(exc: BaseException) -> str:
    """A sentence a person can act on."""
    text = str(exc)
    reason = getattr(exc, "reason", None)
    if isinstance(exc, OfflineError):
        return text
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 407:
        return "The proxy wants a username and password - enter them under Settings → Network."
    if isinstance(reason, ssl.SSLCertVerificationError) or isinstance(exc, ssl.SSLCertVerificationError) or \
            "CERTIFICATE_VERIFY_FAILED" in text:
        return ("The connection's security certificate isn't trusted. On company or school networks a security "
                "product often re-signs websites - keep 'Trust this computer's certificates' on, or add your "
                "organisation's certificate file under Settings → Network.")
    if isinstance(reason, ConnectionRefusedError) or isinstance(exc, ConnectionRefusedError):
        return ("Nothing answered at the proxy address - is the proxy app (or Tor) running, and is the port "
                "right?")
    if isinstance(reason, socket.gaierror) or isinstance(exc, socket.gaierror):
        return "The server's name couldn't be looked up - check the internet connection or DNS."
    if isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(exc, (socket.timeout, TimeoutError)):
        return "The connection timed out - the network may be blocking it, or a proxy may be needed."
    if isinstance(exc, SocksUnavailable):
        return text
    return text or type(exc).__name__


def test(cfg=None):
    """(ok, message) - try a real HTTPS request the way the app would, and
    recognise sign-in pages (hotels, airports, campus Wi-Fi)."""
    cfg = cfg if cfg is not None else get()
    if is_offline(cfg):
        return False, "Offline mode is on."
    route = describe(cfg, "https://api.github.com/")
    t0 = time.monotonic()
    try:
        with urlopen("https://api.github.com/zen", timeout=10, cfg=cfg) as r:
            r.read(200)
        return True, f"Connected ({(time.monotonic() - t0) * 1000:.0f} ms). {route}."
    except Exception as exc:
        logger.debug("test: best-effort step failed", exc_info=True)
        message = explain_error(exc)
    if not strict(cfg) and mode(cfg) != "manual":
        try:
            with urlopen("http://detectportal.firefox.com/canonical.html", timeout=6, cfg=cfg) as r:
                body = r.read(600).decode("utf-8", "replace")
            if "success" not in body.lower():
                return False, ("This network wants you to sign in first (hotel, airport or campus Wi-Fi?). "
                               "Open any website in your browser to reach the sign-in page.")
        except Exception:
            logger.debug("test: best-effort step failed", exc_info=True)
    return False, f"{message}  ({route})"


def find_local_proxies(timeout: float = 0.25) -> list:
    """[(port, 'http'|'socks5', label)] for proxy apps listening on this computer.
    Only talks to 127.0.0.1 and sends nothing that would make a proxy go online."""
    found = []
    for port, label in LOCAL_PORTS.items():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
                s.settimeout(timeout)
                s.sendall(b"\x05\x01\x00")                 # SOCKS5 greeting, "no authentication"
                try:
                    reply = s.recv(2)
                except (TimeoutError, OSError):
                    reply = b""
            if reply[:1] == b"\x05":
                found.append((port, "socks5h", label))
                continue
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
                s.settimeout(timeout)
                s.sendall(b"OPTIONS * HTTP/1.0\r\n\r\n")
                try:
                    reply = s.recv(12)
                except (TimeoutError, OSError):
                    reply = b""
            if reply.startswith(b"HTTP/"):
                found.append((port, "http", label))
        except OSError:
            continue
    return found


# ── Tor: a fresh circuit for each retry ───────────────────────────────────────

BLOCK_MARKERS = ("confirm you", "not a bot", "http error 403", "http error 429", "too many requests",
                 "precondition check failed", "unable to download api page", "sign in to confirm")


def tor_retries(cfg: dict | None = None) -> int:
    """How many fresh circuits to try (Settings → Network; 0 = off)."""
    cfg = cfg if cfg is not None else get()
    try:
        return max(0, min(50, int(str(cfg.get("network.tor_retries", "12")).strip() or 0)))
    except ValueError:
        return 12


def tor_like_proxy(cmd: list) -> str | None:
    """The SOCKS proxy in a yt-dlp command when it points at a Tor on this
    computer (where new SOCKS credentials mean a new circuit), else None."""
    proxy = None
    for i, a in enumerate(cmd[:-1]):
        if a == "--proxy":
            proxy = cmd[i + 1]
    if not proxy or not proxy.startswith(("socks5h://", "socks5://")):
        return None
    u = urlsplit(proxy)
    if (u.hostname or "") not in ("127.0.0.1", "localhost", "::1") and not strict():
        return None
    return proxy


def fresh_circuit(proxy: str, attempt: int) -> str:
    """Same Tor, new circuit: Tor isolates streams by SOCKS username/password."""
    import secrets
    u = urlsplit(proxy)
    host = u.hostname or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"{u.scheme}://wme{attempt}-{secrets.token_hex(4)}:{secrets.token_hex(4)}@{host}:{u.port or 9050}"


def looks_blocked(lines) -> bool:
    text = "\n".join(lines).lower()
    return any(m in text for m in BLOCK_MARKERS)
