"""
tab_advancedsettings.py  ─  Advanced Settings

System information, FFmpeg management, app preferences, skins, and
developer tools.  All settings I/O is delegated to core.settings -
no duplicated load/save logic.
"""

import contextlib
import logging
import os
import tkinter as tk
from functools import partial
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

from core import ffmpeg as ff
from core import proc as _proc
from core import settings, sysinfo, tasks
from core.hardware import (
    binaries_dir,
    get_local_version,
    open_in_explorer,
)
from core.i18n import LANGUAGE_NAMES, SUPPORTED_LANGUAGES, get_language, set_language, t
from core.scaling import px, scale_drawing
from core.settings import load_settings, update
from core.skins import (
    _CUSTOM_DEFAULTS,
    SKINS,
    get_skin,
    load_skin_name,
    save_custom_color,
    save_skin_name,
)
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.kit.stars import set_star_count, set_star_speed

logger = logging.getLogger("crossfader.system.advanced_settings")


class _NetForm:
    """What every section of the Network tab builds with: the scrolling body,
    the saved settings, one Tk variable per setting (kept in *variables*)."""

    def __init__(self, body: tk.Frame, cfg: dict[str, Any], variables: dict[str, tk.Variable]):
        self.body, self.cfg, self.variables = body, cfg, variables

    def var(self, key: str) -> tk.Variable:
        from core import network as _net

        default = _net.DEFAULTS[key]
        v: tk.Variable = (
            tk.BooleanVar(value=bool(self.cfg.get(key, default)))
            if isinstance(default, bool)
            else tk.StringVar(value=str(self.cfg.get(key, default) or ""))
        )
        self.variables[key] = v
        return v

    def text_var(self, key: str) -> tk.StringVar:
        v = self.var(key)
        assert isinstance(v, tk.StringVar), key
        return v

    def section(self, title: str) -> tk.LabelFrame:
        lf = tk.LabelFrame(self.body, text=f"  {title}  ", padx=px(12), pady=px(8))
        lf.pack(fill="x", padx=px(20), pady=px(6))
        return lf

    def hint(self, parent: tk.Misc, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
            justify="left",
            anchor="w",
            wraplength=px(760),
        ).pack(anchor="w", fill="x")


