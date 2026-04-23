"""Corpus-driven tests for ``parse_directory_uri`` (OSC 7 CWD tracking).

Each ``*.json`` file under ``tests/corpus/osc7/`` carries a URI and its
expected parse result (or ``None``). See the corpus README for the
schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ashyterm.utils.osc7 import OSC7Parser, parse_directory_uri


CORPUS_DIR = Path(__file__).parent / "corpus" / "osc7"


def _discover():
    if not CORPUS_DIR.is_dir():
        return []
    return sorted(CORPUS_DIR.glob("*.json"))


@pytest.mark.parametrize("corpus_path", _discover(), ids=lambda p: p.stem)
def test_osc7_corpus_entry(corpus_path: Path):
    with corpus_path.open() as f:
        entry = json.load(f)

    uri = entry["uri"]
    expected = entry["expected"]

    try:
        result = parse_directory_uri(uri)
    except Exception as e:  # pragma: no cover — failure path
        pytest.fail(
            f"{corpus_path.name}: parser raised {type(e).__name__} on {uri!r}: {e}"
        )

    if entry.get("accept_any_result"):
        return

    if expected is None:
        assert result is None, (
            f"{corpus_path.name}: expected None, got {result}"
        )
        return

    assert result is not None, f"{corpus_path.name}: parser returned None unexpectedly"
    assert result.hostname == expected["hostname"], (
        f"{corpus_path.name}: hostname mismatch"
    )
    assert result.path == expected["path"], (
        f"{corpus_path.name}: path mismatch"
    )

    if "expected_display_path" in entry:
        parser = OSC7Parser()
        result_with_display = parse_directory_uri(uri, parser)
        assert result_with_display is not None
        assert result_with_display.display_path == entry["expected_display_path"]

    if "expected_display_path_contains" in entry:
        parser = OSC7Parser()
        result_with_display = parse_directory_uri(uri, parser)
        assert result_with_display is not None
        assert entry["expected_display_path_contains"] in result_with_display.display_path


class TestDisplayPath:
    """OSC7Parser._create_display_path home-folder and depth rules."""

    def test_home_replaced_with_tilde(self, monkeypatch, tmp_path):
        parser = OSC7Parser()
        monkeypatch.setattr(parser, "_home_path", str(tmp_path))
        result = parser._create_display_path(f"{tmp_path}/projects")
        assert result == "~/projects"

    def test_exact_home_preserved_as_is(self, monkeypatch, tmp_path):
        parser = OSC7Parser()
        monkeypatch.setattr(parser, "_home_path", str(tmp_path))
        assert parser._create_display_path(str(tmp_path)) == str(tmp_path)

    def test_deep_path_shortened(self):
        parser = OSC7Parser()
        long = "/a/b/c/d/e/f/g"
        result = parser._create_display_path(long)
        assert result.startswith(".../")
        assert result.endswith("/e/f/g")

    def test_root_preserved(self):
        parser = OSC7Parser()
        assert parser._create_display_path("/") == "/"

    def test_empty_path_preserved_as_root(self):
        parser = OSC7Parser()
        assert parser._create_display_path("") == "/"


def test_corpus_has_enough_entries():
    assert len(_discover()) >= 15
