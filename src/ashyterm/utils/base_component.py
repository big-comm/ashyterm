# ashyterm/utils/base_component.py
"""Base component classes → reduce boilerplate.
Auto-setup: logger + config paths.
"""

from typing import Optional

from ..settings.config import get_config_paths
from .logger import get_logger
from typing import Any


class ConfigurableComponent:
    """Base: components needing logger + config paths.

    Auto-setup:
    - logger w/ component module path
    - config paths for app configuration

    Usage:
        class MyComponent(ConfigurableComponent):
            def __init__(self):
                super().__init__("ashyterm.mymodule")
                # self.logger, self._config_paths → ready
    """

    def __init__(self, logger_name: Optional[str] = None):
        """Init component → logger + config paths.

        Args:
            logger_name: logger name | None → class module path
        """
        if logger_name is None:
            logger_name = (
                f"ashyterm.{self.__class__.__module__}.{self.__class__.__name__}"
            )
        self.logger = get_logger(logger_name)
        self._config_paths = get_config_paths()

    @property
    def config_paths(self) -> Any:
        """Get configuration paths."""
        return self._config_paths


class LoggerMixin:
    """Mixin → logger only. Use when inheriting from another class.

    Usage:
        class MyWidget(Gtk.Box, LoggerMixin):
            def __init__(self):
                Gtk.Box.__init__(self)
                LoggerMixin.__init__(self, "ashyterm.ui.mywidget")
    """

    def __init__(self, logger_name: Optional[str] = None):
        """Init logger.

        Args:
            logger_name: logger name | None → class module path
        """
        if logger_name is None:
            logger_name = (
                f"ashyterm.{self.__class__.__module__}.{self.__class__.__name__}"
            )
        self.logger = get_logger(logger_name)
