"""Tests for SettingsValidator (pure validation of the settings dict)."""

import pytest

from ashyterm.settings.validator import SettingsValidator


@pytest.fixture
def v() -> SettingsValidator:
    return SettingsValidator()


# ── validate_color_scheme ────────────────────────────────────


class TestValidateColorScheme:
    def test_accepts_valid_index(self, v):
        assert v.validate_color_scheme(0, num_schemes=5) is True
        assert v.validate_color_scheme(4, num_schemes=5) is True

    def test_rejects_out_of_range(self, v):
        assert v.validate_color_scheme(-1, num_schemes=5) is False
        assert v.validate_color_scheme(5, num_schemes=5) is False

    def test_rejects_non_int(self, v):
        assert v.validate_color_scheme("0", num_schemes=5) is False
        assert v.validate_color_scheme(0.0, num_schemes=5) is False
        assert v.validate_color_scheme(None, num_schemes=5) is False

    def test_zero_schemes_never_valid(self, v):
        # Caller promises at least one scheme exists; but check the edge.
        assert v.validate_color_scheme(0, num_schemes=0) is False


# ── validate_transparency ────────────────────────────────────


class TestValidateTransparency:
    @pytest.mark.parametrize("value", [0, 25, 50, 100, 0.5, 99.9])
    def test_accepts_in_range(self, v, value):
        assert v.validate_transparency(value) is True

    @pytest.mark.parametrize("value", [-1, 101, -0.1])
    def test_rejects_out_of_range(self, v, value):
        assert v.validate_transparency(value) is False

    @pytest.mark.parametrize("value", ["50", None, True])
    def test_rejects_non_numeric(self, v, value):
        # ``True`` is technically an int; but the validator accepts it
        # (bool is a subclass of int). That's pre-existing behavior.
        if isinstance(value, bool):
            assert v.validate_transparency(value) is True
        else:
            assert v.validate_transparency(value) is False


# ── validate_font ────────────────────────────────────────────


class TestValidateFont:
    def test_accepts_named_monospace(self, v):
        assert v.validate_font("Monospace 10") is True
        assert v.validate_font("Sans 12") is True

    def test_rejects_empty_or_whitespace(self, v):
        assert v.validate_font("") is False
        assert v.validate_font("   ") is False

    def test_rejects_non_string(self, v):
        assert v.validate_font(12) is False
        assert v.validate_font(None) is False


# ── validate_shortcut ────────────────────────────────────────


class TestValidateShortcut:
    def test_empty_string_is_valid(self, v):
        # Used to disable a shortcut — must round-trip cleanly.
        assert v.validate_shortcut("") is True

    def test_standard_accelerators(self, v):
        assert v.validate_shortcut("<Control>c") is True
        assert v.validate_shortcut("<Control><Shift>v") is True
        assert v.validate_shortcut("F1") is True

    def test_rejects_garbage(self, v):
        assert v.validate_shortcut("garbage!!!") is False
        assert v.validate_shortcut("<Missing") is False

    def test_rejects_non_string(self, v):
        assert v.validate_shortcut(None) is False
        assert v.validate_shortcut(42) is False


# ── validate_shortcuts (dict) ────────────────────────────────


class TestValidateShortcuts:
    def test_all_valid_returns_empty(self, v):
        out = v.validate_shortcuts({"copy": "<Control>c", "paste": "<Control>v"})
        assert out == []

    def test_duplicate_bindings_flagged(self, v):
        # Two actions mapped to the same accelerator.
        out = v.validate_shortcuts(
            {"copy": "<Control>c", "paste": "<Control>c"}
        )
        assert any("Duplicate" in e for e in out)

    def test_empty_bindings_not_counted_as_duplicates(self, v):
        out = v.validate_shortcuts({"a": "", "b": ""})
        assert out == []

    def test_invalid_accelerator_flagged(self, v):
        out = v.validate_shortcuts({"copy": "garbage!!!"})
        assert any("Invalid shortcut" in e for e in out)

    def test_non_dict_rejected(self, v):
        out = v.validate_shortcuts(["not", "a", "dict"])  # type: ignore[arg-type]
        assert out == ["Shortcuts must be a dictionary"]

    def test_non_string_action_name_flagged(self, v):
        # Non-string key → logged + skipped validation for its value.
        out = v.validate_shortcuts({42: "<Control>c"})  # type: ignore[dict-item]
        assert any("Invalid action name" in e for e in out)


# ── validate_settings_structure ──────────────────────────────


def _minimal_valid_settings() -> dict:
    return {
        "color_scheme": 0,
        "font": "Monospace 10",
        "shortcuts": {"copy": "<Control>c"},
    }


class TestValidateSettingsStructure:
    def test_minimum_required_passes(self, v):
        errors = v.validate_settings_structure(
            _minimal_valid_settings(), num_schemes=5
        )
        assert errors == []

    def test_missing_required_keys_flagged(self, v):
        errors = v.validate_settings_structure({}, num_schemes=5)
        messages = " ".join(errors)
        assert "color_scheme" in messages
        assert "font" in messages
        assert "shortcuts" in messages

    def test_invalid_color_scheme_reported(self, v):
        settings = _minimal_valid_settings()
        settings["color_scheme"] = 99
        errors = v.validate_settings_structure(settings, num_schemes=5)
        assert any("color_scheme" in e for e in errors)

    def test_invalid_transparency_reported(self, v):
        settings = _minimal_valid_settings()
        settings["transparency"] = 200
        errors = v.validate_settings_structure(settings, num_schemes=5)
        assert any("transparency" in e for e in errors)

    def test_invalid_font_reported(self, v):
        settings = _minimal_valid_settings()
        settings["font"] = ""  # empty → invalid
        errors = v.validate_settings_structure(settings, num_schemes=5)
        assert any("font" in e for e in errors)

    def test_boolean_type_enforced(self, v):
        settings = _minimal_valid_settings()
        settings["sidebar_visible"] = "yes"  # must be a bool
        errors = v.validate_settings_structure(settings, num_schemes=5)
        assert any("sidebar_visible" in e and "boolean" in e for e in errors)

    def test_osc52_boolean_type_enforced(self, v):
        settings = _minimal_valid_settings()
        settings["osc52_clipboard_enabled"] = "false"

        errors = v.validate_settings_structure(settings, num_schemes=5)

        assert any(
            "osc52_clipboard_enabled" in error and "boolean" in error
            for error in errors
        )

    def test_boolean_missing_key_is_fine(self, v):
        # A missing optional bool shouldn't be flagged — only present
        # keys with wrong types are rejected.
        settings = _minimal_valid_settings()
        errors = v.validate_settings_structure(settings, num_schemes=5)
        assert errors == []

    def test_accepts_boolean_true_false(self, v):
        settings = _minimal_valid_settings()
        settings.update(
            {
                "sidebar_visible": True,
                "scroll_on_keystroke": False,
                "ai_assistant_enabled": True,
            }
        )
        assert v.validate_settings_structure(settings, num_schemes=5) == []


# ── manager still exposes SettingsValidator ──────────────────


class TestManagerIntegration:
    def test_manager_imports_same_validator_class(self):
        from ashyterm.settings.manager import SettingsValidator as from_manager
        assert from_manager is SettingsValidator
