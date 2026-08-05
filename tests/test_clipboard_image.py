"""Tests for pasting clipboard images as file paths."""

import time
from unittest.mock import MagicMock

from ashyterm.terminal import clipboard_image
from ashyterm.terminal.clipboard_image import (
    clipboard_has_image,
    save_clipboard_image_async,
)


def _clipboard(mime_types=(), gtypes=()) -> MagicMock:
    formats = MagicMock()
    formats.contain_mime_type.side_effect = lambda mime: mime in mime_types
    formats.get_gtypes.return_value = list(gtypes)
    clipboard = MagicMock()
    clipboard.get_formats.return_value = formats
    return clipboard


def test_image_only_clipboard_is_detected() -> None:
    assert clipboard_has_image(_clipboard(mime_types=("image/png",)))


def test_text_wins_over_image() -> None:
    clipboard = _clipboard(mime_types=("image/png", "text/plain;charset=utf-8"))
    assert not clipboard_has_image(clipboard)


def test_empty_clipboard_has_no_image() -> None:
    assert not clipboard_has_image(_clipboard())


def test_missing_formats_are_tolerated() -> None:
    clipboard = MagicMock()
    clipboard.get_formats.return_value = None
    assert not clipboard_has_image(clipboard)


def test_texture_gtype_counts_as_image() -> None:
    from gi.repository import Gdk

    assert clipboard_has_image(_clipboard(gtypes=(Gdk.Texture.__gtype__,)))


def test_saved_path_has_no_shell_metacharacters(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        clipboard_image, "get_clipboard_image_directory", lambda: tmp_path
    )
    path = clipboard_image._build_image_path()
    assert not any(character in path.name for character in " '\"$\\`")
    assert path.suffix == ".png"


def test_repeated_saves_do_not_collide(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        clipboard_image, "get_clipboard_image_directory", lambda: tmp_path
    )
    first = clipboard_image._build_image_path()
    first.touch()
    assert clipboard_image._build_image_path() != first


def test_stale_images_are_removed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        clipboard_image, "get_clipboard_image_directory", lambda: tmp_path
    )
    stale = tmp_path / "clipboard-20200101-000000.png"
    stale.touch()
    old = time.time() - clipboard_image._MAX_AGE_SECONDS - 60
    import os

    os.utime(stale, (old, old))
    fresh = tmp_path / "clipboard-20990101-000000.png"
    fresh.touch()

    clipboard_image._build_image_path()

    assert not stale.exists()
    assert fresh.exists()


def _press_ctrl_v(clipboard: MagicMock, state=None) -> tuple:
    """Run the Ctrl+V handler with a stub manager; return (stopped, manager)."""
    import gi

    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk

    from ashyterm.terminal.manager import TerminalManager

    if state is None:
        state = Gdk.ModifierType.CONTROL_MASK
    terminal = MagicMock()
    terminal.get_clipboard.return_value = clipboard
    manager = MagicMock()
    stopped = TerminalManager._on_terminal_key_pressed_for_image_paste(
        manager, None, Gdk.KEY_v, 0, state, terminal
    )
    return stopped, manager


def test_ctrl_v_pastes_image_when_clipboard_has_one() -> None:
    stopped, manager = _press_ctrl_v(_clipboard(mime_types=("image/png",)))
    assert stopped
    manager._paste_clipboard_image.assert_called_once()


def test_ctrl_v_over_text_reaches_the_shell() -> None:
    stopped, manager = _press_ctrl_v(_clipboard(mime_types=("text/plain",)))
    assert not stopped
    manager._paste_clipboard_image.assert_not_called()


def test_ctrl_shift_v_is_left_to_the_paste_action() -> None:
    import gi

    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk

    stopped, manager = _press_ctrl_v(
        _clipboard(mime_types=("image/png",)),
        state=Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK,
    )
    assert not stopped
    manager._paste_clipboard_image.assert_not_called()


def test_plain_v_is_left_alone() -> None:
    import gi

    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk

    stopped, manager = _press_ctrl_v(
        _clipboard(mime_types=("image/png",)), state=Gdk.ModifierType(0)
    )
    assert not stopped
    manager._paste_clipboard_image.assert_not_called()


def test_save_reports_none_when_clipboard_read_fails() -> None:
    clipboard = MagicMock()
    clipboard.read_texture_async.side_effect = RuntimeError("no image")
    results = []

    save_clipboard_image_async(clipboard, results.append)

    assert results == [None]


def test_save_writes_png_and_reports_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        clipboard_image, "get_clipboard_image_directory", lambda: tmp_path
    )
    texture = MagicMock()
    clipboard = MagicMock()
    clipboard.read_texture_finish.return_value = texture
    clipboard.read_texture_async.side_effect = (
        lambda _cancellable, callback: callback(clipboard, MagicMock())
    )
    results = []

    save_clipboard_image_async(clipboard, results.append)

    assert results and results[0] is not None
    texture.save_to_png.assert_called_once_with(str(results[0]))
