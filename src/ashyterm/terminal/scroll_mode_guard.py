"""Detect input-protocol changes before replaying application scroll reports."""

_INPUT_MODES = {1, 9, 47, 1000, 1002, 1003, 1005, 1006, 1007, 1015, 1016, 1047, 1049}


class ScrollModeGuard:
    """Bounded escape scanner; OSC/DCS payloads are never treated as modes."""

    def __init__(self) -> None:
        self._state = "text"
        self._parameters = bytearray()

    def feed(self, chunk: bytes) -> bool:
        """Return whether a reset or relevant mode change occurred."""
        if self._state == "text" and b"\x1b" not in chunk and b"\x9b" not in chunk:
            return False
        changed = False
        for byte in chunk:
            if self._state == "string":
                if byte == 7:
                    self._state = "text"
                elif byte == 27:
                    self._state = "string_escape"
            elif self._state == "string_escape":
                if byte != 27:
                    self._state = "text" if byte == 92 else "string"
            elif byte == 27:
                self._state = "escape"
            elif byte == 0x9B:
                self._start_escape(91)
            elif self._state == "escape":
                changed |= byte == 99  # RIS
                self._start_escape(byte)
            elif self._state == "csi":
                changed |= self._consume_csi(byte)
        return changed

    def _start_escape(self, byte: int) -> None:
        if byte == 91:
            self._state = "csi"
            self._parameters.clear()
        elif byte in (80, 88, 93, 94, 95):
            self._state = "string"
        else:
            self._state = "text"

    def _consume_csi(self, byte: int) -> bool:
        if 0x40 <= byte <= 0x7E:
            self._state = "text"
            parameters = bytes(self._parameters)
            if byte == 112 and parameters == b"!":  # DECSTR
                return True
            if byte not in (104, 108, 114) or not parameters.startswith(b"?"):
                return False
            modes = parameters[1:].split(b";")
            return any(int(mode) in _INPUT_MODES for mode in modes if mode.isdigit())
        if len(self._parameters) < 128:
            self._parameters.append(byte)
        else:
            # Unknown oversized control: stop momentum conservatively.
            self._state = "text"
            return True
        return False
