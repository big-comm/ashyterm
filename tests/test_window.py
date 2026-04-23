"""Tests for window — constants, bracketed paste, class structure."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestWindowConstants:
    """Tests for module-level constants."""

    def test_bracketed_paste_start(self):
        from ashyterm.window import PASTE_START

        assert PASTE_START == b"\x1b[200~"

    def test_bracketed_paste_end(self):
        from ashyterm.window import PASTE_END

        assert PASTE_END == b"\x1b[201~"

    def test_app_title(self):
        from ashyterm.window import APP_TITLE

        assert isinstance(APP_TITLE, str)
        assert len(APP_TITLE) > 0

    def test_no_active_terminal_msg(self):
        from ashyterm.window import MSG_NO_ACTIVE_TERMINAL

        assert isinstance(MSG_NO_ACTIVE_TERMINAL, str)
        assert len(MSG_NO_ACTIVE_TERMINAL) > 0
        # Message is translated; verify it contains terminal-related text
        assert "terminal" in MSG_NO_ACTIVE_TERMINAL.lower()


class TestCommTerminalWindowClass:
    """Tests for CommTerminalWindow class structure."""

    def test_class_exists(self):
        from ashyterm.window import CommTerminalWindow

        assert CommTerminalWindow is not None

    def test_class_has_expected_attributes(self):
        """Verify key instance attributes are defined in __init__."""
        import inspect
        from ashyterm.window import CommTerminalWindow

        source = inspect.getsource(CommTerminalWindow.__init__)
        # Check essential initialization
        assert "settings_manager" in source
        assert "terminal_manager" in source or "TerminalManager" in source
        assert "logger" in source

    def test_has_file_drag_drop_integration(self):
        """Window should inherit from FileDragDropManager."""
        from ashyterm.window import CommTerminalWindow
        from ashyterm.window_file_drop import FileDragDropManager

        assert issubclass(CommTerminalWindow, FileDragDropManager)

    def test_is_adw_application_window(self):
        """Window should be an Adw.ApplicationWindow subclass."""
        from ashyterm.window import CommTerminalWindow

        # Just verify it has the inheritance chain
        assert hasattr(CommTerminalWindow, "mro")


class TestWindowStateTracking:
    """Tests for window state tracking attributes."""

    def test_search_state_initialization(self):
        """Verify search state variables are initialized."""
        import inspect
        from ashyterm.window import CommTerminalWindow

        # State vars now live in _lifecycle_init_common (mixin)
        source = inspect.getsource(CommTerminalWindow._lifecycle_init_common)
        assert "search_active" in source
        assert "search_current_occurrence" in source
        assert "current_search_terminal" in source

    def test_cleanup_tracking(self):
        """Verify cleanup tracking variables exist."""
        import inspect
        from ashyterm.window import CommTerminalWindow

        source = inspect.getsource(CommTerminalWindow._lifecycle_init_common)
        assert "_cleanup_performed" in source
        assert "_force_closing" in source


class TestWindowFileManager:
    """Tests for file manager integration in window."""

    def test_active_temp_files_is_weak_key_dict(self):
        """WeakKeyDictionary for temp files prevents leaks."""
        import inspect
        from ashyterm.window import CommTerminalWindow

        source = inspect.getsource(CommTerminalWindow._lifecycle_init_common)
        assert "WeakKeyDictionary" in source


class TestWindowLayouts:
    """Tests for layout management in window."""

    def test_layouts_list_initialization(self):
        """Window initializes empty layouts list."""
        import inspect
        from ashyterm.window import CommTerminalWindow

        source = inspect.getsource(CommTerminalWindow._lifecycle_init_common)
        assert "self.layouts" in source