class AdvancedSettingsTab(BaseTab):
    # made by the network section's builders (the app and Tor drop-downs)
    _net_app_var: tk.StringVar
    _net_tor_var: tk.StringVar

    def __init__(self, parent):
        super().__init__(parent)
        self.settings = load_settings()
        self._build_ui()

    def _build_ui(self):
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="⚙  " + t("tab.advanced_settings"),
            font=(UI_FONT, 16, "bold"),
            bg=CLR["panel"],
            fg=CLR["accent"],
        ).pack(side="left", padx=px(20), pady=px(12))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=px(15), pady=px(10))

        self._build_ffmpeg_tab(nb)
        self._build_defaults_tab(nb)
        self._build_networking_tab(nb)
        self._build_skins_tab(nb)
        self._build_language_tab(nb)
        self._build_system_tab(nb)
        self._build_dev_tab(nb)

    # ─── FFmpeg Management ────────────────────────────────────────────────────
    def _build_ffmpeg_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  FFmpeg  ")

        tk.Label(f, text=t("adv.ffmpeg_management"), font=(UI_FONT, 12, "bold")).pack(
            pady=px(20, 10)
        )

        ver_f = tk.LabelFrame(
            f, text=f"  {t('advanced_settings.version_status_section')}  ", padx=px(15), pady=px(10)
        )
        ver_f.pack(fill="x", padx=px(20), pady=px(8))

        self.local_ver_lbl = tk.Label(
            ver_f, text=t("advanced_settings.local_checking"), font=(MONO_FONT, 11)
        )
        self.local_ver_lbl.pack(anchor="w")
        self.online_ver_lbl = tk.Label(
            ver_f, text=t("advanced_settings.online_checking"), font=(MONO_FONT, 11)
        )
        self.online_ver_lbl.pack(anchor="w")

        tk.Label(
            ver_f,
            text="The app ships the FFmpeg build every tool is tested with; it is updated "
            "together with the app (there is no separate FFmpeg updater, so an FFmpeg "
            "change can never break a tool).",
            fg=CLR["fgdim"],
            wraplength=px(640),
            justify="left",
        ).pack(anchor="w", pady=px(4, 0))
        btn_row = tk.Frame(ver_f)
        btn_row.pack(anchor="w", pady=px(8))
        tk.Button(
            btn_row,
            text=t("advanced_settings.open_bin_folder"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=self._open_bin,
        ).pack(side="left", padx=px(4))

        cust_f = tk.LabelFrame(
            f, text=f"  {t('advanced_settings.custom_ffmpeg_section')}  ", padx=px(15), pady=px(10)
        )
        cust_f.pack(fill="x", padx=px(20), pady=px(5))
        tk.Label(cust_f, text=t("adv.ffmpeg_path_label"), fg=CLR["fgdim"]).pack(anchor="w")
        path_row = tk.Frame(cust_f)
        path_row.pack(fill="x", pady=px(4))
        self.ffmpeg_path_var = tk.StringVar(value=self.settings.get("ffmpeg_path_override", ""))
        tk.Entry(path_row, textvariable=self.ffmpeg_path_var, width=60).pack(side="left")
        tk.Button(
            path_row,
            text=t("btn.browse"),
            command=lambda: self._browse_binary("ffmpeg_path_override", self.ffmpeg_path_var),
        ).pack(side="left", padx=px(6))

        self.upd_console = scrolledtext.ScrolledText(
            f, height=8, bg=CLR["console_bg"], fg="#00FF88", font=(MONO_FONT, 9)
        )
        self.upd_console.pack(fill="both", expand=True, padx=px(20), pady=px(8))

        tasks.submit(self._check_versions)

    def _check_versions(self):
        from core import updater
        from core.hardware import find_binary

        local = get_local_version()
        path = find_binary("ffmpeg") or ""
        where = "bundled with the app" if updater.is_app_managed(path) else f"your own copy: {path}"
        self.after(0, lambda: self.local_ver_lbl.config(text=f"FFmpeg {local}"))
        self.after(0, lambda: self.online_ver_lbl.config(text=where))

    def _open_bin(self):
        bin_dir = binaries_dir()
        if not open_in_explorer(bin_dir):
            messagebox.showinfo("Bin folder", bin_dir)

    def _browse_binary(self, key, var):
        p = dialogs.askopenfilename(
            filetypes=[("Program", "*.exe")] if os.name == "nt" else [("All files", "*")]
        )
        if p:
            var.set(p)
            self.settings[key] = p
            update({key: p})

    # ─── Defaults ─────────────────────────────────────────────────────────────
    def _build_defaults_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  Defaults  ")
        tk.Label(f, text=t("adv.encoding_defaults"), font=(UI_FONT, 12, "bold")).pack(
            pady=px(20, 10)
        )

        opts = tk.LabelFrame(
            f, text=f"  {t('advanced_settings.default_encode_section')}  ", padx=px(20), pady=px(12)
        )
        opts.pack(fill="x", padx=px(20), pady=px(8))

        rows = [
            ("Default CRF:", "default_crf", "18", None),
            (
                "Default Preset:",
                "default_preset",
                "fast",
                [
                    "ultrafast",
                    "superfast",
                    "veryfast",
                    "faster",
                    "fast",
                    "medium",
                    "slow",
                    "slower",
                    "veryslow",
                ],
            ),
            (
                "Default Audio Bitrate:",
                "default_audio_bitrate",
                "192k",
                ["96k", "128k", "192k", "256k", "320k"],
            ),
        ]

        self._settings_vars = {}
        for i, (label, key, default, choices) in enumerate(rows):
            tk.Label(opts, text=label, width=24, anchor="e").grid(row=i, column=0, pady=px(6))
            var = tk.StringVar(value=self.settings.get(key, default))
            self._settings_vars[key] = var
            if choices:
                ttk.Combobox(
                    opts, textvariable=var, values=choices, state="readonly", width=16
                ).grid(row=i, column=1, sticky="w", padx=px(8))
            else:
                tk.Entry(opts, textvariable=var, width=8, relief="flat").grid(
                    row=i, column=1, sticky="w", padx=px(8)
                )

        of = tk.Frame(opts)
        of.grid(row=len(rows), column=0, columnspan=3, sticky="w", pady=px(6))
        tk.Label(of, text="Default Output Folder:", width=24, anchor="e").pack(side="left")
        self.out_folder_var = tk.StringVar(value=self.settings.get("default_output_folder", ""))
        tk.Entry(of, textvariable=self.out_folder_var, width=40).pack(side="left", padx=px(8))
        tk.Button(
            of,
            text=t("btn.browse"),
            command=lambda: self.out_folder_var.set(
                dialogs.askdirectory() or self.out_folder_var.get()
            ),
        ).pack(side="left")

        self.auto_open_var = tk.BooleanVar(value=self.settings.get("auto_open_output", False))
        tk.Checkbutton(
            opts, text="Auto-open output folder after render", variable=self.auto_open_var
        ).grid(row=len(rows) + 1, column=0, columnspan=3, sticky="w", pady=px(4))

        tk.Button(
            f,
            text=t("advanced_settings.save_settings"),
            bg=CLR["green"],
            fg="white",
            font=(UI_FONT, 11, "bold"),
            command=self._save_settings,
        ).pack(pady=px(15))
        self.save_lbl = tk.Label(f, text="", fg=CLR["green"])
        self.save_lbl.pack()

    def _save_settings(self):
        changes = {key: var.get() for key, var in self._settings_vars.items()}
        changes["default_output_folder"] = self.out_folder_var.get()
        changes["auto_open_output"] = self.auto_open_var.get()
        changes["ffmpeg_path_override"] = self.ffmpeg_path_var.get()
        override = changes["ffmpeg_path_override"].strip()
        if override and not os.path.isfile(override):
            self.save_lbl.config(text=f"FFmpeg not found at: {override}", fg=CLR["red"])
            return
        self.settings.update(changes)
        if update(changes):
            self.save_lbl.config(text=t("advanced_settings.settings_saved"), fg=CLR["green"])
        else:
            self.save_lbl.config(text=t("advanced_settings.save_failed"), fg=CLR["red"])

    # ─── Networking ───────────────────────────────────────────────────────────
    def _build_networking_tab(self, nb):
        from core import network as _net

        f = ttk.Frame(nb)
        nb.add(f, text="  Network  ")
        outer = tk.Frame(f, bg=CLR["bg"])
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=CLR["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=CLR["bg"])
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        self._net_vars = {}
        form = _NetForm(body, _net.get(), self._net_vars)
        for build in (
            self._net_now,
            self._net_situations,
            self._net_own_proxy,
            self._net_certificates,
            self._net_mirrors,
            self._net_details,
        ):
            build(form)

    def _net_now(self, form: _NetForm) -> None:
        from core import sysproxy

        section = form.section
        body = form.body

        # ── What happens now ─────────────────────────────────────────────────
        now = section("Right now")
        self._net_now_lbl = tk.Label(
            now,
            text="Checking…",
            anchor="w",
            justify="left",
            wraplength=px(760),
            font=(UI_FONT, 10, "bold"),
        )
        self._net_now_lbl.pack(anchor="w", fill="x")
        self._net_sys_lbl = tk.Label(
            now, text="", anchor="w", justify="left", wraplength=px(760), fg=CLR["fgdim"]
        )
        self._net_sys_lbl.pack(anchor="w", fill="x")
        row = tk.Frame(now)
        row.pack(anchor="w", pady=px(6, 0))
        tk.Button(row, text="Test the connection", command=self._net_test).pack(side="left")
        tk.Button(row, text="Save", bg=CLR["green"], fg="white", command=self._net_save).pack(
            side="left", padx=px(6)
        )
        tk.Button(row, text="Reset everything", fg=CLR["red"], command=self._net_reset_all).pack(
            side="left"
        )
        self._net_status_lbl = tk.Label(
            now, text="", anchor="w", justify="left", wraplength=px(760)
        )
        self._net_status_lbl.pack(anchor="w", fill="x", pady=px(4, 0))

        special = sysproxy.special_system()
        if special:
            self._build_tor_system_section(body, special)

    def _net_pick(self, situation):
        self._net_situation_var.set(situation)
        self._net_choose(situation)

    def _net_situations(self, form: _NetForm) -> None:
        from core import network as _net

        section = form.section
        cfg = form.cfg

        # ── Situations ───────────────────────────────────────────────────────
        sit = section("How should the app connect?")
        apps = [
            x
            for x in _net.SITUATIONS
            if x[0] not in ("auto", "direct", "offline", "tor", "tor_browser")
        ]
        tors = [x for x in _net.SITUATIONS if x[0] in ("tor_browser", "tor")]
        self._net_app_labels = {title: sid for sid, title, _d, _v in apps}
        self._net_tor_labels = {title: sid for sid, title, _d, _v in tors}
        current = self._net_situation(cfg)
        kind = (
            "app"
            if current in self._net_app_labels.values()
            else "tor"
            if current in self._net_tor_labels.values()
            else current
        )
        self._net_situation_var = tk.StringVar(value=kind)

        def radio(value, text, desc="", extra=None):
            r = tk.Frame(sit)
            r.pack(anchor="w", fill="x", pady=px(2))
            top = tk.Frame(r)
            top.pack(anchor="w")
            tk.Radiobutton(
                top,
                text=text,
                variable=self._net_situation_var,
                value=value,
                command=lambda: self._net_choose(value),
            ).pack(side="left")
            if extra:
                extra(top)
            if desc:
                tk.Label(
                    r,
                    text=desc,
                    fg=CLR["fgdim"],
                    font=(UI_FONT, 8),
                    wraplength=px(720),
                    justify="left",
                ).pack(anchor="w", padx=px(24, 0))

        def app_box(parent):
            self._net_app_var = tk.StringVar(
                value=next(
                    (t_ for t_, i in self._net_app_labels.items() if i == current), apps[0][1]
                )
            )
            cb = ttk.Combobox(
                parent,
                textvariable=self._net_app_var,
                values=list(self._net_app_labels),
                state="readonly",
                width=44,
            )
            cb.pack(side="left", padx=px(6))
            cb.bind(
                "<<ComboboxSelected>>",
                lambda _e: self._net_pick("app"),
            )
            tk.Button(parent, text="Find…", command=self._net_find_local).pack(side="left")

        def tor_box(parent):
            self._net_tor_var = tk.StringVar(
                value=next(
                    (t_ for t_, i in self._net_tor_labels.items() if i == current), tors[0][1]
                )
            )
            cb = ttk.Combobox(
                parent,
                textvariable=self._net_tor_var,
                values=list(self._net_tor_labels),
                state="readonly",
                width=34,
            )
            cb.pack(side="left", padx=px(6))
            cb.bind(
                "<<ComboboxSelected>>",
                lambda _e: self._net_pick("tor"),
            )

        desc = {sid: d for sid, _t, d, _v in _net.SITUATIONS}
        radio("auto", "Automatic - follow this computer's settings", desc["auto"])
        radio("direct", "No proxy - connect directly", desc["direct"])
        radio(
            "app",
            "A proxy app on this computer:",
            "Clash, v2rayN, Shadowsocks, NekoRay, Hiddify, Privoxy... "
            "'Find…' looks for one that is running.",
            app_box,
        )
        radio(
            "tor",
            "Tor:",
            "Everything goes through Tor and nothing goes out without it. Some sites block Tor; "
            "downloads then retry through fresh Tor circuits.",
            tor_box,
        )
        radio("custom", "My own proxy (details below)")
        radio("offline", "Offline - never use the network", desc["offline"])
        self._net_found_lbl = tk.Label(sit, text="", fg=CLR["fgdim"])
        self._net_found_lbl.pack(anchor="w", padx=px(24, 0))
        self._net_found_frame = tk.Frame(sit)
        self._net_found_frame.pack(anchor="w", padx=px(24, 0))

    def _net_own_proxy(self, form: _NetForm) -> None:
        section = form.section
        hint = form.hint
        var = form.var

        # ── Own proxy ────────────────────────────────────────────────────────
        own = section("Your own proxy")
        var("network.mode")
        g = tk.Frame(own)
        g.pack(anchor="w")
        tk.Label(g, text="Type").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            g,
            textvariable=var("network.proxy_scheme"),
            state="readonly",
            width=9,
            values=["http", "https", "socks5h", "socks5", "socks4a", "socks4"],
        ).grid(row=0, column=1, sticky="w", padx=px(4))
        tk.Label(g, text="Address").grid(row=0, column=2, sticky="w", padx=px(8, 0))
        tk.Entry(g, textvariable=var("network.proxy_host"), width=26).grid(
            row=0, column=3, padx=px(4)
        )
        tk.Label(g, text="Port").grid(row=0, column=4, sticky="w")
        tk.Entry(g, textvariable=var("network.proxy_port"), width=7).grid(
            row=0, column=5, padx=px(4)
        )
        tk.Label(g, text="Username").grid(row=1, column=0, sticky="w", pady=px(4))
        tk.Entry(g, textvariable=var("network.proxy_user"), width=18).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=px(4)
        )
        tk.Label(g, text="Password").grid(row=1, column=3, sticky="e")
        tk.Entry(g, textvariable=var("network.proxy_pass"), width=18, show="•").grid(
            row=1, column=4, columnspan=2, sticky="w", padx=px(4)
        )
        tk.Checkbutton(
            own,
            text="Strict: never connect without this proxy (for Tor and privacy proxies)",
            variable=var("network.strict"),
        ).pack(anchor="w")
        r = tk.Frame(own)
        r.pack(anchor="w", pady=px(2))
        tk.Label(r, text="When a site blocks Tor, retry through a fresh Tor circuit up to").pack(
            side="left"
        )
        tk.Spinbox(r, from_=0, to=50, width=4, textvariable=var("network.tor_retries")).pack(
            side="left", padx=px(4)
        )
        tk.Label(r, text="times (0 = off)").pack(side="left")
        hint(
            own,
            "Proxies that sign you in automatically with your Windows account (NTLM/Kerberos) need a local "
            "helper such as Px or CNTLM; then use its local address here. The password is never put on a "
            "command line.",
        )
        for key in ("network.proxy_scheme", "network.proxy_host", "network.proxy_port"):
            self._net_vars[key].trace_add(
                "write", lambda *_a: self._net_situation_var.set("custom")
            )

    def _choose_certificate(self, ca):
        path = dialogs.askopenfilename(
            filetypes=[("Certificates", "*.pem *.crt *.cer"), ("All files", "*")]
        )
        ca.set(path or ca.get())

    def _net_certificates(self, form: _NetForm) -> None:
        section = form.section
        hint = form.hint
        var = form.var
        text_var = form.text_var

        # ── Certificates ─────────────────────────────────────────────────────
        cert = section("Security certificates")
        tk.Checkbutton(
            cert,
            text="Trust this computer's certificates (needed on many company and school networks)",
            variable=var("network.system_certs"),
        ).pack(anchor="w")
        r = tk.Frame(cert)
        r.pack(anchor="w", fill="x", pady=px(2))
        tk.Label(r, text="Extra certificate file (PEM):").pack(side="left")
        ca = text_var("network.extra_ca")
        tk.Entry(r, textvariable=ca, width=44).pack(side="left", padx=px(4))
        tk.Button(
            r,
            text="Choose…",
            command=partial(self._choose_certificate, ca),
        ).pack(side="left")
        tk.Checkbutton(
            cert,
            text="Check certificates at all (turning this off is unsafe)",
            variable=var("network.verify_ssl"),
        ).pack(anchor="w")
        hint(
            cert,
            "Security products such as Zscaler, Netskope or a school firewall re-sign websites with their own "
            "certificate. It is usually installed on the computer already; if not, ask IT for the file.",
        )

    def _net_mirrors(self, form: _NetForm) -> None:
        section = form.section
        hint = form.hint
        var = form.var

        # ── Mirrors ──────────────────────────────────────────────────────────
        mir = section("Mirrors (when download sites are blocked)")
        g = tk.Frame(mir)
        g.pack(anchor="w")
        tk.Label(g, text="Speech models (Hugging Face) mirror:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            g, textvariable=var("network.hf_mirror"), width=36, values=["", "https://hf-mirror.com"]
        ).grid(row=0, column=1, padx=px(4), pady=px(2))
        tk.Label(g, text="GitHub download prefix:").grid(row=1, column=0, sticky="w")
        ttk.Combobox(
            g,
            textvariable=var("network.github_mirror"),
            width=36,
            values=["", "https://ghfast.top/", "https://gh-proxy.com/"],
        ).grid(row=1, column=1, padx=px(4), pady=px(2))
        hint(
            mir,
            "Downloads are checked against fixed SHA-256 sums, so a mirror can't swap the files.",
        )

    def _net_details(self, form: _NetForm) -> None:
        from core import network as _net

        section = form.section
        hint = form.hint
        var = form.var

        # ── Connection details ───────────────────────────────────────────────
        con = section("Connection details")
        g = tk.Frame(con)
        g.pack(anchor="w")
        rows = [
            ("Don't use the proxy for (comma-separated):", "network.no_proxy", 40),
            ("Bandwidth limit for downloads (e.g. 2M, 500K):", "network.bandwidth_limit", 10),
            ("Send from this local address (choose the connection):", "network.source_address", 20),
            ("Run downloads through this command (VPN split tunnelling):", "network.wrapper", 30),
            ("Timeout (seconds):", "network.socket_timeout", 6),
            ("Browser identity (User-Agent), blank = default:", "network.user_agent", 40),
        ]
        for i, (label, key, width) in enumerate(rows):
            tk.Label(g, text=label).grid(row=i, column=0, sticky="w", pady=px(2))
            if key == "network.source_address":
                addresses = ttk.Combobox(g, textvariable=var(key), width=width, values=[""])
                addresses.grid(row=i, column=1, sticky="w", padx=px(4))
                tasks.submit(
                    _local_addresses,
                    name="LocalAddresses",  # asks `ip`: not on the UI thread
                    on_done=lambda found, cb=addresses: cb.config(values=["", *found]),
                )
            elif key == "network.wrapper":
                ttk.Combobox(
                    g,
                    textvariable=var(key),
                    width=width,
                    values=["", "mullvad-exclude", "proxychains4 -q", "torsocks"],
                ).grid(row=i, column=1, sticky="w", padx=px(4))
            else:
                tk.Entry(g, textvariable=var(key), width=width).grid(
                    row=i, column=1, sticky="w", padx=px(4)
                )
        r = tk.Frame(con)
        r.pack(anchor="w", pady=px(2))
        tk.Label(r, text="Internet protocol:").pack(side="left")
        ttk.Combobox(
            r,
            textvariable=var("network.ip_version"),
            values=["auto", "ipv4", "ipv6"],
            state="readonly",
            width=6,
        ).pack(side="left", padx=px(4))
        tk.Checkbutton(
            r, text="Allow plain (unencrypted) HTTP", variable=var("network.allow_http")
        ).pack(side="left", padx=px(10))
        hint(
            con,
            "The local address only helps where the network allows it (several connections, or a VPN with "
            "split tunnelling). Where an organisation's VPN sends all traffic through itself, the app "
            "doesn't try to get around that.",
        )
        for key in _net.DEFAULTS:
            if key not in self._net_vars:
                var(key)
        self._net_refresh_now()

    def _net_choose(self, kind):
        if kind == "app":
            self._net_apply_preset(self._net_app_labels.get(self._net_app_var.get(), "clash"))
        elif kind == "tor":
            self._net_apply_preset(self._net_tor_labels.get(self._net_tor_var.get(), "tor_browser"))
        else:
            self._net_apply_preset(kind)

    def _net_situation(self, cfg) -> str:
        from core import network as _net

        m = _net.mode(cfg)
        if m in ("auto", "direct", "offline"):
            return m
        for sid, _t, _d, values in _net.SITUATIONS:
            if values.get("network.mode") == "manual" and all(
                str(cfg.get(k)) == str(v) for k, v in values.items() if k != "network.proxy_enabled"
            ):
                return sid
        return "custom"

    def _net_refresh_now(self):
        from core import network as _net
        from core import sysproxy

        def work():
            try:
                now = _net.describe()
                system = sysproxy.system_config(refresh=True).describe()
            except Exception as exc:  # never let a lookup break the settings page
                logger.debug("work: best-effort step failed", exc_info=True)
                now, system = f"Couldn't check: {exc}", ""
            return now, system

        def done(res):
            try:
                self._net_now_lbl.config(text=res[0])
                self._net_sys_lbl.config(
                    text="This computer's own setting: " + res[1] if res[1] else ""
                )
            except tk.TclError:
                pass

        tasks.submit(work, on_done=done, on_error=lambda e: None, name="NetDescribe")

    def _net_find_local(self):
        from core import network as _net

        self._net_found_lbl.config(text="Looking…")
        for w in self._net_found_frame.winfo_children():
            w.destroy()

        def done(found):
            if not found:
                self._net_found_lbl.config(text="No proxy apps found on the usual ports.")
                return
            self._net_found_lbl.config(text=f"Found {len(found)}:")
            for port, scheme, label in found:
                tk.Button(
                    self._net_found_frame,
                    text=f"Use {label} - {scheme} 127.0.0.1:{port}",
                    relief="flat",
                    command=lambda p=port, sc=scheme: self._net_use_local(sc, p),
                ).pack(anchor="w")

        tasks.submit(
            _net.find_local_proxies,
            on_done=done,
            on_error=lambda e: self._net_found_lbl.config(text=str(e)),
            name="ProxyProbe",
        )

    def _net_use_local(self, scheme, port):
        self._net_vars["network.mode"].set("manual")
        self._net_vars["network.proxy_enabled"].set(True)
        self._net_vars["network.proxy_scheme"].set(scheme)
        self._net_vars["network.proxy_host"].set("127.0.0.1")
        self._net_vars["network.proxy_port"].set(str(port))
        self._net_situation_var.set("custom")
        self._net_status_lbl.config(text="Filled in - click Test, then Save.", fg=CLR["fgdim"])

    def _build_tor_system_section(self, body, special):
        lf = tk.LabelFrame(
            body,
            text="  " + ("Tails" if special == "tails" else "Whonix") + "  ",
            padx=px(12),
            pady=px(8),
        )
        lf.pack(fill="x", padx=px(20), pady=px(6))
        if special == "whonix":
            tk.Label(
                lf,
                text="Whonix's gateway already sends all traffic through Tor - no proxy is needed here.",
                wraplength=px(760),
                justify="left",
            ).pack(anchor="w")
            return
        tk.Label(
            lf,
            text="Tails sends everything through Tor, and some sites (YouTube often) block Tor. Downloads "
            "retry through fresh Tor circuits automatically. If that isn't enough, Tails' own "
            "mechanism for its Unsafe Browser can be used for downloads only, for this session.",
            wraplength=px(760),
            justify="left",
        ).pack(anchor="w")
        self._tails_lbl = tk.Label(lf, text="", fg=CLR["fgdim"], wraplength=px(760), justify="left")
        self._tails_lbl.pack(anchor="w", pady=px(4))
        r = tk.Frame(lf)
        r.pack(anchor="w")
        self._tails_on_btn = tk.Button(
            r,
            text="Allow direct downloads (not anonymous)…",
            fg=CLR["red"],
            command=self._tails_enable,
        )
        self._tails_on_btn.pack(side="left")
        tk.Button(r, text="Turn off", command=self._tails_disable).pack(side="left", padx=px(6))
        self._tails_refresh()

    def _tails_refresh(self):
        from core import tails

        if tails.direct_enabled():
            text, colour = "Direct downloads are ON for this session (not anonymous).", CLR["red"]
        else:
            issues = tails.problems()
            text = (
                "Available - needs your administration password."
                if not issues
                else "Not available right now:\n• " + "\n• ".join(issues)
            )
            colour = CLR["fgdim"]
        self._tails_lbl.config(text=text, fg=colour)

    def _tails_enable(self):
        from core import tails

        issues = tails.problems()
        if issues:
            messagebox.showinfo("Tails", "\n\n".join(issues), parent=self)
            return
        if not messagebox.askyesno(
            "Not anonymous",
            "Downloads will connect directly, NOT through Tor.\n\nThe websites you download from, and anyone "
            "watching this network, will see your real internet address. Everything else in Tails stays on "
            "Tor.\n\nThis lasts until you close the app or click Turn off.\n\nContinue?",
            icon="warning",
            default="no",
            parent=self,
        ):
            return
        dlg = tk.Toplevel(self)
        dlg.title("Administration password")
        dlg.transient(self.winfo_toplevel())
        tk.Label(dlg, text="Tails' administration password (set at the Welcome Screen):").pack(
            padx=px(14), pady=px(12, 4)
        )
        pw = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=pw, show="•", width=30)
        entry.pack(padx=px(14))
        remember = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dlg, text="Remember it until I close the app (kept in memory only)", variable=remember
        ).pack(padx=px(14), pady=px(4))
        msg = tk.Label(dlg, text="", fg=CLR["red"])
        msg.pack()

        def ok(_e=None):
            done, text = tails.enable(pw.get(), remember.get())
            pw.set("")
            if done:
                dlg.destroy()
                self._tails_refresh()
                messagebox.showinfo("Tails", text, parent=self)
            else:
                msg.config(text=text)

        bf = tk.Frame(dlg)
        bf.pack(pady=px(10))
        tk.Button(bf, text="Turn on", command=ok).pack(side="left", padx=px(4))

        def cancel():
            pw.set("")
            dlg.destroy()

        tk.Button(bf, text="Cancel", command=cancel).pack(side="left", padx=px(4))
        entry.bind("<Return>", ok)
        entry.focus_set()
        dlg.grab_set()

    def _tails_disable(self):
        from core import tails

        tails.disable()
        self._tails_refresh()

    def _net_collect(self):
        """Read every form variable into a settings-ready dict."""
        from core import network as _net

        out = {}
        for key, var in self._net_vars.items():
            default = _net.DEFAULTS[key]
            v = var.get()
            if isinstance(default, bool):
                out[key] = bool(v)
            else:
                out[key] = v
        return out

    def _net_save(self):
        values = self._net_collect()
        if self._net_situation_var.get() == "custom" or values.get("network.mode") == "manual":
            values["network.mode"] = (
                "manual" if values.get("network.proxy_port") else values.get("network.mode")
            )
            values["network.proxy_enabled"] = values["network.mode"] == "manual"
        ok = update(values)
        self._net_refresh_now()
        self._net_status_lbl.config(
            text=("Saved." if ok else "Save failed."), fg=(CLR["green"] if ok else CLR["red"])
        )

    def _net_test(self):
        from core import network as _net

        # Test against the *form* values, not the saved ones, so the user
        # can validate before clicking Save.
        cfg = dict(_net.DEFAULTS)
        cfg.update(self._net_collect())
        self._net_status_lbl.config(text="Testing…", fg=CLR["fgdim"])
        self.update_idletasks()

        def _work():
            ok, msg = _net.test(cfg=cfg)
            colour = CLR["green"] if ok else CLR["red"]
            text = ("Connection OK: " + msg) if ok else ("Connection failed: " + msg)
            self.after(0, partial(self._net_status_lbl.config, text=text, fg=colour))

        tasks.submit(_work)

    def _net_reset_field(self, key):
        from core import network as _net

        if key not in _net.DEFAULTS:
            return
        default = _net.DEFAULTS[key]
        var = self._net_vars.get(key)
        if var is None:
            return
        if isinstance(default, bool):
            var.set(bool(default))
        else:
            var.set(str(default))
        self._net_status_lbl.config(text=f"{key} reset (not saved yet).", fg=CLR["fgdim"])

    def _net_reset_all(self):
        from core import network as _net

        if not messagebox.askyesno(
            "Reset networking",
            "Reset every networking setting to its default?\n\n"
            "This clears any proxy, bandwidth cap, custom User-Agent, "
            "and other overrides you have made.",
        ):
            return
        _net.reset_all()
        # Refresh form variables from defaults.
        for key, default in _net.DEFAULTS.items():
            var = self._net_vars.get(key)
            if var is None:
                continue
            if isinstance(default, bool):
                var.set(bool(default))
            else:
                var.set(str(default))
        self._net_status_lbl.config(
            text="All networking settings reset to defaults and saved.", fg=CLR["green"]
        )

    def _net_apply_preset(self, preset_id):
        from core import network as _net

        if preset_id == "custom":
            self._net_vars["network.mode"].set("manual")
            self._net_vars["network.proxy_enabled"].set(True)
            return
        preset = _net.PRESETS.get(preset_id, {})
        if not preset:
            return
        # Apply preset values to the form variables (don't save until the
        # user clicks Save, so they can review and tweak first).
        for key, value in preset.items():
            var = self._net_vars.get(key)
            if var is None:
                continue
            if isinstance(_net.DEFAULTS.get(key), bool):
                var.set(bool(value))
            else:
                var.set(str(value))
        self._net_status_lbl.config(text="Chosen - click Save (or Test first).", fg=CLR["fgdim"])

    # ─── Skins ────────────────────────────────────────────────────────────────
    def _build_skins_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("advanced_settings.skins"))

        # Inner notebook: Themes | Custom
        inner_nb = ttk.Notebook(outer)
        inner_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))

        self._build_themes_subtab(inner_nb)
        self._build_custom_subtab(inner_nb)

    # ── Themes sub-tab ────────────────────────────────────────────────────────
    def _build_themes_subtab(self, inner_nb):
        current_skin, f = self._build_themes_subtab_skin(inner_nb)
        self._build_themes_subtab_skin_status(f)
        self._build_themes_subtab_star_speed_control_only_visi(f)
        self._build_themes_subtab_star_density_count(current_skin)

    def _build_themes_subtab_skin(self, inner_nb):
        f = ttk.Frame(inner_nb)
        inner_nb.add(f, text="  Themes  ")

        tk.Label(f, text=t("adv.skins_title"), font=(UI_FONT, 12, "bold")).pack(pady=px(16, 4))
        tk.Label(f, text=t("adv.skins_subtitle"), fg=CLR["fgdim"]).pack()

        current_skin = load_skin_name()
        self._skin_var = tk.StringVar(value=current_skin)

        # Scrollable card grid
        scroll_canvas = tk.Canvas(f, highlightthickness=0)
        scroll_canvas.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        vsb = ttk.Scrollbar(f, orient="vertical", command=scroll_canvas.yview)
        vsb.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        scroll_canvas.configure(yscrollcommand=vsb.set)

        grid_host = tk.Frame(scroll_canvas)
        scroll_canvas.create_window((0, 0), window=grid_host, anchor="nw")

        def _on_resize(evt):
            scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))

        grid_host.bind("<Configure>", _on_resize)

        # Theme cards (exclude "Custom" - it lives in the Custom tab)
        theme_names = [n for n in SKINS if n != "Custom"]
        COLS = 3
        self._skin_previews = {}

        for idx, name in enumerate(theme_names):
            skin = SKINS[name]
            row = idx // COLS
            col = idx % COLS

            card = tk.Frame(
                grid_host,
                relief="raised",
                bd=px(2),
                bg=skin["sb_bg"],
                highlightthickness=px(3),
                highlightbackground=skin["accent"],
            )
            card.grid(row=row, column=col, padx=px(10), pady=px(10), sticky="nsew")
            grid_host.columnconfigure(col, weight=1)

            c = tk.Canvas(
                card, width=px(180), height=px(80), bg=skin["sb_bg"], highlightthickness=0
            )
            c.pack(pady=px(8, 4))
            c.create_rectangle(0, 0, 40, 80, fill=skin["sb_bg"], outline="")
            c.create_rectangle(42, 0, 180, 80, fill=skin.get("content", "#1E1E1E"), outline="")
            for i in range(4):
                c.create_rectangle(
                    4,
                    8 + i * 18,
                    36,
                    20 + i * 18,
                    fill=skin["sb_hover"] if i == 1 else skin["sb_bg"],
                    outline="",
                )
                c.create_text(20, 14 + i * 18, text="─", fill=skin["sb_btn"], font=(UI_FONT, 7))
            c.create_rectangle(44, 8, 176, 26, fill=skin["panel"], outline="")
            c.create_text(110, 17, text=name, fill=skin["accent"], font=(UI_FONT, 8, "bold"))
            if skin.get("stars"):
                for sx, sy in [(20, 5), (60, 15), (120, 8), (155, 30), (80, 55), (30, 65)]:
                    c.create_text(sx, sy, text="✦", fill=skin["accent"], font=(UI_FONT, 6))
            scale_drawing(c)
            self._skin_previews[name] = c

            tk.Label(
                card, text=name, font=(UI_FONT, 11, "bold"), bg=skin["sb_bg"], fg=skin["accent"]
            ).pack()
            tk.Label(
                card,
                text=skin["desc"],
                font=(UI_FONT, 8),
                bg=skin["sb_bg"],
                fg=skin["sb_btn"],
                wraplength=px(170),
                justify="center",
            ).pack(pady=px(2, 6))

            rb = tk.Radiobutton(
                card,
                text=t("advanced_settings.select_this_skin"),
                variable=self._skin_var,
                value=name,
                bg=skin["sb_bg"],
                fg=skin["accent"],
                selectcolor=skin["sb_active"],
                activebackground=skin["sb_hover"],
                font=(UI_FONT, 9),
                command=lambda n=name: self._on_skin_select(n),
            )
            rb.pack(pady=px(0, 8))
        return current_skin, f

    def _build_themes_subtab_skin_status(self, f):
        apply_f = tk.Frame(f)
        apply_f.pack(pady=px(10))
        tk.Button(
            apply_f,
            text=t("advanced_settings.apply_skin_now"),
            bg=CLR["accent"],
            fg="black",
            font=(UI_FONT, 12, "bold"),
            command=self._apply_skin,
        ).pack(side="left", padx=px(8))
        self._skin_status = tk.Label(apply_f, text="", fg=CLR["green"])
        self._skin_status.pack(side="left", padx=px(8))

        tk.Label(
            f,
            text=(
                "✨  Mystical includes animated falling star particles across the app window.\n"
                "All skins apply instantly. No restart required."
            ),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(pady=px(2, 6))

    def _build_themes_subtab_star_speed_control_only_visi(self, f):
        """Star speed control (only visible when Mystical is active)."""
        # ── Star speed control (only visible when Mystical is active) ─────────
        self._star_speed_frame = tk.LabelFrame(
            f, text=t("advanced_settings.star_speed"), padx=px(16), pady=px(10)
        )

        speed_row = tk.Frame(self._star_speed_frame)
        speed_row.pack(fill="x")
        tk.Label(speed_row, text="Slow", font=(UI_FONT, 8), fg=CLR["fgdim"]).pack(side="left")

        saved_speed = settings.STAR_SPEED.get()
        self._star_speed_var = tk.DoubleVar(value=saved_speed)

        speed_slider = tk.Scale(
            speed_row,
            from_=0.15,
            to=4.0,
            resolution=0.05,
            orient="horizontal",
            variable=self._star_speed_var,
            showvalue=False,
            length=px(280),
            command=self._on_star_speed_change,
            bg="#3D1060",
            activebackground="#A840BC",
            troughcolor="#28184A",
            highlightthickness=px(1),
            highlightbackground="#6A2F8A",
            sliderrelief="raised",
            bd=px(2),
        )
        speed_slider.pack(side="left", padx=px(8), fill="x", expand=True)
        tk.Label(speed_row, text="Fast", font=(UI_FONT, 8), fg=CLR["fgdim"]).pack(side="left")

        self._star_speed_lbl = tk.Label(
            self._star_speed_frame,
            text=self._speed_label(saved_speed),
            font=(UI_FONT, 8),
            fg=CLR["fgdim"],
        )
        self._star_speed_lbl.pack()

    def _build_themes_subtab_star_density_count(self, current_skin):
        """Star density (count)."""
        # ── Star density (count) ───────────────────────────────────────────
        tk.Frame(self._star_speed_frame, height=px(6)).pack()  # spacer

        density_row = tk.Frame(self._star_speed_frame)
        density_row.pack(fill="x")
        tk.Label(density_row, text="Few", font=(UI_FONT, 8), fg=CLR["fgdim"]).pack(side="left")

        saved_count = settings.STAR_COUNT.get()
        self._star_count_var = tk.IntVar(value=saved_count)

        density_slider = tk.Scale(
            density_row,
            from_=10,
            to=120,
            resolution=1,
            orient="horizontal",
            variable=self._star_count_var,
            showvalue=False,
            length=px(280),
            command=self._on_star_count_change,
            bg="#3D1060",
            activebackground="#A840BC",
            troughcolor="#28184A",
            highlightthickness=px(1),
            highlightbackground="#6A2F8A",
            sliderrelief="raised",
            bd=px(2),
        )
        density_slider.pack(side="left", padx=px(8), fill="x", expand=True)
        tk.Label(density_row, text="Many", font=(UI_FONT, 8), fg=CLR["fgdim"]).pack(side="left")

        self._star_count_lbl = tk.Label(
            self._star_speed_frame, text=f"{saved_count} stars", font=(UI_FONT, 8), fg=CLR["fgdim"]
        )
        self._star_count_lbl.pack()

        self._update_star_speed_visibility(current_skin)

    # ── Custom skin sub-tab ───────────────────────────────────────────────────
    def _build_custom_subtab(self, inner_nb):
        """Scrollable color-property editor for the 'Custom' skin."""
        current_custom, inner, sections = self._build_custom_subtab_custom_status(inner_nb)
        self._build_custom_colour_rows(current_custom, inner, sections)
        self._build_custom_reset(inner)

    def _build_custom_subtab_custom_status(self, inner_nb):
        f = ttk.Frame(inner_nb)
        inner_nb.add(f, text="  Custom  ")

        # Header
        hdr = tk.Frame(f)
        hdr.pack(fill="x", padx=px(16), pady=px(14, 4))
        tk.Label(hdr, text=t("adv.custom_title"), font=(UI_FONT, 12, "bold")).pack(side="left")
        tk.Button(
            hdr,
            text=t("advanced_settings.apply_custom_skin"),
            bg=CLR["accent"],
            fg="black",
            font=(UI_FONT, 10, "bold"),
            command=self._apply_custom_skin,
        ).pack(side="right", padx=px(4))
        self._custom_status = tk.Label(hdr, text="", fg=CLR["green"])
        self._custom_status.pack(side="right", padx=px(8))

        tk.Label(f, text=t("adv.custom_subtitle"), fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(
            pady=px(0, 8)
        )

        # Scrollable area
        wrapper = tk.Frame(f)
        wrapper.pack(fill="both", expand=True, padx=px(12))

        canvas = tk.Canvas(wrapper, highlightthickness=0)
        vsb = ttk.Scrollbar(wrapper, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(evt):
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", _on_inner_configure)

        def _resize_inner(evt):
            canvas.itemconfig(win_id, width=evt.width)

        canvas.bind("<Configure>", _resize_inner)

        # Load current custom colors
        current_custom = get_skin("Custom")
        self._custom_vars: dict[str, tk.StringVar] = {}

        # Color sections: (Section title, [(display_label, color_key), ...])
        sections = [
            (
                "Sidebar",
                [
                    (t("advanced_settings.sidebar_background"), "sb_bg"),
                    (t("advanced_settings.category_header"), "sb_cat"),
                    (t("advanced_settings.button_text_icons"), "sb_btn"),
                    (t("advanced_settings.button_hover_background"), "sb_hover"),
                    (t("advanced_settings.active_button_background"), "sb_active"),
                ],
            ),
            (
                "Content Area",
                [
                    ("Content background", "content"),
                    ("Panel / card background", "panel"),
                    ("Primary text", "fg"),
                    ("Dimmed / secondary text", "fgdim"),
                    ("Border color", "border"),
                ],
            ),
            (
                "Accent & Inputs",
                [
                    ("Accent color", "accent"),
                    ("Input field background", "input_bg"),
                    ("Input field text", "input_fg"),
                ],
            ),
            (
                "Status Colors",
                [
                    ("Success / green", "green"),
                    ("Error / red", "red"),
                    ("Warning / orange", "orange"),
                    ("Highlight / pink", "pink"),
                ],
            ),
            (
                "Console",
                [
                    ("Console background", "console_bg"),
                    ("Console text", "console_fg"),
                ],
            ),
        ]
        return current_custom, inner, sections

    def _build_custom_colour_rows(self, current_custom, inner, sections):
        for section_title, props in sections:
            sec_frame = tk.LabelFrame(inner, text=f"  {section_title}  ", padx=px(12), pady=px(10))
            sec_frame.pack(fill="x", padx=px(8), pady=px(8, 4))

            for label_text, key in props:
                row = tk.Frame(sec_frame)
                row.pack(fill="x", pady=px(3))

                initial = current_custom.get(key, _CUSTOM_DEFAULTS.get(key, "#222222"))
                var = tk.StringVar(value=initial)
                self._custom_vars[key] = var

                tk.Label(row, text=label_text, width=26, anchor="w", font=(UI_FONT, 9)).pack(
                    side="left"
                )

                # Color swatch
                swatch = tk.Label(row, width=4, bg=initial, relief="solid", bd=px(1))
                swatch.pack(side="left", padx=px(4, 2))

                # Hex entry
                entry = tk.Entry(row, textvariable=var, width=9, font=(MONO_FONT, 9))
                entry.pack(side="left", padx=px(2))

                # Pick button - closure captures key, var, swatch
                def _pick(k=key, v=var, sw=swatch):
                    from tkinter import colorchooser

                    current_hex = v.get()
                    try:
                        _rgb, hex_color = colorchooser.askcolor(
                            color=current_hex, title=f"Choose color for {k}"
                        )
                    except Exception:
                        logger.debug("_pick: best-effort step failed", exc_info=True)
                        return
                    if hex_color:
                        hex_color = hex_color.upper()
                        v.set(hex_color)
                        with contextlib.suppress(tk.TclError):
                            sw.configure(bg=hex_color)
                        save_custom_color(k, hex_color)
                        # Update live preview swatch
                        self._custom_status.config(
                            text=t("advanced_settings.saved"), fg=CLR["green"]
                        )

                tk.Button(
                    row, text=t("advanced_settings.pick"), command=_pick, font=(UI_FONT, 8)
                ).pack(side="left", padx=px(4))

                # Keep swatch in sync when user types directly in entry
                def _sync_swatch(evt, sw=swatch, v=var, k=key):
                    val = v.get().strip()
                    if len(val) in (4, 7) and val.startswith("#"):
                        try:
                            sw.configure(bg=val)
                            save_custom_color(k, val.upper())
                            self._custom_status.config(
                                text=t("advanced_settings.saved"), fg=CLR["green"]
                            )
                        except Exception:
                            logger.debug("_sync_swatch: best-effort step failed", exc_info=True)

                entry.bind("<FocusOut>", _sync_swatch)
                entry.bind("<Return>", _sync_swatch)

    def _build_custom_reset(self, inner):
        # Reset to defaults button
        reset_row = tk.Frame(inner)
        reset_row.pack(fill="x", padx=px(8), pady=px(12, 8))
        tk.Button(
            reset_row,
            text=t("advanced_settings.reset_to_defaults"),
            bg=CLR["panel"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
            command=self._reset_custom_defaults,
        ).pack(side="left")

    def _apply_custom_skin(self):
        """Select 'Custom' and apply it live."""
        self._skin_var.set("Custom")
        self._on_skin_select("Custom")

    def _reset_custom_defaults(self):
        """Reset all custom color vars to _CUSTOM_DEFAULTS."""
        prefix = "custom_color_"
        changes = {}
        for key, val in _CUSTOM_DEFAULTS.items():
            if isinstance(val, str) and val.startswith("#"):
                self._custom_vars[key].set(val)
                changes[f"{prefix}{key}"] = val
        update(changes)
        try:
            self._custom_status.config(
                text=t("advanced_settings.reset_to_defaults_2"), fg=CLR["green"]
            )
        except Exception:
            logger.debug("_reset_custom_defaults: best-effort step failed", exc_info=True)

    def _on_skin_select(self, name):
        """Save and apply the selected skin live, immediately."""
        save_skin_name(name)
        self._update_star_speed_visibility(name)
        shell = self.shell()
        if shell is not None:
            shell.apply_theme(name)
            self._skin_status.config(text=f"✅  '{name}' applied live!", fg=CLR["accent"])
        else:
            self._skin_status.config(
                text=t("advanced_settings.saved_reopen_to_see_full_effect"), fg=CLR["green"]
            )

    def _apply_skin(self):
        self._on_skin_select(self._skin_var.get())

    def _update_star_speed_visibility(self, skin_name: str):
        """Show the star-speed panel only when a stars-enabled skin is active."""
        try:
            skin = get_skin(skin_name)
            if skin.get("stars"):
                self._star_speed_frame.pack(padx=px(20), pady=px(0, 12), fill="x")
            else:
                self._star_speed_frame.pack_forget()
        except Exception:
            logger.debug("_update_star_speed_visibility: best-effort step failed", exc_info=True)

    @staticmethod
    def _speed_label(mult: float) -> str:
        if mult < 0.4:
            return "Very slow"
        if mult < 0.75:
            return "Slow"
        if mult < 1.35:
            return "Default"
        if mult < 2.2:
            return "Fast"
        return "Very fast"

    def _on_star_speed_change(self, value):
        mult = float(value)
        set_star_speed(mult)
        try:
            self._star_speed_lbl.config(text=self._speed_label(mult))
        except Exception:
            logger.debug("_on_star_speed_change: best-effort step failed", exc_info=True)
        settings.STAR_SPEED.set(float(mult))

    def _on_star_count_change(self, value):
        count = int(float(value))
        set_star_count(count)
        with contextlib.suppress(tk.TclError):
            self._star_count_lbl.config(text=f"{count} stars")
        settings.STAR_COUNT.set(count)
        # restart the stars so the new density shows at once
        shell = self.shell()
        if shell is not None and get_skin(load_skin_name()).get("stars"):
            shell.apply_theme(load_skin_name())

    # ─── Language ─────────────────────────────────────────────────────────────
    def _build_language_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("advanced_settings.language"))

        tk.Label(
            f, text=t("advanced_settings.language_langue_idioma"), font=(UI_FONT, 12, "bold")
        ).pack(pady=px(20, 4))
        tk.Label(f, text=t("lang.subtitle"), font=(UI_FONT, 9), fg=CLR["fgdim"]).pack(
            pady=px(0, 18)
        )

        info_f = tk.Frame(f)
        info_f.pack(anchor="center")

        # Current language row
        row1 = tk.Frame(info_f)
        row1.pack(anchor="w", pady=px(4))
        tk.Label(row1, text=t("lang.current"), font=(UI_FONT, 10)).pack(side="left", padx=px(0, 8))
        cur_code = get_language()
        cur_name = LANGUAGE_NAMES.get(cur_code, cur_code)
        tk.Label(row1, text=cur_name, font=(UI_FONT, 10, "bold"), fg=CLR["accent"]).pack(
            side="left"
        )

        # Select language row
        row2 = tk.Frame(info_f)
        row2.pack(anchor="w", pady=px(12, 4))
        tk.Label(row2, text=t("advanced_settings.select_language"), font=(UI_FONT, 10)).pack(
            side="left", padx=px(0, 8)
        )

        display_names = [LANGUAGE_NAMES.get(c, c) for c in SUPPORTED_LANGUAGES]
        self._lang_var = tk.StringVar(value=LANGUAGE_NAMES.get(cur_code, cur_code))
        lang_cb = ttk.Combobox(
            row2,
            textvariable=self._lang_var,
            values=display_names,
            state="readonly",
            width=28,
            font=(UI_FONT, 10),
        )
        lang_cb.pack(side="left")

        # Save button
        def _save_language():
            chosen_name = self._lang_var.get()
            # Reverse look up: name → code
            code = next(
                (c for c in SUPPORTED_LANGUAGES if LANGUAGE_NAMES.get(c) == chosen_name), None
            )
            if not code:
                return
            set_language(code)
            msg_lbl.config(
                text=t("advanced_settings.language_saved_please_restart_the_app_to_apply_t"),
                fg=CLR["green"],
            )

        tk.Button(
            info_f,
            text=t("advanced_settings.save_language"),
            bg=CLR["accent"],
            fg="white",
            font=(UI_FONT, 10),
            padx=px(14),
            pady=px(4),
            relief="flat",
            cursor="hand2",
            command=_save_language,
        ).pack(pady=px(14))

        msg_lbl = tk.Label(
            info_f,
            text="",
            font=(UI_FONT, 9),
            fg=CLR["green"],
            wraplength=px(400),
            justify="center",
        )
        msg_lbl.pack()

    # ─── System Info ──────────────────────────────────────────────────────────
    def _build_system_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=t("advanced_settings.system_info"))
        self.system_info_page = (nb, f)  # Help → System Info selects it by widget

        hdr2 = tk.Frame(f)
        hdr2.pack(fill="x", padx=px(20), pady=px(16, 4))
        tk.Label(hdr2, text=t("adv.sys_hardware"), font=(UI_FONT, 12, "bold")).pack(side="left")
        tk.Button(
            hdr2,
            text=t("advanced_settings.refresh"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=lambda: self._load_sysinfo(info_area),
        ).pack(side="right")

        info_area = scrolledtext.ScrolledText(
            f,
            height=28,
            bg=CLR["console_bg"],
            fg="#00FF88",
            font=(MONO_FONT, 10),
            insertbackground="#00FF88",
        )
        info_area.pack(fill="both", expand=True, padx=px(20), pady=px(8))

        self._load_sysinfo(info_area)

    def _load_sysinfo(self, area: tk.Text) -> None:
        """Read the System Info report in the background, then show it in *area*."""
        tasks.submit(
            partial(sysinfo.report, str(tk.TkVersion)),
            on_done=partial(self._show_sysinfo, area),
            name="SystemInfo",
        )

    @staticmethod
    def _show_sysinfo(area: tk.Text, text: str) -> None:
        area.config(state="normal")
        area.delete("1.0", tk.END)
        area.insert(tk.END, text)
        area.config(state="disabled")

    # ─── Developer ────────────────────────────────────────────────────────────
    def _build_dev_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="  Developer  ")

        tk.Label(
            f, text=t("advanced_settings.ffmpeg_command_runner"), font=(UI_FONT, 12, "bold")
        ).pack(pady=px(20, 10))
        tk.Label(
            f, text=t("advanced_settings.run_any_custom_ffmpeg_command"), fg=CLR["fgdim"]
        ).pack(anchor="w", padx=px(20))

        self.dev_cmd_var = tk.StringVar()
        cmd_entry = tk.Entry(f, textvariable=self.dev_cmd_var, font=(MONO_FONT, 10), width=100)
        cmd_entry.pack(fill="x", padx=px(20), pady=px(4))
        cmd_entry.bind("<Return>", lambda _: self._run_dev_cmd())

        btn_row = tk.Frame(f)
        btn_row.pack(anchor="w", padx=px(20), pady=px(4))
        tk.Button(
            btn_row,
            text=t("advanced_settings.run"),
            bg=CLR["green"],
            fg="white",
            command=self._run_dev_cmd,
        ).pack(side="left", padx=px(4))
        tk.Button(
            btn_row,
            text=t("advanced_settings.clear"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=lambda: self.dev_console.delete("1.0", tk.END),
        ).pack(side="left", padx=px(4))
        tk.Button(
            btn_row,
            text=t("advanced_settings.paste_version"),
            command=lambda: self.dev_cmd_var.set(ff.example_cmd("version")),
            bg=CLR["panel"],
            fg=CLR["fg"],
        ).pack(side="left", padx=px(4))
        tk.Button(
            btn_row,
            text=t("advanced_settings.paste_codecs"),
            command=lambda: self.dev_cmd_var.set(ff.example_cmd("codecs")),
            bg=CLR["panel"],
            fg=CLR["fg"],
        ).pack(side="left", padx=px(4))

        tk.Label(f, text=t("encode_queue.output_label"), fg=CLR["fgdim"]).pack(
            anchor="w", padx=px(20), pady=px(8, 2)
        )
        self.dev_console = scrolledtext.ScrolledText(
            f, height=20, bg=CLR["console_bg"], fg="#00FF88", font=(MONO_FONT, 9)
        )
        self.dev_console.pack(fill="both", expand=True, padx=px(20), pady=px(4))

    def _dev_print(self, text):
        self.dev_console.insert(tk.END, text)
        self.dev_console.see(tk.END)

    def _run_dev_cmd(self):
        cmd_str = self.dev_cmd_var.get().strip()
        if not cmd_str:
            return
        self.dev_console.insert(tk.END, f"$ {cmd_str}\n")
        self.dev_console.see(tk.END)

        def _work():
            import shlex

            try:
                parts = shlex.split(cmd_str)
            except Exception:
                logger.debug("_work: best-effort step failed", exc_info=True)
                parts = cmd_str.split()
            proc = _proc.spawn(parts, stdout=_proc.PIPE, stderr=_proc.STDOUT, text=True)
            for line in _proc.lines(proc):
                self.after(0, self._dev_print, line)
            self.after(
                0, lambda: self.dev_console.insert(tk.END, f"\n[Exited {proc.returncode}]\n")
            )

        tasks.submit(_work)

    def show_system_info(self) -> None:
        nb, page = getattr(self, "system_info_page", (None, None))
        if nb is not None:
            nb.select(page)


def _local_addresses() -> list:
    """IPv4/IPv6 addresses of this computer's network connections."""
    import re as _re
    import socket as _socket

    from core import proc as _proc

    out = []
    try:
        if os.name == "nt":
            text = _proc.run(["ipconfig"], timeout=5).stdout
            out = _re.findall(r"IPv4[^:]*:\s*([0-9.]+)", text)
        else:
            text = _proc.run(["ip", "-o", "addr", "show", "scope", "global"], timeout=5).stdout
            out = [m.split("/")[0] for m in _re.findall(r"inet6?\s+(\S+)", text)]
    except _proc.ProcessError:
        pass
    if not out:
        with contextlib.suppress(OSError):
            found = {str(a[4][0]) for a in _socket.getaddrinfo(_socket.gethostname(), None)}
            out = sorted(a for a in found if not a.startswith(("127.", "::1")))
    return out
