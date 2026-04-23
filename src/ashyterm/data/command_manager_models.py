# ashyterm/data/command_manager_models.py
"""Command Manager data models and JSON-backed manager singleton."""

import json
import os
import shlex
import threading
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..settings.config import get_config_paths
from ..utils.logger import get_logger


class ExecutionMode(Enum):
    """How the command should be executed when clicked."""

    INSERT_ONLY = "insert_only"  # Just add to terminal, don't execute
    INSERT_AND_EXECUTE = "insert_and_execute"  # Add and press Enter
    SHOW_DIALOG = "show_dialog"  # Show a form dialog first


class DisplayMode(Enum):
    """How the command button should be displayed."""

    ICON_ONLY = "icon_only"
    TEXT_ONLY = "text_only"
    ICON_AND_TEXT = "icon_and_text"


class FieldType(Enum):
    """Types of form fields available in command dialogs."""

    TEXT = "text"  # Simple text input
    SWITCH = "switch"  # Boolean toggle
    DROPDOWN = "dropdown"  # Select from options
    NUMBER = "number"  # Numeric input
    FILE_PATH = "file_path"  # File chooser
    DIRECTORY_PATH = "directory_path"  # Directory chooser
    PASSWORD = "password"  # Password/secret input (masked)
    MULTI_SELECT = "multi_select"  # Multiple selections from options
    TEXT_AREA = "text_area"  # Multi-line text input
    SLIDER = "slider"  # Range slider with min/max
    RADIO = "radio"  # Radio button group (mutually exclusive)
    DATE_TIME = "date_time"  # Date/time picker
    COLOR = "color"  # Color picker


# Field types whose values come from free-form user input and must be
# shell-escaped before template substitution to prevent injection.
_SHELL_ESCAPE_TYPES = frozenset(
    {
        FieldType.TEXT,
        FieldType.FILE_PATH,
        FieldType.DIRECTORY_PATH,
        FieldType.PASSWORD,
        FieldType.TEXT_AREA,
    }
)

# Category constants for builtin commands
CATEGORY_FILE_OPERATIONS = "File Operations"
CATEGORY_ARCHIVE_COMMON_NAME = "archive.tar.xz"


@dataclass(slots=True)
class CommandFormField:
    """One form input inside a command's dialog; feeds the template builder."""

    id: str  # Unique identifier for the field
    label: str  # Display label
    field_type: FieldType = FieldType.TEXT
    default_value: Any = ""
    placeholder: str = ""
    tooltip: str = ""
    required: bool = False
    # For SWITCH type: command_flag is added when switch is ON
    command_flag: str = ""
    # For SWITCH type: off_value is added when switch is OFF (can be empty)
    off_value: str = ""
    # For DROPDOWN type: list of (value, label) tuples
    options: List[tuple] = field(default_factory=list)
    # Position marker in command template (e.g., "{search_term}")
    template_key: str = ""
    # For NUMBER type
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    # Extra configuration for special field types (slider, date_time, color, text_area)
    extra_config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["field_type"] = self.field_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "CommandFormField":
        """Create from dictionary."""
        data = data.copy()
        data["field_type"] = FieldType(data.get("field_type", "text"))
        # Convert options from list of lists to list of tuples
        if "options" in data and data["options"]:
            data["options"] = [tuple(opt) for opt in data["options"]]
        return cls(**data)


