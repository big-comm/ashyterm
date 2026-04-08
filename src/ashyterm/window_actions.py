"""Window actions, shortcuts, and menu setup mixin."""

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gtk

from .utils.translation_utils import _

# Bracketed paste mode escape sequences
PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"

MSG_NO_ACTIVE_TERMINAL = _("No active terminal to send command to.")


class WindowActionsMixin:
    """Mixin: window-level actions, keyboard shortcuts, command dispatch."""

    # ─── Actions Setup ─────────────────────────────────────────────────

    def _setup_actions(self) -> None:
        """Set up window-level actions by delegating to the action handler."""
        try:
            self.action_handler.setup_actions()
        except Exception as e:
            self.logger.error(f"Failed to setup actions: {e}")
            from .utils.exceptions import UIError

            raise UIError("window", f"action setup failed: {e}")

    def _setup_keyboard_shortcuts(self) -> None:
        """Sets up window-level keyboard shortcuts for tab navigation."""
        controller = Gtk.EventControllerKey.new()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    # ─── Key Press Dispatch ────────────────────────────────────────────

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        """Handles key press events for tab navigation and search."""
        if self._handle_emergency_dialog_close(keyval, state):
            return Gdk.EVENT_STOP
        if self._handle_escape_key(keyval):
            return Gdk.EVENT_STOP
        if self._handle_search_shortcut(keyval, state):
            return Gdk.EVENT_STOP
        if self._handle_tab_group_shortcuts(keyval, state):
            return Gdk.EVENT_STOP
        accel_string = Gtk.accelerator_name(
            keyval, state & Gtk.accelerator_get_default_mod_mask()
        )
        if self._handle_dynamic_shortcuts(accel_string):
            return Gdk.EVENT_STOP
        if self._handle_alt_number_shortcuts(keyval, state):
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    # ─── Key Handlers ──────────────────────────────────────────────────

    def _handle_escape_key(self, keyval) -> bool:
        """Handle Escape key to cancel tab move mode."""
        if keyval == Gdk.KEY_Escape:
            if self.tab_manager.cancel_tab_move_if_active():
                return True
        return False

    def _handle_search_shortcut(self, keyval, state) -> bool:
        """Handle Ctrl+Shift+F for search toggle."""
        is_ctrl_shift = (
            state & Gdk.ModifierType.CONTROL_MASK
            and state & Gdk.ModifierType.SHIFT_MASK
        )
        is_f_key = keyval == Gdk.KEY_f or keyval == Gdk.KEY_F
        if is_ctrl_shift and is_f_key:
            current_mode = self.search_bar.get_search_mode()
            self.search_bar.set_search_mode(not current_mode)
            if not current_mode:
                self.terminal_search_entry.grab_focus()
            return True
        return False

    def _handle_dynamic_shortcuts(self, accel_string: str) -> bool:
        """Handle dynamically configured shortcuts."""
        if not accel_string:
            return False
        shortcut_actions: dict[str | None, Any] = {
            self.settings_manager.get_shortcut(
                "next-tab"
            ): self.tab_manager.select_next_tab,
            self.settings_manager.get_shortcut(
                "previous-tab"
            ): self.tab_manager.select_previous_tab,
            self.settings_manager.get_shortcut(
                "ai-assistant"
            ): self._on_ai_assistant_requested,
        }
        if action := shortcut_actions.get(accel_string):
            action()
            return True
        # Handle split shortcuts separately (require terminal check)
        split_h = self.settings_manager.get_shortcut("split-horizontal")
        split_v = self.settings_manager.get_shortcut("split-vertical")
        if accel_string in (split_h, split_v):
            if terminal := self.tab_manager.get_selected_terminal():
                if accel_string == split_h:
                    self.tab_manager.split_horizontal(terminal)
                else:
                    self.tab_manager.split_vertical(terminal)
            return True
        return False

    def _handle_alt_number_shortcuts(self, keyval, state) -> bool:
        """Handle Alt+Number for quick tab switching."""
        if not (state & Gdk.ModifierType.ALT_MASK):
            return False
        key_to_index = {
            Gdk.KEY_1: 0,
            Gdk.KEY_2: 1,
            Gdk.KEY_3: 2,
            Gdk.KEY_4: 3,
            Gdk.KEY_5: 4,
            Gdk.KEY_6: 5,
            Gdk.KEY_7: 6,
            Gdk.KEY_8: 7,
            Gdk.KEY_9: 8,
            Gdk.KEY_0: 9,
        }
        if keyval in key_to_index:
            index = key_to_index[keyval]
            if index < self.tab_manager.get_tab_count():
                self.tab_manager.set_active_tab(self.tab_manager.tabs[index])
            return True
        return False

    # ─── Emergency Dialog Close ────────────────────────────────────────

    def _handle_emergency_dialog_close(self, keyval, state) -> bool:
        """Handle Ctrl+Shift+Escape to close any blocking dialogs."""
        ctrl_shift = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        if keyval == Gdk.KEY_Escape and (state & ctrl_shift) == ctrl_shift:
            self.logger.warning("Emergency dialog close triggered (Ctrl+Shift+Escape)")
            self._force_close_all_dialogs()
            return True
        return False

    def _handle_tab_group_shortcuts(self, keyval, state) -> bool:
        """Handle Ctrl+Shift+G (new group) and Ctrl+Shift+U (ungroup)."""
        ctrl_shift = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        if (state & ctrl_shift) != ctrl_shift:
            return False
        if keyval in (Gdk.KEY_g, Gdk.KEY_G):
            self.tab_manager.create_group_from_active_tab()
            return True
        if keyval in (Gdk.KEY_u, Gdk.KEY_U):
            self.tab_manager.ungroup_active_tab()
            return True
        return False

    def _force_close_all_dialogs(self):
        """Force close all dialogs and transient windows."""
        if hasattr(self, "get_dialogs"):
            dialogs = self.get_dialogs()
            for i in range(dialogs.get_n_items()):
                if dialog := dialogs.get_item(i):
                    self.logger.info(f"Force closing dialog: {dialog}")
                    dialog.force_close()

        for window in Gtk.Window.list_toplevels():
            if (
                window.get_transient_for() == self
                and window != self
                and hasattr(window, "get_modal")
                and window.get_modal()
            ):
                self.logger.info(f"Force closing transient: {window}")
                window.set_modal(False)
                window.close()

    # ─── Command Manager ───────────────────────────────────────────────

    def _show_command_manager_dialog(self):
        """Creates and shows the Command Manager dialog, or closes it if already visible."""
        if self.command_manager_dialog is None:
            from .ui.dialogs.command_manager import CommandManagerDialog

            self.command_manager_dialog = CommandManagerDialog(
                self, self.settings_manager
            )
            self.command_manager_dialog.connect(
                "command-selected", self._on_command_selected_from_manager
            )
        if self.command_manager_dialog.get_visible():
            self.command_manager_dialog.close()
        else:
            self.command_manager_dialog.present()

    def _on_command_selected_from_manager(
        self, dialog, command_text: str, execute: bool
    ):
        """Callback for when a command is selected from the Command Manager."""
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            if execute:
                command_bytes = command_text.encode("utf-8") + b"\n"
                terminal.feed_child(command_bytes)
            else:
                paste_data = PASTE_START + command_text.encode("utf-8") + PASTE_END
                terminal.feed_child(paste_data)
            terminal.grab_focus()
        else:
            self.toast_overlay.add_toast(Adw.Toast(title=MSG_NO_ACTIVE_TERMINAL))

    # ─── Broadcast ─────────────────────────────────────────────────────

    def _broadcast_command_to_all(self, command_text: str):
        """Send a command to all open terminals."""
        self.broadcast_manager.broadcast_to_all(command_text)

    # ─── Command Toolbar ───────────────────────────────────────────────

    def refresh_command_toolbar(self) -> None:
        """Refresh the command toolbar with current pinned commands."""
        if hasattr(self.ui_builder, "_populate_command_toolbar"):
            self.ui_builder._populate_command_toolbar(self.ui_builder._toolbar_inner)

    def execute_toolbar_command(self, command) -> None:
        """Execute a command from the toolbar."""
        from .data.command_manager_models import ExecutionMode
        from .ui.dialogs.command_manager import CommandFormDialog

        terminal = self.tab_manager.get_selected_terminal()

        if command.execution_mode == ExecutionMode.SHOW_DIALOG:
            dialog = CommandFormDialog(
                self, command, send_to_all=False, settings_manager=self.settings_manager
            )
            dialog.connect("command-ready", self._on_toolbar_form_command_ready)
            dialog.present()
        else:
            cmd_text = command.command_template
            execute = command.execution_mode == ExecutionMode.INSERT_AND_EXECUTE

            if terminal:
                if execute:
                    command_bytes = cmd_text.encode("utf-8") + b"\n"
                    terminal.feed_child(command_bytes)
                else:
                    paste_data = PASTE_START + cmd_text.encode("utf-8") + PASTE_END
                    terminal.feed_child(paste_data)
                terminal.grab_focus()
            else:
                self.toast_overlay.add_toast(Adw.Toast(title=MSG_NO_ACTIVE_TERMINAL))

    def _on_toolbar_form_command_ready(
        self, dialog, command: str, execute: bool, send_to_all: bool
    ):
        """Handle command ready from toolbar form dialog."""
        if send_to_all:
            self._send_command_to_all_terminals(command, execute)
        else:
            self._send_command_to_active_terminal(command, execute)

    def _send_command_to_all_terminals(self, command: str, execute: bool):
        """Send command to all open terminals."""
        all_terminals = self.tab_manager.get_all_terminals_across_tabs()
        for terminal in all_terminals:
            self._feed_command_to_terminal(terminal, command, execute)
        if all_terminals:
            all_terminals[-1].grab_focus()

    def _send_command_to_active_terminal(self, command: str, execute: bool):
        """Send command to the active terminal."""
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            self._feed_command_to_terminal(terminal, command, execute)
            terminal.grab_focus()
        else:
            self.toast_overlay.add_toast(Adw.Toast(title=MSG_NO_ACTIVE_TERMINAL))

    def _feed_command_to_terminal(self, terminal, command: str, execute: bool) -> None:
        """Feed a command to a terminal, optionally executing it."""
        if execute:
            terminal.feed_child(command.encode("utf-8") + b"\n")
        else:
            paste_data = PASTE_START + command.encode("utf-8") + PASTE_END
            terminal.feed_child(paste_data)

    # ─── New Tab / On-Click ────────────────────────────────────────────

    def _on_new_tab_clicked(self, _button) -> None:
        self.action_handler.new_local_tab(None, None)
