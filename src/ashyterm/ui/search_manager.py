# ashyterm/ui/search_manager.py
import re
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, GObject, Vte

from ..utils.translation_utils import _

if TYPE_CHECKING:
    from ..window import CommTerminalWindow


class SearchManager(GObject.Object):
    """
    Manages the terminal search functionality, decoupling it from the main window.
    """

    def __init__(self, window: "CommTerminalWindow"):
        super().__init__()
        self.window = window
        self.ui = window.ui_builder
        self.tab_manager = window.tab_manager
        self.settings_manager = window.settings_manager
        self.logger = window.logger

        # State
        self.active = False
        self.current_terminal = None
        self.current_occurrence = 0

        self._setup_connections()

    def _setup_connections(self):
        """Connect UI signals to manager methods."""
        # Toggle search mode
        self.ui.search_button.bind_property(
            "active",
            self.ui.search_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL,
        )

        self.ui.search_bar.connect(
            "notify::search-mode-enabled", self._on_search_mode_changed
        )

        # Search entry signals
        self.ui.terminal_search_entry.connect(
            "search-changed", self._on_search_text_changed
        )
        self.ui.terminal_search_entry.connect("stop-search", self._on_search_stop)
        self.ui.terminal_search_entry.connect("activate", self._on_search_next)

        # Buttons
        self.ui.search_prev_button.connect("clicked", self._on_search_previous)
        self.ui.search_next_button.connect("clicked", self._on_search_next)

        # Switches
        self.ui.case_sensitive_switch.connect(
            "notify::active", self._on_case_sensitive_changed
        )
        self.ui.regex_switch.connect("notify::active", self._on_regex_changed)

        # Initialize switch states
        self.ui.case_sensitive_switch.set_active(
            self.settings_manager.get("search_case_sensitive", False)
        )
        self.ui.regex_switch.set_active(
            self.settings_manager.get("search_use_regex", False)
        )

    def _on_search_mode_changed(self, search_bar, param):
        if search_bar.get_search_mode():
            self.ui.terminal_search_entry.grab_focus()
            current_terminal = self.tab_manager.get_selected_terminal()
            if current_terminal:
                self.current_terminal = current_terminal
                self.active = True
        else:
            self.stop_search()

    def _on_search_text_changed(self, search_entry):
        text = search_entry.get_text()
        if not text:
            self.clear_search()
            return

        self.perform_search(text)

    def _on_search_stop(self, _search_entry):
        self.stop_search()

    def _on_search_next(self, _button=None):
        terminal = self.tab_manager.get_selected_terminal()
        if not terminal or not self.active:
            return

        found = terminal.search_find_next()
        if not found:
            found = self._wrap_search_to_beginning(terminal)
            if found:
                self.current_occurrence = 1
        elif found:
            self.current_occurrence += 1

        self._show_search_result(found)

    def _on_search_previous(self, _button=None):
        terminal = self.tab_manager.get_selected_terminal()
        if not terminal or not self.active:
            return

        found = terminal.search_find_previous()
        if not found:
            found = self._wrap_search_to_end(terminal)
            if found:
                self.current_occurrence = 1
        elif found and self.current_occurrence > 1:
            self.current_occurrence -= 1

        self._show_search_result(found)

    def _on_case_sensitive_changed(self, switch, _param):
        self.settings_manager.set("search_case_sensitive", switch.get_active())
        if self.ui.terminal_search_entry.get_text():
            self.perform_search(self.ui.terminal_search_entry.get_text())

    def _on_regex_changed(self, switch, _param):
        self.settings_manager.set("search_use_regex", switch.get_active())
        if self.ui.terminal_search_entry.get_text():
            self.perform_search(self.ui.terminal_search_entry.get_text())

    def perform_search(self, text):
        terminal = self.tab_manager.get_selected_terminal()
        if not terminal:
            return

        self.current_terminal = terminal
        self.active = True

        terminal.search_set_regex(None, 0)

        try:
            pcre2_flags = 0x00000400  # PCRE2_MULTILINE
            if not self.settings_manager.get("search_case_sensitive", False):
                pcre2_flags |= 0x00000008  # PCRE2_CASELESS

            use_regex = self.settings_manager.get("search_use_regex", False)
            search_text = text if use_regex else re.escape(text)

            regex = Vte.Regex.new_for_search(search_text, -1, pcre2_flags)
            if not regex:
                self.window.toast_overlay.add_toast(
                    Adw.Toast(title=_("Invalid search pattern."))
                )
                return

            terminal.search_set_regex(regex, 0)

            found = terminal.search_find_next()
            if not found:
                found = self._search_from_beginning(terminal, regex)

            self.current_occurrence = 1 if found else 0
            self.update_occurrence_display()

        except Exception as e:
            self.logger.error(f"Search error: {e}")
            self.window.toast_overlay.add_toast(Adw.Toast(title=_("Search failed.")))

    def _search_from_beginning(self, terminal, _regex):
        try:
            found = terminal.search_find_next()
            if found:
                v_adj = terminal.get_vadjustment()
                if v_adj:
                    _col, row = terminal.get_cursor_position()
                    v_adj.set_value(max(0, row - 5))
            return found
        except Exception as e:
            self.logger.debug(f"Error searching from beginning: {e}")
            return False

    def _wrap_search_to_beginning(self, terminal) -> bool:
        v_adj = terminal.get_vadjustment()
        if v_adj:
            v_adj.set_value(0.0)
            return terminal.search_find_next()
        return False

    def _wrap_search_to_end(self, terminal) -> bool:
        v_adj = terminal.get_vadjustment()
        if v_adj:
            v_adj.set_value(v_adj.get_upper() - v_adj.get_page_size())
            return terminal.search_find_previous()
        return False

    def _show_search_result(self, found: bool):
        if not found:
            self.window.toast_overlay.add_toast(
                Adw.Toast(title=_("No more matches found."))
            )
        else:
            self.update_occurrence_display()

    def update_occurrence_display(self):
        text = (
            str(self.current_occurrence)
            if self.active and self.current_occurrence > 0
            else ""
        )
        self.ui.search_occurrence_label.set_text(text)

    def clear_search(self):
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            terminal.search_set_regex(None, 0)
        self.active = False
        self.current_terminal = None
        self.current_occurrence = 0
        self.update_occurrence_display()

    def stop_search(self):
        self.clear_search()
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            terminal.grab_focus()

    def hide_if_terminal_changed(self):
        """Hide search if the current terminal is different from when search was started."""
        current_terminal = self.tab_manager.get_selected_terminal()
        if self.active and current_terminal != self.current_terminal:
            self.ui.search_bar.set_search_mode(False)
