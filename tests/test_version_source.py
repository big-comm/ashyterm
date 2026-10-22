"""APP_VERSION is the only version: gitrepo bumps it, packaging reads it."""

import re
import tomllib
from pathlib import Path

from ashyterm.settings.config import AppConstants

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_does_not_pin_a_version() -> None:
    project = _pyproject()["project"]
    assert "version" not in project
    assert "version" in project["dynamic"]


def test_hatch_pattern_reads_app_version() -> None:
    source = _pyproject()["tool"]["hatch"]["version"]
    text = (ROOT / source["path"]).read_text(encoding="utf-8")
    matches = re.findall(source["pattern"], text, flags=re.MULTILINE)
    assert matches == [AppConstants.APP_VERSION]
