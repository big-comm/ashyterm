# ashyterm/terminal/tab_close.py
"""Close-flow helpers for the tab manager.

The UX: click the tab's × → if any terminal in the tab has a child
process, confirm → otherwise proceed. After confirmation each terminal
is either torn down synchronously (stable running process ⇒ wait for
child exit) or removed immediately.

This module owns the three pure predicates and the confirmation
dialog. The ``TabManager`` stays responsible for the actual tab-bar
mutation (``_close_tab_by_page``) because that still touches widget
state only it owns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw

from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .manager import TerminalManager


def has_stable_running_process(info: Optional[dict]) -> bool:
    """Return True when a registry entry describes a live, stable child.

    "Stable" means: we have a real PID (not the ``-1`` placeholder used
    for terminals that haven't finished spawning) *and* the status
    field still says ``"running"``. Short-circuits when ``info`` is
    ``None`` — the registry returns ``None`` for terminals that were
    already torn down.
    """
    if not info:
        return False
    pid = info.get("process_id")
    status = info.get("status")
    return bool(pid and pid != -1 and status == "running")


def any_terminal_has_foreground_process(
    terminals: Iterable[Any],
    *,
    terminal_manager: "TerminalManager",
) -> bool:
    """Return True when any terminal has a *child* process.

    We use ``psutil.Process.children()`` — the existence of any child
    means the user is running something beyond the shell and deserves
    a "close anyway?" dialog. If ``psutil`` isn't installed we return
    False so the user doesn't get spurious warnings; the hard path
    still waits for the child-exit event from VTE.
    """
    try:
        import psutil
    except ImportError:
        return False

    for terminal in terminals:
        terminal_id = getattr(terminal, "terminal_id", None)
        if not terminal_id:
            continue
        info = terminal_manager.registry.get_terminal_info(terminal_id)
        if not info:
            continue
        pid = info.get("process_id")
        if not pid or pid == -1:
            continue
        try:
            proc = psutil.Process(pid)
            if proc.children():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Process vanished between the registry read and the psutil
            # call (common during shutdown) — treat as "no child".
            continue
    return False


def process_terminals_for_close(
    terminals: Iterable[Any],
    *,
    terminal_manager: "TerminalManager",
) -> bool:
    """Tear down each terminal. Return whether the caller must wait.

    "Wait" means: the terminal had a stable running process, so VTE
    will emit ``child-exited`` later and the tab should stay in the
    stack until that fires. Otherwise the tab can be removed
    immediately.

    Auto-reconnecting SSH terminals never trigger the wait path — the
    whole point of auto-reconnect is that the child may bounce.
    """
    should_wait = False

    for terminal in terminals:
        terminal_id = getattr(terminal, "terminal_id", None)
        is_auto_reconnecting = terminal_manager.is_auto_reconnect_active(terminal)

        if terminal_id and not is_auto_reconnecting:
            info = terminal_manager.registry.get_terminal_info(terminal_id)
            if has_stable_running_process(info):
                should_wait = True

        terminal_manager.remove_terminal(terminal, force_kill_group=True)

    return should_wait


def build_close_confirmation_dialog(
    *,
    parent: Any,
    on_response: Callable[[Adw.AlertDialog, str], None],
) -> Adw.AlertDialog:
    """Build (but don't present) the "Close Tab?" confirmation dialog.

    Returns the dialog so the caller can ``.present(parent_window)``;
    ``on_response`` is connected to the "response" signal with no extra
    user-data so the caller can bind whatever context it needs via
    closure.
    """
    dialog = Adw.AlertDialog(
        heading=_("Close Tab?"),
        body=_("A process is still running in this tab. Close anyway?"),
    )
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("close", _("Close"))
    dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", on_response)
    return dialog


def present_close_confirmation(
    *,
    presenter: Any,
    on_response: Callable[[Adw.AlertDialog, str], None],
) -> Adw.AlertDialog:
    """Build the dialog and present it via ``presenter.present()``.

    ``presenter`` is whatever the calling window returns from
    ``get_root()`` — ``Adw.AlertDialog.present`` accepts any
    ``Gtk.Widget`` that resolves to the right top-level window.
    """
    dialog = build_close_confirmation_dialog(
        parent=presenter, on_response=on_response
    )
    dialog.present(presenter)
    return dialog
