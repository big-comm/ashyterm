"""Main HighlightDialog for managing syntax highlighting settings.

The heavy lifting is done by delegate classes:
- ColorSchemeDelegate:      Terminal Colors page (scheme list, editor)
- CatColorizationDelegate:  cat command Pygments colorization
- ShellInputDelegate:       Shell input highlighting (experimental)
- GlobalRulesDelegate:      Global highlight rule list CRUD
- ContextDelegate:          Command-specific contexts and rules
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

from ....settings.highlights import get_highlight_manager
from ....settings.manager import get_settings_manager
from ....utils.logger import get_logger
from ....utils.translation_utils import _
from ..base_dialog import BaseDialog, create_icon_button, show_delete_confirmation_dialog
from .cat_colorization_delegate import CatColorizationDelegate
from .color_scheme_delegate import ColorSchemeDelegate
from .context_delegate import ContextDelegate
from .global_rules_delegate import GlobalRulesDelegate
from .shell_input_delegate import ShellInputDelegate
from .small_dialogs import AddIgnoredCommandDialog


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
        """Initialize the highlight dialog."""
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
        self._rule_rows: list = []
        self._context_rule_rows: list = []
        self._selected_context: str = ""
        # UI groups for visibility control (set during _setup_ui)
        self._cat_group: Adw.PreferencesGroup = None  # type: ignore[assignment]
        self._shell_input_group: Adw.PreferencesGroup = None  # type: ignore[assignment]
        self._ignored_commands_group: Adw.PreferencesGroup = None  # type: ignore[assignment]
        self._rules_group: Adw.PreferencesGroup = None  # type: ignore[assignment]
        self._context_page: Adw.PreferencesPage = None  # type: ignore[assignment]
        self._css_provider = None
        self._dark_theme_names: list[str] | None = None
        self._light_theme_names: list[str] | None = None

        self._initializing = True

        # Instantiate delegates
        self._color_scheme = ColorSchemeDelegate(self)
        self._cat_colorization = CatColorizationDelegate(self)
        self._shell_input = ShellInputDelegate(self, self._cat_colorization)
        self._global_rules = GlobalRulesDelegate(self)
        self._context = ContextDelegate(self)

        self.connect("close-request", self._on_close_request)

        self._setup_ui()
        self._load_settings()

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
        """Setup the dialog UI components using delegates."""
        # Page 1: Terminal Colors
        self._terminal_colors_page = Adw.PreferencesPage(
            title=_("Terminal Colors"),
            icon_name="preferences-color-symbolic",
        )
        self.add(self._terminal_colors_page)
        self._color_scheme.setup_page(self._terminal_colors_page)

        # Page 2: Output Highlighting (Global Rules)
        self._global_page = Adw.PreferencesPage(
            title=_("Output Highlighting"),
            icon_name="view-list-symbolic",
        )
        self.add(self._global_page)

        self._setup_welcome_banner(self._global_page)
        self._setup_activation_group(self._global_page)
        self._cat_colorization.setup_group(self._global_page)
        self._shell_input.setup_group(self._global_page)
        self._setup_ignored_commands_group(self._global_page)
        self._global_rules.setup_group(self._global_page)
        self._update_dependent_groups_sensitivity()

        # Page 3: Command-Specific Rules
        self._context_page = Adw.PreferencesPage(
            title=_("Command-Specific"),
            icon_name="utilities-terminal-symbolic",
        )
        self.add(self._context_page)

        self._context.setup_context_settings_group(self._context_page)
        self._context.setup_context_selector_group(self._context_page)

    def _load_settings(self) -> None:
        """Load current settings from the manager."""
        self._local_toggle.set_active(self._manager.enabled_for_local)
        self._ssh_toggle.set_active(self._manager.enabled_for_ssh)
        self._context_aware_toggle.set_active(self._manager.context_aware_enabled)

        settings = get_settings_manager()
        cat_enabled = settings.get("cat_colorization_enabled", True)
        self._cat_colorization_toggle.set_active(cat_enabled)
        self._cat_colorization.load_settings(settings, cat_enabled)

        self._global_rules.populate_rules()
        self._context.populate_contexts()

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
            from ....terminal.highlighter import get_output_highlighter

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
                from ....terminal.highlighter import get_output_highlighter

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
            from ....terminal.highlighter import get_output_highlighter

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

    def _show_restart_required_dialog(self) -> None:
        """Show a dialog informing user that restart is required for changes to take effect."""
        dialog = Adw.AlertDialog(
            heading=_("Restart Required"),
            body=_("Restart the program for the colors to be applied to the terminal."),
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present(self)

