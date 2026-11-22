"""System proxy discovery and network policy."""

import email.message
import http.server
import socket
import socketserver
import threading

import pytest

from core import network as net
from core import sysproxy as sp


def test_normalise_proxy_formats():
    assert sp.normalise_proxy("proxy.corp:8080") == "http://proxy.corp:8080"
    assert sp.normalise_proxy("http://proxy.corp 3128") == "http://proxy.corp:3128"        # KDE style
    assert sp.normalise_proxy("'http://proxy.corp:3128/'") == "http://proxy.corp:3128"
    assert sp.normalise_proxy("socks://127.0.0.1:1080") == "socks5h://127.0.0.1:1080"
    assert sp.normalise_proxy("socks5://127.0.0.1 9050") == "socks5h://127.0.0.1:9050"
    assert sp.normalise_proxy("http://user:p%40ss@p:80") == "http://user:p%40ss@p:80"
    assert sp.normalise_proxy("") == ""


def test_windows_proxy_server():
    assert sp.parse_windows_proxy_server("127.0.0.1:7890") == {"all": "http://127.0.0.1:7890"}
    got = sp.parse_windows_proxy_server("http=p:80;https=p:443;socks=s:1080")
    assert got == {"http": "http://p:80", "https": "http://p:443", "socks": "socks5h://s:1080"}


def test_scutil_manual_and_pac():
    manual = """<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
    1 : 169.254/16
  }
  HTTPEnable : 1
  HTTPPort : 3128
  HTTPProxy : proxy.corp
  HTTPSEnable : 1
  HTTPSPort : 3128
  HTTPSProxy : proxy.corp
  ProxyAutoConfigEnable : 0
}"""
    cfg = sp.parse_scutil(manual)
    assert cfg.mode == "manual" and cfg.proxies["https"] == "http://proxy.corp:3128"
    assert cfg.bypass == ["*.local", "169.254/16"]
    pac = sp.parse_scutil("<dictionary> {\n  ProxyAutoConfigEnable : 1\n"
                          "  ProxyAutoConfigURLString : http://wpad.corp/proxy.pac\n}")
    assert pac.mode == "pac" and pac.pac_url == "http://wpad.corp/proxy.pac"


GSETTINGS_MANUAL = """org.gnome.system.proxy autoconfig-url ''
org.gnome.system.proxy ignore-hosts ['localhost', '127.0.0.0/8', '::1', '*.corp']
org.gnome.system.proxy mode 'manual'
org.gnome.system.proxy use-same-proxy true
org.gnome.system.proxy.http authentication-password 's3cret'
org.gnome.system.proxy.http authentication-user 'alice'
org.gnome.system.proxy.http enabled false
org.gnome.system.proxy.http host 'proxy.corp'
org.gnome.system.proxy.http port 8080
org.gnome.system.proxy.http use-authentication true
org.gnome.system.proxy.https host ''
org.gnome.system.proxy.https port 0
org.gnome.system.proxy.socks host '127.0.0.1'
org.gnome.system.proxy.socks port 1080
"""


def test_gsettings():
    cfg = sp.parse_gsettings(GSETTINGS_MANUAL)
    assert cfg.mode == "manual"
    assert cfg.proxies["http"] == "http://alice:s3cret@proxy.corp:8080"
    assert cfg.proxies["https"] == cfg.proxies["http"]                  # use-same-proxy
    assert cfg.proxies["socks"] == "socks5h://127.0.0.1:1080"
    assert "*.corp" in cfg.bypass
    auto = sp.parse_gsettings("org.gnome.system.proxy mode 'auto'\n"
                              "org.gnome.system.proxy autoconfig-url 'http://pac.corp/x.pac'\n")
    assert (auto.mode, auto.pac_url) == ("pac", "http://pac.corp/x.pac")
    assert sp.parse_gsettings("org.gnome.system.proxy mode 'auto'\n").mode == "wpad"
    assert sp.parse_gsettings("org.gnome.system.proxy mode 'none'\n").mode == "direct"


