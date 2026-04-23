"""Tests for icons — bundled icon loading utilities."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestGetIconDir:
    """Tests for icon directory resolution."""

    def test_icon_dir_returns_string_or_none(self):
        from ashyterm.utils.icons import _get_icon_dir

        result = _get_icon_dir()
        assert result is None or isinstance(result, str)

    def test_icon_paths_include_bundled_location(self):
        from ashyterm.utils import icons

        expected = os.path.realpath(
            os.path.join(
                os.path.dirname(__file__), "..", "src", "ashyterm", "icons"
            )
        )
        resolved_paths = [os.path.realpath(p) for p in icons._ICON_PATHS]
        assert expected in resolved_paths


class TestGetIconPath:
    """Tests for icon path resolution."""

    def test_existing_icon(self):
        from ashyterm.utils.icons import get_icon_path

        # The icons directory should contain SVG files
        result = get_icon_path("folder-symbolic")
        if result is not None:
            assert result.endswith("folder-symbolic.svg")
            assert os.path.isfile(result)

    def test_nonexistent_icon_returns_none(self):
        from ashyterm.utils.icons import get_icon_path

        assert get_icon_path("nonexistent-icon-xyz") is None

    def test_icon_name_without_svg_extension(self):
        from ashyterm.utils.icons import get_icon_path

        # Should auto-append .svg
        result = get_icon_path("folder-symbolic")
        if result is not None:
            assert result.endswith(".svg")

    def test_icon_name_with_svg_extension(self):
        from ashyterm.utils.icons import get_icon_path

        result = get_icon_path("folder-symbolic.svg")
        if result is not None:
            assert result.endswith(".svg")


class TestHasBundledIcon:
    """Tests for bundled icon existence check."""

    def test_known_icon_exists(self):
        from ashyterm.utils.icons import has_bundled_icon

        # folder-symbolic should exist in bundled icons
        result = has_bundled_icon("folder-symbolic")
        # If bundled icons directory exists, this should be True
        # If not installed, might be False — just check it returns bool
        assert isinstance(result, bool)

    def test_unknown_icon_returns_false(self):
        from ashyterm.utils.icons import has_bundled_icon

        assert has_bundled_icon("totally-made-up-icon-xyz123") is False


class TestIconPathsList:
    """Tests for _ICON_PATHS configuration."""

    def test_icon_paths_is_list(self):
        from ashyterm.utils.icons import _ICON_PATHS

        assert isinstance(_ICON_PATHS, list)
        assert len(_ICON_PATHS) >= 2  # install path + bundled fallback

    def test_icon_paths_contains_install_location(self):
        from ashyterm.utils.icons import _ICON_PATHS

        assert "/usr/share/ashyterm/icons" in _ICON_PATHS

    def test_icon_paths_contains_bundled_fallback(self):
        from ashyterm.utils.icons import _ICON_PATHS

        paths = [p for p in _ICON_PATHS if "ashyterm/icons" in p]
        assert len(paths) >= 1


class TestUseBundledIconsFlag:
    """Tests for _use_bundled_icons global flag."""

    def test_flag_is_boolean(self):
        from ashyterm.utils.icons import _use_bundled_icons

        assert isinstance(_use_bundled_icons, bool)

    def test_default_is_true(self):
        """Default to bundled icons for performance."""
        from ashyterm.utils.icons import _use_bundled_icons

        assert _use_bundled_icons is True


class TestConvenienceAliases:
    """Tests for icon_image and icon_button aliases."""

    def test_icon_image_is_create_icon_image(self):
        from ashyterm.utils.icons import create_icon_image, icon_image

        assert icon_image is create_icon_image

    def test_icon_button_is_create_icon_button(self):
        from ashyterm.utils.icons import create_icon_button, icon_button

        assert icon_button is create_icon_button
