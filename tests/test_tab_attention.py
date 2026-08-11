"""Tests for persistent terminal tab attention state."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ashyterm.terminal.tab_attention import (
    ATTENTION_CSS_CLASS,
    PROGRESS_HINT_INACTIVE,
    ProgressAttentionTracker,
    TitleActivityTracker,
    clear_tab_attention,
    mark_tab_attention,
    title_shows_spinner,
)
from ashyterm.terminal.tabs import HAS_PROGRESS_TERMPROP, TabManager


def _stub_attention_settings(tab_manager, sound="none", color=""):
    """Give a bare TabManager the settings that marking attention reads."""
    tab_manager.terminal_manager = MagicMock()
    tab_manager.terminal_manager.settings_manager.get = MagicMock(
        side_effect=lambda key, default=None: {
            "tab_attention_sound": sound,
            "tab_attention_color": color,
        }.get(key, default)
    )
    return tab_manager


# Vte.ProgressHint values other than INACTIVE all mean "still working".
PROGRESS_HINT_ACTIVE = 1
PROGRESS_HINT_ERROR = 2
PROGRESS_HINT_INDETERMINATE = 3
PROGRESS_HINT_PAUSED = 4


def test_mark_tab_attention_adds_css_class() -> None:
    tab_widget = MagicMock()

    mark_tab_attention(tab_widget)

    tab_widget.add_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)


def test_clear_tab_attention_removes_css_class() -> None:
    tab_widget = MagicMock()

    clear_tab_attention(tab_widget)

    tab_widget.remove_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)


def test_background_terminal_bell_marks_tab_until_visited() -> None:
    page = object()
    tab_widget = MagicMock()
    tab_widget.label_widget = MagicMock()
    tab_manager = TabManager.__new__(TabManager)
    tab_manager.active_tab = MagicMock()
    tab_manager._find_tab_for_page = MagicMock(return_value=tab_widget)
    _stub_attention_settings(tab_manager)

    tab_manager._on_terminal_bell(SimpleNamespace(ashy_parent_page=page))

    tab_widget.add_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)


def test_activating_tab_clears_attention() -> None:
    previous_tab = MagicMock()
    target_tab = MagicMock()
    page = MagicMock()
    tab_manager = TabManager.__new__(TabManager)
    tab_manager.active_tab = previous_tab
    tab_manager.group_manager = MagicMock()
    tab_manager.group_manager.get_group_for_tab.return_value = None
    tab_manager.get_tab_id = MagicMock(return_value="tab-1")
    tab_manager._handle_previous_tab_focus = MagicMock()
    tab_manager.pages = {target_tab: page}
    tab_manager.view_stack = MagicMock()
    tab_manager._get_terminal_to_focus = MagicMock(return_value=None)

    tab_manager.set_active_tab(target_tab)

    target_tab.remove_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)


# ── OSC 9;4 progress tracking ──────────────────────────────


class TestProgressAttentionTracker:
    def test_finishing_after_progress_fires_once(self) -> None:
        tracker = ProgressAttentionTracker()

        assert tracker.update("t1", PROGRESS_HINT_ACTIVE) is False
        assert tracker.update("t1", PROGRESS_HINT_INACTIVE) is True

    def test_progress_updates_alone_never_fire(self) -> None:
        # A progress bar ticking must not flash the tab on every update.
        tracker = ProgressAttentionTracker()

        for hint in (
            PROGRESS_HINT_ACTIVE,
            PROGRESS_HINT_INDETERMINATE,
            PROGRESS_HINT_PAUSED,
            PROGRESS_HINT_ERROR,
        ):
            assert tracker.update("t1", hint) is False

    def test_inactive_without_prior_progress_is_ignored(self) -> None:
        tracker = ProgressAttentionTracker()

        assert tracker.update("t1", PROGRESS_HINT_INACTIVE) is False

    def test_second_inactive_does_not_fire_again(self) -> None:
        tracker = ProgressAttentionTracker()
        tracker.update("t1", PROGRESS_HINT_ACTIVE)

        assert tracker.update("t1", PROGRESS_HINT_INACTIVE) is True
        assert tracker.update("t1", PROGRESS_HINT_INACTIVE) is False

    def test_terminals_are_tracked_independently(self) -> None:
        tracker = ProgressAttentionTracker()
        tracker.update("t1", PROGRESS_HINT_ACTIVE)

        assert tracker.update("t2", PROGRESS_HINT_INACTIVE) is False
        assert tracker.update("t1", PROGRESS_HINT_INACTIVE) is True

    def test_forget_drops_pending_state(self) -> None:
        tracker = ProgressAttentionTracker()
        tracker.update("t1", PROGRESS_HINT_ACTIVE)

        tracker.forget("t1")

        assert tracker.update("t1", PROGRESS_HINT_INACTIVE) is False


# ── window-title spinner tracking ──────────────────────────

# Titles captured from a real `claude` session in a terminal it does not
# recognise: the prefix cycles through braille spinner frames while it works and
# returns to U+2733 when the task is done.
CC_IDLE_TITLE = "✳ Claude Code"
CC_WORKING_TITLE = "⠂ Claude Code"
CC_WORKING_TITLE_2 = "⠐ Confirmação de entendimento"
CC_DONE_TITLE = "✳ Confirmação de entendimento"


class TestTitleShowsSpinner:
    def test_braille_frames_count_as_working(self) -> None:
        assert title_shows_spinner(CC_WORKING_TITLE) is True
        assert title_shows_spinner(CC_WORKING_TITLE_2) is True

    def test_idle_titles_do_not(self) -> None:
        assert title_shows_spinner(CC_IDLE_TITLE) is False
        assert title_shows_spinner(CC_DONE_TITLE) is False

    def test_plain_shell_titles_do_not(self) -> None:
        assert title_shows_spinner("talesam@host: ~/projects") is False
        assert title_shows_spinner("") is False


class TestTitleActivityTracker:
    def test_spinner_then_idle_fires_once(self) -> None:
        tracker = TitleActivityTracker()

        assert tracker.update("t1", CC_IDLE_TITLE) is False
        assert tracker.update("t1", CC_WORKING_TITLE) is False
        assert tracker.update("t1", CC_WORKING_TITLE_2) is False
        assert tracker.update("t1", CC_DONE_TITLE) is True

    def test_spinner_frames_alone_never_fire(self) -> None:
        tracker = TitleActivityTracker()

        for frame in "⠂⠐⡀⣿":
            assert tracker.update("t1", f"{frame} working") is False

    def test_title_change_without_prior_spinner_is_ignored(self) -> None:
        tracker = TitleActivityTracker()

        assert tracker.update("t1", "talesam@host: ~") is False
        assert tracker.update("t1", "vim README.md") is False

    def test_consecutive_tasks_fire_once_each(self) -> None:
        tracker = TitleActivityTracker()

        tracker.update("t1", CC_WORKING_TITLE)
        assert tracker.update("t1", CC_DONE_TITLE) is True
        tracker.update("t1", CC_WORKING_TITLE)
        assert tracker.update("t1", CC_DONE_TITLE) is True

    def test_forget_drops_pending_state(self) -> None:
        tracker = TitleActivityTracker()
        tracker.update("t1", CC_WORKING_TITLE)

        tracker.forget("t1")

        assert tracker.update("t1", CC_DONE_TITLE) is False


def test_background_tab_marked_when_spinner_stops() -> None:
    tab_widget = MagicMock()
    tab_widget.label_widget = MagicMock()
    tab_manager = TabManager.__new__(TabManager)
    tab_manager.active_tab = MagicMock()
    tab_manager._find_tab_for_page = MagicMock(return_value=tab_widget)
    tab_manager._title_attention = TitleActivityTracker()
    _stub_attention_settings(tab_manager)

    terminal = MagicMock()
    terminal.ashy_parent_page = object()
    terminal.terminal_id = 3

    terminal.get_window_title.return_value = CC_WORKING_TITLE
    tab_manager._on_terminal_title_changed(terminal)
    tab_widget.add_css_class.assert_not_called()

    terminal.get_window_title.return_value = CC_DONE_TITLE
    tab_manager._on_terminal_title_changed(terminal)
    tab_widget.add_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)


def test_missing_window_title_is_treated_as_idle() -> None:
    tab_manager = TabManager.__new__(TabManager)
    tab_manager._title_attention = TitleActivityTracker()
    terminal = MagicMock()
    terminal.terminal_id = 4
    terminal.get_window_title.return_value = None

    # VTE reports no title before the app sets one; must not raise.
    tab_manager._on_terminal_title_changed(terminal)


@pytest.mark.skipif(
    not HAS_PROGRESS_TERMPROP, reason="VTE too old for progress termprops"
)
class TestTermpropAttention:
    @staticmethod
    def _tab_manager(tab_widget: MagicMock) -> TabManager:
        tab_manager = TabManager.__new__(TabManager)
        tab_manager.active_tab = MagicMock()
        tab_manager._find_tab_for_page = MagicMock(return_value=tab_widget)
        tab_manager._progress_attention = ProgressAttentionTracker()
        _stub_attention_settings(tab_manager)
        return tab_manager

    @staticmethod
    def _terminal(hint: int, found: bool = True) -> MagicMock:
        terminal = MagicMock()
        terminal.ashy_parent_page = object()
        terminal.terminal_id = 7
        terminal.get_termprop_int.return_value = (found, hint)
        return terminal

    def test_finished_task_marks_background_tab(self) -> None:
        from gi.repository import Vte

        tab_widget = MagicMock()
        tab_widget.label_widget = MagicMock()
        tab_manager = self._tab_manager(tab_widget)

        terminal = self._terminal(PROGRESS_HINT_ACTIVE)
        tab_manager._on_terminal_termprop_changed(
            terminal, Vte.TERMPROP_PROGRESS_HINT
        )
        tab_widget.add_css_class.assert_not_called()

        terminal.get_termprop_int.return_value = (True, PROGRESS_HINT_INACTIVE)
        tab_manager._on_terminal_termprop_changed(
            terminal, Vte.TERMPROP_PROGRESS_HINT
        )
        tab_widget.add_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)

    def test_reset_termprop_counts_as_finished(self) -> None:
        from gi.repository import Vte

        tab_widget = MagicMock()
        tab_widget.label_widget = MagicMock()
        tab_manager = self._tab_manager(tab_widget)

        terminal = self._terminal(PROGRESS_HINT_ACTIVE)
        tab_manager._on_terminal_termprop_changed(
            terminal, Vte.TERMPROP_PROGRESS_HINT
        )

        # Removing the progress bar reads back as an absent termprop.
        terminal.get_termprop_int.return_value = (False, 0)
        tab_manager._on_terminal_termprop_changed(
            terminal, Vte.TERMPROP_PROGRESS_HINT
        )

        tab_widget.add_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)

    def test_unrelated_termprop_is_ignored(self) -> None:
        from gi.repository import Vte

        tab_widget = MagicMock()
        tab_widget.label_widget = MagicMock()
        tab_manager = self._tab_manager(tab_widget)

        tab_manager._on_terminal_termprop_changed(
            self._terminal(PROGRESS_HINT_INACTIVE),
            Vte.TERMPROP_CURRENT_DIRECTORY_URI,
        )

        tab_widget.add_css_class.assert_not_called()

    def test_title_termprop_marks_tab_when_spinner_stops(self) -> None:
        from gi.repository import Vte

        tab_widget = MagicMock()
        tab_widget.label_widget = MagicMock()
        tab_manager = self._tab_manager(tab_widget)
        tab_manager._title_attention = TitleActivityTracker()

        terminal = self._terminal(PROGRESS_HINT_INACTIVE)
        terminal.get_termprop_string.return_value = (CC_WORKING_TITLE, 0)
        tab_manager._on_terminal_termprop_changed(
            terminal, Vte.TERMPROP_XTERM_TITLE
        )
        tab_widget.add_css_class.assert_not_called()

        terminal.get_termprop_string.return_value = (CC_DONE_TITLE, 0)
        tab_manager._on_terminal_termprop_changed(
            terminal, Vte.TERMPROP_XTERM_TITLE
        )
        tab_widget.add_css_class.assert_called_once_with(ATTENTION_CSS_CLASS)

    def test_active_tab_is_not_marked(self) -> None:
        from gi.repository import Vte

        tab_widget = MagicMock()
        tab_widget.label_widget = MagicMock()
        tab_manager = self._tab_manager(tab_widget)
        tab_manager.active_tab = tab_widget

        terminal = self._terminal(PROGRESS_HINT_ACTIVE)
        tab_manager._on_terminal_termprop_changed(
            terminal, Vte.TERMPROP_PROGRESS_HINT
        )
        terminal.get_termprop_int.return_value = (True, PROGRESS_HINT_INACTIVE)
        tab_manager._on_terminal_termprop_changed(
            terminal, Vte.TERMPROP_PROGRESS_HINT
        )

        tab_widget.add_css_class.assert_not_called()


# ── signal wiring across every tab-creation path ───────────


def _wiring_manager():
    tab_manager = TabManager.__new__(TabManager)
    tab_manager.scroll_handler = MagicMock()
    return tab_manager


def _fake_terminal():
    terminal = MagicMock()
    # A fresh Vte.Terminal has no wiring marker yet.
    del terminal._ashy_signals_connected
    return terminal


def test_connect_terminal_signals_wires_bell_and_title():
    tab_manager = _wiring_manager()
    terminal = _fake_terminal()

    tab_manager._connect_terminal_signals(terminal)

    wired = {call.args[0] for call in terminal.connect.call_args_list}
    assert "bell" in wired
    assert "contents-changed" in wired
    assert "termprop-changed" in wired or "window-title-changed" in wired


def test_connect_terminal_signals_is_idempotent():
    # Restored tabs and split panes can both reach the same terminal; wiring
    # twice would fire every handler twice.
    tab_manager = _wiring_manager()
    terminal = _fake_terminal()

    tab_manager._connect_terminal_signals(terminal)
    first = terminal.connect.call_count
    tab_manager._connect_terminal_signals(terminal)

    assert terminal.connect.call_count == first


def test_restored_tabs_wire_every_terminal():
    from ashyterm.terminal.tab_restore_controller import TabRestoreController

    source = inspect.getsource(TabRestoreController.recreate_tab_from_structure)
    assert "_connect_terminal_signals" in source


def test_split_panes_wire_their_terminal():
    from ashyterm.terminal.pane_manager import PaneManager

    source = inspect.getsource(PaneManager._create_pane_for_split)
    assert "_connect_terminal_signals" in source


# ── configurable color + sound on attention ────────────────


def _attention_manager(sound="none", color=""):
    tab_widget = MagicMock()
    tab_widget.label_widget = MagicMock()
    tab_manager = TabManager.__new__(TabManager)
    tab_manager.active_tab = MagicMock()
    tab_manager._find_tab_for_page = MagicMock(return_value=tab_widget)
    tab_manager.terminal_manager = MagicMock()
    tab_manager.terminal_manager.settings_manager.get = MagicMock(
        side_effect=lambda key, default=None: {
            "tab_attention_sound": sound,
            "tab_attention_color": color,
        }.get(key, default)
    )
    return tab_manager, tab_widget


def test_attention_plays_the_configured_sound() -> None:
    tab_manager, _tab = _attention_manager(sound="bell")

    with patch("ashyterm.terminal.tabs.play_notification_sound") as play:
        tab_manager._mark_attention_for_terminal(
            SimpleNamespace(ashy_parent_page=object())
        )

    play.assert_called_once_with("bell")


def test_attention_on_active_tab_plays_nothing() -> None:
    # Sound on a tab you are already looking at would be pure noise.
    tab_manager, tab_widget = _attention_manager(sound="bell")
    tab_manager.active_tab = tab_widget

    with patch("ashyterm.terminal.tabs.play_notification_sound") as play:
        tab_manager._mark_attention_for_terminal(
            SimpleNamespace(ashy_parent_page=object())
        )

    play.assert_not_called()


def test_configured_color_is_applied_before_flashing() -> None:
    tab_manager, tab_widget = _attention_manager(color="rgba(255,0,0,1.00)")

    # Nested rather than parenthesized: the project targets Python 3.8.
    with patch("ashyterm.terminal.tabs.play_notification_sound"), patch(
        "ashyterm.terminal.tabs._apply_attention_color_impl"
    ) as apply_color:
        tab_manager._mark_attention_for_terminal(
            SimpleNamespace(ashy_parent_page=object())
        )

    apply_color.assert_called_once_with(tab_widget, "rgba(255,0,0,1.00)")