@pytest.mark.parametrize("http_value", ["http://proxy.corp 8080", "http://proxy.corp:8080"])
def test_kioslaverc_manual_both_formats(http_value):
    text = f"""[Proxy Settings]
NoProxyFor=localhost,.corp
ProxyType=1
httpProxy={http_value}
httpsProxy={http_value}
socksProxy=
"""
    cfg = sp.parse_kioslaverc(text)
    assert cfg.mode == "manual" and cfg.proxies["https"] == "http://proxy.corp:8080"
    assert cfg.bypass == ["localhost", ".corp"]


def test_kioslaverc_other_modes():
    assert sp.parse_kioslaverc("[Proxy Settings]\nProxyType=2\nProxy Config Script=http://p/x.pac\n").pac_url \
        == "http://p/x.pac"
    assert sp.parse_kioslaverc("[Proxy Settings]\nProxyType=3\n").mode == "wpad"
    assert sp.parse_kioslaverc("[Proxy Settings]\nProxyType=0\nhttpProxy=http://x 1\n").mode == "direct"
    env = {"HTTP_PROXY": "http://envproxy:3128"}
    cfg = sp.parse_kioslaverc("[Proxy Settings]\nProxyType=4\nhttpProxy=HTTP_PROXY\nhttpsProxy=HTTPS_PROXY\n", env)
    assert cfg.proxies == {"http": "http://envproxy:3128"}


def test_environment_any_case():
    cfg = sp.from_environment({"https_proxy": "proxy:3128", "NO_PROXY": "localhost,10.0.0.0/8"})
    assert cfg.proxies == {"https": "http://proxy:3128"} and "10.0.0.0/8" in cfg.bypass
    assert sp.from_environment({}).mode == "direct"


def test_pac_result_parsing():
    assert sp.parse_pac_result("PROXY a:1; SOCKS5 b:2; DIRECT") == ["http://a:1", "socks5h://b:2", "direct://"]
    assert sp.parse_pac_result("SOCKS s:1080") == ["socks4a://s:1080"]
    assert sp.parse_pac_result("") == ["direct://"]


@pytest.mark.parametrize("url,patterns,expected", [
    ("https://intranet.corp/x", [".corp"], True),
    ("https://a.b.corp/x", ["*.corp"], True),
    ("https://corp.example/x", [".corp"], False),
    ("http://10.1.2.3/", ["10.0.0.0/8"], True),
    ("http://192.168.1.5/", ["169.254/16"], False),
    ("http://printer/", ["<local>"], True),
    ("http://printer.lan/", ["<local>"], False),
    ("https://anything.example/", ["*"], True),
    ("https://svc.corp:8443/", ["svc.corp:8443"], True),
    ("https://svc.corp:443/", ["svc.corp:8443"], False),
    ("http://[::1]:8080/", ["::1"], True),
])
def test_bypass_rules(url, patterns, expected):
    assert sp.bypassed(url, patterns) is expected


def test_choose_per_scheme():
    cfg = sp.SystemProxy(mode="manual", proxies={"http": "http://h:1", "https": "http://s:2"}, bypass=[".corp"])
    assert sp.choose(cfg, "https://youtube.com/") == ["http://s:2"]
    assert sp.choose(cfg, "http://example.com/") == ["http://h:1"]
    assert sp.choose(cfg, "https://wiki.corp/") == ["direct://"]
    socks_only = sp.SystemProxy(mode="manual", proxies={"socks": "socks5h://127.0.0.1:1080"})
    assert sp.choose(socks_only, "https://youtube.com/") == ["socks5h://127.0.0.1:1080"]


