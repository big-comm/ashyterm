"""Tests for terminal/sftp_open (open remote directory in the system file manager)."""

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.sftp_open import (
    RemoteFileTarget,
    build_sftp_uri,
    resolve_remote_file_target,
)

LOCAL_HOST = "workstation"


def _ssh_session(**overrides) -> SessionItem:
    fields = dict(name="prod", session_type="ssh", host="example.org", user="deploy")
    fields.update(overrides)
    return SessionItem(**fields)


# --- build_sftp_uri ---------------------------------------------------------


def test_uri_minimal_host_only():
    assert build_sftp_uri(RemoteFileTarget(host="example.org")) == "sftp://example.org"


def test_uri_with_user_port_and_path():
    target = RemoteFileTarget(host="example.org", user="deploy", port=2222, path="/srv/www")
    assert build_sftp_uri(target) == "sftp://deploy@example.org:2222/srv/www"


def test_uri_omits_default_port():
    target = RemoteFileTarget(host="example.org", user="deploy", port=22, path="/")
    assert build_sftp_uri(target) == "sftp://deploy@example.org/"


def test_uri_percent_encodes_path_and_user():
    target = RemoteFileTarget(host="h", user="a b", path="/tmp/pasta com espaço/ação")
    assert build_sftp_uri(target) == (
        "sftp://a%20b@h/tmp/pasta%20com%20espa%C3%A7o/a%C3%A7%C3%A3o"
    )


def test_uri_brackets_ipv6_host():
    target = RemoteFileTarget(host="fe80::1", port=2200)
    assert build_sftp_uri(target) == "sftp://[fe80::1]:2200"


def test_uri_adds_leading_slash_to_relative_path():
    assert build_sftp_uri(RemoteFileTarget(host="h", path="srv")) == "sftp://h/srv"


# --- resolve_remote_file_target: stored sessions ----------------------------


def test_local_session_has_no_remote_target():
    local = SessionItem("Local Terminal", session_type="local")
    assert (
        resolve_remote_file_target(
            session=local,
            ssh_target=None,
            terminal_type="local",
            directory_uri=f"file://{LOCAL_HOST}/home/me",
            local_hostname=LOCAL_HOST,
        )
        is None
    )


def test_ssh_session_uses_session_fields_and_osc7_path():
    target = resolve_remote_file_target(
        session=_ssh_session(port=2222),
        ssh_target=None,
        terminal_type="ssh",
        directory_uri="file://remotebox/var/log",
        local_hostname=LOCAL_HOST,
    )
    assert target == RemoteFileTarget(
        host="example.org", user="deploy", port=2222, path="/var/log"
    )


def test_ssh_session_without_osc7_has_no_path():
    target = resolve_remote_file_target(
        session=_ssh_session(),
        ssh_target=None,
        terminal_type="ssh",
        directory_uri=None,
        local_hostname=LOCAL_HOST,
    )
    assert target is not None
    assert target.path is None
    assert build_sftp_uri(target) == "sftp://deploy@example.org"


def test_sftp_terminal_ignores_directory_uri():
    target = resolve_remote_file_target(
        session=_ssh_session(),
        ssh_target=None,
        terminal_type="sftp",
        directory_uri="file://whatever/x",
        local_hostname=LOCAL_HOST,
    )
    assert target is not None and target.path is None


def test_ssh_session_without_host_is_ignored():
    assert (
        resolve_remote_file_target(
            session=_ssh_session(host=""),
            ssh_target=None,
            terminal_type="ssh",
            directory_uri=None,
            local_hostname=LOCAL_HOST,
        )
        is None
    )


# --- resolve_remote_file_target: manual ssh in a local terminal -------------


def test_manual_ssh_target_wins_over_local_session():
    local = SessionItem("Local Terminal", session_type="local")
    target = resolve_remote_file_target(
        session=local,
        ssh_target="root@10.0.0.5",
        terminal_type="local",
        directory_uri="file://server5/etc",
        local_hostname=LOCAL_HOST,
    )
    assert target == RemoteFileTarget(host="10.0.0.5", user="root", port=None, path="/etc")


def test_manual_ssh_drops_stale_local_osc7_path():
    """Before ``ssh`` ran, the local shell reported its own cwd; ignore it."""
    for stale in (f"file://{LOCAL_HOST}/home/me", "file:///home/me", "file://localhost/x"):
        target = resolve_remote_file_target(
            session=None,
            ssh_target="host.example",
            terminal_type="local",
            directory_uri=stale,
            local_hostname=f"{LOCAL_HOST}.lan",
        )
        assert target == RemoteFileTarget(host="host.example", user="", port=None, path=None)


def test_manual_ssh_uri_form_with_port():
    target = resolve_remote_file_target(
        session=None,
        ssh_target="ssh://admin@box:2200/",
        terminal_type="local",
        directory_uri=None,
        local_hostname=LOCAL_HOST,
    )
    assert target == RemoteFileTarget(host="box", user="admin", port=2200, path=None)


def test_nothing_remote_returns_none():
    assert (
        resolve_remote_file_target(
            session=None,
            ssh_target=None,
            terminal_type="local",
            directory_uri="file://elsewhere/x",
            local_hostname=LOCAL_HOST,
        )
        is None
    )
