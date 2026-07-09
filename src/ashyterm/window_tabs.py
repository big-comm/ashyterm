"""Window tab management mixin — create, switch, detach, layout."""

from typing import Optional

import gi
from typing import Any

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gtk

from .sessions.models import SessionItem
from .utils.translation_utils import _

APP_TITLE = _("Ashy Terminal")


class WindowTabsMixin:
    """Mixin: tab creation, management, switching, layout updates."""

    # ─── Public API: Tab Creation ──────────────────────────────────────

    def create_local_tab(
        self,
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> Any:
        """Public method to create a local tab."""
        return self.tab_manager.create_local_tab(
            working_directory=working_directory,
            execute_command=execute_command,
            close_after_execute=close_after_execute,
        )

    def create_ssh_tab(self, ssh_target: str) -> Any:
        """Public method to parse an SSH target string and create a tab."""
        try:
            remote_path = None
            if "/" in ssh_target:
                connection_part, remote_path_part = ssh_target.split("/", 1)
                remote_path = "/" + remote_path_part
            else:
                connection_part = ssh_target

            user_host_port = connection_part
            if "@" in connection_part:
                user, user_host_port = connection_part.split("@", 1)
            else:
                user = ""

            if ":" in user_host_port:
                host, port_str = user_host_port.rsplit(":", 1)
                port = int(port_str)
            else:
                host = user_host_port
                port = 22

            session_name = f"{user}@{host}" if user else host

            session = SessionItem(
                name=session_name, session_type="ssh", user=user, host=host, port=port
            )
            initial_command = f"cd '{remote_path}'" if remote_path else None
            return self.tab_manager.create_ssh_tab(
                session, initial_command=initial_command
            )
        except Exception as e:
            self.logger.error(f"Failed to parse SSH target '{ssh_target}': {e}")
            self._show_error_dialog(
                _("Invalid SSH Target"),
                _("Could not parse the provided SSH connection string."),
            )

    def create_execute_tab(
        self, command: str, working_directory: str, close_after: bool
    ) -> Any:
        """Public method to create a tab that executes a command."""
        return self.tab_manager.create_local_tab(
            working_directory=working_directory,
            execute_command=command,
            close_after_execute=close_after,
        )

    # ─── Tab Layout / Title ────────────────────────────────────────────

    def _update_tab_layout(self):
        """Update tab layout and window title based on tab count."""
        tab_count = self.tab_manager.get_tab_count()
        self.set_title(APP_TITLE)

        if tab_count > 1:
            self.title_stack.set_visible_child_name("tabs-view")
        else:
            self.title_stack.set_visible_child_name("title-view")
            if tab_count == 1:
                page = self.tab_manager.view_stack.get_page(
                    self.tab_manager.view_stack.get_visible_child()
                )
                if page:
                    self.single_tab_title_widget.set_title(
                        f"{APP_TITLE} - {page.get_title()}"
                    )
            else:
                self.single_tab_title_widget.set_title(APP_TITLE)

    # ─── Tab Change Handler ────────────────────────────────────────────

    def _on_tab_changed(self, view_stack, _param):
        """Handle tab changes."""
        if not self.tab_manager.active_tab:
            return

        self.search_manager.hide_if_terminal_changed()

        # Pause highlighting on inactive tabs — cheap win for big dumps.
        active_page = self.tab_manager.pages.get(self.tab_manager.active_tab)
        for tab, page in self.tab_manager.pages.items():
            panes: list = []
            self.tab_manager._find_panes_recursive(page.get_child(), panes)
            is_active = page is active_page
            for pane in panes:
                tid = getattr(getattr(pane, "terminal", None), "terminal_id", None)
                if tid is not None:
                    if is_active:
                        self.terminal_manager.resume_highlight_proxy(tid)
                    else:
                        self.terminal_manager.pause_highlight_proxy(tid)

        self._sync_toggle_button_state()
        self._update_font_sizer_widget()
        self._update_tab_layout()

    # ─── Detach Tab ────────────────────────────────────────────────────

    def _on_detach_tab_requested(self, page_to_detach: Adw.ViewStackPage):
        """Orchestrates detaching a tab into a new window."""
        if self.tab_manager.get_tab_count() <= 1:
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Cannot detach the last tab."))
            )
            return

        tab_widget = next(
            (
                tab
                for tab in self.tab_manager.tabs
                if self.tab_manager.pages.get(tab) == page_to_detach
            ),
            None,
        )
        if not tab_widget:
            return

        fm_to_detach = self.tab_manager.file_managers.pop(page_to_detach, None)

        # Collect and deregister all terminals from the tab
        terminals_to_move = []
        terminals_in_page = self.tab_manager.get_all_terminals_in_page(page_to_detach)
        for terminal in terminals_in_page:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id:
                self.terminal_manager._cleanup_highlight_proxy(terminal_id)
                terminal_info = (
                    self.terminal_manager.registry.deregister_terminal_for_move(
                        terminal_id
                    )
                )
                if terminal_info:
                    terminals_to_move.append(
                        {
                            "id": terminal_id,
                            "info": terminal_info,
                            "widget": terminal,
                        }
                    )

        content = page_to_detach.get_child()
        title = tab_widget._base_title
        session = getattr(tab_widget, "session_item", None)
        session_type = session.session_type if session else "local"

        self.tab_manager.view_stack.remove(content)
        self.tab_manager.tab_bar_box.remove(tab_widget)
        self.tab_manager.tabs.remove(tab_widget)
        del self.tab_manager.pages[tab_widget]

        if self.tab_manager.active_tab == tab_widget and self.tab_manager.tabs:
            self.tab_manager.set_active_tab(self.tab_manager.tabs[-1])
        elif not self.tab_manager.tabs:
            self.tab_manager.active_tab = None
            if self.get_application():
                self.close()

        app = self.get_application()
        new_window = app.create_new_window(
            _is_for_detached_tab=True,
            detached_terminals_data=terminals_to_move,
            detached_file_manager=fm_to_detach,
        )
        new_window.tab_manager.re_attach_detached_page(
            content, title, session_type, fm_to_detach
        )

        new_window._update_tab_layout()
        new_window.present()

    # ─── Toggle File Manager ───────────────────────────────────────────

    def _on_toggle_file_manager(self, button: Gtk.ToggleButton):
        """Toggle file manager for the current tab."""
        self.tab_manager.toggle_file_manager_for_active_tab(button.get_active())

    # ─── Font Sizer / Toggle Sync ──────────────────────────────────────

    def _update_font_sizer_widget(self):
        if self.ui_builder.font_sizer_widget:
            self.ui_builder.font_sizer_widget.update_display()

    def _sync_toggle_button_state(self):
        """Synchronize toggle button state with file manager visibility."""
        if not self.tab_manager.active_tab:
            self.file_manager_button.set_active(False)
            return

        page = self.tab_manager.pages.get(self.tab_manager.active_tab)
        if page and hasattr(page, "content_paned"):
            is_visible = page.content_paned.get_end_child() is not None
            if self.file_manager_button.get_active() != is_visible:
                self.file_manager_button.set_active(is_visible)
        else:
            self.file_manager_button.set_active(False)

    # ─── Temp Files / Cleanup Button ───────────────────────────────────

    def _on_temp_files_changed(self, file_manager, count, page):
        """Handle signal from a FileManager about its temp file count."""
        if count > 0:
            self.active_temp_files[file_manager] = count
        elif file_manager in self.active_temp_files:
            del self.active_temp_files[file_manager]

        self._update_cleanup_button_visibility()
        self._populate_cleanup_popover()

    def _update_cleanup_button_visibility(self):
        """Show or hide the cleanup button based on total count of temp files."""
        total_count = sum(self.active_temp_files.values())
        self.cleanup_button.set_visible(total_count > 0)

    def _populate_cleanup_popover(self):
        """Dynamically build the list of temporary files for the popover."""
        if self.cleanup_popover.get_child():
            self.cleanup_popover.set_child(None)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=10,
            margin_bottom=10,
            margin_start=10,
            margin_end=10,
        )
        scrolled = Gtk.ScrolledWindow(
            propagate_natural_height=True, propagate_natural_width=True
        )
        content_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrolled.set_child(content_container)

        has_files = False
        all_files = []
        for fm in self.tab_manager.file_managers.values():
            all_files.extend(fm.get_temp_files_info())

        if all_files:
            has_files = True
            group = Adw.PreferencesGroup()
            content_container.append(group)
            for info in all_files:
                row = Adw.ActionRow(
                    title=info["remote_path"], subtitle=info["session_name"]
                )
                row.set_title_selectable(True)
                remove_button = Gtk.Button(
                    css_classes=["flat", "circular"],
                    tooltip_text=_("Remove this temporary file"),
                )
                from .utils.icons import icon_image

                remove_button.set_child(icon_image("edit-delete-symbolic"))

                fm_to_call = next(
                    (
                        fm
                        for fm in self.tab_manager.file_managers.values()
                        if fm.session_item.name == info["session_name"]
                    ),
                    None,
                )

                if fm_to_call:
                    edit_key = (info["session_name"], info["remote_path"])
                    remove_button.connect(
                        "clicked",
                        lambda _, fm_instance=fm_to_call, key=edit_key: (
                            self._on_clear_single_temp_file_clicked(fm_instance, key)
                        ),
                    )
                row.add_suffix(remove_button)
                group.add(row)

        if not has_files:
            content_container.append(Gtk.Label(label=_("No temporary files found.")))

        clear_button = Gtk.Button(
            label=_("Clear All Temporary Files"),
            css_classes=["destructive-action", "pill"],
            halign=Gtk.Align.CENTER,
            margin_top=10,
        )
        clear_button.connect("clicked", self._on_clear_all_temp_files_clicked)
        clear_button.set_sensitive(has_files)

        box.append(scrolled)
        box.append(clear_button)
        self.cleanup_popover.set_child(box)

    def _on_clear_single_temp_file_clicked(self, file_manager, edit_key):
        """Callback to clear a single temporary file and its directory."""
        file_manager.cleanup_all_temp_files(edit_key)
        self._populate_cleanup_popover()

    def _on_clear_all_temp_files_clicked(self, button):
        """Show confirmation and then clear all temp files."""
        self.cleanup_popover.popdown()
        dialog = Adw.AlertDialog(
            heading=_("Clear All Temporary Files?"),
            body=_(
                "This will remove all locally downloaded files for remote editing. "
                "Any unsaved changes in your editor will be lost. This action cannot be undone."
            ),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear All"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_clear_all_confirm)
        dialog.present(self)

    def _on_clear_all_confirm(self, dialog, response_id):
        if response_id == "clear":
            self.logger.info("User confirmed clearing all temporary files.")
            for fm in self.tab_manager.file_managers.values():
                fm.cleanup_all_temp_files()
