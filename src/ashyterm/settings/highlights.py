"""HighlightManager — orchestrator for highlight config, colors, and file I/O.

Composes HighlightLoader (file I/O) + HighlightColorResolver (theme colors).
Re-exports all data models for API compatibility.
"""

import concurrent.futures
import re
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

import gi
from typing import Any
gi.require_version("GObject", "2.0")
from gi.repository import GObject

from .highlight_colors import HighlightColorResolver
from .highlight_loader import HighlightLoader
from .highlight_models import (
    HighlightConfig,
    HighlightContext,
    HighlightRule,
    ANSI_COLOR_MAP,  # noqa: F401 — re-export for API compat
    ANSI_MODIFIERS,  # noqa: F401 — re-export for API compat
)


class HighlightManager(GObject.GObject):
    """Manage syntax highlighting rules — layered system + user config."""

    __gsignals__ = {
        "rules-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "context-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config_path: Optional[Path] = None, settings_manager=None):
        GObject.GObject.__init__(self)
        self._loader = HighlightLoader(config_path)
        self._colors = HighlightColorResolver()
        self._colors.set_settings_manager(settings_manager)
        self._config: HighlightConfig = HighlightConfig()
        self._trigger_map: Dict[str, str] = {}
        self._pattern_dirty = True
        self._lock = threading.RLock()
        self._load_layered_config()

    # ── Config Loading ──────────────────────────────────────────────

    def _load_layered_config(self) -> None:
        """Load system + user highlights, build trigger map."""
        with self._lock:
            try:
                self._config = self._loader.load_layered_config()
                self._build_trigger_map()
                self._pattern_dirty = True
            except Exception:
                self._config = HighlightConfig(
                    context_aware_enabled=True, global_rules=[], contexts={},
                )
                self._pattern_dirty = True

    def save_config(self) -> None:
        """Persist user settings to disk."""
        self._loader.save_config(self._config)

    # ── Color Resolution ────────────────────────────────────────────

    def set_settings_manager(self, manager: Any) -> None:
        """Attach settings manager for theme-aware colors."""
        self._colors.set_settings_manager(manager)

    def get_current_theme_palette(self) -> Dict[str, str]:
        """Current theme palette (foreground, background, cursor, palette)."""
        return self._colors.get_current_theme_palette()

    def resolve_color(self, color_name: str) -> str:
        """Logical color name → hex color."""
        return self._colors.resolve_color(color_name)

    def resolve_color_to_ansi(self, color_name: str) -> str:
        """Logical color name → ANSI escape sequence."""
        return self._colors.resolve_color_to_ansi(color_name)

    # ── Context / Trigger Lookup ────────────────────────────────────

    def _build_trigger_map(self) -> None:
        """Map trigger commands → context names."""
        self._trigger_map.clear()
        for ctx_name, ctx in self._config.contexts.items():
            for trigger in ctx.triggers:
                self._trigger_map[trigger.lower()] = ctx_name

    def get_all_triggers(self) -> Set[str]:
        """All command triggers from loaded contexts."""
        with self._lock:
            return set(self._trigger_map.keys())

    def get_context_for_command(self, command: str) -> Optional[str]:
        """Context name for given command, or None."""
        with self._lock:
            return self._trigger_map.get(command.lower())

    # ── Properties ──────────────────────────────────────────────────

    @property
    def enabled_for_local(self) -> bool:
        return self._config.enabled_for_local

    @enabled_for_local.setter
    def enabled_for_local(self, value: bool) -> None:
        with self._lock:
            self._config.enabled_for_local = value

    @property
    def enabled_for_ssh(self) -> bool:
        return self._config.enabled_for_ssh

    @enabled_for_ssh.setter
    def enabled_for_ssh(self, value: bool) -> None:
        with self._lock:
            self._config.enabled_for_ssh = value

    @property
    def context_aware_enabled(self) -> bool:
        return self._config.context_aware_enabled

    @context_aware_enabled.setter
    def context_aware_enabled(self, value: bool) -> None:
        with self._lock:
            self._config.context_aware_enabled = value

    @property
    def contexts(self) -> Dict[str, HighlightContext]:
        return self._config.contexts.copy()

    @property
    def rules(self) -> List[HighlightRule]:
        return self._config.global_rules.copy()

    # ── Context Management ──────────────────────────────────────────

    def get_context(self, command_name: str) -> Optional[HighlightContext]:
        with self._lock:
            return self._config.contexts.get(command_name)

    def get_context_names(self) -> List[str]:
        with self._lock:
            return list(self._config.contexts.keys())

    def add_context(self, context: HighlightContext) -> None:
        with self._lock:
            self._config.contexts[context.command_name] = context
            self._build_trigger_map()
            self._pattern_dirty = True
        self.emit("rules-changed")

    def remove_context(self, command_name: str) -> bool:
        with self._lock:
            if command_name in self._config.contexts:
                del self._config.contexts[command_name]
                self._build_trigger_map()
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    def set_context_enabled(self, command_name: str, enabled: bool) -> bool:
        return self._modify_context_attr(
            command_name, lambda ctx: setattr(ctx, "enabled", enabled)
        )

    def set_context_use_global_rules(self, command_name: str, use_global: bool) -> bool:
        return self._modify_context_attr(
            command_name, lambda ctx: setattr(ctx, "use_global_rules", use_global)
        )

    def get_context_use_global_rules(self, command_name: str) -> bool:
        with self._lock:
            if command_name in self._config.contexts:
                return self._config.contexts[command_name].use_global_rules
            return False

    # ── Global Rule Management ──────────────────────────────────────

    def get_rules_for_context(self, command_name: str) -> List[HighlightRule]:
        """Rules for context — global+context if use_global_rules, else context-only."""
        with self._lock:
            if (
                self._config.context_aware_enabled
                and command_name
                and command_name in self._config.contexts
            ):
                ctx = self._config.contexts[command_name]
                if ctx.enabled:
                    context_rules = [r for r in ctx.rules if r.enabled and r.is_valid()]
                    if ctx.use_global_rules:
                        global_rules = [
                            r for r in self._config.global_rules
                            if r.enabled and r.is_valid()
                        ]
                        return global_rules + context_rules
                    return context_rules
            return [r for r in self._config.global_rules if r.enabled and r.is_valid()]

    def get_rule(self, index: int) -> Optional[HighlightRule]:
        with self._lock:
            if 0 <= index < len(self._config.global_rules):
                return self._config.global_rules[index]
            return None

    def add_rule(self, rule: HighlightRule) -> None:
        with self._lock:
            self._config.global_rules.append(rule)
            self._pattern_dirty = True
        self.emit("rules-changed")

    def update_rule(self, index: int, rule: HighlightRule) -> bool:
        return self._modify_global_rule(
            index, lambda rules, i: rules.__setitem__(i, rule)
        )

    def remove_rule(self, index: int) -> bool:
        return self._modify_global_rule(index, lambda rules, i: rules.__delitem__(i))

    def set_rule_enabled(self, index: int, enabled: bool) -> bool:
        return self._modify_global_rule(
            index, lambda rules, i: setattr(rules[i], "enabled", enabled)
        )

    # ── Context Rule Management ─────────────────────────────────────

    def add_rule_to_context(self, command_name: str, rule: HighlightRule) -> bool:
        return self._modify_context_attr(
            command_name, lambda ctx: ctx.rules.append(rule)
        )

    def update_context_rule(
        self, command_name: str, index: int, rule: HighlightRule
    ) -> bool:
        return self._modify_context_rule(
            command_name, index, lambda rules, i: rules.__setitem__(i, rule)
        )

    def remove_context_rule(self, command_name: str, index: int) -> bool:
        return self._modify_context_rule(
            command_name, index, lambda rules, i: rules.__delitem__(i)
        )

    def set_context_rule_enabled(
        self, command_name: str, index: int, enabled: bool
    ) -> bool:
        return self._modify_context_rule(
            command_name, index, lambda rules, i: setattr(rules[i], "enabled", enabled)
        )

    def move_context_rule(
        self, command_name: str, from_index: int, to_index: int
    ) -> bool:
        with self._lock:
            if command_name not in self._config.contexts:
                return False
            ctx = self._config.contexts[command_name]
            if not (
                0 <= from_index < len(ctx.rules) and 0 <= to_index < len(ctx.rules)
            ):
                return False
            rule = ctx.rules.pop(from_index)
            ctx.rules.insert(to_index, rule)
            self._pattern_dirty = True
            self.emit("rules-changed")
            return True

    # ── User File Operations ────────────────────────────────────────

    def save_context_to_user(self, context: HighlightContext) -> None:
        """Save context as user override."""
        self._loader.save_context_to_user(context)
        with self._lock:
            self._config.contexts[context.command_name] = context
            self._build_trigger_map()
            self._pattern_dirty = True
        self.emit("rules-changed")

    def save_global_rules_to_user(self) -> None:
        """Persist global rules as user global.json."""
        self._loader.save_global_rules_to_user(self._config)
        self._pattern_dirty = True

    def delete_user_context(self, command_name: str) -> bool:
        """Delete user context override, reload system version."""
        if self._loader.delete_user_context(command_name):
            self._load_layered_config()
            self.emit("rules-changed")
            return True
        return False

    def has_user_context_override(self, command_name: str) -> bool:
        return self._loader.has_user_context_override(command_name)

    def reset_to_defaults(self) -> None:
        """Delete all user files, reload system defaults."""
        with self._lock:
            self._loader.delete_all_user_context_files()
            self._loader.delete_user_global_file()
            self._load_layered_config()
            self._pattern_dirty = True
        self.emit("rules-changed")

    def reset_global_rules(self) -> None:
        """Reset global rules to system defaults, keep context customizations."""
        with self._lock:
            self._loader.delete_user_global_file()
            self._load_layered_config()
            self._pattern_dirty = True
        self.emit("rules-changed")

    def reset_all_contexts(self) -> None:
        """Reset all contexts to system defaults, keep global rules."""
        with self._lock:
            self._loader.delete_all_user_context_files()
            self._load_layered_config()
            self._pattern_dirty = True
        self.emit("rules-changed")

    # ── Validation ──────────────────────────────────────────────────

    def validate_pattern(self, pattern: str) -> Tuple[bool, str]:
        """Validate regex pattern + basic ReDoS complexity check.

        Uses a worker thread so we can apply a timeout regardless of which
        thread called us (SIGALRM is main-thread-only on POSIX).
        """
        if not pattern:
            return False, "Pattern cannot be empty"
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return False, str(e)

        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="ashy-redos"
            ) as executor:
                future = executor.submit(compiled.search, "a" * 1000)
                try:
                    future.result(timeout=2)
                except concurrent.futures.TimeoutError:
                    return False, "Pattern is too complex (potential ReDoS)"
        except Exception as e:
            return False, str(e)
        return True, ""

    def is_enabled_for_terminal_type(self, terminal_type: str) -> bool:
        """Check if highlighting enabled for terminal type."""
        if terminal_type == "local":
            return self._config.enabled_for_local
        elif terminal_type in ("ssh", "sftp"):
            return self._config.enabled_for_ssh
        return False

    # ── Proxy methods for test/file-loading compatibility ───────────
    # Delegate to _loader so tests accessing private methods still work.

    def _load_context_from_file(self, file_path):
        """Load HighlightContext from JSON file. Works without _loader init."""
        if hasattr(self, "_loader"):
            return self._loader._load_context_from_file(file_path)
        # Fallback for tests using __new__ — direct import
        import json
        from .highlight_models import HighlightContext
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return HighlightContext.from_dict(data)
        except Exception:
            return None

    def _load_layered_config(self):
        """Load system + user highlights, build trigger map."""
        with self._lock:
            try:
                self._config = self._loader.load_layered_config()
                self._build_trigger_map()
                self._pattern_dirty = True
            except Exception:
                self._config = HighlightConfig(
                    context_aware_enabled=True, global_rules=[], contexts={},
                )
                self._pattern_dirty = True

    # ── Proxy methods for test compatibility (color resolution) ─────

    def _parse_color_spec(self, color_name: str):
        return self._colors._parse_color_spec(color_name)

    def _get_foreground_ansi_code(self, base_color: str):
        return self._colors._get_foreground_ansi_code(base_color)

    def _get_background_ansi_code(self, bg_color):
        return self._colors._get_background_ansi_code(bg_color)

    def _resolve_base_color(self, color_name: str, palette: dict) -> str:
        return self._colors._resolve_base_color(color_name, palette)

    # ── Internal Helpers ────────────────────────────────────────────

    def _modify_global_rule(
        self, index: int, action: Callable[[List[HighlightRule], int], None]
    ) -> bool:
        """Apply action to global rule at index. Emit rules-changed."""
        with self._lock:
            if 0 <= index < len(self._config.global_rules):
                action(self._config.global_rules, index)
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    def _modify_context_rule(
        self, command_name: str, index: int,
        action: Callable[[List[HighlightRule], int], None],
    ) -> bool:
        """Apply action to context rule at index. Emit rules-changed."""
        with self._lock:
            if command_name in self._config.contexts:
                ctx = self._config.contexts[command_name]
                if 0 <= index < len(ctx.rules):
                    action(ctx.rules, index)
                    self._pattern_dirty = True
                    self.emit("rules-changed")
                    return True
            return False

    def _modify_context_attr(
        self, command_name: str, action: Callable[[HighlightContext], None]
    ) -> bool:
        """Apply action to context. Emit rules-changed."""
        with self._lock:
            if command_name in self._config.contexts:
                action(self._config.contexts[command_name])
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    @property
    def pattern_dirty(self) -> bool:
        return self._pattern_dirty

    @pattern_dirty.setter
    def pattern_dirty(self, value: bool) -> None:
        self._pattern_dirty = value

    @property
    def user_highlights_dir(self) -> Path:
        return self._loader.user_highlights_dir


# ── Singleton ───────────────────────────────────────────────────────

_highlight_manager: Optional[HighlightManager] = None
_manager_lock = threading.Lock()


def get_highlight_manager() -> HighlightManager:
    """Get global HighlightManager instance."""
    global _highlight_manager
    if _highlight_manager is None:
        with _manager_lock:
            if _highlight_manager is None:
                _highlight_manager = HighlightManager()
    return _highlight_manager
