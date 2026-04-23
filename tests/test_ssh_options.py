"""Tests for ssh_options (SSH -o dictionary builders)."""

import pytest

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.ssh_options import (
    ALLOWED_STRICT_HOSTKEY,
    DEFAULT_STRICT_HOSTKEY,
    apply_x11_and_tunnel_options,
    build_base_ssh_options,
    build_ssh_test_options,
    needs_x11_flag,
    resolve_strict_host_key_checking,
)


def _session(**kwargs) -> SessionItem:
    """SessionItem factory with sensible SSH defaults."""
    base = dict(
        name="s",
        session_type="ssh",
        host="example.com",
        user="alice",
        port=22,
        auth_type="key",
        auth_value="/home/alice/.ssh/id",
    )
    base.update(kwargs)
    return SessionItem(**base)


# ── resolve_strict_host_key_checking ───────────────────────


class TestResolveStrictHostKeyChecking:
    @pytest.mark.parametrize("value", ALLOWED_STRICT_HOSTKEY)
    def test_allowed_values_pass_through(self, value):
        assert resolve_strict_host_key_checking(value) == value

    def test_unknown_value_falls_back_to_default(self):
        assert resolve_strict_host_key_checking("maybe") == DEFAULT_STRICT_HOSTKEY
        assert resolve_strict_host_key_checking("") == DEFAULT_STRICT_HOSTKEY
        assert resolve_strict_host_key_checking("YES") == DEFAULT_STRICT_HOSTKEY

    def test_default_itself_is_in_allowlist(self):
        # Sanity: the fallback must be a value OpenSSH actually accepts.
        assert DEFAULT_STRICT_HOSTKEY in ALLOWED_STRICT_HOSTKEY


# ── build_ssh_test_options ─────────────────────────────────


class TestBuildSshTestOptions:
    def test_key_auth_disables_password_and_batchmode_on(self):
        opts = build_ssh_test_options(
            _session(), use_password=False, strict_host_key="accept-new"
        )
        assert opts["BatchMode"] == "yes"
        assert opts["PasswordAuthentication"] == "no"
        assert opts["ConnectTimeout"] == "10"

    def test_password_auth_opens_batch_mode_and_password_auth(self):
        opts = build_ssh_test_options(
            _session(), use_password=True, strict_host_key="accept-new"
        )
        assert opts["BatchMode"] == "no"
        assert opts["PasswordAuthentication"] == "yes"

    def test_strict_host_key_is_honored(self):
        opts = build_ssh_test_options(
            _session(), use_password=False, strict_host_key="yes"
        )
        assert opts["StrictHostKeyChecking"] == "yes"

    def test_x11_session_adds_forward_options(self):
        opts = build_ssh_test_options(
            _session(x11_forwarding=True),
            use_password=False,
            strict_host_key="accept-new",
        )
        assert opts["ForwardX11"] == "yes"
        assert opts["ForwardX11Trusted"] == "yes"

    def test_no_x11_omits_forward_options(self):
        opts = build_ssh_test_options(
            _session(), use_password=False, strict_host_key="accept-new"
        )
        assert "ForwardX11" not in opts
        assert "ForwardX11Trusted" not in opts


# ── build_base_ssh_options ─────────────────────────────────


