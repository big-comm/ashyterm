# ashyterm/dialogs.py
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import os
from pathlib import Path
from typing import Optional
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, GObject

from .models import SessionItem, SessionFolder
from .utils import save_sessions_and_folders, save_settings
from .config import DEFAULT_SETTINGS, COLOR_SCHEME_MAP, COLOR_SCHEMES

class SessionEditDialog(Adw.Window):
    def __init__(self, parent_window, session_item: SessionItem, session_store, position: int, folder_store=None):
        is_new_item = (position == -1)
        title = "Adicionar Sessão" if is_new_item else "Editar Sessão"

        super().__init__(
            title=title,
            modal=True,
            transient_for=parent_window,
            default_width=420,
            default_height=580,
            hide_on_close=True
        )

        self.parent_window = parent_window
        self.editing_session_item = SessionItem.from_dict(session_item.to_dict()) if not is_new_item else session_item
        self.original_session_item = session_item if not is_new_item else None
        self.session_store = session_store
        self.position = position
        self.folder_store = folder_store
        self.is_new_item = is_new_item

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15, margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)

        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name_label = Gtk.Label(label="<b>Nome da Sessão:</b>", use_markup=True, halign=Gtk.Align.START)
        self.name_entry = Gtk.Entry(text=self.editing_session_item.name)
        name_box.append(name_label)
        name_box.append(self.name_entry)
        main_box.append(name_box)

        if folder_store:
            folder_box_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            folder_label = Gtk.Label(label="<b>Pasta:</b>", use_markup=True, halign=Gtk.Align.START)
            self.folder_combo = Gtk.DropDown()
            folder_model_list = Gtk.StringList()
            folder_model_list.append("Raiz")
            self.folder_paths_map = {"Raiz": ""}

            sorted_folders = sorted(
                [folder_store.get_item(i) for i in range(folder_store.get_n_items())],
                key=lambda f: f.path
            )
            for folder in sorted_folders:
                display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
                folder_model_list.append(display_name)
                self.folder_paths_map[display_name] = folder.path

            self.folder_combo.set_model(folder_model_list)

            selected_dropdown_text = "Raiz"
            for display, path_val in self.folder_paths_map.items():
                if path_val == self.editing_session_item.folder_path:
                    selected_dropdown_text = display
                    break

            found_idx = 0
            for i in range(folder_model_list.get_n_items()):
                if folder_model_list.get_string(i) == selected_dropdown_text:
                    found_idx = i; break
            self.folder_combo.set_selected(found_idx)

            folder_box_outer.append(folder_label)
            folder_box_outer.append(self.folder_combo)
            main_box.append(folder_box_outer)

        type_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        type_label = Gtk.Label(label="<b>Tipo de Sessão:</b>", use_markup=True, halign=Gtk.Align.START)
        self.type_combo = Gtk.DropDown.new_from_strings(["Local", "SSH"])
        self.type_combo.set_selected(0 if self.editing_session_item.session_type == "local" else 1)
        self.type_combo.connect("notify::selected", self._on_type_changed)
        type_box.append(type_label); type_box.append(self.type_combo); main_box.append(type_box)

        self.ssh_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        host_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        host_label = Gtk.Label(label="Host:", halign=Gtk.Align.START)
        self.host_entry = Gtk.Entry(text=self.editing_session_item.host)
        host_box.append(host_label); host_box.append(self.host_entry); self.ssh_box.append(host_box)

        user_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        user_label = Gtk.Label(label="Usuário:", halign=Gtk.Align.START)
        self.user_entry = Gtk.Entry(text=self.editing_session_item.user)
        user_box.append(user_label); user_box.append(self.user_entry); self.ssh_box.append(user_box)

        auth_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        auth_label = Gtk.Label(label="Autenticação:", halign=Gtk.Align.START)
        self.auth_combo = Gtk.DropDown.new_from_strings(["Chave SSH", "Senha"])
        self.auth_combo.set_selected(0 if self.editing_session_item.auth_type == "key" else 1)
        self.auth_combo.connect("notify::selected", self._on_auth_changed)
        auth_box.append(auth_label); auth_box.append(self.auth_combo); self.ssh_box.append(auth_box)

        self.key_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        key_label = Gtk.Label(label="Caminho da Chave SSH:", halign=Gtk.Align.START)
        key_path_box_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.key_path_entry = Gtk.Entry(text=self.editing_session_item.auth_value if self.editing_session_item.auth_type == "key" else "", hexpand=True)
        self.browse_button = Gtk.Button(label="Procurar...")
        self.browse_button.connect("clicked", self._on_browse_key_clicked)
        key_path_box_inner.append(self.key_path_entry); key_path_box_inner.append(self.browse_button)
        self.key_box.append(key_label); self.key_box.append(key_path_box_inner); self.ssh_box.append(self.key_box)

        self.password_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        password_label = Gtk.Label(label="Senha:", halign=Gtk.Align.START)
        self.password_entry = Gtk.PasswordEntry(text=self.editing_session_item.auth_value if self.editing_session_item.auth_type == "password" else "", show_peek_icon=True)
        self.password_box.append(password_label); self.password_box.append(self.password_entry); self.ssh_box.append(self.password_box)
        main_box.append(self.ssh_box)

        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label="Cancelar")
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)
        save_button = Gtk.Button(label="Salvar", css_classes=["suggested-action"])
        save_button.connect("clicked", self._on_save_clicked)
        action_bar.pack_end(save_button)

        content_and_bar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scrolled_window = Gtk.ScrolledWindow(vexpand=True, hexpand=True, child=main_box)
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content_and_bar_box.append(scrolled_window)
        content_and_bar_box.append(action_bar)
        self.set_content(content_and_bar_box)

        self._update_ssh_visibility()
        self._update_auth_visibility()

    def _on_type_changed(self, dropdown, param): self._update_ssh_visibility()
    def _update_ssh_visibility(self): self.ssh_box.set_visible(self.type_combo.get_selected() == 1)
    def _on_auth_changed(self, dropdown, param): self._update_auth_visibility()
    def _update_auth_visibility(self):
        is_key = (self.auth_combo.get_selected() == 0)
        self.key_box.set_visible(is_key)
        self.password_box.set_visible(not is_key)

    def _on_browse_key_clicked(self, button):
        file_dialog = Gtk.FileDialog(modal=True) # Gtk.FileDialog não tem transient_for
        file_dialog.set_title("Selecionar Chave SSH")
        ssh_dir = Path.home() / ".ssh"
        if ssh_dir.exists(): file_dialog.set_initial_folder(Gio.File.new_for_path(str(ssh_dir)))
        file_dialog.open(self, None, self._on_file_dialog_response)

    def _on_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file: self.key_path_entry.set_text(file.get_path() or "")
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                print(f"Erro ao selecionar arquivo: {e.message}")

    def _on_cancel_clicked(self, button): self.close()

    def _on_save_clicked(self, button):
        self.editing_session_item.name = self.name_entry.get_text().strip()
        if not self.editing_session_item.name:
            dialog = Adw.MessageDialog(transient_for=self, title="Erro", body="O nome da sessão não pode estar vazio.")
            dialog.add_response("ok", "OK")
            dialog.present()
            return

        self.editing_session_item.session_type = "local" if self.type_combo.get_selected() == 0 else "ssh"

        if hasattr(self, "folder_combo"):
            selected_item = self.folder_combo.get_selected_item()
            self.editing_session_item.folder_path = self.folder_paths_map.get(selected_item.get_string(), "") if selected_item else ""
        else:
            self.editing_session_item.folder_path = self.original_session_item.folder_path if self.original_session_item else ""


        if self.editing_session_item.session_type == "ssh":
            self.editing_session_item.host = self.host_entry.get_text().strip()
            if not self.editing_session_item.host:
                dialog = Adw.MessageDialog(transient_for=self, title="Erro", body="O host não pode estar vazio para sessões SSH.")
                dialog.add_response("ok", "OK")
                dialog.present()
                return
            self.editing_session_item.user = self.user_entry.get_text().strip()
            self.editing_session_item.auth_type = "key" if self.auth_combo.get_selected() == 0 else "password"
            self.editing_session_item.auth_value = self.key_path_entry.get_text().strip() if self.editing_session_item.auth_type == "key" else self.password_entry.get_text()
        else:
            self.editing_session_item.host = ""; self.editing_session_item.user = ""
            self.editing_session_item.auth_type = ""; self.editing_session_item.auth_value = ""

        if self.is_new_item:
            self.session_store.append(self.editing_session_item)
        else:
            self.original_session_item.name = self.editing_session_item.name
            self.original_session_item.session_type = self.editing_session_item.session_type
            self.original_session_item.host = self.editing_session_item.host
            self.original_session_item.user = self.editing_session_item.user
            self.original_session_item.auth_type = self.editing_session_item.auth_type
            self.original_session_item.auth_value = self.editing_session_item.auth_value
            self.original_session_item.folder_path = self.editing_session_item.folder_path

        save_sessions_and_folders(self.session_store, self.folder_store)
        if hasattr(self.parent_window, '_refresh_tree_view'): self.parent_window._refresh_tree_view()
        self.close()


