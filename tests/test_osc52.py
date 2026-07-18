"""Tests for bounded, write-only OSC 52 clipboard support."""

import base64
from unittest.mock import MagicMock

from ashyterm.terminal.osc52 import (
    OSC52_MAX_ENCODED_BYTES,
    OSC52StreamParser,
    consume_osc52_output,
)


def _sequence(text: str, selection: str = "c", terminator: bytes = b"\x07") -> bytes:
    payload = base64.b64encode(text.encode("utf-8"))
    return b"\x1b]52;" + selection.encode() + b";" + payload + terminator


def test_bel_sequence_copies_to_clipboard() -> None:
    terminal = MagicMock()
    output = consume_osc52_output(
        OSC52StreamParser(),
        _sequence("OSC 52 A copia funcionou!"),
        terminal,
        enabled=True,
    )

    assert output == b""
    terminal.get_clipboard.return_value.set.assert_called_once_with(
        "OSC 52 A copia funcionou!"
    )


def test_st_sequence_preserves_surrounding_output() -> None:
    terminal = MagicMock()
    output = consume_osc52_output(
        OSC52StreamParser(),
        b"before" + _sequence("copied", terminator=b"\x1b\\") + b"after",
        terminal,
        enabled=True,
    )

    assert output == b"beforeafter"
    terminal.get_clipboard.return_value.set.assert_called_once_with("copied")


def test_sequence_split_across_chunks_is_buffered() -> None:
    terminal = MagicMock()
    parser = OSC52StreamParser()
    sequence = _sequence("dividido")

    first = consume_osc52_output(parser, sequence[:4], terminal, enabled=True)
    second = consume_osc52_output(parser, sequence[4:], terminal, enabled=True)

    assert first == b""
    assert second == b""
    terminal.get_clipboard.return_value.set.assert_called_once_with("dividido")


def test_disabled_support_consumes_without_copying() -> None:
    terminal = MagicMock()
    output = consume_osc52_output(
        OSC52StreamParser(), _sequence("blocked"), terminal, enabled=False
    )

    assert output == b""
    terminal.get_clipboard.assert_not_called()


def test_setting_callback_is_not_read_without_a_request() -> None:
    setting = MagicMock(return_value=True)

    output = consume_osc52_output(
        OSC52StreamParser(), b"ordinary output", MagicMock(), enabled=setting
    )

    assert output == b"ordinary output"
    setting.assert_not_called()


def test_read_query_is_never_answered() -> None:
    terminal = MagicMock()
    query = b"\x1b]52;c;?\x07"

    output = consume_osc52_output(OSC52StreamParser(), query, terminal, enabled=True)

    assert output == b""
    terminal.get_clipboard.assert_not_called()


def test_primary_selection_uses_primary_clipboard() -> None:
    terminal = MagicMock()
    consume_osc52_output(
        OSC52StreamParser(), _sequence("primary", "p"), terminal, enabled=True
    )

    terminal.get_primary_clipboard.return_value.set.assert_called_once_with("primary")
    terminal.get_clipboard.assert_not_called()


def test_invalid_base64_is_ignored() -> None:
    terminal = MagicMock()
    sequence = b"\x1b]52;c;not-base64!\x07"

    output = consume_osc52_output(OSC52StreamParser(), sequence, terminal, enabled=True)

    assert output == b""
    terminal.get_clipboard.assert_not_called()


def test_oversized_incomplete_sequence_is_discarded_until_terminator() -> None:
    terminal = MagicMock()
    parser = OSC52StreamParser()
    first = b"\x1b]52;c;" + (b"A" * (OSC52_MAX_ENCODED_BYTES + 40))

    assert consume_osc52_output(parser, first, terminal, enabled=True) == b""
    assert consume_osc52_output(parser, b"tail\x07visible", terminal, enabled=True) == (
        b"visible"
    )
    terminal.get_clipboard.assert_not_called()


def test_highlight_proxy_routes_osc52_through_runtime_setting(monkeypatch) -> None:
    from ashyterm.terminal._highlighter_impl import HighlightedTerminalProxy

    settings = MagicMock()
    settings.get.return_value = True
    monkeypatch.setattr(
        "ashyterm.settings.manager.get_settings_manager", lambda: settings
    )
    proxy = HighlightedTerminalProxy.__new__(HighlightedTerminalProxy)
    proxy._osc52_parser = OSC52StreamParser()
    terminal = MagicMock()

    output = proxy._consume_osc52(_sequence("proxy path"), terminal)

    assert output == b""
    settings.get.assert_called_once_with("osc52_clipboard_enabled", True)
    terminal.get_clipboard.return_value.set.assert_called_once_with("proxy path")
