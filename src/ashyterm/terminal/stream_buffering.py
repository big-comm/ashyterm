# ashyterm/terminal/stream_buffering.py
"""Byte-level buffering rules for the streaming handler.

The real streaming pipeline lives in :mod:`_streaming_handler`, which
holds all the ``_burst_counter`` / ``_partial_line_buffer`` state.
The decisions it makes — "is this a burst?", "should I buffer the
tail?", "is this remainder interactive?" — are pure and live here
where tests can hit them without a terminal.
"""

from __future__ import annotations

from typing import Tuple

# Over this many bytes in a single chunk, we stop trying to highlight
# and just forward the data. 1 MiB is plenty of screenfuls; anything
# bigger is likely ``cat`` of a huge file or a ``yes`` loop.
HARD_BYTES_LIMIT = 1 << 20  # 1 MiB

# Chunks larger than this increment the burst counter; smaller chunks
# reset it. Rough proxy for "is the process spewing".
BURST_CHUNK_MIN = 1024

# When the burst counter hits the hard cap in :func:`classify_burst`
# we seed the counter at this value so the next few chunks stay in
# the fast-path without flapping back to the slow path.
BURST_HARD_HIT_SEED = 100

# How many bytes of remainder to even bother decoding when deciding
# whether the tail looks interactive. Anything longer than this is
# almost certainly real output the shell sent without a trailing
# newline (e.g. long ``echo -n`` streams).
REMAINDER_DECODE_LIMIT = 200


def classify_burst(
    data_len: int, burst_counter: int, threshold: int
) -> Tuple[int, bool]:
    """Update ``burst_counter`` for a chunk of ``data_len`` bytes.

    Returns ``(new_counter, is_burst)``. ``is_burst`` means the caller
    should flush the queue and feed the chunk raw. Rules:

    * ``data_len`` exceeds :data:`HARD_BYTES_LIMIT` ⇒ counter pinned to
      :data:`BURST_HARD_HIT_SEED`, ``is_burst=True``.
    * Otherwise the counter increments when ``data_len`` is over
      :data:`BURST_CHUNK_MIN` and resets when it's below, then we
      compare against ``threshold``.
    """
    if data_len > HARD_BYTES_LIMIT:
        return BURST_HARD_HIT_SEED, True

    if data_len > BURST_CHUNK_MIN:
        burst_counter += 1
    else:
        burst_counter = 0

    return burst_counter, burst_counter > threshold


def is_remainder_interactive(rem_str: str) -> bool:
    """Return True when ``rem_str`` looks like a prompt tail.

    Any of: a prompt terminator (``$ # % > :``) at the end, a prompt
    character followed by space (current input in progress), or an
    embedded ANSI/OSC escape (prompt styling or CWD update) counts
    as "interactive" and should not be buffered.
    """
    stripped = rem_str.strip()
    if stripped.endswith(("$", "#", "%", ">", ":")):
        return True
    if any(t in rem_str for t in ("$ ", "# ", "% ", "> ")):
        return True
    if "\x1b[" in rem_str or "\x1b]7;" in rem_str or "\033]7;" in rem_str:
        return True
    return False


def split_partial_line(
    data: bytes, *, at_shell_prompt: bool
) -> Tuple[bytes, bytes]:
    """Split ``data`` into ``(emit, buffer)`` honoring partial-line rules.

    ``emit`` is the prefix of ``data`` that ends at the last newline —
    safe to highlight + forward. ``buffer`` is the trailing partial
    line to stash for the next chunk.

    ``buffer`` stays empty when:
    * ``data`` has no ``\\n`` (there's nothing complete to emit yet —
      caller keeps the whole chunk as the partial for next round).
    * The last ``\\n`` is the final byte (nothing to buffer).
    * The remainder looks interactive (prompt ending / escape) — we
      must forward it so the user sees the prompt drawing.
    * ``at_shell_prompt`` is True — we're already inside an input
      context where buffering would swallow keystrokes.

    When no split is requested, returns ``(data, b"")``.
    """
    data_len = len(data)
    last_newline = data.rfind(b"\n")
    if last_newline == -1 or last_newline >= data_len - 1:
        return data, b""

    remainder = data[last_newline + 1 :]
    interactive = False
    if len(remainder) < REMAINDER_DECODE_LIMIT:
        rem_str = remainder.decode("utf-8", errors="ignore")
        interactive = is_remainder_interactive(rem_str)

    if interactive or at_shell_prompt:
        return data, b""

    return data[: last_newline + 1], remainder


def should_skip_line_highlight(
    line: str, *, index: int, skip_first: bool
) -> bool:
    """Should the highlighter skip ``line`` (emit it as-is)?

    Three cases:

    * ``skip_first`` and ``index == 0`` — the first line of a chunk
      is the continuation of a line we already emitted.
    * The line is empty or a bare line terminator.
    * The line embeds an OSC7 sequence (CWD update) — highlighting
      those breaks the escape parser.
    """
    if skip_first and index == 0:
        return True
    if not line or line in ("\n", "\r", "\r\n"):
        return True
    if "\x1b]7;" in line or "\033]7;" in line:
        return True
    return False
