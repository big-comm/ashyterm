# ashyterm/terminal/ssh_options.py
"""Pure builders for SSH ``-o`` option dictionaries.

Every SSH spawn goes through the same funnel: pick a base set of
options (ControlMaster, timeouts, host-key policy), layer in X11 and
port-forwarding tweaks, then hand the dict off to the command builder.
The rules are all side-effect-free — this module owns them so they
can be audited (and tested) without spinning up the spawner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from ..sessions.models import SessionItem


# Values accepted by OpenSSH's ``StrictHostKeyChecking``. Any
# configured value outside this set falls back to ``accept-new``.
ALLOWED_STRICT_HOSTKEY: tuple[str, ...] = ("ask", "accept-new", "yes", "no")

DEFAULT_STRICT_HOSTKEY: str = "accept-new"


def resolve_strict_host_key_checking(value: str) -> str:
    """Clamp ``value`` to an accepted StrictHostKeyChecking policy.

    Returns :data:`DEFAULT_STRICT_HOSTKEY` for anything unexpected —
    OpenSSH would silently refuse to start otherwise.
    """
    if value in ALLOWED_STRICT_HOSTKEY:
        return value
    return DEFAULT_STRICT_HOSTKEY


def build_ssh_test_options(
    session: "SessionItem",
    *,
    use_password: bool,
    strict_host_key: str,
) -> Dict[str, str]:
    """Options for the lightweight "can we connect?" test run.

    Differences from a real spawn:

    * No ControlMaster/Path (we're not reusing the connection).
    * ``BatchMode`` flips with ``use_password`` — we need to *allow*
      the TTY password prompt when ``sshpass`` will inject the answer.
    * Short ``ConnectTimeout`` so the UI doesn't spin on dead hosts.
    """
    opts: Dict[str, str] = {
        "BatchMode": "no" if use_password else "yes",
        "ConnectTimeout": "10",
        "StrictHostKeyChecking": strict_host_key,
        "PasswordAuthentication": "yes" if use_password else "no",
    }
    if getattr(session, "x11_forwarding", False):
        opts["ForwardX11"] = "yes"
        opts["ForwardX11Trusted"] = "yes"
    _apply_proxy_jump(opts, session)
    return opts


def build_base_ssh_options(
    session: "SessionItem",
    *,
    strict_host_key: str,
    connect_timeout: int,
    control_persist_duration: int,
    control_path: str,
) -> Dict[str, str]:
    """Base option dict every long-lived SSH spawn starts from.

    ``control_persist_duration`` of ``0`` (or negative) disables the
    ControlPersist option entirely — used when the user explicitly
    turns off connection multiplexing.
    """
    options: Dict[str, str] = {
        "ConnectTimeout": str(connect_timeout),
        "ServerAliveInterval": "30",
        "ServerAliveCountMax": "3",
        "StrictHostKeyChecking": strict_host_key,
        "UpdateHostKeys": "yes",
        "ControlMaster": "auto",
        "ControlPath": control_path,
    }
    if control_persist_duration > 0:
        options["ControlPersist"] = str(control_persist_duration)
    _apply_proxy_jump(options, session)
    return options


def _apply_proxy_jump(
    options: Dict[str, str], session: "SessionItem"
) -> None:
    """Set ``ProxyJump`` from ``session.proxy_jump`` if configured.

    ProxyJump disables multiplexing because the proxied hop is what
    holds the master; reusing our own ControlPath would dial the wrong
    host. Stripping the Control* trio here keeps the connection fresh.
    """
    jump = (getattr(session, "proxy_jump", "") or "").strip()
    if not jump:
        return
    options["ProxyJump"] = jump
    options.pop("ControlPersist", None)
    options.pop("ControlMaster", None)
    options.pop("ControlPath", None)


def apply_x11_and_tunnel_options(
    ssh_options: Dict[str, str],
    session: "SessionItem",
    command_type: str,
) -> None:
    """In-place: layer X11 + port-forwarding tweaks onto ``ssh_options``.

    X11 and tunnels need a fresh connection — reusing a multiplexed
    master would miss forwarding setup. We strip the ControlMaster
    trio whenever either feature is requested.

    ``ExitOnForwardFailure`` is set when tunnels are configured so a
    bind-port clash surfaces as an SSH exit rather than a silently
    functional-but-incomplete session.
    """
    if command_type != "ssh":
        # SFTP/SCP etc. don't use X11 or port forwarding.
        return

    has_x11 = bool(getattr(session, "x11_forwarding", False))
    has_tunnels = bool(getattr(session, "port_forwardings", None))

    if has_x11 or has_tunnels:
        # Drop multiplexing so the forwarding setup actually runs.
        ssh_options.pop("ControlPersist", None)
        ssh_options.pop("ControlMaster", None)
        ssh_options.pop("ControlPath", None)

    if has_tunnels:
        ssh_options["ExitOnForwardFailure"] = "yes"

    if has_x11:
        ssh_options["ForwardX11"] = "yes"
        ssh_options["ForwardX11Trusted"] = "yes"


def needs_x11_flag(session: "SessionItem", command_type: str) -> bool:
    """True when the command line needs ``-Y`` for trusted X11.

    The ``-Y`` flag complements the ``ForwardX11Trusted`` option — the
    flag activates forwarding at connect time, the option marks the
    connection as trusted so unrestricted GUI apps can run.
    """
    if command_type != "ssh":
        return False
    return bool(getattr(session, "x11_forwarding", False))