CORPORATE_PAC = r"""
function FindProxyForURL(url, host) {
    if (isPlainHostName(host) || dnsDomainIs(host, ".corp.example")) return "DIRECT";
    if (isInNet(host, "10.0.0.0", "255.0.0.0") || isInNet(host, "127.0.0.0", "255.0.0.0")) return "DIRECT";
    if (shExpMatch(host, "*.youtube.com") || shExpMatch(url, "https://youtu.be/*"))
        return "PROXY media-proxy.corp.example:8080; PROXY backup.corp.example:8080";
    if (myIpAddress() === "") return "DIRECT";
    return "PROXY proxy.corp.example:3128; DIRECT";
}
"""


def test_pac_runs_sandboxed():
    if sp._deno() is None:
        pytest.skip("Deno is not available")
    assert sp.run_pac(CORPORATE_PAC, "https://www.youtube.com/watch?v=x") == \
        ["http://media-proxy.corp.example:8080", "http://backup.corp.example:8080"]
    assert sp.run_pac(CORPORATE_PAC, "http://intranet/") == ["direct://"]
    assert sp.run_pac(CORPORATE_PAC, "http://127.0.0.1/") == ["direct://"]
    assert sp.run_pac(CORPORATE_PAC, "https://example.org/") == ["http://proxy.corp.example:3128", "direct://"]
    # a hostile script can't reach the network or files
    hostile = "function FindProxyForURL(u,h){ try { Deno.readTextFileSync('/etc/hostname'); return 'PROXY leak:1'; }" \
              " catch (e) { return 'DIRECT'; } }"
    assert sp.run_pac(hostile, "https://x.example/") == ["direct://"]


def test_special_systems(monkeypatch):
    monkeypatch.setattr(sp, "IS_WIN", False)
    monkeypatch.setattr(sp, "IS_MAC", False)
    monkeypatch.setattr(sp, "os_release", lambda: {"ID": "tails", "NAME": "Tails"})
    assert sp.special_system() == "tails"
    monkeypatch.setattr(sp, "os_release", lambda: {"ID": "debian"})
    monkeypatch.setattr(sp.os.path, "exists", lambda p: p == "/etc/whonix_version")
    assert sp.special_system() == "whonix"


# ── Network policy (core/network.py) ─────────────────────────────────────────



def cfg(**changes):
    c = dict(net.DEFAULTS)
    c.update({f"network.{k}": v for k, v in changes.items()})
    return c


@pytest.fixture(autouse=True)
def _no_special_system(request, monkeypatch):
    if request.function.__name__ != "test_special_systems":
        monkeypatch.setattr(sp, "special_system", lambda: "")
    monkeypatch.setattr(net, "_tails_direct", False)
    # the GUI harness switches the network off for the whole test process; these
    # tests only talk to servers on 127.0.0.1
    monkeypatch.delenv("CROSSFADER_NO_NETWORK", raising=False)


def test_modes_and_routes(monkeypatch):
    monkeypatch.setattr(sp, "proxies_for", lambda url, refresh=False: ["http://sys:3128", "direct://"])
    assert net.proxies_for("https://x.example/", cfg()) == ["http://sys:3128", "direct://"]      # auto
    assert net.proxies_for("https://x.example/", cfg(mode="direct")) == ["direct://"]
    manual = cfg(mode="manual", proxy_scheme="socks5h", proxy_port="9050", no_proxy="localhost,.lan")
    assert net.proxies_for("https://x.example/", manual) == ["socks5h://127.0.0.1:9050"]
    assert net.proxies_for("http://nas.lan/", manual) == ["direct://"]
    strict = dict(manual, **{"network.strict": True})
    assert net.proxies_for("http://nas.lan/", strict) == ["socks5h://127.0.0.1:9050"]      # no exceptions
    with pytest.raises(net.OfflineError):
        net.proxies_for("https://x.example/", cfg(mode="offline"))
    legacy = cfg(proxy_enabled=True, proxy_port="8080")                                      # pre-2.0 settings
    assert net.mode(legacy) == "manual"


def test_proxy_url_auth_and_ipv6():
    c = cfg(mode="manual", proxy_host="::1", proxy_port="8080", proxy_user="al ice", proxy_pass="p@ss:w")
    assert net.proxy_url(c) == "http://al%20ice:p%40ss%3Aw@[::1]:8080"


