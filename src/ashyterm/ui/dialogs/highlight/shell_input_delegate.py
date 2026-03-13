"""Shell input highlighting settings delegate for HighlightDialog."""

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ....settings.manager import get_settings_manager
from ....utils.translation_utils import _
from ..base_dialog import BaseDialog
from .cat_colorization_delegate import (
    CatColorizationDelegate,
    _create_theme_combo_row,
    _create_theme_mode_combo_row,
)

if TYPE_CHECKING:
    from .highlight_dialog import HighlightDialog


class ShellInputDelegate:
    """Manages shell input highlighting settings (experimental Pygments-based)."""

    def __init__(
        self,
        dialog: "HighlightDialog",
        cat_delegate: CatColorizationDelegate,
    ) -> None:
        self.dlg = dialog
        self._cat = cat_delegate

    def setup_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the shell input highlighting settings group (experimental)."""
        pygments_available = self._create_shell_input_group(page)
        self._add_shell_input_experimental_note(pygments_available)
        current_enabled = self._add_shell_input_toggle(pygments_available)
        self._add_command_not_found_toggle(current_enabled and pygments_available)

        if pygments_available:
            self._add_shell_input_theme_selectors(current_enabled)
        else:
            self._init_shell_input_fallback_attrs()

    # -- UI builders ----------------------------------------------------------

    def _create_shell_input_group(self, page: Adw.PreferencesPage) -> bool:
        """Create the shell input preferences group and check Pygments availability."""
        import importlib.util

        pygments_available = importlib.util.find_spec("pygments") is not None

        description = (
            _("Live syntax highlighting as you type commands (experimental)")
            if pygments_available
            else _(
                "Pygments is not installed - shell input highlighting unavailable"
            )
        )

        self.dlg._shell_input_group = Adw.PreferencesGroup(
            title=_("Shell Input Highlighting"),
            description=description,
        )
        page.add(self.dlg._shell_input_group)
        return pygments_available

    def _add_shell_input_experimental_note(self, pygments_available: bool) -> None:
        """Add experimental feature notice to shell input group."""
        if not pygments_available:
            return

        note_row = Adw.ActionRow(
            title=_("⚠️ Experimental Feature"),
            subtitle=_(
                "This feature applies highlighting to echoed shell input. "
                "It may not work perfectly with all prompts or shells."
            ),
        )
        note_row.add_css_class("dim-label")
        self.dlg._shell_input_group.add(note_row)

    def _add_shell_input_toggle(self, pygments_available: bool) -> bool:
        """Add the shell input highlighting toggle switch."""
        settings = get_settings_manager()

        self.dlg._shell_input_toggle = Adw.SwitchRow(
            title=_("Enable Shell Input Highlighting"),
            subtitle=_(
                "Color shell commands as you type them. Useful for SSH sessions and "
                "Docker containers where shell configuration cannot be changed."
            ),
        )
        current_enabled = settings.get("shell_input_highlighting_enabled", False)
        self.dlg._shell_input_toggle.set_active(current_enabled)
        self.dlg._shell_input_toggle.set_sensitive(pygments_available)
        self.dlg._shell_input_toggle.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE,
            self.on_shell_input_highlighting_toggled,
        )
        self.dlg._shell_input_group.add(self.dlg._shell_input_toggle)
        return current_enabled

    def _add_command_not_found_toggle(self, sensitive: bool) -> None:
        """Add toggle for command-not-found highlighting (red underline)."""
        settings = get_settings_manager()
        self.dlg._cmd_not_found_toggle = Adw.SwitchRow(
            title=_("Highlight Unknown Commands"),
            subtitle=_(
                "Underline commands in red if they are not found in $PATH or shell builtins."
            ),
        )
        current = settings.get("command_not_found_highlighting", True)
        self.dlg._cmd_not_found_toggle.set_active(current)
        self.dlg._cmd_not_found_toggle.set_sensitive(sensitive)
        self.dlg._cmd_not_found_toggle.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE,
            self._on_command_not_found_toggled,
        )
        self.dlg._shell_input_group.add(self.dlg._cmd_not_found_toggle)

    def _add_shell_input_theme_selectors(self, current_enabled: bool) -> None:
        """Add all theme selector rows for shell input highlighting."""
        settings = get_settings_manager()
        current_mode = self._add_shell_input_mode_row(settings)
        dark_themes, light_themes = self._get_shell_input_theme_categories()

        self._add_shell_input_dark_theme_row(settings, dark_themes)
        self._add_shell_input_light_theme_row(settings, dark_themes, light_themes)
        self._add_shell_input_manual_theme_row(settings)
        self._update_shell_input_theme_visibility(current_enabled, current_mode)

    def _add_shell_input_mode_row(self, settings) -> str:
        """Add the theme mode selector row (auto/manual)."""
        current_mode = settings.get("shell_input_theme_mode", "auto")
        self.dlg._theme_mode_row = _create_theme_mode_combo_row(
            subtitle=_("Auto detects background, Manual uses selected theme"),
            current_mode=current_mode,
            on_changed_callback=self.on_shell_input_mode_changed,
        )
        self.dlg._shell_input_group.add(self.dlg._theme_mode_row)
        return current_mode

    def _get_shell_input_theme_categories(self) -> tuple[list[str], list[str]]:
        """Get categorized dark and light theme lists from Pygments."""
        from pygments.styles import get_all_styles

        all_themes = sorted(get_all_styles())
        dark_only, light_only = self._cat._get_theme_categories()

        dark_themes = [t for t in dark_only if t in all_themes]
        for theme in all_themes:
            if theme not in dark_themes and theme not in light_only:
                dark_themes.append(theme)

        light_themes = [t for t in light_only if t in all_themes]
        for theme in all_themes:
            if theme not in light_themes and theme not in dark_only:
                light_themes.append(theme)

        return dark_themes, light_themes

    def _add_shell_input_dark_theme_row(
        self, settings, dark_themes: list[str]
    ) -> None:
        """Add the dark background theme selector row."""
        current_dark = settings.get("shell_input_dark_theme", "monokai")
        self.dlg._dark_theme_row, self.dlg._dark_theme_names = (
            _create_theme_combo_row(
                title=_("Dark Background Theme"),
                subtitle=_("Theme used when background is dark"),
                themes=dark_themes,
                current_theme=current_dark,
                on_changed_callback=self.on_dark_theme_changed,
            )
        )
        self.dlg._shell_input_group.add(self.dlg._dark_theme_row)

    def _add_shell_input_light_theme_row(
        self, settings, dark_themes: list[str], light_themes: list[str]
    ) -> None:
        """Add the light background theme selector row."""
        current_light = settings.get("shell_input_light_theme", "solarized-light")
        self.dlg._light_theme_row, self.dlg._light_theme_names = (
            _create_theme_combo_row(
                title=_("Light Background Theme"),
                subtitle=_("Theme used when background is light"),
                themes=light_themes,
                current_theme=current_light,
                on_changed_callback=self.on_light_theme_changed,
            )
        )
        self.dlg._shell_input_group.add(self.dlg._light_theme_row)

    def _add_shell_input_manual_theme_row(self, settings) -> None:
        """Add the manual theme selector row (legacy mode)."""
        from pygments.styles import get_all_styles

        all_themes = sorted(get_all_styles())
        current_theme = settings.get(
            "shell_input_pygments_theme", "monokai"
        ).lower()

        self.dlg._shell_input_theme_row, self.dlg._shell_input_theme_names = (
            _create_theme_combo_row(
                title=_("Manual Theme"),
                subtitle=_("Single theme to use in manual mode"),
                themes=all_themes,
                current_theme=current_theme,
                on_changed_callback=self.on_shell_input_theme_changed,
            )
        )
        self.dlg._shell_input_group.add(self.dlg._shell_input_theme_row)

    def _update_shell_input_theme_visibility(
        self, current_enabled: bool, current_mode: str
    ) -> None:
        """Update visibility of theme selector rows based on settings."""
        is_auto = current_mode == "auto"
        self.dlg._dark_theme_row.set_visible(current_enabled and is_auto)
        self.dlg._light_theme_row.set_visible(current_enabled and is_auto)
        self.dlg._shell_input_theme_row.set_visible(
            current_enabled and not is_auto
        )
        self.dlg._theme_mode_row.set_visible(current_enabled)

    def _init_shell_input_fallback_attrs(self) -> None:
        """Initialize fallback attributes when Pygments is not available."""
        self.dlg._shell_input_theme_row = None
        self.dlg._shell_input_theme_names = []
        self.dlg._theme_mode_row = None
        self.dlg._dark_theme_row = None
        self.dlg._light_theme_row = None

    # -- event handlers -------------------------------------------------------

    def _on_command_not_found_toggled(
        self, switch: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle command-not-found highlighting toggle."""
        enabled = switch.get_active()
        settings = get_settings_manager()
        settings.set("command_not_found_highlighting", enabled)
        self._refresh_shell_input_highlighter()

    def _refresh_shell_input_highlighter(self) -> None:
        """Refresh the shell input highlighter after settings change."""
        try:
            from ....terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.dlg.logger.warning(
                f"Failed to refresh shell input highlighter: {e}"
            )

    def on_shell_input_highlighting_toggled(
        self, switch: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle shell input highlighting toggle changes."""
        enabled = switch.get_active()
        settings = get_settings_manager()
        settings.set("shell_input_highlighting_enabled", enabled)

        is_auto = settings.get("shell_input_theme_mode", "auto") == "auto"
        if self.dlg._theme_mode_row:
            self.dlg._theme_mode_row.set_visible(enabled)
        if self.dlg._dark_theme_row:
            self.dlg._dark_theme_row.set_visible(enabled and is_auto)
        if self.dlg._light_theme_row:
            self.dlg._light_theme_row.set_visible(enabled and is_auto)
        if self.dlg._shell_input_theme_row:
            self.dlg._shell_input_theme_row.set_visible(enabled and not is_auto)
        if hasattr(self.dlg, "_cmd_not_found_toggle") and self.dlg._cmd_not_found_toggle:
            self.dlg._cmd_not_found_toggle.set_sensitive(enabled)

        self._refresh_shell_input_highlighter()

        self.dlg.logger.debug(
            f"Shell input highlighting {'enabled' if enabled else 'disabled'}"
        )

    def on_shell_input_mode_changed(
        self, combo_row: Adw.ComboRow, _pspec
    ) -> None:
        """Handle theme mode changes (auto/manual)."""
        idx = combo_row.get_selected()
        is_auto = idx == 0
        mode = "auto" if is_auto else "manual"

        settings = get_settings_manager()
        settings.set("shell_input_theme_mode", mode)

        if self.dlg._dark_theme_row:
            self.dlg._dark_theme_row.set_visible(is_auto)
        if self.dlg._light_theme_row:
            self.dlg._light_theme_row.set_visible(is_auto)
        if self.dlg._shell_input_theme_row:
            self.dlg._shell_input_theme_row.set_visible(not is_auto)

        self._refresh_shell_input_highlighter()
        self.dlg.logger.debug(f"Shell input theme mode changed to: {mode}")

    def on_dark_theme_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle dark theme selection changes."""
        idx = combo_row.get_selected()
        if (
            idx != Gtk.INVALID_LIST_POSITION
            and self.dlg._dark_theme_names
            and idx < len(self.dlg._dark_theme_names)
        ):
            theme = self.dlg._dark_theme_names[idx]
            settings = get_settings_manager()
            settings.set("shell_input_dark_theme", theme)
            self._refresh_shell_input_highlighter()
            self.dlg.logger.debug(
                f"Shell input dark theme changed to: {theme}"
            )

    def on_light_theme_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle light theme selection changes."""
        idx = combo_row.get_selected()
        if (
            idx != Gtk.INVALID_LIST_POSITION
            and self.dlg._light_theme_names
            and idx < len(self.dlg._light_theme_names)
        ):
            theme = self.dlg._light_theme_names[idx]
            settings = get_settings_manager()
            settings.set("shell_input_light_theme", theme)
            self._refresh_shell_input_highlighter()
            self.dlg.logger.debug(
                f"Shell input light theme changed to: {theme}"
            )

    def on_shell_input_theme_changed(
        self, combo_row: Adw.ComboRow, _pspec
    ) -> None:
        """Handle shell input color theme changes (manual mode)."""
        idx = combo_row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and hasattr(
            self.dlg, "_shell_input_theme_names"
        ):
            theme_names = self.dlg._shell_input_theme_names
            if idx < len(theme_names):
                theme = theme_names[idx]
                settings = get_settings_manager()
                settings.set("shell_input_pygments_theme", theme)
                self._refresh_shell_input_highlighter()
                self.dlg.logger.debug(
                    f"Shell input theme changed to: {theme}"
                )

    def show_restart_required_dialog(self) -> None:
        """Show a dialog informing user that restart is required."""
        dialog = Adw.AlertDialog(
            heading=_("Restart Required"),
            body=_(
                "Restart the program for the colors to be applied to the terminal."
            ),
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present(self.dlg)
