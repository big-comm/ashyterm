"""Host shell support for the Flatpak package of Ashy Terminal."""

import os
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .logger import get_logger


_RECORD_HOST_PROCESS = (
    'umask 077; IFS= read -r stat < "/proc/$$/stat"; '
    'printf "%s\\n%s\\n" "$$" "$stat" > "$1" || exit 1; '
    'shift; exec "$@"'
)
# Prints two lines per PID: its stat (empty if the process is gone) and its
# children. One host round trip covers every terminal being closed.
_READ_HOST_PROCESSES = (
    'for pid in "$@"; do '
    'stat=; IFS= read -r stat < "/proc/$pid/stat"; '
    'printf "%s\\n" "$stat"; '
    'children=; IFS= read -r children < "/proc/$pid/task/$pid/children"; '
    'printf "%s\\n" "$children"; '
    "done"
)


@lru_cache(maxsize=1)
def is_flatpak() -> bool:
    return Path("/.flatpak-info").is_file()


@lru_cache(maxsize=1)
def get_host_shell() -> str:
    """Resolve the shell in the host environment, including NixOS paths.

    Falls back to /bin/sh so a misbehaving host never blocks opening a tab.
    """
    try:
        result = subprocess.run(
            ["flatpak-spawn", "--host", "sh", "-c", 'printf "%s" "$SHELL"'],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        shell = result.stdout.strip()
        if shell.startswith("/"):
            return shell
        reason = f"non-absolute SHELL {shell!r}"
    except (OSError, subprocess.SubprocessError) as exc:
        reason = str(exc)
    get_logger(__name__).warning(
        f"Could not resolve the host shell ({reason}); using /bin/sh"
    )
    return "/bin/sh"


def shell_temp_root() -> str:
    """Place shell integration files where both environments can read them."""
    root = Path(os.environ["XDG_CACHE_HOME"]) / "ashyterm" / "shell"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return str(root)


def prepare_host_bash(osc7_command: str) -> tuple[str, str]:
    """Install OSC7 after the user's bashrc without changing the host config."""
    temp_dir = tempfile.mkdtemp(prefix="ashyterm_bash_", dir=shell_temp_root())
    rcfile = Path(temp_dir) / "bashrc"
    rcfile.write_text(
        'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n'
        f"{osc7_command}\n"
        'PROMPT_COMMAND=(__ashyterm_osc7 "${PROMPT_COMMAND[@]}")\n',
        encoding="utf-8",
    )
    return temp_dir, str(rcfile)


def host_shell_command(
    command: List[str],
    env: Dict[str, str],
    working_dir: str,
    terminal: Any = None,
) -> List[str]:
    """Keep the host environment and forward only terminal integration values."""
    if not is_flatpak():
        return command
    argv = ["flatpak-spawn", "--host", "--watch-bus", f"--directory={working_dir}"]
    for key in ("TERM", "COLORTERM", "VTE_VERSION", "ZDOTDIR"):
        if key in env:
            argv.append(f"--env={key}={env[key]}")
    if state_file := env.get("_ASHYTERM_HOST_STATE"):
        if terminal is not None:
            terminal._flatpak_host_state = state_file
        command = [
            "sh",
            "-c",
            _RECORD_HOST_PROCESS,
            "ashyterm-host",
            state_file,
            *command,
        ]
    return [*argv, *command]


def _process_start_time(stat: str) -> str:
    """Parse /proc/PID/stat without assuming comm contains no spaces."""
    return stat.rsplit(")", 1)[1].split()[19]


def host_terminals_have_children(terminals: Iterable[Any]) -> bool:
    """Inspect these shells on the host; ask for confirmation when uncertain.

    A single flatpak-spawn call serves all terminals, so closing a tab with
    several panes blocks the UI for one round trip (2s at worst), not one each.
    """
    try:
        states = [
            Path(t._flatpak_host_state).read_text().splitlines() for t in terminals
        ]
        if not states:
            return False
        pids = []
        for pid, _original_stat in states:
            if not pid.isdecimal() or int(pid) <= 0:
                return True
            pids.append(pid)
        result = subprocess.run(
            [
                "flatpak-spawn",
                "--host",
                "sh",
                "-c",
                _READ_HOST_PROCESSES,
                "ashyterm-check",
                *pids,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        lines = result.stdout.splitlines()
        if len(lines) != 2 * len(states):
            return True
        for (_pid, original_stat), current_stat, children in zip(
            states, lines[0::2], lines[1::2]
        ):
            if _process_start_time(original_stat) != _process_start_time(current_stat):
                return True
            if children.strip():
                return True
        return False
    except (
        AttributeError,
        OSError,
        ValueError,
        IndexError,
        subprocess.SubprocessError,
    ):
        return True


def host_terminal_has_children(terminal: Any) -> bool:
    """Single-terminal form of host_terminals_have_children()."""
    return host_terminals_have_children([terminal])