def test_tails_forces_tor(monkeypatch):
    monkeypatch.setattr(sp, "special_system", lambda: "tails")
    assert net.proxies_for("https://x.example/", cfg(mode="direct")) == ["socks5h://127.0.0.1:9050"]
    assert net.strict(cfg())


def test_yt_dlp_args_never_carry_passwords(monkeypatch):
    with_auth = cfg(mode="manual", proxy_port="3128", proxy_user="u", proxy_pass="secret")
    args = net.build_yt_dlp_args(with_auth)
    assert "--proxy" not in args and "secret" not in " ".join(args)
    env = net.subprocess_env(cfg=with_auth)
    assert env["HTTPS_PROXY"] == "http://u:secret@127.0.0.1:3128"
    assert net.build_yt_dlp_args(cfg(mode="direct"))[:2] == ["--proxy", ""]              # ignore system proxies
    plain = net.build_yt_dlp_args(cfg(mode="manual", proxy_port="7890"))
    assert plain[:2] == ["--proxy", "http://127.0.0.1:7890"]
    with pytest.raises(net.OfflineError):
        net.build_yt_dlp_args(cfg(mode="offline"))


def test_certificates_for_tools(tmp_path, monkeypatch):
    extra = tmp_path / "corp.pem"
    extra.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr("core.paths.cache_dir", lambda: str(tmp_path))
    env = net.subprocess_env(cfg=cfg(mode="direct", extra_ca=str(extra)))
    assert env["DENO_CERT"] == str(extra) and env["DENO_TLS_CA_STORE"] == "system,mozilla"
    bundle = env["SSL_CERT_FILE"]
    assert "MIIB" in open(bundle).read()                                                   # merged bundle
    if net.system_ca_file():
        assert len(open(bundle).read()) > len(extra.read_text())


def test_mirrors():
    c = cfg(hf_mirror="https://hf-mirror.com/", github_mirror="https://ghfast.top")
    assert net.mirror_url("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/x.bin", c) == \
        "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/x.bin"
    assert net.mirror_url("https://github.com/yt-dlp/yt-dlp/releases/latest/download/a", c) == \
        "https://ghfast.top/https://github.com/yt-dlp/yt-dlp/releases/latest/download/a"
    assert net.mirror_url("https://example.com/x", c) == "https://example.com/x"


def test_tor_fresh_circuits():
    cmd = ["yt-dlp", "--proxy", "socks5h://127.0.0.1:9050", "https://youtu.be/x"]
    tor = net.tor_like_proxy(cmd)
    assert tor == "socks5h://127.0.0.1:9050"
    a, b = net.fresh_circuit(tor, 1), net.fresh_circuit(tor, 2)
    assert a != b and a.endswith("@127.0.0.1:9050") and a.startswith("socks5h://wme1-")
    assert net.tor_like_proxy(["yt-dlp", "--proxy", "http://127.0.0.1:7890", "u"]) is None
    assert net.looks_blocked(["ERROR: [youtube] x: Sign in to confirm you’re not a bot"])
    assert not net.looks_blocked(["ERROR: Unsupported URL"])
    assert net.tor_retries(cfg()) == 12 and net.tor_retries(cfg(tor_retries="3")) == 3
    assert net.tor_retries(cfg(tor_retries="999")) == 50 and net.tor_retries(cfg(tor_retries="0")) == 0


def test_wrapper_prefix_keeps_windows_paths():
    assert net.wrapper_prefix(cfg(wrapper='"C:\\Program Files\\Mullvad\\mullvad-exclude.exe" -x')) == \
        ["C:\\Program Files\\Mullvad\\mullvad-exclude.exe", "-x"]


class _Hello(http.server.BaseHTTPRequestHandler):
    """Answers any GET - used as a web server and as an HTTP proxy."""

    def do_GET(self):
        port = self.server.server_address[1]  # type: ignore[index]  # a TCP server: (host, port)
        body = f"hello via {port} for {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Quiet: the tests read assertions, not access logs."""


