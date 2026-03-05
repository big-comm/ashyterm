"""Command Manager Dialog — main dialog for browsing and managing commands."""

from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ....data.command_manager_models import (
    CommandButton,
    ExecutionMode,
    generate_id,
    get_command_button_manager,
)
from ....settings.manager import SettingsManager
from ....utils.accessibility import set_label as a11y_label
from ....utils.syntax_utils import get_bash_pango_markup
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ...widgets.action_rows import ManagedListRow
from ...widgets.bash_text_view import BashTextView
from ..base_dialog import BaseDialog, show_delete_confirmation_dialog
from .command_button_widget import CommandButtonWidget
from .command_editor_dialog import CommandEditorDialog
from .command_form_dialog import CommandFormDialog


class CommandManagerDialog(Adw.Window):
    """
    Main Command Manager dialog with button-based commands and send-to-all functionality.
    Redesigned for modern UI/UX with search, grid layout, and streamlined interactions.
    """

    __gsignals__ = {
        "command-selected": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str, bool),
        ),  # (command, execute)
    }

    def __init__(
        self, parent_window, settings_manager: Optional[SettingsManager] = None
    ):
        super().__init__(
            transient_for=parent_window,
            modal=False,
        )
        self.add_css_class("ashyterm-dialog")
        self.add_css_class("command-manager-dialog")
        self.parent_window = parent_window
        self.command_manager = get_command_button_manager()
        self._settings_manager = settings_manager

        self._allow_destroy = False
        self._presenting = False
        self._search_filter = ""
        self._all_command_widgets: List[CommandButtonWidget] = []

        # Calculate size based on parent
        parent_width = parent_window.get_width()
        parent_height = parent_window.get_height()
        self.set_default_size(int(parent_width * 0.7), int(parent_height * 0.75))
        self.set_title(_("Command Manager"))

        self._build_ui()
        self._apply_color_scheme()
        self._populate_commands()

        # Connect signals
        self.connect("notify::is-active", self._on_active_changed)
        self.connect("close-request", self._on_close_request)

        if parent_window:
            parent_window.connect("destroy", self._on_parent_destroyed)

        # Keyboard handler
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def present(self):
        self._presenting = True
        super().present()
        GLib.idle_add(lambda: setattr(self, "_presenting", False))

    def _apply_color_scheme(self):
        """Apply terminal color scheme to BashTextView when gtk_theme is 'terminal'.

        Dialog theming is handled globally via the ashyterm-dialog class.
        This method only updates the syntax highlighting in the BashTextView.
        """
        if not self._settings_manager:
            return

        gtk_theme = self._settings_manager.get("gtk_theme", "")
        if gtk_theme != "terminal":
            return

        # Update BashTextView syntax highlighting colors
        scheme = self._settings_manager.get_color_scheme_data()
        palette = scheme.get("palette", [])
        fg_color = scheme.get("foreground", "#ffffff")
        if palette and hasattr(self, "command_textview") and self.command_textview:
            self.command_textview.update_colors_from_scheme(palette, fg_color)

    def _build_ui(self):
        # Static CSS is loaded from dialogs.css at application startup
        # Dynamic theme colors are applied via _apply_color_scheme()

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header bar with search on left side
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        # Search entry on left side of header
        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search..."),
            width_chars=20,
        )
        a11y_label(self.search_entry, _("Search commands"))
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.pack_start(self.search_entry)

        # Restore hidden button (only visible when there are hidden commands)
        self.restore_hidden_button = Gtk.Button(
            icon_name="view-reveal-symbolic",
        )
        get_tooltip_helper().add_tooltip(
            self.restore_hidden_button, _("Restore hidden commands")
        )
        self.restore_hidden_button.connect("clicked", self._on_restore_hidden_clicked)
        header.pack_end(self.restore_hidden_button)
        self._update_restore_hidden_visibility()

        # Main content
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Command input section - modern integrated design
        input_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=12,
            margin_bottom=10,
            margin_start=16,
            margin_end=16,
        )

        # Unified container with text input and button using overlay
        input_overlay = Gtk.Overlay()

        # Main text input area
        input_frame = Gtk.Frame()
        input_frame.add_css_class("view")
        input_frame.add_css_class("command-input-frame")

        scrolled_text = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=60,
            max_content_height=200,
            propagate_natural_height=True,
        )
        self.command_textview = BashTextView()

        # Add right margin to make room for execute button
        self.command_textview.set_right_margin(85)

        # Placeholder handling
        placeholder_buffer = self.command_textview.get_buffer()
        placeholder_buffer.connect("changed", self._on_textview_changed)

        scrolled_text.set_child(self.command_textview)
        input_frame.set_child(scrolled_text)
        input_overlay.set_child(input_frame)

        # Execute split button overlaid on the right side, vertically centered
        execute_menu = Gio.Menu()
        execute_menu.append(_("Execute in Multiple Terminals"), "execute.send-to-many")

        self.execute_button = Adw.SplitButton(
            label=_("Execute"),
            menu_model=execute_menu,
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
            margin_end=8,
        )
        self.execute_button.add_css_class("suggested-action")
        self.execute_button.connect("clicked", self._on_execute_clicked)

        # Action group for the dropdown menu
        execute_action_group = Gio.SimpleActionGroup()
        send_many_action = Gio.SimpleAction.new("send-to-many", None)
        send_many_action.connect("activate", self._on_execute_to_many)
        execute_action_group.add_action(send_many_action)
        self.insert_action_group("execute", execute_action_group)

        input_overlay.add_overlay(self.execute_button)

        input_section.append(input_overlay)
        main_box.append(input_section)

        # Separator
        main_box.append(
            Gtk.Separator(
                orientation=Gtk.Orientation.HORIZONTAL, margin_start=16, margin_end=16
            )
        )

        # Scrolled area for commands (grid layout)
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )

        # FlowBox for command buttons with improved spacing
        self.commands_flow_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=False,
            max_children_per_line=5,
            min_children_per_line=2,
            row_spacing=8,
            column_spacing=8,
            margin_top=14,
            margin_bottom=14,
            margin_start=16,
            margin_end=16,
            valign=Gtk.Align.START,
            css_classes=["commands-flow-box"],
        )
        self.commands_flow_box.set_filter_func(self._filter_command)

        scrolled.set_child(self.commands_flow_box)
        main_box.append(scrolled)

        # Bottom action bar with Add Command button
        bottom_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=8,
            margin_bottom=12,
            margin_start=16,
            margin_end=16,
            halign=Gtk.Align.CENTER,
        )

        add_command_button = Gtk.Button(
            css_classes=["add-command-button"],
        )
        add_button_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_button_content.append(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        add_button_content.append(Gtk.Label(label=_("Add Command")))
        add_command_button.set_child(add_button_content)
        add_command_button.connect("clicked", self._on_add_clicked)
        bottom_bar.append(add_command_button)

        main_box.append(bottom_bar)

        toolbar_view.set_content(main_box)

    def _on_textview_changed(self, buffer):
        """Handle textview content changes."""
        pass  # Placeholder for future functionality

    def _filter_command(self, flow_box_child) -> bool:
        """Filter function for command buttons based on search."""
        if not self._search_filter:
            return True

        button = flow_box_child.get_child()
        if not isinstance(button, CommandButtonWidget):
            return True

        # Check if command is hidden
        if hasattr(button, "command") and button.command:
            if self.command_manager.is_command_hidden(button.command.id):
                return False

        search_lower = self._search_filter.lower()
        command = button.command

        # Search in name, description, and command template
        return (
            search_lower in command.name.lower()
            or search_lower in command.description.lower()
            or search_lower in command.command_template.lower()
        )

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        self._search_filter = entry.get_text().strip()
        self.commands_flow_box.invalidate_filter()

    def _populate_commands(self):
        """Populate the dialog with command buttons in a flat grid (no categories)."""
        # Clear existing
        while child := self.commands_flow_box.get_first_child():
            self.commands_flow_box.remove(child)

        self._all_command_widgets.clear()

        # Get all commands and sort by name
        commands = self.command_manager.get_all_commands()
        visible_commands = [
            cmd
            for cmd in commands
            if not self.command_manager.is_command_hidden(cmd.id)
        ]

        # Sort commands alphabetically by name
        for cmd in sorted(visible_commands, key=lambda c: c.name.lower()):
            btn = CommandButtonWidget(cmd)
            btn.connect("command-activated", self._on_command_activated)
            btn.connect("command-activated-all", self._on_command_activated_all)
            btn.connect("edit-requested", self._on_edit_requested)
            btn.connect("delete-requested", self._on_delete_requested)
            btn.connect("restore-requested", self._on_restore_requested)
            btn.connect("hide-requested", self._on_hide_requested)
            btn.connect("duplicate-requested", self._on_duplicate_requested)
            btn.connect("pin-requested", self._on_pin_requested)
            btn.connect("unpin-requested", self._on_unpin_requested)
            self._all_command_widgets.append(btn)
            self.commands_flow_box.append(btn)

    def _on_command_activated(self, widget, command: CommandButton):
        """Handle command button click."""
        if command.execution_mode == ExecutionMode.SHOW_DIALOG:
            dialog = CommandFormDialog(
                self.parent_window,
                command,
                send_to_all=False,
                settings_manager=self._settings_manager,
            )
            dialog.connect("command-ready", self._on_form_command_ready)
            dialog.present()
        else:
            cmd_text = command.command_template
            execute = command.execution_mode == ExecutionMode.INSERT_AND_EXECUTE
            self.emit("command-selected", cmd_text, execute)
            self.close()

    def _on_command_activated_all(self, widget, command: CommandButton):
        """Handle 'Execute in All Terminals' from context menu."""
        if command.execution_mode == ExecutionMode.SHOW_DIALOG:
            # Show form dialog with send_to_all=True
            dialog = CommandFormDialog(
                self.parent_window,
                command,
                send_to_all=True,
                settings_manager=self._settings_manager,
            )
            dialog.connect("command-ready", self._on_form_command_ready)
            dialog.present()
        else:
            # Build command directly and show terminal selection with all pre-selected
            cmd_text = command.command_template
            execute = command.execution_mode == ExecutionMode.INSERT_AND_EXECUTE
            self._show_terminal_selection_dialog(cmd_text, execute, pre_select_all=True)

    def _on_form_command_ready(
        self, dialog, command: str, execute: bool, send_to_all: bool
    ):
        """Handle command ready from form dialog."""
        if send_to_all:
            # Show terminal selection dialog with all terminals pre-selected
            self._show_terminal_selection_dialog(command, execute, pre_select_all=True)
        else:
            # Send directly to current terminal
            self.emit("command-selected", command, execute)
            self.close()

    def _send_to_all_terminals(self, command: str, execute: bool):
        """Send command to all terminals via parent window."""
        if hasattr(self.parent_window, "_broadcast_command_to_all"):
            # Add newline if executing
            cmd = command + "\n" if execute else command
            self.parent_window._broadcast_command_to_all(cmd)
        else:
            # Fallback to single terminal
            self.emit("command-selected", command, execute)

    def _show_terminal_selection_dialog(
        self, command: str, execute: bool, pre_select_all: bool = False
    ):
        """Show dialog to select which terminals should receive the command."""
        if not hasattr(self.parent_window, "tab_manager"):
            # Fallback - just send to all
            self._send_to_all_terminals(command, execute)
            self.close()
            return

        all_terminals = self.parent_window.tab_manager.get_all_terminals_across_tabs()
        if not all_terminals:
            if hasattr(self.parent_window, "toast_overlay"):
                self.parent_window.toast_overlay.add_toast(
                    Adw.Toast(
                        title=_(
                            "Cannot broadcast: no terminals are open. Open at least one terminal first."
                        )
                    )
                )
            return

        count = len(all_terminals)
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Confirm sending of command"),
            body=_(
                "Select which of the <b>{count}</b> open terminals should receive the command below."
            ).format(count=count),
            body_use_markup=True,
            close_response="cancel",
        )
        dialog.add_css_class("ashyterm-dialog")

        # Display the command for the user to review with syntax highlighting
        palette = None
        fg_color = "#ffffff"
        if (
            self._settings_manager
            and self._settings_manager.get("gtk_theme", "") == "terminal"
        ):
            scheme = self._settings_manager.get_color_scheme_data()
            palette = scheme.get("palette", [])
            fg_color = scheme.get("foreground", "#ffffff")
        highlighted_cmd = get_bash_pango_markup(command, palette, fg_color)
        command_label = Gtk.Label(
            label=f"<tt>{highlighted_cmd}</tt>",
            use_markup=True,
            css_classes=["card"],
            halign=Gtk.Align.CENTER,
            margin_start=8,
            margin_end=8,
            margin_top=6,
            margin_bottom=6,
        )

        instructions_label = Gtk.Label(
            label=_("Choose the tabs that should run this command:"),
            halign=Gtk.Align.START,
            margin_top=6,
        )
        instructions_label.set_wrap(True)

        flow_box = Gtk.FlowBox()
        flow_box.set_selection_mode(Gtk.SelectionMode.NONE)
        flow_box.set_row_spacing(6)
        flow_box.set_column_spacing(12)
        max_columns = 3
        columns = max(1, min(max_columns, len(all_terminals)))
        flow_box.set_min_children_per_line(columns)
        flow_box.set_max_children_per_line(max_columns)

        # Get active/current terminal to determine default selection
        current_terminal = None
        if hasattr(self.parent_window, "tab_manager") and not pre_select_all:
            current_terminal = self.parent_window.tab_manager.get_selected_terminal()

        selection_controls = []
        for terminal in all_terminals:
            display_title = self._get_terminal_display_name(terminal)
            check_button = Gtk.CheckButton(label=display_title)
            # If pre_select_all is True, select all; otherwise only select current terminal
            if pre_select_all:
                check_button.set_active(True)
            else:
                check_button.set_active(terminal == current_terminal)
            check_button.set_halign(Gtk.Align.START)
            flow_box.insert(check_button, -1)
            selection_controls.append((terminal, check_button))

        if len(selection_controls) > 6:
            scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
            scrolled.set_min_content_height(200)
            scrolled.set_child(flow_box)
            selection_container = scrolled
        else:
            selection_container = flow_box

        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        content_box.append(command_label)
        content_box.append(instructions_label)
        content_box.append(selection_container)
        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("send", _("Send Command"))
        dialog.set_default_response("send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)

        dialog.connect(
            "response",
            self._on_terminal_selection_response,
            command,
            execute,
            selection_controls,
        )
        dialog.present()

    def _get_terminal_display_name(self, terminal) -> str:
        """Get display name for a terminal."""
        if hasattr(self.parent_window, "_get_terminal_display_name"):
            return self.parent_window._get_terminal_display_name(terminal)

        if hasattr(self.parent_window, "terminal_manager"):
            terminal_id = self.parent_window.terminal_manager.registry.get_terminal_id(
                terminal
            )
            if terminal_id:
                terminal_info = (
                    self.parent_window.terminal_manager.registry.get_terminal_info(
                        terminal_id
                    )
                )
                if terminal_info:
                    identifier = terminal_info.get("identifier")
                    if hasattr(identifier, "name"):
                        return identifier.name
                    if isinstance(identifier, str):
                        return identifier

        return _("Terminal")

    def _on_terminal_selection_response(
        self,
        dialog,
        response_id: str,
        command: str,
        execute: bool,
        selection_controls: list,
    ):
        """Handle terminal selection dialog response."""
        if response_id == "send":
            selected_terminals = [
                terminal for terminal, check in selection_controls if check.get_active()
            ]

            if not selected_terminals:
                return

            # Send command to selected terminals
            cmd = command + "\n" if execute else command
            command_bytes = cmd.encode("utf-8")

            if not cmd.endswith("\n"):
                # Use bracketed paste for insertion without execution
                for terminal in selected_terminals:
                    paste_data = b"\x1b[200~" + command_bytes + b"\x1b[201~"
                    terminal.feed_child(paste_data)
            else:
                # Execute on selected terminals
                for terminal in selected_terminals:
                    terminal.feed_child(command_bytes)

            self.close()

    def _on_execute_clicked(self, button):
        """Handle execute button click - sends to current terminal."""
        command = self.command_textview.get_text().strip()
        if not command:
            return

        self.emit("command-selected", command, True)
        self.command_textview.set_text("")
        self.close()

    def _on_execute_to_many(self, action, param):
        """Handle 'Execute in Multiple Terminals' from dropdown menu."""
        command = self.command_textview.get_text().strip()
        if not command:
            return

        self._show_terminal_selection_dialog(command, execute=True, pre_select_all=True)
        self.command_textview.set_text("")

    def _on_add_clicked(self, button):
        """Open editor dialog for new command."""
        dialog = CommandEditorDialog(
            self.parent_window, settings_manager=self._settings_manager
        )
        dialog.connect("save-requested", self._on_save_new_command)
        dialog.present()

    def _on_edit_requested(self, widget, command: CommandButton):
        """Open editor dialog for existing command (builtin or custom)."""
        dialog = CommandEditorDialog(
            self.parent_window, command, settings_manager=self._settings_manager
        )
        dialog.connect("save-requested", self._on_save_edited_command)
        dialog.present()

    def _on_delete_requested(self, widget, command: CommandButton):
        """Show delete confirmation."""

        def on_confirm():
            self.command_manager.remove_command(command.id)
            self._populate_commands()

        show_delete_confirmation_dialog(
            parent=self.parent_window,
            heading=_("Delete Command?"),
            body=_("Are you sure you want to delete '{name}'?").format(
                name=command.name
            ),
            on_confirm=on_confirm,
        )

    def _on_restore_requested(self, widget, command: CommandButton):
        """Restore a builtin command to its default state."""

        def on_confirm():
            self.command_manager.restore_builtin_default(command.id)
            self._populate_commands()

        show_delete_confirmation_dialog(
            parent=self.parent_window,
            heading=_("Restore Default?"),
            body=_(
                "This will restore '{name}' to its original default configuration. Your customizations will be lost."
            ).format(name=command.name),
            on_confirm=on_confirm,
            delete_label=_("Restore Default"),
        )

    def _on_pin_requested(self, widget, command: CommandButton):
        """Pin a command to the toolbar."""
        self.command_manager.pin_command(command.id)
        self._populate_commands()  # Refresh to update context menu
        # Notify parent window to refresh toolbar
        if hasattr(self.parent_window, "refresh_command_toolbar"):
            self.parent_window.refresh_command_toolbar()

    def _on_unpin_requested(self, widget, command: CommandButton):
        """Unpin a command from the toolbar."""
        self.command_manager.unpin_command(command.id)
        self._populate_commands()  # Refresh to update context menu
        # Notify parent window to refresh toolbar
        if hasattr(self.parent_window, "refresh_command_toolbar"):
            self.parent_window.refresh_command_toolbar()

    def _on_hide_requested(self, widget, command: CommandButton):
        """Hide a command from the interface."""

        def on_confirm():
            self.command_manager.hide_command(command.id)
            self._populate_commands()
            self._update_restore_hidden_visibility()

        show_delete_confirmation_dialog(
            parent=self.parent_window,
            heading=_("Hide Command?"),
            body=_(
                "Hide '{name}' from the command list? You can restore it later from settings."
            ).format(name=command.name),
            on_confirm=on_confirm,
            delete_label=_("Hide"),
        )

    def _update_restore_hidden_visibility(self):
        """Update visibility of restore hidden button based on hidden commands."""
        hidden_ids = self.command_manager.get_hidden_command_ids()
        self.restore_hidden_button.set_visible(len(hidden_ids) > 0)

    def _on_restore_hidden_clicked(self, button):
        """Show dialog to restore hidden commands."""
        hidden_ids = self.command_manager.get_hidden_command_ids()
        if not hidden_ids:
            return

        # Create a dialog with checkboxes for each hidden command
        dialog = Adw.Window(
            transient_for=self.parent_window,
            modal=True,
            default_width=400,
            default_height=400,
            title=_("Restore Hidden Commands"),
        )

        toolbar_view = Adw.ToolbarView()
        dialog.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        close_btn = Gtk.Button(label=_("Close"))
        close_btn.connect("clicked", lambda b: dialog.close())
        header.pack_end(close_btn)

        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )

        info_label = Gtk.Label(
            label=_("Click on a command to restore it:"),
            xalign=0.0,
            css_classes=[BaseDialog.CSS_CLASS_DIM_LABEL],
        )
        content_box.append(info_label)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=[BaseDialog.CSS_CLASS_BOXED_LIST],
        )

        # Get all commands to find names for hidden IDs
        all_commands = self.command_manager.get_all_commands()
        # Also get original builtins to find hidden builtin names
        from ...data.command_manager_models import get_builtin_commands

        builtin_commands = get_builtin_commands()

        all_cmd_map = {cmd.id: cmd for cmd in all_commands}
        builtin_map = {cmd.id: cmd for cmd in builtin_commands}

        for cmd_id in hidden_ids:
            # Try to get command name
            cmd = all_cmd_map.get(cmd_id) or builtin_map.get(cmd_id)
            cmd_name = cmd.name if cmd else cmd_id

            row = ManagedListRow(
                title=cmd_name,
                show_reorder=False,
                show_actions=False,
                show_toggle=False,
            )
            row.set_activatable(True)
            row.add_suffix(Gtk.Image.new_from_icon_name("view-reveal-symbolic"))
            row.connect("activated", self._on_unhide_command, cmd_id, dialog)
            list_box.append(row)

        scrolled.set_child(list_box)
        content_box.append(scrolled)

        # Restore all button
        restore_all_btn = Gtk.Button(
            label=_("Restore All"),
            css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
            halign=Gtk.Align.CENTER,
            margin_top=8,
        )
        restore_all_btn.connect("clicked", self._on_restore_all_hidden, dialog)
        content_box.append(restore_all_btn)

        toolbar_view.set_content(content_box)
        dialog.present()

    def _on_unhide_command(self, row, cmd_id, dialog):
        """Unhide a single command."""
        self.command_manager.unhide_command(cmd_id)
        self._populate_commands()
        self._update_restore_hidden_visibility()

        # Close dialog if no more hidden commands
        if not self.command_manager.get_hidden_command_ids():
            dialog.close()
        else:
            # Remove the row from list
            parent = row.get_parent()
            if parent:
                parent.remove(row)

    def _on_restore_all_hidden(self, button, dialog):
        """Restore all hidden commands."""
        for cmd_id in self.command_manager.get_hidden_command_ids():
            self.command_manager.unhide_command(cmd_id)
        self._populate_commands()
        self._update_restore_hidden_visibility()
        dialog.close()

    def _on_duplicate_requested(self, widget, command: CommandButton):
        """Duplicate a command as a new custom command."""
        # Create a copy with new ID
        new_command = CommandButton(
            id=generate_id(),
            name=f"{command.name} " + _("(Copy)"),
            description=command.description,
            command_template=command.command_template,
            icon_name=command.icon_name,
            display_mode=command.display_mode,
            execution_mode=command.execution_mode,
            cursor_position=command.cursor_position,
            form_fields=list(command.form_fields),
            is_builtin=False,  # Duplicates are always custom
            category=command.category,
            sort_order=command.sort_order,
        )

        # Open editor with the copy
        dialog = CommandEditorDialog(
            self.parent_window, new_command, settings_manager=self._settings_manager
        )
        dialog.connect("save-requested", self._on_save_new_command)
        dialog.present()

    def _on_save_new_command(self, dialog, command: CommandButton):
        """Save a new command."""
        self.command_manager.add_custom_command(command)
        self._populate_commands()

    def _on_save_edited_command(self, dialog, command: CommandButton):
        """Save an edited command (custom or customized builtin)."""
        self.command_manager.update_command(command)
        self._populate_commands()

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_active_changed(self, widget, _pspec):
        if not self._presenting and not self.is_active() and self.get_visible():
            GLib.timeout_add(200, self._delayed_close)

    def _delayed_close(self):
        if not self.is_active() and self.get_visible():
            self.close()
        return False

    def _on_close_request(self, widget):
        self.hide()
        return Gdk.EVENT_STOP

    def close(self):
        self.hide()

    def destroy(self):
        if not hasattr(self, "_allow_destroy") or not self._allow_destroy:
            self.hide()
            return
        super().destroy()

    def _on_parent_destroyed(self, parent):
        self._allow_destroy = True
        self.destroy()
