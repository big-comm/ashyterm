# tests/test_ai_history_manager.py
"""Tests for AIHistoryManager — conversation CRUD and persistence."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def history_dir(tmp_path):
    return tmp_path


@pytest.fixture
def manager(history_dir):
    """Create a fresh AIHistoryManager with a temp directory."""
    mock_paths = MagicMock()
    mock_paths.CONFIG_DIR = history_dir

    with patch.dict(
        "sys.modules",
        {
            "ashyterm.settings.config": MagicMock(
                get_config_paths=MagicMock(return_value=mock_paths)
            ),
            "ashyterm.utils.logger": MagicMock(
                get_logger=MagicMock(return_value=MagicMock())
            ),
            "ashyterm.utils.security": MagicMock(
                atomic_json_write=_fake_atomic_write
            ),
        },
    ):
        # Clear any previously cached module
        import sys

        sys.modules.pop("ashyterm.data.ai_history_manager", None)
        from ashyterm.data.ai_history_manager import AIHistoryManager

        mgr = AIHistoryManager()
        return mgr


def _fake_atomic_write(path, data):
    """Simple JSON writer that replaces atomic_json_write for testing."""
    import json

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class TestAIHistoryManager:
    def test_starts_empty(self, manager):
        assert manager.get_history() == []
        assert manager.get_all_conversations() == []

    def test_new_conversation(self, manager):
        conv = manager.new_conversation()
        assert "id" in conv
        assert conv["messages"] == []
        assert manager._current_conversation_id == conv["id"]

    def test_add_user_message(self, manager):
        manager.new_conversation()
        manager.add_user_message("hello")
        history = manager.get_history()
        assert len(history) == 1
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "hello"

    def test_add_assistant_message_with_commands(self, manager):
        manager.new_conversation()
        manager.add_assistant_message("Here's the result", commands=["ls -la"])
        history = manager.get_history()
        assert len(history) == 1
        assert history[0]["role"] == "assistant"
        assert history[0]["commands"] == ["ls -la"]

    def test_empty_message_ignored(self, manager):
        manager.new_conversation()
        manager.add_user_message("")
        manager.add_user_message("   ")
        assert manager.get_history() == []

    def test_get_recent_history(self, manager):
        manager.new_conversation()
        for i in range(10):
            manager.add_user_message(f"msg {i}")

        recent = manager.get_recent_history(3)
        assert len(recent) == 3
        assert recent[0]["content"] == "msg 7"
        assert recent[2]["content"] == "msg 9"

    def test_clear_history(self, manager):
        manager.new_conversation()
        manager.add_user_message("test")
        manager.clear_history()
        assert manager.get_history() == []
        # Conversation still exists, just empty
        assert manager._current_conversation_id is not None

    def test_delete_conversation(self, manager):
        conv = manager.new_conversation()
        conv_id = conv["id"]
        manager.add_user_message("test")

        result = manager.delete_conversation(conv_id)
        assert result is True
        assert manager._current_conversation_id is None

    def test_delete_nonexistent_conversation(self, manager):
        result = manager.delete_conversation("nonexistent-id")
        assert result is False

    def test_clear_all_history(self, manager):
        manager.new_conversation()
        manager.add_user_message("msg1")
        manager.new_conversation()
        manager.add_user_message("msg2")

        manager.clear_all_history()
        assert manager.get_all_conversations() == []
        assert manager._current_conversation_id is None

    def test_load_conversation(self, manager):
        conv1 = manager.new_conversation()
        manager.add_user_message("first")
        conv2 = manager.new_conversation()
        manager.add_user_message("second")

        # Currently on conv2
        assert manager.get_history()[0]["content"] == "second"

        # Switch back to conv1
        result = manager.load_conversation(conv1["id"])
        assert result is True
        assert manager.get_history()[0]["content"] == "first"

    def test_load_nonexistent_conversation(self, manager):
        result = manager.load_conversation("bad-id")
        assert result is False

    def test_multiple_conversations(self, manager):
        manager.new_conversation()
        manager.add_user_message("conv1")
        manager.new_conversation()
        manager.add_user_message("conv2")

        all_convs = manager.get_all_conversations()
        assert len(all_convs) == 2
        # Newest first
        assert all_convs[0]["messages"][0]["content"] == "conv2"
        assert all_convs[1]["messages"][0]["content"] == "conv1"

    def test_ensure_current_conversation_auto_creates(self, manager):
        # No conversation yet
        manager.add_user_message("auto-created")
        # Should auto-create a conversation
        assert manager._current_conversation_id is not None
        assert manager.get_history()[0]["content"] == "auto-created"

    def test_persistence(self, history_dir):
        """Verify data survives across instances."""
        mock_paths = MagicMock()
        mock_paths.CONFIG_DIR = history_dir

        with patch.dict(
            "sys.modules",
            {
                "ashyterm.settings.config": MagicMock(
                    get_config_paths=MagicMock(return_value=mock_paths)
                ),
                "ashyterm.utils.logger": MagicMock(
                    get_logger=MagicMock(return_value=MagicMock())
                ),
                "ashyterm.utils.security": MagicMock(
                    atomic_json_write=_fake_atomic_write
                ),
            },
        ):
            import sys

            sys.modules.pop("ashyterm.data.ai_history_manager", None)
            from ashyterm.data.ai_history_manager import AIHistoryManager

            mgr1 = AIHistoryManager()
            mgr1.new_conversation()
            mgr1.add_user_message("persistent msg")
            conv_id = mgr1._current_conversation_id

            # Create new instance (simulates restart)
            mgr2 = AIHistoryManager()
            assert len(mgr2.get_all_conversations()) == 1
            mgr2.load_conversation(conv_id)
            assert mgr2.get_history()[0]["content"] == "persistent msg"
