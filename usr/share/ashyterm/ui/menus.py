import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gio, GLib
from typing import Optional

from ..utils.translation_utils import _

class ZoomWidget(Gtk.Box):
    """Custom zoom widget for menu - horizontal layout like GNOME Console."""
    
    def __init__(self, parent_window):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.parent_window = parent_window
        
        # Add CSS class for styling
        self.add_css_class("zoom-widget")
        
        # Zoom out button
        zoom_out_btn = Gtk.Button(label="−")
        zoom_out_btn.set_size_request(30, -1)
        zoom_out_btn.add_css_class("flat")
        zoom_out_btn.connect("clicked", self._on_zoom_out)
        
        # Zoom level label
        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.set_size_request(50, -1)
        self.zoom_label.set_halign(Gtk.Align.CENTER)
        
        # Zoom in button  
        zoom_in_btn = Gtk.Button(label="+")
        zoom_in_btn.set_size_request(30, -1)
        zoom_in_btn.add_css_class("flat")
        zoom_in_btn.connect("clicked", self._on_zoom_in)
        
        # Add to box
        self.append(zoom_out_btn)
        self.append(self.zoom_label)
        self.append(zoom_in_btn)
    
    def _on_zoom_out(self, button):
        """Handle zoom out button."""
        if hasattr(self.parent_window, 'activate_action'):
            self.parent_window.activate_action('zoom-out', None)
    
    def _on_zoom_in(self, button):
        """Handle zoom in button."""
        if hasattr(self.parent_window, 'activate_action'):
            self.parent_window.activate_action('zoom-in', None)
    
    def update_zoom_level(self, scale: float):
        """Update the zoom percentage display."""
        percentage = int(scale * 100)
        self.zoom_label.set_text(f"{percentage}%")


class SessionContextMenu(Gtk.PopoverMenu):
    """Context menu for session items in the tree view."""
    
    def __init__(self, parent_window, session_item, session_store, position, 
                 folder_store=None, clipboard_has_content=False):
        """
        Initialize session context menu.
        ...
        """
        menu = Gio.Menu()
        
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
            menu.append_item(Gio.MenuItem.new(_("Move to Folder"), "win.move-session-to-folder"))
        
        # Destructive operation at the end
        menu.append_item(Gio.MenuItem.new(_("Delete"), "win.delete-session"))
        
        # Initialize the menu by passing the model directly
        super().__init__(menu_model=menu)
        self.set_parent(parent_window) 
        self.set_has_arrow(False)


class FolderContextMenu(Gtk.PopoverMenu):
    """Context menu for folder items in the tree view."""
    
    def __init__(self, parent_window, folder_item, folder_store, position, 
                 session_store=None, clipboard_has_content=False):
        """
        Initialize folder context menu.
        ...
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
        
        # Initialize the menu by passing the model directly
        super().__init__(menu_model=menu)
        self.set_parent(parent_window) 
        self.set_has_arrow(False)


class RootTreeViewContextMenu(Gtk.PopoverMenu):
    """Context menu for empty areas of the tree view (root level)."""
    
    def __init__(self, parent_window, clipboard_has_content=False):
        """
        Initialize root context menu.
        ...
        """
        menu = Gio.Menu()
        
        # Add new items
        menu.append_item(Gio.MenuItem.new(_("Add Session"), "win.add-session-root"))
        menu.append_item(Gio.MenuItem.new(_("Add Folder"), "win.add-folder-root"))
        
        # Paste if available
        if clipboard_has_content:
            menu.append_section(None, Gio.Menu())
            menu.append_item(Gio.MenuItem.new(_("Paste to Root"), "win.paste-item-root"))
        
        # Initialize the menu by passing the model directly
        super().__init__(menu_model=menu)
        self.set_parent(parent_window)
        self.set_has_arrow(False)


class TerminalContextMenu(Gtk.PopoverMenu):
    """Context menu for terminal widgets."""
    
    def __init__(self, parent_window, terminal):
        """
        Initialize terminal context menu.
        ...
        """
        menu = Gio.Menu()
        
        # Standard terminal operations
        menu.append(_("Copy"), "win.copy")
        menu.append(_("Paste"), "win.paste")
        menu.append(_("Select All"), "win.select-all")
        
        # Splitting options
        split_section = Gio.Menu()
        
        # Create menu item for horizontal split with a more standard icon name
        split_h_item = Gio.MenuItem.new(_("Split Horizontally"), "win.split-horizontal")
        split_h_item.set_icon(Gio.ThemedIcon.new("view-split-horizontal-symbolic"))
        split_section.append_item(split_h_item)
        
        # Create menu item for vertical split with a more standard icon name
        split_v_item = Gio.MenuItem.new(_("Split Vertically"), "win.split-vertical")
        split_v_item.set_icon(Gio.ThemedIcon.new("view-split-vertical-symbolic"))
        split_section.append_item(split_v_item)
        
        menu.append_section(None, split_section)
        
        # Initialize the menu by passing the model directly
        super().__init__(menu_model=menu)
        self.set_parent(parent_window)
        self.set_has_arrow(False)


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
        
        # Widget customizado como no GNOME Console
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


def setup_context_menu(widget: Gtk.Widget, menu: Gtk.PopoverMenu, 
                      x: float, y: float) -> None:
    """
    Helper function to setup and show a context menu.
    
    Args:
        widget: Widget to attach the menu to
        menu: The popover menu to show
        x: X coordinate for menu positioning
        y: Y coordinate for menu positioning
    """
    from gi.repository import Gdk
    
    # Create rectangle for menu positioning
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    
    # Setup and show menu
    menu.set_pointing_to(rect)
    menu.popup()


def create_session_menu(parent_window, session_item, session_store, position,
                       folder_store=None, clipboard_has_content=False) -> SessionContextMenu:
    """
    Factory function to create a session context menu.
    
    Args:
        parent_window: Parent window reference
        session_item: SessionItem for the menu
        session_store: Store containing sessions
        position: Position in store
        folder_store: Optional folder store
        clipboard_has_content: Whether clipboard has content
        
    Returns:
        Configured SessionContextMenu
    """
    return SessionContextMenu(
        parent_window, session_item, session_store, position,
        folder_store, clipboard_has_content
    )


def create_folder_menu(parent_window, folder_item, folder_store, position,
                      session_store=None, clipboard_has_content=False) -> FolderContextMenu:
    """
    Factory function to create a folder context menu.
    
    Args:
        parent_window: Parent window reference
        folder_item: SessionFolder for the menu
        folder_store: Store containing folders
        position: Position in store
        session_store: Optional session store
        clipboard_has_content: Whether clipboard has content
        
    Returns:
        Configured FolderContextMenu
    """
    return FolderContextMenu(
        parent_window, folder_item, folder_store, position,
        session_store, clipboard_has_content
    )


def create_root_menu(parent_window, clipboard_has_content=False) -> RootTreeViewContextMenu:
    """
    Factory function to create a root tree view context menu.
    
    Args:
        parent_window: Parent window reference
        clipboard_has_content: Whether clipboard has content
        
    Returns:
        Configured RootTreeViewContextMenu
    """
    return RootTreeViewContextMenu(parent_window, clipboard_has_content)


def create_terminal_menu(parent_window, terminal) -> TerminalContextMenu:
    """
    Factory function to create a terminal context menu.
    
    Args:
        parent_window: Parent window reference
        terminal: Terminal widget
        
    Returns:
        Configured TerminalContextMenu
    """
    return TerminalContextMenu(parent_window, terminal)