"""Tests for terminal_config (pure config helpers for TerminalManager)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.terminal_config import (
    compute_highlighting_config,
    get_ssh_highlight_config,
    resolve_working_directory,
)


def _settings(**overrides):
    """Fake settings_manager with ``get(key, default)``."""
    sm = MagicMock()
    defaults = {
        "cat_colorization_enabled": True,
        "shell_input_highlighting_enabled": False,
    }
    defaults.update(overrides)
    sm.get = MagicMock(side_effect=lambda k, d=None: defaults.get(k, d))
    return sm


def _hl_manager(*, local=True, ssh=True):
    """Fake highlight_manager exposing enabled_for_local/ssh flags."""
    return SimpleNamespace(enabled_for_local=local, enabled_for_ssh=ssh)


# ── resolve_working_directory ───────────────────────────────


class TestResolveWorkingDirectory:
    def test_empty_or_none_returns_none(self):
        assert resolve_working_directory(None) is None
        assert resolve_working_directory("") is None

    def test_absolute_existing_dir_is_accepted(self, tmp_path):
        assert resolve_working_directory(str(tmp_path)) == str(tmp_path)

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        # Point $HOME at tmp_path so ``~`` resolves somewhere valid.
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_working_directory("~") == str(tmp_path)

    def test_env_var_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_DATA", str(tmp_path))
        assert resolve_working_directory("$MY_DATA") == str(tmp_path)

    def test_non_existent_path_returns_none(self, tmp_path):
        bogus = tmp_path / "does" / "not" / "exist"
        assert resolve_working_directory(str(bogus)) is None

    def test_file_instead_of_dir_returns_none(self, tmp_path):
        file_path = tmp_path / "a.txt"
        file_path.write_text("hi")
        assert resolve_working_directory(str(file_path)) is None

    def test_relative_path_resolves_to_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sub = tmp_path / "sub"
        sub.mkdir()
        out = resolve_working_directory("sub")
        assert out == str(sub)


# ── compute_highlighting_config ─────────────────────────────


class TestComputeHighlightingConfig:
    def test_everything_off_when_global_disabled_no_session(self):
        hl = _hl_manager(local=False)
        should, cfg = compute_highlighting_config(
            session=None,
            is_local=True,
            highlight_manager=hl,
            settings_manager=_settings(),
        )
        assert should is False
        assert cfg == {
            "output_highlighting": False,
            "cat_colorization": False,
            "shell_input_highlighting": False,
        }

    def test_local_enabled_pulls_cat_but_not_shell_input(self):
        should, cfg = compute_highlighting_config(
            session=None,
            is_local=True,
            highlight_manager=_hl_manager(local=True),
            settings_manager=_settings(),
        )
        assert should is True
        assert cfg["output_highlighting"] is True
        assert cfg["cat_colorization"] is True
        # Default shell_input is off globally.
        assert cfg["shell_input_highlighting"] is False

    def test_session_override_forces_output_on(self):
        hl = _hl_manager(local=False)  # global off
        session = SessionItem(
            name="s", session_type="local", output_highlighting=True
        )
        should, cfg = compute_highlighting_config(
            session=session,
            is_local=True,
            highlight_manager=hl,
            settings_manager=_settings(),
        )
        assert cfg["output_highlighting"] is True
        assert should is True

    def test_session_override_forces_output_off(self):
        session = SessionItem(
            name="s", session_type="local", output_highlighting=False
        )
        _, cfg = compute_highlighting_config(
            session=session,
            is_local=True,
            highlight_manager=_hl_manager(local=True),
            settings_manager=_settings(),
        )
        # Output off ⇒ cat and shell are forcibly off too.
        assert cfg["output_highlighting"] is False
        assert cfg["cat_colorization"] is False
        assert cfg["shell_input_highlighting"] is False

    def test_cat_session_override_gated_by_output(self):
        # Even when the session says cat=True, if output is off it
        # stays off — the proxy can't color output it isn't handling.
        session = SessionItem(
            name="s",
            session_type="local",
            output_highlighting=False,
            cat_colorization=True,
        )
        _, cfg = compute_highlighting_config(
            session=session,
            is_local=True,
            highlight_manager=_hl_manager(local=True),
            settings_manager=_settings(),
        )
        assert cfg["cat_colorization"] is False

    def test_shell_input_session_override_is_respected(self):
        session = SessionItem(
            name="s",
            session_type="local",
            shell_input_highlighting=True,
        )
        _, cfg = compute_highlighting_config(
            session=session,
            is_local=True,
            highlight_manager=_hl_manager(local=True),
            settings_manager=_settings(),  # shell_input global = False
        )
        # Global says off, session says on ⇒ on (gated by output).
        assert cfg["shell_input_highlighting"] is True

    def test_ssh_variant_uses_enabled_for_ssh(self):
        hl = _hl_manager(local=False, ssh=True)
        should, cfg = compute_highlighting_config(
            session=None,
            is_local=False,
            highlight_manager=hl,
            settings_manager=_settings(),
        )
        # Picked ssh branch ⇒ output on.
        assert cfg["output_highlighting"] is True
        assert should is True


# ── get_ssh_highlight_config ────────────────────────────────


class TestSshHighlightConfig:
    def test_emits_legacy_key_names(self):
        session = SessionItem(name="s", session_type="ssh")
        cfg = get_ssh_highlight_config(
            session=session,
            highlight_manager=_hl_manager(ssh=True),
            settings_manager=_settings(),
        )
        # The SSH spawner reads these specific keys.
        assert set(cfg) == {
            "output_enabled",
            "cat_enabled",
            "shell_input_enabled",
            "should_highlight",
        }

    def test_respects_session_output_override(self):
        session = SessionItem(
            name="s", session_type="ssh", output_highlighting=False
        )
        cfg = get_ssh_highlight_config(
            session=session,
            highlight_manager=_hl_manager(ssh=True),
            settings_manager=_settings(),
        )
        assert cfg["output_enabled"] is False
        assert cfg["cat_enabled"] is False
        assert cfg["should_highlight"] is False

    def test_shell_input_session_override_applies(self):
        session = SessionItem(
            name="s", session_type="ssh", shell_input_highlighting=True
        )
        cfg = get_ssh_highlight_config(
            session=session,
            highlight_manager=_hl_manager(ssh=True),
            settings_manager=_settings(),
        )
        assert cfg["shell_input_enabled"] is True
        assert cfg["should_highlight"] is True


# ── manager delegation ──────────────────────────────────────


class TestManagerDelegation:
    def test_manager_delegators_exist(self):
        from ashyterm.terminal.manager import TerminalManager

        for name in (
            "_resolve_working_directory",
            "_compute_highlighting_config",
            "_get_ssh_highlight_config",
        ):
            assert callable(getattr(TerminalManager, name))
