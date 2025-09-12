# ashyterm/sessions/models.py

import time
from typing import Any, Dict, List, Optional

from gi.repository import Gio, GObject

from ..utils.crypto import (
    clear_password,
    is_encryption_available,
    lookup_password,
    store_password,
)
from ..utils.exceptions import SessionValidationError
from ..utils.logger import get_logger
from ..utils.platform import normalize_path
from ..utils.security import InputSanitizer
from ..utils.translation_utils import _


class BaseModel(GObject.GObject):
    """Base model for data items, providing common metadata and validation logic."""

    def __init__(self):
        super().__init__()
        self._created_at = time.time()
        self._modified_at = self._created_at
        self.logger = get_logger("ashyterm.sessions.basemodel")

    def _mark_modified(self):
        """Updates the modification timestamp."""
        self._modified_at = time.time()

    def get_validation_errors(self) -> List[str]:
        """
        Returns a list of validation error messages.
        Subclasses must override this method.
        """
        return []

    def validate(self) -> bool:
        """
        Performs basic validation on the item's configuration.
        Logs a warning if validation fails.
        """
        errors = self.get_validation_errors()
        if errors:
            # Use getattr to safely access 'name' which exists on subclasses
            item_name = getattr(self, "name", "Unknown Item")
            self.logger.warning(
                f"{self.__class__.__name__} validation failed for '{item_name}': {errors}"
            )
            return False
        return True


class LayoutItem(GObject.GObject):
    """Data model for a saved layout item in the tree view."""

    def __init__(self, name: str, folder_path: str = ""):
        super().__init__()
        self._name = name
        self._folder_path = str(normalize_path(folder_path)) if folder_path else ""

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        self._name = value

    @property
    def folder_path(self) -> str:
        return self._folder_path

    @folder_path.setter
    def folder_path(self, value: str):
        self._folder_path = str(normalize_path(value)) if value else ""

    @property
    def children(self) -> Optional[Gio.ListStore]:
        return None


