# ashyterm/ui/menus.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk

from ..helpers import accelerator_to_label
from ..settings.config import DefaultSettings
from ..settings.manager import SettingsManager
from ..utils.translation_utils import _


class ThemeSelectorWidget(Gtk.Box):
    """Custom widget for selecting the GTK color scheme."""

    def __init__(self, settings_manager: SettingsManager):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)
        self.settings_manager = settings_manager
        self.style_manager = Adw.StyleManager.get_default()

        self.add_css_class("themeselector")
        self.set_halign(Gtk.Align.CENTER)

        # System Theme Button
        self.system_button = Gtk.CheckButton()
        self.system_button.add_css_class("follow")
        self.system_button.add_css_class("theme-selector")
        self.system_button.set_tooltip_text(_("Follow System Style"))
        self.system_button.connect("toggled", self._on_theme_changed, "default")

        # Light Theme Button
        self.light_button = Gtk.CheckButton(group=self.system_button)
        self.light_button.add_css_class("light")
        self.light_button.add_css_class("theme-selector")
        self.light_button.set_tooltip_text(_("Light Style"))
        self.light_button.connect("toggled", self._on_theme_changed, "light")

        # Dark Theme Button
        self.dark_button = Gtk.CheckButton(group=self.system_button)
        self.dark_button.add_css_class("dark")
        self.dark_button.add_css_class("theme-selector")
        self.dark_button.set_tooltip_text(_("Dark Style"))
        self.dark_button.connect("toggled", self._on_theme_changed, "dark")

        self.append(self.system_button)
        self.append(self.light_button)
        self.append(self.dark_button)

        self._update_button_state()

    def _on_theme_changed(self, button: Gtk.CheckButton, theme: str):
        if not button.get_active():
            return

        if theme == "light":
            self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif theme == "dark":
            self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            self.style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

        self.settings_manager.set("gtk_theme", theme)

    def _update_button_state(self):
        current_theme = self.settings_manager.get("gtk_theme", "default")
        if current_theme == "light":
            self.light_button.set_active(True)
        elif current_theme == "dark":
            self.dark_button.set_active(True)
        else:
            self.system_button.set_active(True)


class FontSizerWidget(Gtk.CenterBox):
    """Custom widget for changing the base font size with a centered layout."""

    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self.settings_manager = parent_window.settings_manager

        # --- Center Widget: Zoom Controls ---
        zoom_controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        zoom_controls_box.add_css_class("navigation-sidebar")

        decrement_btn = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        decrement_btn.add_css_class("flat")
        decrement_btn.connect("clicked", self._on_decrement)

        font_size_button = Gtk.Button()
        font_size_button.add_css_class("flat")
        font_size_button.connect("clicked", self._on_reset)

        self.font_size_label = Gtk.Label(label="12 pt")
        self.font_size_label.set_halign(Gtk.Align.CENTER)
        self.font_size_label.set_size_request(60, -1)
        font_size_button.set_child(self.font_size_label)

        increment_btn = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        increment_btn.add_css_class("flat")
        increment_btn.connect("clicked", self._on_increment)

        zoom_controls_box.append(decrement_btn)
        zoom_controls_box.append(font_size_button)
        zoom_controls_box.append(increment_btn)

        # Set the children of the CenterBox
        self.set_center_widget(zoom_controls_box)

        self.update_display()

    def _parse_font_string(self, font_string: str) -> tuple[str, int]:
        """Parses a font string like 'Family Name 12' into ('Family Name', 12)."""
        try:
            parts = font_string.rsplit(" ", 1)
            family = parts[0]
            size = int(parts[1])
            return family, size
        except (IndexError, ValueError):
            return "Monospace", 12

    def _change_font_size(self, delta: int):
        """Changes the font size by a given delta."""
        current_font = self.settings_manager.get("font")
        family, size = self._parse_font_string(current_font)
        new_size = max(6, min(72, size + delta))
        new_font_string = f"{family} {new_size}"
        self.settings_manager.set("font", new_font_string)

    def _on_decrement(self, button):
        self._change_font_size(-1)

    def _on_increment(self, button):
        self._change_font_size(1)

    def _on_reset(self, button):
        default_font = DefaultSettings.get_defaults()["font"]
        self.settings_manager.set("font", default_font)

    def update_display(self):
        """Updates the label to show the current font size."""
        font_string = self.settings_manager.get("font")
        _family, size = self._parse_font_string(font_string)
        self.font_size_label.set_text(f"{size} pt")


class MainApplicationMenu:
    """Factory for creating the main application popover menu."""

    @staticmethod
    def create_main_popover(parent_window) -> tuple[Gtk.Popover, FontSizerWidget]:
        """
        Creates a Gtk.Popover with a modern layout for the main menu.

        Args:
            parent_window: The main CommTerminalWindow instance.

        Returns:
            A tuple containing the configured Gtk.Popover and the FontSizerWidget instance.
        """
        popover = Gtk.Popover()
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.add_css_class("main-menu-popover")
        popover.set_child(main_box)

        # --- Theme and Font Sizer Section ---
        theme_selector = ThemeSelectorWidget(parent_window.settings_manager)
        font_sizer_widget = FontSizerWidget(parent_window)

        main_box.append(theme_selector)
        main_box.append(font_sizer_widget)
        main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- Menu Items ---
        menu_items = [
            {"label": _("New Window"), "action": "win.new-window"},
            {"label": _("Preferences"), "action": "win.preferences"},
            {"label": _("Keyboard Shortcuts"), "action": "win.shortcuts"},
            {"label": _("About"), "action": "app.about"},
            "---",
            {"label": _("Quit"), "action": "app.quit"},
        ]

        # Define which actions should close the popover upon being clicked
        actions_that_close_menu = {
            "win.new-window",
            "win.preferences",
            "win.shortcuts",
            "app.about",
        }

        app = parent_window.get_application()

        for item in menu_items:
            if item == "---":
                main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                main_box.add_css_class("navigation-sidebar")
            else:
                button = Gtk.Button()
                button.set_action_name(item["action"])
                button.add_css_class("flat")
                button.add_css_class("body")
                button.set_halign(Gtk.Align.FILL)

                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
                button.set_child(box)

                action_label = Gtk.Label(label=item["label"], xalign=0.0, hexpand=True)
                box.append(action_label)

                accels = app.get_accels_for_action(item["action"])
                if accels:
                    shortcut_label = Gtk.Label(xalign=1.0)
                    shortcut_label.add_css_class("dim-label")
                    shortcut_label.set_text(accelerator_to_label(accels[0]))
                    box.append(shortcut_label)

                # If this button's action should close the menu, connect the signal.
                if item["action"] in actions_that_close_menu:
                    button.connect("clicked", lambda b: popover.popdown())

                main_box.append(button)

        return popover, font_sizer_widget


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
