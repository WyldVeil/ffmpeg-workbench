"""
tab_settings.py  ─  Quick Settings

A stripped-down panel for quick access to the most commonly adjusted
preferences without navigating to Advanced Settings.
Reads and writes the same settings.json as AdvancedSettingsTab via the
centralised core.settings module (no duplicated load/save logic).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core import settings
from core.i18n import t
from core.scaling import px
from core.settings import StartPage, load_settings, update
from tabs.base_tab import CLR, UI_FONT, BaseTab, make_header
from tabs.kit.widgets import ChoiceBox
from tabs.widgets import FolderRow

START_CHOICES: dict[StartPage, str] = {
    StartPage.START: "Start - What do you want to do?",
    StartPage.HOME: "Home - all tools",
    StartPage.LAST: "The last tool I used",
}


class SettingsTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.settings = load_settings()
        self._build_ui()

    def _build_ui(self) -> None:
        make_header(self, t("tab.settings"), t("settings.subtitle"), icon="⚙")

        lf = tk.LabelFrame(
            self, text=f"  {t('settings.encoding_defaults_section')}  ", padx=px(20), pady=px(14)
        )
        lf.pack(fill="x", padx=px(24), pady=px(14))

        rows = [
            (t("settings.default_crf_label"), "default_crf", "18", None),
            (
                t("settings.default_preset_label"),
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
                t("settings.default_audio_label"),
                "default_audio_bitrate",
                "192k",
                ["96k", "128k", "192k", "256k", "320k"],
            ),
        ]

        self._vars: dict[str, tk.StringVar] = {}
        for i, (label, key, default, choices) in enumerate(rows):
            var = tk.StringVar(value=self.settings.get(key, default))
            self._vars[key] = var
            if choices:
                box = ttk.Combobox(lf, textvariable=var, values=choices, state="readonly", width=16)
            else:
                box = tk.Entry(lf, textvariable=var, width=8, relief="flat")
            self.form_row(lf, i, label, box, label_width=24, pady=8)

        # Output folder
        self._out_var = tk.StringVar(value=self.settings.get("default_output_folder", ""))
        FolderRow(
            lf, t("settings.output_folder_label"), self._out_var, width=44, label_width=24
        ).grid(row=len(rows), column=0, columnspan=3, sticky="w", pady=px(8))

        # Auto-open
        self._auto_open = tk.BooleanVar(value=self.settings.get("auto_open_output", False))
        tk.Checkbutton(
            lf,
            text=t("settings.auto_open_checkbox"),
            variable=self._auto_open,
        ).grid(row=len(rows) + 1, column=0, columnspan=3, sticky="w", pady=px(4))

        # Startup page
        self.start_box = ChoiceBox(lf, START_CHOICES, settings.START_PAGE.get())
        self.form_row(
            lf, len(rows) + 2, "Start the app on:", self.start_box, label_width=24, pady=8
        )

        # Save button
        btn_f = tk.Frame(self)
        btn_f.pack(pady=px(16))
        self.make_render_btn(btn_f, t("settings.save_settings_button"), self._save).pack(
            side="left", padx=px(8)
        )
        self._status = tk.Label(btn_f, text="", fg=CLR["green"])
        self._status.pack(side="left", padx=px(8))

        tk.Label(
            self,
            text=t("settings.subtitle"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 9),
        ).pack(pady=px(8))

    def _save(self) -> None:
        # Only the values on this page: settings changed elsewhere since it was
        # opened (favourites, language, network...) must not be reverted.
        changes: dict[str, object] = {key: var.get() for key, var in self._vars.items()}
        changes["default_output_folder"] = self._out_var.get()
        changes["auto_open_output"] = self._auto_open.get()
        changes["start_page"] = self.start_box.value.value
        self.settings.update(changes)
        if update(changes):
            self._status.config(text=f"✅  {t('settings.saved_confirmation')}", fg=CLR["green"])
        else:
            self._status.config(text=f"❌  {t('settings.save_failed')}", fg=CLR["red"])
