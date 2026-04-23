"""Tests for TabRestoreController.

Focus is on the node-walker dispatch and session-lookup logic, which
are the parts that turn persisted dicts back into real widgets. GTK
widget creation itself is exercised through ``conftest``'s mocked
``gi``, so the assertions target *what* the walker did, not what it
rendered.
"""

from unittest.mock import MagicMock

import pytest

from ashyterm.sessions.models import SessionItem
from ashyterm.terminal.tab_restore_controller import TabRestoreController


class _FakeSession:
    """Minimal SessionItem stand-in with the is_ssh/is_local contract."""

    def __init__(self, name: str, session_type: str = "local"):
        self.name = name
        self.session_type = session_type

    def is_ssh(self) -> bool:
        return self.session_type == "ssh"

    def is_local(self) -> bool:
        return self.session_type == "local"


def _make_manager(sessions=None) -> MagicMock:
    """Fake TabManager with every attribute TabRestoreController touches."""
    mgr = MagicMock()
    mgr.tabs = []
    mgr.pages = {}
    mgr.tab_bar_box = MagicMock()
    mgr.view_stack = MagicMock()
    mgr.on_tab_count_changed = MagicMock()
    mgr.close_pane = MagicMock()
    mgr._on_move_to_tab_callback = MagicMock()

    mgr.pane_handler = MagicMock()
    mgr.terminal_manager = MagicMock()
    mgr.terminal_manager.registry.get_terminal_info = MagicMock(
        return_value={"identifier": "Local"}
    )
    mgr.terminal_manager.parent_window.session_store = sessions or []
    mgr.terminal_manager.settings_manager = MagicMock()
    return mgr


@pytest.fixture
def controller():
    return TabRestoreController(_make_manager())


# ── recreate_widget_from_node dispatch ───────────────────────


class TestDispatcher:
    def test_empty_node_returns_none(self, controller):
        assert controller.recreate_widget_from_node({}) is None
        assert controller.recreate_widget_from_node(None) is None

    def test_unknown_type_returns_none(self, controller):
        assert controller.recreate_widget_from_node({"type": "banana"}) is None

    def test_terminal_dispatches_to_terminal_builder(self, controller):
        controller.recreate_terminal_node = MagicMock(return_value="TERM")
        controller.recreate_paned_node = MagicMock()
        result = controller.recreate_widget_from_node({"type": "terminal"})
        assert result == "TERM"
        controller.recreate_terminal_node.assert_called_once()
        controller.recreate_paned_node.assert_not_called()

    def test_paned_dispatches_to_paned_builder(self, controller):
        controller.recreate_terminal_node = MagicMock()
        controller.recreate_paned_node = MagicMock(return_value="PANED")
        result = controller.recreate_widget_from_node({"type": "paned"})
        assert result == "PANED"
        controller.recreate_paned_node.assert_called_once()


# ── create_terminal_from_session lookup ──────────────────────


class TestSessionLookup:
    def test_ssh_with_matching_session_spawns_ssh(self):
        target = _FakeSession("dev-box", "ssh")
        mgr = _make_manager(sessions=[target, _FakeSession("local", "local")])
        ctrl = TabRestoreController(mgr)
        mgr.terminal_manager.create_ssh_terminal.return_value = "SSH"

        out = ctrl.create_terminal_from_session(
            "ssh", "dev-box", "Dev", "/home", "cd /home"
        )

        assert out == "SSH"
        mgr.terminal_manager.create_ssh_terminal.assert_called_once_with(
            target, initial_command="cd /home"
        )

    def test_ssh_with_missing_session_falls_back_to_local(self):
        mgr = _make_manager(sessions=[])
        ctrl = TabRestoreController(mgr)
        mgr.terminal_manager.create_local_terminal.return_value = "LOCAL"

        out = ctrl.create_terminal_from_session("ssh", "gone", "Title", None, None)

        assert out == "LOCAL"
        mgr.terminal_manager.create_ssh_terminal.assert_not_called()
        mgr.terminal_manager.create_local_terminal.assert_called_once()
        # Title must be flagged as missing so the user sees the degradation.
        _, kwargs = mgr.terminal_manager.create_local_terminal.call_args
        assert "Missing:" in kwargs["title"]

    def test_ssh_session_type_mismatch_falls_back(self):
        # Stored as ssh but the session_store entry is a local session.
        local_only = _FakeSession("name", "local")
        mgr = _make_manager(sessions=[local_only])
        ctrl = TabRestoreController(mgr)
        mgr.terminal_manager.create_local_terminal.return_value = "LOCAL"

        out = ctrl.create_terminal_from_session("ssh", "name", "Title", None, None)

        assert out == "LOCAL"
        mgr.terminal_manager.create_ssh_terminal.assert_not_called()

    def test_local_passes_session_and_working_dir(self):
        target = _FakeSession("shell", "local")
        mgr = _make_manager(sessions=[target])
        ctrl = TabRestoreController(mgr)
        mgr.terminal_manager.create_local_terminal.return_value = "LOCAL"

        out = ctrl.create_terminal_from_session(
            "local", "shell", "Title", "/tmp", None
        )

        assert out == "LOCAL"
        mgr.terminal_manager.create_local_terminal.assert_called_once_with(
            session=target, title="Title", working_directory="/tmp"
        )


# ── session_from_terminal ────────────────────────────────────


