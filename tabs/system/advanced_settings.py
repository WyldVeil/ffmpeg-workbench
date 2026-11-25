"""
tab_advancedsettings.py  ─  Advanced Settings

System information, FFmpeg management, app preferences, skins, and
developer tools.  All settings I/O is delegated to core.settings -
no duplicated load/save logic.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tkinter as tk
from collections.abc import Callable
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
from core.network import local_addresses
from core.scaling import px, scale_drawing
from core.settings import load_settings, update
from core.skins import (
    CUSTOM_DEFAULTS,
    SKINS,
    ColourKey,
    Skin,
    get_skin,
    load_skin_name,
    save_custom_color,
    save_skin_name,
)
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.kit.stars import set_star_count, set_star_speed
from tabs.kit.tktyped import on_write
from tabs.kit.widgets import ColourButton, normal_colour
from tabs.widgets import FolderRow, scroll_body

logger = logging.getLogger("crossfader.system.advanced_settings")


def _custom_sections() -> list[tuple[str, list[tuple[str, ColourKey]]]]:
    """The custom skin's colours by section: (title, [(label, colour key), ...])."""
    return [
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


FormVar = tk.StringVar | tk.BooleanVar  # a network setting: text, or a tick box


def _set_form(var: FormVar, value: object) -> None:
    """Put a setting's *value* in its form variable (a tick box takes a bool)."""
    if isinstance(var, tk.BooleanVar):
        var.set(bool(value))
    else:
        var.set(str(value))


class _NetForm:
    """What every section of the Network tab builds with: the scrolling body,
    the saved settings, one Tk variable per setting (kept in *variables*)."""

    def __init__(self, body: tk.Frame, cfg: dict[str, Any], variables: dict[str, FormVar]) -> None:
        self.body, self.cfg, self.variables = body, cfg, variables

    def var(self, key: str) -> FormVar:
        from core import network as _net

        default = _net.DEFAULTS[key]
        v: FormVar = (
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

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.settings = load_settings()
        self._build_ui()

    def _build_ui(self) -> None:
        make_header(self, t("tab.advanced_settings"), icon="⚙")

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
    def _build_ffmpeg_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb)
        nb.add(f, text=t("advanced_settings.ffmpeg"))

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
            text=t("advanced_settings.the_app_ships_the_ffmpeg_build_every"),
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

    def _check_versions(self) -> None:
        from core import updater
        from core.hardware import find_binary

        local = get_local_version()
        path = find_binary("ffmpeg") or ""
        where = "bundled with the app" if updater.is_app_managed(path) else f"your own copy: {path}"
        self.after(
            0,
            lambda: self.local_ver_lbl.config(
                text=t("advanced_settings.ffmpeg_local", local=local)
            ),
        )
        self.after(0, lambda: self.online_ver_lbl.config(text=where))

    def _open_bin(self) -> None:
        bin_dir = binaries_dir()
        if not open_in_explorer(bin_dir):
            messagebox.showinfo(t("advanced_settings.bin_folder"), bin_dir)

    def _browse_binary(self, key: str, var: tk.StringVar) -> None:
        p = dialogs.askopenfilename(
            filetypes=[("Program", "*.exe")] if os.name == "nt" else [("All files", "*")]
        )
        if p:
            var.set(p)
            self.settings[key] = p
            update({key: p})

    # ─── Defaults ─────────────────────────────────────────────────────────────
    def _build_defaults_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb)
        nb.add(f, text=t("advanced_settings.defaults"))
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

        self._settings_vars: dict[str, tk.StringVar] = {}
        for i, (label, key, default, choices) in enumerate(rows):
            var = tk.StringVar(value=self.settings.get(key, default))
            self._settings_vars[key] = var
            if choices:
                box = ttk.Combobox(
                    opts, textvariable=var, values=choices, state="readonly", width=16
                )
            else:
                box = tk.Entry(opts, textvariable=var, width=8, relief="flat")
            self.form_row(opts, i, label, box, label_width=24, pady=6)

        self.out_folder_var = tk.StringVar(value=self.settings.get("default_output_folder", ""))
        FolderRow(
            opts, t("adv.default_out_folder"), self.out_folder_var, width=40, label_width=24
        ).grid(row=len(rows), column=0, columnspan=3, sticky="w", pady=px(6))

        self.auto_open_var = tk.BooleanVar(value=self.settings.get("auto_open_output", False))
        tk.Checkbutton(opts, text=t("adv.auto_open_output"), variable=self.auto_open_var).grid(
            row=len(rows) + 1, column=0, columnspan=3, sticky="w", pady=px(4)
        )

        self.make_render_btn(f, t("advanced_settings.save_settings"), self._save_settings).pack(
            pady=px(15)
        )
        self.save_lbl = tk.Label(f, text="", fg=CLR["green"])
        self.save_lbl.pack()

    def _save_settings(self) -> None:
        changes: dict[str, object] = {key: var.get() for key, var in self._settings_vars.items()}
        changes["default_output_folder"] = self.out_folder_var.get()
        changes["auto_open_output"] = self.auto_open_var.get()
        changes["ffmpeg_path_override"] = self.ffmpeg_path_var.get()
        override = changes["ffmpeg_path_override"].strip()
        if override and not os.path.isfile(override):
            self.save_lbl.config(
                text=t("advanced_settings.ffmpeg_not_found_at_override", override=override),
                fg=CLR["red"],
            )
            return
        self.settings.update(changes)
        if update(changes):
            self.save_lbl.config(text=t("advanced_settings.settings_saved"), fg=CLR["green"])
        else:
            self.save_lbl.config(text=t("advanced_settings.save_failed"), fg=CLR["red"])

    # ─── Networking ───────────────────────────────────────────────────────────
    def _build_networking_tab(self, nb: ttk.Notebook) -> None:
        from core import network as _net

        f = ttk.Frame(nb)
        nb.add(f, text=t("advanced_settings.network"))
        body = scroll_body(f)
        self._net_vars: dict[str, FormVar] = {}
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
        now = section(t("advanced_settings.right_now"))
        self._net_now_lbl = tk.Label(
            now,
            text=t("advanced_settings.checking"),
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
        tk.Button(
            row, text=t("advanced_settings.test_the_connection"), command=self._net_test
        ).pack(side="left")
        tk.Button(
            row, text=t("btn.save"), bg=CLR["green"], fg="white", command=self._net_save
        ).pack(side="left", padx=px(6))
        tk.Button(
            row,
            text=t("advanced_settings.reset_everything"),
            fg=CLR["red"],
            command=self._net_reset_all,
        ).pack(side="left")
        self._net_status_lbl = tk.Label(
            now, text="", anchor="w", justify="left", wraplength=px(760)
        )
        self._net_status_lbl.pack(anchor="w", fill="x", pady=px(4, 0))

        special = sysproxy.special_system()
        if special:
            self._build_tor_system_section(body, special)

    def _net_pick(self, situation: str) -> None:
        self._net_situation_var.set(situation)
        self._net_choose(situation)

    def _net_situations(self, form: _NetForm) -> None:
        from core import network as _net

        section = form.section
        cfg = form.cfg

        # ── Situations ───────────────────────────────────────────────────────
        sit = section(t("advanced_settings.how_should_the_app_connect"))
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

        def app_box(parent: tk.Misc) -> None:
            self._net_app_var = self._net_pick_list(
                parent, "app", self._net_app_labels, current, 44
            )
            tk.Button(parent, text=t("advanced_settings.find"), command=self._net_find_local).pack(
                side="left"
            )

        def tor_box(parent: tk.Misc) -> None:
            self._net_tor_var = self._net_pick_list(
                parent, "tor", self._net_tor_labels, current, 34
            )

        desc = {sid: d for sid, _t, d, _v in _net.SITUATIONS}
        radio = partial(self._net_radio, sit)
        radio(
            "auto", t("advanced_settings.automatic_follow_this_computer_s_settings"), desc["auto"]
        )
        radio("direct", t("advanced_settings.no_proxy_connect_directly"), desc["direct"])
        radio(
            "app",
            t("advanced_settings.a_proxy_app_on_this_computer"),
            t("advanced_settings.clash_v2rayn_shadowsocks_nekoray_hiddify_privoxy"),
            app_box,
        )
        radio(
            "tor",
            t("advanced_settings.tor"),
            t("advanced_settings.everything_goes_through_tor_and_nothing_goes"),
            tor_box,
        )
        radio("custom", t("advanced_settings.my_own_proxy_details_below"))
        radio("offline", t("advanced_settings.offline_never_use_the_network"), desc["offline"])
        self._net_found_lbl = tk.Label(sit, text="", fg=CLR["fgdim"])
        self._net_found_lbl.pack(anchor="w", padx=px(24, 0))
        self._net_found_frame = tk.Frame(sit)
        self._net_found_frame.pack(anchor="w", padx=px(24, 0))

    def _net_radio(
        self,
        sit: tk.LabelFrame,
        value: str,
        text: str,
        desc: str = "",
        extra: Callable[[tk.Frame], object] | None = None,
    ) -> None:
        """One way of connecting: its radio button, any extra controls beside it, and a note."""
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
                r, text=desc, fg=CLR["fgdim"], font=(UI_FONT, 8), wraplength=px(720), justify="left"
            ).pack(anchor="w", padx=px(24, 0))

    def _net_pick_list(
        self, parent: tk.Misc, kind: str, labels: dict[str, str], current: str, width: int
    ) -> tk.StringVar:
        """Which proxy app (or which Tor): the current one, else the first."""
        var = tk.StringVar(
            value=next((t_ for t_, i in labels.items() if i == current), next(iter(labels)))
        )
        cb = ttk.Combobox(
            parent, textvariable=var, values=list(labels), state="readonly", width=width
        )
        cb.pack(side="left", padx=px(6))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._net_pick(kind))
        return var

    def _net_own_proxy(self, form: _NetForm) -> None:
        section = form.section
        hint = form.hint
        var = form.var

        # ── Own proxy ────────────────────────────────────────────────────────
        own = section(t("advanced_settings.your_own_proxy"))
        var("network.mode")
        g = tk.Frame(own)
        g.pack(anchor="w")
        self.form_row(
            g,
            0,
            t("mixer.track_type_col"),
            ttk.Combobox(
                g,
                textvariable=var("network.proxy_scheme"),
                state="readonly",
                width=9,
                values=["http", "https", "socks5h", "socks5", "socks4a", "socks4"],
            ),
            tk.Label(g, text=t("advanced_settings.address")),
            tk.Entry(g, textvariable=var("network.proxy_host"), width=26),
            tk.Label(g, text=t("advanced_settings.port")),
            tk.Entry(g, textvariable=var("network.proxy_port"), width=7),
        )
        self.form_row(
            g,
            1,
            t("advanced_settings.username"),
            tk.Entry(g, textvariable=var("network.proxy_user"), width=18),
            tk.Label(g, text=t("advanced_settings.password")),
            tk.Entry(g, textvariable=var("network.proxy_pass"), width=18, show="•"),
            pady=4,
        )
        tk.Checkbutton(
            own,
            text=t("advanced_settings.strict_never_connect_without_this_proxy_for"),
            variable=var("network.strict"),
        ).pack(anchor="w")
        r = tk.Frame(own)
        r.pack(anchor="w", pady=px(2))
        tk.Label(r, text=t("advanced_settings.when_a_site_blocks_tor_retry_through")).pack(
            side="left"
        )
        tk.Spinbox(r, from_=0, to=50, width=4, textvariable=var("network.tor_retries")).pack(
            side="left", padx=px(4)
        )
        tk.Label(r, text=t("advanced_settings.times_0_off")).pack(side="left")
        hint(
            own,
            t("advanced_settings.proxies_that_sign_you_in_automatically_with"),
        )
        for key in ("network.proxy_scheme", "network.proxy_host", "network.proxy_port"):
            on_write(self._net_vars[key], lambda: self._net_situation_var.set("custom"))

    def _choose_certificate(self, ca: tk.StringVar) -> None:
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
        cert = section(t("advanced_settings.security_certificates"))
        tk.Checkbutton(
            cert,
            text=t("advanced_settings.trust_this_computer_s_certificates_needed_on"),
            variable=var("network.system_certs"),
        ).pack(anchor="w")
        r = tk.Frame(cert)
        r.pack(anchor="w", fill="x", pady=px(2))
        tk.Label(r, text=t("advanced_settings.extra_certificate_file_pem")).pack(side="left")
        ca = text_var("network.extra_ca")
        tk.Entry(r, textvariable=ca, width=44).pack(side="left", padx=px(4))
        tk.Button(
            r,
            text=t("scene_detect.choose"),
            command=partial(self._choose_certificate, ca),
        ).pack(side="left")
        tk.Checkbutton(
            cert,
            text=t("advanced_settings.check_certificates_at_all_turning_this_off"),
            variable=var("network.verify_ssl"),
        ).pack(anchor="w")
        hint(
            cert,
            t("advanced_settings.security_products_such_as_zscaler_netskope_or"),
        )

    def _net_mirrors(self, form: _NetForm) -> None:
        section = form.section
        hint = form.hint
        var = form.var

        # ── Mirrors ──────────────────────────────────────────────────────────
        mir = section(t("advanced_settings.mirrors_when_download_sites_are_blocked"))
        g = tk.Frame(mir)
        g.pack(anchor="w")
        self.form_row(
            g,
            0,
            t("advanced_settings.speech_models_hugging_face_mirror"),
            ttk.Combobox(
                g,
                textvariable=var("network.hf_mirror"),
                width=36,
                values=["", "https://hf-mirror.com"],
            ),
        )
        self.form_row(
            g,
            1,
            t("advanced_settings.github_download_prefix"),
            ttk.Combobox(
                g,
                textvariable=var("network.github_mirror"),
                width=36,
                values=["", "https://ghfast.top/", "https://gh-proxy.com/"],
            ),
        )
        hint(
            mir,
            t("advanced_settings.downloads_are_checked_against_fixed_sha_256"),
        )

    def _net_details(self, form: _NetForm) -> None:
        from core import network as _net

        section = form.section
        hint = form.hint
        var = form.var

        # ── Connection details ───────────────────────────────────────────────
        con = section(t("advanced_settings.connection_details"))
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
            if key == "network.source_address":
                box = ttk.Combobox(g, textvariable=var(key), width=width, values=[""])
                tasks.submit(
                    local_addresses,
                    name="LocalAddresses",  # asks `ip`: not on the UI thread
                    on_done=lambda found, cb=box: cb.config(values=["", *found]),
                )
            elif key == "network.wrapper":
                box = ttk.Combobox(
                    g,
                    textvariable=var(key),
                    width=width,
                    values=["", "mullvad-exclude", "proxychains4 -q", "torsocks"],
                )
            else:
                box = tk.Entry(g, textvariable=var(key), width=width)
            self.form_row(g, i, label, box, pady=2)
        r = tk.Frame(con)
        r.pack(anchor="w", pady=px(2))
        tk.Label(r, text=t("advanced_settings.internet_protocol")).pack(side="left")
        ttk.Combobox(
            r,
            textvariable=var("network.ip_version"),
            values=["auto", "ipv4", "ipv6"],
            state="readonly",
            width=6,
        ).pack(side="left", padx=px(4))
        tk.Checkbutton(
            r,
            text=t("advanced_settings.allow_plain_unencrypted_http"),
            variable=var("network.allow_http"),
        ).pack(side="left", padx=px(10))
        hint(
            con,
            t("advanced_settings.the_local_address_only_helps_where_the"),
        )
        for key in _net.DEFAULTS:
            if key not in self._net_vars:
                var(key)
        self._net_refresh_now()

    def _net_choose(self, kind: str) -> None:
        if kind == "app":
            self._net_apply_preset(self._net_app_labels.get(self._net_app_var.get(), "clash"))
        elif kind == "tor":
            self._net_apply_preset(self._net_tor_labels.get(self._net_tor_var.get(), "tor_browser"))
        else:
            self._net_apply_preset(kind)

    def _net_situation(self, cfg: dict[str, bool | str]) -> str:
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

    def _net_refresh_now(self) -> None:
        from core import network as _net
        from core import sysproxy

        def work() -> tuple[str, str]:
            try:
                now = _net.describe()
                system = sysproxy.system_config(refresh=True).describe()
            except Exception as exc:  # never let a lookup break the settings page
                logger.debug("work: best-effort step failed", exc_info=True)
                now, system = f"Couldn't check: {exc}", ""
            return now, system

        def done(res: tuple[str, str]) -> None:
            try:
                self._net_now_lbl.config(text=res[0])
                self._net_sys_lbl.config(
                    text="This computer's own setting: " + res[1] if res[1] else ""
                )
            except tk.TclError:
                pass

        tasks.submit(work, on_done=done, on_error=lambda e: None, name="NetDescribe")

    def _net_find_local(self) -> None:
        from core import network as _net

        self._net_found_lbl.config(text=t("advanced_settings.looking"))
        for w in self._net_found_frame.winfo_children():
            w.destroy()

        def done(found: list[tuple[int, str, str]]) -> None:
            if not found:
                self._net_found_lbl.config(
                    text=t("advanced_settings.no_proxy_apps_found_on_the_usual")
                )
                return
            self._net_found_lbl.config(text=t("advanced_settings.found_found", found=len(found)))
            for port, scheme, label in found:
                tk.Button(
                    self._net_found_frame,
                    text=t(
                        "advanced_settings.use_label_scheme_127_0_0_1",
                        label=label,
                        scheme=scheme,
                        port=port,
                    ),
                    relief="flat",
                    command=partial(self._net_use_local, scheme, port),
                ).pack(anchor="w")

        tasks.submit(
            _net.find_local_proxies,
            on_done=done,
            on_error=lambda e: self._net_found_lbl.config(text=str(e)),
            name="ProxyProbe",
        )

    def _net_use_local(self, scheme: str, port: int) -> None:
        _set_form(self._net_vars["network.mode"], "manual")
        _set_form(self._net_vars["network.proxy_enabled"], True)
        _set_form(self._net_vars["network.proxy_scheme"], scheme)
        _set_form(self._net_vars["network.proxy_host"], "127.0.0.1")
        _set_form(self._net_vars["network.proxy_port"], str(port))
        self._net_situation_var.set("custom")
        self._net_status_lbl.config(
            text=t("advanced_settings.filled_in_click_test_then_save"), fg=CLR["fgdim"]
        )

    def _build_tor_system_section(self, body: tk.Frame, special: str) -> None:
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
                text=t("advanced_settings.whonix_s_gateway_already_sends_all_traffic"),
                wraplength=px(760),
                justify="left",
            ).pack(anchor="w")
            return
        tk.Label(
            lf,
            text=t("advanced_settings.tails_sends_everything_through_tor_and_some"),
            wraplength=px(760),
            justify="left",
        ).pack(anchor="w")
        self._tails_lbl = tk.Label(lf, text="", fg=CLR["fgdim"], wraplength=px(760), justify="left")
        self._tails_lbl.pack(anchor="w", pady=px(4))
        r = tk.Frame(lf)
        r.pack(anchor="w")
        self._tails_on_btn = tk.Button(
            r,
            text=t("advanced_settings.allow_direct_downloads_not_anonymous"),
            fg=CLR["red"],
            command=self._tails_enable,
        )
        self._tails_on_btn.pack(side="left")
        tk.Button(r, text=t("advanced_settings.turn_off"), command=self._tails_disable).pack(
            side="left", padx=px(6)
        )
        self._tails_refresh()

    def _tails_refresh(self) -> None:
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

    def _tails_enable(self) -> None:
        from core import tails

        issues = tails.problems()
        if issues:
            messagebox.showinfo(t("advanced_settings.tails"), "\n\n".join(issues), parent=self)
            return
        if not messagebox.askyesno(
            t("advanced_settings.not_anonymous"),
            t("advanced_settings.downloads_will_connect_directly_not_through_tor"),
            icon="warning",
            default="no",
            parent=self,
        ):
            return
        dlg = tk.Toplevel(self)
        dlg.title("Administration password")
        dlg.transient(self.winfo_toplevel())
        tk.Label(
            dlg, text=t("advanced_settings.tails_administration_password_set_at_the_welcome")
        ).pack(padx=px(14), pady=px(12, 4))
        pw = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=pw, show="•", width=30)
        entry.pack(padx=px(14))
        remember = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dlg, text=t("advanced_settings.remember_it_until_i_close_the_app"), variable=remember
        ).pack(padx=px(14), pady=px(4))
        msg = tk.Label(dlg, text="", fg=CLR["red"])
        msg.pack()

        def ok(_e: object = None) -> None:
            done, text = tails.enable(pw.get(), remember.get())
            pw.set("")
            if done:
                dlg.destroy()
                self._tails_refresh()
                messagebox.showinfo(t("advanced_settings.tails"), text, parent=self)
            else:
                msg.config(text=text)

        bf = tk.Frame(dlg)
        bf.pack(pady=px(10))
        tk.Button(bf, text=t("advanced_settings.turn_on"), command=ok).pack(side="left", padx=px(4))

        def cancel() -> None:
            pw.set("")
            dlg.destroy()

        tk.Button(bf, text=t("btn.cancel"), command=cancel).pack(side="left", padx=px(4))
        entry.bind("<Return>", ok)
        entry.focus_set()
        dlg.grab_set()

    def _tails_disable(self) -> None:
        from core import tails

        tails.disable()
        self._tails_refresh()

    def _net_collect(self) -> dict[str, object]:
        """Read every form variable into a settings-ready dict."""
        from core import network as _net

        out: dict[str, object] = {}
        for key, var in self._net_vars.items():
            default = _net.DEFAULTS[key]
            v = var.get()
            if isinstance(default, bool):
                out[key] = bool(v)
            else:
                out[key] = v
        return out

    def _net_save(self) -> None:
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

    def _net_test(self) -> None:
        from core import network as _net

        # Test against the *form* values, not the saved ones, so the user
        # can validate before clicking Save.
        cfg = dict(_net.DEFAULTS)
        cfg.update(self._net_collect())
        self._net_status_lbl.config(text=t("advanced_settings.testing"), fg=CLR["fgdim"])
        self.update_idletasks()

        def _work() -> None:
            ok, msg = _net.test(cfg=cfg)
            colour = CLR["green"] if ok else CLR["red"]
            text = ("Connection OK: " + msg) if ok else ("Connection failed: " + msg)
            self.after(0, partial(self._net_status_lbl.config, text=text, fg=colour))

        tasks.submit(_work)

    def _net_reset_field(self, key: str) -> None:
        from core import network as _net

        if key not in _net.DEFAULTS:
            return
        default = _net.DEFAULTS[key]
        var = self._net_vars.get(key)
        if var is None:
            return
        _set_form(var, default)
        self._net_status_lbl.config(
            text=t("advanced_settings.key2_reset_not_saved_yet", key2=key), fg=CLR["fgdim"]
        )

    def _net_reset_all(self) -> None:
        from core import network as _net

        if not messagebox.askyesno(
            t("advanced_settings.reset_networking"),
            t("advanced_settings.reset_every_networking_setting_to_its_default"),
        ):
            return
        _net.reset_all()
        # Refresh form variables from defaults.
        for key, default in _net.DEFAULTS.items():
            var = self._net_vars.get(key)
            if var is None:
                continue
            _set_form(var, default)
        self._net_status_lbl.config(
            text=t("advanced_settings.all_networking_settings_reset_to_defaults_and"),
            fg=CLR["green"],
        )

    def _net_apply_preset(self, preset_id: str) -> None:
        from core import network as _net

        if preset_id == "custom":
            _set_form(self._net_vars["network.mode"], "manual")
            _set_form(self._net_vars["network.proxy_enabled"], True)
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
            _set_form(var, value)
        self._net_status_lbl.config(
            text=t("advanced_settings.chosen_click_save_or_test_first"), fg=CLR["fgdim"]
        )

    # ─── Skins ────────────────────────────────────────────────────────────────
    def _build_skins_tab(self, nb: ttk.Notebook) -> None:
        outer = ttk.Frame(nb)
        nb.add(outer, text=t("advanced_settings.skins"))

        # Inner notebook: Themes | Custom
        inner_nb = ttk.Notebook(outer)
        inner_nb.pack(fill="both", expand=True, padx=px(8), pady=px(8))

        self._build_themes_subtab(inner_nb)
        self._build_custom_subtab(inner_nb)

    # ── Themes sub-tab ────────────────────────────────────────────────────────
    def _build_themes_subtab(self, inner_nb: ttk.Notebook) -> None:
        current_skin, f = self._build_skin_cards(inner_nb)
        self._build_skin_apply(f)
        self._build_star_speed(f)
        self._build_star_density(current_skin)

    def _build_skin_cards(self, inner_nb: ttk.Notebook) -> tuple[str, ttk.Frame]:
        f = ttk.Frame(inner_nb)
        inner_nb.add(f, text=t("advanced_settings.themes"))

        tk.Label(f, text=t("adv.skins_title"), font=(UI_FONT, 12, "bold")).pack(pady=px(16, 4))
        tk.Label(f, text=t("adv.skins_subtitle"), fg=CLR["fgdim"]).pack()

        current_skin = load_skin_name()
        self._skin_var = tk.StringVar(value=current_skin)

        # Scrollable card grid
        holder = tk.Frame(f)
        holder.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        grid_host = scroll_body(holder)

        # Theme cards (exclude "Custom" - it lives in the Custom tab)
        theme_names = [n for n in SKINS if n != "Custom"]
        COLS = 3
        self._skin_previews: dict[str, tk.Canvas] = {}

        for idx, name in enumerate(theme_names):
            self._skin_card(grid_host, idx, name, COLS)
        return current_skin, f

    def _skin_card(self, grid_host: tk.Frame, idx: int, name: str, cols: int) -> None:
        """One theme: its preview, name, description and Select button, in skin colours."""
        skin = SKINS[name]
        row, col = divmod(idx, cols)

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

        self._skin_previews[name] = self._skin_preview(card, name, skin)

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

    @staticmethod
    def _skin_preview(card: tk.Frame, name: str, skin: Skin) -> tk.Canvas:
        """A miniature of the window in this skin: sidebar, buttons, title bar (and stars)."""
        c = tk.Canvas(card, width=px(180), height=px(80), bg=skin["sb_bg"], highlightthickness=0)
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
        return c

    def _build_skin_apply(self, f: ttk.Frame) -> None:
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
            text=(t("advanced_settings.mystical_includes_animated_falling_star_particle")),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(pady=px(2, 6))

    def _build_star_speed(self, f: ttk.Frame) -> None:
        """Star speed control (only visible when Mystical is active)."""
        self._star_speed_frame = tk.LabelFrame(
            f, text=t("advanced_settings.star_speed"), padx=px(16), pady=px(10)
        )

        speed_row = tk.Frame(self._star_speed_frame)
        speed_row.pack(fill="x")
        tk.Label(speed_row, text=t("adv.star_slow"), font=(UI_FONT, 8), fg=CLR["fgdim"]).pack(
            side="left"
        )

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
        tk.Label(speed_row, text=t("adv.star_fast"), font=(UI_FONT, 8), fg=CLR["fgdim"]).pack(
            side="left"
        )

        self._star_speed_lbl = tk.Label(
            self._star_speed_frame,
            text=self._speed_label(saved_speed),
            font=(UI_FONT, 8),
            fg=CLR["fgdim"],
        )
        self._star_speed_lbl.pack()

    def _build_star_density(self, current_skin: str) -> None:
        """Star density (count)."""
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
        tk.Label(density_row, text=t("adv.star_many"), font=(UI_FONT, 8), fg=CLR["fgdim"]).pack(
            side="left"
        )

        self._star_count_lbl = tk.Label(
            self._star_speed_frame,
            text=t("advanced_settings.saved_count_stars", saved_count=saved_count),
            font=(UI_FONT, 8),
            fg=CLR["fgdim"],
        )
        self._star_count_lbl.pack()

        self._update_star_speed_visibility(current_skin)

    # ── Custom skin sub-tab ───────────────────────────────────────────────────
    def _build_custom_subtab(self, inner_nb: ttk.Notebook) -> None:
        """Scrollable color-property editor for the 'Custom' skin."""
        current_custom, inner, sections = self._build_custom_header(inner_nb)
        self._build_custom_colour_rows(current_custom, inner, sections)
        self._build_custom_reset(inner)

    def _build_custom_header(
        self, inner_nb: ttk.Notebook
    ) -> tuple[Skin, tk.Frame, list[tuple[str, list[tuple[str, ColourKey]]]]]:
        f = ttk.Frame(inner_nb)
        inner_nb.add(f, text=t("advanced_settings.custom"))

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

        inner = scroll_body(wrapper)

        # Load current custom colors
        current_custom = get_skin("Custom")
        self._custom_vars: dict[str, tk.StringVar] = {}
        self._custom_swatches: dict[str, ColourButton] = {}

        sections = _custom_sections()
        return current_custom, inner, sections

    def _build_custom_colour_rows(
        self,
        current_custom: Skin,
        inner: tk.Frame,
        sections: list[tuple[str, list[tuple[str, ColourKey]]]],
    ) -> None:
        for section_title, props in sections:
            sec_frame = tk.LabelFrame(inner, text=f"  {section_title}  ", padx=px(12), pady=px(10))
            sec_frame.pack(fill="x", padx=px(8), pady=px(8, 4))
            for label_text, key in props:
                initial = current_custom.get(key) or CUSTOM_DEFAULTS.get(key) or "#222222"
                self._custom_colour_row(sec_frame, key, label_text, initial)

    def _custom_colour_row(self, parent: tk.Misc, key: str, label_text: str, initial: str) -> None:
        """One colour of the custom skin: name, swatch (click to pick), hex field."""
        row = tk.Frame(parent)
        row.pack(fill="x", pady=px(3))
        var = tk.StringVar(value=initial)
        self._custom_vars[key] = var
        tk.Label(row, text=label_text, width=26, anchor="w", font=(UI_FONT, 9)).pack(side="left")
        swatch = ColourButton(
            row,
            initial,
            text=t("advanced_settings.pick"),
            on_change=partial(self._custom_picked, key),
        )
        swatch.pack(side="left", padx=px(4, 2))
        self._custom_swatches[key] = swatch
        entry = tk.Entry(row, textvariable=var, width=9, font=(MONO_FONT, 9))
        entry.pack(side="left", padx=px(2))
        entry.bind("<FocusOut>", partial(self._custom_typed, key))
        entry.bind("<Return>", partial(self._custom_typed, key))

    def _custom_picked(self, key: str, colour: str) -> None:
        self._custom_vars[key].set(colour)
        self._save_custom(key, colour)

    def _custom_typed(self, key: str, _event: object = None) -> None:
        """A hex colour typed in the field (ignored until it is one)."""
        try:
            colour = normal_colour(self._custom_vars[key].get())
        except ValueError:
            return
        self._custom_swatches[key].set(colour)
        self._save_custom(key, colour)

    def _save_custom(self, key: str, colour: str) -> None:
        save_custom_color(key, colour)
        self._custom_status.config(text=t("advanced_settings.saved"), fg=CLR["green"])

    def _build_custom_reset(self, inner: tk.Frame) -> None:
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

    def _apply_custom_skin(self) -> None:
        """Select 'Custom' and apply it live."""
        self._skin_var.set("Custom")
        self._on_skin_select("Custom")

    def _reset_custom_defaults(self) -> None:
        """Reset all custom color vars to CUSTOM_DEFAULTS."""
        prefix = "custom_color_"
        changes: dict[str, object] = {}
        for key, val in CUSTOM_DEFAULTS.items():
            if isinstance(val, str) and val.startswith("#"):
                self._custom_vars[key].set(val)
                if key in self._custom_swatches:
                    self._custom_swatches[key].set(val)
                changes[f"{prefix}{key}"] = val
        update(changes)
        try:
            self._custom_status.config(
                text=t("advanced_settings.reset_to_defaults_2"), fg=CLR["green"]
            )
        except Exception:
            logger.debug("_reset_custom_defaults: best-effort step failed", exc_info=True)

    def _on_skin_select(self, name: str) -> None:
        """Save and apply the selected skin live, immediately."""
        save_skin_name(name)
        self._update_star_speed_visibility(name)
        shell = self.shell()
        if shell is not None:
            shell.apply_theme(name)
            self._skin_status.config(
                text=t("advanced_settings.name_applied_live", name=name), fg=CLR["accent"]
            )
        else:
            self._skin_status.config(
                text=t("advanced_settings.saved_reopen_to_see_full_effect"), fg=CLR["green"]
            )

    def _apply_skin(self) -> None:
        self._on_skin_select(self._skin_var.get())

    def _update_star_speed_visibility(self, skin_name: str) -> None:
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

    def _on_star_speed_change(self, value: str) -> None:
        mult = float(value)
        set_star_speed(mult)
        try:
            self._star_speed_lbl.config(text=self._speed_label(mult))
        except Exception:
            logger.debug("_on_star_speed_change: best-effort step failed", exc_info=True)
        settings.STAR_SPEED.set(float(mult))

    def _on_star_count_change(self, value: str) -> None:
        count = int(float(value))
        set_star_count(count)
        with contextlib.suppress(tk.TclError):
            self._star_count_lbl.config(text=t("advanced_settings.count_stars", count=count))
        settings.STAR_COUNT.set(count)
        # restart the stars so the new density shows at once
        shell = self.shell()
        if shell is not None and get_skin(load_skin_name()).get("stars"):
            shell.apply_theme(load_skin_name())

    # ─── Language ─────────────────────────────────────────────────────────────
    def _build_language_tab(self, nb: ttk.Notebook) -> None:
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
        def _save_language() -> None:
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
    def _build_system_tab(self, nb: ttk.Notebook) -> None:
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
    def _build_dev_tab(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb)
        nb.add(f, text=t("advanced_settings.developer"))

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

    def _dev_print(self, text: str) -> None:
        self.dev_console.insert(tk.END, text)
        self.dev_console.see(tk.END)

    def _run_dev_cmd(self) -> None:
        cmd_str = self.dev_cmd_var.get().strip()
        if not cmd_str:
            return
        self.dev_console.insert(tk.END, f"$ {cmd_str}\n")
        self.dev_console.see(tk.END)

        def _work() -> None:
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
