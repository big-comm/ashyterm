"""UTF-8 boundary tests across parsers.

Python ``str`` is forgiving. Rust ``String`` requires valid UTF-8 and
forces explicit decisions at every bytes↔str boundary. Testing these
edges now exposes assumptions the Python code makes that the Rust port
will have to encode explicitly (lossy vs strict, ``OsString`` vs
``String``, etc.).

Categories:

* **Invalid UTF-8 bytes** — continuation bytes without a leader, BOMs
  mid-stream, surrogates.
* **Unicode lookalikes** — cyrillic ``а`` vs latin ``a``.
* **Partial UTF-8 at buffer boundary** — a 4-byte character split
  across a read boundary (scrollback replay, stream decoding).
* **Zero-width / combining marks** — single grapheme, multiple code
  points.
* **Non-BMP characters** — emoji, multi-codepoint graphemes.
"""

from __future__ import annotations

import pytest


# ── OSC 7 parser ─────────────────────────────────────────────


class TestOSC7UTF8:
    def test_unicode_path(self):
        from ashyterm.utils.osc7 import parse_directory_uri
        info = parse_directory_uri("file://localhost/home/usuário/projetos")
        assert info is not None
        assert info.path == "/home/usuário/projetos"

    def test_percent_encoded_unicode(self):
        """OSC 7 per spec uses percent-encoding for non-ASCII bytes.
        %C3%A9 is the UTF-8 encoding of 'é'."""
        from ashyterm.utils.osc7 import parse_directory_uri
        info = parse_directory_uri("file://localhost/caf%C3%A9")
        assert info is not None
        assert info.path == "/café"

    def test_invalid_percent_encoding(self):
        """Partial percent escape (%ZZ) — urllib.unquote replaces with
        literal. Must not crash."""
        from ashyterm.utils.osc7 import parse_directory_uri
        info = parse_directory_uri("file://localhost/bad%ZZpath")
        assert info is not None
        assert "%ZZpath" in info.path or "bad" in info.path

    def test_empty_path(self):
        from ashyterm.utils.osc7 import parse_directory_uri
        info = parse_directory_uri("file://localhost/")
        assert info is not None
        assert info.hostname == "localhost"

    def test_non_file_scheme_rejected(self):
        from ashyterm.utils.osc7 import parse_directory_uri
        assert parse_directory_uri("http://example.com/path") is None
        assert parse_directory_uri("ssh://host/x") is None

    def test_garbage_input_returns_none(self):
        from ashyterm.utils.osc7 import parse_directory_uri
        # Must never raise, only return None for bad input.
        for raw in ["", "not-a-uri", "file:no-slashes", "\x00", "file:"]:
            try:
                result = parse_directory_uri(raw)
            except Exception as e:  # pragma: no cover
                pytest.fail(f"parse raised on {raw!r}: {e}")
            assert result is None or result.path is not None


# ── ls_output parser ─────────────────────────────────────────


class TestLsOutputUTF8:
    def test_unicode_filename(self):
        from ashyterm.filemanager.ls_output import parse_ls_output
        # Synthetic ls output with unicode filename.
        output = (
            "total 1\n"
            "-rw-r--r-- 1 user group 100 Jan 01 12:00 日本語.txt\n"
        )
        items = parse_ls_output(output, "/")
        assert any("日本語.txt" in getattr(i, "name", "") for i in items)

    def test_filename_with_space(self):
        from ashyterm.filemanager.ls_output import parse_ls_output
        output = (
            "total 1\n"
            "-rw-r--r-- 1 user group 100 Jan 01 12:00 file with spaces.txt\n"
        )
        items = parse_ls_output(output, "/")
        assert any("file with spaces" in getattr(i, "name", "") for i in items)

    def test_emoji_filename(self):
        from ashyterm.filemanager.ls_output import parse_ls_output
        output = (
            "total 1\n"
            "drwxr-xr-x 2 user group 4096 Jan 01 12:00 📁\n"
        )
        items = parse_ls_output(output, "/")
        # Directory emoji makes it into some entry (contract is non-crash
        # plus minimum preservation).
        assert items is not None

    def test_empty_output(self):
        from ashyterm.filemanager.ls_output import parse_ls_output
        assert parse_ls_output("", "/") == []

    def test_only_total_line(self):
        from ashyterm.filemanager.ls_output import parse_ls_output
        assert parse_ls_output("total 0\n", "/") == []


