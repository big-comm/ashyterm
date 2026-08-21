"""Persistent visual attention state for terminal tabs."""

from typing import Hashable, Protocol, Set


ATTENTION_CSS_CLASS = "tab-bell"

# Mirrors Vte.ProgressHint.INACTIVE (OSC 9;4 state 0). Kept as a plain int so
# this module stays importable — and unit-testable — without a VTE stack.
PROGRESS_HINT_INACTIVE = 0

# Code point ranges CLIs animate spinners with. A tool that keeps a spinner in
# its window title advertises "still working" on a channel every terminal
# already receives, which is what lets tab attention work without the tool
# being configured for this terminal.
#
# Several families are covered on purpose: matching only one is fragile. Claude
# Code shipped braille frames up to 2.1.220 and switched to circle halves in
# 2.1.238, which silently broke detection until this list grew. These are the
# families used by the common spinner libraries (cli-spinners, indicatif, rich).
_SPINNER_RANGES = (
    (0x2800, 0x28FF),  # Braille Patterns — "dots", the most common
    (0x25D0, 0x25D3),  # ◐◑◒◓ circle halves — Claude Code 2.1.238+
    (0x25F4, 0x25F7),  # ◴◵◶◷ circle quarters
    (0x25DC, 0x25DF),  # ◜◝◞◟ arcs
    (0x2596, 0x259F),  # ▖▘▝▗ quadrants
    (0x2581, 0x2588),  # ▁▃▄▅▆▇ growing bars
    (0x2B12, 0x2B15),  # ⬒⬓⬔⬕ half-filled squares
    (0x25E2, 0x25E5),  # ◢◣◤◥ triangles
)

# U+2733 (✳) is deliberately absent: Claude Code uses it for the *idle* state,
# so counting it as a spinner would mean the tab never stops looking busy.


def title_shows_spinner(title: str) -> bool:
    """True when ``title`` carries a spinner frame from any known family."""
    return any(
        first <= ord(char) <= last
        for char in title
        for first, last in _SPINNER_RANGES
    )


class CssClassWidget(Protocol):
    """Widget subset required by the attention state helpers."""

    def add_css_class(self, css_class: str) -> None: ...

    def remove_css_class(self, css_class: str) -> None: ...


def mark_tab_attention(tab_widget: CssClassWidget) -> None:
    """Mark a background tab as requiring attention."""
    tab_widget.add_css_class(ATTENTION_CSS_CLASS)


def clear_tab_attention(tab_widget: CssClassWidget) -> None:
    """Clear attention after the tab has been visited."""
    tab_widget.remove_css_class(ATTENTION_CSS_CLASS)


class _BusyEdgeTracker:
    """Fire exactly once when a terminal goes from busy back to idle.

    Both attention sources are edge-triggered for the same reason: a tool
    reports "busy" repeatedly while it works, so anything but the falling edge
    would flash the tab on every update. A terminal never seen busy is ignored,
    since an idle report on its own carries no completion to announce.
    """

    def __init__(self) -> None:
        self._busy: Set[Hashable] = set()

    def _update(self, key: Hashable, busy: bool) -> bool:
        if busy:
            self._busy.add(key)
            return False

        if key not in self._busy:
            return False
        self._busy.discard(key)
        return True

    def forget(self, key: Hashable) -> None:
        """Drop tracked state for a terminal that is going away."""
        self._busy.discard(key)


class ProgressAttentionTracker(_BusyEdgeTracker):
    """Turn a stream of OSC 9;4 progress hints into one event per finished task."""

    def update(self, key: Hashable, hint: int) -> bool:
        """Feed a progress hint; True when ``key`` just finished a task."""
        return self._update(key, hint != PROGRESS_HINT_INACTIVE)


class TitleActivityTracker(_BusyEdgeTracker):
    """Turn window-title updates into one event per finished task.

    This is the only source that needs no cooperation beyond what tools already
    do: the title is set unconditionally, whereas BEL and OSC 9;4 depend on the
    tool being configured for — or recognising — this terminal.
    """

    def update(self, key: Hashable, title: str) -> bool:
        """Feed a window title; True when ``key`` just stopped working."""
        return self._update(key, title_shows_spinner(title))