class FolderEditDialog(Adw.Window):
    def __init__(self, parent_window, folder_store, folder_item: Optional[SessionFolder] = None, position: Optional[int] = None):
        is_new_item = (folder_item is None)
        title = "Adicionar Pasta" if is_new_item else "Editar Pasta"
        super().__init__(title=title, modal=True, transient_for=parent_window, default_width=380, default_height=320, hide_on_close=True)

        self.parent_window = parent_window
        self.folder_store = folder_store
        self.original_folder_item = folder_item
        self.editing_folder_item = SessionFolder.from_dict(folder_item.to_dict()) if folder_item else SessionFolder(name="")
        self.position = position
        self.is_new_item = is_new_item
        self.old_path_on_edit = folder_item.path if folder_item else None

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15, margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)

        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name_label = Gtk.Label(label="<b>Nome da Pasta:</b>", use_markup=True, halign=Gtk.Align.START)
        self.name_entry = Gtk.Entry(text=self.editing_folder_item.name)
        name_box.append(name_label); name_box.append(self.name_entry); main_box.append(name_box)

        parent_box_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        parent_label = Gtk.Label(label="<b>Pasta Pai:</b>", use_markup=True, halign=Gtk.Align.START)
        self.parent_combo = Gtk.DropDown()
        parent_model_list = Gtk.StringList(); parent_model_list.append("Raiz")
        self.parent_paths_map = {"Raiz": ""}

        available_parents = [
            folder_store.get_item(i) for i in range(folder_store.get_n_items())
            if not (
                not self.is_new_item and
                (folder_store.get_item(i).path == self.editing_folder_item.path or
                 folder_store.get_item(i).path.startswith(self.editing_folder_item.path + "/"))
            )
        ]
        for p_folder in sorted(available_parents, key=lambda f: f.path):
            display_name = f"{'  ' * p_folder.path.count('/')}{p_folder.name}"
            parent_model_list.append(display_name); self.parent_paths_map[display_name] = p_folder.path

        self.parent_combo.set_model(parent_model_list)
        selected_dropdown_text = "Raiz"
        if self.editing_folder_item.parent_path:
            for display, path_val in self.parent_paths_map.items():
                if path_val == self.editing_folder_item.parent_path:
                    selected_dropdown_text = display; break

        found_idx = 0
        for i in range(parent_model_list.get_n_items()):
            if parent_model_list.get_string(i) == selected_dropdown_text:
                found_idx = i; break
        self.parent_combo.set_selected(found_idx)

        parent_box_outer.append(parent_label); parent_box_outer.append(self.parent_combo); main_box.append(parent_box_outer)

        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label="Cancelar"); cancel_button.connect("clicked", self._on_cancel_clicked); action_bar.pack_start(cancel_button)
        save_button = Gtk.Button(label="Salvar", css_classes=["suggested-action"]); save_button.connect("clicked", self._on_save_clicked); action_bar.pack_end(save_button)

        content_and_bar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_and_bar_box.append(main_box)
        content_and_bar_box.append(action_bar)
        self.set_content(content_and_bar_box)

    def _on_cancel_clicked(self, button): self.close()

    def _on_save_clicked(self, button):
        new_folder_name = self.name_entry.get_text().strip()
        if not new_folder_name:
            dialog = Adw.MessageDialog(transient_for=self, title="Erro", body="O nome da pasta não pode estar vazio.")
            dialog.add_response("ok", "OK")
            dialog.present()
            return

        selected_item = self.parent_combo.get_selected_item()
        new_parent_path = self.parent_paths_map.get(selected_item.get_string(), "") if selected_item else ""
        prospective_new_path = os.path.normpath(new_parent_path + "/" + new_folder_name if new_parent_path else "/" + new_folder_name)

        for i in range(self.folder_store.get_n_items()):
            f = self.folder_store.get_item(i)
            is_same_item_being_edited = (not self.is_new_item and f == self.original_folder_item)
            if f.path == prospective_new_path and not is_same_item_being_edited:
                dialog = Adw.MessageDialog(transient_for=self, title="Erro", body=f"O caminho da pasta '{prospective_new_path}' já existe.")
                dialog.add_response("ok", "OK")
                dialog.present()
                return

        self.editing_folder_item.name = new_folder_name
        self.editing_folder_item.parent_path = new_parent_path
        self.editing_folder_item.path = prospective_new_path

        if self.is_new_item:
            self.folder_store.append(self.editing_folder_item)
        else:
            path_changed = (self.old_path_on_edit != self.editing_folder_item.path)

            self.original_folder_item.name = self.editing_folder_item.name
            self.original_folder_item.parent_path = self.editing_folder_item.parent_path
            self.original_folder_item.path = self.editing_folder_item.path

            if path_changed and self.old_path_on_edit is not None:
                if hasattr(self.parent_window, '_update_child_paths'):
                    self.parent_window._update_child_paths(self.old_path_on_edit, self.original_folder_item.path)

        if hasattr(self.parent_window, 'session_store'):
            save_sessions_and_folders(self.parent_window.session_store, self.folder_store)
        else:
            save_sessions_and_folders(None, self.folder_store)

        if hasattr(self.parent_window, '_refresh_tree_view'): self.parent_window._refresh_tree_view()
        self.close()

