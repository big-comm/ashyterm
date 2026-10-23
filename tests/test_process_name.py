"""The process is named "ashyterm" even when libchildenv edits environ."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).parent.parent / "src"

# What libchildenv's constructor does before main(): unset CHILD_ENV_RULES,
# which the launcher puts first in the environment block.
CHILD = """
import logging, os
os.unsetenv("CHILD_ENV_RULES")
from ashyterm import _set_process_name
_set_process_name(logging.getLogger("test"))
print(open("/proc/self/comm").read().strip())
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_process_named_after_libchildenv_strips_environ():
    pytest.importorskip("setproctitle")
    # The child's environment block keeps this order: the rules come first.
    env = {"CHILD_ENV_RULES": "LD_PRELOAD,CHILD_ENV_RULES"}
    env.update(os.environ, PYTHONPATH=str(SRC_DIR))
    result = subprocess.run(
        [sys.executable, "-c", CHILD],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ashyterm"
