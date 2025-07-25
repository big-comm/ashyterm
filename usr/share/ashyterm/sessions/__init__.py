"""Sessions module for Ashy Terminal."""

from .models import SessionItem, SessionFolder
from .storage import (
    load_sessions_and_folders, save_sessions_and_folders,
    load_sessions_to_store, load_folders_to_store,
    find_session_by_name, find_folder_by_path,
    get_sessions_in_folder, get_subfolders
)
from .operations import SessionOperations
from .tree import SessionTreeView

__all__ = [
    # Models
    "SessionItem", "SessionFolder",
    
    # Storage functions
    "load_sessions_and_folders", "save_sessions_and_folders",
    "load_sessions_to_store", "load_folders_to_store",
    "find_session_by_name", "find_folder_by_path",
    "get_sessions_in_folder", "get_subfolders",
    
    # Operations and UI
    "SessionOperations", "SessionTreeView"
]