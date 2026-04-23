# ashyterm/terminal/tab_titles.py
"""Pure helpers for composing tab titles.

The tab title visible to the user is derived from a handful of
inputs: the tab's ``_base_title`` (set at creation), whether the tab
is local or remote, the current incoming title (usually the VTE
"window title" for the terminal), and how many terminals the tab
hosts. The rules that map these to a single string are pure
transformations and live here so ``tabs.py`` stays focused on the
GTK wiring.
"""

from __future__ import annotations


def build_display_title(
    *,
    base_title: str,
    new_title: str,
    is_local: bool,
) -> str:
    """Compose the tab-title text from the incoming update.

    Rules:

    * **Local tabs** — always show ``new_title`` verbatim. Local
      terminals are identified by their cwd, so the incoming title
      is already the full label.
    * **Remote tabs** — prefix with ``base_title:`` so the user can
      tell sessions apart when working in multiple tabs, unless:
        * ``new_title`` already starts with ``"{base_title}:"`` —
          the caller already prefixed it;
        * ``new_title`` equals ``base_title`` — the incoming update
          is the session name itself, no path info worth appending.
    """
    if is_local:
        return new_title

    if new_title.startswith(base_title + ":"):
        return new_title

    if new_title == base_title:
        return base_title

    return f"{base_title}: {new_title}"


def append_terminal_count(display_title: str, terminal_count: int) -> str:
    """Append ``(N)`` to ``display_title`` when more than one terminal.

    A tab can host a split pane with multiple terminals; this is the
    indicator that tells the user they have more than one. Single
    terminal tabs keep a clean title so short-named sessions aren't
    cluttered with ``(1)``.
    """
    if terminal_count > 1:
        return f"{display_title} ({terminal_count})"
    return display_title
