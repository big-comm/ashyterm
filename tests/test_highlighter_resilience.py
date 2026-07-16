"""Regression tests for output highlighter fault containment."""

import threading
import time
from unittest.mock import MagicMock

from ashyterm.terminal.highlighter.output import OutputHighlighter
from ashyterm.terminal.highlighter.rules import CompiledRule
from ashyterm.utils.re_engine import engine as re_engine


def _make_highlighter() -> OutputHighlighter:
    highlighter = OutputHighlighter.__new__(OutputHighlighter)
    highlighter.logger = MagicMock()
    highlighter._lock = threading.Lock()
    highlighter._proxy_contexts = {}
    highlighter._full_commands = {}
    highlighter._skip_first_output = {}
    highlighter._ignored_commands = frozenset()
    highlighter._context_rules_cache = {}
    highlighter._context_compile_pending = set()
    highlighter._rules_generation = 0
    highlighter._disabled_rule_ids = set()
    highlighter._global_rules = ("global",)
    highlighter._manager = MagicMock()
    highlighter._manager.context_aware_enabled = True
    highlighter._manager.get_context_for_command.return_value = None
    return highlighter


def test_unknown_commands_use_global_rules_without_cache_growth() -> None:
    highlighter = _make_highlighter()

    for proxy_id in range(100):
        highlighter.register_proxy(proxy_id)
        highlighter.set_context(f"unknown-{proxy_id}", proxy_id)
        context, rules = highlighter.get_context_and_rules(proxy_id)
        assert context == ""
        assert rules == ("global",)

    assert highlighter._context_rules_cache == {}
    assert highlighter._context_compile_pending == set()
    highlighter._manager.get_rules_for_context.assert_not_called()


def test_context_compilation_cannot_block_caller_or_other_tab() -> None:
    highlighter = _make_highlighter()
    highlighter._manager.get_context_for_command.side_effect = (
        lambda command: "slow" if command == "slow" else None
    )
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def compile_slow_context(_prepared_rules: tuple) -> tuple[str, ...]:
        started.set()
        release.wait(timeout=2)
        finished.set()
        return ("compiled",)

    highlighter._manager.get_rules_for_context.return_value = []
    highlighter._compile_prepared_rules = compile_slow_context
    highlighter.register_proxy(1)
    highlighter.set_context("slow", 1)

    before = time.monotonic()
    context, rules = highlighter.get_context_and_rules(1)
    elapsed = time.monotonic() - before

    assert context == "slow"
    assert rules == ("global",)
    assert elapsed < 0.2
    assert started.wait(timeout=1)

    highlighter.register_proxy(2)
    highlighter.set_context("ordinary text", 2)
    assert highlighter.get_context_and_rules(2) == ("", ("global",))

    release.set()
    assert finished.wait(timeout=1)


def test_regex_timeout_disables_only_the_failing_rule() -> None:
    highlighter = _make_highlighter()
    pattern = re_engine.compile(r"(a+)+$")
    rule = CompiledRule(pattern, ("\033[31m",), "next", 1, None)
    matches: list[tuple[int, int, str]] = []

    before = time.monotonic()
    assert not highlighter._process_compiled_rule(
        rule, "a" * 10_000 + "!", "a" * 10_000 + "!", matches
    )
    elapsed = time.monotonic() - before

    assert elapsed < 0.5
    assert id(rule) in highlighter._disabled_rule_ids
    highlighter.logger.warning.assert_called_once()

    before = time.monotonic()
    assert not highlighter._process_compiled_rule(rule, "aaaa", "aaaa", matches)
    assert time.monotonic() - before < 0.05
