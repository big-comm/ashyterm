#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import shutil
import uuid # Para gerar IDs únicos para itens colados, se necessário para diferenciar
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Callable, Union, cast
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

try:
    gi.require_version("Vte", "3.91")
    VTE_AVAILABLE_FROM_MAIN = True
    print("VTE 3.91 bindings encontrados em main.py. Habilitando terminal embutido.")
except (ValueError, ImportError):
    VTE_AVAILABLE_FROM_MAIN = False
    print("VTE 3.91 bindings não encontrados em main.py. Usando terminal externo como fallback.")

from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk
if VTE_AVAILABLE_FROM_MAIN:
    from gi.repository import Vte, Pango

Adw.init()

from ashyterm.config import (
    APP_ID, APP_TITLE, DEFAULT_SETTINGS, COLOR_SCHEMES, COLOR_SCHEME_MAP,
    SSH_CONNECT_TIMEOUT, VTE_AVAILABLE as CONFIG_VTE_AVAILABLE
)
from ashyterm.models import SessionItem, SessionFolder
from ashyterm.dialogs import SessionEditDialog, FolderEditDialog, PreferencesDialog
from ashyterm.widgets import SessionContextMenu, FolderContextMenu, TerminalContextMenu, RootTreeViewContextMenu
from ashyterm.utils import save_sessions_and_folders, load_sessions_and_folders, load_settings, save_settings

class CommTerminalWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(1200, 700)
        self.set_title(APP_TITLE)
        self.settings = load_settings()
        
        self._internal_clipboard: Optional[Union[SessionItem, SessionFolder]] = None
        self._internal_clipboard_is_cut = False
        
        # Variável de estado para rastreamento de foco manual
        self._sidebar_has_logical_focus = False

        self._setup_actions()
        self.connect("close-request", self._on_window_close_request)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        self.toggle_sidebar_button = Gtk.ToggleButton()
        self.toggle_sidebar_button.set_icon_name("view-reveal-symbolic")
        self.toggle_sidebar_button.set_tooltip_text("Alternar Barra Lateral")
        self.toggle_sidebar_button.connect("toggled", self._on_toggle_sidebar_clicked)
        header.pack_start(self.toggle_sidebar_button)
        prefs_button = Gtk.Button.new_from_icon_name("preferences-system-symbolic")
        prefs_button.set_tooltip_text("Preferências")
        prefs_button.set_action_name("win.preferences")
        header.pack_end(prefs_button)
        menu_button_main = Gtk.MenuButton()
        menu_button_main.set_icon_name("open-menu-symbolic")
        menu_button_main.set_tooltip_text("Menu Principal")
        menu_button_main.set_menu_model(self.create_main_menu())
        header.pack_end(menu_button_main)
        main_box.append(header)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(220)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        self.sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sessions_scroll = Gtk.ScrolledWindow()
        sessions_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sessions_scroll.set_vexpand(True)
        self.session_store = Gio.ListStore.new(SessionItem)
        self.folder_store = Gio.ListStore.new(SessionFolder)
        self._load_initial_sessions_and_folders()
        self.tree_view = self._create_tree_view()
        sessions_scroll.set_child(self.tree_view)
        session_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        session_toolbar.add_css_class("toolbar")
        add_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_button.set_tooltip_text("Adicionar Sessão"); add_button.connect("clicked", self.on_add_session_clicked)
        add_folder_button = Gtk.Button.new_from_icon_name("folder-new-symbolic")
        add_folder_button.set_tooltip_text("Adicionar Pasta"); add_folder_button.connect("clicked", self.on_add_folder_clicked)
        edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        edit_button.set_tooltip_text("Editar Selecionado"); edit_button.connect("clicked", self.on_edit_selected_clicked)
        remove_button = Gtk.Button.new_from_icon_name("list-remove-symbolic")
        remove_button.set_tooltip_text("Remover Selecionado"); remove_button.connect("clicked", self.on_remove_selected_clicked)
        session_toolbar.append(add_button); session_toolbar.append(add_folder_button)
        session_toolbar.append(edit_button); session_toolbar.append(remove_button)
        self.sidebar_box.append(sessions_scroll); self.sidebar_box.append(session_toolbar)
        self.tab_view = Adw.TabView()
        self.tab_view.set_vexpand(True); self.tab_view.set_hexpand(True)
        self.tab_view.connect("notify::selected-page", self._on_tab_selected)
        tab_bar = Adw.TabBar()
        tab_bar.set_view(self.tab_view)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(tab_bar); content_box.append(self.tab_view)
        paned.set_start_child(self.sidebar_box); paned.set_end_child(content_box)
        main_box.append(paned); self.set_content(main_box)
        self.terminals: List[Vte.Terminal] = []
        initial_sidebar_visible = self.settings.get("sidebar_visible", True)
        self.toggle_sidebar_button.set_active(initial_sidebar_visible)
        self.sidebar_box.set_visible(initial_sidebar_visible)
        self.toggle_sidebar_button.set_icon_name("view-reveal-symbolic" if initial_sidebar_visible else "view-conceal-symbolic")
        self._refresh_tree_view()
        self.open_initial_terminal()

    # --- O "PORTEIRO" DE TECLADO DO TERMINAL ---
    def _on_any_terminal_key_pressed(self, controller, keyval, keycode, state, terminal_widget):
        if self._sidebar_has_logical_focus:
            # Bloqueia o evento para o terminal se o foco lógico estiver na barra lateral
            return Gdk.EVENT_STOP
        # Permite que o terminal processe a tecla se ele tiver o foco lógico
        return Gdk.EVENT_PROPAGATE

    # --- ATUALIZADORES DE ESTADO DE FOCO LÓGICO ---
    def _on_tree_view_clicked(self, gesture, n_press, x, y):
        """Define o foco lógico para a barra lateral quando clicada."""
        self._sidebar_has_logical_focus = True
        self.tree_view.grab_focus()
        return Gdk.EVENT_PROPAGATE

    def _on_terminal_clicked(self, gesture, n_press, x, y, terminal):
        """Define o foco lógico para o terminal quando clicado."""
        self._sidebar_has_logical_focus = False
        terminal.grab_focus()
        return Gdk.EVENT_PROPAGATE
        
    def _on_window_close_request(self, window):
        print("Requisição de fechamento da janela. Fechando todas as abas...")
        pages_to_close = list(self.tab_view.get_pages())
        for page in pages_to_close:
            terminal = self._find_terminal_in_page(page)
            if terminal:
                pty = terminal.get_pty()
                if pty:
                    try:
                        pty.close()
                    except GLib.Error as e:
                        print(f"Erro ao fechar PTY para aba '{page.get_title()}': {e.message}")
                if terminal in self.terminals:
                    self.terminals.remove(terminal)
            self.tab_view.close_page(page)
        self.terminals.clear()
        print("Limpeza de terminais e abas concluída.")
        return Gdk.EVENT_PROPAGATE

    def _load_initial_sessions_and_folders(self):
        sessions_data, folders_data = load_sessions_and_folders()
        if folders_data: [self.folder_store.append(SessionFolder.from_dict(fd)) for fd in folders_data]
        if sessions_data: [self.session_store.append(SessionItem.from_dict(sd)) for sd in sessions_data]

    def _on_toggle_sidebar_clicked(self, button: Gtk.ToggleButton):
        is_visible = button.get_active()
        self.sidebar_box.set_visible(is_visible)
        button.set_icon_name("view-reveal-symbolic" if is_visible else "view-conceal-symbolic")
        self.settings["sidebar_visible"] = is_visible; save_settings(self.settings)

    def _refresh_tree_view(self):
        model = self.tree_view.get_model()
        if model: self._populate_tree_store_from_lists(cast(Gtk.TreeStore, model))

    def open_initial_terminal(self):
        if self.tab_view.get_n_pages() == 0:
            self.open_local_terminal_tab("Terminal Local")

    def _on_any_terminal_child_exited(self, terminal_widget: Vte.Terminal, child_status: int, identifier: Union[str, SessionItem]):
        terminal_name = identifier if isinstance(identifier, str) else identifier.name
        page_title_for_log = "Desconhecida"
        found_page = None
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            if page:
                current_terminal_in_page = self._find_terminal_in_page(page)
                if current_terminal_in_page == terminal_widget:
                    page_title_for_log = page.get_title()
                    found_page = page
                    break
        print(f"DIAGNÓSTICO: Processo filho do terminal '{terminal_name}' saiu com status: {child_status}.")
        if found_page and terminal_widget.get_is_realized() and not terminal_widget.is_closed():
            try:
                message = f"\r\n[Processo em '{terminal_name}' encerrado (status: {child_status})]\r\n"
                terminal_widget.feed(message.encode('utf-8'))
            except GLib.Error as e:
                print(f"  Erro ao alimentar mensagem de child-exited em '{terminal_name}': {e.message}")
        
    def _on_any_terminal_eof(self, terminal_widget: Vte.Terminal, identifier: Union[str, SessionItem]):
        terminal_name = identifier if isinstance(identifier, str) else identifier.name
        page_title_for_log = "Desconhecida"
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            if page:
                current_terminal_in_page = self._find_terminal_in_page(page)
                if current_terminal_in_page == terminal_widget:
                    page_title_for_log = page.get_title()
                    break
        print(f"DIAGNÓSTICO: Sinal 'eof' recebido para o terminal '{terminal_name}'.")

    def open_local_terminal_tab(self, title: str):
        if not CONFIG_VTE_AVAILABLE:
            dialog = Adw.MessageDialog(transient_for=self, title="VTE Não Disponível", body="A biblioteca VTE não está instalada.")
            dialog.add_response("ok", "OK")
            dialog.present()
            return None
        terminal = Vte.Terminal()
        terminal.set_vexpand(True); terminal.set_hexpand(True); terminal.set_mouse_autohide(True)
        terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON); terminal.set_scroll_on_output(True); terminal.set_scroll_on_keystroke(True)
        
        # Conecta o porteiro de teclado
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_any_terminal_key_pressed, terminal)
        terminal.add_controller(key_controller)
        
        # Conecta o clique para definir o foco lógico
        click_controller = Gtk.GestureClick(); click_controller.set_button(0)
        click_controller.connect("pressed", self._on_terminal_clicked, terminal)
        terminal.add_controller(click_controller)
        
        self.apply_terminal_settings(terminal)
        self.terminals.append(terminal)
        self._add_terminal_context_menu(terminal)
        terminal.connect("child-exited", self._on_any_terminal_child_exited, title) 
        terminal.connect("eof", self._on_any_terminal_eof, title)
        scrolled_window = Gtk.ScrolledWindow(); scrolled_window.set_child(terminal)
        page = self.tab_view.add_page(scrolled_window, None); page.set_title(title)
        page.set_icon(Gio.ThemedIcon.new("computer-symbolic"))
        shell = os.environ.get("SHELL", "/bin/bash"); cmd = [shell]
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, os.environ.get("HOME", "/tmp"),
            cmd, [], GLib.SpawnFlags.DEFAULT, None, None, -1, None,
            self._on_terminal_spawn_callback, ()
        )
        self._sidebar_has_logical_focus = False
        GLib.idle_add(terminal.grab_focus)
        self.tab_view.set_selected_page(page)
        return terminal

    def _add_terminal_context_menu(self, terminal: Vte.Terminal):
        click = Gtk.GestureClick(); click.set_button(Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_terminal_right_click, terminal); terminal.add_controller(click)

    def _on_terminal_right_click(self, gesture, n_press, x, y, terminal):
        self._sidebar_has_logical_focus = False
        terminal.grab_focus()
        menu = TerminalContextMenu(self, terminal); rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        menu.set_pointing_to(rect); menu.set_parent(terminal); menu.popup()

    def _on_terminal_spawn_callback(self, terminal, pid, error, user_data=None):
        if error: print(f"Erro ao iniciar terminal (PID {pid}): {error.message}")
        else: print(f"Terminal iniciado com PID {pid}.")

    def open_session_in_new_tab(self, session_item: SessionItem):
        if not CONFIG_VTE_AVAILABLE:
            dialog = Adw.MessageDialog(transient_for=self, title="VTE Não Disponível", body="Não é possível abrir a sessão.")
            dialog.add_response("ok", "OK"); dialog.present()
            return None
        if session_item.session_type == "local": return self.open_local_terminal_tab(session_item.name)

        terminal = Vte.Terminal()
        terminal.set_vexpand(True);terminal.set_hexpand(True);terminal.set_mouse_autohide(True)
        terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON);terminal.set_scroll_on_output(True);terminal.set_scroll_on_keystroke(True)
        
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_any_terminal_key_pressed, terminal)
        terminal.add_controller(key_controller)

        click_controller = Gtk.GestureClick(); click_controller.set_button(0)
        click_controller.connect("pressed", self._on_terminal_clicked, terminal)
        terminal.add_controller(click_controller)

        self.apply_terminal_settings(terminal)
        self.terminals.append(terminal)
        self._add_terminal_context_menu(terminal)
        terminal.connect("child-exited", self._on_any_terminal_child_exited, session_item)
        terminal.connect("eof", self._on_any_terminal_eof, session_item)
        
        sw = Gtk.ScrolledWindow(); sw.set_child(terminal)
        page = self.tab_view.add_page(sw, None); page.set_title(session_item.name)
        page.set_icon(Gio.ThemedIcon.new("network-server-symbolic"))
        cmd_list = ["ssh", "-o",f"ConnectTimeout={SSH_CONNECT_TIMEOUT}","-o","ServerAliveInterval=5","-o","ServerAliveCountMax=3"]
        if session_item.auth_type=="key" and session_item.auth_value and Path(session_item.auth_value).is_file():
            cmd_list.extend(["-i", str(session_item.auth_value)])
        elif session_item.auth_type=="key":
            terminal.feed(f"\r\nAviso: Chave '{session_item.auth_value}' não encontrada.\r\n".encode('utf-8'))

        target = f"{session_item.user}@{session_item.host}" if session_item.user else session_item.host
        if session_item.auth_type == "password" and session_item.auth_value and shutil.which("sshpass"):
            final_cmd = ["sshpass", "-p", session_item.auth_value] + cmd_list + [target]
        else:
            final_cmd = cmd_list + [target]
            if session_item.auth_type == "password" and not shutil.which("sshpass"):
                terminal.feed("\r\nAviso: sshpass não encontrado.\r\n".encode('utf-8'))
        
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, os.environ.get("HOME","/tmp"),
            final_cmd, [], GLib.SpawnFlags.DEFAULT, None, None, -1, None, 
            self._on_ssh_spawn_callback, session_item
        )
        self._sidebar_has_logical_focus = False
        GLib.idle_add(terminal.grab_focus); self.tab_view.set_selected_page(page)
        return terminal

    def _on_ssh_spawn_callback(self, terminal: Vte.Terminal, pid: int, error: Optional[GLib.Error], user_data: Any):
        session_item = cast(SessionItem, user_data)
        if error:
            msg=f"Erro para {session_item.name}: {error.message}\r\n"
            try: terminal.feed(msg.encode('utf-8'))
            except GLib.Error as feed_error: print(f"Erro ao alimentar msg de erro SSH: {feed_error.message}")
            self._show_ssh_error_dialog(session_item, error.message)
        else:
            print(f"SSH PID {pid} para {session_item.name}")

    def _show_ssh_error_dialog(self, si, err_msg):
        dialog = Adw.MessageDialog(transient_for=self, title="Falha na Conexão", body=f"Para: {si.name}\nDetalhes: {err_msg}")
        dialog.add_response("ok","OK")
        dialog.present()

    def apply_terminal_settings(self, terminal: Vte.Terminal):
        if not CONFIG_VTE_AVAILABLE: return
        idx = self.settings.get("color_scheme",0); name = COLOR_SCHEME_MAP[idx if 0<=idx<len(COLOR_SCHEME_MAP) else 0]
        cs = COLOR_SCHEMES.get(name, COLOR_SCHEMES[COLOR_SCHEME_MAP[0]])
        fg,bg = Gdk.RGBA(),Gdk.RGBA()
        if not fg.parse(cs.get("foreground","#FFF")): fg.parse("#FFFFFF")
        if not bg.parse(cs.get("background","#000")): bg.parse("#000000")
        if self.settings.get("transparency",0)>0: bg.alpha=max(0.0,min(1.0,1.0-(self.settings.get("transparency",0)/100.0)))
        palette = []
        palette_src = cs.get("palette",COLOR_SCHEMES[COLOR_SCHEME_MAP[0]]["palette"])
        for cs_str in palette_src:
            color=Gdk.RGBA()
            if not color.parse(cs_str): color.parse("#000000")
            palette.append(color)
        while len(palette) < 16: 
            fallback_rgba = Gdk.RGBA(); fallback_rgba.parse("#000000"); palette.append(fallback_rgba)
        terminal.set_colors(fg,bg,palette[:16]) 
        terminal.set_font(Pango.FontDescription.from_string(self.settings.get("font","Monospace 10")))

    def update_all_terminals(self):
        [self.apply_terminal_settings(t) for t in self.terminals if t and t.get_realized()]

    def _on_new_local_tab(self,a,p): self.open_local_terminal_tab("Terminal Local")
    def _on_close_tab(self,a,p):
        pg=self.tab_view.get_selected_page()
        if pg:
            t=self._find_terminal_in_page(pg)
            if t:
                pty=t.get_pty()
                if pty:
                    try: pty.close()
                    except GLib.Error as e: print(f"Erro ao fechar PTY da aba: {e.message}")
                if t in self.terminals: self.terminals.remove(t)
            self.tab_view.close_page(pg)

    def _find_terminal_in_page(self,pg: Adw.TabPage):
        if not pg: return None
        child = pg.get_child()
        if isinstance(child, Gtk.ScrolledWindow):
            grandchild = child.get_child()
            if isinstance(grandchild, Vte.Terminal):
                return grandchild
        return None

    def _on_copy(self,a,p): 
        t=self._find_terminal_in_page(self.tab_view.get_selected_page())
        if t and t.get_has_selection(): t.copy_clipboard_format(Vte.Format.TEXT)

    def _on_paste(self,a,p): 
        t=self._find_terminal_in_page(self.tab_view.get_selected_page())
        if t and t.is_focus(): t.paste_clipboard()

    def _on_select_all(self,a,p):
        t=self._find_terminal_in_page(self.tab_view.get_selected_page())
        if t: t.select_all()

    def _setup_actions(self):
        acts={
            "new-local-tab":self._on_new_local_tab, "close-tab":self._on_close_tab,
            "copy":self._on_copy, "paste":self._on_paste, "select-all":self._on_select_all,
            "edit-session":self._on_edit_session, "duplicate-session":self._on_duplicate_session,
            "rename-session":self._on_rename_session,
            "move-session-to-folder":self._on_move_session_to_folder,
            "delete-session":self._on_delete_session,
            "edit-folder":self._on_edit_folder, "rename-folder": self._on_rename_folder_action,
            "add-session-to-folder":self._on_add_session_to_folder,
            "delete-folder":self._on_delete_folder, "preferences":self._on_preferences,
            # Ações de Recortar/Copiar/Colar
            "cut-item": self._on_cut_item,
            "copy-item": self._on_copy_item,
            "paste-item": self._on_paste_item,
            "paste-item-root": self._on_paste_item_root,
            "add-session-root": lambda a, p: self.on_add_session_clicked(None),
            "add-folder-root": lambda a, p: self.on_add_folder_clicked(None),
        }
        for n,c in acts.items(): act=Gio.SimpleAction.new(n,None);act.connect("activate",c);self.add_action(act)

    def _on_preferences(self,a,p):
        d=PreferencesDialog(self,self.settings)
        d.connect("color-scheme-changed",lambda _,i:self.update_all_terminals())
        d.connect("transparency-changed",lambda _,v:self.update_all_terminals())
        d.connect("font-changed",lambda _,f:self.update_all_terminals())
        d.connect("shortcut-changed",lambda _:self._update_keyboard_shortcuts())
        d.present()

    def _update_keyboard_shortcuts(self):
        app=self.get_application(); scfg=self.settings.get("shortcuts",DEFAULT_SETTINGS["shortcuts"]); ds=DEFAULT_SETTINGS["shortcuts"]
        if app:
            for k in ["new-local-tab", "close-tab", "copy", "paste"]:
                 accel = scfg.get(k, ds.get(k, ""))
                 app.set_accels_for_action(f"win.{k}", [accel] if accel else [])

    def _on_rename_folder_action(self, action, param):
        if hasattr(self, "folder_context_item") and self.folder_context_item:
            self._start_rename_dialog(self.folder_context_item, is_session=False)

    def create_main_menu(self):
        m=Gio.Menu(); fm=Gio.Menu(); m.append_submenu("Arquivo",fm)
        fm.append("Nova Aba","win.new-local-tab"); fm.append("Fechar Aba","win.close-tab"); fm.append_section(None,Gio.Menu()); fm.append("Sair","app.quit")
        em=Gio.Menu(); m.append_submenu("Editar",em)
        em.append("Copiar","win.copy"); em.append("Colar","win.paste"); em.append("Selecionar Tudo","win.select-all"); em.append_section(None,Gio.Menu()); em.append("Preferências","win.preferences")
        hm=Gio.Menu(); m.append_submenu("Ajuda",hm); hm.append("Sobre","app.about")
        return m

    def _on_edit_session(self,a,p):
        if hasattr(self,"session_context_item") and self.session_context_item:
            SessionEditDialog(self,self.session_context_item,self.session_store,self.session_context_position,self.folder_store).present()

    def _on_duplicate_session(self,a,p):
        if hasattr(self,"session_context_item") and self.session_context_item:
            orig=self.session_context_item; dup=SessionItem.from_dict(orig.to_dict()); dup.name=f"Cópia de {orig.name}"
            self.session_store.append(dup); save_sessions_and_folders(self.session_store,self.folder_store); self._refresh_tree_view()

    def _on_rename_session(self,a,p):
        if hasattr(self,"session_context_item") and self.session_context_item: self._start_rename_dialog(self.session_context_item,True)

    def _start_rename_dialog(self, item, is_session):
        title = f"Renomear {'Sessão' if is_session else 'Pasta'}";
        dlg = Adw.MessageDialog(transient_for=self, title=title, body=f"Novo nome para \"{item.name}\":")
        entry = Gtk.Entry(text=item.name); dlg.set_extra_child(entry);
        dlg.add_response("cancel","Cancelar"); dlg.add_response("rename","Renomear")
        dlg.set_default_response("rename"); dlg.connect("response",self._on_rename_dialog_response,entry,item,is_session); dlg.present()

    def _on_rename_dialog_response(self, dialog, resp_id, entry, item, is_session):
        if resp_id=="rename":
            new_name = entry.get_text().strip()
            if new_name and new_name != item.name:
                if not is_session: 
                    old_path = item.path 
                    new_path = os.path.normpath((item.parent_path + "/" + new_name) if item.parent_path else ("/" + new_name))
                    if any(f.path == new_path for i in range(self.folder_store.get_n_items()) if (f:=self.folder_store.get_item(i)) != item):
                        error_dialog = Adw.MessageDialog(transient_for=self, title="Erro", body=f"Caminho da pasta '{new_path}' já existe.")
                        error_dialog.add_response("ok", "OK"); error_dialog.present(); dialog.close(); return
                    item.name = new_name; item.path = new_path 
                    self._update_child_paths(old_path, new_path)
                else: 
                    item.name = new_name
                save_sessions_and_folders(self.session_store, self.folder_store); self._refresh_tree_view()
        dialog.close()

    def _update_child_paths(self, old_p_path, new_p_path):
        for i in range(self.session_store.get_n_items()):
            s=self.session_store.get_item(i)
            if s.folder_path == old_p_path: s.folder_path = new_p_path
            elif s.folder_path and s.folder_path.startswith(old_p_path+"/"):
                s.folder_path=s.folder_path.replace(old_p_path+"/",new_p_path+"/",1)
        for i in range(self.folder_store.get_n_items()):
            f=self.folder_store.get_item(i)
            if f.parent_path == old_p_path: 
                f.parent_path=new_p_path; f.path=os.path.normpath(new_p_path+"/"+f.name) 
            elif f.parent_path and f.parent_path.startswith(old_p_path+"/"): 
                f.parent_path=f.parent_path.replace(old_p_path+"/",new_p_path+"/",1)
                f.path=os.path.normpath(f.parent_path+"/"+f.name)

    def _on_move_session_to_folder(self,a,p):
        if hasattr(self,"session_context_item") and self.session_context_item and self.folder_store:
            si=self.session_context_item
            dlg = Adw.MessageDialog(transient_for=self, title="Mover Sessão", body=f"Mover \"{si.name}\" para a pasta:")
            strs=["Raiz (Sem Pasta)"]; paths_map={"Raiz (Sem Pasta)":""}
            s_flds=sorted([self.folder_store.get_item(i) for i in range(self.folder_store.get_n_items())],key=lambda f:f.path)
            for fld in s_flds:
                d_name=f"{'  '*fld.path.count('/')}{fld.name}"; strs.append(d_name); paths_map[d_name]=fld.path
            combo=Gtk.DropDown.new_from_strings(strs); cur_d="Raiz (Sem Pasta)"
            for dsp,pval in paths_map.items():
                if pval==si.folder_path: cur_d=dsp; break
            try: combo.set_selected(strs.index(cur_d))
            except ValueError: combo.set_selected(0)
            dlg.set_extra_child(combo); dlg.add_response("cancel","Cancelar"); dlg.add_response("move","Mover")
            dlg.set_default_response("move"); dlg.connect("response",self._on_move_session_dialog_response,combo,paths_map,si); dlg.present()

    def _on_move_session_dialog_response(self,dialog,resp_id,combo,paths_map,si):
        if resp_id=="move":
            sel_item=combo.get_selected_item()
            if sel_item:
                new_fpath=paths_map.get(sel_item.get_string(),"")
                if si.folder_path!=new_fpath:
                    si.folder_path=new_fpath;
                    save_sessions_and_folders(self.session_store, self.folder_store);
                    self._refresh_tree_view()
        dialog.close()

    def _on_delete_session(self,a,p):
        if hasattr(self,"session_context_item") and self.session_context_item:
            self._confirm_delete_item_dialog(self.session_context_item,True)

    def _on_delete_folder(self,a,p):
        if hasattr(self,"folder_context_item") and self.folder_context_item:
            self._confirm_delete_item_dialog(self.folder_context_item,False)

    def _confirm_delete_item_dialog(self,item,is_session):
        item_type="Sessão" if is_session else "Pasta"
        if not is_session: 
            has_child_sessions = any(s.folder_path == item.path for i in range(self.session_store.get_n_items()) if (s:=self.session_store.get_item(i)))
            has_child_folders = any(f.parent_path == item.path for i in range(self.folder_store.get_n_items()) if (f:=self.folder_store.get_item(i)))
            if has_child_sessions or has_child_folders:
                error_dialog = Adw.MessageDialog(transient_for=self, title=f"Não é Possível Excluir {item_type}", body=f"A pasta \"{item.name}\" não está vazia.")
                error_dialog.add_response("ok", "OK"); error_dialog.present(); return
        dlg = Adw.MessageDialog(transient_for=self, title=f"Excluir {item_type}", body=f"Excluir {item_type.lower()} \"{item.name}\"?")
        dlg.add_response("cancel","Cancelar"); dlg.add_response("delete","Excluir");
        dlg.set_response_appearance("delete",Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response",self._on_confirm_delete_response,item,is_session); dlg.present()

    def _on_confirm_delete_response(self,dialog,resp_id,item,is_session):
        if resp_id=="delete":
            store=self.session_store if is_session else self.folder_store
            found=False; item_name_for_debug = item.name
            for i in range(store.get_n_items()-1,-1,-1): 
                if store.get_item(i)==item:
                    store.remove(i); found=True; break
            if found:
                save_sessions_and_folders(self.session_store, self.folder_store); self._refresh_tree_view()
        dialog.close()

    def _on_edit_folder(self,a,p):
        if hasattr(self,"folder_context_item") and self.folder_context_item:
            FolderEditDialog(self,self.folder_store,self.folder_context_item,self.folder_context_position).present()

    def _on_add_session_to_folder(self,a,p):
        if hasattr(self,"folder_context_item") and self.folder_context_item:
            SessionEditDialog(self,SessionItem(name="Nova Sessão",folder_path=self.folder_context_item.path),self.session_store,-1,self.folder_store).present()

    def on_add_session_clicked(self,b):
        SessionEditDialog(self,SessionItem(name="Nova Sessão", folder_path=""),self.session_store,-1,self.folder_store).present()

    def on_add_folder_clicked(self,b):
        FolderEditDialog(self,self.folder_store,None,None).present()

    def _find_item_in_store_by_tree_model_data(self,model_iter,tree_model):
        if not model_iter: return None,-1,None
        item_name = tree_model[model_iter][0]; row_type = tree_model[model_iter][1]; path_data_from_tree = tree_model[model_iter][3] 
        store_to_search = self.session_store if row_type == "session" else self.folder_store
        for i in range(store_to_search.get_n_items()):
            item_in_store = store_to_search.get_item(i)
            current_item_path_attr = item_in_store.folder_path if row_type == "session" else item_in_store.path
            if item_in_store.name == item_name and current_item_path_attr == path_data_from_tree:
                return item_in_store, i, store_to_search
        return None,-1,None

    def on_edit_selected_clicked(self,b):
        sel=self.tree_view.get_selection(); model,it=sel.get_selected()
        item = None
        if it: item,pos,store=self._find_item_in_store_by_tree_model_data(it,model)
        if item and store==self.session_store:
            SessionEditDialog(self,item,store,pos,self.folder_store).present()
        elif item and store==self.folder_store:
            FolderEditDialog(self,store,item,pos).present()

    def on_remove_selected_clicked(self,b):
        sel=self.tree_view.get_selection(); model,it=sel.get_selected()
        item = None
        if it: item,pos,store=self._find_item_in_store_by_tree_model_data(it,model)
        if item and isinstance(item,SessionItem):
            self.session_context_item=item; self.session_context_position=pos; self._on_delete_session(None,None)
        elif item and isinstance(item,SessionFolder):
            self.folder_context_item=item; self.folder_context_position=pos; self._on_delete_folder(None,None)

    def _create_tree_view(self):
        ts=Gtk.TreeStore(str,str,str,str); self.tree_view=Gtk.TreeView(model=ts); self.tree_view.set_headers_visible(False)
        col=Gtk.TreeViewColumn(); col.set_title("Sessões");
        rp=Gtk.CellRendererPixbuf(); col.pack_start(rp,False); col.add_attribute(rp,"icon-name",2)
        rt=Gtk.CellRendererText(); col.pack_start(rt,True); col.add_attribute(rt,"text",0);
        self.tree_view.append_column(col)
        self.tree_view.connect("row-activated",self._on_tree_row_activated)
        
        # Conecta o clique para definir o foco lógico
        cg_focus = Gtk.GestureClick(); cg_focus.set_button(0)
        cg_focus.connect("pressed", self._on_tree_view_clicked)
        self.tree_view.add_controller(cg_focus)
        
        cg_right=Gtk.GestureClick();cg_right.set_button(Gdk.BUTTON_SECONDARY);cg_right.connect("pressed",self._on_tree_right_click);self.tree_view.add_controller(cg_right)
        kc=Gtk.EventControllerKey();kc.connect("key-pressed",self._on_tree_key_pressed);self.tree_view.add_controller(kc)
        return self.tree_view

    def _populate_tree_store_from_lists(self,ts_model:Gtk.TreeStore):
        ts_model.clear(); fp_iters={} 
        s_flds=sorted([self.folder_store.get_item(i) for i in range(self.folder_store.get_n_items())],key=lambda f:(f.path.count('/'),f.path))
        for fld in s_flds:
            p_it=fp_iters.get(fld.parent_path) if fld.parent_path else None;
            f_it=ts_model.append(p_it,[fld.name,"folder","folder-symbolic",fld.path]); 
            fp_iters[fld.path]=f_it
        for i in range(self.session_store.get_n_items()):
            sess = self.session_store.get_item(i)
            parent_iter = fp_iters.get(sess.folder_path) if sess.folder_path else None
            icon_name = "computer-symbolic" if sess.session_type == "local" else "network-server-symbolic"
            ts_model.append(parent_iter, [sess.name, "session", icon_name, sess.folder_path]) 

    def _on_tree_key_pressed(self,c,kv,kc,s):
        if kv==Gdk.KEY_Delete: self.on_remove_selected_clicked(None); return Gdk.EVENT_STOP
        elif kv==Gdk.KEY_Return or kv==Gdk.KEY_KP_Enter:
            sel=self.tree_view.get_selection(); m,it=sel.get_selected()
            if it: pth,_=m.get_path(it); self._on_tree_row_activated(self.tree_view,pth,None); return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_tree_row_activated(self,tv,path,col):
        m=tv.get_model(); it=m.get_iter(path)
        item = None
        if it: item,_,_=self._find_item_in_store_by_tree_model_data(it,m)
        if item and isinstance(item,SessionItem):
            self._sidebar_has_logical_focus = False
            self.open_session_in_new_tab(item)
        elif item and isinstance(item,SessionFolder):
            if tv.row_expanded(path): tv.collapse_row(path) 
            else: tv.expand_row(path,False)

    def _on_tree_right_click(self,gest,n_press,x,y):
        self._sidebar_has_logical_focus = True
        self.tree_view.grab_focus()
        path_info = self.tree_view.get_path_at_pos(int(x), int(y))
        menu: Optional[Gtk.PopoverMenu] = None 
        if path_info: 
            path, _, _, _ = path_info
            model = self.tree_view.get_model()
            if model is None: return 
            it = model.get_iter(path)
            if it:
                selection = self.tree_view.get_selection()
                selection.select_path(path)
                item, item_pos, store = self._find_item_in_store_by_tree_model_data(it, model)
                if not item: return
                if isinstance(item, SessionItem):
                    self.session_context_item = item; self.session_context_position = item_pos; self.folder_context_item = None 
                    menu = SessionContextMenu(self, item, store, item_pos, self.folder_store, bool(self._internal_clipboard))
                elif isinstance(item, SessionFolder):
                    self.folder_context_item = item; self.folder_context_position = item_pos; self.session_context_item = None 
                    menu = FolderContextMenu(self, item, store, item_pos, self.session_store, bool(self._internal_clipboard))
        else: 
            self.session_context_item = None; self.folder_context_item = None  
            menu = RootTreeViewContextMenu(self, bool(self._internal_clipboard))
        if menu:
            rect = Gdk.Rectangle(); rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            menu.set_pointing_to(rect); menu.set_parent(self.tree_view); menu.popup()
    
    def _on_cut_item(self, action, param):
        item_to_cut = None
        if hasattr(self, "session_context_item") and self.session_context_item:
            item_to_cut = self.session_context_item
        elif hasattr(self, "folder_context_item") and self.folder_context_item:
            item_to_cut = self.folder_context_item
        
        if item_to_cut:
            self._internal_clipboard = item_to_cut
            self._internal_clipboard_is_cut = True
            print(f"Item '{item_to_cut.name}' recortado para a área de transferência.")
        else:
            print("Nenhum item selecionado para recortar.")

    def _on_copy_item(self, action, param):
        item_to_copy = None
        if hasattr(self, "session_context_item") and self.session_context_item:
            item_to_copy = self.session_context_item
        elif hasattr(self, "folder_context_item") and self.folder_context_item:
            item_to_copy = self.folder_context_item

        if item_to_copy:
            self._internal_clipboard = item_to_copy.from_dict(item_to_copy.to_dict()) 
            self._internal_clipboard_is_cut = False 
            print(f"Item '{item_to_copy.name}' copiado para a área de transferência.")
        else:
            print("Nenhum item selecionado para copiar.")

    def _generate_unique_name(self, base_name: str, target_folder_path: str, is_session: bool, item_to_ignore=None) -> str:
        current_name = base_name; suffix_counter = 0
        existing_names = set()
        store = self.session_store if is_session else self.folder_store
        
        for i in range(store.get_n_items()):
            item = store.get_item(i)
            if item == item_to_ignore: continue
            
            path_attr = item.folder_path if is_session else item.parent_path
            if path_attr == target_folder_path:
                existing_names.add(item.name)
        
        while current_name in existing_names:
            suffix_counter += 1
            current_name = f"{base_name} ({suffix_counter})" if suffix_counter > 0 else base_name
        return current_name

    def _paste_session(self, session_to_paste: SessionItem, target_folder_path: str):
        new_session = SessionItem.from_dict(session_to_paste.to_dict())
        new_session.name = self._generate_unique_name(new_session.name, target_folder_path, True)
        new_session.folder_path = target_folder_path
        self.session_store.append(new_session)
        print(f"Sessão '{new_session.name}' colada em '{target_folder_path or "Raiz"}'")

    def _paste_folder_recursive(self, folder_to_paste: SessionFolder, target_parent_path: str):
        new_folder = SessionFolder.from_dict(folder_to_paste.to_dict())
        new_folder.name = self._generate_unique_name(new_folder.name, target_parent_path, False)
        new_folder.parent_path = target_parent_path
        new_folder.path = os.path.normpath(f"{target_parent_path}/{new_folder.name}" if target_parent_path else f"/{new_folder.name}")
        
        if any(f.path == new_folder.path for i in range(self.folder_store.get_n_items())):
            print(f"Erro: Caminho da pasta '{new_folder.path}' já existiria."); return

        self.folder_store.append(new_folder)
        original_folder_path = folder_to_paste.path
        
        sessions_to_clone = [s.from_dict(s.to_dict()) for i in range(self.session_store.get_n_items()) if (s:=self.session_store.get_item(i)).folder_path == original_folder_path]
        for s_clone in sessions_to_clone: self._paste_session(s_clone, new_folder.path)
        
        subfolders_to_clone = [sf.from_dict(sf.to_dict()) for i in range(self.folder_store.get_n_items()) if (sf:=self.folder_store.get_item(i)).parent_path == original_folder_path and sf != new_folder]
        for sf_clone in subfolders_to_clone: self._paste_folder_recursive(sf_clone, new_folder.path)

    def _on_paste_item(self, action, param): 
        if not self._internal_clipboard: print("Área de transferência vazia."); return
        if not (hasattr(self, "folder_context_item") and self.folder_context_item): print("Nenhuma pasta de destino."); return
        self._do_paste(self.folder_context_item.path)

    def _on_paste_item_root(self, action, param): 
        if not self._internal_clipboard: print("Área de transferência vazia."); return
        self._do_paste("") 

    def _do_paste(self, target_path: str):
        if not self._internal_clipboard: return

        if self._internal_clipboard_is_cut:
            item_to_move = self._internal_clipboard
            print(f"Movendo '{item_to_move.name}' para '{target_path or 'Raiz'}'")
            if isinstance(item_to_move, SessionItem):
                item_to_move.name = self._generate_unique_name(item_to_move.name, target_path, True, item_to_move)
                item_to_move.folder_path = target_path
            elif isinstance(item_to_move, SessionFolder):
                if target_path.startswith(item_to_move.path + "/") or target_path == item_to_move.path:
                    print("Erro: Não é possível mover uma pasta para dentro dela mesma.")
                    return
                old_path = item_to_move.path
                item_to_move.name = self._generate_unique_name(item_to_move.name, target_path, False, item_to_move)
                item_to_move.parent_path = target_path
                item_to_move.path = os.path.normpath(f"{target_path}/{item_to_move.name}" if target_path else f"/{item_to_move.name}")
                self._update_child_paths(old_path, item_to_move.path)
            self._internal_clipboard = None
            self._internal_clipboard_is_cut = False
        else:
            item_to_copy = self._internal_clipboard
            if isinstance(item_to_copy, SessionItem): self._paste_session(item_to_copy, target_path)
            elif isinstance(item_to_copy, SessionFolder): self._paste_folder_recursive(item_to_copy, target_path)
        
        save_sessions_and_folders(self.session_store, self.folder_store)
        self._refresh_tree_view()

    def _on_tab_selected(self, tabview, param):
        page = self.tab_view.get_selected_page()
        if page:
            terminal = self._find_terminal_in_page(page)
            if terminal:
                self._sidebar_has_logical_focus = False
                GLib.idle_add(terminal.grab_focus)

class CommTerminalApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.connect("startup",self._on_startup)
        self.connect("activate",self.on_activate)

    def _on_startup(self,app): self._setup_app_actions()

    def _setup_app_actions(self):
        acts=[("quit",self._on_quit),("preferences",self._on_app_preferences),("about",self._on_about)]
        for n,cb in acts:
            act=Gio.SimpleAction.new(n,None);act.connect("activate",cb);self.add_action(act)
        self.set_accels_for_action("app.quit",["<Control>q"]);
        self.set_accels_for_action("app.preferences",["<Control>comma"])

    def on_activate(self,app):
        win=self.get_active_window()
        if not win: win=CommTerminalWindow(application=self)
        win.present()
        if hasattr(win,'_update_keyboard_shortcuts'): win._update_keyboard_shortcuts()

    def _on_quit(self,a,p): self.quit()

    def _on_app_preferences(self,a,p):
        win=self.get_active_window()
        if not win: win=CommTerminalWindow(application=self);
        if not win.get_visible(): win.present()
        if hasattr(win,'_update_keyboard_shortcuts'): win._update_keyboard_shortcuts() 
        if win and hasattr(win, 'activate_action'): win.activate_action("preferences", None)

    def _on_about(self,a,p):
        about_dialog = Adw.AboutWindow(
            application_name=APP_TITLE, application_icon=APP_ID, developer_name="BigCommunity",
            version="1.0.1", developers=["BigCommunity Team"], copyright="© 2024 BigCommunity",
            license_type=Gtk.License.MIT_X11, website="https://communitybig.org/",
            issue_url="https://github.com/big-comm/ashyterm/issues", transient_for=self.get_active_window(),
            modal=True
        )
        about_dialog.present()

def main():
    app=CommTerminalApp();
    return app.run(sys.argv)

if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Exceção não tratada: {e}");
        import traceback; traceback.print_exc();
        sys.exit(1)