class PreferencesDialog(Adw.PreferencesWindow):
    # ... (sem alterações, assumindo que Adw.MessageDialog.new já foi corrigido aqui) ...
    __gsignals__ = {
        "color-scheme-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "transparency-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "font-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "shortcut-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self, parent_window, settings_ref):
        super().__init__(title="Preferências", transient_for=parent_window, modal=True, hide_on_close=True)
        self.settings_ref = settings_ref

        appearance_page = Adw.PreferencesPage(title="Aparência", icon_name="preferences-desktop-theme-symbolic")
        self.add(appearance_page)

        colors_group = Adw.PreferencesGroup(title="Cores & Fonte")
        appearance_page.add(colors_group)

        color_scheme_row = Adw.ComboRow(title="Esquema de Cores", subtitle="Selecione o esquema de cores do terminal")
        color_scheme_strings = [name.replace("_", " ").title() for name in COLOR_SCHEME_MAP]
        color_scheme_row.set_model(Gtk.StringList.new(color_scheme_strings))
        color_scheme_row.set_selected(self.settings_ref.get("color_scheme", DEFAULT_SETTINGS["color_scheme"]))
        color_scheme_row.connect("notify::selected", self._on_color_scheme_changed)
        colors_group.add(color_scheme_row)

        transparency_row = Adw.ActionRow(title="Transparência do Fundo", subtitle="Ajuste a transparência do fundo do terminal (0-100%)")
        self.transparency_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.transparency_scale.set_value(self.settings_ref.get("transparency", DEFAULT_SETTINGS["transparency"]))
        self.transparency_scale.set_draw_value(True); self.transparency_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.transparency_scale.set_hexpand(True)
        self.transparency_scale.connect("value-changed", self._on_transparency_changed)
        transparency_row.add_suffix(self.transparency_scale); transparency_row.set_activatable_widget(self.transparency_scale)
        colors_group.add(transparency_row)

        font_row = Adw.ActionRow(title="Fonte do Terminal", subtitle="Selecione a fonte para o texto do terminal")
        self.font_button = Gtk.FontButton(font=self.settings_ref.get("font", DEFAULT_SETTINGS["font"]))
        self.font_button.connect("font-set", self._on_font_changed)
        font_row.add_suffix(self.font_button); font_row.set_activatable_widget(self.font_button)
        colors_group.add(font_row)

        shortcuts_page = Adw.PreferencesPage(title="Atalhos", icon_name="preferences-desktop-keyboard-shortcuts-symbolic")
        self.add(shortcuts_page)
        terminal_shortcuts_group = Adw.PreferencesGroup(title="Ações do Terminal"); shortcuts_page.add(terminal_shortcuts_group)
        self.shortcut_rows = {}
        shortcuts_data = self.settings_ref.get("shortcuts", DEFAULT_SETTINGS["shortcuts"])
        default_shortcuts = DEFAULT_SETTINGS["shortcuts"]

        shortcut_display_map = {
            "new-local-tab": "Nova Aba",
            "close-tab": "Fechar Aba",
            "copy": "Copiar",
            "paste": "Colar"
        }
        for key, title_str in shortcut_display_map.items():
            current_accel_str = shortcuts_data.get(key, default_shortcuts.get(key, ""))
            subtitle = current_accel_str if current_accel_str else "Nenhum"

            if current_accel_str:
                try:
                    keyval, mods = Gtk.accelerator_parse_with_keycode(current_accel_str, None, None)
                    if keyval != 0:
                        subtitle = Gtk.accelerator_get_label(keyval, mods)
                except GLib.Error:
                    try:
                        parsed_result = Gtk.accelerator_parse(current_accel_str)
                        if isinstance(parsed_result, tuple) and len(parsed_result) == 2:
                            keyval_alt, mods_alt = parsed_result
                            if keyval_alt != 0:
                                subtitle = Gtk.accelerator_get_label(keyval_alt, mods_alt)
                    except (GLib.Error, ValueError):
                        pass
                except Exception as e:
                    print(f"Erro inesperado ao parsear '{current_accel_str}': {e}")


            row = Adw.ActionRow(title=title_str, subtitle=subtitle)
            btn = Gtk.Button(label="Editar"); btn.connect("clicked", self._on_shortcut_edit_clicked, key, row)
            row.add_suffix(btn); row.set_activatable_widget(btn); terminal_shortcuts_group.add(row)
            self.shortcut_rows[key] = row

    def _on_color_scheme_changed(self, combo_row, param):
        idx = combo_row.get_selected(); self.settings_ref["color_scheme"] = idx
        save_settings(self.settings_ref); self.emit("color-scheme-changed", idx)

    def _on_transparency_changed(self, scale):
        val = scale.get_value(); self.settings_ref["transparency"] = val
        save_settings(self.settings_ref); self.emit("transparency-changed", val)

    def _on_font_changed(self, font_button):
        font = font_button.get_font(); self.settings_ref["font"] = font
        save_settings(self.settings_ref); self.emit("font-changed", font)

    def _on_shortcut_edit_clicked(self, button, shortcut_key_name: str, row_widget: Adw.ActionRow):
        dialog_title = f"Editar Atalho '{row_widget.get_title()}'"
        shortcut_dialog = Adw.MessageDialog( # Uso direto do construtor
            transient_for=self,
            title=dialog_title,
            body="Pressione a nova combinação de teclas ou Esc para cancelar."
        )

        current_accel_str = self.settings_ref.get("shortcuts", {}).get(shortcut_key_name, DEFAULT_SETTINGS["shortcuts"].get(shortcut_key_name, ""))
        display_accel = current_accel_str if current_accel_str else "Nenhum"

        if current_accel_str:
            try:
                keyval, mods = Gtk.accelerator_parse_with_keycode(current_accel_str, None, None)
                if keyval != 0:
                    display_accel = Gtk.accelerator_get_label(keyval, mods)
            except GLib.Error:
                try:
                    parsed_result = Gtk.accelerator_parse(current_accel_str)
                    if isinstance(parsed_result, tuple) and len(parsed_result) == 2:
                        keyval_alt, mods_alt = parsed_result
                        if keyval_alt != 0:
                            display_accel = Gtk.accelerator_get_label(keyval_alt, mods_alt)
                except (GLib.Error, ValueError):
                    pass
            except Exception as e:
                 print(f"Erro inesperado ao parsear display_accel '{current_accel_str}': {e}")

        feedback_label = Gtk.Label(label=f"Atual: {display_accel}\nNovo: (pressione as teclas)")
        shortcut_dialog.set_extra_child(feedback_label)

        key_controller = Gtk.EventControllerKey(); temp_new_accelerator = [None]

        def on_key_pressed(controller, keyval, keycode, state):
            if keyval in (Gdk.KEY_Control_L,Gdk.KEY_Control_R,Gdk.KEY_Shift_L,Gdk.KEY_Shift_R,Gdk.KEY_Alt_L,Gdk.KEY_Alt_R,Gdk.KEY_Super_L,Gdk.KEY_Super_R):
                return Gdk.EVENT_PROPAGATE
            if keyval == Gdk.KEY_Escape:
                temp_new_accelerator[0]="cancel"; shortcut_dialog.response("cancel_id");
                return Gdk.EVENT_STOP

            accel_name = Gtk.accelerator_name(keyval, state & Gtk.accelerator_get_default_mod_mask())
            temp_new_accelerator[0] = accel_name
            feedback_label.set_label(f"Atual: {display_accel}\nNovo: {Gtk.accelerator_get_label(keyval, state & Gtk.accelerator_get_default_mod_mask())}")
            return Gdk.EVENT_STOP

        key_controller.connect("key-pressed", on_key_pressed)
        shortcut_dialog.add_controller(key_controller)

        shortcut_dialog.add_response("cancel_id", "Cancelar")
        shortcut_dialog.add_response("save_id", "Definir Atalho")
        shortcut_dialog.set_default_response("save_id")
        shortcut_dialog.set_response_appearance("save_id", Adw.ResponseAppearance.SUGGESTED)

        def on_response(s_dialog, res_id):
            if res_id=="save_id" and temp_new_accelerator[0] and temp_new_accelerator[0]!="cancel":
                new_accel = temp_new_accelerator[0]
                if "shortcuts" not in self.settings_ref: self.settings_ref["shortcuts"] = {}
                self.settings_ref["shortcuts"][shortcut_key_name] = new_accel
                save_settings(self.settings_ref)

                s_label = new_accel if new_accel else "Nenhum"
                if new_accel:
                    try:
                        keyval, mods = Gtk.accelerator_parse_with_keycode(new_accel, None, None)
                        if keyval != 0:
                            s_label = Gtk.accelerator_get_label(keyval, mods)
                    except GLib.Error:
                        try:
                            parsed_result = Gtk.accelerator_parse(new_accel)
                            if isinstance(parsed_result, tuple) and len(parsed_result) == 2:
                                pk, pm = parsed_result
                                if pk != 0:
                                    s_label = Gtk.accelerator_get_label(pk, pm)
                        except (GLib.Error, ValueError):
                             pass
                    except Exception as e:
                        print(f"Erro inesperado ao parsear s_label '{new_accel}': {e}")

                row_widget.set_subtitle(s_label)
                self.emit("shortcut-changed")
            s_dialog.close()
        shortcut_dialog.connect("response", on_response)
        shortcut_dialog.present()