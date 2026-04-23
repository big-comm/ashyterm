"""Highlight loader — system/user JSON file loading + layered config merge."""

import json
import threading
from importlib import resources
from pathlib import Path
from typing import Dict, Optional

from ..utils.logger import get_logger, log_error_with_context
from ..utils.security import atomic_json_write
from .config import get_config_paths
from .highlight_models import HighlightConfig, HighlightContext

_LOGGER_NAME = "ashyterm.highlights"
_JSON_GLOB_PATTERN = "*.json"
_GLOBAL_CONFIG_FILENAME = "global.json"


class HighlightLoader:
    """Load/save highlight config from JSON files — system layer + user overrides."""

    def __init__(self, config_path: Optional[Path] = None):
        self.logger = get_logger(_LOGGER_NAME)
        self._config_paths = get_config_paths()
        self._user_highlights_dir = config_path or (
            self._config_paths.CONFIG_DIR / "highlights"
        )
        self._user_config_file = (
            self._config_paths.CONFIG_DIR / "highlights_settings.json"
        )
        self._lock = threading.RLock()

    def load_layered_config(self) -> HighlightConfig:
        """Load config: user settings → system highlights → user overrides → merge."""
        with self._lock:
            config = HighlightConfig()
            disabled_global_rules: set = set()
            disabled_contexts: set = set()

            # 1. Load user settings (enabled flags, disabled rule names)
            try:
                if self._user_config_file.exists():
                    with open(self._user_config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    config.enabled_for_local = data.get("enabled_for_local", False)
                    config.enabled_for_ssh = data.get("enabled_for_ssh", False)
                    config.context_aware_enabled = data.get(
                        "context_aware_enabled", True
                    )
                    disabled_global_rules = set(data.get("disabled_global_rules", []))
                    disabled_contexts = set(data.get("disabled_contexts", []))
            except Exception as e:
                self.logger.warning(f"Failed to load user settings: {e}")

            # 2. Load system + user highlight contexts
            system_contexts = self._load_system_highlights()
            user_contexts = self._load_user_highlights()
            merged = {**system_contexts, **user_contexts}
            config.contexts = merged

            # 3. Extract global rules from "global" context → stored separately
            global_ctx = merged.pop("global", None)
            if global_ctx:
                config.global_rules = global_ctx.rules

            # 4. Apply disabled states
            for rule in config.global_rules:
                if rule.name in disabled_global_rules:
                    rule.enabled = False
            for ctx_name in disabled_contexts:
                if ctx_name in config.contexts:
                    config.contexts[ctx_name].enabled = False

            self.logger.info(
                f"Loaded {len(config.contexts)} contexts, "
                f"{len(config.global_rules)} global rules"
            )
            return config

    def save_config(self, config: HighlightConfig) -> None:
        """Persist user settings + modified contexts."""
        with self._lock:
            try:
                self._save_user_settings(config)
                self.logger.info("Saved highlight configuration")
            except Exception as e:
                self.logger.error(f"Failed to save highlight config: {e}")
                log_error_with_context(e, "saving highlight config", _LOGGER_NAME)

    def _save_user_settings(self, config: HighlightConfig) -> None:
        """Write enabled flags + disabled rule/context names to JSON."""
        disabled_global = [r.name for r in config.global_rules if not r.enabled]
        disabled_ctx = [n for n, c in config.contexts.items() if not c.enabled]
        settings = {
            "enabled_for_local": config.enabled_for_local,
            "enabled_for_ssh": config.enabled_for_ssh,
            "context_aware_enabled": config.context_aware_enabled,
            "disabled_global_rules": disabled_global,
            "disabled_contexts": disabled_ctx,
        }
        atomic_json_write(self._user_config_file, settings)

    def save_context_to_user(self, context: HighlightContext) -> None:
        """Write context override → user highlights dir."""
        file_path = self._user_highlights_dir / f"{context.command_name}.json"
        atomic_json_write(file_path, context.to_dict())

    def save_global_rules_to_user(self, config: HighlightConfig) -> None:
        """Write global rules as global.json in user highlights dir."""
        file_path = self._user_highlights_dir / _GLOBAL_CONFIG_FILENAME
        global_data = {
            "name": "global", "triggers": [],
            "rules": [r.to_dict() for r in config.global_rules],
            "enabled": True,
            "description": "Global highlight rules",
            "use_global_rules": False,
        }
        atomic_json_write(file_path, global_data)

    def delete_user_context(self, command_name: str) -> bool:
        """Remove user context override file."""
        file_path = self._user_highlights_dir / f"{command_name}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def has_user_context_override(self, command_name: str) -> bool:
        """Check if user override file exists for context."""
        file_path = self._user_highlights_dir / f"{command_name}.json"
        return file_path.exists()

    def delete_user_global_file(self) -> None:
        """Remove user global.json override."""
        file_path = self._user_highlights_dir / _GLOBAL_CONFIG_FILENAME
        if file_path.exists():
            file_path.unlink()

    def delete_all_user_context_files(self) -> int:
        """Remove all user context files except global.json. Returns count deleted."""
        deleted = 0
        if not self._user_highlights_dir.exists():
            return 0
        for json_file in self._user_highlights_dir.glob(_JSON_GLOB_PATTERN):
            if json_file.name != _GLOBAL_CONFIG_FILENAME:
                try:
                    json_file.unlink()
                    deleted += 1
                except Exception as e:
                    self.logger.warning(f"Failed to delete {json_file}: {e}")
        return deleted

    def _get_system_highlights_path(self) -> Optional[Path]:
        """Locate system highlight JSON files in package data."""
        try:
            if hasattr(resources, "files"):
                pkg_path = resources.files("ashyterm.data.highlights")
                if hasattr(pkg_path, "_path"):
                    return Path(pkg_path._path)
                return Path(__file__).parent.parent / "data" / "highlights"
            return Path(__file__).parent.parent / "data" / "highlights"
        except Exception as e:
            self.logger.warning(f"Could not locate system highlights: {e}")
            return Path(__file__).parent.parent / "data" / "highlights"

    def _load_system_highlights(self) -> Dict[str, HighlightContext]:
        """Load contexts from system package data JSON files."""
        contexts: Dict[str, HighlightContext] = {}
        system_path = self._get_system_highlights_path()
        if not system_path or not system_path.exists():
            self.logger.warning(f"System highlights path not found: {system_path}")
            return contexts
        try:
            for json_file in system_path.glob(_JSON_GLOB_PATTERN):
                try:
                    ctx = self._load_context_from_file(json_file)
                    if ctx:
                        contexts[ctx.command_name] = ctx
                except Exception as e:
                    self.logger.warning(f"Failed to load system highlight {json_file}: {e}")
        except Exception as e:
            self.logger.error(f"Failed to scan system highlights: {e}")
        return contexts

    def _load_user_highlights(self) -> Dict[str, HighlightContext]:
        """Load contexts from user config directory JSON files."""
        contexts: Dict[str, HighlightContext] = {}
        if not self._user_highlights_dir.exists():
            return contexts
        try:
            for json_file in self._user_highlights_dir.glob(_JSON_GLOB_PATTERN):
                try:
                    ctx = self._load_context_from_file(json_file)
                    if ctx:
                        contexts[ctx.command_name] = ctx
                except Exception as e:
                    self.logger.warning(f"Failed to load user highlight {json_file}: {e}")
        except Exception as e:
            self.logger.error(f"Failed to scan user highlights: {e}")
        return contexts

    def _load_context_from_file(self, file_path: Path) -> Optional[HighlightContext]:
        """Parse single JSON file → HighlightContext."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return HighlightContext.from_dict(data)
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in {file_path}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to load {file_path}: {e}")
            return None

    @property
    def user_highlights_dir(self) -> Path:
        """User highlights directory path."""
        return self._user_highlights_dir

    @property
    def global_config_filename(self) -> str:
        """Global config JSON filename."""
        return _GLOBAL_CONFIG_FILENAME
