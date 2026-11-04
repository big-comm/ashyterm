"""Host shell support for the Flatpak package of Ashy Terminal."""

import os
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List


_RECORD_HOST_PROCESS = (
    'umask 077; IFS= read -r stat < "/proc/$$/stat"; '
    'printf "%s\\n%s\\n" "$$" "$stat" > "$1" || exit 1; '
    'shift; exec "$@"'
)
_READ_HOST_PROCESS = (
    'IFS= read -r stat < "/proc/$1/stat" || exit 1; '
    'printf "%s\\n" "$stat"; '
    'IFS= read -r children < "/proc/$1/task/$1/children"; '
    'printf "%s\\n" "$children"'
)


def is_flatpak() -> bool:
    return Path("/.flatpak-info").is_file()


@lru_cache(maxsize=1)
def get_host_shell() -> str:
    """Resolve the shell in the host environment, including NixOS paths."""
    result = subprocess.run(
        ["flatpak-spawn", "--host", "sh", "-c", 'printf "%s" "$SHELL"'],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    shell = result.stdout.strip()
    if not shell.startswith("/"):
        raise RuntimeError("The host did not provide an absolute SHELL path")
    return shell


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


def host_terminal_has_children(terminal: Any) -> bool:
    """Inspect this shell on the host; ask for confirmation when uncertain."""
    try:
        state = Path(terminal._flatpak_host_state).read_text().splitlines()
        pid, original_stat = state
        if not pid.isdecimal() or int(pid) <= 0:
            return True
        result = subprocess.run(
            [
                "flatpak-spawn",
                "--host",
                "sh",
                "-c",
                _READ_HOST_PROCESS,
                "ashyterm-check",
                pid,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        current_stat, children = result.stdout.splitlines()
        if _process_start_time(original_stat) != _process_start_time(current_stat):
            return True
        return bool(children.strip())
    except (
        AttributeError,
        OSError,
        ValueError,
        IndexError,
        subprocess.SubprocessError,
    ):
        return True