class SessionItem(BaseModel):
    """Data model for a terminal session, either local or remote (SSH)."""

    def __init__(
        self,
        name: str,
        session_type: str = "local",
        host: str = "",
        user: str = "",
        auth_type: str = "key",
        auth_value: str = "",
        folder_path: str = "",
        port: int = 22,
        tab_color: Optional[str] = None,
    ):
        super().__init__()
        self.logger = get_logger("ashyterm.sessions.model")

        # Core properties
        self._name = InputSanitizer.sanitize_filename(name)
        self._session_type = session_type
        self._host = host
        self._user = user
        self._auth_type = auth_type
        self._auth_value = auth_value
        self._folder_path = str(normalize_path(folder_path)) if folder_path else ""
        self._port = port
        self._tab_color = tab_color

    @property
    def children(self) -> Optional[Gio.ListStore]:
        """Session items are leaf nodes and have no children."""
        return None

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        old_name = self._name
        new_name = InputSanitizer.sanitize_filename(value)
        if old_name != new_name and self.uses_password_auth():
            password = lookup_password(old_name)
            if password:
                store_password(new_name, password)
                clear_password(old_name)
        self._name = new_name
        self._mark_modified()

    @property
    def session_type(self) -> str:
        return self._session_type

    @session_type.setter
    def session_type(self, value: str):
        if value not in ["local", "ssh"]:
            raise SessionValidationError(self.name, [f"Invalid session type: {value}"])
        self._session_type = value
        self._mark_modified()

    @property
    def host(self) -> str:
        return self._host

    @host.setter
    def host(self, value: str):
        self._host = value.strip()
        self._mark_modified()

    @property
    def user(self) -> str:
        return self._user

    @user.setter
    def user(self, value: str):
        self._user = value.strip()
        self._mark_modified()

    @property
    def auth_type(self) -> str:
        return self._auth_type

    @auth_type.setter
    def auth_type(self, value: str):
        if value not in ["key", "password", ""]:
            raise SessionValidationError(self.name, [f"Invalid auth type: {value}"])
        if self._auth_type == "password" and value != "password":
            clear_password(self.name)
        self._auth_type = value
        self._mark_modified()

    @property
    def auth_value(self) -> str:
        """Returns the password from keyring or the raw key path."""
        if self.uses_password_auth():
            if is_encryption_available():
                return lookup_password(self.name) or ""
            self.logger.warning(
                "Encryption is not available, cannot retrieve password."
            )
            return ""
        return self._auth_value

    @auth_value.setter
    def auth_value(self, value: str):
        """Sets the auth value, storing it in the keyring if it's a password."""
        if self.uses_password_auth():
            if is_encryption_available():
                if value:
                    store_password(self.name, value)
                else:
                    clear_password(self.name)
            else:
                self.logger.error("Cannot store password: Encryption is not available.")
            self._auth_value = ""
        else:
            self._auth_value = value
        self._mark_modified()

    @property
    def folder_path(self) -> str:
        return self._folder_path

    @folder_path.setter
    def folder_path(self, value: str):
        self._folder_path = str(normalize_path(value)) if value else ""
        self._mark_modified()

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: int):
        try:
            port_val = int(value)
            if not (1 <= port_val <= 65535):
                raise ValueError("Port out of range")
            self._port = port_val
            self._mark_modified()
        except (ValueError, TypeError):
            raise SessionValidationError(
                self.name, [_("Port must be a valid number between 1 and 65535")]
            )

    @property
    def tab_color(self) -> Optional[str]:
        return self._tab_color

    @tab_color.setter
    def tab_color(self, value: Optional[str]):
        self._tab_color = value
        self._mark_modified()

    def get_validation_errors(self) -> List[str]:
        """Returns a list of validation error messages."""
        errors = []
        if not self.name:
            errors.append(_("Session name is required."))
        if self.is_ssh():
            if not self.host:
                errors.append(_("Host is required for SSH sessions."))
            if not (1 <= self.port <= 65535):
                errors.append(_("Port must be between 1 and 65535."))
        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the session item to a dictionary."""
        auth_value_to_save = "" if self.uses_password_auth() else self._auth_value
        return {
            "name": self.name,
            "session_type": self._session_type,
            "host": self.host,
            "user": self.user,
            "auth_type": self._auth_type,
            "auth_value": auth_value_to_save,
            "folder_path": self._folder_path,
            "port": self.port,
            "tab_color": self.tab_color,
            "created_at": self._created_at,
            "modified_at": self._modified_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionItem":
        """Deserializes a dictionary into a SessionItem instance."""
        session = cls(
            name=data.get("name", _("Unnamed Session")),
            session_type=data.get("session_type", "local"),
            host=data.get("host", ""),
            user=data.get("user", ""),
            auth_type=data.get("auth_type", "key"),
            folder_path=data.get("folder_path", ""),
            port=data.get("port", 22),
        )
        # __init__ sets default metadata; overwrite with loaded data
        session._auth_value = data.get("auth_value", "")
        session.tab_color = data.get("tab_color")
        session._created_at = data.get("created_at", time.time())
        session._modified_at = data.get("modified_at", time.time())
        return session

    def is_local(self) -> bool:
        return self._session_type == "local"

    def is_ssh(self) -> bool:
        return self._session_type == "ssh"

    def uses_key_auth(self) -> bool:
        return self.is_ssh() and self._auth_type == "key"

    def uses_password_auth(self) -> bool:
        return self.is_ssh() and self._auth_type == "password"

    def get_connection_string(self) -> str:
        if self.is_local():
            return _("Local Terminal")
        return f"{self.user}@{self.host}" if self.user else self.host

    def __str__(self) -> str:
        return f"SessionItem(name='{self.name}', type='{self._session_type}')"


class SessionFolder(BaseModel):
    """Data model for a folder used to organize sessions."""

    def __init__(self, name: str, path: str = "", parent_path: str = ""):
        super().__init__()
        self.logger = get_logger("ashyterm.sessions.folder")

        self._name = InputSanitizer.sanitize_filename(name)
        self._path = str(normalize_path(path)) if path else ""
        self._parent_path = str(normalize_path(parent_path)) if parent_path else ""
        self._children = Gio.ListStore.new(GObject.GObject)

    @property
    def children(self) -> Gio.ListStore:
        """Provides the list of children for Gtk.TreeListModel."""
        return self._children

    def add_child(self, item):
        """Add a child item (SessionItem or SessionFolder) to this folder."""
        self._children.append(item)

    def clear_children(self):
        """Remove all children from this folder."""
        self._children.remove_all()

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        self._name = InputSanitizer.sanitize_filename(value)
        self._mark_modified()

    @property
    def path(self) -> str:
        return self._path

    @path.setter
    def path(self, value: str):
        self._path = str(normalize_path(value)) if value else ""
        self._mark_modified()

    @property
    def parent_path(self) -> str:
        return self._parent_path

    @parent_path.setter
    def parent_path(self, value: str):
        self._parent_path = str(normalize_path(value)) if value else ""
        self._mark_modified()

    def get_validation_errors(self) -> List[str]:
        """Returns a list of validation error messages."""
        errors = []
        if not self.name:
            errors.append(_("Folder name is required."))
        if (
            self.path
            and self.parent_path
            and not self.path.startswith(self.parent_path + "/")
            and self.parent_path != ""
        ):
            errors.append(_("Folder path is not consistent with its parent path."))
        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the folder item to a dictionary."""
        return {
            "name": self.name,
            "path": self.path,
            "parent_path": self.parent_path,
            "created_at": self._created_at,
            "modified_at": self._modified_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionFolder":
        """Deserializes a dictionary into a SessionFolder instance."""
        folder = cls(
            name=data.get("name", _("Unnamed Folder")),
            path=data.get("path", ""),
            parent_path=data.get("parent_path", ""),
        )
        # __init__ sets default metadata; overwrite with loaded data
        folder._created_at = data.get("created_at", time.time())
        folder._modified_at = data.get("modified_at", time.time())
        return folder

    def __str__(self) -> str:
        return f"SessionFolder(name='{self.name}', path='{self.path}')"