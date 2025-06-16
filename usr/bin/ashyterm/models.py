from typing import Dict, Any
from gi.repository import GObject

class SessionItem(GObject.GObject):
    def __init__(self, name: str, session_type: str = "local",
                 host: str = "", user: str = "",
                 auth_type: str = "key", auth_value: str = "",
                 folder_path: str = ""): # Caminho da pasta pai, "" se raiz
        super().__init__()
        self.name = name
        self.session_type = session_type # "local" ou "ssh"
        self.host = host
        self.user = user
        self.auth_type = auth_type # "key" ou "password" para SSH
        self.auth_value = auth_value # Caminho da chave ou senha
        self.folder_path = folder_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "session_type": self.session_type,
            "host": self.host,
            "user": self.user,
            "auth_type": self.auth_type,
            "auth_value": self.auth_value,
            "folder_path": self.folder_path
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionItem":
        return cls(
            name=data.get("name", "Sessão Sem Nome"),
            session_type=data.get("session_type", "local"),
            host=data.get("host", ""),
            user=data.get("user", ""),
            auth_type=data.get("auth_type", "key"), # Padrão para chave se não especificado
            auth_value=data.get("auth_value", ""),
            folder_path=data.get("folder_path", "")
        )

class SessionFolder(GObject.GObject):
    def __init__(self, name: str, path: str = "", parent_path: str = ""):
        super().__init__()
        self.name = name
        self.path = path # Caminho completo da pasta, ex: /pasta1/subpastaA
        self.parent_path = parent_path # Caminho da pasta pai, ex: /pasta1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "parent_path": self.parent_path
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionFolder":
        return cls(
            name=data.get("name", "Pasta Sem Nome"),
            path=data.get("path", ""),
            parent_path=data.get("parent_path", "")
        )