@dataclass(slots=True)
class CommandButton:
    """A user-visible command entry: template + fields + execution mode."""

    id: str  # Unique identifier
    name: str  # Display name
    description: str  # Help text / tooltip
    command_template: str  # Command with placeholders like {search_term}
    icon_name: str = "utilities-terminal-symbolic"  # GTK icon name
    display_mode: DisplayMode = DisplayMode.ICON_AND_TEXT
    execution_mode: ExecutionMode = ExecutionMode.INSERT_ONLY
    # Where to place cursor after insertion (0 = end, negative = from end)
    cursor_position: int = 0
    # Form fields for SHOW_DIALOG mode
    form_fields: List[CommandFormField] = field(default_factory=list)
    # Whether this is a built-in command
    is_builtin: bool = False
    # Category for grouping
    category: str = ""
    # Sort order within category
    sort_order: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "command_template": self.command_template,
            "icon_name": self.icon_name,
            "display_mode": self.display_mode.value,
            "execution_mode": self.execution_mode.value,
            "cursor_position": self.cursor_position,
            "form_fields": [f.to_dict() for f in self.form_fields],
            "is_builtin": self.is_builtin,
            "category": self.category,
            "sort_order": self.sort_order,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CommandButton":
        """Create from dictionary."""
        data = data.copy()
        data["display_mode"] = DisplayMode(data.get("display_mode", "icon_and_text"))
        data["execution_mode"] = ExecutionMode(
            data.get("execution_mode", "insert_only")
        )
        data["form_fields"] = [
            CommandFormField.from_dict(f) for f in data.get("form_fields", [])
        ]
        return cls(**data)

    def build_command(self, field_values: Optional[Dict[str, Any]] = None) -> str:
        """Expand ``command_template`` with values from ``field_values``."""
        if not field_values:
            field_values = {}

        command = self.command_template

        for form_field in self.form_fields:
            value = field_values.get(form_field.id, form_field.default_value)
            template_key = form_field.template_key or form_field.id
            command = self._substitute_field(command, form_field, value, template_key)

        return " ".join(command.split())

    def _substitute_field(
        self,
        command: str,
        form_field: "CommandFormField",
        value: Any,
        template_key: str,
    ) -> str:
        """Substitute a single field value in the command template."""
        placeholder = f"{{{template_key}}}"

        if form_field.field_type == FieldType.SWITCH:
            return self._substitute_switch_field(
                command, form_field, value, placeholder
            )

        # For other fields, shell-escape user-provided values to prevent injection
        if placeholder in command:
            str_value = str(value) if value else ""
            if form_field.field_type in _SHELL_ESCAPE_TYPES and str_value:
                str_value = shlex.quote(str_value)
            return command.replace(placeholder, str_value)
        return command

    def _substitute_switch_field(
        self, command: str, form_field: "CommandFormField", value: Any, placeholder: str
    ) -> str:
        """Handle switch field substitution."""
        if value:
            return self._apply_switch_on_value(command, form_field, placeholder)
        return self._apply_switch_off_value(command, form_field, placeholder)

    def _apply_switch_on_value(
        self, command: str, form_field: "CommandFormField", placeholder: str
    ) -> str:
        """Apply switch ON value to command."""
        if placeholder in command:
            return command.replace(placeholder, form_field.command_flag)
        return f"{command} {form_field.command_flag}"

    def _apply_switch_off_value(
        self, command: str, form_field: "CommandFormField", placeholder: str
    ) -> str:
        """Apply switch OFF value to command."""
        if placeholder in command:
            return command.replace(placeholder, form_field.off_value)
        if form_field.off_value:
            return f"{command} {form_field.off_value}"
        return command


def generate_id() -> str:
    """Generate a unique ID for a command button."""
    return str(uuid.uuid4())[:8]


# Built-in example commands — data lives in command_manager_builtins.py.
# Imported lazily so command_manager_builtins can itself import
# from this module without a circular reference.
def get_builtin_commands():
    """Return the canned built-in commands (see command_manager_builtins)."""
    from .command_manager_builtins import get_builtin_commands as _impl
    return _impl()


