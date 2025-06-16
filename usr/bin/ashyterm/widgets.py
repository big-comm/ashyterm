# ashyterm/widgets.py
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, Gdk

class SessionContextMenu(Gtk.PopoverMenu):
    def __init__(self, parent_window, session_item, session_store, position, folder_store=None, clipboard_has_content=False):
        super().__init__()

        menu = Gio.Menu()
        menu.append_item(Gio.MenuItem.new("Editar", "win.edit-session"))
        menu.append_item(Gio.MenuItem.new("Duplicar", "win.duplicate-session"))
        menu.append_item(Gio.MenuItem.new("Renomear", "win.rename-session"))
        
        # --- INÍCIO DA ALTERAÇÃO ---
        menu.append_section(None, Gio.Menu())
        menu.append_item(Gio.MenuItem.new("Recortar", "win.cut-item"))
        menu.append_item(Gio.MenuItem.new("Copiar", "win.copy-item"))
        # A opção "Copiar Comando SSH" foi removida.
        # --- FIM DA ALTERAÇÃO ---

        menu.append_section(None, Gio.Menu())

        if folder_store and folder_store.get_n_items() > 0:
            menu.append_item(Gio.MenuItem.new("Mover para Pasta", "win.move-session-to-folder"))

        menu.append_item(Gio.MenuItem.new("Excluir", "win.delete-session"))

        self.set_menu_model(menu)
        self.set_has_arrow(False)

class FolderContextMenu(Gtk.PopoverMenu):
    def __init__(self, parent_window, folder_item, folder_store, position, session_store=None, clipboard_has_content=False):
        super().__init__()

        menu = Gio.Menu()
        menu.append_item(Gio.MenuItem.new("Editar", "win.edit-folder"))
        menu.append_item(Gio.MenuItem.new("Adicionar Sessão", "win.add-session-to-folder"))
        menu.append_item(Gio.MenuItem.new("Renomear", "win.rename-folder"))

        # --- INÍCIO DA ALTERAÇÃO ---
        menu.append_section(None, Gio.Menu())
        menu.append_item(Gio.MenuItem.new("Recortar", "win.cut-item"))
        menu.append_item(Gio.MenuItem.new("Copiar", "win.copy-item"))

        if clipboard_has_content:
            menu.append_item(Gio.MenuItem.new("Colar", "win.paste-item"))
        # --- FIM DA ALTERAÇÃO ---

        menu.append_section(None, Gio.Menu())
        menu.append_item(Gio.MenuItem.new("Excluir", "win.delete-folder"))

        self.set_menu_model(menu)
        self.set_has_arrow(False)

class RootTreeViewContextMenu(Gtk.PopoverMenu):
    def __init__(self, parent_window, clipboard_has_content=False):
        super().__init__()
        menu = Gio.Menu()
        menu.append_item(Gio.MenuItem.new("Adicionar Sessão", "win.add-session-root"))
        menu.append_item(Gio.MenuItem.new("Adicionar Pasta", "win.add-folder-root"))

        if clipboard_has_content:
            menu.append_section(None, Gio.Menu())
            menu.append_item(Gio.MenuItem.new("Colar na Raiz", "win.paste-item-root"))
        
        self.set_menu_model(menu)
        self.set_has_arrow(False)


class TerminalContextMenu(Gtk.PopoverMenu):
    def __init__(self, parent_window, terminal):
        super().__init__()
        menu = Gio.Menu()
        menu.append_item(Gio.MenuItem.new("Copiar", "win.copy"))
        menu.append_item(Gio.MenuItem.new("Colar", "win.paste"))
        menu.append_item(Gio.MenuItem.new("Selecionar Tudo", "win.select-all"))

        self.set_menu_model(menu)
        self.set_has_arrow(False)