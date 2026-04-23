"""Property-based roundtrip tests for SessionItem / SessionFolder.

Invariant: ``from_dict(to_dict(s))`` preserves every observable field on
the original object (minus the quirks listed below). Hypothesis explores
the input space looking for combinations that break the contract.

Quirks deliberately excluded from the equality:

* ``auth_value`` for password sessions is always blanked in ``to_dict`` —
  the real password lives in the keyring, the dict never carries it.
* ``name`` and path fields are normalized/sanitized on ``__init__``.
  Generating only pre-sanitized values avoids false positives where the
  input mutates on first store, before we ever serialize.

Porting these to Rust: same properties, proptest / quickcheck crate.
The strategies translate directly; the invariants are the contract the
Rust models must satisfy.
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="Install python-hypothesis to run property tests.",
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ashyterm.sessions.models import SessionFolder, SessionItem


SAFE_FILENAME_ALPHABET = st.characters(
    min_codepoint=33,
    max_codepoint=126,
    blacklist_characters='<>:"/\\|?*\0. ',
)
SAFE_NAMES = st.text(alphabet=SAFE_FILENAME_ALPHABET, min_size=1, max_size=40)

SAFE_HOSTS = st.text(
    alphabet=st.characters(
        min_codepoint=ord("a"), max_codepoint=ord("z"), whitelist_characters="0123456789.-"
    ),
    min_size=1,
    max_size=40,
)

SAFE_USERS = st.text(
    alphabet=st.characters(
        min_codepoint=ord("a"), max_codepoint=ord("z"), whitelist_characters="0123456789_-"
    ),
    min_size=1,
    max_size=32,
)

PORTS = st.integers(min_value=1, max_value=65535)
FORWARD_PORTS = st.integers(min_value=1025, max_value=65535)
TRISTATE = st.one_of(st.none(), st.booleans())


@st.composite
def port_forwarding_entries(draw):
    return {
        "name": draw(SAFE_NAMES),
        "local_host": draw(st.sampled_from(["localhost", "127.0.0.1", "0.0.0.0"])),
        "local_port": draw(FORWARD_PORTS),
        "remote_host": draw(SAFE_HOSTS),
        "remote_port": draw(PORTS),
    }


@st.composite
def session_items(draw):
    session_type = draw(st.sampled_from(["local", "ssh"]))
    auth_type = draw(st.sampled_from(["key", "password"]))
    uses_password = session_type == "ssh" and auth_type == "password"

    return SessionItem(
        name=draw(SAFE_NAMES),
        session_type=session_type,
        host=draw(SAFE_HOSTS) if session_type == "ssh" else "",
        user=draw(SAFE_USERS) if session_type == "ssh" else "",
        auth_type=auth_type,
        # Password auth goes to keyring → always empty in to_dict. Use empty
        # to keep the invariant trivially satisfiable without libsecret.
        auth_value="" if uses_password else draw(st.text(max_size=40)),
        folder_path="",  # paths go through normalize_path — skip for now
        port=draw(PORTS),
        tab_color=draw(st.one_of(st.none(), st.sampled_from(["#ff0000", "#00ff00"]))),
        post_login_command_enabled=draw(st.booleans()),
        post_login_command=draw(st.text(max_size=40)),
        sftp_session_enabled=draw(st.booleans()),
        sftp_local_directory="",
        sftp_remote_directory=draw(st.text(max_size=40)),
        port_forwardings=draw(st.lists(port_forwarding_entries(), max_size=4)),
        x11_forwarding=draw(st.booleans()),
        proxy_jump=draw(st.text(max_size=60)),
        source=draw(st.sampled_from(["user", "system"])),
        local_working_directory="",
        local_startup_command=draw(st.text(max_size=40)),
        output_highlighting=draw(TRISTATE),
        command_specific_highlighting=draw(TRISTATE),
        cat_colorization=draw(TRISTATE),
        shell_input_highlighting=draw(TRISTATE),
    )


@st.composite
def session_folders(draw):
    # Paths go through normalize_path. Use simple pre-normalized paths so
    # equality survives the roundtrip without a fixed-point dance.
    return SessionFolder(
        name=draw(SAFE_NAMES),
        path=draw(st.sampled_from(["", "/a", "/a/b"])),
        parent_path=draw(st.sampled_from(["", "/a"])),
    )


def _session_observable_state(s: SessionItem) -> dict:
    """Tuple of values every equal SessionItem must agree on."""
    return {
        "name": s.name,
        "session_type": s.session_type,
        "host": s.host,
        "user": s.user,
        "auth_type": s.auth_type,
        "port": s.port,
        "tab_color": s.tab_color,
        "post_login_command_enabled": s.post_login_command_enabled,
        "post_login_command": s.post_login_command,
        "sftp_session_enabled": s.sftp_session_enabled,
        "sftp_remote_directory": s.sftp_remote_directory,
        "port_forwardings": s.port_forwardings,
        "x11_forwarding": s.x11_forwarding,
        "proxy_jump": s.proxy_jump,
        "source": s.source,
        "local_startup_command": s.local_startup_command,
        "output_highlighting": s.output_highlighting,
        "command_specific_highlighting": s.command_specific_highlighting,
        "cat_colorization": s.cat_colorization,
        "shell_input_highlighting": s.shell_input_highlighting,
    }


def _folder_observable_state(f: SessionFolder) -> dict:
    return {"name": f.name, "path": f.path, "parent_path": f.parent_path}


# ── Properties ─────────────────────────────────────────────


@given(session_items())
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_session_item_roundtrip_preserves_observable_state(session):
    restored = SessionItem.from_dict(session.to_dict())
    assert _session_observable_state(restored) == _session_observable_state(session)


@given(session_items())
@settings(max_examples=100, deadline=None)
def test_session_item_double_roundtrip_is_idempotent(session):
    """Serializing twice gives the same dict — catches non-deterministic fields."""
    once = SessionItem.from_dict(session.to_dict()).to_dict()
    twice = SessionItem.from_dict(once).to_dict()
    # ``modified_at`` / ``created_at`` pass through untouched so they match too.
    assert once == twice


@given(session_folders())
@settings(max_examples=100, deadline=None)
def test_session_folder_roundtrip_preserves_observable_state(folder):
    restored = SessionFolder.from_dict(folder.to_dict())
    assert _folder_observable_state(restored) == _folder_observable_state(folder)


@given(
    auth=st.sampled_from(["key", "password"]),
    value=st.text(max_size=40),
)
def test_password_auth_value_never_leaks_to_dict(auth, value):
    """Regression guard: no matter what auth_value is set to, a password
    session must serialize with an empty auth_value (the real thing is in
    the keyring). Key auth is the only path that keeps the value."""
    s = SessionItem(
        name="s",
        session_type="ssh",
        host="h",
        user="u",
        auth_type=auth,
        auth_value=value,
    )
    serialized = s.to_dict()
    if auth == "password":
        assert serialized["auth_value"] == ""
    else:
        assert serialized["auth_value"] == value
