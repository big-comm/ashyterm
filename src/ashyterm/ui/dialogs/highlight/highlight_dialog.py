"""Main HighlightDialog for managing syntax highlighting settings."""

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ....helpers import generate_unique_name
from ....settings.highlights import (
    HighlightContext,
    HighlightRule,
    get_highlight_manager,
)
from ....settings.manager import get_settings_manager
from ....utils.accessibility import set_label as a11y_label
from ....utils.icons import icon_image
from ....utils.logger import get_logger
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ..base_dialog import (
    BaseDialog,
    create_icon_button,
    show_delete_confirmation_dialog,
)
from ._constants import get_rule_subtitle
from .context_rules_dialog import ContextRulesDialog
from .rule_edit_dialog import RuleEditDialog
from .small_dialogs import AddIgnoredCommandDialog, ContextNameDialog


class HighlightDialog(Adw.PreferencesWindow):
    """
    Main dialog for managing syntax highlighting settings.

    Provides controls for global activation settings, a list of
    customizable highlight rules, and context-aware highlighting
    with command-specific rule sets.

    Window size is persisted between sessions.
    """

    __gsignals__ = {
        "settings-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    # Settings keys for window size persistence
    _SIZE_KEY_WIDTH = "highlight_dialog_width"
    _SIZE_KEY_HEIGHT = "highlight_dialog_height"
    _DEFAULT_WIDTH = 900
    _DEFAULT_HEIGHT = 700

    def __init__(self, parent_window: Gtk.Window):
        """
        Initialize the highlight dialog.

        Args:
            parent_window: Parent window for the dialog.
        """
        # Load saved dimensions
        settings = get_settings_manager()
        saved_width = settings.get(self._SIZE_KEY_WIDTH, self._DEFAULT_WIDTH)
        saved_height = settings.get(self._SIZE_KEY_HEIGHT, self._DEFAULT_HEIGHT)

        super().__init__(
            title=_("Highlight Colors"),
            transient_for=parent_window,
            modal=False,
            hide_on_close=False,
            default_width=saved_width,
            default_height=saved_height,
            search_enabled=True,
        )
        self.add_css_class("ashyterm-dialog")
        self.logger = get_logger("ashyterm.ui.dialogs.highlight")
        self._parent_window = parent_window
        self._manager = get_highlight_manager()
        self._rule_rows: list[Adw.ExpanderRow] = []
        self._context_rule_rows: list[Adw.ExpanderRow] = []
        self._selected_context: str = ""
        # UI groups for visibility control (set during _build_ui before any .add() calls)
        self._cat_group: Adw.PreferencesGroup = None  # type: ignore[assignment]
        self._shell_input_group: Adw.PreferencesGroup = None  # type: ignore[assignment]
        self._ignored_commands_group: Adw.PreferencesGroup = None  # type: ignore[assignment]
        self._rules_group: Adw.PreferencesGroup = None  # type: ignore[assignment]
        self._context_page: Adw.PreferencesPage = None  # type: ignore[assignment]
        self._css_provider = None
        self._dark_theme_names: list[str] | None = None
        self._light_theme_names: list[str] | None = None

        # Flag to block signals during initialization
        self._initializing = True

        # Connect to window state events for size persistence
        self.connect("close-request", self._on_close_request)

        self._setup_ui()
        self._load_settings()

        # Mark initialization complete
        self._initializing = False

        self.logger.info("HighlightDialog initialized")

    def _on_close_request(self, window) -> bool:
        """Save window size when closing."""
        self._save_window_size()
        return False  # Allow default close behavior

    def _save_window_size(self) -> None:
        """Save the current window size to settings."""
        settings = get_settings_manager()
        width = self.get_width()
        height = self.get_height()

        # Only save if different from current saved values
        if (
            settings.get(self._SIZE_KEY_WIDTH, 0) != width
            or settings.get(self._SIZE_KEY_HEIGHT, 0) != height
        ):
            settings.set(self._SIZE_KEY_WIDTH, width)
            settings.set(self._SIZE_KEY_HEIGHT, height)
            self.logger.debug(f"Saved highlight dialog size: {width}x{height}")

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Create first page for Terminal Colors (primary, fundamental settings)
        self._terminal_colors_page = Adw.PreferencesPage(
            title=_("Terminal Colors"),
            icon_name="preferences-color-symbolic",
        )
        self.add(self._terminal_colors_page)

        # Color Scheme group - the most important terminal color setting
        self._setup_color_scheme_page(self._terminal_colors_page)

        # Create second page for Output Highlighting (Global Rules)
        self._global_page = Adw.PreferencesPage(
            title=_("Output Highlighting"),
            icon_name="view-list-symbolic",
        )
        self.add(self._global_page)

        # Welcome/explanation text
        self._setup_welcome_banner(self._global_page)

        # Activation group with performance warning
        self._setup_activation_group(self._global_page)

        # Cat colorization group (Pygments-based syntax highlighting)
        self._setup_cat_colorization_group(self._global_page)

        # Shell input highlighting group (experimental)
        self._setup_shell_input_highlighting_group(self._global_page)

        # Ignored commands group (collapsible - placed before Global Rules as it's compact)
        self._setup_ignored_commands_group(self._global_page)

        # Global rules group (last, as it can be a longer list)
        self._setup_rules_group(self._global_page)

        # Apply initial sensitivity state for dependent groups
        self._update_dependent_groups_sensitivity()

        # Create third page for Command-Specific Rules
        self._context_page = Adw.PreferencesPage(
            title=_("Command-Specific"),
            icon_name="utilities-terminal-symbolic",
        )
        self.add(self._context_page)

        # Context settings group
        self._setup_context_settings_group(self._context_page)

        # Context selector group (clicking a context opens a dialog)
        self._setup_context_selector_group(self._context_page)

    def _setup_color_scheme_page(self, page: Adw.PreferencesPage) -> None:
        """Setup the Terminal Colors page with integrated Color Scheme selector."""
        # Color Scheme group
        scheme_group = Adw.PreferencesGroup(
            title=_("Color Scheme"),
        )
        page.add(scheme_group)

        # Create the scheme list
        self._scheme_listbox = Gtk.ListBox()
        self._scheme_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._scheme_listbox.add_css_class("boxed-list")
        self._scheme_listbox.connect("row-selected", self._on_scheme_row_selected)

        # Populate schemes
        self._scheme_rows: dict = {}
        self._populate_color_schemes()

        scheme_group.add(self._scheme_listbox)

        # Actions group (New, Edit, Delete)
        actions_group = Adw.PreferencesGroup()
        page.add(actions_group)

        # New scheme button
        new_row = Adw.ActionRow(
            title=_("Create New Scheme"),
            subtitle=_("Create a custom color scheme based on existing"),
        )
        new_row.set_activatable(True)
        new_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_new_scheme_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        new_row.add_suffix(new_btn)
        new_row.set_activatable_widget(new_btn)
        actions_group.add(new_row)

    def _populate_color_schemes(self) -> None:
        """Populate the color scheme list."""
        settings = get_settings_manager()
        all_schemes = settings.get_all_schemes()
        scheme_order = settings.get_scheme_order()
        current_scheme = settings.get_color_scheme_name()

        # Clear existing rows
        while True:
            row = self._scheme_listbox.get_first_child()
            if row is None:
                break
            self._scheme_listbox.remove(row)
        self._scheme_rows.clear()

        for scheme_key in scheme_order:
            if scheme_key not in all_schemes:
                continue

            scheme_data = all_schemes[scheme_key]
            is_custom = scheme_key in settings.custom_schemes

            row = self._create_scheme_row(scheme_key, scheme_data, is_custom)
            self._scheme_listbox.append(row)
            self._scheme_rows[scheme_key] = row

            # Select current scheme
            if scheme_key == current_scheme:
                self._scheme_listbox.select_row(row)

    def _create_scheme_row(
        self, scheme_key: str, scheme_data: dict, is_custom: bool
    ) -> Adw.ActionRow:
        """Create a row for a color scheme with preview."""
        row = Adw.ActionRow(
            title=scheme_data.get("name", scheme_key),
        )
        row.scheme_key = scheme_key
        row.scheme_data = scheme_data
        row.is_custom = is_custom

        # Color preview using DrawingArea for better visual representation
        preview = Gtk.DrawingArea()
        preview.set_size_request(120, 32)
        preview.set_valign(Gtk.Align.CENTER)
        preview.set_margin_end(12)
        a11y_label(preview, _("Color scheme preview"))

        def draw_preview(area, cr, width, height):
            # Draw background
            bg_color = scheme_data.get("background", "#000000")
            self._set_color_from_hex(cr, bg_color)
            cr.rectangle(0, 0, width * 0.3, height)
            cr.fill()

            # Draw foreground
            fg_color = scheme_data.get("foreground", "#ffffff")
            self._set_color_from_hex(cr, fg_color)
            cr.rectangle(width * 0.3, 0, width * 0.15, height)
            cr.fill()

            # Draw palette colors
            palette = scheme_data.get("palette", [])
            num_colors = min(len(palette), 8)
            if num_colors > 0:
                color_width = (width * 0.55) / num_colors
                x_offset = width * 0.45
                for i, color in enumerate(palette[:num_colors]):
                    self._set_color_from_hex(cr, color)
                    cr.rectangle(x_offset + i * color_width, 0, color_width, height)
                    cr.fill()

            # Draw subtle border
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.3)
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, width - 1, height - 1)
            cr.stroke()

        preview.set_draw_func(draw_preview)
        row.add_prefix(preview)

        # Edit button - available for ALL schemes (built-in creates a copy)
        edit_tooltip = (
            _("Edit scheme") if is_custom else _("Customize (creates a copy)")
        )
        edit_btn = create_icon_button(
            "document-edit-symbolic",
            tooltip=edit_tooltip,
            on_clicked=lambda b, r=row: self._on_edit_scheme_clicked(r),
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        row.add_suffix(edit_btn)

        # Delete button only for custom schemes
        if is_custom:
            delete_btn = create_icon_button(
                "user-trash-symbolic",
                tooltip=_("Delete scheme"),
                on_clicked=lambda b, r=row: self._on_delete_scheme_clicked(r),
                flat=True,
                valign=Gtk.Align.CENTER,
            )
            row.add_suffix(delete_btn)

        # Checkmark for selected scheme
        check_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        check_icon.set_visible(False)
        row.check_icon = check_icon
        row.add_suffix(check_icon)

        return row

    def _set_color_from_hex(self, cr, hex_color: str) -> None:
        """Set cairo source color from hex string."""
        try:
            hex_val = hex_color.lstrip("#")
            r = int(hex_val[0:2], 16) / 255.0
            g = int(hex_val[2:4], 16) / 255.0
            b = int(hex_val[4:6], 16) / 255.0
            cr.set_source_rgb(r, g, b)
        except (ValueError, IndexError):
            cr.set_source_rgb(0.5, 0.5, 0.5)

    def _on_scheme_row_selected(self, listbox, row) -> None:
        """Handle color scheme selection."""
        if row is None:
            return

        # Skip if this is during initialization
        if self._initializing:
            return

        # Update visual selection (checkmarks)
        for scheme_row in self._scheme_rows.values():
            scheme_row.check_icon.set_visible(scheme_row == row)

        # Apply the scheme
        settings = get_settings_manager()
        scheme_order = settings.get_scheme_order()
        selected_index = scheme_order.index(row.scheme_key)
        settings.set("color_scheme", selected_index)

        self.logger.info(f"Color scheme changed to: {row.scheme_key}")

        # Apply to terminals
        if self._parent_window and hasattr(self._parent_window, "terminal_manager"):
            self._parent_window.terminal_manager.apply_settings_to_all_terminals()

        # Refresh shell input highlighter
        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input highlighter: {e}")

    def _on_new_scheme_clicked(self, button) -> None:
        """Create a new color scheme based on selected."""
        from ...color_scheme_dialog import _SchemeEditorDialog

        settings = get_settings_manager()
        selected_row = self._scheme_listbox.get_selected_row()
        template_scheme = (
            selected_row.scheme_data
            if selected_row
            else settings.get_all_schemes()["dark"]
        )

        all_names = {s["name"] for s in settings.get_all_schemes().values()}

        new_name = generate_unique_name(f"Copy of {template_scheme['name']}", all_names)

        new_scheme_data = template_scheme.copy()
        new_scheme_data["name"] = new_name

        editor = _SchemeEditorDialog(
            self, settings, new_name, new_scheme_data, is_new=True
        )
        editor.connect("save-requested", self._on_editor_save)
        editor.present()

    def _on_edit_scheme_clicked(self, row) -> None:
        """Edit a color scheme. Built-in schemes create a copy when saved."""
        from ...color_scheme_dialog import _SchemeEditorDialog

        settings = get_settings_manager()

        # For built-in schemes, we'll create a new scheme (is_new=True)
        # For custom schemes, we edit in place (is_new=False)
        is_builtin = not row.is_custom

        if is_builtin:
            # Generate unique name for the copy
            all_names = {s["name"] for s in settings.get_all_schemes().values()}

            new_name = generate_unique_name(
                f"{row.scheme_data.get('name', row.scheme_key)} (Custom)",
                all_names,
            )
            scheme_data = row.scheme_data.copy()
            scheme_data["name"] = new_name

            editor = _SchemeEditorDialog(self, settings, None, scheme_data, is_new=True)
        else:
            editor = _SchemeEditorDialog(
                self, settings, row.scheme_key, row.scheme_data.copy(), is_new=False
            )

        editor.connect("save-requested", self._on_editor_save)
        editor.present()

    def _on_delete_scheme_clicked(self, row) -> None:
        """Delete a custom color scheme."""
        scheme_key = row.scheme_key
        scheme_name = row.scheme_data.get("name", scheme_key)

        def on_confirm() -> None:
            settings = get_settings_manager()
            if scheme_key in settings.custom_schemes:
                del settings.custom_schemes[scheme_key]
                settings.save_custom_schemes()

                # If deleted scheme was selected, switch to first scheme
                if settings.get_color_scheme_name() == scheme_key:
                    settings.set("color_scheme", 0)
                    if self._parent_window and hasattr(
                        self._parent_window, "terminal_manager"
                    ):
                        self._parent_window.terminal_manager.apply_settings_to_all_terminals()

                self._populate_color_schemes()
                self.add_toast(Adw.Toast(title=_("Scheme deleted")))

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Delete Scheme?"),
            body=BaseDialog.MSG_DELETE_CONFIRMATION.format(scheme_name),
            on_confirm=on_confirm,
        )

    def _save_scheme_data(
        self, original_key: str, new_key: str, scheme_data: dict
    ) -> tuple[bool, str]:
        """Save scheme data and return (is_new, final_key)."""
        settings = get_settings_manager()
        is_new = (
            original_key is None
            or original_key == ""
            or original_key not in settings.custom_schemes
        )

        if is_new:
            import time

            unique_key = f"custom_{int(time.time() * 1000)}"
            settings.custom_schemes[unique_key] = scheme_data
            return True, unique_key

        if original_key in settings.custom_schemes:
            del settings.custom_schemes[original_key]
        final_key = new_key if new_key else original_key
        settings.custom_schemes[final_key] = scheme_data
        return False, final_key

    def _select_scheme_by_key(self, scheme_key: str) -> None:
        """Select a scheme in the listbox by its key."""
        settings = get_settings_manager()
        scheme_order = settings.get_scheme_order()
        try:
            new_scheme_index = scheme_order.index(scheme_key)
            settings.set("color_scheme", new_scheme_index)
            for scheme_row in self._scheme_rows.values():
                if scheme_row.scheme_key == scheme_key:
                    self._scheme_listbox.select_row(scheme_row)
                    for other_row in self._scheme_rows.values():
                        other_row.check_icon.set_visible(other_row == scheme_row)
                    break
        except ValueError:
            pass

    def _apply_scheme_changes(self) -> None:
        """Apply scheme changes to terminals and GTK theme."""
        # Apply to GTK theme if using terminal theme
        # Note: Theme update is handled automatically by settings listener

        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception:
            pass

    def _on_editor_save(
        self, editor, original_key: str, new_key: str, scheme_data: dict
    ) -> None:
        """Handle save from scheme editor."""
        settings = get_settings_manager()

        is_new, unique_key = self._save_scheme_data(original_key, new_key, scheme_data)
        settings.save_custom_schemes()
        self._populate_color_schemes()

        if is_new:
            self._select_scheme_by_key(unique_key)

        self._apply_scheme_changes()
        self.add_toast(Adw.Toast(title=_("Scheme saved")))

    def _setup_welcome_banner(self, page: Adw.PreferencesPage) -> None:
        """Setup the welcome/explanation text at the top."""
        welcome_group = Adw.PreferencesGroup(
            description=_(
                "Colorizes terminal output patterns like errors, warnings, and IPs. "
                "Many rules can slow down large outputs."
            ),
        )
        page.add(welcome_group)

    def _setup_activation_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the activation settings group with performance warning."""
        activation_group = Adw.PreferencesGroup(
            title=_("Activation"),
            description=_(
                "⚠️ On slower computers, enabling output highlighting may slightly "
                "reduce terminal responsiveness, as all displayed content is processed for color patterns."
            ),
        )
        page.add(activation_group)

        # Enable for local terminals toggle
        self._local_toggle = Adw.SwitchRow(
            title=_("Local Terminals"),
            subtitle=_("Apply output highlighting to local terminal sessions"),
        )
        self._local_toggle.set_active(self._manager.enabled_for_local)
        self._local_toggle.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_local_toggled
        )
        activation_group.add(self._local_toggle)

        # Enable for SSH terminals toggle
        self._ssh_toggle = Adw.SwitchRow(
            title=_("SSH Sessions"),
            subtitle=_("Apply output highlighting to SSH connections"),
        )
        self._ssh_toggle.set_active(self._manager.enabled_for_ssh)
        self._ssh_toggle.connect(BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_ssh_toggled)
        activation_group.add(self._ssh_toggle)

    def _setup_cat_colorization_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the cat colorization settings group using Pygments."""
        import importlib.util

        pygments_available = importlib.util.find_spec("pygments") is not None

        self._create_cat_group(page, pygments_available)
        settings = get_settings_manager()

        if pygments_available:
            self._add_cat_experimental_note()

        self._add_cat_colorization_toggle(settings)

        if pygments_available:
            self._add_cat_theme_selectors(settings)
        else:
            self._add_pygments_install_hint()

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

        self._cat_group = Adw.PreferencesGroup(
            title=_("{} Command Colorization").format("cat"),
            description=description,
        )
        page.add(self._cat_group)

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
        self._cat_group.add(note_row)

    def _add_cat_colorization_toggle(self, settings) -> None:
        """Add the cat colorization enable/disable toggle."""
        self._cat_colorization_toggle = Adw.SwitchRow(
            title=_("Enable '{}' Colorization").format("cat"),
            subtitle=_("Apply syntax highlighting to '{}' command output").format(
                "cat"
            ),
        )
        current_enabled = settings.get("cat_colorization_enabled", True)
        self._cat_colorization_toggle.set_active(current_enabled)
        self._cat_colorization_toggle.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_cat_colorization_toggled
        )
        self._cat_group.add(self._cat_colorization_toggle)

    def _add_cat_theme_selectors(self, settings) -> None:
        """Add theme selection widgets for cat colorization."""
        from pygments.styles import get_all_styles

        all_themes = sorted(get_all_styles())
        current_mode = settings.get("cat_theme_mode", "auto")
        current_enabled = settings.get("cat_colorization_enabled", True)

        # Theme mode selector
        self._add_cat_theme_mode_row(settings, current_mode)

        # Get theme lists
        dark_only, light_only = self._get_theme_categories()

        # Dark and light theme selectors
        self._add_cat_dark_theme_row(settings, all_themes, dark_only, light_only)
        self._add_cat_light_theme_row(settings, all_themes, dark_only, light_only)

        # Manual theme selector
        self._add_cat_manual_theme_row(settings, all_themes)

        # Set visibility based on mode
        is_auto_mode = current_mode == "auto"
        self._cat_theme_mode_row.set_visible(current_enabled)
        self._cat_dark_theme_row.set_visible(current_enabled and is_auto_mode)
        self._cat_light_theme_row.set_visible(current_enabled and is_auto_mode)
        self._cat_theme_row.set_visible(current_enabled and not is_auto_mode)

    def _add_cat_theme_mode_row(self, settings, current_mode: str) -> None:
        """Add the theme mode selector row."""
        self._cat_theme_mode_row = self._create_theme_mode_combo_row(
            subtitle=_("Auto: adapts to background color. Manual: single theme."),
            current_mode=current_mode,
            on_changed_callback=self._on_cat_theme_mode_changed,
        )
        self._cat_group.add(self._cat_theme_mode_row)

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
        self._cat_dark_theme_row = Adw.ComboRow(
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
        self._cat_dark_theme_row.set_model(dark_themes_model)
        self._cat_dark_theme_names = dark_themes

        current_dark = settings.get("cat_dark_theme", "monokai")
        try:
            self._cat_dark_theme_row.set_selected(dark_themes.index(current_dark))
        except ValueError:
            self._cat_dark_theme_row.set_selected(0)
        self._cat_dark_theme_row.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self._on_cat_dark_theme_changed
        )
        self._cat_group.add(self._cat_dark_theme_row)
        return dark_themes

    def _add_cat_light_theme_row(
        self, settings, all_themes, dark_only, light_only
    ) -> list:
        """Add light theme selector row."""
        self._cat_light_theme_row = Adw.ComboRow(
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
        self._cat_light_theme_row.set_model(light_themes_model)
        self._cat_light_theme_names = light_themes

        current_light = settings.get("cat_light_theme", "solarized-light")
        try:
            self._cat_light_theme_row.set_selected(light_themes.index(current_light))
        except ValueError:
            self._cat_light_theme_row.set_selected(0)
        self._cat_light_theme_row.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self._on_cat_light_theme_changed
        )
        self._cat_group.add(self._cat_light_theme_row)
        return light_themes

    def _add_cat_manual_theme_row(self, settings, all_themes) -> None:
        """Add manual theme selector row."""
        self._cat_theme_row = Adw.ComboRow(
            title=_("Manual Theme"),
            subtitle=_("Single theme to use in manual mode"),
        )
        manual_themes_model = Gtk.StringList()
        for theme in all_themes:
            manual_themes_model.append(theme)
        self._cat_theme_row.set_model(manual_themes_model)
        self._cat_theme_names = all_themes

        current_theme = settings.get("pygments_theme", "monokai").lower()
        try:
            self._cat_theme_row.set_selected(all_themes.index(current_theme))
        except ValueError:
            self._cat_theme_row.set_selected(0)
        self._cat_theme_row.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self._on_cat_theme_changed
        )
        self._cat_group.add(self._cat_theme_row)

    def _add_pygments_install_hint(self) -> None:
        """Add Pygments installation hint when not available."""
        self._cat_theme_row = None
        self._cat_theme_names = []
        self._cat_theme_mode_row = None
        self._cat_dark_theme_row = None
        self._cat_dark_theme_names = []
        self._cat_light_theme_row = None
        self._cat_light_theme_names = []

        install_row = Adw.ActionRow(
            title=_("Install Pygments"),
            subtitle=_("pip install pygments"),
        )
        install_row.add_css_class("dim-label")
        self._cat_group.add(install_row)

    def _setup_shell_input_highlighting_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the shell input highlighting settings group (experimental)."""
        pygments_available = self._create_shell_input_group(page)
        self._add_shell_input_experimental_note(pygments_available)
        current_enabled = self._add_shell_input_toggle(pygments_available)

        if pygments_available:
            self._add_shell_input_theme_selectors(current_enabled)
        else:
            self._init_shell_input_fallback_attrs()

    def _create_shell_input_group(self, page: Adw.PreferencesPage) -> bool:
        """Create the shell input preferences group and check Pygments availability."""
        import importlib.util

        pygments_available = importlib.util.find_spec("pygments") is not None

        description = (
            _("Live syntax highlighting as you type commands (experimental)")
            if pygments_available
            else _("Pygments is not installed - shell input highlighting unavailable")
        )

        self._shell_input_group = Adw.PreferencesGroup(
            title=_("Shell Input Highlighting"),
            description=description,
        )
        page.add(self._shell_input_group)
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
        self._shell_input_group.add(note_row)

    def _add_shell_input_toggle(self, pygments_available: bool) -> bool:
        """Add the shell input highlighting toggle switch."""
        settings = get_settings_manager()

        self._shell_input_toggle = Adw.SwitchRow(
            title=_("Enable Shell Input Highlighting"),
            subtitle=_(
                "Color shell commands as you type them. Useful for SSH sessions and "
                "Docker containers where shell configuration cannot be changed."
            ),
        )
        current_enabled = settings.get("shell_input_highlighting_enabled", False)
        self._shell_input_toggle.set_active(current_enabled)
        self._shell_input_toggle.set_sensitive(pygments_available)
        self._shell_input_toggle.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_shell_input_highlighting_toggled
        )
        self._shell_input_group.add(self._shell_input_toggle)
        return current_enabled

    def _add_shell_input_theme_selectors(self, current_enabled: bool) -> None:
        """Add all theme selector rows for shell input highlighting."""
        settings = get_settings_manager()
        current_mode = self._add_shell_input_mode_row(settings)
        all_themes = self._get_shell_input_theme_categories()
        dark_themes, light_themes = all_themes

        self._add_shell_input_dark_theme_row(settings, dark_themes)
        self._add_shell_input_light_theme_row(settings, dark_themes, light_themes)
        self._add_shell_input_manual_theme_row(settings)
        self._update_shell_input_theme_visibility(current_enabled, current_mode)

    def _add_shell_input_mode_row(self, settings) -> str:
        """Add the theme mode selector row (auto/manual)."""
        current_mode = settings.get("shell_input_theme_mode", "auto")
        self._theme_mode_row = self._create_theme_mode_combo_row(
            subtitle=_("Auto detects background, Manual uses selected theme"),
            current_mode=current_mode,
            on_changed_callback=self._on_shell_input_mode_changed,
        )
        self._shell_input_group.add(self._theme_mode_row)
        return current_mode

    def _get_shell_input_theme_categories(self) -> tuple[list[str], list[str]]:
        """Get categorized dark and light theme lists from Pygments."""
        from pygments.styles import get_all_styles

        all_themes = sorted(get_all_styles())

        dark_only, light_only = self._get_theme_categories()

        # Build dark themes list
        dark_themes = [t for t in dark_only if t in all_themes]
        for theme in all_themes:
            if theme not in dark_themes and theme not in light_only:
                dark_themes.append(theme)

        # Build light themes list
        light_themes = [t for t in light_only if t in all_themes]
        for theme in all_themes:
            if theme not in light_themes and theme not in dark_only:
                light_themes.append(theme)

        return dark_themes, light_themes

    def _create_theme_mode_combo_row(
        self,
        subtitle: str,
        current_mode: str,
        on_changed_callback: Callable,
    ) -> Adw.ComboRow:
        """Create a theme mode (Auto/Manual) selector ComboRow.

        Args:
            subtitle: Row subtitle describing the mode behavior
            current_mode: Current mode ("auto" or "manual")
            on_changed_callback: Callback for selection changes

        Returns:
            Configured ComboRow for theme mode selection
        """
        row = Adw.ComboRow(title=_("Theme Mode"), subtitle=subtitle)
        model = Gtk.StringList()
        model.append(_("Auto"))
        model.append(_("Manual"))
        row.set_model(model)
        row.set_selected(0 if current_mode == "auto" else 1)
        row.connect(BaseDialog.SIGNAL_NOTIFY_SELECTED, on_changed_callback)
        return row

    def _create_theme_combo_row(
        self,
        title: str,
        subtitle: str,
        themes: list[str],
        current_theme: str,
        on_changed_callback: Callable,
    ) -> tuple[Adw.ComboRow, list[str]]:
        """Create a theme selector ComboRow.

        Args:
            title: Row title
            subtitle: Row subtitle
            themes: List of theme names
            current_theme: Currently selected theme name
            on_changed_callback: Callback for selection changes

        Returns:
            Tuple of (ComboRow, theme_names_list)
        """
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

    def _add_shell_input_dark_theme_row(self, settings, dark_themes: list[str]) -> None:
        """Add the dark background theme selector row."""
        current_dark = settings.get("shell_input_dark_theme", "monokai")
        self._dark_theme_row, self._dark_theme_names = self._create_theme_combo_row(
            title=_("Dark Background Theme"),
            subtitle=_("Theme used when background is dark"),
            themes=dark_themes,
            current_theme=current_dark,
            on_changed_callback=self._on_dark_theme_changed,
        )
        self._shell_input_group.add(self._dark_theme_row)

    def _add_shell_input_light_theme_row(
        self, settings, dark_themes: list[str], light_themes: list[str]
    ) -> None:
        """Add the light background theme selector row."""
        current_light = settings.get("shell_input_light_theme", "solarized-light")
        self._light_theme_row, self._light_theme_names = self._create_theme_combo_row(
            title=_("Light Background Theme"),
            subtitle=_("Theme used when background is light"),
            themes=light_themes,
            current_theme=current_light,
            on_changed_callback=self._on_light_theme_changed,
        )
        self._shell_input_group.add(self._light_theme_row)

    def _add_shell_input_manual_theme_row(self, settings) -> None:
        """Add the manual theme selector row (legacy mode)."""
        from pygments.styles import get_all_styles

        all_themes = sorted(get_all_styles())
        current_theme = settings.get("shell_input_pygments_theme", "monokai").lower()

        self._shell_input_theme_row, self._shell_input_theme_names = (
            self._create_theme_combo_row(
                title=_("Manual Theme"),
                subtitle=_("Single theme to use in manual mode"),
                themes=all_themes,
                current_theme=current_theme,
                on_changed_callback=self._on_shell_input_theme_changed,
            )
        )
        self._shell_input_group.add(self._shell_input_theme_row)

    def _update_shell_input_theme_visibility(
        self, current_enabled: bool, current_mode: str
    ) -> None:
        """Update visibility of theme selector rows based on settings."""
        is_auto = current_mode == "auto"
        self._dark_theme_row.set_visible(current_enabled and is_auto)
        self._light_theme_row.set_visible(current_enabled and is_auto)
        self._shell_input_theme_row.set_visible(current_enabled and not is_auto)
        self._theme_mode_row.set_visible(current_enabled)

    def _init_shell_input_fallback_attrs(self) -> None:
        """Initialize fallback attributes when Pygments is not available."""
        self._shell_input_theme_row = None
        self._shell_input_theme_names = []
        self._theme_mode_row = None
        self._dark_theme_row = None
        self._light_theme_row = None

    def _on_shell_input_highlighting_toggled(
        self, switch: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle shell input highlighting toggle changes."""
        enabled = switch.get_active()
        settings = get_settings_manager()
        settings.set("shell_input_highlighting_enabled", enabled)

        # Update all related row visibility
        is_auto = settings.get("shell_input_theme_mode", "auto") == "auto"
        if self._theme_mode_row:
            self._theme_mode_row.set_visible(enabled)
        if self._dark_theme_row:
            self._dark_theme_row.set_visible(enabled and is_auto)
        if self._light_theme_row:
            self._light_theme_row.set_visible(enabled and is_auto)
        if self._shell_input_theme_row:
            self._shell_input_theme_row.set_visible(enabled and not is_auto)

        # Refresh the shell input highlighter
        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input highlighter: {e}")

        self.logger.debug(
            f"Shell input highlighting {'enabled' if enabled else 'disabled'}"
        )

    def _on_shell_input_mode_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle theme mode changes (auto/manual)."""
        idx = combo_row.get_selected()
        is_auto = idx == 0
        mode = "auto" if is_auto else "manual"

        settings = get_settings_manager()
        settings.set("shell_input_theme_mode", mode)

        # Update row visibility
        if self._dark_theme_row:
            self._dark_theme_row.set_visible(is_auto)
        if self._light_theme_row:
            self._light_theme_row.set_visible(is_auto)
        if self._shell_input_theme_row:
            self._shell_input_theme_row.set_visible(not is_auto)

        # Refresh highlighter
        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input highlighter: {e}")

        self.logger.debug(f"Shell input theme mode changed to: {mode}")

    def _on_dark_theme_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle dark theme selection changes."""
        idx = combo_row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and self._dark_theme_names:
            if idx < len(self._dark_theme_names):
                theme = self._dark_theme_names[idx]
                settings = get_settings_manager()
                settings.set("shell_input_dark_theme", theme)

                # Refresh highlighter
                try:
                    from ...terminal.highlighter import get_shell_input_highlighter

                    highlighter = get_shell_input_highlighter()
                    highlighter.refresh_settings()
                except Exception as e:
                    self.logger.warning(
                        f"Failed to refresh shell input highlighter: {e}"
                    )

                self.logger.debug(f"Shell input dark theme changed to: {theme}")

    def _on_light_theme_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle light theme selection changes."""
        idx = combo_row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and self._light_theme_names:
            if idx < len(self._light_theme_names):
                theme = self._light_theme_names[idx]
                settings = get_settings_manager()
                settings.set("shell_input_light_theme", theme)

                # Refresh highlighter
                try:
                    from ...terminal.highlighter import get_shell_input_highlighter

                    highlighter = get_shell_input_highlighter()
                    highlighter.refresh_settings()
                except Exception as e:
                    self.logger.warning(
                        f"Failed to refresh shell input highlighter: {e}"
                    )

                self.logger.debug(f"Shell input light theme changed to: {theme}")

    def _on_shell_input_theme_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle shell input color theme changes (manual mode)."""
        idx = combo_row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and hasattr(
            self, "_shell_input_theme_names"
        ):
            theme_names = self._shell_input_theme_names
            if idx < len(theme_names):
                theme = theme_names[idx]
                settings = get_settings_manager()
                settings.set("shell_input_pygments_theme", theme)

                # Refresh the shell input highlighter with new theme
                try:
                    from ...terminal.highlighter import get_shell_input_highlighter

                    highlighter = get_shell_input_highlighter()
                    highlighter.refresh_settings()
                except Exception as e:
                    self.logger.warning(
                        f"Failed to refresh shell input highlighter: {e}"
                    )

                self.logger.debug(f"Shell input theme changed to: {theme}")

    def _setup_ignored_commands_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the ignored commands group as a collapsible section."""
        self._ignored_commands_group = Adw.PreferencesGroup()
        page.add(self._ignored_commands_group)

        # Main expander row that contains all ignored commands
        self._ignored_expander = Adw.ExpanderRow(
            title=_("Ignored Commands"),
        )
        self._ignored_expander.set_enable_expansion(True)
        self._ignored_expander.set_expanded(False)  # Collapsed by default

        # Add icon prefix
        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.set_opacity(0.6)
        self._ignored_expander.add_prefix(icon)

        self._ignored_commands_group.add(self._ignored_expander)

        # Restore defaults row (inside expander, at the top)
        restore_row = Adw.ActionRow(
            title=_("Restore Defaults"),
        )
        restore_row.set_activatable(True)
        restore_btn = create_icon_button(
            "view-refresh-symbolic",
            on_clicked=self._on_restore_ignored_defaults_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        restore_row.add_suffix(restore_btn)
        restore_row.set_activatable_widget(restore_btn)
        self._ignored_expander.add_row(restore_row)

        # Add command button (inside expander) - prominent style
        add_cmd_row = Adw.ActionRow(
            title=_("➕ Add Ignored Command"),
        )
        add_cmd_row.set_activatable(True)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_ignored_command_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
        )
        add_cmd_row.add_suffix(add_btn)
        add_cmd_row.set_activatable_widget(add_btn)
        self._add_ignored_cmd_row = add_cmd_row
        self._ignored_expander.add_row(add_cmd_row)

        # Container for command rows inside the expander
        self._ignored_command_rows: dict[str, Adw.ActionRow] = {}

        # Populate initial list (after add button)
        self._populate_ignored_commands()

    def _populate_ignored_commands(self) -> None:
        """Populate the ignored commands list from settings."""
        # Clear existing rows from expander (but keep the add button)
        for row in self._ignored_command_rows.values():
            self._ignored_expander.remove(row)
        self._ignored_command_rows.clear()

        # Get current ignored commands
        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])

        # Sort and add rows to expander (after the add button)
        for cmd in sorted(ignored_commands):
            row = self._create_ignored_command_row(cmd)
            self._ignored_command_rows[cmd] = row
            self._ignored_expander.add_row(row)

        # Update expander subtitle with count
        count = len(ignored_commands)
        self._ignored_expander.set_subtitle(
            _("{count} command(s) • Click to expand/collapse").format(count=count)
        )

    def _create_ignored_command_row(self, cmd: str) -> Adw.ActionRow:
        """Create a row for an ignored command with remove button."""
        row = Adw.ActionRow(title=cmd)
        remove_btn = create_icon_button(
            "user-trash-symbolic",
            tooltip=_("Remove from ignored list"),
            on_clicked=self._on_remove_ignored_command,
            callback_args=(cmd,),
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        row.add_suffix(remove_btn)
        return row

    def _on_add_ignored_command_clicked(self, button: Gtk.Button) -> None:
        """Handle add ignored command button click."""
        dialog = AddIgnoredCommandDialog(self)
        dialog.connect("command-added", self._on_ignored_command_added)
        dialog.present(self)

    def _on_ignored_command_added(self, dialog, command: str) -> None:
        """Handle new ignored command added."""
        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])

        if command not in ignored_commands:
            ignored_commands.append(command)
            ignored_commands.sort()
            settings.set("ignored_highlight_commands", ignored_commands)

            # Refresh highlighter's ignored commands cache
            from ...terminal.highlighter import get_output_highlighter

            get_output_highlighter().refresh_ignored_commands()

            self._populate_ignored_commands()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Command added: {}").format(command)))

    def _on_remove_ignored_command(self, button: Gtk.Button, command: str) -> None:
        """Handle remove ignored command button click - show confirmation."""

        def on_confirm() -> None:
            settings = get_settings_manager()
            ignored_commands = settings.get("ignored_highlight_commands", [])

            if command in ignored_commands:
                ignored_commands.remove(command)
                settings.set("ignored_highlight_commands", ignored_commands)

                # Refresh highlighter's ignored commands cache
                from ...terminal.highlighter import get_output_highlighter

                get_output_highlighter().refresh_ignored_commands()

                self._populate_ignored_commands()
                self.emit("settings-changed")
                self.add_toast(
                    Adw.Toast(title=_("Command removed: {}").format(command))
                )

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Remove Ignored Command?"),
            body=_(
                'Remove "{}" from the ignored commands list? Highlighting will be applied to this command\'s output.'
            ).format(command),
            on_confirm=on_confirm,
            delete_label=_("Remove"),
        )

    def _on_restore_ignored_defaults_clicked(self, button: Gtk.Button) -> None:
        """Handle restore defaults button click for ignored commands."""

        def on_confirm() -> None:
            from ...settings.config import DefaultSettings

            default_ignored = DefaultSettings.get_defaults().get(
                "ignored_highlight_commands", []
            )

            settings = get_settings_manager()
            settings.set("ignored_highlight_commands", list(default_ignored))

            # Refresh highlighter's ignored commands cache
            from ...terminal.highlighter import get_output_highlighter

            get_output_highlighter().refresh_ignored_commands()

            self._populate_ignored_commands()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Restored default ignored commands")))

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Restore Default Ignored Commands?"),
            body=_(
                "This will replace your current ignored commands list with the system defaults. Custom additions will be lost."
            ),
            on_confirm=on_confirm,
            delete_label=_("Restore Defaults"),
        )

    def _setup_context_settings_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context-aware settings group."""
        context_settings_group = Adw.PreferencesGroup(
            title=_("Per-Command Highlighting"),
        )
        page.add(context_settings_group)

        # Enable context-aware highlighting toggle
        self._context_aware_toggle = Adw.SwitchRow(
            title=_("Enable Command Detection"),
        )
        self._context_aware_toggle.set_active(self._manager.context_aware_enabled)
        self._context_aware_toggle.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_context_aware_toggled
        )
        context_settings_group.add(self._context_aware_toggle)

    def _setup_context_selector_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context selector group with bulk actions."""
        self._context_selector_group = Adw.PreferencesGroup(
            title=_("Commands"),
        )
        page.add(self._context_selector_group)

        # ADD NEW CONTEXT - prominent at the top
        add_context_row = Adw.ActionRow(
            title=_("➕ Add Command"),
        )
        add_context_row.set_activatable(True)
        add_context_row.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_context_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
        )
        add_context_row.add_suffix(add_btn)
        add_context_row.set_activatable_widget(add_btn)
        self._add_context_row = add_context_row
        self._context_selector_group.add(add_context_row)

        # Bulk action buttons
        bulk_actions_row = Adw.ActionRow(
            title=_("Bulk Actions"),
        )

        enable_all_btn = Gtk.Button(label=_("Enable All"))
        enable_all_btn.set_valign(Gtk.Align.CENTER)
        enable_all_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        enable_all_btn.connect("clicked", self._on_enable_all_contexts)
        bulk_actions_row.add_suffix(enable_all_btn)

        disable_all_btn = Gtk.Button(label=_("Disable All"))
        disable_all_btn.set_valign(Gtk.Align.CENTER)
        disable_all_btn.add_css_class("destructive-action")
        disable_all_btn.connect("clicked", self._on_disable_all_contexts)
        bulk_actions_row.add_suffix(disable_all_btn)

        self._context_selector_group.add(bulk_actions_row)

        # Reset all contexts button
        reset_contexts_row = Adw.ActionRow(
            title=_("Reset All Commands"),
        )
        reset_contexts_row.set_activatable(True)
        reset_contexts_btn = create_icon_button(
            "view-refresh-symbolic",
            on_clicked=self._on_reset_all_contexts_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        reset_contexts_row.add_suffix(reset_contexts_btn)
        reset_contexts_row.set_activatable_widget(reset_contexts_btn)
        self._context_selector_group.add(reset_contexts_row)

        # Scrolled container for context list
        self._context_list_group = Adw.PreferencesGroup(
            title=_("Available Commands"),
        )
        page.add(self._context_list_group)

        # Context rows will be added dynamically
        self._context_rows: dict[str, Adw.ActionRow] = {}

    def _setup_context_rules_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context rules list group with reorder support."""
        self._context_rules_group = Adw.PreferencesGroup(
            title=_("Command Rules"),
            description=_("Rules specific to the selected command. Order matters!"),
        )
        page.add(self._context_rules_group)

        # Context header with enable/settings
        self._context_header_row = Adw.ActionRow(
            title=_("No command selected"),
            subtitle=_("Select a command from the list above"),
        )
        self._context_rules_group.add(self._context_header_row)

        # Context enable toggle
        self._context_enable_row = Adw.SwitchRow(
            title=_("Enable Command Rules"),
            subtitle=_("Apply rules when this command is detected"),
        )
        self._context_enable_row.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_context_enable_toggled
        )
        self._context_rules_group.add(self._context_enable_row)

        # Use global rules toggle
        self._use_global_rules_row = Adw.SwitchRow(
            title=_("Include Global Rules"),
            subtitle=_("Also apply global rules alongside command-specific rules"),
        )
        self._use_global_rules_row.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_use_global_rules_toggled
        )
        self._context_rules_group.add(self._use_global_rules_row)

        # Reset to default button
        self._reset_context_row = Adw.ActionRow(
            title=_("Reset to System Default"),
            subtitle=_("Remove user customization and revert to system rules"),
        )
        self._reset_context_row.set_activatable(True)
        reset_btn = create_icon_button(
            "edit-undo-symbolic",
            on_clicked=self._on_reset_context_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        self._reset_context_row.add_suffix(reset_btn)
        self._reset_context_row.set_activatable_widget(reset_btn)
        self._reset_context_row.set_sensitive(False)
        self._context_rules_group.add(self._reset_context_row)

        # Rules list group (separate for better organization)
        self._context_rules_list_group = Adw.PreferencesGroup(
            title=_("Rules (in execution order)"),
            description=_(
                "Use arrows to reorder rules. Rules are matched from top to bottom."
            ),
        )
        page.add(self._context_rules_list_group)

        # Add rule to context button - make it prominent
        add_rule_row = Adw.ActionRow(
            title=_("➕ Add Rule to This Command"),
            subtitle=_("Create a new highlighting pattern for the selected command"),
        )
        add_rule_row.set_activatable(True)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_context_rule_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
        )
        add_rule_row.add_suffix(add_btn)
        add_rule_row.set_activatable_widget(add_btn)
        self._add_context_rule_row = add_rule_row
        self._context_rules_list_group.add(add_rule_row)

    def _setup_rules_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the global rules list group."""
        self._rules_group = Adw.PreferencesGroup(
            title=_("Global Highlight Rules"),
        )
        page.add(self._rules_group)

        # Add rule button - make it more prominent
        add_row = Adw.ActionRow(
            title=_("➕ Add New Global Rule"),
        )
        add_row.set_activatable(True)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_rule_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
        )
        add_row.add_suffix(add_btn)
        add_row.set_activatable_widget(add_btn)
        self._rules_group.add(add_row)

        # Reset global rules button
        reset_row = Adw.ActionRow(
            title=_("Reset Global Rules"),
        )
        reset_row.set_activatable(True)
        reset_btn = create_icon_button(
            "view-refresh-symbolic",
            on_clicked=self._on_reset_global_rules_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        reset_row.add_suffix(reset_btn)
        reset_row.set_activatable_widget(reset_btn)
        self._rules_group.add(reset_row)

    def _load_settings(self) -> None:
        """Load current settings from the manager."""
        self._local_toggle.set_active(self._manager.enabled_for_local)
        self._ssh_toggle.set_active(self._manager.enabled_for_ssh)
        self._context_aware_toggle.set_active(self._manager.context_aware_enabled)

        # Load cat colorization settings
        settings = get_settings_manager()

        # Load cat colorization enabled state
        cat_enabled = settings.get("cat_colorization_enabled", True)
        self._cat_colorization_toggle.set_active(cat_enabled)

        # Load cat theme settings
        self._load_cat_theme_settings(settings, cat_enabled)

        self._populate_rules()
        self._populate_contexts()

    def _load_cat_theme_settings(self, settings, cat_enabled: bool) -> None:
        """Load cat theme-related settings."""
        if self._cat_theme_mode_row is None:
            return

        current_mode = settings.get("cat_theme_mode", "auto")
        self._cat_theme_mode_row.set_selected(0 if current_mode == "auto" else 1)
        is_auto_mode = current_mode == "auto"

        # Update dark theme selection
        if self._cat_dark_theme_row is not None:
            current_dark = settings.get("cat_dark_theme", "monokai")
            try:
                dark_idx = self._cat_dark_theme_names.index(current_dark)
                self._cat_dark_theme_row.set_selected(dark_idx)
            except ValueError:
                self._cat_dark_theme_row.set_selected(0)
            self._cat_dark_theme_row.set_visible(cat_enabled and is_auto_mode)

        # Update light theme selection
        if self._cat_light_theme_row is not None:
            current_light = settings.get("cat_light_theme", "solarized-light")
            try:
                light_idx = self._cat_light_theme_names.index(current_light)
                self._cat_light_theme_row.set_selected(light_idx)
            except ValueError:
                self._cat_light_theme_row.set_selected(0)
            self._cat_light_theme_row.set_visible(cat_enabled and is_auto_mode)

        # Update manual theme selection
        if self._cat_theme_row is not None:
            current_theme = settings.get("pygments_theme", "monokai").lower()
            try:
                theme_index = self._cat_theme_names.index(current_theme)
                self._cat_theme_row.set_selected(theme_index)
            except ValueError:
                self._cat_theme_row.set_selected(0)
            self._cat_theme_row.set_visible(cat_enabled and not is_auto_mode)
            self._cat_theme_row.set_sensitive(cat_enabled)

        self._cat_theme_mode_row.set_visible(cat_enabled)

    def _populate_contexts(self) -> None:
        """Populate the context list with toggle rows for each context."""
        # Clear existing context rows
        for row in self._context_rows.values():
            self._context_list_group.remove(row)
        self._context_rows.clear()

        # Get all contexts sorted by name
        context_names = sorted(self._manager.get_context_names())

        # Count enabled contexts
        enabled_count = sum(
            1
            for name in context_names
            if (ctx := self._manager.get_context(name)) is not None and ctx.enabled
        )

        # Update selector group description
        self._context_selector_group.set_description(
            _("{total} command(s), {enabled} enabled").format(
                total=len(context_names), enabled=enabled_count
            )
        )

        # Create a row for each context
        for name in context_names:
            ctx = self._manager.get_context(name)
            if not ctx:
                continue

            row = self._create_context_list_row(name, ctx)
            self._context_rows[name] = row
            self._context_list_group.add(row)

    def _create_context_list_row(self, name: str, ctx) -> Adw.ActionRow:
        """Create a row for a context in the list with inline edit, delete, switch buttons."""
        rule_count = len(ctx.rules)

        trigger_info = ", ".join(ctx.triggers)
        row = Adw.ActionRow(
            title=trigger_info,
            subtitle=_("{count} rules").format(count=rule_count),
        )
        row.set_activatable(False)  # Not clickable as full row

        # Terminal icon prefix (uses bundled icon)
        icon = icon_image("utilities-terminal-symbolic")
        icon.set_opacity(0.6)
        row.add_prefix(icon)

        # Edit button (icon)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(edit_btn, _("Edit command rules"))
        edit_btn.connect("clicked", self._on_edit_context_clicked, name)
        row.add_suffix(edit_btn)

        # Delete button (icon) - only for user-modified contexts
        if self._manager.has_user_context_override(name):
            delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
            delete_btn.add_css_class("flat")
            delete_btn.set_valign(Gtk.Align.CENTER)
            get_tooltip_helper().add_tooltip(delete_btn, _("Delete command"))
            delete_btn.connect("clicked", self._on_delete_context_clicked, name)
            row.add_suffix(delete_btn)

        # Enable/disable switch (rightmost)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(ctx.enabled)
        switch.connect("state-set", self._on_context_toggle, name)
        row.add_suffix(switch)

        # Store switch reference for later updates
        row._context_switch = switch

        return row

    def _on_edit_context_clicked(self, button: Gtk.Button, context_name: str) -> None:
        """Handle edit context button click."""
        self._open_context_dialog(context_name)

    def _on_delete_context_clicked(self, button: Gtk.Button, context_name: str) -> None:
        """Handle delete context button click."""
        ctx = self._manager.get_context(context_name)
        if not ctx or not self._manager.has_user_context_override(context_name):
            return

        def on_confirm() -> None:
            if self._manager.delete_user_context(context_name):
                self._populate_contexts()
                self.emit("settings-changed")
                self.add_toast(
                    Adw.Toast(title=_("Command deleted: {}").format(context_name))
                )

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Delete Command?"),
            body=_(
                'Are you sure you want to delete "{}"? This will remove all custom rules for this command.'
            ).format(context_name),
            on_confirm=on_confirm,
        )

    def _on_context_toggle(
        self, switch: Gtk.Switch, state: bool, context_name: str
    ) -> bool:
        """Handle context toggle from the list."""
        self._manager.set_context_enabled(context_name, state)
        self._manager.save_config()

        # Update the description count
        context_names = self._manager.get_context_names()
        enabled_count = sum(
            1
            for name in context_names
            if (ctx := self._manager.get_context(name)) is not None and ctx.enabled
        )
        self._context_selector_group.set_description(
            _("{total} command(s), {enabled} enabled").format(
                total=len(context_names), enabled=enabled_count
            )
        )

        # If this is the selected context, update its detail view
        if context_name == self._selected_context:
            self._context_enable_row.handler_block_by_func(
                self._on_context_enable_toggled
            )
            self._context_enable_row.set_active(state)
            self._context_enable_row.handler_unblock_by_func(
                self._on_context_enable_toggled
            )

        self.emit("settings-changed")
        return False  # Don't block the default handler

    def _on_context_row_activated(self, row: Adw.ActionRow, context_name: str) -> None:
        """Handle context row activation - open context rules dialog."""
        self._open_context_dialog(context_name)

    def _open_context_dialog(self, context_name: str) -> None:
        """Open the context rules dialog for a specific context."""
        dialog = ContextRulesDialog(self, context_name)
        dialog.connect("context-updated", self._on_context_dialog_updated)
        dialog.present()

    def _on_context_dialog_updated(self, dialog) -> None:
        """Handle updates from the context rules dialog."""
        self._populate_contexts()
        self.emit("settings-changed")

    def _select_context(self, context_name: str) -> None:
        """Select a context for editing."""
        self._selected_context = context_name

        # Update visual selection (highlight the selected row)
        for name, row in self._context_rows.items():
            if name == context_name:
                row.add_css_class("accent")
            else:
                row.remove_css_class("accent")

        # Enable reset button
        self._reset_context_row.set_sensitive(bool(self._selected_context))

        # Update the context rules section
        self._populate_context_rules()

    def _on_enable_all_contexts(self, button: Gtk.Button) -> None:
        """Enable all contexts."""
        for name in self._manager.get_context_names():
            self._manager.set_context_enabled(name, True)
        self._manager.save_config()
        self._populate_contexts()
        self.emit("settings-changed")

    def _on_disable_all_contexts(self, button: Gtk.Button) -> None:
        """Disable all contexts."""
        for name in self._manager.get_context_names():
            self._manager.set_context_enabled(name, False)
        self._manager.save_config()
        self._populate_contexts()
        self.emit("settings-changed")

    def _populate_context_rules(self) -> None:
        """Populate rules for the selected context."""
        # Clear existing context rule rows
        for row in self._context_rule_rows:
            self._context_rules_list_group.remove(row)
        self._context_rule_rows.clear()

        if not self._selected_context:
            self._context_enable_row.set_sensitive(False)
            self._use_global_rules_row.set_sensitive(False)
            self._add_context_rule_row.set_sensitive(False)
            self._reset_context_row.set_sensitive(False)
            # Update header
            self._context_header_row.set_title(_("No command selected"))
            self._context_header_row.set_subtitle(
                _("Select a command from the list above")
            )
            self._context_rules_list_group.set_description(
                _("Select a command to view its rules")
            )
            return

        self._context_enable_row.set_sensitive(True)
        self._use_global_rules_row.set_sensitive(True)
        self._add_context_rule_row.set_sensitive(True)
        self._reset_context_row.set_sensitive(True)

        # Get context
        context = self._manager.get_context(self._selected_context)
        if not context:
            self._context_header_row.set_title(self._selected_context)
            self._context_header_row.set_subtitle(_("Command not found"))
            return

        # Update header with context info
        trigger_info = ", ".join(context.triggers[:3])
        if len(context.triggers) > 3:
            trigger_info += "..."
        self._context_header_row.set_title(self._selected_context)
        self._context_header_row.set_subtitle(
            _("Triggers: {triggers}").format(triggers=trigger_info)
        )

        # Update rules group description
        status = _("Enabled") if context.enabled else _("Disabled")
        rule_count = len(context.rules)
        self._context_rules_list_group.set_description(
            _("{count} rule(s) • {status} • Use arrows to reorder").format(
                count=rule_count, status=status
            )
        )

        # Block signal handler during programmatic update
        self._context_enable_row.handler_block_by_func(self._on_context_enable_toggled)
        self._context_enable_row.set_active(context.enabled)
        self._context_enable_row.handler_unblock_by_func(
            self._on_context_enable_toggled
        )

        # Update use global rules toggle
        self._use_global_rules_row.handler_block_by_func(
            self._on_use_global_rules_toggled
        )
        self._use_global_rules_row.set_active(context.use_global_rules)
        self._use_global_rules_row.handler_unblock_by_func(
            self._on_use_global_rules_toggled
        )

        # Add rule rows for this context with reorder buttons
        for index, rule in enumerate(context.rules):
            row = self._create_context_rule_row(rule, index, len(context.rules))
            self._context_rules_list_group.add(row)
            self._context_rule_rows.append(row)

    def _get_rule_color_display(self, rule: HighlightRule) -> str:
        """Get the first color from a rule for display."""
        if rule.colors and rule.colors[0]:
            return self._manager.resolve_color(rule.colors[0])
        return "#ffffff"

    def _create_context_rule_row(
        self, rule: HighlightRule, index: int, total_rules: int = 0
    ) -> Adw.ExpanderRow:
        """Create an expander row for a context-specific rule with reorder buttons."""
        # Escape markup characters to prevent GTK parsing errors
        escaped_name = GLib.markup_escape_text(rule.name)
        row = Adw.ExpanderRow()
        row.set_title(f"#{index + 1} {escaped_name}")
        row.set_subtitle(GLib.markup_escape_text(get_rule_subtitle(rule)))

        # Reorder buttons prefix (box with up/down arrows)
        reorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        reorder_box.set_valign(Gtk.Align.CENTER)
        reorder_box.set_margin_end(4)

        up_btn = Gtk.Button(icon_name="go-up-symbolic")
        up_btn.add_css_class("flat")
        up_btn.add_css_class("circular")
        up_btn.set_size_request(24, 24)
        up_btn.set_sensitive(index > 0)
        up_btn.connect("clicked", self._on_move_rule_up, index)
        get_tooltip_helper().add_tooltip(up_btn, _("Move up"))
        reorder_box.append(up_btn)

        down_btn = Gtk.Button(icon_name="go-down-symbolic")
        down_btn.add_css_class("flat")
        down_btn.add_css_class("circular")
        down_btn.set_size_request(24, 24)
        down_btn.set_sensitive(index < total_rules - 1)
        down_btn.connect("clicked", self._on_move_rule_down, index)
        get_tooltip_helper().add_tooltip(down_btn, _("Move down"))
        reorder_box.append(down_btn)

        row.add_prefix(reorder_box)

        # Color indicator (shows first color)
        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        self._apply_color_to_box(color_box, self._get_rule_color_display(rule))
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)

        # Colors count badge
        if rule.colors and len(rule.colors) > 1:
            colors_badge = Gtk.Label(label=f"{len(rule.colors)}")
            colors_badge.add_css_class("dim-label")
            colors_badge.set_valign(Gtk.Align.CENTER)
            get_tooltip_helper().add_tooltip(
                colors_badge, _("{} colors").format(len(rule.colors))
            )
            row.add_suffix(colors_badge)

        # Enable/disable switch suffix
        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_context_rule_switch_toggled, index
        )
        row.add_suffix(switch)

        # Expanded content - action buttons
        actions_row = Adw.ActionRow(title=_("Actions"))

        edit_btn = Gtk.Button(label=_("Edit"))
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.connect("clicked", self._on_edit_context_rule_clicked, index)
        actions_row.add_suffix(edit_btn)

        delete_btn = Gtk.Button(label=_("Delete"))
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", self._on_delete_context_rule_clicked, index)
        actions_row.add_suffix(delete_btn)

        row.add_row(actions_row)

        return row

    def _on_move_rule_up(self, button: Gtk.Button, index: int) -> None:
        """Move a rule up in the order."""
        if not self._selected_context or index <= 0:
            return
        self._manager.move_context_rule(self._selected_context, index, index - 1)
        self._manager.save_config()
        self._populate_context_rules()
        self.emit("settings-changed")

    def _on_move_rule_down(self, button: Gtk.Button, index: int) -> None:
        """Move a rule down in the order."""
        if not self._selected_context:
            return
        ctx = self._manager.get_context(self._selected_context)
        if not ctx or index >= len(ctx.rules) - 1:
            return
        self._manager.move_context_rule(self._selected_context, index, index + 1)
        self._manager.save_config()
        self._populate_context_rules()
        self.emit("settings-changed")

    def _on_context_aware_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle context-aware toggle."""
        self._manager.context_aware_enabled = switch.get_active()
        self._manager.save_config()
        self.emit("settings-changed")

    def _on_context_enable_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle context enable toggle from detail view."""
        if self._selected_context:
            state = switch.get_active()
            self._manager.set_context_enabled(self._selected_context, state)
            self._manager.save_config()

            # Update the list row switch
            if self._selected_context in self._context_rows:
                row = self._context_rows[self._selected_context]
                # The switch is the first child of prefix
                for child in row:
                    if isinstance(child, Gtk.Switch):
                        child.handler_block_by_func(self._on_context_toggle)
                        child.set_active(state)
                        child.handler_unblock_by_func(self._on_context_toggle)
                        break

            # Update the description count
            context_names = self._manager.get_context_names()
            enabled_count = sum(
                1
                for name in context_names
                if (ctx := self._manager.get_context(name)) is not None and ctx.enabled
            )
            self._context_selector_group.set_description(
                _(
                    "{total} command(s), {enabled} enabled. Click to toggle, select to edit."
                ).format(total=len(context_names), enabled=enabled_count)
            )

            self.emit("settings-changed")

    def _on_use_global_rules_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle use global rules toggle."""
        if self._selected_context:
            self._manager.set_context_use_global_rules(
                self._selected_context, switch.get_active()
            )
            self._manager.save_config()
            self.emit("settings-changed")

    def _on_context_rule_switch_toggled(
        self, switch: Gtk.Switch, _pspec, index: int
    ) -> None:
        """Handle context rule enable/disable toggle."""
        if self._selected_context:
            self._manager.set_context_rule_enabled(
                self._selected_context, index, switch.get_active()
            )
            self._manager.save_config()
            self.emit("settings-changed")

    def _on_add_context_clicked(self, button: Gtk.Button) -> None:
        """Handle add context button click."""
        dialog = ContextNameDialog(self)
        dialog.connect("context-created", self._on_context_created)
        dialog.present(self)

    def _on_context_created(self, dialog, context_name: str) -> None:
        """Handle new context creation."""
        context = HighlightContext(
            command_name=context_name,
            triggers=[context_name],
            rules=[],
            enabled=True,
            description=f"Custom rules for {context_name}",
        )
        self._manager.add_context(context)
        self._manager.save_context_to_user(context)
        self._populate_contexts()

        self.emit("settings-changed")
        self.add_toast(Adw.Toast(title=_("Command created: {}").format(context_name)))

        # Open the dialog for the new context so user can add rules
        self._open_context_dialog(context_name)

    def _on_reset_context_clicked(self, button: Gtk.Button) -> None:
        """Handle reset context to system default button click."""
        if not self._selected_context:
            return

        context_name = self._selected_context

        def on_confirm() -> None:
            if self._manager.delete_user_context(context_name):
                self._populate_contexts()
                self.emit("settings-changed")
                self.add_toast(
                    Adw.Toast(title=_("Command reset: {}").format(context_name))
                )
            else:
                self.add_toast(Adw.Toast(title=_("No user customization to reset")))

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Reset to System Default?"),
            body=_(
                'This will remove your customizations for "{}" and revert to system rules.'
            ).format(context_name),
            on_confirm=on_confirm,
            delete_label=_("Reset"),
        )

    def _on_add_context_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule to context button click."""
        if not self._selected_context:
            return

        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_context_rule_saved)
        dialog.present()

    def _on_context_rule_saved(
        self, dialog: RuleEditDialog, rule: HighlightRule
    ) -> None:
        """Handle saving a new context rule."""
        if self._selected_context:
            self._manager.add_rule_to_context(self._selected_context, rule)
            # Save to user directory to create override
            context = self._manager.get_context(self._selected_context)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule added: {}").format(rule.name)))

    def _on_edit_context_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle edit context rule button click."""
        if not self._selected_context:
            return

        context = self._manager.get_context(self._selected_context)
        if context and 0 <= index < len(context.rules):
            rule = context.rules[index]
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_context_rule_edited, index)
            dialog.present()

    def _on_context_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle saving an edited context rule."""
        if self._selected_context:
            self._manager.update_context_rule(self._selected_context, index, rule)
            # Save to user directory to create override
            context = self._manager.get_context(self._selected_context)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule updated: {}").format(rule.name)))

    def _on_delete_context_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle delete context rule button click."""
        if not self._selected_context:
            return

        context = self._manager.get_context(self._selected_context)
        if not context or index >= len(context.rules):
            return

        rule = context.rules[index]
        rule_name = rule.name
        selected_ctx = self._selected_context

        def on_confirm() -> None:
            self._manager.remove_context_rule(selected_ctx, index)
            # Save to user directory to create override
            ctx = self._manager.get_context(selected_ctx)
            if ctx:
                self._manager.save_context_to_user(ctx)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule deleted: {}").format(rule_name)))

        show_delete_confirmation_dialog(
            parent=self,
            heading=BaseDialog.MSG_DELETE_RULE_HEADING,
            body=BaseDialog.MSG_DELETE_CONFIRMATION.format(rule_name),
            on_confirm=on_confirm,
        )

    def _update_dependent_groups_sensitivity(self) -> None:
        """Update sensitivity of highlighting groups based on activation state.

        When BOTH local and SSH highlighting are disabled, all output-related
        highlighting features should be disabled as well since there's no output to process.
        This includes:
        - Cat colorization group
        - Shell input highlighting group
        - Ignored commands group
        - Global highlight rules group
        - Command-Specific page (entire page)
        """
        any_output_enabled = (
            self._manager.enabled_for_local or self._manager.enabled_for_ssh
        )

        # Update cat group sensitivity
        if self._cat_group is not None:
            self._cat_group.set_sensitive(any_output_enabled)

        # Update shell input group sensitivity
        if self._shell_input_group is not None:
            self._shell_input_group.set_sensitive(any_output_enabled)

        # Update ignored commands group sensitivity
        if self._ignored_commands_group is not None:
            self._ignored_commands_group.set_sensitive(any_output_enabled)

        # Update global rules group sensitivity
        if self._rules_group is not None:
            self._rules_group.set_sensitive(any_output_enabled)

        # Update Command-Specific page sensitivity (entire page)
        if self._context_page is not None:
            self._context_page.set_sensitive(any_output_enabled)

    def _on_local_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle local terminals toggle."""
        is_active = switch.get_active()
        self._manager.enabled_for_local = is_active
        self._manager.save_config()
        self.emit("settings-changed")
        self._update_dependent_groups_sensitivity()

    def _on_ssh_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle SSH terminals toggle."""
        is_active = switch.get_active()
        self._manager.enabled_for_ssh = is_active
        self._manager.save_config()
        self.emit("settings-changed")
        self._update_dependent_groups_sensitivity()

    def _on_cat_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle Pygments theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(self._cat_theme_names):
            theme = self._cat_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("pygments_theme", theme)
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Theme changed to: {}").format(theme)))

    def _on_cat_colorization_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle cat colorization toggle."""
        is_active = switch.get_active()
        settings = get_settings_manager()
        settings.set("cat_colorization_enabled", is_active)

        # Update visibility based on mode and enabled state
        if self._cat_theme_mode_row is not None:
            self._cat_theme_mode_row.set_visible(is_active)
            is_auto_mode = self._cat_theme_mode_row.get_selected() == 0
            if self._cat_dark_theme_row is not None:
                self._cat_dark_theme_row.set_visible(is_active and is_auto_mode)
            if self._cat_light_theme_row is not None:
                self._cat_light_theme_row.set_visible(is_active and is_auto_mode)
            if self._cat_theme_row is not None:
                self._cat_theme_row.set_visible(is_active and not is_auto_mode)
        elif self._cat_theme_row is not None:
            self._cat_theme_row.set_visible(is_active)

        self.emit("settings-changed")

        status = _("enabled") if is_active else _("disabled")
        self.add_toast(Adw.Toast(title=_("'{}' colorization {}").format("cat", status)))

    def _on_cat_theme_mode_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat theme mode change (auto/manual)."""
        selected_index = combo.get_selected()
        is_auto_mode = selected_index == 0
        mode = "auto" if is_auto_mode else "manual"

        settings = get_settings_manager()
        settings.set("cat_theme_mode", mode)

        # Update visibility of theme dropdowns
        if self._cat_dark_theme_row is not None:
            self._cat_dark_theme_row.set_visible(is_auto_mode)
        if self._cat_light_theme_row is not None:
            self._cat_light_theme_row.set_visible(is_auto_mode)
        if self._cat_theme_row is not None:
            self._cat_theme_row.set_visible(not is_auto_mode)

        self.emit("settings-changed")
        mode_name = _("Auto") if is_auto_mode else _("Manual")
        self.add_toast(Adw.Toast(title=_("Cat theme mode: {}").format(mode_name)))

    def _on_cat_dark_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat dark theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(self._cat_dark_theme_names):
            theme = self._cat_dark_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("cat_dark_theme", theme)
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Dark theme: {}").format(theme)))

    def _on_cat_light_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat light theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(self._cat_light_theme_names):
            theme = self._cat_light_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("cat_light_theme", theme)
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Light theme: {}").format(theme)))

    def _show_restart_required_dialog(self) -> None:
        """Show a dialog informing user that restart is required for changes to take effect."""
        dialog = Adw.AlertDialog(
            heading=_("Restart Required"),
            body=_("Restart the program for the colors to be applied to the terminal."),
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present(self)

    def _populate_rules(self) -> None:
        """Populate the global rules list from the manager."""
        # Clear existing rule rows
        for row in self._rule_rows:
            self._rules_group.remove(row)
        self._rule_rows.clear()

        # Add rules
        for index, rule in enumerate(self._manager.rules):
            row = self._create_rule_row(rule, index)
            self._rules_group.add(row)
            self._rule_rows.append(row)

    def _create_rule_row(self, rule: HighlightRule, index: int) -> Adw.ActionRow:
        """Create an action row for a highlight rule with inline edit/delete icons."""
        # Escape markup characters to prevent GTK parsing errors
        escaped_name = GLib.markup_escape_text(rule.name)
        row = Adw.ActionRow()
        row.set_title(escaped_name)
        row.set_subtitle(GLib.markup_escape_text(get_rule_subtitle(rule)))

        # Color indicator prefix (shows first color)
        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        self._apply_color_to_box(color_box, self._get_rule_color_display(rule))
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)

        # Colors count badge
        if rule.colors and len(rule.colors) > 1:
            colors_badge = Gtk.Label(label=f"{len(rule.colors)}")
            colors_badge.add_css_class("dim-label")
            colors_badge.set_valign(Gtk.Align.CENTER)
            get_tooltip_helper().add_tooltip(
                colors_badge, _("{} colors").format(len(rule.colors))
            )
            row.add_suffix(colors_badge)

        # Edit button
        edit_btn = create_icon_button(
            "document-edit-symbolic",
            tooltip=_("Edit rule"),
            on_clicked=self._on_edit_rule_clicked,
            callback_args=(index,),
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        row.add_suffix(edit_btn)

        # Delete button
        delete_btn = create_icon_button(
            "user-trash-symbolic",
            tooltip=_("Delete rule"),
            on_clicked=self._on_delete_rule_clicked,
            callback_args=(index,),
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        row.add_suffix(delete_btn)

        # Enable/disable switch suffix (rightmost)
        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_rule_switch_toggled, index
        )
        row.add_suffix(switch)

        # Store index for reference
        row._rule_index = index
        row._rule_switch = switch
        row._color_box = color_box

        return row

    def _apply_color_to_box(self, box: Gtk.Box, hex_color: str) -> None:
        """Apply a color as background to a box widget."""
        css_provider = Gtk.CssProvider()
        css = f"""
        .rule-color-indicator {{
            background-color: {hex_color};
            border-radius: 50%;
            border: 1px solid alpha(currentColor, 0.3);
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))

        context = box.get_style_context()
        # Store and remove old provider
        if hasattr(box, "_css_provider"):
            context.remove_provider(box._css_provider)

        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box.add_css_class("rule-color-indicator")
        box._css_provider = css_provider

    def _on_rule_switch_toggled(self, switch: Gtk.Switch, _pspec, index: int) -> None:
        """Handle rule enable/disable toggle."""
        self._manager.set_rule_enabled(index, switch.get_active())
        self._manager.save_global_rules_to_user()  # Save full rules to user file
        self._manager.save_config()
        self.emit("settings-changed")

    def _on_add_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule button click."""
        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_new_rule_saved)
        dialog.present()

    def _on_new_rule_saved(self, dialog: RuleEditDialog, rule: HighlightRule) -> None:
        """Handle saving a new rule."""
        self._manager.add_rule(rule)
        self._manager.save_global_rules_to_user()  # Save full rules to user file
        self._manager.save_config()
        self._populate_rules()
        self.emit("settings-changed")

        self.add_toast(Adw.Toast(title=_("Rule added: {}").format(rule.name)))

    def _on_edit_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle edit rule button click."""
        rule = self._manager.get_rule(index)
        if rule:
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_rule_edited, index)
            dialog.present()

    def _on_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle saving an edited rule."""
        self._manager.update_rule(index, rule)
        self._manager.save_global_rules_to_user()  # Save full rules to user file
        self._manager.save_config()
        self._populate_rules()
        self.emit("settings-changed")

        self.add_toast(Adw.Toast(title=_("Rule updated: {}").format(rule.name)))

    def _confirm_delete_rule(
        self, rule_name: str, on_confirm: Callable[[], None]
    ) -> None:
        """Show a confirmation dialog for deleting a rule."""
        show_delete_confirmation_dialog(
            parent=self,
            heading=BaseDialog.MSG_DELETE_RULE_HEADING,
            body=BaseDialog.MSG_DELETE_CONFIRMATION.format(rule_name),
            on_confirm=on_confirm,
        )

    def _on_delete_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle delete rule button click."""
        rule = self._manager.get_rule(index)
        if not rule:
            return

        rule_name = rule.name

        def on_confirm() -> None:
            self._manager.remove_rule(index)
            self._manager.save_global_rules_to_user()  # Save full rules to user file
            self._manager.save_config()
            self._populate_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule deleted: {}").format(rule_name)))

        show_delete_confirmation_dialog(
            parent=self,
            heading=BaseDialog.MSG_DELETE_RULE_HEADING,
            body=BaseDialog.MSG_DELETE_CONFIRMATION.format(rule_name),
            on_confirm=on_confirm,
        )

    def _on_reset_global_rules_clicked(self, button: Gtk.Button) -> None:
        """Handle reset global rules button click."""

        def on_confirm() -> None:
            self._manager.reset_global_rules()
            self._manager.save_config()
            self._populate_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Global rules reset to defaults")))

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Reset Global Rules?"),
            body=_(
                "This will restore global rules to system defaults. Context customizations will be preserved."
            ),
            on_confirm=on_confirm,
            delete_label=_("Reset"),
        )

    def _on_reset_all_contexts_clicked(self, button: Gtk.Button) -> None:
        """Handle reset all contexts button click."""

        def on_confirm() -> None:
            self._manager.reset_all_contexts()
            self._manager.save_config()
            self._populate_contexts()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("All commands reset to defaults")))

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Reset All Commands?"),
            body=_(
                "This will restore all commands to system defaults. Global rules will be preserved."
            ),
            on_confirm=on_confirm,
            delete_label=_("Reset"),
        )
