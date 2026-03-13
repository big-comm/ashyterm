# ashyterm/terminal/fm_integration.py
"""File manager integration delegate for TabManager.

Handles toggling, positioning, and lifecycle of the file manager
pane within terminal tabs.
"""

from typing import TYPE_CHECKING, Optional

from gi.repository import Adw, Gtk, Vte

from ..utils.logger import get_logger

if TYPE_CHECKING:
    from ..filemanager.manager import FileManager
    from .tabs import TabManager


class FileManagerIntegration:
    """Manages the file manager pane within terminal tabs."""

    def __init__(self, tab_manager: "TabManager") -> None:
        self.tm = tab_manager
        self.logger = get_logger("ashyterm.tabs.fm")

    def is_widget_in_filemanager(self, widget: Gtk.Widget) -> bool:
        """Checks if a widget is a descendant of the FileManager's main widget."""
        if not widget or not self.tm.active_tab:
            return False

        page = self.tm.pages.get(self.tm.active_tab)
        if not page:
            return False

        fm = self.tm.file_managers.get(page)
        if not fm:
            return False

        fm_widget = fm.get_main_widget()
        current = widget
        while current:
            if current == fm_widget:
                return True
            current = current.get_parent()
        return False

    def reset_file_manager_button(self) -> None:
        """Resets the file manager button to inactive state."""
        if hasattr(self.tm.terminal_manager.parent_window, "file_manager_button"):
            self.tm.terminal_manager.parent_window.file_manager_button.set_active(False)

    def activate_file_manager(self, page, paned, fm) -> None:
        """Activates and shows the file manager for the given page."""
        active_terminal = self.tm.get_selected_terminal()
        if not active_terminal:
            self.reset_file_manager_button()
            return

        if not fm:
            fm = self.create_file_manager(page)

        fm.rebind_terminal(active_terminal)
        paned.set_end_child(fm.get_main_widget())

        target_pos = self._calculate_file_manager_position(page, paned)
        paned.set_position(target_pos)
        fm.set_visibility(True, source="filemanager")

        self._connect_paned_position_handler(paned, page)

    def create_file_manager(self, page) -> "FileManager":
        """Creates a new FileManager instance for the page."""
        from ..filemanager.manager import FileManager

        fm = FileManager(
            self.tm.terminal_manager.parent_window,
            self.tm.terminal_manager,
            self.tm.terminal_manager.settings_manager,
        )
        fm.temp_files_changed_handler_id = fm.connect(
            "temp-files-changed",
            self.tm.terminal_manager.parent_window._on_temp_files_changed,
            page,
        )
        self.tm.file_managers[page] = fm
        return fm

    def deactivate_file_manager(self, page, paned, fm) -> None:
        """Deactivates and hides the file manager for the given page."""
        page._fm_paned_pos = paned.get_position()

        available_height = self._get_available_paned_height(paned)
        if available_height > 1:
            fm_height = available_height - paned.get_position()
        else:
            window_height = self.tm.terminal_manager.parent_window.get_height()
            fm_height = window_height - paned.get_position()

        min_fm_height = 240
        fm_height = max(min_fm_height, fm_height)
        self.logger.debug(
            f"File manager closing: available_height={available_height}, "
            f"paned_pos={paned.get_position()}, fm_height={fm_height}"
        )
        self.tm.terminal_manager.settings_manager.set(
            "file_manager_height", fm_height, save_immediately=True
        )
        fm.set_visibility(False, source="filemanager")
        paned.set_end_child(None)

    def toggle_file_manager_for_active_tab(self, is_active: bool) -> None:
        """Toggles the file manager's visibility for the currently active tab."""
        page = self.tm._get_active_tab_page()
        if not page:
            self.reset_file_manager_button()
            return

        if not hasattr(page, "content_paned"):
            self.logger.warning(
                "Attempted to toggle file manager on a page without a content_paned."
            )
            self.reset_file_manager_button()
            return

        paned = page.content_paned
        fm = self.tm.file_managers.get(page)

        if is_active:
            self.activate_file_manager(page, paned, fm)
        elif fm:
            self.deactivate_file_manager(page, paned, fm)

    def on_file_manager_paned_position_changed(self, paned, _param_spec, page) -> None:
        """Save file manager height when the pane is resized by the user."""
        fm = self.tm.file_managers.get(page)
        if not fm or not fm.revealer.get_reveal_child():
            return

        paned_allocation = paned.get_allocation()
        available_height = paned_allocation.height
        if available_height <= 1:
            return

        fm_height = available_height - paned.get_position()

        min_fm_height = 240
        fm_height = max(min_fm_height, fm_height)

        page._fm_paned_pos = paned.get_position()

        self.tm.terminal_manager.settings_manager.set(
            "file_manager_height", fm_height, save_immediately=True
        )

    def cleanup_file_manager_for_page(self, page: Adw.ViewStackPage) -> None:
        """Cleanup file manager instance for a page."""
        if page not in self.tm.file_managers:
            return
        fm = self.tm.file_managers.pop(page)
        if hasattr(page, "content_paned") and page.content_paned:
            page.content_paned.set_end_child(None)
        fm.destroy()

    # -- internal helpers -----------------------------------------------------

    def _calculate_file_manager_position(self, page, paned) -> int:
        """Calculates the optimal paned position for the file manager."""
        available_height = self._get_available_paned_height(paned)
        saved_fm_height = self.tm.terminal_manager.settings_manager.get(
            "file_manager_height", 250
        )

        min_fm_height = 240
        min_terminal_height = 120
        max_fm_height = max(min_fm_height, available_height - min_terminal_height)

        saved_fm_height = max(min_fm_height, min(saved_fm_height, max_fm_height))

        self.logger.debug(
            f"File manager: available_height={available_height}, "
            f"saved_fm_height={saved_fm_height}, max_fm_height={max_fm_height}"
        )

        target_pos = available_height - saved_fm_height

        if hasattr(page, "_fm_paned_pos"):
            last_pos = page._fm_paned_pos
            last_fm_height = available_height - last_pos
            if min_fm_height <= last_fm_height <= max_fm_height:
                target_pos = last_pos

        target_pos = max(
            min_terminal_height, min(target_pos, available_height - min_fm_height)
        )

        self.logger.debug(
            f"File manager: target_pos={target_pos}, "
            f"has_page_pos={hasattr(page, '_fm_paned_pos')}"
        )

        return target_pos

    def _get_available_paned_height(self, paned) -> int:
        """Returns the available height for the paned widget."""
        paned_allocation = paned.get_allocation()
        available_height = paned_allocation.height

        if available_height <= 1:
            available_height = self.tm.terminal_manager.parent_window.get_height()
            available_height = max(400, available_height - 100)

        return available_height

    def _connect_paned_position_handler(self, paned, page) -> None:
        """Connects the position change handler for the paned widget."""
        if not hasattr(paned, "_fm_position_handler_id"):
            paned._fm_position_handler_id = paned.connect(
                "notify::position",
                self.on_file_manager_paned_position_changed,
                page,
            )
