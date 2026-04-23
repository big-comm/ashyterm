# ashyterm/terminal/shell_prompt_detect.py
"""Pure helpers for shell prompt and readline-activity detection.

The streaming handler decides whether to apply syntax highlighting,
buffer input, or bail out by peeking at the bytes the shell emits.
The byte-level heuristics are pure — they don't touch the terminal or
highlighter state — so they live here where they can be covered by
tests without a live PTY.
"""

from __future__ import annotations

from .highlighter.constants import SHELL_NAME_PROMPT_PATTERN

# Readline/ANSI control byte sequences that indicate the shell is
# rewriting the visible line (history recall, tab completion, resize…).
# Detecting these lets us bail out of highlighting — we'd otherwise
# fight readline for cursor position.
READLINE_SEQUENCES: frozenset[bytes] = frozenset(
    (
        b"\x1b[D",       # Cursor left
        b"\x1b[C",       # Cursor right
        b"\x1b[K",       # Erase to end of line
        b"\x1b[0K",      # Erase to end of line (explicit)
        b"\x1b[1D",      # Move cursor left 1
        b"\x1b[1C",      # Move cursor right 1
        b"\x1b[A",       # Cursor up
        b"\x1b[B",       # Cursor down
        b"\x1b[J",       # Erase display
        b"\x1b[0J",      # Erase display (explicit)
        b"\x1b[H",       # Cursor home
        b"\x1b[?25l",    # Hide cursor
        b"\x1b[?25h",    # Show cursor
        b"\x1b[P",       # Delete char
        b"\x1b[@",       # Insert char
    )
)

# Bash/Zsh incremental search prompts (Ctrl-R).
SEARCH_PROMPT_PATTERNS: tuple[bytes, ...] = (
    b"(reverse-i-search)",
    b"(i-search)",
    b"(bck-i-search)",
    b"(fwd-i-search)",
    b"(failed ",
)

FANCY_PROMPT_CHARS: tuple[str, ...] = ("❯", "➜", "λ", "›")
TRADITIONAL_PROMPT_CHARS: tuple[str, ...] = ("#", "%")

# Characters that plausibly terminate a prompt; used to short-circuit
# detection before we bother stripping escape codes.
PROMPT_TRIGGER_CHARS: str = "$#%>❯"


def is_readline_redraw(data: bytes) -> bool:
    """Return True if ``data`` looks like a readline-driven line redraw.

    A lone ``\\r`` is enough — readline uses it to reset the cursor to
    the start of the line — or any of the canned ANSI erase/cursor
    sequences above, or an incremental-search prompt.
    """
    if b"\r" in data:
        return True
    if any(seq in data for seq in READLINE_SEQUENCES):
        return True
    return any(pat in data for pat in SEARCH_PROMPT_PATTERNS)


def looks_like_prompt(text: str) -> bool:
    """Loose check: does ``text`` resemble a shell prompt?

    Used at streaming boundaries where we haven't yet stripped ANSI
    codes — hence the mild heuristic (``@``/``:``/trailing path sep)
    plus the stricter ``SHELL_NAME_PROMPT_PATTERN`` regex.
    """
    return bool(
        SHELL_NAME_PROMPT_PATTERN.match(text)
        or "@" in text
        or ":" in text
        or text.endswith("~")
        or text.endswith("/")
    )


def is_valid_traditional_prompt(prompt_part: str) -> bool:
    """Stricter variant used when we've already seen a ``#`` / ``%`` trailer.

    The prefix must look like one of: a named-shell prompt (``bash-5.2``),
    a home/tilde, an absolute path, or a ``user@host`` / ``user:path``
    fragment. Anything else is probably just the character ``#`` inside
    command output.
    """
    if SHELL_NAME_PROMPT_PATTERN.match(prompt_part):
        return True
    return (
        prompt_part.endswith("~")
        or prompt_part.endswith("/")
        or "@" in prompt_part
        or ":" in prompt_part
    )


def extract_line_content_and_ending(line: str) -> tuple[str, str]:
    """Split a line into ``(content, terminator)`` where terminator is
    one of ``""`` / ``"\\r"`` / ``"\\n"`` / ``"\\r\\n"``.

    Empty lines come back as ``("", "")``.
    """
    if not line:
        return "", ""
    if line[-1] == "\n":
        if len(line) > 1 and line[-2] == "\r":
            return line[:-2], "\r\n"
        return line[:-1], "\n"
    if line[-1] == "\r":
        return line[:-1], "\r"
    return line, ""


def extract_last_line(text: str) -> str:
    """Return the last logical line of ``text`` with whitespace trimmed.

    Robust to both ``\\n`` and ``\\r`` line endings so a ``\\r``-redraw
    (common inside progress bars) doesn't inflate the prompt detector.
    """
    last_line = text.rsplit("\n", 1)[-1].strip()
    return last_line.rsplit("\r", 1)[-1].strip()


# Arrow-key sequences that get echoed as literal ASCII when the shell
# isn't putting the terminal in raw mode — shell-input highlighting
# must ignore them so it doesn't color "[A" as a command.
_LITERAL_ARROW_KEYS: frozenset[str] = frozenset(
    {"[A", "[B", "[C", "[D", "[H", "[F"}
)
_CONTINUATION_PROMPTS: frozenset[str] = frozenset({">", "> "})


def is_valid_shell_input(text: str) -> bool:
    """Return True if ``text`` is safe to run through the shell-input
    highlighter.

    Rejects anything containing non-printable characters, literal escape
    markers (``^[``), echoed arrow keys, and continuation prompts — all
    of which would either break the lexer or produce misleading color.
    """
    if not all(c.isprintable() or c == " " for c in text):
        return False
    if "^[" in text:
        return False
    if text in _LITERAL_ARROW_KEYS:
        return False
    if text.strip() in _CONTINUATION_PROMPTS:
        return False
    return True
