"""Cat colorization settings delegate for HighlightDialog."""

from typing import TYPE_CHECKING, Callable

from gi.repository import Adw, Gtk

from ....settings.manager import get_settings_manager
from ....utils.translation_utils import _
from ..base_dialog import BaseDialog

if TYPE_CHECKING:
    from .highlight_dialog import HighlightDialog


class CatColorizationDelegate:
    """Manages cat command colorization settings (Pygments-based)."""

    def __init__(self, dialog: "HighlightDialog") -> None:
        self.dlg = dialog

    def setup_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the cat colorization settings group using Pygments."""
        import importlib.util

        pygments_available = importlib.util.find_spec("pygments") is not None

        self._create_cat_group(page, pygments_available)
        cat_settings = get_settings_manager()

        if pygments_available:
            self._add_cat_experimental_note()

        self._add_cat_colorization_toggle(cat_settings)

        if pygments_available:
            self._add_cat_theme_selectors(cat_settings)
        else:
            self._add_pygments_install_hint()

    def load_settings(self, settings, cat_enabled: bool) -> None:
        """Load cat theme-related settings."""
        if self.dlg._cat_theme_mode_row is None:
            return

        current_mode = settings.get("cat_theme_mode", "auto")
        self.dlg._cat_theme_mode_row.set_selected(
            0 if current_mode == "auto" else 1
        )
        is_auto_mode = current_mode == "auto"

        if self.dlg._cat_dark_theme_row is not None:
            current_dark = settings.get("cat_dark_theme", "monokai")
            try:
                dark_idx = self.dlg._cat_dark_theme_names.index(current_dark)
                self.dlg._cat_dark_theme_row.set_selected(dark_idx)
            except ValueError:
                self.dlg._cat_dark_theme_row.set_selected(0)
            self.dlg._cat_dark_theme_row.set_visible(cat_enabled and is_auto_mode)

        if self.dlg._cat_light_theme_row is not None:
            current_light = settings.get("cat_light_theme", "solarized-light")
            try:
                light_idx = self.dlg._cat_light_theme_names.index(current_light)
                self.dlg._cat_light_theme_row.set_selected(light_idx)
            except ValueError:
                self.dlg._cat_light_theme_row.set_selected(0)
            self.dlg._cat_light_theme_row.set_visible(cat_enabled and is_auto_mode)

        if self.dlg._cat_theme_row is not None:
            current_theme = settings.get("pygments_theme", "monokai").lower()
            try:
                theme_index = self.dlg._cat_theme_names.index(current_theme)
                self.dlg._cat_theme_row.set_selected(theme_index)
            except ValueError:
                self.dlg._cat_theme_row.set_selected(0)
            self.dlg._cat_theme_row.set_visible(cat_enabled and not is_auto_mode)
            self.dlg._cat_theme_row.set_sensitive(cat_enabled)

        self.dlg._cat_theme_mode_row.set_visible(cat_enabled)

    # -- UI builders ----------------------------------------------------------

    def _create_cat_group(
        self, page: Adw.PreferencesPage, pygments_available: bool
    ) -> None:
        """Create the cat colorization preferences group."""
        if pygments_available:
            description = _(
                "Syntax highlighting for '{}' command output (using Pygments)"
            ).format("cat")
        else:
            description = _(
                "Pygments is not installed - '{}' output will not be colorized"
            ).format("cat")

        self.dlg._cat_group = Adw.PreferencesGroup(
            title=_("{} Command Colorization").format("cat"),
            description=description,
        )
        page.add(self.dlg._cat_group)

    def _add_cat_experimental_note(self) -> None:
        """Add experimental feature notice to cat group."""
        note_row = Adw.ActionRow(
            title=_("⚠️ Experimental Feature"),
            subtitle=_(
                "This feature colorizes output for the '{}' command. "
                "It may not work perfectly with every shell/prompt or when output is fragmented."
            ).format("cat"),
        )
        note_row.add_css_class("dim-label")
        self.dlg._cat_group.add(note_row)

    def _add_cat_colorization_toggle(self, settings) -> None:
        """Add the cat colorization enable/disable toggle."""
        self.dlg._cat_colorization_toggle = Adw.SwitchRow(
            title=_("Enable '{}' Colorization").format("cat"),
            subtitle=_("Apply syntax highlighting to '{}' command output").format(
                "cat"
            ),
        )
        current_enabled = settings.get("cat_colorization_enabled", True)
        self.dlg._cat_colorization_toggle.set_active(current_enabled)
        self.dlg._cat_colorization_toggle.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self.on_cat_colorization_toggled
        )
        self.dlg._cat_group.add(self.dlg._cat_colorization_toggle)

    def _add_cat_theme_selectors(self, settings) -> None:
        """Add theme selection widgets for cat colorization."""
        from pygments.styles import get_all_styles

        all_themes = sorted(get_all_styles())
        current_mode = settings.get("cat_theme_mode", "auto")
        current_enabled = settings.get("cat_colorization_enabled", True)

        self._add_cat_theme_mode_row(settings, current_mode)

        dark_only, light_only = self._get_theme_categories()

        self._add_cat_dark_theme_row(settings, all_themes, dark_only, light_only)
        self._add_cat_light_theme_row(settings, all_themes, dark_only, light_only)
        self._add_cat_manual_theme_row(settings, all_themes)

        is_auto_mode = current_mode == "auto"
        self.dlg._cat_theme_mode_row.set_visible(current_enabled)
        self.dlg._cat_dark_theme_row.set_visible(current_enabled and is_auto_mode)
        self.dlg._cat_light_theme_row.set_visible(current_enabled and is_auto_mode)
        self.dlg._cat_theme_row.set_visible(current_enabled and not is_auto_mode)

    def _add_cat_theme_mode_row(self, settings, current_mode: str) -> None:
        """Add the theme mode selector row."""
        self.dlg._cat_theme_mode_row = _create_theme_mode_combo_row(
            subtitle=_("Auto: adapts to background color. Manual: single theme."),
            current_mode=current_mode,
            on_changed_callback=self.on_cat_theme_mode_changed,
        )
        self.dlg._cat_group.add(self.dlg._cat_theme_mode_row)

    def _get_theme_categories(self) -> tuple:
        """Get lists of dark-only and light-only Pygments themes."""
        dark_only_themes = [
            "a11y-dark",
            "a11y-high-contrast-dark",
            "blinds-dark",
            "coffee",
            "dracula",
            "fruity",
            "github-dark",
            "github-dark-colorblind",
            "github-dark-high-contrast",
            "gotthard-dark",
            "greative",
            "gruvbox-dark",
            "inkpot",
            "lightbulb",
            "material",
            "monokai",
            "native",
            "nord",
            "nord-darker",
            "one-dark",
            "paraiso-dark",
            "pitaya-smoothie",
            "rrt",
            "solarized-dark",
            "stata-dark",
            "vim",
            "zenburn",
        ]
        light_only_themes = [
            "a11y-high-contrast-light",
            "a11y-light",
            "abap",
            "algol",
            "algol_nu",
            "arduino",
            "autumn",
            "blinds-light",
            "borland",
            "bw",
            "colorful",
            "default",
            "emacs",
            "friendly",
            "friendly_grayscale",
            "github-light",
            "github-light-colorblind",
            "github-light-high-contrast",
            "gotthard-light",
            "gruvbox-light",
            "igor",
            "lilypond",
            "lovelace",
            "manni",
            "murphy",
            "paraiso-light",
            "pastie",
            "perldoc",
            "rainbow_dash",
            "sas",
            "solarized-light",
            "staroffice",
            "stata-light",
            "tango",
            "trac",
            "vs",
            "xcode",
        ]
        return dark_only_themes, light_only_themes

    def _add_cat_dark_theme_row(
        self, settings, all_themes, dark_only, light_only
    ) -> list:
        """Add dark theme selector row."""
        self.dlg._cat_dark_theme_row = Adw.ComboRow(
            title=_("Dark Background Theme"),
            subtitle=_("Theme used when background is dark"),
        )
        dark_themes_model = Gtk.StringList()
        dark_themes = [t for t in dark_only if t in all_themes]
        dark_themes.extend(
            t for t in all_themes if t not in dark_themes and t not in light_only
        )
        for theme in dark_themes:
            dark_themes_model.append(theme)
        self.dlg._cat_dark_theme_row.set_model(dark_themes_model)
        self.dlg._cat_dark_theme_names = dark_themes

        current_dark = settings.get("cat_dark_theme", "monokai")
        try:
            self.dlg._cat_dark_theme_row.set_selected(dark_themes.index(current_dark))
        except ValueError:
            self.dlg._cat_dark_theme_row.set_selected(0)
        self.dlg._cat_dark_theme_row.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self.on_cat_dark_theme_changed
        )
        self.dlg._cat_group.add(self.dlg._cat_dark_theme_row)
        return dark_themes

    def _add_cat_light_theme_row(
        self, settings, all_themes, dark_only, light_only
    ) -> list:
        """Add light theme selector row."""
        self.dlg._cat_light_theme_row = Adw.ComboRow(
            title=_("Light Background Theme"),
            subtitle=_("Theme used when background is light"),
        )
        light_themes_model = Gtk.StringList()
        light_themes = [t for t in light_only if t in all_themes]
        light_themes.extend(
            t for t in all_themes if t not in light_themes and t not in dark_only
        )
        for theme in light_themes:
            light_themes_model.append(theme)
        self.dlg._cat_light_theme_row.set_model(light_themes_model)
        self.dlg._cat_light_theme_names = light_themes

        current_light = settings.get("cat_light_theme", "solarized-light")
        try:
            self.dlg._cat_light_theme_row.set_selected(
                light_themes.index(current_light)
            )
        except ValueError:
            self.dlg._cat_light_theme_row.set_selected(0)
        self.dlg._cat_light_theme_row.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self.on_cat_light_theme_changed
        )
        self.dlg._cat_group.add(self.dlg._cat_light_theme_row)
        return light_themes

    def _add_cat_manual_theme_row(self, settings, all_themes) -> None:
        """Add manual theme selector row."""
        self.dlg._cat_theme_row = Adw.ComboRow(
            title=_("Manual Theme"),
            subtitle=_("Single theme to use in manual mode"),
        )
        manual_themes_model = Gtk.StringList()
        for theme in all_themes:
            manual_themes_model.append(theme)
        self.dlg._cat_theme_row.set_model(manual_themes_model)
        self.dlg._cat_theme_names = all_themes

        current_theme = settings.get("pygments_theme", "monokai").lower()
        try:
            self.dlg._cat_theme_row.set_selected(all_themes.index(current_theme))
        except ValueError:
            self.dlg._cat_theme_row.set_selected(0)
        self.dlg._cat_theme_row.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self.on_cat_theme_changed
        )
        self.dlg._cat_group.add(self.dlg._cat_theme_row)

    def _add_pygments_install_hint(self) -> None:
        """Add Pygments installation hint when not available."""
        self.dlg._cat_theme_row = None
        self.dlg._cat_theme_names = []
        self.dlg._cat_theme_mode_row = None
        self.dlg._cat_dark_theme_row = None
        self.dlg._cat_dark_theme_names = []
        self.dlg._cat_light_theme_row = None
        self.dlg._cat_light_theme_names = []

        install_row = Adw.ActionRow(
            title=_("Install Pygments"),
            subtitle=_("pip install pygments"),
        )
        install_row.add_css_class("dim-label")
        self.dlg._cat_group.add(install_row)

    # -- event handlers -------------------------------------------------------

    def on_cat_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle Pygments theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(self.dlg._cat_theme_names):
            theme = self.dlg._cat_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("pygments_theme", theme)
            self.dlg.emit("settings-changed")
            self.dlg.add_toast(
                Adw.Toast(title=_("Theme changed to: {}").format(theme))
            )

    def on_cat_colorization_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle cat colorization toggle."""
        is_active = switch.get_active()
        settings = get_settings_manager()
        settings.set("cat_colorization_enabled", is_active)

        if self.dlg._cat_theme_mode_row is not None:
            self.dlg._cat_theme_mode_row.set_visible(is_active)
            is_auto_mode = self.dlg._cat_theme_mode_row.get_selected() == 0
            if self.dlg._cat_dark_theme_row is not None:
                self.dlg._cat_dark_theme_row.set_visible(is_active and is_auto_mode)
            if self.dlg._cat_light_theme_row is not None:
                self.dlg._cat_light_theme_row.set_visible(is_active and is_auto_mode)
            if self.dlg._cat_theme_row is not None:
                self.dlg._cat_theme_row.set_visible(is_active and not is_auto_mode)
        elif self.dlg._cat_theme_row is not None:
            self.dlg._cat_theme_row.set_visible(is_active)

        self.dlg.emit("settings-changed")

        status = _("enabled") if is_active else _("disabled")
        self.dlg.add_toast(
            Adw.Toast(title=_("'{}' colorization {}").format("cat", status))
        )

    def on_cat_theme_mode_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat theme mode change (auto/manual)."""
        selected_index = combo.get_selected()
        is_auto_mode = selected_index == 0
        mode = "auto" if is_auto_mode else "manual"

        settings = get_settings_manager()
        settings.set("cat_theme_mode", mode)

        if self.dlg._cat_dark_theme_row is not None:
            self.dlg._cat_dark_theme_row.set_visible(is_auto_mode)
        if self.dlg._cat_light_theme_row is not None:
            self.dlg._cat_light_theme_row.set_visible(is_auto_mode)
        if self.dlg._cat_theme_row is not None:
            self.dlg._cat_theme_row.set_visible(not is_auto_mode)

        self.dlg.emit("settings-changed")
        mode_name = _("Auto") if is_auto_mode else _("Manual")
        self.dlg.add_toast(
            Adw.Toast(title=_("Cat theme mode: {}").format(mode_name))
        )

    def on_cat_dark_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat dark theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(
            self.dlg._cat_dark_theme_names
        ):
            theme = self.dlg._cat_dark_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("cat_dark_theme", theme)
            self.dlg.emit("settings-changed")
            self.dlg.add_toast(
                Adw.Toast(title=_("Dark theme: {}").format(theme))
            )

    def on_cat_light_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat light theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(
            self.dlg._cat_light_theme_names
        ):
            theme = self.dlg._cat_light_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("cat_light_theme", theme)
            self.dlg.emit("settings-changed")
            self.dlg.add_toast(
                Adw.Toast(title=_("Light theme: {}").format(theme))
            )


# -- module-level helpers (shared with ShellInputDelegate) --------------------


def _create_theme_mode_combo_row(
    subtitle: str,
    current_mode: str,
    on_changed_callback: Callable,
) -> Adw.ComboRow:
    """Create a theme mode (Auto/Manual) selector ComboRow."""
    row = Adw.ComboRow(title=_("Theme Mode"), subtitle=subtitle)
    model = Gtk.StringList()
    model.append(_("Auto"))
    model.append(_("Manual"))
    row.set_model(model)
    row.set_selected(0 if current_mode == "auto" else 1)
    row.connect(BaseDialog.SIGNAL_NOTIFY_SELECTED, on_changed_callback)
    return row


def _create_theme_combo_row(
    title: str,
    subtitle: str,
    themes: list[str],
    current_theme: str,
    on_changed_callback: Callable,
) -> tuple[Adw.ComboRow, list[str]]:
    """Create a theme selector ComboRow."""
    row = Adw.ComboRow(title=title, subtitle=subtitle)
    model = Gtk.StringList()
    for theme in themes:
        model.append(theme)
    row.set_model(model)

    try:
        idx = themes.index(current_theme)
        row.set_selected(idx)
    except ValueError:
        row.set_selected(0)

    row.connect(BaseDialog.SIGNAL_NOTIFY_SELECTED, on_changed_callback)
    return row, themes
