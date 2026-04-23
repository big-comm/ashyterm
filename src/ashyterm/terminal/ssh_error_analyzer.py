# ashyterm/terminal/ssh_error_analyzer.py
"""Pure-logic SSH error analysis — exit code decoding, auth detection, text analysis."""

import os
from typing import Optional

import gi

gi.require_version("Vte", "3.91")
from gi.repository import Vte
from ..utils.logger import log_swallowed_exception


def decode_exit_code(child_status: int) -> int:
    """Decode wait() status → actual exit code."""
    if os.WIFEXITED(child_status):
        return os.WEXITSTATUS(child_status)
    elif os.WIFSIGNALED(child_status):
        return 128 + os.WTERMSIG(child_status)
    return child_status


def analyze_exit_status(terminal_info: dict, child_status: int) -> dict:
    """Analyze exit status → exit info dict. Pure logic, no GTK."""
    decoded = decode_exit_code(child_status)
    user_terminated_codes = {130, 137, 143}  # SIGINT, SIGKILL, SIGTERM
    is_user_terminated = decoded in user_terminated_codes
    closed_by_user = terminal_info.get("_closed_by_user", False)
    is_ssh = terminal_info.get("type") in ["ssh", "sftp"]

    ssh_failed = (
        is_ssh
        and child_status != 0
        and not closed_by_user
        and not is_user_terminated
    )

    return {
        "decoded_exit_code": decoded,
        "is_user_terminated": is_user_terminated,
        "closed_by_user": closed_by_user,
        "is_ssh": is_ssh,
        "ssh_failed": ssh_failed,
    }


def has_connection_error(text: str) -> bool:
    """Check if text contains SSH connection error patterns."""
    error_patterns = [
        "no route to host",
        "connection refused",
        "connection timed out",
        "permission denied",
        "authentication failed",
        "host key verification failed",
        "broken pipe",
    ]
    return any(p in text for p in error_patterns)


def has_shell_prompt(text: str) -> bool:
    """Check if text contains shell prompt indicators."""
    success_patterns = [
        "$",
        "#",
        "❯",
        "➜",
        "›",
        "last login:",
        "welcome to",
    ]
    return any(p in text for p in success_patterns)


def check_ssh_auth_error(child_status: int, terminal: Vte.Terminal) -> bool:
    """Check if SSH failure is due to authentication error.

    Checks exit code + scans last 20 rows of terminal text for auth patterns.
    """
    if decode_exit_code(child_status) in (5, 6):
        return True

    try:
        col_count = terminal.get_column_count()
        row_count = terminal.get_row_count()
        start_row = max(0, row_count - 20)
        result = terminal.get_text_range_format(
            0, start_row, 0, row_count - 1, col_count - 1,
        )
        if result and len(result) > 0 and result[0]:
            text_lower = result[0].lower()
            for pattern in [
                "permission denied",
                "authentication failed",
                "incorrect password",
                "invalid password",
                "too many authentication failures",
            ]:
                if pattern in text_lower:
                    return True
    except Exception as exc:
        log_swallowed_exception(exc)

    return False


def extract_terminal_text(terminal: Vte.Terminal, max_rows: int = 50) -> Optional[str]:
    """Extract recent text from terminal for error analysis."""
    try:
        col_count = terminal.get_column_count()
        row_count = terminal.get_row_count()
        start_row = max(0, row_count - max_rows)

        result = terminal.get_text_range_format(
            Vte.Format.TEXT,
            start_row, 0,
            row_count - 1, col_count - 1,
        )
        if result and len(result) > 0 and result[0]:
            return result[0]
    except Exception as exc:
        log_swallowed_exception(exc)
    return None


def analyze_ssh_error(exit_code: int, terminal_text: Optional[str]) -> dict:
    """Analyze SSH error → typed error info dict.

    Delegates to ssh_dialogs.get_error_info for classification,
    then enriches with is_auth_error / is_host_key_error booleans.
    """
    from ..ui.ssh_dialogs import get_error_info

    error_type, _, error_description = get_error_info(exit_code, terminal_text)

    auth_error_types = (
        "auth_failed", "auth_multi_failed", "key_rejected",
        "key_format_error", "key_permissions",
    )
    host_key_error_types = ("host_key_failed", "host_key_changed")

    return {
        "error_type": error_type,
        "error_description": error_description,
        "is_auth_error": error_type in auth_error_types,
        "is_host_key_error": error_type in host_key_error_types,
    }