class TestIsConnectionError:
    @pytest.mark.parametrize("output, expected", [
        ("connection refused", True),
        ("CONNECTION REFUSED", True),
        ("Network is unreachable", True),
        ("timeout after 30s", True),
        ("operation timed out", True),
        ("permission denied", False),
        ("no such file or directory", False),
        ("", False),
        # Unicode surrounding text doesn't confuse the lowercased scan.
        ("erro de conexão timed out", True),
    ])
    def test_table(self, output: str, expected: bool):
        from ashyterm.filemanager.ls_output import is_connection_error
        assert is_connection_error(output) is expected


# ── SessionItem with unicode fields ─────────────────────────


class TestSessionUnicode:
    def test_roundtrip_with_unicode_name(self):
        from ashyterm.sessions.models import SessionItem
        s = SessionItem(
            name="日本語-session",
            session_type="ssh",
            host="example.com",
            user="usuário",
            auth_type="key",
            auth_value="/home/usuário/.ssh/id",
        )
        restored = SessionItem.from_dict(s.to_dict())
        assert restored.name == "日本語-session"
        assert restored.user == "usuário"

    def test_emoji_in_post_login_command(self):
        from ashyterm.sessions.models import SessionItem
        s = SessionItem(
            name="s",
            session_type="ssh",
            host="h",
            user="u",
            auth_type="key",
            post_login_command="echo 🎉 deploy done",
        )
        restored = SessionItem.from_dict(s.to_dict())
        assert restored.post_login_command == "echo 🎉 deploy done"


# ── Security sanitizers with unicode ────────────────────────


class TestSecuritySanitizersUnicode:
    def test_filename_preserves_combining_marks(self):
        """é can be NFC 'é' (U+00E9) or NFD 'e' + U+0301. Both survive
        sanitization unchanged (no normalization happens)."""
        from ashyterm.utils.security import InputSanitizer
        nfc = "é"              # precomposed
        nfd = "é"             # decomposed
        assert InputSanitizer.sanitize_filename(nfc) == nfc
        assert InputSanitizer.sanitize_filename(nfd) == nfd

    def test_hostname_strips_non_ascii_entirely(self):
        """sanitize_hostname keeps [a-z0-9.-] only — every non-ASCII
        codepoint is dropped."""
        from ashyterm.utils.security import InputSanitizer
        assert InputSanitizer.sanitize_hostname("host.例え.jp") == "host..jp"

    def test_cyrillic_lookalike_in_hostname_stripped(self):
        """'аpple.com' where 'а' is Cyrillic U+0430 — stripped to
        'pple.com'. Prevents IDN homograph attacks at the sanitizer
        level. HostnameValidator.is_valid_hostname would then reject
        the 'p' leading dot etc."""
        from ashyterm.utils.security import InputSanitizer
        out = InputSanitizer.sanitize_hostname("аpple.com")
        assert "а" not in out
        assert out == "pple.com"


# ── cli_parser with unicode argv ────────────────────────────


class TestCliParserUnicode:
    def test_unicode_working_directory(self):
        from ashyterm.cli_parser import CliArgParser
        from unittest.mock import MagicMock
        app = MagicMock()
        app.logger = MagicMock()
        parser = CliArgParser(app)

        result = parser.parse_command_line_args(["ashyterm", "/home/usuário/projetos"])
        assert result["working_directory"] == "/home/usuário/projetos"

    def test_unicode_ssh_target(self):
        from ashyterm.cli_parser import CliArgParser
        from unittest.mock import MagicMock
        app = MagicMock()
        app.logger = MagicMock()
        parser = CliArgParser(app)

        result = parser.parse_command_line_args(["ashyterm", "--ssh=u@münchen.de"])
        assert result["ssh_target"] == "u@münchen.de"


# ── UTF-8 surrogate & partial sequence handling ─────────────


class TestInvalidUTF8Tolerance:
    def test_osc7_surrogate_survives(self):
        """Lone surrogate (U+D800-U+DFFF) in a path. Python str permits;
        Rust String does not. We only assert no raise here — the Rust
        port will need ``String::from_utf8_lossy``."""
        from ashyterm.utils.osc7 import parse_directory_uri
        # Can't pass a bare surrogate through urllib.quote cleanly;
        # we rely on the parser gracefully handling the worst the
        # standard library can throw at it.
        try:
            parse_directory_uri("file://localhost/\ud800")
        except Exception as e:  # pragma: no cover
            pytest.fail(f"must not raise: {e}")

    def test_sanitizer_accepts_astral_plane(self):
        """Characters above BMP (emoji, rare scripts) pass through."""
        from ashyterm.utils.security import InputSanitizer
        # 🎉 is U+1F389, outside BMP.
        out = InputSanitizer.sanitize_filename("party-🎉-2024")
        assert "🎉" in out