class CommandButtonManager:
    """Load/save commands. Thread-safe singleton, merges builtins + user data."""

    _instance: Optional["CommandButtonManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.logger = get_logger("ashyterm.data.command_manager")
        self.config_paths = get_config_paths()
        self.custom_commands_file = (
            self.config_paths.CONFIG_DIR / "command_buttons.json"
        )
        self.customized_builtins_file = (
            self.config_paths.CONFIG_DIR / "customized_builtins.json"
        )
        self.hidden_commands_file = (
            self.config_paths.CONFIG_DIR / "hidden_commands.json"
        )
        self.command_prefs_file = self.config_paths.CONFIG_DIR / "command_prefs.json"
        self._data_lock = threading.RLock()

        self._builtin_commands: List[CommandButton] = []
        self._custom_commands: List[CommandButton] = []
        self._customized_builtins: Dict[str, dict] = {}  # id -> customized data
        self._hidden_command_ids: set = set()
        self._command_prefs: Dict[
            str, dict
        ] = {}  # command_id -> preferences (e.g., send_to_all)

        self._load_builtin_commands()
        self._load_custom_commands()
        self._load_customized_builtins()
        self._load_hidden_commands()
        self._load_command_prefs()

        self._initialized = True

    def _load_builtin_commands(self):
        """Load the built-in example commands."""
        with self._data_lock:
            self._builtin_commands = get_builtin_commands()
            self.logger.info(f"Loaded {len(self._builtin_commands)} built-in commands.")

    # ── JSON I/O helpers ─────────────────────────────────────

    def _save_json_file(self, filepath: Path, data: Any, label: str) -> None:
        """Atomic JSON write with ``os.fsync`` before rename."""
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            temp_file = filepath.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            temp_file.replace(filepath)
            self.logger.info(f"{label} saved successfully.")
        except Exception as e:
            self.logger.error(f"Failed to save {label}: {e}")

    def _load_json_file(self, filepath: Path, default: Any, label: str) -> Any:
        """Load JSON or return ``default`` on missing/corrupt file."""
        if not filepath.exists():
            return default

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.logger.info(f"Loaded {label}.")
            return data
        except (json.JSONDecodeError, FileNotFoundError) as e:
            self.logger.error(f"Failed to load {label}: {e}")
            return default

    # ── Data loading ─────────────────────────────────────────

    def _load_custom_commands(self):
        with self._data_lock:
            data = self._load_json_file(
                self.custom_commands_file, [], "custom commands"
            )
            try:
                all_cmds = [CommandButton.from_dict(cmd) for cmd in data]
                # Deduplicate by ID, keeping only the last occurrence
                seen_ids: Dict[str, int] = {}
                for i, cmd in enumerate(all_cmds):
                    seen_ids[cmd.id] = i
                self._custom_commands = [
                    cmd for i, cmd in enumerate(all_cmds) if seen_ids[cmd.id] == i
                ]
                if len(self._custom_commands) < len(all_cmds):
                    removed = len(all_cmds) - len(self._custom_commands)
                    self.logger.warning(
                        f"Removed {removed} duplicate custom commands on load."
                    )
                    self.save_custom_commands()
                if self._custom_commands:
                    self.logger.info(
                        f"Loaded {len(self._custom_commands)} custom commands."
                    )
            except (KeyError, TypeError) as e:
                self.logger.error(f"Failed to parse custom commands: {e}")
                self._custom_commands = []

    def _load_customized_builtins(self):
        """Load customizations for built-in commands."""
        with self._data_lock:
            self._customized_builtins = self._load_json_file(
                self.customized_builtins_file, {}, "customized builtins"
            )

    def _load_hidden_commands(self):
        """Load list of hidden command IDs."""
        with self._data_lock:
            data = self._load_json_file(
                self.hidden_commands_file, [], "hidden commands"
            )
            self._hidden_command_ids = set(data)

    def save_custom_commands(self):
        """Save user-defined commands to file."""
        with self._data_lock:
            data = [cmd.to_dict() for cmd in self._custom_commands]
            self._save_json_file(self.custom_commands_file, data, "Custom commands")

    def _save_customized_builtins(self):
        """Save customized builtin commands."""
        with self._data_lock:
            self._save_json_file(
                self.customized_builtins_file,
                self._customized_builtins,
                "Customized builtins",
            )

    def _save_hidden_commands(self):
        """Save hidden commands list."""
        with self._data_lock:
            self._save_json_file(
                self.hidden_commands_file,
                list(self._hidden_command_ids),
                "Hidden commands",
            )

    def _load_command_prefs(self):
        """Load per-command preferences (e.g., send_to_all)."""
        with self._data_lock:
            self._command_prefs = self._load_json_file(
                self.command_prefs_file, {}, "command preferences"
            )
            # Diagnostic: log any commands that are loaded as pinned
            pinned_ids = [
                cmd_id
                for cmd_id, prefs in self._command_prefs.items()
                if prefs.get("pinned")
            ]
            if pinned_ids:
                self.logger.info(f"Loaded pinned commands from prefs: {pinned_ids}")

    def _save_command_prefs(self):
        """Save per-command preferences."""
        with self._data_lock:
            self._save_json_file(
                self.command_prefs_file, self._command_prefs, "Command preferences"
            )

    def get_command_pref(self, command_id: str, pref_key: str, default=None):
        """Get a preference value for a command."""
        with self._data_lock:
            return self._command_prefs.get(command_id, {}).get(pref_key, default)

    def set_command_pref(self, command_id: str, pref_key: str, value):
        """Set a preference value for a command and save."""
        with self._data_lock:
            if command_id not in self._command_prefs:
                self._command_prefs[command_id] = {}
            self._command_prefs[command_id][pref_key] = value
            self._save_command_prefs()

    def _apply_customizations_to_builtin(self, cmd: CommandButton) -> CommandButton:
        """Return the user-customized copy of ``cmd`` if one exists."""
        if cmd.id in self._customized_builtins:
            customized = CommandButton.from_dict(self._customized_builtins[cmd.id])
            customized.is_builtin = True
            return customized
        return cmd

    def get_all_commands(self) -> List[CommandButton]:
        """Get all commands (built-in with customizations applied, and custom)."""
        with self._data_lock:
            result = [
                self._apply_customizations_to_builtin(cmd)
                for cmd in self._builtin_commands
            ]
            result.extend(self._custom_commands)
            return result

    def get_builtin_commands(self) -> List[CommandButton]:
        """Get only built-in commands (with customizations applied)."""
        with self._data_lock:
            return [
                self._apply_customizations_to_builtin(cmd)
                for cmd in self._builtin_commands
            ]

    def get_custom_commands(self) -> List[CommandButton]:
        """Get only custom commands."""
        with self._data_lock:
            return list(self._custom_commands)

    def get_command_by_id(self, command_id: str) -> Optional[CommandButton]:
        """Lookup by ID (builtins first, custom second; customizations applied)."""
        with self._data_lock:
            for cmd in self._builtin_commands:
                if cmd.id == command_id:
                    return self._apply_customizations_to_builtin(cmd)
            for cmd in self._custom_commands:
                if cmd.id == command_id:
                    return cmd
            return None

    def get_categories(self) -> List[str]:
        """Get all unique categories."""
        with self._data_lock:
            categories = set()
            for cmd in self.get_all_commands():
                if cmd.category:
                    categories.add(cmd.category)
            return sorted(categories)

    def is_builtin_customized(self, command_id: str) -> bool:
        """Check if a builtin command has been customized."""
        with self._data_lock:
            return command_id in self._customized_builtins

    def is_command_hidden(self, command_id: str) -> bool:
        """Check if a command is hidden."""
        with self._data_lock:
            return command_id in self._hidden_command_ids

    def hide_command(self, command_id: str):
        """Hide a command from the interface."""
        with self._data_lock:
            self._hidden_command_ids.add(command_id)
            self._save_hidden_commands()
            self.logger.info(f"Hidden command: {command_id}")

    def unhide_command(self, command_id: str):
        """Unhide a command."""
        with self._data_lock:
            self._hidden_command_ids.discard(command_id)
            self._save_hidden_commands()
            self.logger.info(f"Unhidden command: {command_id}")

    def get_hidden_command_ids(self) -> List[str]:
        """Get list of hidden command IDs."""
        with self._data_lock:
            return list(self._hidden_command_ids)

    def is_command_pinned(self, command_id: str) -> bool:
        """Check if a command is pinned to the toolbar."""
        return self.get_command_pref(command_id, "pinned", False)

    def pin_command(self, command_id: str):
        """Pin a command to the toolbar."""
        self.set_command_pref(command_id, "pinned", True)
        self.logger.info(f"Pinned command to toolbar: {command_id}")

    def unpin_command(self, command_id: str):
        """Unpin a command from the toolbar."""
        self.set_command_pref(command_id, "pinned", False)
        self.logger.info(f"Unpinned command from toolbar: {command_id}")

    def get_pinned_commands(self) -> List["CommandButton"]:
        """Get all commands that are pinned to the toolbar, in order."""
        with self._data_lock:
            pinned = []
            all_commands = self.get_all_commands()
            for cmd in all_commands:
                if self.is_command_pinned(cmd.id) and not self.is_command_hidden(
                    cmd.id
                ):
                    pinned.append(cmd)
            return pinned

    def add_custom_command(self, command: CommandButton):
        """Add a new custom command."""
        with self._data_lock:
            if not command.id:
                command.id = generate_id()
            # Prevent duplicate IDs
            existing_ids = {cmd.id for cmd in self._custom_commands}
            if command.id in existing_ids:
                self.logger.warning(
                    f"Duplicate command ID rejected: {command.id}"
                )
                return
            command.is_builtin = False
            self._custom_commands.append(command)
            self.save_custom_commands()

    def update_command(self, command: CommandButton):
        """Update an existing command (custom or builtin customization)."""
        with self._data_lock:
            # Check if it's a builtin command
            builtin_ids = {cmd.id for cmd in self._builtin_commands}

            if command.id in builtin_ids:
                # Store customization for builtin
                self._customized_builtins[command.id] = command.to_dict()
                self._save_customized_builtins()
                self.logger.info(f"Saved customization for builtin: {command.id}")
                return

            # Update custom command
            for i, cmd in enumerate(self._custom_commands):
                if cmd.id == command.id:
                    self._custom_commands[i] = command
                    self.save_custom_commands()
                    return

            self.logger.warning(
                f"Command not found for update: {command.id}"
            )

    def restore_builtin_default(self, command_id: str):
        """Restore a builtin command to its default configuration."""
        with self._data_lock:
            if command_id in self._customized_builtins:
                del self._customized_builtins[command_id]
                self._save_customized_builtins()
                self.logger.info(f"Restored default for builtin: {command_id}")

    def remove_command(self, command_id: str):
        """Remove a custom command by ID."""
        with self._data_lock:
            self._custom_commands = [
                cmd for cmd in self._custom_commands if cmd.id != command_id
            ]
            self.save_custom_commands()

    def reorder_commands(self, command_ids: List[str]):
        """Reorder custom commands based on the given ID order."""
        with self._data_lock:
            id_to_cmd = {cmd.id: cmd for cmd in self._custom_commands}
            reordered = []
            for cmd_id in command_ids:
                if cmd_id in id_to_cmd:
                    reordered.append(id_to_cmd[cmd_id])
            # Add any commands not in the list at the end
            for cmd in self._custom_commands:
                if cmd.id not in command_ids:
                    reordered.append(cmd)
            self._custom_commands = reordered
            self.save_custom_commands()


def get_command_button_manager() -> CommandButtonManager:
    """Get the singleton CommandButtonManager instance."""
    return CommandButtonManager()
