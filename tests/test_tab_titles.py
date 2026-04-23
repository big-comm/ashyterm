"""Tests for tab_titles (pure tab title composition helpers)."""

import pytest

from ashyterm.terminal.tab_titles import (
    append_terminal_count,
    build_display_title,
)


# ── build_display_title ─────────────────────────────────────


class TestBuildDisplayTitle:
    def test_local_tab_shows_incoming_title_verbatim(self):
        out = build_display_title(
            base_title="My Local", new_title="~/projects/foo", is_local=True
        )
        assert out == "~/projects/foo"

    def test_local_ignores_base_title_mismatch(self):
        # Local titles are cwd-driven; the base should never leak.
        out = build_display_title(
            base_title="irrelevant", new_title="/etc", is_local=True
        )
        assert out == "/etc"

    def test_remote_equal_to_base_stays_base(self):
        # Incoming title equal to session name: just the base, no colon.
        out = build_display_title(
            base_title="prod", new_title="prod", is_local=False
        )
        assert out == "prod"

    def test_remote_with_already_prefixed_title_is_passthrough(self):
        # If the upstream already formatted it, don't double-prefix.
        out = build_display_title(
            base_title="prod", new_title="prod:/var/log", is_local=False
        )
        assert out == "prod:/var/log"

    def test_remote_with_unrelated_title_gets_prefixed(self):
        out = build_display_title(
            base_title="prod", new_title="/var/log", is_local=False
        )
        assert out == "prod: /var/log"

    def test_remote_prefix_check_respects_colon_boundary(self):
        # "production" starts with "prod" but not "prod:" — must prefix.
        out = build_display_title(
            base_title="prod", new_title="production", is_local=False
        )
        assert out == "prod: production"

    def test_empty_new_title_is_still_prefixed_on_remote(self):
        out = build_display_title(
            base_title="prod", new_title="", is_local=False
        )
        assert out == "prod: "

    def test_empty_base_title_on_remote(self):
        # Unusual but tolerated — the prefix becomes ": something".
        out = build_display_title(
            base_title="", new_title="/tmp", is_local=False
        )
        assert out == ": /tmp"


# ── append_terminal_count ──────────────────────────────────


class TestAppendTerminalCount:
    def test_single_terminal_is_passthrough(self):
        assert append_terminal_count("prod: /var", 1) == "prod: /var"

    def test_zero_terminals_also_passthrough(self):
        # Edge case: tab being torn down; don't show "(0)".
        assert append_terminal_count("prod", 0) == "prod"

    def test_two_terminals_shown_as_suffix(self):
        assert append_terminal_count("prod: /var", 2) == "prod: /var (2)"

    def test_many_terminals(self):
        assert append_terminal_count("dev", 7) == "dev (7)"


# ── tabs.py delegation ─────────────────────────────────────


class TestTabsDelegation:
    def test_tabs_delegators_exist(self):
        from ashyterm.terminal.tabs import TabManager

        for name in ("_build_display_title", "_append_terminal_count"):
            assert callable(getattr(TabManager, name))
