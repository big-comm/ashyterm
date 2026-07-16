"""Settings integration contracts for terminal scrolling."""

from unittest.mock import MagicMock

import pytest

from ashyterm.settings.manager import SettingsManager
from ashyterm.settings.scrolling import normalize_scroll_mode
from ashyterm.utils.exceptions import ConfigValidationError


@pytest.mark.parametrize("mode", ["automatic", "custom", "native"])
def test_normalize_scroll_mode_preserves_supported_values(mode):
    assert normalize_scroll_mode(mode) == mode


@pytest.mark.parametrize("value", [None, "system", "", 1])
def test_normalize_scroll_mode_falls_back_to_automatic(value):
    assert normalize_scroll_mode(value) == "automatic"


def test_settings_manager_validates_scroll_mode_on_set(tmp_path):
    settings = SettingsManager(settings_file=tmp_path / "settings.json")
    settings.set("terminal_scroll_mode", "native", save_immediately=False)
    assert settings.get("terminal_scroll_mode") == "native"

    with pytest.raises(ConfigValidationError):
        settings.set("terminal_scroll_mode", "broken", save_immediately=False)


def test_zero_scrollback_is_applied_as_vte_unlimited(tmp_path):
    settings = SettingsManager(settings_file=tmp_path / "settings.json")
    settings.set("scrollback_lines", 0, save_immediately=False)
    terminal = MagicMock()

    settings._apply_scrolling(terminal)

    terminal.set_scrollback_lines.assert_called_once_with(-1)
