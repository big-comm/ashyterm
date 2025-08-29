# ashyterm/utils/crypto.py

from typing import Optional

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret

from .exceptions import AshyTerminalError
from .logger import get_logger

# Schema para identificar as senhas do Ashy Terminal no chaveiro do sistema.
SECRET_SCHEMA = Secret.Schema.new(
    "org.communitybig.ashyterm.Password",
    Secret.SchemaFlags.NONE,
    {"session_name": Secret.SchemaAttributeType.STRING},
)


def is_encryption_available() -> bool:
    """Verifica se a biblioteca libsecret está disponível."""
    try:
        # A importação bem-sucedida já é uma boa indicação.
        return True
    except (ImportError, ValueError):
        return False


def store_password(session_name: str, password: str) -> bool:
    """
    Armazena uma senha de forma segura no chaveiro do sistema (GNOME Keyring, KWallet).
    """
    if not is_encryption_available():
        raise AshyTerminalError("Secret Service API is not available.")

    try:
        attributes = {"session_name": session_name}
        # O último argumento "cancellable" é None, e a função é síncrona.
        Secret.password_store_sync(
            SECRET_SCHEMA,
            attributes,
            Secret.COLLECTION_DEFAULT,
            f"Password for Ashy Terminal session '{session_name}'",
            password,
            None,
        )
        get_logger().info(f"Stored password securely for session '{session_name}'.")
        return True
    except Exception as e:
        get_logger().error(
            f"Failed to store password for session '{session_name}': {e}"
        )
        raise AshyTerminalError(f"Failed to store password: {e}") from e


def lookup_password(session_name: str) -> Optional[str]:
    """
    Busca uma senha do chaveiro do sistema.
    """
    if not is_encryption_available():
        raise AshyTerminalError("Secret Service API is not available.")

    try:
        attributes = {"session_name": session_name}
        # A função síncrona retorna a senha ou None se não for encontrada.
        password = Secret.password_lookup_sync(SECRET_SCHEMA, attributes, None)
        if password:
            get_logger().info(
                f"Retrieved password securely for session '{session_name}'."
            )
            return password
        return None
    except Exception as e:
        get_logger().error(
            f"Failed to lookup password for session '{session_name}': {e}"
        )
        raise AshyTerminalError(f"Failed to lookup password: {e}") from e


def clear_password(session_name: str) -> bool:
    """
    Remove uma senha do chaveiro do sistema.
    """
    if not is_encryption_available():
        return False

    try:
        attributes = {"session_name": session_name}
        # A função síncrona retorna True em caso de sucesso.
        return Secret.password_clear_sync(SECRET_SCHEMA, attributes, None)
    except Exception as e:
        get_logger().error(
            f"Failed to clear password for session '{session_name}': {e}"
        )
        return False
