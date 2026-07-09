"""Detect interactive SSH clients and extract their connection target."""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional, Sequence, Tuple


_OPTIONS_WITH_VALUE = {
    "-B",
    "-b",
    "-c",
    "-D",
    "-E",
    "-e",
    "-F",
    "-I",
    "-i",
    "-J",
    "-L",
    "-l",
    "-m",
    "-O",
    "-o",
    "-p",
    "-Q",
    "-R",
    "-S",
    "-W",
    "-w",
}


def extract_ssh_target(command: Sequence[str]) -> Optional[str]:
    """Return the destination from an OpenSSH or ``tailscale ssh`` argv."""
    args = list(command)
    if not args:
        return None

    executable = os.path.basename(args[0]).lower()
    index = 1
    if executable == "tailscale":
        try:
            index = args.index("ssh", 1) + 1
        except ValueError:
            return None
    elif executable != "ssh":
        return None

    skip_next = False
    positional_only = False
    for argument in args[index:]:
        if skip_next:
            skip_next = False
            continue
        if not positional_only and argument == "--":
            positional_only = True
            continue
        if not positional_only and argument in _OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if not positional_only and argument.startswith("-"):
            continue
        return argument or None
    return None


def find_ssh_process(
    processes: Iterable[Any],
) -> Tuple[Optional[Any], Optional[str]]:
    """Find a live SSH client process and its target.

    Process inspection is best-effort because a child can exit between
    ``children()`` and ``cmdline()``.
    """
    for process in processes:
        try:
            command = process.cmdline()
            target = extract_ssh_target(command)
        except Exception:
            continue
        if target:
            return process, target
    return None, None
