"""Persistent visual attention state for terminal tabs."""

from typing import Hashable, Protocol, Set


ATTENTION_CSS_CLASS = "tab-bell"

# Mirrors Vte.ProgressHint.INACTIVE (OSC 9;4 state 0). Kept as a plain int so
# this module stays importable — and unit-testable — without a VTE stack.
PROGRESS_HINT_INACTIVE = 0

# Braille Patterns. Modern CLIs animate spinners with these code points, and
# tools that keep a spinner in the window title (Claude Code, for one) thereby
# advertise "still working" on a channel every terminal already receives.
_SPINNER_FIRST = 0x2800
_SPINNER_LAST = 0x28FF


def title_shows_spinner(title: str) -> bool:
    """True when ``title`` carries a braille spinner frame."""
    return any(_SPINNER_FIRST <= ord(char) <= _SPINNER_LAST for char in title)


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