class TestSessionFromTerminal:
    def test_returns_registered_session_item_unchanged(self):
        mgr = _make_manager()
        session = SessionItem(name="web", session_type="ssh")
        mgr.terminal_manager.registry.get_terminal_info.return_value = {
            "identifier": session
        }
        ctrl = TabRestoreController(mgr)

        out = ctrl.session_from_terminal(MagicMock(terminal_id="abc"))

        assert out is session

    def test_wraps_string_identifier_as_local_session(self):
        mgr = _make_manager()
        mgr.terminal_manager.registry.get_terminal_info.return_value = {
            "identifier": "Local Shell"
        }
        ctrl = TabRestoreController(mgr)

        out = ctrl.session_from_terminal(MagicMock(terminal_id="abc"))

        assert isinstance(out, SessionItem)
        assert out.name == "Local Shell"
        assert out.is_local()

    def test_no_registry_info_yields_fallback(self):
        mgr = _make_manager()
        mgr.terminal_manager.registry.get_terminal_info.return_value = None
        ctrl = TabRestoreController(mgr)

        out = ctrl.session_from_terminal(MagicMock(terminal_id="abc"))

        assert out.name == "Local"


# ── find_and_remove_terminals ────────────────────────────────


class TestFindAndRemoveTerminals:
    def test_unregisters_every_terminal_found(self):
        mgr = _make_manager()

        def fake_finder(widget, bucket):
            bucket.extend(["t1", "t2", "t3"])

        mgr.pane_handler.find_terminals_recursive.side_effect = fake_finder
        ctrl = TabRestoreController(mgr)

        ctrl.find_and_remove_terminals(MagicMock())

        assert mgr.terminal_manager.remove_terminal.call_count == 3


# ── recreate_tab_from_structure guards ───────────────────────


class TestRecreateTabGuards:
    def test_empty_structure_is_noop(self, controller):
        controller.recreate_tab_from_structure({})
        controller.recreate_tab_from_structure(None)
        controller.manager.view_stack.add_titled.assert_not_called()

    def test_bails_when_root_widget_cannot_be_built(self):
        mgr = _make_manager()
        ctrl = TabRestoreController(mgr)
        ctrl.recreate_widget_from_node = MagicMock(return_value=None)

        ctrl.recreate_tab_from_structure({"type": "terminal"})

        mgr.view_stack.add_titled.assert_not_called()

    def test_bails_when_walker_finds_no_terminals(self):
        mgr = _make_manager()
        ctrl = TabRestoreController(mgr)
        ctrl.recreate_widget_from_node = MagicMock(return_value=MagicMock())
        # Skip the real Gtk-widget construction for this guard test —
        # the assertion is about short-circuiting when the terminal
        # bucket stays empty.
        ctrl.unwrap_toolbar_view = MagicMock(return_value=MagicMock())
        ctrl.build_tab_content_paned = MagicMock(
            return_value=(MagicMock(), MagicMock())
        )
        mgr.pane_handler.find_terminals_recursive.side_effect = (
            lambda w, bucket: None  # never populates
        )

        ctrl.recreate_tab_from_structure({"type": "terminal"})

        mgr.view_stack.add_titled.assert_not_called()


# ── close_all_tabs ───────────────────────────────────────────


class TestCloseAllTabs:
    def test_iterates_over_snapshot_of_tabs(self):
        mgr = _make_manager()
        tabs = [MagicMock(name=f"tab-{i}") for i in range(3)]
        mgr.tabs = tabs[:]

        def fake_close(btn, tab):
            mgr.tabs.remove(tab)

        mgr._on_tab_close_button_clicked.side_effect = fake_close
        ctrl = TabRestoreController(mgr)

        ctrl.close_all_tabs()

        # Snapshot-iteration must have closed every original tab even
        # though the list mutated during iteration.
        assert mgr._on_tab_close_button_clicked.call_count == 3
        assert mgr.tabs == []


# ── unwrap_toolbar_view ──────────────────────────────────────


class TestUnwrapToolbarView:
    def test_non_toolbar_passes_through(self, controller):
        widget = MagicMock(__class__=MagicMock(__name__="Box"))
        # isinstance(widget, Adw.ToolbarView) is False because the mock
        # Adw module returns a fresh MagicMock for ToolbarView.
        out = controller.unwrap_toolbar_view(widget)
        assert out is widget


# ── integration: TabManager exposes the surface ──────────────


class TestTabManagerIntegration:
    def test_tab_manager_has_restore_controller_surface(self):
        from ashyterm.terminal.tabs import TabManager

        # Public methods external state-restore code relies on.
        for name in ("recreate_tab_from_structure", "close_all_tabs"):
            assert hasattr(TabManager, name)

    def test_recreate_delegates_to_controller(self):
        from ashyterm.terminal.tabs import TabManager

        mgr = object.__new__(TabManager)
        mgr.restore_controller = MagicMock()
        TabManager.recreate_tab_from_structure(mgr, {"type": "terminal"})
        mgr.restore_controller.recreate_tab_from_structure.assert_called_once_with(
            {"type": "terminal"}
        )

    def test_close_all_delegates_to_controller(self):
        from ashyterm.terminal.tabs import TabManager

        mgr = object.__new__(TabManager)
        mgr.restore_controller = MagicMock()
        TabManager.close_all_tabs(mgr)
        mgr.restore_controller.close_all_tabs.assert_called_once_with()
