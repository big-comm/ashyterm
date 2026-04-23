"""Integration tests — run ``python -m ashyterm`` as a subprocess.

These are language-agnostic by construction. The Rust port produces the
binary ``ashyterm`` (or an ``ashyterm-bin`` variant); pointing ``CMD``
below at the new binary reuses every test unchanged.

Only paths that short-circuit before GTK/Adw/Vte load are exercised
(``--help``, ``--version``, debug-flag pre-parsing). Anything that
would need a display is either skipped in headless CI or covered by
the widget-level tests elsewhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
SRC_DIR = REPO_ROOT / "src"

# Single point of truth for "how do we invoke ashyterm?" Rust port
# changes this to ``[str(target_dir / "ashyterm")]``. Everything else
# stays the same.
CMD: list[str] = [sys.executable, "-m", "ashyterm"]


def _run(*args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Invoke ashyterm with ``args``, return completed process."""
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC_DIR),
        # Force C locale so --help / --version output is predictable.
        "LC_ALL": "C",
        "LANG": "C",
    }
    return subprocess.run(
        [*CMD, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
        env=env,
    )


class TestHelp:
    def test_long_flag_exits_zero(self):
        r = _run("--help")
        assert r.returncode == 0
        assert "Usage:" in r.stdout
        assert r.stderr == ""

    def test_short_flag_exits_zero(self):
        r = _run("-h")
        assert r.returncode == 0
        assert "Usage:" in r.stdout

    def test_help_lists_expected_options(self):
        """Contract: the help text names every top-level flag. A Rust
        port must enumerate the same set."""
        r = _run("--help")
        for flag in [
            "--help",
            "--version",
            "--debug",
            "--log-level",
            "--working-directory",
            "--execute",
            "--close-after-execute",
            "--ssh",
            "--new-window",
        ]:
            assert flag in r.stdout, f"{flag} missing from --help output"

    def test_help_wins_over_other_args(self):
        """--help short-circuits before GTK loads, so even combined with
        other flags it still exits 0 without error."""
        r = _run("--help", "--ssh=u@h", "/tmp")
        assert r.returncode == 0


class TestVersion:
    def test_long_flag_exits_zero(self):
        r = _run("--version")
        assert r.returncode == 0
        assert r.stdout.startswith("ashyterm ")

    def test_short_flag_exits_zero(self):
        r = _run("-v")
        assert r.returncode == 0
        assert r.stdout.startswith("ashyterm ")

    def test_matches_package_version(self):
        """The printed version must match ``APP_VERSION`` from config —
        guards against bumping one and forgetting the other."""
        from ashyterm.settings.config import APP_VERSION

        r = _run("--version")
        assert APP_VERSION in r.stdout


class TestDebugPreParsing:
    def test_debug_flag_is_accepted(self):
        """--debug alone enables debug mode then continues; we can't run
        past that without GTK, but the help path still works with
        --debug set."""
        r = _run("--debug", "--help")
        assert r.returncode == 0
        assert "Usage:" in r.stdout

    def test_invalid_log_level_prints_warning_not_crash(self):
        """Invalid --log-level emits a Warning: line to stdout but does
        not exit non-zero — the pre-parse is defensive."""
        r = _run("--log-level", "BOGUS", "--help")
        assert r.returncode == 0
        assert "Usage:" in r.stdout
        assert "Warning" in r.stdout or "BOGUS" in r.stdout

    def test_valid_log_level_accepted(self):
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            r = _run("--log-level", level, "--help")
            assert r.returncode == 0, f"--log-level {level} failed"


class TestEnvironmentIsolation:
    def test_sandbox_env_does_not_pollute_real_home(self, tmp_path):
        """Running ashyterm --version with a custom XDG_CONFIG_HOME
        must not write anything under the real $HOME. Confirms the
        short-circuit path is genuinely inert."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        env = {
            **os.environ,
            "PYTHONPATH": str(SRC_DIR),
            "HOME": str(sandbox),
            "XDG_CONFIG_HOME": str(sandbox / "config"),
            "XDG_CACHE_HOME": str(sandbox / "cache"),
            "LC_ALL": "C",
        }
        r = subprocess.run(
            [*CMD, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(REPO_ROOT),
            env=env,
        )
        assert r.returncode == 0
        # The logger initializes before --version is checked, so log
        # directories get created (benign). What must NOT happen is any
        # user data file (sessions, commands, state) being written —
        # those are the files that cause the "Find Files ghost button"
        # class of bugs when tests leak into the real config.
        bad = [p for p in sandbox.rglob("*.json") if p.is_file()]
        assert not bad, f"short-circuit leaked user data files: {bad}"


class TestFastPathLatency:
    def test_help_is_fast(self):
        """--help must not pull in GTK (heavy import). We don't measure
        wall time directly — that's flaky — but we assert the call
        succeeds under a small timeout, proving GTK wasn't loaded."""
        r = _run("--help", timeout=3.0)
        assert r.returncode == 0

    def test_version_is_fast(self):
        r = _run("--version", timeout=3.0)
        assert r.returncode == 0
