# ashyterm/terminal/terminal_config.py
"""Pure configuration helpers used by ``TerminalManager``.

Two jobs live here:

* :func:`resolve_working_directory` validates a user-supplied working
  directory and expands env/``~`` before handing it off to the spawner.
* :func:`compute_highlighting_config` + :func:`get_ssh_highlight_config`
  combine the global highlighting settings with any per-session
  overrides to produce the config the highlight-proxy needs.

Both are pure: they touch :mod:`os` for path checks but never mutate
the live terminal or the highlight manager.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any, Dict, Optional, Tuple

from ..sessions.models import SessionItem
from ..utils.logger import get_logger

_logger = get_logger("ashyterm.terminal.config")


# ── working-directory resolution ────────────────────────────


def resolve_working_directory(
    working_directory: Optional[str],
) -> Optional[str]:
    """Expand + validate ``working_directory``.

    Returns an absolute path if the directory exists, is a directory,
    and the process has read+execute permission. Returns ``None`` when
    the input is empty, inaccessible, or triggers any filesystem error
    (logged as a warning for visibility).
    """
    if not working_directory:
        return None
    try:
        expanded_path = os.path.expanduser(os.path.expandvars(working_directory))
        resolved_path = os.path.abspath(expanded_path)
        path_obj = pathlib.Path(resolved_path)
        if (
            path_obj.exists()
            and path_obj.is_dir()
            and os.access(resolved_path, os.R_OK | os.X_OK)
        ):
            return resolved_path
        _logger.warning(
            f"Working directory not accessible: {working_directory}"
        )
        return None
    except Exception as e:
        _logger.error(
            f"Error resolving working directory '{working_directory}': {e}"
        )
        return None


# ── highlight-config ────────────────────────────────────────


def _resolve_output_enabled(
    *, base_enabled: bool, session: Optional[SessionItem]
) -> bool:
    """Apply a session's ``output_highlighting`` override if set."""
    if session and session.output_highlighting is not None:
        return bool(session.output_highlighting)
    return bool(base_enabled)


def _resolve_dependent_flags(
    *,
    output_enabled: bool,
    cat_global: bool,
    shell_global: bool,
    session: Optional[SessionItem],
) -> Tuple[bool, bool]:
    """Derive (cat_enabled, shell_input_enabled).

    Both are gated by ``output_enabled`` — disabling output also turns
    off cat colorization and shell-input highlighting. Per-session
    overrides are still respected, but the AND with ``output_enabled``
    stays in place so the proxy stays quiet when highlighting is off.
    """
    cat_enabled = output_enabled and cat_global
    shell_enabled = output_enabled and shell_global

    if session is not None:
        if session.cat_colorization is not None:
            cat_enabled = output_enabled and bool(session.cat_colorization)
        if session.shell_input_highlighting is not None:
            shell_enabled = output_enabled and bool(session.shell_input_highlighting)

    return cat_enabled, shell_enabled


def compute_highlighting_config(
    *,
    session: Optional[SessionItem],
    is_local: bool,
    highlight_manager: Any,
    settings_manager: Any,
) -> Tuple[bool, Dict[str, bool]]:
    """Compute highlighting flags for a local/SSH terminal.

    Returns ``(should_highlight, config_dict)`` where ``should_highlight``
    is True if any of the three flags ends up enabled — that's what the
    spawner uses to decide whether to attach the proxy at all.
    """
    base_enabled = (
        highlight_manager.enabled_for_local
        if is_local
        else highlight_manager.enabled_for_ssh
    )
    output_enabled = _resolve_output_enabled(
        base_enabled=base_enabled, session=session
    )

    cat_global = settings_manager.get("cat_colorization_enabled", True)
    shell_global = settings_manager.get("shell_input_highlighting_enabled", False)

    cat_enabled, shell_enabled = _resolve_dependent_flags(
        output_enabled=output_enabled,
        cat_global=cat_global,
        shell_global=shell_global,
        session=session,
    )

    osc52_enabled = settings_manager.get("osc52_clipboard_enabled", True)
    should_highlight = output_enabled or cat_enabled or shell_enabled or osc52_enabled
    config = {
        "output_highlighting": output_enabled,
        "cat_colorization": cat_enabled,
        "shell_input_highlighting": shell_enabled,
    }
    return should_highlight, config


def get_ssh_highlight_config(
    *,
    session: SessionItem,
    highlight_manager: Any,
    settings_manager: Any,
) -> Dict[str, bool]:
    """SSH-specific variant that exposes legacy key names.

    Kept separate from :func:`compute_highlighting_config` because the
    SSH code path reads ``output_enabled`` etc. while the generic code
    path reads ``output_highlighting`` — renaming either would be a
    mass rename of call sites.
    """
    output_enabled = _resolve_output_enabled(
        base_enabled=highlight_manager.enabled_for_ssh, session=session
    )

    cat_enabled, shell_enabled = _resolve_dependent_flags(
        output_enabled=output_enabled,
        cat_global=settings_manager.get("cat_colorization_enabled", True),
        shell_global=settings_manager.get(
            "shell_input_highlighting_enabled", False
        ),
        session=session,
    )

    return {
        "output_enabled": output_enabled,
        "cat_enabled": cat_enabled,
        "shell_input_enabled": shell_enabled,
        "should_highlight": (
            output_enabled
            or cat_enabled
            or shell_enabled
            or settings_manager.get("osc52_clipboard_enabled", True)
        ),
    }
