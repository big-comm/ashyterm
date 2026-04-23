"""Corpus-driven tests for CliArgParser.

Every ``*.json`` file under ``tests/corpus/cli_parser/`` is an input case.
See that directory's README for the file format.

The parser must:

* Never raise on any corpus entry.
* Match ``expected`` exactly when the entry declares one.

This is the shape the Rust port inherits. ``cargo fuzz`` seeds from the
same directory; new crashers drop into ``crashers/`` without code changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest


CORPUS_DIR = Path(__file__).parent / "corpus" / "cli_parser"


def _discover_corpus() -> List[Path]:
    if not CORPUS_DIR.is_dir():
        return []
    return sorted(
        p
        for p in CORPUS_DIR.rglob("*.json")
        if p.is_file()
    )


def _corpus_id(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


@pytest.fixture
def parser():
    from ashyterm.cli_parser import CliArgParser

    app = MagicMock()
    app.logger = MagicMock()
    return CliArgParser(app)


@pytest.mark.parametrize(
    "corpus_path",
    _discover_corpus(),
    ids=lambda p: _corpus_id(p),
)
def test_parser_corpus_entry(parser, corpus_path: Path):
    with corpus_path.open() as f:
        entry = json.load(f)

    argv = entry["argv"]
    assert isinstance(argv, list), f"argv must be a list in {corpus_path}"
    assert all(isinstance(a, str) for a in argv), (
        f"argv must be list[str] in {corpus_path}"
    )

    # Hard contract: the parser must never raise on any corpus input.
    try:
        result = parser.parse_command_line_args(argv)
    except Exception as e:  # pragma: no cover — failure path
        pytest.fail(
            f"parser raised {type(e).__name__} on {corpus_path.name}: {e}\n"
            f"argv = {argv}"
        )

    expected = entry.get("expected")
    if expected is not None:
        assert result == expected, (
            f"\n{corpus_path.name}: output mismatch\n"
            f"argv    = {argv}\n"
            f"expect  = {expected}\n"
            f"actual  = {result}"
        )

    if entry.get("assert_warning"):
        assert parser.logger.warning.called, (
            f"{corpus_path.name}: expected logger.warning to be called"
        )


def test_corpus_has_enough_entries():
    """Guard against accidental removal of the whole corpus."""
    assert len(_discover_corpus()) >= 20


def test_valid_and_edge_directories_both_populated():
    """Keep the valid/edge split meaningful — both should have entries."""
    valid = list((CORPUS_DIR / "valid").glob("*.json"))
    edge = list((CORPUS_DIR / "edge").glob("*.json"))
    assert len(valid) >= 10
    assert len(edge) >= 5
