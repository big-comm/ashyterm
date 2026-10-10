"""Mirror the DEC private input modes VTE applies from PTY output.

VTE exposes no API for its mouse-tracking state, so the PTY reader feeds
output here before VTE parses it. The rules match VTE 0.84 (verified with
DECRQM, see tests/test_scroll_mode_guard.py): modes are independent flags,
ESC or U+009B aborts an OSC/DCS payload, CAN/SUB cancel a sequence, and C0
controls inside CSI are executed without ending it.
"""

import re
from typing import Any

MOUSE_TRACKING_MODES = frozenset({9, 1000, 1001, 1002, 1003})
_INPUT_MODES = MOUSE_TRACKING_MODES | {1, 47, 1005, 1006, 1007, 1015, 1016, 1047, 1049}
# Complete CSI sequences take the fast path. A lone ESC, U+009B, or a UTF-8
# lead byte ending the chunk falls through to the byte-wise parser (chunk
# splits, embedded C0, CAN/SUB).
_INTRODUCER = re.compile(rb"\x1b\[([0-?]*)([ -/]*)([@-~])|\x1b|\xc2(?:\x9b|\Z)")
_MODE_FINALS = b"hlsrp"  # SM, RM, XTSAVE, XTRESTORE, DECSTR
_PRIVATE_MODES = re.compile(rb"\?[0-9;]*")
_MAX_PARAMETERS = 32  # VTE ignores a CSI with more parameters.
_MAX_SEQUENCE = 256  # Memory bound for a CSI split across PTY reads.
_TEXT, _ESCAPE, _ESCAPE_INTERMEDIATE, _CSI, _C1 = range(5)
_SET, _RESET, _SAVE, _RESTORE = b"hlsr"
_CANCEL = (0x18, 0x1A)


class ScrollModeGuard:
    """Incremental escape scanner tracking which mouse modes are active."""

    def __init__(self) -> None:
        self._state = _TEXT
        self._parameters = bytearray()
        self._intermediates = bytearray()
        self._mouse_modes: set[int] = set()
        self._saved_modes: dict[int, bool] = {}
        self.observed = False

    @classmethod
    def for_terminal(cls, terminal: Any) -> "ScrollModeGuard":
        """Return the guard owned by ``terminal``; it outlives scroll bindings."""
        guard = getattr(terminal, "_ashy_mode_guard", None)
        if not isinstance(guard, cls):
            guard = cls()
            terminal._ashy_mode_guard = guard
        return guard

    @property
    def mouse_tracking(self) -> bool:
        """Whether VTE encodes wheel input as mouse reports for the application."""
        return bool(self._mouse_modes)

    def feed(self, chunk: bytes) -> bool:
        """Return whether a reset or relevant input-mode change occurred."""
        self.observed = True
        if self._state == _TEXT and b"\x1b" not in chunk and b"\xc2" not in chunk:
            return False  # Plain output: stay at C speed.
        changed = False
        index, size = 0, len(chunk)
        while index < size:
            if self._state != _TEXT:
                changed |= self._consume(chunk[index])
                index += 1
                continue
            match = _INTRODUCER.search(chunk, index)
            if match is None:
                break
            index = match.end()
            if match[3] is not None:
                if match[3] in _MODE_FINALS:
                    changed |= self._dispatch_csi(match[1], match[2], match[3][0])
            elif match[0] == b"\x1b":
                self._state = _ESCAPE
            elif match[0] == b"\xc2":
                self._state = _C1  # The code point continues in the next read.
            else:
                self._start_csi()  # U+009B
        return changed

    def _consume(self, byte: int) -> bool:
        if byte == 0x1B:
            self._state = _ESCAPE
        elif byte in _CANCEL:
            self._state = _TEXT
        elif self._state == _C1 and byte == 0x9B:
            self._start_csi()  # U+009B; a raw 0x9B elsewhere is UTF-8 text.
        elif byte == 0xC2:
            self._state = _C1
        elif byte >= 0x80 or self._state == _C1:
            self._state = _TEXT  # Any other non-ASCII code point aborts.
        elif self._state == _CSI:
            return self._consume_csi(byte)
        else:
            return self._consume_escape(byte)
        return False

    def _consume_escape(self, byte: int) -> bool:
        if byte < 0x20 or byte == 0x7F:
            return False  # C0 is executed in place; DEL is ignored.
        if byte < 0x30:
            self._state = _ESCAPE_INTERMEDIATE
            return False
        intermediate = self._state == _ESCAPE_INTERMEDIATE
        self._state = _TEXT
        if intermediate:
            return False
        if byte == 0x5B:
            self._start_csi()
            return False
        return byte == 0x63 and self._reset()  # RIS

    def _start_csi(self) -> None:
        self._state = _CSI
        self._parameters.clear()
        self._intermediates.clear()

    def _consume_csi(self, byte: int) -> bool:
        if 0x40 <= byte <= 0x7E:
            self._state = _TEXT
            return self._dispatch_csi(self._parameters, self._intermediates, byte)
        if byte < 0x20 or byte == 0x7F:
            return False
        if len(self._parameters) + len(self._intermediates) >= _MAX_SEQUENCE:
            # Unbounded control: drop it and stop momentum conservatively.
            self._state = _TEXT
            return True
        (self._intermediates if byte < 0x30 else self._parameters).append(byte)
        return False

    def _dispatch_csi(self, parameters: bytes, intermediates: bytes, final: int) -> bool:
        if len(parameters) + len(intermediates) >= _MAX_SEQUENCE:
            return True  # Same outcome as the byte-wise bound.
        if final == 0x70 and intermediates == b"!":  # DECSTR
            return self._reset()
        if intermediates or final not in (_SET, _RESET, _SAVE, _RESTORE):
            return False
        if not _PRIVATE_MODES.fullmatch(parameters):
            return False
        values = parameters[1:].split(b";")
        if len(values) > _MAX_PARAMETERS:
            return False
        relevant = False
        for mode in (int(value) for value in values if value):
            relevant |= mode in _INPUT_MODES
            if mode in MOUSE_TRACKING_MODES:
                self._apply_mouse_mode(mode, final)
        return relevant and final != _SAVE

    def _apply_mouse_mode(self, mode: int, final: int) -> None:
        if final == _SAVE:
            self._saved_modes[mode] = mode in self._mouse_modes
            return
        if final == _RESTORE:
            # XTRESTORE consumes the saved value; unsaved modes restore off.
            enabled = self._saved_modes.pop(mode, False)
        else:
            enabled = final == _SET
        if enabled:
            self._mouse_modes.add(mode)
        else:
            self._mouse_modes.discard(mode)

    def _reset(self) -> bool:
        """RIS and DECSTR clear active and saved modes, as in VTE."""
        self._mouse_modes.clear()
        self._saved_modes.clear()
        return True


def wheel_reaches_application(terminal: Any, adjustment: Any) -> bool:
    """Whether VTE turns wheel input into application mouse or cursor input.

    Mouse tracking comes from observed PTY output and holds even with
    scrollback. Without history (alternate screen) the wheel also belongs to
    the application, which keeps unobserved PTYs working.
    """
    guard = getattr(terminal, "_ashy_mode_guard", None)
    if isinstance(guard, ScrollModeGuard) and guard.mouse_tracking:
        return True
    history = adjustment.get_upper() - adjustment.get_lower()
    return history <= adjustment.get_page_size()
