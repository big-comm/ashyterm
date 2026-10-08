"""Use the history scroll engine to decelerate VTE-encoded application input."""

from __future__ import annotations

import math
import re
import weakref
from typing import Any, Optional

import gi

gi.require_version("Vte", "3.91")
from gi.repository import Vte

_SGR_WHEEL = re.compile(r"\x1b\[<(\d+);\d+;\d+M\Z")
_CURSOR = re.compile(r"\x1b(?:O|\[)(?:1;\d+)?([AB])\Z")


def scroll_report_direction(report: str) -> int:
    """Identify vertical wheel/cursor reports without rewriting their encoding."""
    if match := _SGR_WHEEL.fullmatch(report):
        button = int(match[1]) & ~28  # Shift/Alt/Ctrl modifiers
        return -1 if button == 64 else 1 if button == 65 else 0
    if match := _CURSOR.fullmatch(report):
        return -1 if match[1] == "A" else 1
    if report.startswith("\x1b[M") and len(report) == 6:
        button = (ord(report[3]) - 32) & ~28
        return -1 if button == 64 else 1 if button == 65 else 0
    return 0


class ApplicationScrollMomentum:
    """Adjustment-compatible target for ScrollHandler's existing kinetic engine."""

    def __init__(self, terminal: Vte.Terminal, scroll_engine: Any) -> None:
        self._terminal_ref = weakref.ref(terminal)
        self._engine_ref = weakref.ref(scroll_engine)
        self._report = b""
        self._direction = 0
        self._position = 0.0
        self._fraction = 0.0
        self.replaying = False

    def cancel(self) -> None:
        """Drop timers and any report captured before a mode/input change."""
        engine = self._engine_ref()
        if engine is not None:
            engine._cancel_kinetic_scroll(self)
        self._report = b""
        self._direction = 0
        self._position = self._fraction = 0.0

    def track(self, reports: list[str], source: Any) -> None:
        """Measure native scroll reports in pixels for the shared deceleration."""
        terminal = self._terminal_ref()
        engine = self._engine_ref()
        if terminal is None or engine is None:
            return
        pixels = 0.0
        for report in reports:
            direction = scroll_report_direction(report)
            if not direction:
                continue
            if direction != self._direction:
                self.cancel()
                pixels = 0.0
            self._direction = direction
            self._report = report.encode("utf-8")
            pixels += direction * terminal.get_char_height()
        if self._report:
            self._position += pixels
            engine._track_kinetic_scroll(self, source, pixels)

    def get_vadjustment(self) -> Optional["ApplicationScrollMomentum"]:
        """Stop when the terminal hides or returns to its normal history."""
        terminal = self._terminal_ref()
        if terminal is None or not self._report or not terminal.get_mapped():
            return None
        adjustment = terminal.get_vadjustment()
        if adjustment.get_upper() - adjustment.get_lower() > adjustment.get_page_size():
            return None
        return self

    def get_value(self) -> float:
        """Return the virtual pixel position used by the kinetic engine."""
        return self._position

    def set_value(self, position: float) -> None:
        """Replay whole native reports, retaining fractional pixel movement."""
        terminal = self._terminal_ref()
        if terminal is None or self.get_vadjustment() is None:
            return
        self._fraction += position - self._position
        self._position = position
        steps = math.trunc(self._fraction / max(1, terminal.get_char_height()))
        if not steps or (steps > 0) != (self._direction > 0):
            return
        self._fraction -= steps * terminal.get_char_height()
        self.replaying = True
        try:
            terminal.feed_child(self._report * min(abs(steps), 128))
        finally:
            self.replaying = False
