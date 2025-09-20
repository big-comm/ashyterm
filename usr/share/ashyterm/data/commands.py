# ashyterm/data/commands.py

import json
import threading
from typing import List, Optional, Set

from gi.repository import GObject

from ..settings.config import get_config_paths
from ..utils.logger import get_logger
from .command_data import NATIVE_COMMANDS


class CommandItem(GObject.GObject):
    """Data model for a command in the command guide."""

    __gproperties__ = {
        "name": (str, "Name", "The command itself", "", GObject.ParamFlags.READABLE),
        "category": (
            str,
            "Category",
            "Command category",
            "",
            GObject.ParamFlags.READABLE,
        ),
        "description": (
            str,
            "Description",
            "Explanation of the command",
            "",
            GObject.ParamFlags.READABLE,
        ),
        "is_custom": (
            bool,
            "Is Custom",
            "Is it a user-defined command",
            False,
            GObject.ParamFlags.READABLE,
        ),
        "is_general_description": (
            bool,
            "Is General Description",
            "Is this a general description row",
            False,
            GObject.ParamFlags.READABLE,
        ),
    }

    def __init__(
        self,
        name: str,
        category: str,
        description: str,
        is_custom: bool = False,
        is_general_description: bool = False,
        _native_sort_index: int = -1,
    ):
        super().__init__()
        self._name = name
        self._category = category
        self._description = description
        self._is_custom = is_custom
        self._is_general_description = is_general_description
        # MODIFIED: Store the original sort index for native commands
        self._native_sort_index = _native_sort_index

    @property
    def name(self) -> str:
        return self._name

    @property
    def category(self) -> str:
        return self._category

    @property
    def description(self) -> str:
        return self._description

    @property
    def is_custom(self) -> bool:
        return self._is_custom

    @property
    def is_general_description(self) -> bool:
        return self._is_general_description

    @property
    def native_sort_index(self) -> int:
        return self._native_sort_index

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
        }


class CommandManager:
    """Manages loading, saving, and accessing built-in and custom commands."""

    def __init__(self):
        self.logger = get_logger("ashyterm.data.commands")
        self.config_paths = get_config_paths()
        self.custom_commands_file = self.config_paths.CUSTOM_COMMANDS_FILE
        self._lock = threading.RLock()

        self._native_commands: List[CommandItem] = []
        self._custom_commands: List[CommandItem] = []

        self._load_native_commands()
        self._load_custom_commands()

    def _load_native_commands(self):
        with self._lock:
            self._native_commands = []
            # MODIFIED: Use a simple counter to preserve the exact original order
            native_index = 0
            for cmd_group in NATIVE_COMMANDS:
                # Add the general description row
                self._native_commands.append(
                    CommandItem(
                        name=cmd_group["command"],
                        category=cmd_group["category"],
                        description=cmd_group["general_description"],
                        is_custom=False,
                        is_general_description=True,
                        _native_sort_index=native_index,
                    )
                )
                native_index += 1
                # Add each variation
                for variation in cmd_group["variations"]:
                    self._native_commands.append(
                        CommandItem(
                            name=variation["name"],
                            category=cmd_group["category"],
                            description=variation["description"],
                            is_custom=False,
                            is_general_description=False,
                            _native_sort_index=native_index,
                        )
                    )
                    native_index += 1
            self.logger.info(
                f"Loaded {len(self._native_commands)} native command items (including descriptions)."
            )

    def _load_custom_commands(self):
        with self._lock:
            if not self.custom_commands_file.exists():
                self._custom_commands = []
                return

            try:
                with open(self.custom_commands_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._custom_commands = [
                        CommandItem(
                            name=cmd["name"],
                            category=cmd["category"],
                            description=cmd["description"],
                            is_custom=True,
                        )
                        for cmd in data
                    ]
                self.logger.info(
                    f"Loaded {len(self._custom_commands)} custom commands."
                )
            except (json.JSONDecodeError, FileNotFoundError) as e:
                self.logger.error(f"Failed to load custom commands: {e}")
                self._custom_commands = []

    def save_custom_commands(self):
        with self._lock:
            try:
                data_to_save = [cmd.to_dict() for cmd in self._custom_commands]
                with open(self.custom_commands_file, "w", encoding="utf-8") as f:
                    json.dump(data_to_save, f, indent=2, ensure_ascii=False)
                self.logger.info("Custom commands saved successfully.")
            except Exception as e:
                self.logger.error(f"Failed to save custom commands: {e}")

    def get_all_commands(self) -> List[CommandItem]:
        with self._lock:
            return self._native_commands + self._custom_commands

    def get_all_categories(self) -> Set[str]:
        """Returns a set of all unique category names."""
        with self._lock:
            return {
                cmd.category
                for cmd in self.get_all_commands()
                if cmd.category and not cmd.is_general_description
            }

    def add_custom_command(self, command: CommandItem):
        with self._lock:
            self._custom_commands.append(command)
            self.save_custom_commands()

    def remove_custom_command(self, command_to_remove: CommandItem):
        with self._lock:
            self._custom_commands = [
                cmd
                for cmd in self._custom_commands
                if cmd.name != command_to_remove.name
            ]
            self.save_custom_commands()

    def update_custom_command(
        self, original_command: CommandItem, updated_command: CommandItem
    ):
        with self._lock:
            for i, cmd in enumerate(self._custom_commands):
                if cmd.name == original_command.name:
                    self._custom_commands[i] = updated_command
                    break
            self.save_custom_commands()


_command_manager_instance: Optional[CommandManager] = None
_command_manager_lock = threading.Lock()


def get_command_manager() -> CommandManager:
    """Get the global CommandManager instance (thread-safe singleton)."""
    global _command_manager_instance
    if _command_manager_instance is None:
        with _command_manager_lock:
            if _command_manager_instance is None:
                _command_manager_instance = CommandManager()
    return _command_manager_instance
