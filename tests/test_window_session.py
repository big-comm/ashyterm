"""Window side of the session snapshot: who restores, who decides on close."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from ashyterm.window_session import WindowSessionMixin


class FakeWindow(WindowSessionMixin):
    def __init__(self, app, tab_count=2, policy="ask", **launch):
        self._app = app
        self.state_manager = MagicMock()
        self.state_manager.restore_window.return_value = True
        self.tab_manager = SimpleNamespace(get_tab_count=lambda: tab_count)
        self.settings_manager = SimpleNamespace(get=lambda key, default=None: policy)
        self.logger = MagicMock()
        self._cleanup_performed = False
        self.restored_window_state = launch.pop("restored_window_state", None)
        self.initial_ssh_target = launch.get("ssh")
        self.initial_execute_command = launch.get("command")
        self.initial_working_directory = launch.get("directory")

    def get_application(self):
        return self._app


def make_app(saved_windows=None):
    app = SimpleNamespace(windows=[])
    app.session_autosaver = MagicMock()
    app.session_autosaver.claim_restore.return_value = saved_windows
    app.get_windows = lambda: app.windows
    app.create_new_window = MagicMock()
    return app


class TestRestore:
    def test_first_window_restores_and_reopens_the_others(self):
        saved = [{"tabs": [1]}, {"tabs": [2]}, {"tabs": [3]}]
        app = make_app(saved)
        window = FakeWindow(app)

        assert window._restore_saved_session() is True
        window.state_manager.restore_window.assert_called_once_with(saved[0])
        reopened = [c.kwargs["restored_window_state"] for c in app.create_new_window.call_args_list]
        assert reopened == saved[1:]
        app.session_autosaver.finish_restore.assert_called_once()

    def test_later_windows_open_empty(self):
        app = make_app(None)  # the session was already claimed
        window = FakeWindow(app)
        assert window._restore_saved_session() is False
        window.state_manager.restore_window.assert_not_called()
        app.session_autosaver.finish_restore.assert_not_called()

    def test_reopened_window_restores_its_own_state_once(self):
        app = make_app()
        window = FakeWindow(app, restored_window_state={"tabs": [2]})
        assert window._restore_saved_session() is True
        window.state_manager.restore_window.assert_called_once_with({"tabs": [2]})
        app.session_autosaver.window_restored.assert_called_once()
        app.session_autosaver.claim_restore.assert_not_called()
        assert window.restored_window_state is None

    def test_failed_reopen_does_not_block_snapshots(self):
        app = make_app([{"tabs": [1]}, {"tabs": [2]}])
        app.create_new_window.side_effect = RuntimeError("no display")
        FakeWindow(app)._restore_saved_session()
        app.session_autosaver.window_restored.assert_called_once()

    def test_launch_request_is_detected(self):
        app = make_app()
        assert not FakeWindow(app)._has_launch_request()
        assert FakeWindow(app, command="htop")._has_launch_request()
        assert FakeWindow(app, directory="/tmp")._has_launch_request()
        assert FakeWindow(app, ssh="user@host")._has_launch_request()


class TestCloseChoice:
    def test_last_window_applies_the_dialog_answer(self):
        app = make_app()
        window = FakeWindow(app)
        app.windows = [window]
        window._session_close_response = "save"
        window._apply_session_close_choice()
        app.session_autosaver.close_last_window.assert_called_once_with(True)

    def test_last_window_follows_the_policy_without_a_dialog(self):
        app = make_app()
        window = FakeWindow(app, policy="just_close")
        app.windows = [window]
        window._session_close_response = None
        window._apply_session_close_choice()
        app.session_autosaver.close_last_window.assert_called_once_with(False)

    def test_other_windows_leave_the_session_alone(self):
        app = make_app()
        window, other = FakeWindow(app), FakeWindow(app)
        app.windows = [window, other]
        window._session_close_response = "close"
        window._apply_session_close_choice()
        app.session_autosaver.close_last_window.assert_not_called()

    def test_a_window_already_closing_does_not_count(self):
        app = make_app()
        window, other = FakeWindow(app), FakeWindow(app)
        other._cleanup_performed = True
        app.windows = [window, other, SimpleNamespace()]  # plus a dialog
        assert window._is_last_open_window()
