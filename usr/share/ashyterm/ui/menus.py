import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio, Gtk

from ..utils.translation_utils import _


class ZoomWidget(Gtk.Box):
    """Custom zoom widget for menu - horizontal layout like GNOME Console."""

    def __init__(self, parent_window):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.parent_window = parent_window

        # Add CSS class for styling
        self.add_css_class("zoom-widget")

        # Zoom out button
        zoom_out_btn = Gtk.Button(label="âˆ'")
        zoom_out_btn.add_css_class("flat")
        zoom_out_btn.connect("clicked", self._on_zoom_out)

        # Zoom level label
        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.set_halign(Gtk.Align.CENTER)

        # Zoom in button
        zoom_in_btn = Gtk.Button(label="+")
        zoom_in_btn.add_css_class("flat")
        zoom_in_btn.connect("clicked", self._on_zoom_in)

        # Add to box
        self.append(zoom_out_btn)
        self.append(self.zoom_label)
        self.append(zoom_in_btn)

    def _on_zoom_out(self, button):
        """Handle zoom out button."""
        if hasattr(self.parent_window, "activate_action"):
            self.parent_window.activate_action("zoom-out", None)

    def _on_zoom_in(self, button):
        """Handle zoom in button."""
        if hasattr(self.parent_window, "activate_action"):
            self.parent_window.activate_action("zoom-in", None)

    def update_zoom_level(self, scale: float):
        """Update the zoom percentage display."""
        percentage = int(scale * 100)
        self.zoom_label.set_text(f"{percentage}%")


class MainApplicationMenu:
    """Factory for creating the main application menu."""

    @staticmethod
    def create_menu(parent_window=None) -> Gio.Menu:
        """
        Create the main application menu structure.

        Returns:
            Gio.Menu object for the main menu
        """
        main_menu = Gio.Menu()

        # Terminal actions section
        terminal_section = Gio.Menu()
        terminal_section.append(_("New Tab"), "win.new-local-tab")
        terminal_section.append(_("Close Tab"), "win.close-tab")
        terminal_section.append(_("New Window"), "win.new-window")
        main_menu.append_section(None, terminal_section)

        # Custom widget like GNOME Console
        zoom_section = Gio.Menu()
        zoom_section.append(_("Zoom Out (-)"), "win.zoom-out")
        zoom_section.append(_("Reset Zoom (100%)"), "win.zoom-reset")
        zoom_section.append(_("Zoom In (+)"), "win.zoom-in")
        main_menu.append_section(None, zoom_section)

        # Edit actions section
        edit_section = Gio.Menu()
        edit_section.append(_("Copy"), "win.copy")
        edit_section.append(_("Paste"), "win.paste")
        edit_section.append(_("Select All"), "win.select-all")
        main_menu.append_section(None, edit_section)

        # Settings and help section
        settings_section = Gio.Menu()
        settings_section.append(_("Preferences"), "win.preferences")
        settings_section.append(_("Keyboard Shortcuts"), "win.shortcuts")
        main_menu.append_section(None, settings_section)

        # Application section
        app_section = Gio.Menu()
        app_section.append(_("About"), "app.about")
        app_section.append(_("Quit"), "app.quit")
        main_menu.append_section(None, app_section)

        return main_menu


def create_session_menu(
    session_item,
    session_store,
    position,
    folder_store=None,
    clipboard_has_content=False,
) -> Gio.Menu:
    """
    Factory function to create a session context menu model.
    """
    menu = Gio.Menu()

    # Add SFTP option if the session is of type SSH
    if session_item.is_ssh():
        sftp_item = Gio.MenuItem.new(_("Connect with SFTP"), "win.connect-sftp")
        sftp_item.set_icon(Gio.ThemedIcon.new("folder-remote-symbolic"))
        menu.append_item(sftp_item)
        menu.append_section(None, Gio.Menu())  # Add a separator

    # Basic session operations
    menu.append_item(Gio.MenuItem.new(_("Edit"), "win.edit-session"))
    menu.append_item(Gio.MenuItem.new(_("Duplicate"), "win.duplicate-session"))
    menu.append_item(Gio.MenuItem.new(_("Rename"), "win.rename-session"))

    # Clipboard operations
    menu.append_section(None, Gio.Menu())
    menu.append_item(Gio.MenuItem.new(_("Cut"), "win.cut-item"))
    menu.append_item(Gio.MenuItem.new(_("Copy"), "win.copy-item"))

    # Organization operations
    menu.append_section(None, Gio.Menu())

    if folder_store and folder_store.get_n_items() > 0:
        menu.append_item(
            Gio.MenuItem.new(_("Move to Folder"), "win.move-session-to-folder")
        )

    # Destructive operation at the end
    menu.append_item(Gio.MenuItem.new(_("Delete"), "win.delete-session"))

    return menu


def create_folder_menu(
    folder_item,
    folder_store,
    position,
    session_store=None,
    clipboard_has_content=False,
) -> Gio.Menu:
    """
    Factory function to create a folder context menu model.
    """
    menu = Gio.Menu()

    # Basic folder operations
    menu.append_item(Gio.MenuItem.new(_("Edit"), "win.edit-folder"))
    menu.append_item(Gio.MenuItem.new(_("Add Session"), "win.add-session-to-folder"))
    menu.append_item(Gio.MenuItem.new(_("Rename"), "win.rename-folder"))

    # Clipboard operations
    menu.append_section(None, Gio.Menu())
    menu.append_item(Gio.MenuItem.new(_("Cut"), "win.cut-item"))
    menu.append_item(Gio.MenuItem.new(_("Copy"), "win.copy-item"))

    if clipboard_has_content:
        menu.append_item(Gio.MenuItem.new(_("Paste"), "win.paste-item"))

    # Destructive operation at the end
    menu.append_section(None, Gio.Menu())
    menu.append_item(Gio.MenuItem.new(_("Delete"), "win.delete-folder"))

    return menu