class TestBuildBaseSshOptions:
    def _call(self, **overrides):
        defaults = dict(
            strict_host_key="accept-new",
            connect_timeout=30,
            control_persist_duration=600,
            control_path="/tmp/cp-foo",
        )
        defaults.update(overrides)
        return build_base_ssh_options(_session(), **defaults)

    def test_control_trio_present_by_default(self):
        opts = self._call()
        assert opts["ControlMaster"] == "auto"
        assert opts["ControlPath"] == "/tmp/cp-foo"
        assert opts["ControlPersist"] == "600"

    def test_control_persist_zero_drops_the_option(self):
        opts = self._call(control_persist_duration=0)
        assert "ControlPersist" not in opts
        # The other control-trio entries remain; the caller may still
        # want multiplexing even without persisting the master.
        assert "ControlMaster" in opts
        assert "ControlPath" in opts

    def test_negative_persist_drops_the_option_too(self):
        # Defensive: negative values are nonsense but shouldn't crash.
        opts = self._call(control_persist_duration=-1)
        assert "ControlPersist" not in opts

    def test_server_alive_values_are_fixed(self):
        opts = self._call()
        # These constants protect against NAT/firewall timeouts; they
        # should never be configurable per-build.
        assert opts["ServerAliveInterval"] == "30"
        assert opts["ServerAliveCountMax"] == "3"

    def test_connect_timeout_is_stringified(self):
        opts = self._call(connect_timeout=45)
        assert opts["ConnectTimeout"] == "45"


# ── apply_x11_and_tunnel_options ───────────────────────────


def _base_ctrl_opts() -> dict:
    """Base options with the ControlMaster trio populated."""
    return {
        "ControlMaster": "auto",
        "ControlPath": "/tmp/cp-foo",
        "ControlPersist": "600",
    }


class TestApplyX11AndTunnelOptions:
    def test_non_ssh_command_is_noop(self):
        opts = _base_ctrl_opts()
        apply_x11_and_tunnel_options(
            opts, _session(x11_forwarding=True), command_type="sftp"
        )
        assert "ControlMaster" in opts
        assert "ForwardX11" not in opts

    def test_plain_ssh_without_x11_or_tunnels_is_noop(self):
        opts = _base_ctrl_opts()
        apply_x11_and_tunnel_options(opts, _session(), command_type="ssh")
        assert "ControlMaster" in opts
        assert "ExitOnForwardFailure" not in opts

    def test_x11_strips_control_master_trio(self):
        opts = _base_ctrl_opts()
        apply_x11_and_tunnel_options(
            opts, _session(x11_forwarding=True), command_type="ssh"
        )
        assert "ControlMaster" not in opts
        assert "ControlPath" not in opts
        assert "ControlPersist" not in opts
        assert opts["ForwardX11"] == "yes"
        assert opts["ForwardX11Trusted"] == "yes"

    def test_tunnels_strip_control_trio_and_add_exit_on_failure(self):
        opts = _base_ctrl_opts()
        session = _session(
            port_forwardings=[{"local_port": 8080, "remote_port": 80}]
        )
        apply_x11_and_tunnel_options(opts, session, command_type="ssh")
        assert "ControlMaster" not in opts
        assert opts["ExitOnForwardFailure"] == "yes"

    def test_both_x11_and_tunnels_combine(self):
        opts = _base_ctrl_opts()
        session = _session(
            x11_forwarding=True,
            port_forwardings=[{"local_port": 8080, "remote_port": 80}],
        )
        apply_x11_and_tunnel_options(opts, session, command_type="ssh")
        assert "ControlMaster" not in opts
        assert opts["ForwardX11"] == "yes"
        assert opts["ExitOnForwardFailure"] == "yes"


# ── needs_x11_flag ─────────────────────────────────────────


class TestNeedsX11Flag:
    def test_ssh_with_x11_true(self):
        assert (
            needs_x11_flag(_session(x11_forwarding=True), "ssh") is True
        )

    def test_ssh_without_x11_false(self):
        assert needs_x11_flag(_session(), "ssh") is False

    def test_sftp_never_needs_x11(self):
        assert (
            needs_x11_flag(_session(x11_forwarding=True), "sftp") is False
        )


# ── spawn-mixin delegation ─────────────────────────────────


class TestDelegation:
    def test_mixin_still_exposes_delegators(self):
        from ashyterm.terminal.ssh_spawn_mixin import SSHSpawnMixin

        for name in (
            "_get_strict_host_key_checking",
            "_build_ssh_test_options",
            "_get_base_ssh_options",
            "_apply_x11_and_tunnel_options",
            "_add_x11_flag_to_command",
        ):
            assert callable(getattr(SSHSpawnMixin, name))
