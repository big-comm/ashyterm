# ashyterm/data/ai_history_manager.py

"""AI chat history persistence using JSON with conversation support."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..settings.config import get_config_paths
from ..utils.logger import get_logger
from ..utils.security import atomic_json_write


class AIHistoryManager:
    """Persist AI chat conversations in a single JSON file."""

    def __init__(self):
        self.logger = get_logger("ashyterm.data.ai_history_manager")
        self._config_paths = get_config_paths()
        self._history_file = self._config_paths.CONFIG_DIR / "ai_history.json"
        self._conversations: List[Dict[str, Any]] = []
        self._current_conversation_id: Optional[str] = None
        self._max_conversations = 50
        self._max_messages_per_conversation = 100
        self._load_history()

    def _load_history(self) -> None:
        try:
            if self._history_file.exists():
                with open(self._history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                    # Accept both the old (``history``) and new (``conversations``) shape.
                    if "conversations" in data:
                        self._conversations = data.get("conversations", [])
                        self._current_conversation_id = data.get(
                            "current_conversation_id"
                        )
                    elif "history" in data:
                        # Migrate pre-1.7 format: flat history → single conversation.
                        old_history = data.get("history", [])
                        if old_history:
                            conv_id = str(uuid.uuid4())
                            self._conversations = [
                                {
                                    "id": conv_id,
                                    "created_at": old_history[0].get(
                                        "timestamp", datetime.now().isoformat()
                                    ),
                                    "messages": old_history,
                                }
                            ]
                            self._current_conversation_id = conv_id
                        else:
                            self._conversations = []
                            self._current_conversation_id = None

                    self.logger.info(
                        f"Loaded {len(self._conversations)} AI conversations from history"
                    )
            else:
                self._conversations = []
                self._current_conversation_id = None
                self.logger.info("No AI history file found, starting fresh")
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse AI history JSON: {e}")
            self._conversations = []
            self._current_conversation_id = None
        except Exception as e:
            self.logger.error(f"Failed to load AI history: {e}")
            self._conversations = []
            self._current_conversation_id = None

    def _save_history(self) -> None:
        try:
            if len(self._conversations) > self._max_conversations:
                self._conversations = self._conversations[-self._max_conversations :]

            for conv in self._conversations:
                msgs = conv.get("messages", [])
                if len(msgs) > self._max_messages_per_conversation:
                    conv["messages"] = msgs[-self._max_messages_per_conversation :]

            data = {
                "conversations": self._conversations,
                "current_conversation_id": self._current_conversation_id,
            }

            atomic_json_write(self._history_file, data)

            self.logger.debug(
                f"Saved {len(self._conversations)} AI conversations to history"
            )
        except Exception as e:
            self.logger.error(f"Failed to save AI history: {e}")

    def _get_current_conversation(self) -> Optional[Dict[str, Any]]:
        if not self._current_conversation_id:
            return None
        for conv in self._conversations:
            if conv.get("id") == self._current_conversation_id:
                return conv
        return None

    def _ensure_current_conversation(self) -> Dict[str, Any]:
        conv = self._get_current_conversation()
        if conv is None:
            conv = self.new_conversation()
        return conv

    def new_conversation(self) -> Dict[str, Any]:
        conv_id = str(uuid.uuid4())
        conv = {"id": conv_id, "created_at": datetime.now().isoformat(), "messages": []}
        self._conversations.append(conv)
        self._current_conversation_id = conv_id
        if len(self._conversations) > self._max_conversations:
            self._conversations = self._conversations[-self._max_conversations :]
        self._save_history()
        self.logger.info(f"Created new conversation: {conv_id}")
        return conv

    def get_current_conversation(self) -> Optional[Dict[str, Any]]:
        return self._get_current_conversation()

    def get_all_conversations(self) -> List[Dict[str, Any]]:
        """Return every conversation, newest first."""
        return list(reversed(self._conversations))

    def load_conversation(self, conv_id: str) -> bool:
        """Switch the ``current`` pointer to ``conv_id``. ``False`` if unknown."""
        for conv in self._conversations:
            if conv.get("id") == conv_id:
                self._current_conversation_id = conv_id
                self._save_history()
                self.logger.info(f"Loaded conversation: {conv_id}")
                return True
        return False

    def add_message(
        self, role: str, content: str, commands: Optional[List[str]] = None
    ) -> None:
        """Append ``role``/``content`` (+optional commands) to current conversation."""
        if not content or not content.strip():
            return

        conv = self._ensure_current_conversation()

        entry: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "content": content.strip(),
        }
        if commands:
            entry["commands"] = commands

        conv["messages"].append(entry)

        # Trim in memory too; save also trims, but this keeps recent reads cheap.
        if len(conv["messages"]) > self._max_messages_per_conversation:
            conv["messages"] = conv["messages"][-self._max_messages_per_conversation :]

        self._save_history()

    def add_user_message(self, content: str) -> None:
        self.add_message("user", content)

    def add_assistant_message(
        self, content: str, commands: Optional[List[str]] = None
    ) -> None:
        self.add_message("assistant", content, commands)

    def get_history(self) -> List[Dict[str, Any]]:
        """Messages of the current conversation (copy)."""
        conv = self._get_current_conversation()
        if conv:
            return conv.get("messages", []).copy()
        return []

    def get_recent_history(self, count: int = 50) -> List[Dict[str, Any]]:
        """Last ``count`` messages of the current conversation."""
        history = self.get_history()
        return history[-count:] if count < len(history) else history

    def clear_history(self) -> None:
        """Empty the current conversation without deleting it."""
        conv = self._get_current_conversation()
        if conv:
            conv["messages"] = []
            self._save_history()
            self.logger.info("Cleared current conversation history")

    def delete_conversation(self, conv_id: str) -> bool:
        """Drop a conversation. Returns ``False`` if the id wasn't found."""
        for i, conv in enumerate(self._conversations):
            if conv.get("id") == conv_id:
                del self._conversations[i]
                if self._current_conversation_id == conv_id:
                    self._current_conversation_id = None
                self._save_history()
                self.logger.info(f"Deleted conversation: {conv_id}")
                return True
        return False

    def clear_all_history(self) -> None:
        self._conversations = []
        self._current_conversation_id = None
        self._save_history()
        self.logger.info("Cleared all AI chat history")


_history_manager: Optional[AIHistoryManager] = None


def get_ai_history_manager() -> AIHistoryManager:
    global _history_manager
    if _history_manager is None:
        _history_manager = AIHistoryManager()
    return _history_manager
