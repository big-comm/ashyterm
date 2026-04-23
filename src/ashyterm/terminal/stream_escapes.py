# ashyterm/terminal/stream_escapes.py
"""Pure helpers for inspecting terminal escape sequences.

The streaming handler decides when to pause/resume highlighting by
peeking at specific escape sequences: alt-screen switches (vim, fzf,
htop), bracketed paste markers, and backspace characters the shell
emits to erase input. Each of those decisions is a byte-level pattern
match with no terminal state — ideal for extraction.

None of these helpers touch the terminal or mutate highlighter state.
They return plain values the caller applies back to its own state.
"""

from __future__ import annotations

from enum import Enum
from typing import Final


# ── bracketed paste markers ─────────────────────────────────

# Shell sends these when bracketed paste mode is on. Data between the
# start and end markers is user-pasted content that must not be
# highlighted as shell input.
BRACKETED_PASTE_START: Final[bytes] = b"\x1b[200~"
BRACKETED_PASTE_END: Final[bytes] = b"\x1b[201~"


def contains_bracketed_paste_start(data: bytes) -> bool:
    """True when ``data`` contains the bracketed-paste *begin* marker."""
    return BRACKETED_PASTE_START in data


def contains_bracketed_paste_end(data: bytes) -> bool:
    """True when ``data`` contains the bracketed-paste *end* marker."""
    return BRACKETED_PASTE_END in data


# ── alt screen transitions ──────────────────────────────────


class AltScreenTransition(Enum):
    """Result of inspecting a chunk for alt-screen switches."""

    NO_CHANGE = "no_change"
    ENTERED = "entered"
    EXITED = "exited"
    # Chunk contains both an enter and an exit: the caller should
    # treat the net effect as "exited" (disable wins) because the
    # alt-screen lifecycle end-state is what we care about.
    TOGGLED_ENDED = "toggled_ended"


# xterm sequences for switching to/from the alternate screen buffer.
# 1049 is the preferred modern sequence (saves cursor + clears);
# 47 is the legacy variant; 1047 is a middle-ground without cursor
# save. Detecting any of them is equivalent for our purposes.
_ALT_SCREEN_ENABLE: Final[tuple[bytes, ...]] = (
    b"\x1b[?1049h",
    b"\x1b[?47h",
    b"\x1b[?1047h",
)
_ALT_SCREEN_DISABLE: Final[tuple[bytes, ...]] = (
    b"\x1b[?1049l",
    b"\x1b[?47l",
    b"\x1b[?1047l",
)


def _contains_any(data: bytes, needles: tuple[bytes, ...]) -> bool:
    return any(needle in data for needle in needles)


def detect_alt_screen_transition(
    data: bytes, *, currently_alt: bool
) -> AltScreenTransition:
    """Classify the alt-screen transition implied by ``data``.

    ``currently_alt`` is the caller's current belief about whether the
    terminal is in alt-screen mode. The result tells the caller how to
    update that flag:

    * ``ENTERED`` — ``data`` contains an enable sequence and we were
      NOT already in alt-screen.
    * ``EXITED`` — ``data`` contains a disable sequence and we WERE
      in alt-screen.
    * ``TOGGLED_ENDED`` — ``data`` contains both enable and disable,
      and we started in alt-screen; the net effect is still "exited".
    * ``NO_CHANGE`` — state doesn't need to flip (either no sequence
      matched, or the matching sequence doesn't change our belief).
    """
    has_enable = _contains_any(data, _ALT_SCREEN_ENABLE)
    has_disable = _contains_any(data, _ALT_SCREEN_DISABLE)

    # Check enable first but let disable win when both are present —
    # the end state after a complete toggle is "exited".
    entered = has_enable and not currently_alt
    exited = has_disable and currently_alt

    if entered and exited:
        return AltScreenTransition.TOGGLED_ENDED
    if entered:
        return AltScreenTransition.ENTERED
    if exited:
        return AltScreenTransition.EXITED
    return AltScreenTransition.NO_CHANGE


# ── backspace counting ──────────────────────────────────────


def count_backspaces(data: bytes) -> int:
    """Count how many characters ``data`` would erase from input.

    Handles three forms the shell emits:

    * ``\\x7f`` — ASCII DEL (bash default erase).
    * ``\\x08`` — ASCII BS (sh/dash default erase).
    * ``\\x08 \\x08`` — the classic "backspace, space, backspace"
      sequence shells use to visually erase a character; counted as
      one erase, not three.

    Returns the total count. Callers mutate their own input buffers
    with this value (typically ``buf[:-count]``).
    """
    if not data:
        return 0

    temp = data
    count = 0

    # Pull out the BS-space-BS combos first so their BS bytes don't
    # double-count below.
    while b"\x08 \x08" in temp:
        count += 1
        temp = temp.replace(b"\x08 \x08", b"", 1)

    count += temp.count(b"\x7f") + temp.count(b"\x08")
    return count


def apply_backspaces(buffer: str, backspace_count: int) -> str:
    """Return ``buffer`` with up to ``backspace_count`` chars erased.

    Clamping means we never raise or return junk when the shell has
    emitted more backspaces than the buffer has characters (happens
    when the user holds the backspace key past the prompt boundary).
    """
    if backspace_count <= 0 or not buffer:
        return buffer
    chars_to_remove = min(backspace_count, len(buffer))
    return buffer[:-chars_to_remove] if chars_to_remove else buffer
