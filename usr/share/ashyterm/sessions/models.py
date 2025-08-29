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
from ..utils.security import sanitize_folder_name, sanitize_session_name
from ..utils.translation_utils import _


class SessionItem(GObject.GObject):
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
    ):
        super().__init__()
        self.logger = get_logger("ashyterm.sessions.model")

        # Core properties
        self._name = sanitize_session_name(name)
        self._session_type = session_type
        self._host = host
        self._user = user
        self._auth_type = auth_type
        # For password auth, _auth_value is NOT used. Password is in keyring.
        # For key auth, this stores the key path.
        self._auth_value = auth_value
        self._folder_path = str(normalize_path(folder_path)) if folder_path else ""
        self._port = port

        # Metadata
        self._created_at = time.time()
        self._modified_at = self._created_at
        self._last_used = None
        self._use_count = 0
        self._version = 1

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
        new_name = sanitize_session_name(value)
        if old_name != new_name and self.uses_password_auth():
            # Migrate password in keyring if session is renamed
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
        # If changing away from password auth, clear the stored password
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
            # Do not store the password in the object itself
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

    def _mark_modified(self):
        self._modified_at = time.time()

    def mark_used(self):
        self._last_used = time.time()
        self._use_count += 1

    def validate(self) -> bool:
        """Performs basic validation on the session's configuration."""
        errors = self.get_validation_errors()
        if errors:
            self.logger.warning(
                f"Session validation failed for '{self.name}': {errors}"
            )
            return False
        return True

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
        # For password auth, auth_value is intentionally saved as an empty string
        # to avoid storing secrets in the JSON file.
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
            "created_at": self._created_at,
            "modified_at": self._modified_at,
            "last_used": self._last_used,
            "use_count": self._use_count,
            "version": self._version,
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
        # auth_value is loaded directly. If it's a password session, this will be
        # an empty string, and the actual password must be in the keyring.
        session._auth_value = data.get("auth_value", "")
        # Restore metadata
        session._created_at = data.get("created_at", time.time())
        session._modified_at = data.get("modified_at", time.time())
        session._last_used = data.get("last_used")
        session._use_count = data.get("use_count", 0)
        session._version = data.get("version", 1)
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


class SessionFolder(GObject.GObject):
    """Data model for a folder used to organize sessions."""

    def __init__(self, name: str, path: str = "", parent_path: str = ""):
        super().__init__()
        self.logger = get_logger("ashyterm.sessions.folder")

        # Core properties
        self._name = sanitize_folder_name(name)
        self._path = str(normalize_path(path)) if path else ""
        self._parent_path = str(normalize_path(parent_path)) if parent_path else ""

        # Metadata
        self._created_at = time.time()
        self._modified_at = self._created_at
        self._version = 1

        # Store for child items (SessionItem or SessionFolder)
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
        self._name = sanitize_folder_name(value)
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

    def _mark_modified(self):
        self._modified_at = time.time()

    def validate(self) -> bool:
        """Performs basic validation on the folder's configuration."""
        errors = self.get_validation_errors()
        if errors:
            self.logger.warning(f"Folder validation failed for '{self.name}': {errors}")
            return False
        return True

    def get_validation_errors(self) -> List[str]:
        """Returns a list of validation error messages."""
        errors = []
        if not self.name:
            errors.append(_("Folder name is required."))
        if (
            self.path
            and self.parent_path
            and not self.path.startswith(self.parent_path + "/")
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
            "version": self._version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionFolder":
        """Deserializes a dictionary into a SessionFolder instance."""
        folder = cls(
            name=data.get("name", _("Unnamed Folder")),
            path=data.get("path", ""),
            parent_path=data.get("parent_path", ""),
        )
        folder._created_at = data.get("created_at", time.time())
        folder._modified_at = data.get("modified_at", time.time())
        folder._version = data.get("version", 1)
        return folder

    def __str__(self) -> str:
        return f"SessionFolder(name='{self.name}', path='{self.path}')"
