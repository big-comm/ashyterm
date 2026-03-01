# ashyterm/filemanager/search.py
"""File search mixin: recursive search, fd/find command building, result processing."""

import shlex
import subprocess
import threading
from pathlib import PurePosixPath
from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from ..utils.translation_utils import _
from .models import FileItem
from .operations import FileOperations

MAX_RECURSIVE_RESULTS = 1000


class FileSearchMixin:
    """Mixin providing file search functionality."""

    def _on_recursive_switch_toggled(self, switch, _param):
        self._on_recursive_toggle(switch)

    def _on_recursive_toggle(self, toggle_widget):
        self.recursive_search_enabled = toggle_widget.get_active()
        if not self.recursive_search_enabled:
            # Invalidate any pending searches and cancel any in-progress search
            self._recursive_search_generation += 1
            self._recursive_search_in_progress = False
            self._update_recursive_search_ui_state()
        self._update_search_placeholder()
        if hasattr(self, "search_entry"):
            self.search_entry.set_sensitive(True)
            # Hide/show the magnifying glass icon in the search entry using CSS
            # (when recursive mode is on, we have an external search button)
            if self.recursive_search_enabled:
                self.search_entry.add_css_class("search-entry-no-icon")
            else:
                self.search_entry.remove_css_class("search-entry-no-icon")

        # Show/hide the search button based on recursive mode
        self.recursive_search_button.set_visible(self.recursive_search_enabled)

        if self.recursive_search_enabled:
            # Don't auto-start search when toggling recursive mode
            self._showing_recursive_results = False
            self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)
        else:
            if self._showing_recursive_results:
                self._showing_recursive_results = False
                self.refresh(source="filemanager", clear_search=False)
            else:
                self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_recursive_search_button_clicked(self, button):
        """Handle click on the recursive search button."""
        search_term = (
            self.search_entry.get_text().strip()
            if hasattr(self, "search_entry")
            else ""
        )
        if search_term and self.recursive_search_enabled:
            self._start_recursive_search(search_term)

    def _on_cancel_recursive_search(self, button):
        """Cancel an ongoing recursive search."""
        self._recursive_search_generation += 1
        self._recursive_search_in_progress = False
        self._update_recursive_search_ui_state()
        self._update_search_placeholder()
        if hasattr(self, "search_entry"):
            self.search_entry.set_sensitive(True)
        self._show_toast(_("Search cancelled"))

    def _update_recursive_search_ui_state(self):
        """Update UI elements based on recursive search state."""
        is_searching = self._recursive_search_in_progress

        # Show spinner and cancel button during search
        self.recursive_search_cancel_box.set_visible(is_searching)
        if is_searching:
            self.recursive_search_spinner.start()
        else:
            self.recursive_search_spinner.stop()

        # Hide search button during active search, show when recursive enabled
        self.recursive_search_button.set_visible(
            self.recursive_search_enabled and not is_searching
        )

    def _on_search_changed(self, search_entry):
        search_term = search_entry.get_text().strip()
        if self.recursive_search_enabled:
            # In recursive mode, don't auto-start search on typing
            # User must press Enter or click the search button
            if not search_term and self._showing_recursive_results:
                self._showing_recursive_results = False
                self.refresh(source="filemanager", clear_search=False)
            return
        else:
            if self._showing_recursive_results:
                self._showing_recursive_results = False
                self.refresh(source="filemanager", clear_search=False)
        self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)
        if (
            hasattr(self, "column_view")
            and self.column_view
            and self.selection_model
            and self.selection_model.get_n_items() > 0
        ):
            self.selection_model.select_item(0, True)
            self.column_view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)

    def _start_recursive_search(self, search_term: str) -> None:
        if not self.operations:
            return

        base_path = self.current_path or "/"
        self._recursive_search_generation += 1
        generation = self._recursive_search_generation
        self._recursive_search_in_progress = True
        self._showing_recursive_results = True

        # Capture show_hidden setting from main thread (GTK widget)
        show_hidden = (
            self.hidden_files_toggle.get_active()
            if hasattr(self, "hidden_files_toggle")
            else False
        )

        # Update UI to show searching state
        self._update_recursive_search_ui_state()

        if hasattr(self, "search_entry"):
            if getattr(self.search_entry, "has_focus", False):
                if hasattr(self, "column_view") and self.column_view:
                    self.column_view.grab_focus()
            self.search_entry.set_sensitive(False)
            self._update_search_placeholder(_("Searching..."))

        thread = threading.Thread(
            target=self._recursive_search_thread,
            args=(generation, base_path, search_term, show_hidden),
            daemon=True,
            name="RecursiveSearchThread",
        )
        thread.start()

    def _recursive_search_thread(
        self, generation: int, base_path: str, search_term: str, show_hidden: bool
    ):
        """Task 6: Memory-efficient recursive search using line-by-line processing.

        Uses subprocess.Popen to read stdout line-by-line instead of loading
        the entire output into memory at once. This keeps RAM usage stable
        even for directories with many files.
        """
        operations = self.operations
        if self._is_destroyed or not operations:
            self._schedule_search_complete(generation, [], "Search cancelled", False)
            return

        use_fd = self._check_fd_available(operations)
        command = self._build_search_command(
            base_path, search_term, show_hidden, use_fd
        )
        base_posix = PurePosixPath(base_path)

        try:
            if self._is_remote_session():
                results, error_message, truncated = self._search_remote(
                    generation, command, base_posix, use_fd, operations
                )
            else:
                results, error_message, truncated = self._search_local(
                    generation, command, base_posix, use_fd
                )
        except subprocess.TimeoutExpired:
            results, error_message, truncated = [], "Search timed out", False
        except Exception as exc:
            results, error_message, truncated = [], str(exc), False

        self._schedule_search_complete(generation, results, error_message, truncated)

    def _build_search_command(
        self, base_path: str, search_term: str, show_hidden: bool, use_fd: bool
    ) -> list[str]:
        """Build the appropriate search command based on available tools."""
        if use_fd:
            return self._build_fd_command(base_path, search_term, show_hidden)
        return self._build_find_command(base_path, search_term, show_hidden)

    def _search_remote(
        self,
        generation: int,
        command: list[str],
        base_posix: PurePosixPath,
        use_fd: bool,
        operations,
    ) -> tuple[list, str, bool]:
        """Execute remote search and process results."""
        results: list[FileItem] = []
        error_message = ""
        truncated = False

        success, output = operations.execute_command_on_session(command)
        if not success:
            return results, output.strip(), False

        for line in output.splitlines():
            if self._recursive_search_generation != generation:
                return [], "", False

            if not line or (not use_fd and line.startswith("find:")):
                continue

            file_item = self._process_search_result_line(line, base_posix)
            if file_item:
                results.append(file_item)
                if len(results) >= MAX_RECURSIVE_RESULTS:
                    return results, "", True

        return results, error_message, truncated

    def _search_local(
        self,
        generation: int,
        command: list[str],
        base_posix: PurePosixPath,
        use_fd: bool,
    ) -> tuple[list, str, bool]:
        """Execute local search with memory-efficient line-by-line reading."""
        results: list[FileItem] = []
        error_message = ""

        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        ) as proc:
            truncated = self._process_search_output(
                proc, generation, base_posix, use_fd, results
            )
            error_message = self._get_process_error(proc)

        return results, error_message, truncated

    def _process_search_output(
        self,
        proc,
        generation: int,
        base_posix: PurePosixPath,
        use_fd: bool,
        results: list,
    ) -> bool:
        """Process search output line by line. Returns True if truncated."""
        for line in proc.stdout:
            if self._recursive_search_generation != generation:
                proc.terminate()
                return False

            line = line.rstrip("\n")
            if not line or (not use_fd and line.startswith("find:")):
                continue

            file_item = self._process_search_result_line(line, base_posix)
            if file_item:
                results.append(file_item)
                if len(results) >= MAX_RECURSIVE_RESULTS:
                    proc.terminate()
                    return True
        return False

    def _get_process_error(self, proc) -> str:
        """Get error message from process if any."""
        if proc.returncode and proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr else ""
            if stderr:
                return stderr.strip()
        return ""

    def _schedule_search_complete(
        self, generation: int, results: list, error_message: str, truncated: bool
    ) -> None:
        """Schedule completion callback on main thread."""
        GLib.idle_add(
            self._complete_recursive_search,
            generation,
            results,
            error_message,
            truncated,
        )

    def _process_search_result_line(
        self, line: str, base_posix: PurePosixPath
    ) -> Optional[FileItem]:
        """Process a single line from search output and return a FileItem."""
        file_item = FileItem.from_ls_line(line)
        if not file_item:
            return None

        full_path = PurePosixPath(file_item.name)
        try:
            relative_path = str(full_path.relative_to(base_posix))
        except ValueError:
            relative_path = str(full_path)

        relative_path = relative_path.lstrip("./")
        if not relative_path:
            relative_path = full_path.name

        file_item._name = relative_path
        return file_item

    def _check_fd_available(
        self, operations: Optional["FileOperations"] = None
    ) -> bool:
        """Check if fd or fdfind command is available locally or on remote session.

        Args:
            operations: Optional FileOperations instance for thread-safe access.
                       If None, uses self.operations (not thread-safe).
        """
        import shutil

        # Use provided operations or fall back to instance attribute
        ops = operations if operations is not None else self.operations

        # Check for fd (common name) or fdfind (Debian/Ubuntu name)
        for cmd_name in ["fd", "fdfind"]:
            if self._is_remote_session():
                # For remote sessions, use 'command -v' which works via SSH shell
                if ops:
                    success, _ = ops.execute_command_on_session(
                        [
                            "command",
                            "-v",
                            cmd_name,
                        ]
                    )
                    if success:
                        self._fd_command_name = cmd_name
                        return True
            else:
                # For local, use shutil.which() which is reliable
                if shutil.which(cmd_name):
                    self._fd_command_name = cmd_name
                    return True
        return False

    def _build_fd_command(
        self, base_path: str, search_term: str, show_hidden: bool
    ) -> List[str]:
        """Build fd command for recursive search.

        Uses fd for fast searching, then pipes through xargs ls to get
        consistent output format that FileItem.from_ls_line can parse.

        Args:
            base_path: Directory to search in
            search_term: Pattern to search for
            show_hidden: Whether to include hidden files/directories
        """
        fd_cmd = getattr(self, "_fd_command_name", "fd")
        # fd options:
        # -i: case-insensitive
        # -H: include hidden files (only if show_hidden is True)
        # -0: null-separated output for safe xargs
        # --color=never: no color codes
        #
        # We use a shell to pipe fd output through xargs ls for consistent format
        hidden_flag = "-H" if show_hidden else ""

        # SECURITY: Use shlex.quote to prevent shell injection
        # User input (search_term) and path (base_path) must be properly escaped
        safe_search_term = shlex.quote(search_term)
        safe_base_path = shlex.quote(base_path)

        return [
            "sh",
            "-c",
            f"{fd_cmd} -i {hidden_flag} -0 --color=never {safe_search_term} {safe_base_path} | xargs -0 ls -ld --full-time --classify 2>/dev/null",
        ]

    def _build_find_command(
        self, base_path: str, search_term: str, show_hidden: bool
    ) -> List[str]:
        """Build find command for recursive search (fallback).

        Args:
            base_path: Directory to search in
            search_term: Pattern to search for
            show_hidden: Whether to include hidden files/directories
        """
        pattern = f"*{search_term}*"

        if show_hidden:
            # Include all files
            return [
                "find",
                base_path,
                "-iname",
                pattern,
                "-exec",
                "ls",
                "-ld",
                "--full-time",
                "--classify",
                "{}",
                "+",
            ]
        else:
            # Exclude hidden files and directories (those starting with .)
            # -not -path '*/.*' excludes anything inside hidden directories
            return [
                "find",
                base_path,
                "-not",
                "-path",
                "*/.*",
                "-iname",
                pattern,
                "-exec",
                "ls",
                "-ld",
                "--full-time",
                "--classify",
                "{}",
                "+",
            ]

    def _complete_recursive_search(
        self,
        generation: int,
        file_items: List[FileItem],
        error_message: str,
        truncated: bool,
    ):
        """Complete the recursive search and update UI.

        Returns False for GLib.idle_add callback compatibility.
        """
        if generation != self._recursive_search_generation:
            return False

        self._recursive_search_in_progress = False
        self._showing_recursive_results = self.recursive_search_enabled

        # Update UI to hide spinner and show search button again
        self._update_recursive_search_ui_state()

        if hasattr(self, "search_entry"):
            self.search_entry.set_sensitive(True)
            self._update_search_placeholder()

        if not self.recursive_search_enabled:
            return False

        if truncated and not error_message:
            error_message = _(
                "Showing first {count} results. Refine your search to narrow the list."
            ).format(count=MAX_RECURSIVE_RESULTS)

        if error_message:
            self.logger.warning(f"Recursive search warning: {error_message}")

        self.store.splice(0, self.store.get_n_items(), file_items)
        self.combined_filter.changed(Gtk.FilterChange.DIFFERENT)

        if (
            self.selection_model
            and file_items
            and self.selection_model.get_n_items() > 0
        ):
            self.selection_model.select_item(0, True)
            if hasattr(self, "column_view") and self.column_view:
                self.column_view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)

        return False

    def _on_search_activate(self, search_entry):
        """Handle activation (Enter key) on the search entry."""
        search_term = search_entry.get_text().strip()

        # In recursive mode, Enter has dual behavior:
        # - If we're showing results and have a selection, activate the selection
        # - Otherwise, start/restart the search
        if self.recursive_search_enabled:
            # If we have results showing and something is selected, navigate to it
            if (
                self._showing_recursive_results
                and self.selection_model
                and self.selection_model.get_selection().get_size() > 0
            ):
                position = self.selection_model.get_selection().get_nth(0)
                GLib.idle_add(self._deferred_activate_row, self.column_view, position)
                return

            # Otherwise, start the search if there's a search term
            if search_term and not self._recursive_search_in_progress:
                self._start_recursive_search(search_term)
            return

        # In normal mode, Enter opens the selected item
        if self.selection_model and self.selection_model.get_selection().get_size() > 0:
            position = self.selection_model.get_selection().get_nth(0)
            GLib.idle_add(self._deferred_activate_row, self.column_view, position)

    def _on_search_delete_text(self, search_entry, start_pos, end_pos):
        """Handle text deletion in search entry for backspace navigation."""
        current_text = search_entry.get_text()
        if start_pos == 0 and end_pos == len(current_text):
            GLib.idle_add(self._navigate_up_directory)
