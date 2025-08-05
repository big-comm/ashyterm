import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gio
from typing import Optional

from ..utils.translation_utils import _


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
        menu.append_item(Gio.MenuItem.new(_("Copy"), "win.copy"))
        menu.append_item(Gio.MenuItem.new(_("Paste"), "win.paste"))
        menu.append_item(Gio.MenuItem.new(_("Select All"), "win.select-all"))
        
        # Initialize the menu by passing the model directly
        super().__init__(menu_model=menu)
        self.set_parent(parent_window)
        self.set_has_arrow(False)


class MainApplicationMenu:
    """Factory for creating the main application menu."""
    
    @staticmethod
    def create_menu() -> Gio.Menu:
        """
        Create the main application menu structure.
        
        Returns:
            Gio.Menu object for the main menu
        """
        main_menu = Gio.Menu()
        
        # Terminal actions section
        main_menu.append(_("Nova Aba"), "win.new-local-tab")
        main_menu.append(_("Fechar Aba"), "win.close-tab")
        main_menu.append(_("Nova Janela"), "win.new-window")
        
        # Edit actions section
        main_menu.append_section(None, Gio.Menu())
        main_menu.append(_("Copiar"), "win.copy")
        main_menu.append(_("Colar"), "win.paste")
        main_menu.append(_("Selecionar Tudo"), "win.select-all")
        
        # Settings and help section
        main_menu.append_section(None, Gio.Menu())
        main_menu.append(_("Preferências"), "win.preferences")
        main_menu.append(_("Atalhos de Teclado"), "win.shortcuts")
        
        # Application section
        main_menu.append_section(None, Gio.Menu())
        main_menu.append(_("Sobre"), "app.about")
        main_menu.append(_("Sair"), "app.quit")
        
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