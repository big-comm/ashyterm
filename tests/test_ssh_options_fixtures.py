"""Fixture-driven contract tests for ssh_options.

Every JSON file under ``tests/fixtures/ssh_argv/`` is a language-agnostic
contract: given a session + call kwargs, the option dict must equal the
recorded value. Any Rust port has to pass the exact same fixtures to
prove behavioural parity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.ssh_options import (
    apply_x11_and_tunnel_options,
    build_base_ssh_options,
    build_ssh_test_options,
    needs_x11_flag,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "ssh_argv"


def _load_fixture(path: Path) -> Dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def _build_session(data: Dict[str, Any]) -> SessionItem:
    return SessionItem(**data)


def _run_fixture(fx: Dict[str, Any]) -> Dict[str, str]:
    session = _build_session(fx["session"])
    kwargs = fx.get("kwargs", {})
    fn = fx["function"]

    if fn == "build_base_ssh_options":
        return build_base_ssh_options(session, **kwargs)
    if fn == "build_ssh_test_options":
        return build_ssh_test_options(session, **kwargs)
    if fn == "apply_x11_and_tunnel_options":
        opts = dict(fx.get("initial_options", {}))
        apply_x11_and_tunnel_options(opts, session, **kwargs)
        return opts
    raise ValueError(f"Unknown fixture function: {fn}")


def _discover_fixtures():
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(p for p in FIXTURES_DIR.iterdir() if p.suffix == ".json")


@pytest.mark.parametrize("fixture_path", _discover_fixtures(), ids=lambda p: p.stem)
def test_ssh_options_fixture(fixture_path: Path):
    fx = _load_fixture(fixture_path)
    expected = fx["expected_options"]

    actual = _run_fixture(fx)
    assert actual == expected, (
        f"\nFixture: {fixture_path.name}\n"
        f"Description: {fx.get('description', '(none)')}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}"
    )

    if "expected_needs_x11" in fx:
        session = _build_session(fx["session"])
        cmd = fx.get("kwargs", {}).get("command_type", "ssh")
        assert needs_x11_flag(session, cmd) is fx["expected_needs_x11"]


def test_fixture_directory_is_not_empty():
    """Guard: a refactor that removes the directory silently would pass the
    parametrize above (empty set → 0 tests). Keep at least one fixture present."""
    assert len(_discover_fixtures()) >= 5
