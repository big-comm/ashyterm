# ashyterm/utils.py

import json
import os
from typing import Tuple, List, Dict, Any, Optional
from gi.repository import Gio

from .config import SETTINGS_FILE, SESSIONS_FILE, DEFAULT_SETTINGS
from .models import SessionItem, SessionFolder

def load_settings() -> Dict[str, Any]:
    # ... (sem alterações aqui) ...
    settings = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                loaded_settings = json.load(f)
                for key, default_value in DEFAULT_SETTINGS.items():
                    if key in loaded_settings:
                        if isinstance(default_value, dict) and isinstance(loaded_settings[key], dict):
                            if key not in settings or not isinstance(settings[key], dict):
                                settings[key] = {}
                            for sub_key, sub_default_value in default_value.items():
                                settings[key][sub_key] = loaded_settings[key].get(sub_key, sub_default_value)
                        else:
                            settings[key] = loaded_settings[key]
        except (json.JSONDecodeError, IOError, TypeError) as e:
            print(f"Erro ao carregar arquivo de configurações '{SETTINGS_FILE}': {e}. Usando configurações padrão.")
            return DEFAULT_SETTINGS.copy()
    return settings

def save_settings(settings_data: Dict[str, Any]):
    # ... (sem alterações aqui) ...
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings_data, f, indent=4)
    except IOError as e:
        print(f"Erro ao salvar arquivo de configurações '{SETTINGS_FILE}': {e}")

def load_sessions_and_folders() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    # ... (sem alterações aqui, mas adicionei prints para consistência se quiser) ...
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r") as f:
                data = json.load(f)
                sessions = data.get("sessions", [])
                folders = data.get("folders", [])
                # print(f"DEBUG utils.py load_sessions_and_folders: Carregado {len(sessions)} sessões, {len(folders)} pastas.")
                return sessions, folders
        except (json.JSONDecodeError, IOError) as e:
            print(f"Erro ao carregar arquivo de sessões/pastas '{SESSIONS_FILE}': {e}. Retornando listas vazias.")
            return [], []
    # print("DEBUG utils.py load_sessions_and_folders: Arquivo de sessões não encontrado, retornando listas vazias.")
    return [], []

def save_sessions_and_folders(session_store: Optional[Gio.ListStore] = None, folder_store: Optional[Gio.ListStore] = None):
    """Salva sessões e pastas no arquivo JSON."""
    data_to_save = {}

    print(f"\nDEBUG utils.py save_sessions_and_folders: INÍCIO DA CHAMADA")
    if session_store is not None: # Verifica explicitamente por None
        print(f"DEBUG utils.py: session_store FOI fornecido. N. itens: {session_store.get_n_items()}")
        sessions_list = []
        for i in range(session_store.get_n_items()):
            session_item = session_store.get_item(i)
            if isinstance(session_item, SessionItem):
                sessions_list.append(session_item.to_dict())
        print(f"DEBUG utils.py: Construída sessions_list a partir do session_store fornecido. {len(sessions_list)} sessões.")
    else:
        print(f"DEBUG utils.py: session_store NÃO FOI fornecido. Carregando sessões existentes do disco...")
        existing_sessions, _ = load_sessions_and_folders()
        sessions_list = existing_sessions
        print(f"DEBUG utils.py: Carregadas {len(sessions_list)} sessões do disco para sessions_list.")
    data_to_save["sessions"] = sessions_list

    if folder_store is not None: # Verifica explicitamente por None
        print(f"DEBUG utils.py: folder_store FOI fornecido. N. itens: {folder_store.get_n_items()}")
        folders_list = []
        for i in range(folder_store.get_n_items()):
            folder_item = folder_store.get_item(i)
            if isinstance(folder_item, SessionFolder):
                folders_list.append(folder_item.to_dict())
        print(f"DEBUG utils.py: Construída folders_list a partir do folder_store fornecido. {len(folders_list)} pastas.")
    else:
        print(f"DEBUG utils.py: folder_store NÃO FOI fornecido. Carregando pastas existentes do disco...")
        _, existing_folders = load_sessions_and_folders()
        folders_list = existing_folders
        print(f"DEBUG utils.py: Carregadas {len(folders_list)} pastas do disco para folders_list.")
    data_to_save["folders"] = folders_list

    print(f"DEBUG utils.py: PREPARANDO PARA SALVAR NO ARQUIVO:")
    print(f"DEBUG utils.py:   Total de SESSÕES a serem salvas: {len(sessions_list)}")
    # print(f"DEBUG utils.py:   Conteúdo de sessions_list: {sessions_list}")
    print(f"DEBUG utils.py:   Total de PASTAS a serem salvas: {len(folders_list)}")
    print(f"DEBUG utils.py:   Conteúdo detalhado de folders_list: {folders_list}") # Mudado para printar sempre

    try:
        os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
        with open(SESSIONS_FILE, "w") as f:
            json.dump(data_to_save, f, indent=4)
        print(f"DEBUG utils.py: Dados SALVOS com sucesso em '{SESSIONS_FILE}'.")
    except IOError as e:
        print(f"Erro CRÍTICO ao salvar arquivo de sessões/pastas '{SESSIONS_FILE}': {e}")
    print(f"DEBUG utils.py save_sessions_and_folders: FIM DA CHAMADA\n")