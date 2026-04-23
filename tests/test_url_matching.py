"""URL detection tables.

Two contracts pinned here:

* ``ashyterm.helpers.is_valid_url`` — classifies a string as URL/email
  or not. Used by the context-menu hyperlink detection.
* ``_PATTERN_URL`` in ``syntax_utils.py`` — the regex that wraps http(s)
  URLs in a colored span inside bash markup.

Both surfaces have subtly different behaviours (scheme set, email
detection, trailing punctuation handling). We tabulate the expected
classification so the Rust port can't silently drift.
"""

from __future__ import annotations

import re

import pytest


# ── is_valid_url ───────────────────────────────────────────


IS_VALID_URL_CASES = [
    # (input, expected_valid)
    # http/https
    ("https://example.com", True),
    ("http://example.com/path", True),
    ("https://example.com:8080/api", True),
    ("https://sub.example.com/with?query=1&x=2", True),
    ("https://user:pass@example.com/", True),
    ("https://192.168.1.1:8080", True),
    ("https://[::1]:8080/api", True),  # IPv6 literal
    # Other schemes
    ("ftp://ftp.example.com/file.zip", True),
    # Quirk: mailto: is listed in the docstring as supported, but the
    # urlparse-based check requires a netloc and mailto: has none —
    # current behaviour is False. Pin it so a future change is a
    # conscious breaking change, not a drift.
    ("mailto:admin@example.com", False),
    # Bare emails — custom path in is_valid_url
    ("alice@example.com", True),
    ("first.last@subdomain.example.co.uk", True),
    # Rejected as URL
    ("", False),
    ("   ", False),
    ("not a url", False),
    ("just text", False),
    # Missing TLD on bare "email"
    ("alice@host", False),
    # scheme-prefixed but also has @ — caught by startswith guard, then urlparse
    ("http://alice@example.com", True),
    # Strict: scheme without netloc is rejected
    ("https://", False),
    ("file:/no-authority", False),  # urlparse sees no netloc
]


class TestIsValidURL:
    @pytest.mark.parametrize("text, valid", IS_VALID_URL_CASES)
    def test_table(self, text: str, valid: bool):
        from ashyterm.helpers import is_valid_url
        assert is_valid_url(text) is valid, f"{text!r}: want {valid}"

    def test_leading_trailing_whitespace_stripped(self):
        from ashyterm.helpers import is_valid_url
        assert is_valid_url("  https://example.com  ") is True

    def test_empty_string(self):
        from ashyterm.helpers import is_valid_url
        assert is_valid_url("") is False

    def test_none_safe(self):
        from ashyterm.helpers import is_valid_url
        # None is explicitly not accepted; function returns False on empty.
        assert is_valid_url(None or "") is False


# ── syntax_utils _PATTERN_URL regex ─────────────────────────


class TestSyntaxUtilsURLPattern:
    """The regex is intentionally simple: ``(https?://[^\\s]+)``.

    That means:
    * Only http/https are highlighted (ftp/mailto/etc. are NOT).
    * The match extends to the next whitespace — trailing punctuation
      sticks to the URL.
    """

    @pytest.fixture
    def pattern(self):
        from ashyterm.utils.syntax_utils import _PATTERN_URL
        return _PATTERN_URL

    @pytest.mark.parametrize("text, expected", [
        ("https://example.com", ["https://example.com"]),
        ("visit https://example.com today", ["https://example.com"]),
        ("http://a.io and https://b.io", ["http://a.io", "https://b.io"]),
        # Trailing punctuation is captured by the regex (known limitation).
        ("See: https://example.com.", ["https://example.com."]),
        ("(https://example.com)", ["https://example.com)"]),
        # Non-http schemes are NOT matched by this pattern.
        ("ftp://ftp.example.com/", []),
        ("mailto:a@b.com", []),
        # Empty / no URL
        ("", []),
        ("plain text", []),
        # IPv6 URL with brackets
        ("http://[::1]:8080/api", ["http://[::1]:8080/api"]),
        # Query + fragment preserved
        ("https://a.b/x?q=1&y=2#frag", ["https://a.b/x?q=1&y=2#frag"]),
    ])
    def test_matches(self, pattern: re.Pattern, text: str, expected: list):
        assert pattern.findall(text) == expected


# ── Cross-contract sanity: is_valid_url and pattern agree on core cases ──


class TestIsValidUrlVsPattern:
    """For core http(s) URLs, both surfaces must agree that the text is
    a URL. Catches drift where one side stops recognizing what the other
    still highlights (or vice versa)."""

    CORE_URLS = [
        "https://example.com",
        "http://example.com/path",
        "https://sub.example.com:8080/api?q=1",
    ]

    @pytest.mark.parametrize("url", CORE_URLS)
    def test_both_accept_core_urls(self, url: str):
        from ashyterm.helpers import is_valid_url
        from ashyterm.utils.syntax_utils import _PATTERN_URL

        assert is_valid_url(url) is True
        assert _PATTERN_URL.search(url) is not None

    def test_neither_accepts_plain_text(self):
        from ashyterm.helpers import is_valid_url
        from ashyterm.utils.syntax_utils import _PATTERN_URL

        text = "not a url at all"
        assert is_valid_url(text) is False
        assert _PATTERN_URL.search(text) is None


# ── ANSI escape stripping regex (terminal/url_handler) ──────


class TestAnsiEscapePattern:
    """``_ANSI_ESCAPE_PATTERN`` removes escape codes from text before URL
    detection in the terminal. Tested here because it's the same
    category (port-sensitive regex on untrusted stream bytes)."""

    @pytest.fixture
    def pattern(self):
        from ashyterm.terminal.url_handler import _ANSI_ESCAPE_PATTERN
        return _ANSI_ESCAPE_PATTERN

    @pytest.mark.parametrize("raw, cleaned", [
        # SGR (colors)
        ("\x1b[31mred\x1b[0m", "red"),
        # Cursor movement
        ("\x1b[2Aup\x1b[3B", "up"),
        # Clear screen
        ("\x1b[2J", ""),
        # OSC 8 hyperlink open/close (ends with \x07)
        ("\x1b]8;;https://example.com\x07text\x1b]8;;\x07", "text"),
        # No escapes
        ("plain text", "plain text"),
        # Empty
        ("", ""),
    ])
    def test_strips_ansi(self, pattern: re.Pattern, raw: str, cleaned: str):
        result = pattern.sub("", raw)
        assert result == cleaned
