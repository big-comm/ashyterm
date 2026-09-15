# ashyterm/terminal/sftp_open.py
"""Open the remote side of an SSH terminal in the system file manager.

Two pieces, kept apart so the first is trivially unit-testable:

* :func:`resolve_remote_file_target` — pure. Combines what the
  registry knows about a terminal (session, type), what the manual-SSH
  tracker detected (``user@host`` typed by hand) and the last OSC7
  directory URI into a :class:`RemoteFileTarget`, or ``None`` when the
  terminal is not talking to a remote host.
* :func:`launch_file_manager_for_uri` — GIO. Hands an ``sftp://`` URI to
  whichever application the desktop uses for that scheme, falling back
  to the default directory handler (Nautilus, Dolphin, Thunar…) because
  most desktops never register an ``x-scheme-handler/sftp`` entry.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional
from urllib.parse import quote

from ..sessions.models import SessionItem
from ..utils.logger import get_logger
from ..utils.osc7 import parse_directory_uri

_logger = get_logger("ashyterm.terminal.sftp_open")

_DEFAULT_SSH_PORT = 22
_LOCAL_HOSTNAMES = frozenset({"", "localhost"})


class RemoteFileTarget(NamedTuple):
    """Where the system file manager should be pointed."""

    host: str
    user: str = ""
    port: Optional[int] = None
    path: Optional[str] = None


def build_sftp_uri(target: RemoteFileTarget) -> str:
    """Render ``sftp://[user@]host[:port][/path]`` for GVfs/KIO.

    When ``path`` is ``None`` the URI stops at the authority; both GVfs
    and KIO then land in the remote user's home directory.
    """
    authority = _format_host(target.host)
    if target.user:
        authority = f"{quote(target.user, safe='')}@{authority}"
    if target.port and target.port != _DEFAULT_SSH_PORT:
        authority = f"{authority}:{target.port}"
    uri = f"sftp://{authority}"
    if target.path:
        uri += quote(target.path if target.path.startswith("/") else f"/{target.path}")
    return uri


def _format_host(host: str) -> str:
    """Bracket bare IPv6 literals so the port separator stays unambiguous."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def resolve_remote_file_target(
    *,
    session: Any,
    ssh_target: Optional[str],
    terminal_type: Optional[str],
    directory_uri: Optional[str],
    local_hostname: str,
) -> Optional[RemoteFileTarget]:
    """Decide whether ``terminal`` has a remote side and where it is.

    Precedence mirrors ``FileManager.rebind_terminal``: a manual SSH
    session detected in a local terminal wins over the stored session,
    because the user is visibly *inside* that hop right now.
    """
    if ssh_target:
        user, host, port = _split_ssh_target(ssh_target)
        if not host:
            return None
        # The last OSC7 in a local terminal may predate the ``ssh``
        # command, so only trust a path the *remote* shell reported.
        path = _remote_path_from_uri(
            directory_uri, local_hostname, require_remote_host=True
        )
        return RemoteFileTarget(host=host, user=user, port=port, path=path)

    if isinstance(session, SessionItem) and session.is_ssh() and session.host:
        # SSH/SFTP terminals never run a local shell, so any OSC7 they
        # emitted came from the remote side.
        path = None
        if terminal_type == "ssh":
            path = _remote_path_from_uri(
                directory_uri, local_hostname, require_remote_host=False
            )
        return RemoteFileTarget(
            host=session.host,
            user=session.user or "",
            port=session.port,
            path=path,
        )
    return None


def _split_ssh_target(ssh_target: str) -> tuple[str, str, Optional[int]]:
    """Parse ``user@host``, ``host`` or ``ssh://user@host:port`` forms."""
    target = ssh_target.strip()
    port: Optional[int] = None
    if target.startswith("ssh://"):
        target = target[len("ssh://") :].split("/", 1)[0]
        target, port = _split_port(target)
    user, _, host = target.rpartition("@")
    return user, host, port


def _split_port(authority: str) -> tuple[str, Optional[int]]:
    if authority.startswith("["):
        return authority, None
    host, sep, port_text = authority.rpartition(":")
    if sep and port_text.isdigit() and "@" not in port_text:
        return host, int(port_text)
    return authority, None


def _remote_path_from_uri(
    directory_uri: Optional[str],
    local_hostname: str,
    *,
    require_remote_host: bool,
) -> Optional[str]:
    info = parse_directory_uri(directory_uri or "")
    if not info or not info.path:
        return None
    if require_remote_host and _is_local_hostname(info.hostname, local_hostname):
        return None
    return info.path


def _is_local_hostname(hostname: str, local_hostname: str) -> bool:
    candidate = (hostname or "").lower()
    if candidate in _LOCAL_HOSTNAMES:
        return True
    local = (local_hostname or "").lower()
    return bool(local) and candidate in (local, local.split(".", 1)[0])


def launch_file_manager_for_uri(uri: str) -> bool:
    """Open ``uri`` in the desktop's file manager. Returns success."""
    from gi.repository import Gio

    app_info = Gio.AppInfo.get_default_for_uri_scheme("sftp")
    if app_info is None:
        app_info = Gio.AppInfo.get_default_for_type("inode/directory", False)
        if app_info is not None and not app_info.supports_uris():
            app_info = None
    try:
        if app_info is not None:
            _logger.info(f"Opening {uri} with {app_info.get_id()}")
            return bool(app_info.launch_uris([uri], None))
        _logger.info(f"No file manager registered; using default handler for {uri}")
        return bool(Gio.AppInfo.launch_default_for_uri(uri, None))
    except Exception as exc:
        _logger.error(f"Failed to open {uri} in the file manager: {exc}")
        return False
