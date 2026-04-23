"""Tests for ai_os_context (OS + locale detection for AI system prompt)."""


import pytest

from ashyterm.terminal import ai_os_context
from ashyterm.terminal.ai_os_context import (
    build_system_prompt,
    detect_language,
    detect_os_context,
    language_from_locale,
    parse_lsb_release,
    parse_os_release,
    sanitize_os_value,
)


# ── sanitize_os_value ────────────────────────────────────────


class TestSanitize:
    def test_alphanumeric_and_space_pass_through(self):
        assert sanitize_os_value("Ubuntu 22.04") == "Ubuntu 22.04"

    def test_drops_prompt_injection_characters(self):
        # Angle brackets are dropped; (), / and basic word chars survive.
        assert (
            sanitize_os_value("Arch <script>alert()</script>")
            == "Arch scriptalert()/script"
        )

    def test_strips_newlines_and_tabs(self):
        assert sanitize_os_value("Arch\nLinux\trolling") == "ArchLinuxrolling"

    def test_truncates_long_values(self):
        out = sanitize_os_value("X" * 500)
        assert len(out) == 100
        assert out == "X" * 100

    def test_empty_input(self):
        assert sanitize_os_value("") == ""

    def test_preserves_parentheses_dots_hyphens(self):
        assert (
            sanitize_os_value("BigLinux (Manjaro-based) 22.04.1-lts")
            == "BigLinux (Manjaro-based) 22.04.1-lts"
        )


# ── parse_os_release ─────────────────────────────────────────


class TestParseOsRelease:
    def test_pretty_name_and_id_like(self, tmp_path):
        f = tmp_path / "os-release"
        f.write_text(
            'PRETTY_NAME="BigLinux 22"\nID_LIKE="arch manjaro"\nOTHER=ignored\n'
        )
        name, base = parse_os_release(str(f))
        assert name == "BigLinux 22"
        assert base == "arch manjaro"

    def test_missing_fields_yield_defaults(self, tmp_path):
        f = tmp_path / "os-release"
        f.write_text("UNRELATED=x\n")
        name, base = parse_os_release(str(f))
        assert name == "Linux"
        assert base == ""

    def test_missing_file_returns_defaults(self, tmp_path):
        name, base = parse_os_release(str(tmp_path / "does-not-exist"))
        assert name == "Linux"
        assert base == ""

    def test_sanitizes_hostile_pretty_name(self, tmp_path):
        f = tmp_path / "os-release"
        f.write_text('PRETTY_NAME="<evil>Distro</evil>"\n')
        name, _ = parse_os_release(str(f))
        assert "<" not in name
        assert ">" not in name


# ── parse_lsb_release ────────────────────────────────────────


class TestParseLsbRelease:
    def test_description_is_picked(self, tmp_path):
        f = tmp_path / "lsb-release"
        f.write_text(
            'DISTRIB_ID=Ubuntu\nDISTRIB_DESCRIPTION="Ubuntu 22.04.3 LTS"\n'
        )
        assert parse_lsb_release(str(f)) == "Ubuntu 22.04.3 LTS"

    def test_missing_description_defaults_to_linux(self, tmp_path):
        f = tmp_path / "lsb-release"
        f.write_text("DISTRIB_ID=Ubuntu\n")
        assert parse_lsb_release(str(f)) == "Linux"

    def test_missing_file_defaults_to_linux(self, tmp_path):
        assert parse_lsb_release(str(tmp_path / "nope")) == "Linux"


# ── detect_os_context ────────────────────────────────────────


class TestDetectOsContext:
    def test_prefers_os_release(self, tmp_path, monkeypatch):
        path_os = tmp_path / "os-release"
        path_os.write_text('PRETTY_NAME="Arch Linux"\n')
        monkeypatch.setattr(
            ai_os_context.os.path,
            "exists",
            lambda p: p == "/etc/os-release",
        )
        monkeypatch.setattr(
            ai_os_context, "parse_os_release", lambda: ("Arch Linux", "")
        )

        assert detect_os_context() == "Arch Linux"

    def test_includes_id_like_when_present(self, monkeypatch):
        monkeypatch.setattr(
            ai_os_context.os.path, "exists", lambda p: p == "/etc/os-release"
        )
        monkeypatch.setattr(
            ai_os_context, "parse_os_release", lambda: ("BigLinux", "manjaro")
        )
        assert detect_os_context() == "BigLinux (based on manjaro)"

    def test_falls_back_to_lsb_release(self, monkeypatch):
        monkeypatch.setattr(
            ai_os_context.os.path, "exists", lambda p: p == "/etc/lsb-release"
        )
        monkeypatch.setattr(ai_os_context, "parse_lsb_release", lambda: "Mint")
        assert detect_os_context() == "Mint"

    def test_final_fallback_is_linux(self, monkeypatch):
        monkeypatch.setattr(ai_os_context.os.path, "exists", lambda p: False)
        assert detect_os_context() == "Linux"


# ── language_from_locale / detect_language ───────────────────


class TestLanguage:
    @pytest.mark.parametrize(
        "locale_code,expected",
        [
            ("pt_BR", "Portuguese"),
            ("en_US", "English"),
            ("de_DE", "German"),
            ("JA_JP", "Japanese"),  # case-insensitive prefix
        ],
    )
    def test_known_prefixes(self, locale_code, expected):
        assert language_from_locale(locale_code) == expected

    def test_unknown_locale_falls_back_to_english(self):
        assert language_from_locale("xx_XX") == "English"
        assert language_from_locale("") == "English"
        assert language_from_locale(None) == "English"

    def test_detect_language_swallows_locale_errors(self, monkeypatch):
        def boom():
            raise RuntimeError("locale unavailable")

        monkeypatch.setattr(ai_os_context.locale, "getdefaultlocale", boom)
        assert detect_language() == "English"


# ── build_system_prompt ──────────────────────────────────────


class TestBuildSystemPrompt:
    def test_substitutes_language_and_os(self, monkeypatch):
        monkeypatch.setattr(ai_os_context, "detect_language", lambda: "Portuguese")
        monkeypatch.setattr(ai_os_context, "detect_os_context", lambda: "BigLinux")

        out = build_system_prompt("hello {language} on {os_context}!")
        assert out == "hello Portuguese on BigLinux!"
