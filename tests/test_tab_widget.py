"""Tests for tab_widget (tab button factory + color helpers)."""

import pytest

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.tab_widget import (
    _icon_name_for_session,
    contrasting_text_for_rgba,
    generate_unique_sftp_name,
)


# ── contrasting_text_for_rgba ──────────────────────────────


class TestContrastingTextForRgba:
    def test_dark_background_yields_white(self):
        # black → white text
        assert contrasting_text_for_rgba("rgb(0,0,0)") == "#FFFFFF"
        assert contrasting_text_for_rgba("rgb(30,30,30)") == "#FFFFFF"

    def test_light_background_yields_black(self):
        assert contrasting_text_for_rgba("rgb(255,255,255)") == "#000000"
        assert contrasting_text_for_rgba("rgb(240,240,200)") == "#000000"

    def test_accepts_rgba_with_alpha(self):
        # Alpha component is ignored.
        assert contrasting_text_for_rgba("rgba(0, 0, 0, 0.5)") == "#FFFFFF"
        assert contrasting_text_for_rgba("rgba(255,255,255,0.3)") == "#000000"

    def test_flexible_whitespace(self):
        # Commas may be followed by zero or more spaces.
        assert contrasting_text_for_rgba("rgb(0,  0,  0)") == "#FFFFFF"

    def test_empty_string_falls_back_to_black(self):
        assert contrasting_text_for_rgba("") == "#000000"

    def test_unparseable_falls_back_to_black(self):
        assert contrasting_text_for_rgba("magenta") == "#000000"
        assert contrasting_text_for_rgba("#ff0000") == "#000000"  # hex not supported

    def test_colors_above_and_below_luminance_threshold(self):
        # Red (r=255, g=0, b=0) ⇒ luminance ≈ 0.213 → below 0.5 → white text.
        assert contrasting_text_for_rgba("rgb(255,0,0)") == "#FFFFFF"
        # Yellow (r=255, g=255, b=0) ⇒ luminance ≈ 0.928 → black text.
        assert contrasting_text_for_rgba("rgb(255,255,0)") == "#000000"

    def test_green_maps_to_black_text(self):
        # Green (r=0, g=255, b=0) ⇒ luminance ≈ 0.715 → black.
        assert contrasting_text_for_rgba("rgb(0,255,0)") == "#000000"

    def test_blue_maps_to_white_text(self):
        # Blue (r=0, g=0, b=255) ⇒ luminance ≈ 0.072 → white.
        assert contrasting_text_for_rgba("rgb(0,0,255)") == "#FFFFFF"


# ── _icon_name_for_session ──────────────────────────────────


class TestIconNameForSession:
    def test_local_session_has_no_icon(self):
        session = SessionItem(name="Local", session_type="local")
        assert _icon_name_for_session(session) is None

    def test_ssh_session_gets_server_icon(self):
        session = SessionItem(
            name="prod-box", session_type="ssh", host="host", user="user"
        )
        assert _icon_name_for_session(session) == "network-server-symbolic"

    def test_sftp_prefix_wins_over_ssh_type(self):
        # SFTP tabs carry a naming convention that's stronger than the
        # session_type — the tab should show the remote-folder icon.
        session = SessionItem(
            name="SFTP-prod", session_type="ssh", host="host", user="user"
        )
        assert _icon_name_for_session(session) == "folder-remote-symbolic"

    def test_local_session_with_sftp_prefix_still_gets_folder_icon(self):
        # Defensive: the SFTP naming convention is checked first.
        session = SessionItem(name="SFTP-something", session_type="local")
        assert _icon_name_for_session(session) == "folder-remote-symbolic"


# ── generate_unique_sftp_name ──────────────────────────────


class TestGenerateUniqueSftpName:
    def test_no_existing_uses_base(self):
        assert (
            generate_unique_sftp_name("prod", existing_names=[]) == "SFTP-prod"
        )

    def test_collision_appends_suffix(self):
        assert (
            generate_unique_sftp_name("prod", existing_names=["SFTP-prod"])
            == "SFTP-prod(1)"
        )

    def test_finds_lowest_free_suffix(self):
        existing = ["SFTP-prod", "SFTP-prod(1)", "SFTP-prod(3)"]
        # (2) is free even though (3) exists — we take the first gap.
        assert (
            generate_unique_sftp_name("prod", existing_names=existing)
            == "SFTP-prod(2)"
        )

    def test_unrelated_names_are_ignored(self):
        existing = ["SFTP-staging", "Local-1", "SFTP-prod-other"]
        # The filter matches prefix ``SFTP-prod`` exactly — ``SFTP-prod-other``
        # shares the prefix but isn't an exact base collision, so base wins.
        # Wait: ``SFTP-prod-other`` does start with ``SFTP-prod``. Adjust
        # this test to use a name that shares prefix but also matters.
        assert (
            generate_unique_sftp_name("prod", existing_names=existing)
            == "SFTP-prod"
        )

    def test_prefix_shadow_still_offers_base(self):
        # A name that shares the prefix but isn't equal to it doesn't
        # block the base name from being used.
        existing = ["SFTP-prod-beta"]
        assert (
            generate_unique_sftp_name("prod", existing_names=existing)
            == "SFTP-prod"
        )

    def test_special_chars_in_session_name(self):
        assert (
            generate_unique_sftp_name("my session", existing_names=[])
            == "SFTP-my session"
        )

    def test_empty_base_name(self):
        # Degenerate input but shouldn't crash.
        assert generate_unique_sftp_name("", existing_names=[]) == "SFTP-"


# ── tabs.py delegation ──────────────────────────────────────


class TestTabsDelegation:
    def test_tabs_delegators_exist(self):
        from ashyterm.terminal.tabs import TabManager

        for name in (
            "_get_contrasting_text_color",
            "_apply_tab_color",
            "_create_tab_widget",
            "_generate_unique_sftp_name",
        ):
            assert callable(getattr(TabManager, name))