def create_root_menu(clipboard_has_content=False) -> Gio.Menu:
    """
    Factory function to create a root tree view context menu model.
    """
    menu = Gio.Menu()

    # Add new items
    menu.append_item(Gio.MenuItem.new(_("Add Session"), "win.add-session-root"))
    menu.append_item(Gio.MenuItem.new(_("Add Folder"), "win.add-folder-root"))

    # Paste if available
    if clipboard_has_content:
        menu.append_section(None, Gio.Menu())
        menu.append_item(Gio.MenuItem.new(_("Paste to Root"), "win.paste-item-root"))

    return menu


def create_terminal_menu(terminal, click_x=None, click_y=None) -> Gio.Menu:
    """
    Factory function to create a terminal context menu model.

    Args:
        terminal: Vte.Terminal widget
        click_x: X coordinate of right-click (for URL detection)
        click_y: Y coordinate of right-click (for URL detection)
    """
    menu = Gio.Menu()

    # Check for URL at click position
    url_at_click = None
    if click_x is not None and click_y is not None and hasattr(terminal, "match_check"):
        try:
            char_width = terminal.get_char_width()
            char_height = terminal.get_char_height()

            if char_width > 0 and char_height > 0:
                col = int(click_x / char_width)
                row = int(click_y / char_height)

                match_result = terminal.match_check(col, row)
                if match_result and len(match_result) >= 2:
                    matched_text = match_result[0]
                    if matched_text and _is_valid_url_simple(matched_text):
                        url_at_click = matched_text
        except Exception:
            pass

    # URL section (if URL detected)
    if url_at_click:
        url_section = Gio.Menu()

        # Store URL in terminal for actions
        terminal._context_menu_url = url_at_click

        url_section.append(_("Open Link"), "win.open-url")
        url_section.append(_("Copy Link"), "win.copy-url")
        menu.append_section(None, url_section)

    # Standard terminal operations
    standard_section = Gio.Menu()
    standard_section.append(_("Copy"), "win.copy")
    standard_section.append(_("Paste"), "win.paste")
    standard_section.append(_("Select All"), "win.select-all")
    menu.append_section(None, standard_section)

    # Splitting options
    split_section = Gio.Menu()

    # Create menu item for horizontal split
    split_h_item = Gio.MenuItem.new(_("Split Horizontally"), "win.split-horizontal")
    split_h_item.set_icon(Gio.ThemedIcon.new("view-split-horizontal-symbolic"))
    split_section.append_item(split_h_item)

    # Create menu item for vertical split
    split_v_item = Gio.MenuItem.new(_("Split Vertically"), "win.split-vertical")
    split_v_item.set_icon(Gio.ThemedIcon.new("view-split-vertical-symbolic"))
    split_section.append_item(split_v_item)

    split_section.append_item(Gio.MenuItem.new(_("Close Pane"), "win.close-pane"))

    menu.append_section(None, split_section)

    return menu


def _is_valid_url_simple(text: str) -> bool:
    """Simple URL validation for menu."""
    text = text.strip()
    return any(text.startswith(scheme) for scheme in ["http://", "https://", "ftp://"])


def create_file_item_menu(file_item, is_remote_session=False) -> Gio.Menu:
    menu = Gio.Menu()

    # Basic file operations
    menu.append_item(Gio.MenuItem.new(_("Rename"), "win.file-rename"))

    # File management section
    file_ops_section = Gio.Menu()
    move_item = Gio.MenuItem.new(_("Move"), "win.file-move")
    move_item.set_icon(Gio.ThemedIcon.new("edit-cut-symbolic"))
    file_ops_section.append_item(move_item)

    copy_item = Gio.MenuItem.new(_("Copy"), "win.file-copy")
    copy_item.set_icon(Gio.ThemedIcon.new("edit-copy-symbolic"))
    file_ops_section.append_item(copy_item)
    menu.append_section(None, file_ops_section)

    # Permissions
    menu.append_item(Gio.MenuItem.new(_("Change Permissions"), "win.file-chmod"))

    # Only show transfer options for remote (SSH/SFTP) sessions
    if is_remote_session:
        transfer_section = Gio.Menu()
        if not file_item.is_directory:
            download_item = Gio.MenuItem.new(_("Download File"), "win.file-download")
            download_item.set_icon(Gio.ThemedIcon.new("folder-download-symbolic"))
            transfer_section.append_item(download_item)
            edit_item = Gio.MenuItem.new(_("Edit with Local App"), "win.file-edit")
            edit_item.set_icon(Gio.ThemedIcon.new("document-edit-symbolic"))
            transfer_section.append_item(edit_item)
        upload_item = Gio.MenuItem.new(_("Upload Files Here"), "win.file-upload")
        upload_item.set_icon(Gio.ThemedIcon.new("folder-upload-symbolic"))
        transfer_section.append_item(upload_item)
        menu.append_section(None, transfer_section)

    delete_section = Gio.Menu()
    delete_item = Gio.MenuItem.new(_("Delete"), "win.file-delete")
    delete_item.set_icon(Gio.ThemedIcon.new("user-trash-symbolic"))
    delete_section.append_item(delete_item)
    menu.append_section(None, delete_section)
    return menu
