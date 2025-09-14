# ashyterm/ui/dialogs/preferences_dialog.py

from typing import Dict, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GObject, Gtk

from ...helpers import accelerator_to_label
from ...settings.manager import SettingsManager
from ...utils.logger import get_logger
from ...utils.translation_utils import _
from ..color_scheme_dialog import ColorSchemeDialog


class PreferencesDialog(Adw.PreferencesWindow):
    """Enhanced preferences dialog with comprehensive settings management."""

    __gsignals__ = {
        "transparency-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "font-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "setting-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    def __init__(self, parent_window, settings_manager: SettingsManager):
        super().__init__(
            title=_("Preferences"),
            transient_for=parent_window,
            modal=False,
            hide_on_close=True,
            default_width=900,
            default_height=680,
            search_enabled=True,
        )
        self.logger = get_logger("ashyterm.ui.dialogs.preferences")
        self.settings_manager = settings_manager
        self._setup_appearance_page()
        self._setup_terminal_page()
        self._setup_profiles_page()
        self._setup_advanced_page()
        self.logger.info("Preferences dialog initialized")

    def _setup_appearance_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Appearance"), icon_name="preferences-desktop-display-symbolic"
        )
        self.add(page)

        palette_group = Adw.PreferencesGroup(title=_("Color Scheme"))
        page.add(palette_group)

        self.color_scheme_row = Adw.ActionRow(title=_("Current Scheme"))
        self._update_color_scheme_row_subtitle()

        manage_button = Gtk.Button(label=_("Manage Schemes..."))
        manage_button.set_valign(Gtk.Align.CENTER)
        manage_button.connect("clicked", self._on_manage_schemes_clicked)
        self.color_scheme_row.add_suffix(manage_button)
        self.color_scheme_row.set_activatable_widget(manage_button)
        palette_group.add(self.color_scheme_row)

        font_group = Adw.PreferencesGroup(
            title=_("Typography"),
            description=_("Configure fonts and spacing"),
        )
        page.add(font_group)

        font_row = Adw.ActionRow(
            title=_("Terminal Font"),
            subtitle=_("Select font family and size for terminal text"),
        )
        font_button = Gtk.FontButton()
        font_button.set_valign(Gtk.Align.CENTER)
        font_button.set_font(self.settings_manager.get("font", "Monospace 10"))
        font_button.connect("font-set", self._on_font_changed)
        font_row.add_suffix(font_button)
        font_row.set_activatable_widget(font_button)
        font_group.add(font_row)

        line_spacing_row = Adw.ActionRow(
            title=_("Line Spacing"),
            subtitle=_("Adjust the vertical space between lines"),
        )
        spacing_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.spacing_spin = Gtk.SpinButton.new_with_range(0.8, 2.0, 0.05)
        self.spacing_spin.set_valign(Gtk.Align.CENTER)
        self.spacing_spin.set_value(self.settings_manager.get("line_spacing", 1.0))
        self.spacing_spin.connect("value-changed", self._on_line_spacing_changed)
        spacing_box.append(self.spacing_spin)
        line_spacing_row.add_suffix(spacing_box)
        line_spacing_row.set_activatable_widget(self.spacing_spin)
        font_group.add(line_spacing_row)

        misc_group = Adw.PreferencesGroup(title=_("Miscellaneous"))
        page.add(misc_group)

        transparency_row = Adw.ActionRow(
            title=_("Background Transparency"),
            subtitle=_("Adjust terminal background transparency"),
        )
        self.transparency_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self.transparency_scale.set_value(self.settings_manager.get("transparency", 0))
        self.transparency_scale.set_draw_value(True)
        self.transparency_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.transparency_scale.set_hexpand(True)
        self.transparency_scale.connect("value-changed", self._on_transparency_changed)
        transparency_row.add_suffix(self.transparency_scale)
        transparency_row.set_activatable_widget(self.transparency_scale)
        misc_group.add(transparency_row)

        bold_bright_row = Adw.SwitchRow(
            title=_("Use Bright Colors for Bold Text"),
            subtitle=_("Render bold text with the brighter version of the base color"),
        )
        bold_bright_row.set_active(self.settings_manager.get("bold_is_bright", True))
        bold_bright_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("bold_is_bright", r.get_active()),
        )
        misc_group.add(bold_bright_row)

        auto_hide_sidebar_row = Adw.SwitchRow(
            title=_("Auto-Hide Sidebar"),
            subtitle=_(
                "Automatically hide the sidebar when activating sessions or layouts"
            ),
        )
        auto_hide_sidebar_row.set_active(
            self.settings_manager.get("auto_hide_sidebar", False)
        )
        auto_hide_sidebar_row.connect(
            "notify::active",
            lambda r, _: self._on_auto_hide_sidebar_changed(r.get_active()),
        )
        misc_group.add(auto_hide_sidebar_row)

        text_blink_row = Adw.ComboRow(
            title=_("Blinking Text"),
            subtitle=_("Control how the terminal handles blinking text"),
        )
        text_blink_row.set_model(Gtk.StringList.new([_("When focused"), _("Always")]))
        text_blink_row.set_selected(self.settings_manager.get("text_blink_mode", 0))
        text_blink_row.connect("notify::selected", self._on_text_blink_mode_changed)
        misc_group.add(text_blink_row)

    def _setup_terminal_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Terminal"), icon_name="utilities-terminal-symbolic"
        )
        self.add(page)

        cursor_group = Adw.PreferencesGroup(title=_("Cursor"))
        page.add(cursor_group)

        cursor_shape_row = Adw.ComboRow(
            title=_("Cursor Shape"),
            subtitle=_("Select the shape of the terminal cursor"),
        )
        cursor_shape_row.set_model(
            Gtk.StringList.new([_("Block"), _("I-Beam"), _("Underline")])
        )
        cursor_shape_row.set_selected(self.settings_manager.get("cursor_shape", 0))
        cursor_shape_row.connect("notify::selected", self._on_cursor_shape_changed)
        cursor_group.add(cursor_shape_row)

        cursor_blink_row = Adw.ComboRow(
            title=_("Cursor Blinking"), subtitle=_("Control cursor blinking behavior")
        )
        cursor_blink_row.set_model(
            Gtk.StringList.new([_("Follow System"), _("On"), _("Off")])
        )
        cursor_blink_row.set_selected(self.settings_manager.get("cursor_blink", 0))
        cursor_blink_row.connect("notify::selected", self._on_cursor_blink_changed)
        cursor_group.add(cursor_blink_row)

        scrolling_group = Adw.PreferencesGroup(title=_("Scrolling"))
        page.add(scrolling_group)

        scrollback_row = Adw.ActionRow(
            title=_("Scrollback Lines"),
            subtitle=_("Number of lines to keep in history (0 for unlimited)"),
        )
        scrollback_spin = Gtk.SpinButton.new_with_range(0, 1000000, 1000)
        scrollback_spin.set_valign(Gtk.Align.CENTER)
        scrollback_spin.set_value(self.settings_manager.get("scrollback_lines", 10000))
        scrollback_spin.connect("value-changed", self._on_scrollback_changed)
        scrollback_row.add_suffix(scrollback_spin)
        scrollback_row.set_activatable_widget(scrollback_spin)
        scrolling_group.add(scrollback_row)

        mouse_scroll_row = Adw.ActionRow(
            title=_("Mouse Scroll Sensitivity"),
            subtitle=_("Controls the scroll speed for a mouse wheel. Lower is slower."),
        )
        mouse_scroll_spin = Gtk.SpinButton.new_with_range(1, 500, 1)
        mouse_scroll_spin.set_valign(Gtk.Align.CENTER)
        mouse_scroll_spin.set_value(
            self.settings_manager.get("mouse_scroll_sensitivity", 30.0)
        )
        mouse_scroll_spin.connect(
            "value-changed", self._on_mouse_scroll_sensitivity_changed
        )
        mouse_scroll_row.add_suffix(mouse_scroll_spin)
        mouse_scroll_row.set_activatable_widget(mouse_scroll_spin)
        scrolling_group.add(mouse_scroll_row)

        touchpad_scroll_row = Adw.ActionRow(
            title=_("Touchpad Scroll Sensitivity"),
            subtitle=_("Controls the scroll speed for a touchpad. Lower is slower."),
        )
        touchpad_scroll_spin = Gtk.SpinButton.new_with_range(1, 500, 1)
        touchpad_scroll_spin.set_valign(Gtk.Align.CENTER)
        touchpad_scroll_spin.set_value(
            self.settings_manager.get("touchpad_scroll_sensitivity", 30.0)
        )
        touchpad_scroll_spin.connect(
            "value-changed", self._on_touchpad_scroll_sensitivity_changed
        )
        touchpad_scroll_row.add_suffix(touchpad_scroll_spin)
        touchpad_scroll_row.set_activatable_widget(touchpad_scroll_spin)
        scrolling_group.add(touchpad_scroll_row)

        scroll_on_insert_row = Adw.SwitchRow(
            title=_("Scroll on Paste"),
            subtitle=_("Automatically scroll to the bottom when pasting text"),
        )
        scroll_on_insert_row.set_active(
            self.settings_manager.get("scroll_on_insert", True)
        )
        scroll_on_insert_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("scroll_on_insert", r.get_active()),
        )
        scrolling_group.add(scroll_on_insert_row)

        selection_group = Adw.PreferencesGroup(
            title=_("Selection"),
            description=_("Extra characters for word selection (e.g., -_.:/~)"),
        )
        page.add(selection_group)

        word_chars_row = Adw.EntryRow(
            title=_("Word Characters"),
        )
        word_chars_row.set_text(
            self.settings_manager.get("word_char_exceptions", "-_.:/~")
        )
        word_chars_row.connect("changed", self._on_word_chars_changed)
        selection_group.add(word_chars_row)

        shell_group = Adw.PreferencesGroup(title=_("Shell &amp; Bell"))
        page.add(shell_group)

        login_shell_row = Adw.SwitchRow(
            title=_("Run Command as a Login Shell"),
            subtitle=_("Sources /etc/profile and ~/.profile on startup"),
        )
        login_shell_row.set_active(self.settings_manager.get("use_login_shell", False))
        login_shell_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("use_login_shell", r.get_active()),
        )
        shell_group.add(login_shell_row)

        bell_row = Adw.SwitchRow(
            title=_("Audible Bell"),
            subtitle=_("Emit a sound for the terminal bell character"),
        )
        bell_row.set_active(self.settings_manager.get("bell_sound", False))
        bell_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("bell_sound", r.get_active()),
        )
        shell_group.add(bell_row)

    def _setup_profiles_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Profiles & Data"), icon_name="folder-saved-search-symbolic"
        )
        self.add(page)

        startup_group = Adw.PreferencesGroup(title=_("Startup"))
        page.add(startup_group)

        restore_policy_row = Adw.ComboRow(
            title=_("On Startup"),
            subtitle=_("Action to take when the application starts"),
        )
        policy_map = ["always", "ask", "never"]
        policy_strings = [
            _("Always restore previous session"),
            _("Ask to restore previous session"),
            _("Never restore previous session"),
        ]
        restore_policy_row.set_model(Gtk.StringList.new(policy_strings))
        current_policy = self.settings_manager.get("session_restore_policy", "never")
        try:
            selected_index = policy_map.index(current_policy)
        except ValueError:
            selected_index = 2
        restore_policy_row.set_selected(selected_index)
        restore_policy_row.connect(
            "notify::selected", self._on_restore_policy_changed, policy_map
        )
        startup_group.add(restore_policy_row)

        backup_group = Adw.PreferencesGroup(
            title=_("Backup &amp; Recovery"),
            description=_(
                "Create an encrypted backup of your data or restore from a previous backup."
            ),
        )
        page.add(backup_group)

        backup_now_row = Adw.ActionRow(
            title=_("Create Backup"),
            subtitle=_(
                "Save all sessions, settings, and passwords to an encrypted file."
            ),
        )
        backup_now_button = Gtk.Button(label=_("Create Backup..."))
        backup_now_button.set_valign(Gtk.Align.CENTER)
        backup_now_button.connect("clicked", self._on_backup_now_clicked)
        backup_now_row.add_suffix(backup_now_button)
        backup_now_row.set_activatable_widget(backup_now_button)
        backup_group.add(backup_now_row)

        restore_row = Adw.ActionRow(
            title=_("Restore from Backup"),
            subtitle=_("Replace all current data with a backup file."),
        )
        restore_button = Gtk.Button(label=_("Restore..."))
        restore_button.set_valign(Gtk.Align.CENTER)
        restore_button.connect("clicked", self._on_restore_backup_clicked)
        restore_row.add_suffix(restore_button)
        restore_row.set_activatable_widget(restore_button)
        backup_group.add(restore_row)

        remote_edit_group = Adw.PreferencesGroup(title=_("Remote Editing"))
        page.add(remote_edit_group)

        use_tmp_dir_row = Adw.SwitchRow(
            title=_("Use System Temporary Directory"),
            subtitle=_(
                "Store temporary files for remote editing in /tmp instead of the config folder"
            ),
        )
        use_tmp_dir_row.set_active(
            self.settings_manager.get("use_system_tmp_for_edit", False)
        )
        use_tmp_dir_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed(
                "use_system_tmp_for_edit", r.get_active()
            ),
        )
        remote_edit_group.add(use_tmp_dir_row)

        clear_on_exit_row = Adw.SwitchRow(
            title=_("Clear Remote Edit Files on Exit"),
            subtitle=_(
                "Automatically delete all temporary remote files when closing the app"
            ),
        )
        clear_on_exit_row.set_active(
            self.settings_manager.get("clear_remote_edit_files_on_exit", False)
        )
        clear_on_exit_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed(
                "clear_remote_edit_files_on_exit", r.get_active()
            ),
        )
        remote_edit_group.add(clear_on_exit_row)

    def _setup_advanced_page(self) -> None:
        advanced_page = Adw.PreferencesPage(
            title=_("Advanced"), icon_name="preferences-other-symbolic"
        )
        self.add(advanced_page)

        features_group = Adw.PreferencesGroup(
            title=_("Advanced Features"),
            description=_("Enable or disable advanced terminal features"),
        )
        advanced_page.add(features_group)

        bidi_row = Adw.SwitchRow(
            title=_("Bidirectional Text Support"),
            subtitle=_(
                "Enable for languages like Arabic and Hebrew (may affect performance)"
            ),
        )
        bidi_row.set_active(self.settings_manager.get("bidi_enabled", False))
        bidi_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("bidi_enabled", r.get_active()),
        )
        features_group.add(bidi_row)

        shaping_row = Adw.SwitchRow(
            title=_("Enable Arabic Text Shaping"),
            subtitle=_(
                "Correctly render ligatures and contextual forms for Arabic script"
            ),
        )
        shaping_row.set_active(self.settings_manager.get("enable_shaping", False))
        shaping_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("enable_shaping", r.get_active()),
        )
        features_group.add(shaping_row)

        sixel_row = Adw.SwitchRow(
            title=_("SIXEL Graphics Support"),
            subtitle=_("Allow the terminal to display SIXEL images (experimental)"),
        )
        sixel_row.set_active(self.settings_manager.get("sixel_enabled", True))
        sixel_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("sixel_enabled", r.get_active()),
        )
        features_group.add(sixel_row)

        compatibility_group = Adw.PreferencesGroup(
            title=_("Compatibility"),
            description=_("Settings for compatibility with older systems and tools"),
        )
        advanced_page.add(compatibility_group)

        backspace_row = Adw.ComboRow(
            title=_("Backspace Key"), subtitle=_("Sequence to send for Backspace key")
        )
        backspace_row.set_model(
            Gtk.StringList.new([
                _("Automatic"),
                _("ASCII BACKSPACE (^H)"),
                _("ASCII DELETE"),
                _("Escape Sequence"),
            ])
        )
        backspace_row.set_selected(self.settings_manager.get("backspace_binding", 0))
        backspace_row.connect("notify::selected", self._on_backspace_binding_changed)
        compatibility_group.add(backspace_row)

        delete_row = Adw.ComboRow(
            title=_("Delete Key"), subtitle=_("Sequence to send for Delete key")
        )
        delete_row.set_model(
            Gtk.StringList.new([
                _("Automatic"),
                _("ASCII DELETE"),
                _("Escape Sequence"),
            ])
        )
        delete_row.set_selected(self.settings_manager.get("delete_binding", 0))
        delete_row.connect("notify::selected", self._on_delete_binding_changed)
        compatibility_group.add(delete_row)

        cjk_width_row = Adw.ComboRow(
            title=_("Ambiguous-width Characters"),
            subtitle=_("Set the width for ambiguous characters (e.g., CJK)"),
        )
        cjk_width_row.set_model(
            Gtk.StringList.new([_("Narrow (single-cell)"), _("Wide (double-cell)")])
        )
        cjk_width_row.set_selected(
            self.settings_manager.get("cjk_ambiguous_width", 1) - 1
        )
        cjk_width_row.connect("notify::selected", self._on_cjk_width_changed)
        compatibility_group.add(cjk_width_row)

        log_group = Adw.PreferencesGroup(
            title=_("Logging"), description=_("Configure application logging behavior")
        )
        advanced_page.add(log_group)

        log_to_file_row = Adw.SwitchRow(
            title=_("Save Logs to File"),
            subtitle=_("Save application logs to the configuration directory"),
        )
        log_to_file_row.set_active(self.settings_manager.get("log_to_file", False))
        log_to_file_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("log_to_file", r.get_active()),
        )
        log_group.add(log_to_file_row)

        log_level_row = Adw.ComboRow(
            title=_("Console Log Level"),
            subtitle=_("Set the minimum level of messages shown in the console"),
        )
        log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        log_level_row.set_model(Gtk.StringList.new(log_levels))
        current_level = self.settings_manager.get("console_log_level", "ERROR")
        try:
            selected_index = log_levels.index(current_level.upper())
        except ValueError:
            selected_index = 3
        log_level_row.set_selected(selected_index)
        log_level_row.connect("notify::selected", self._on_log_level_changed)
        log_group.add(log_level_row)

        reset_group = Adw.PreferencesGroup(
            title=_("Reset"), description=_("Reset application settings to defaults")
        )
        advanced_page.add(reset_group)
        reset_row = Adw.ActionRow(
            title=_("Reset All Settings"),
            subtitle=_("Restore all settings to their default values"),
        )
        reset_button = Gtk.Button(label=_("Reset"), css_classes=["destructive-action"])
        reset_button.set_valign(Gtk.Align.CENTER)
        reset_button.connect("clicked", self._on_reset_settings_clicked)
        reset_row.add_suffix(reset_button)
        reset_row.set_activatable_widget(reset_button)
        reset_group.add(reset_row)

    def _update_color_scheme_row_subtitle(self):
        scheme_data = self.settings_manager.get_color_scheme_data()
        self.color_scheme_row.set_subtitle(scheme_data.get("name", "Unknown"))

    def _on_manage_schemes_clicked(self, button):
        dialog = ColorSchemeDialog(self, self.settings_manager)
        main_window = self.get_transient_for()
        if main_window and hasattr(main_window, "terminal_manager"):
            dialog.connect(
                "scheme-changed",
                lambda d,
                idx: main_window.terminal_manager.apply_settings_to_all_terminals(),
            )
        dialog.connect(
            "close-request", lambda win: self._update_color_scheme_row_subtitle()
        )
        dialog.present()

    def _on_font_changed(self, font_button) -> None:
        font = font_button.get_font()
        self.settings_manager.set("font", font)
        self.emit("font-changed", font)

    def _on_line_spacing_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("line_spacing", value)

    def _on_transparency_changed(self, scale) -> None:
        value = scale.get_value()
        self.settings_manager.set("transparency", value)
        self.emit("transparency-changed", value)

    def _on_restore_policy_changed(self, combo_row, _param, policy_map):
        index = combo_row.get_selected()
        if 0 <= index < len(policy_map):
            policy = policy_map[index]
            self._on_setting_changed("session_restore_policy", policy)

    def _on_backup_now_clicked(self, button):
        app = self.get_transient_for().get_application()
        if app:
            app.activate_action("backup-now", None)

    def _on_restore_backup_clicked(self, button):
        app = self.get_transient_for().get_application()
        if app:
            app.activate_action("restore-backup", None)

    def _on_log_level_changed(self, combo_row, _param):
        selected_item = combo_row.get_selected_item()
        if selected_item:
            level_str = selected_item.get_string()
            self._on_setting_changed("console_log_level", level_str)

    def _on_scrollback_changed(self, spin_button) -> None:
        value = int(spin_button.get_value())
        self._on_setting_changed("scrollback_lines", value)

    def _on_mouse_scroll_sensitivity_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("mouse_scroll_sensitivity", value)

    def _on_touchpad_scroll_sensitivity_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("touchpad_scroll_sensitivity", value)

    def _on_cursor_shape_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("cursor_shape", index)

    def _on_cursor_blink_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("cursor_blink", index)

    def _on_text_blink_mode_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("text_blink_mode", index)

    def _on_word_chars_changed(self, entry_row):
        text = entry_row.get_text()
        self._on_setting_changed("word_char_exceptions", text)

    def _on_cjk_width_changed(self, combo_row, _param) -> None:
        value = combo_row.get_selected() + 1  # 0 -> 1 (Narrow), 1 -> 2 (Wide)
        self._on_setting_changed("cjk_ambiguous_width", value)

    def _on_backspace_binding_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("backspace_binding", index)

    def _on_delete_binding_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("delete_binding", index)

    def _on_setting_changed(self, key: str, value) -> None:
        self.settings_manager.set(key, value)
        self.emit("setting-changed", key, value)

    def _on_auto_hide_sidebar_changed(self, new_value: bool) -> None:
        """Handle auto-hide sidebar setting change with informational dialog."""
        current_value = self.settings_manager.get("auto_hide_sidebar", True)

        # If user is disabling auto-hide sidebar, show informational dialog
        if current_value and not new_value:
            self._show_sidebar_info_dialog()

        # Apply the setting
        self._on_setting_changed("auto_hide_sidebar", new_value)

    def _show_sidebar_info_dialog(self) -> None:
        """Show informational dialog about sidebar visibility changes."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Sidebar Visibility"),
            body=_(
                "The sidebar visibility change will take effect when you close and reopen the application. "
                "You can also toggle the sidebar manually using Ctrl+Shift+H."
            ),
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present()

    def _on_reset_settings_clicked(self, button) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            title=_("Reset All Settings"),
            body=_(
                "Are you sure you want to reset all settings to their default values? This action cannot be undone."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset All Settings"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            if response_id == "reset":
                try:
                    self.settings_manager.reset_to_defaults()
                    success_dialog = Adw.MessageDialog(
                        transient_for=self,
                        title=_("Settings Reset"),
                        body=_(
                            "All settings have been reset to their default values. Please restart the application for all changes to take effect."
                        ),
                    )
                    success_dialog.add_response("ok", _("OK"))
                    success_dialog.present()
                    self.logger.info("All settings reset to defaults")
                except Exception as e:
                    self.logger.error(f"Failed to reset settings: {e}")
                    error_dialog = Adw.MessageDialog(
                        transient_for=self,
                        title=_("Reset Failed"),
                        body=_("Failed to reset settings: {}").format(e),
                    )
                    error_dialog.add_response("ok", _("OK"))
                    error_dialog.present()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()
