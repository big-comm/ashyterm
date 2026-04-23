"""Corpus-driven tests for SSHConfigParser.

Each ``NN_*.config`` under ``tests/corpus/ssh_config/`` pairs with a
``NN_*.expected.json`` declaring the full list of SSHConfigHost dicts
the parser should produce. See that directory's README for the format.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from ashyterm.utils.ssh_config_parser import SSHConfigParser


CORPUS_DIR = Path(__file__).parent / "corpus" / "ssh_config"


def _discover_cases():
    if not CORPUS_DIR.is_dir():
        return []
    return sorted(CORPUS_DIR.glob("*.config"))


@pytest.mark.parametrize(
    "config_path",
    _discover_cases(),
    ids=lambda p: p.stem,
)
def test_ssh_config_corpus_entry(config_path: Path):
    expected_path = config_path.with_suffix(".expected.json")
    assert expected_path.exists(), f"missing expected file for {config_path.name}"

    with expected_path.open() as f:
        expected_doc = json.load(f)
    expected = expected_doc["expected"]

    parser = SSHConfigParser()
    entries = parser.parse(config_path)
    actual = [asdict(e) for e in entries]

    assert actual == expected, (
        f"\nConfig:   {config_path.name}\n"
        f"Desc:     {expected_doc.get('description', '(none)')}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}"
    )


def test_parser_never_raises_on_corpus(tmp_path: Path):
    """Independent of per-file semantics, the parser must not raise on
    any corpus file. Catches regressions that break the whole table."""
    for config_path in _discover_cases():
        parser = SSHConfigParser()
        try:
            parser.parse(config_path)
        except Exception as e:  # pragma: no cover
            pytest.fail(f"{config_path.name}: parser raised {type(e).__name__}: {e}")


def test_parser_tolerates_missing_file(tmp_path: Path):
    """Nonexistent path returns empty list, no raise."""
    parser = SSHConfigParser()
    result = parser.parse(tmp_path / "does-not-exist")
    assert result == []


def test_parser_tolerates_unreadable_bytes(tmp_path: Path):
    """File with invalid UTF-8 should parse with errors='ignore' — no crash,
    whatever valid ASCII/UTF-8 lines survive become entries."""
    config = tmp_path / "bad.config"
    config.write_bytes(
        b"Host good\n    HostName g.example\n"
        b"# comment with bad byte: \xff\xfe\n"
        b"Host good2\n    HostName g2.example\n"
    )
    parser = SSHConfigParser()
    entries = parser.parse(config)
    aliases = [e.alias for e in entries]
    assert "good" in aliases
    assert "good2" in aliases


def test_corpus_has_enough_cases():
    """Guard: losing the corpus would silently weaken this module."""
    assert len(_discover_cases()) >= 20