def _serve():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Hello)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_urlopen_follows_pac_fallbacks(monkeypatch):
    web, proxy = _serve(), _serve()
    try:
        dead = _free_port()
        url = f"http://127.0.0.1:{web.server_address[1]}/page"
        monkeypatch.setattr(sp, "proxies_for",
                            lambda u, refresh=False: [f"http://127.0.0.1:{dead}",
                                                      f"http://127.0.0.1:{proxy.server_address[1]}", "direct://"])
        with net.urlopen(url, cfg=cfg(), timeout=15) as r:
            text = r.read().decode()
        assert f"via {proxy.server_address[1]}" in text and "http://127.0.0.1" in text   # went through the proxy
        monkeypatch.setattr(sp, "proxies_for", lambda u, refresh=False: [f"http://127.0.0.1:{dead}", "direct://"])
        with net.urlopen(url, cfg=cfg(), timeout=15) as r:
            assert r.read().decode() == f"hello via {web.server_address[1]} for /page"     # fell back to direct
    finally:
        web.shutdown()
        proxy.shutdown()


def test_strict_never_falls_back(monkeypatch):
    dead = _free_port()
    c = cfg(mode="manual", proxy_port=str(dead), strict=True)
    with pytest.raises(OSError) as e:
        net.urlopen("http://127.0.0.1:9/x", cfg=c, timeout=3)
    assert "refused" in net.explain_error(e.value).lower() or "nothing answered" in net.explain_error(e.value).lower()


def test_offline_blocks_everything():
    with pytest.raises(net.OfflineError):
        net.urlopen("https://example.com/", cfg=cfg(mode="offline"))


def test_find_local_proxies(monkeypatch):
    srv = _serve()
    try:
        port = srv.server_address[1]
        monkeypatch.setattr(net, "LOCAL_PORTS", {port: "Test proxy", _free_port(): "Nothing"})
        found = net.find_local_proxies(timeout=0.5)
        assert found == [(port, "http", "Test proxy")]
    finally:
        srv.shutdown()


def test_explain_error_messages():
    import ssl as _ssl
    import urllib.error
    assert "certificate" in net.explain_error(urllib.error.URLError(_ssl.SSLCertVerificationError("x"))).lower()
    assert "username and password" in net.explain_error(
        urllib.error.HTTPError("http://x", 407, "Proxy Authentication Required", email.message.Message(), None))
    assert "looked up" in net.explain_error(urllib.error.URLError(socket.gaierror(-2, "x")))


def test_tails_wrap_mirrors_tails_launcher(monkeypatch):
    from core import tails
    argv = tails.wrap(["/opt/yt-dlp", "--version"])
    assert argv[:6] == ["sudo", "-n", "ip", "netns", "exec", "clearnet"]
    assert argv[argv.index("bwrap") - 5:argv.index("bwrap")] == ["sudo", "-u", "amnesia", "-H", "--"]
    assert argv[argv.index("/etc/resolv-over-clearnet.conf") - 1:argv.index("/etc/resolv-over-clearnet.conf") + 2] == \
        ["--bind", "/etc/resolv-over-clearnet.conf", "/etc/resolv.conf"]
    assert argv[-2:] == ["/opt/yt-dlp", "--version"]
    monkeypatch.setattr(sp, "special_system", lambda: "")
    assert tails.problems() == ["This is only for Tails."]


def test_downloader_banner_follows_tails_route(monkeypatch):
    pytest.importorskip("tkinter")
    from tests.gui_harness import Harness
    ui = Harness.instance()
    tab = ui.open("Video Downloader")
    from core import tails
    monkeypatch.setattr(tails, "direct_enabled", lambda: True)
    ui.pump(2.0)
    assert tab._route_banner.winfo_ismapped()
    monkeypatch.setattr(tails, "direct_enabled", lambda: False)
    ui.pump(2.0)
    assert not tab._route_banner.winfo_ismapped()
