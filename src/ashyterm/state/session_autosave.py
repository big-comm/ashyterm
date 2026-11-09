# ashyterm/state/session_autosave.py
"""Continuous session snapshot, so open tabs survive a crash or power loss.

The snapshot file holds every open window. It is rewritten whenever the
layout changes and is only removed when the user closes the last window
without saving, so a killed process leaves the last snapshot behind for
the next start to restore.
"""

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from gi.repository import GLib

from ..utils.json_versioning import migrate_data, stamp_version
from ..utils.logger import get_logger
from ..utils.security import atomic_json_write

if TYPE_CHECKING:
    from ..settings.manager import SettingsManager

AUTOSAVE_SETTING = "restore_tabs_after_crash"
AUTOSAVE_INTERVAL_SECONDS = 3
SCHEMA_VERSION = 3


def _migrate_v1_to_v2(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add empty groups list and null group_id to each tab."""
    data.setdefault("groups", [])
    for tab in data.get("tabs", []):
        tab.setdefault("group_id", None)
    return data


def _migrate_v2_to_v3(data: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap the single-window state into the multi-window list."""
    window = {"groups": data.pop("groups", []), "tabs": data.pop("tabs", [])}
    data["windows"] = [window] if window["tabs"] else []
    return data


MIGRATIONS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
}


def load_saved_windows(state_file: Path) -> List[Dict[str, Any]]:
    """Return the saved windows that have tabs; empty on a missing or bad file."""
    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        get_logger("ashyterm.state.autosave").error(
            f"Failed to read session state file: {error}"
        )
        return []
    if not isinstance(state, dict):
        return []
    state = migrate_data(state, SCHEMA_VERSION, MIGRATIONS)
    windows = state.get("windows")
    if not isinstance(windows, list):
        return []
    return [w for w in windows if isinstance(w, dict) and w.get("tabs")]


def close_keeps_session(policy: str, tab_count: int, response: Optional[str]) -> bool:
    """Whether closing the last window should keep the session for next start.

    ``response`` is the answer to the close dialog, when one was shown.
    """
    if response is not None:
        return response == "save"
    return tab_count > 1 and policy == "save_and_close"


class SessionAutosaver:
    """Owns the session snapshot file for the whole application."""

    def __init__(
        self,
        settings_manager: "SettingsManager",
        state_file: Path,
        list_windows: Callable[[], List[Any]],
    ) -> None:
        self.settings_manager = settings_manager
        self.state_file = Path(state_file)
        self._list_windows = list_windows
        self.logger = get_logger("ashyterm.state.autosave")
        self._last_written: Optional[str] = None
        self._frozen = False
        self._restore_claimed = False
        self._pending_restores = 0
        self._timer_id = 0

    @property
    def enabled(self) -> bool:
        return bool(self.settings_manager.get(AUTOSAVE_SETTING, True))

    def claim_restore(self) -> Optional[List[Dict[str, Any]]]:
        """Hand the saved windows to the first window that asks; None afterwards.

        Windows past the first are recreated by the caller, which reports
        each one through ``window_restored`` so no snapshot is written
        while they are still empty.
        """
        if self._restore_claimed:
            return None
        self._restore_claimed = True
        windows = load_saved_windows(self.state_file)
        self._pending_restores = max(len(windows) - 1, 0)
        return windows

    def window_restored(self) -> None:
        self._pending_restores = max(self._pending_restores - 1, 0)

    def finish_restore(self) -> None:
        """Start snapshots after startup; without autosave, keep the old one-shot file."""
        if not self.enabled:
            self.clear()
            return
        if not self._timer_id:
            self._timer_id = GLib.timeout_add_seconds(
                AUTOSAVE_INTERVAL_SECONDS, self._on_timer
            )

    def _on_timer(self) -> bool:
        self.save()
        return GLib.SOURCE_CONTINUE

    def save(self, force: bool = False) -> None:
        """Write the open windows when they changed; never erase on an empty view."""
        if self._frozen or self._pending_restores or not (force or self.enabled):
            return
        windows = self._snapshot_windows()
        if not windows:
            return
        state = stamp_version({"windows": windows}, SCHEMA_VERSION)
        encoded = json.dumps(state, sort_keys=True)
        if encoded == self._last_written:
            return
        try:
            atomic_json_write(self.state_file, state)
            self._last_written = encoded
        except OSError as error:
            self.logger.error(f"Failed to save session state: {error}")

    def _snapshot_windows(self) -> List[Dict[str, Any]]:
        windows = []
        # get_windows() is most-recently-focused first; keep creation order.
        ordered = sorted(self._list_windows(), key=lambda w: getattr(w, "get_id", lambda: 0)())
        for window in ordered:
            if getattr(window, "_cleanup_performed", False):
                continue
            state_manager = getattr(window, "state_manager", None)
            if state_manager is None:
                continue
            window_state = state_manager.serialize_window()
            if window_state:
                windows.append(window_state)
        return windows

    def clear(self) -> None:
        """Remove the snapshot so nothing is restored on the next start."""
        self._last_written = None
        try:
            os.remove(self.state_file)
            self.logger.info("Session state file removed.")
        except FileNotFoundError:
            pass
        except OSError as error:
            self.logger.error(f"Failed to remove state file: {error}")

    def close_last_window(self, keep: bool) -> None:
        """Apply the user's choice for the final window and stop writing."""
        if keep:
            self.save(force=True)
        else:
            self.clear()
        self.freeze()

    def on_window_removed(self, window: Any) -> None:
        """Drop a closed window from the snapshot; forget it all with the last one."""
        if self._frozen or not hasattr(window, "state_manager"):
            return
        if self._snapshot_windows():
            self.save()
        else:
            self.clear()
            self.freeze()

    def freeze(self) -> None:
        """Stop writing; used before teardown so closing tabs is not recorded."""
        self._frozen = True
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
