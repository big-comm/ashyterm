# ashyterm/utils/base_component.py
"""
Base component classes for reducing boilerplate code.

This module provides base classes and mixins that encapsulate common
initialization patterns used throughout the application.
"""

from typing import Optional

from ..settings.config import get_config_paths
from .logger import get_logger


class ConfigurableComponent:
    """Base class for components that need logger and config paths.

    This class reduces boilerplate by automatically setting up:
    - A logger with the component's module path
    - Config paths for accessing application configuration

    Usage:
        class MyComponent(ConfigurableComponent):
            def __init__(self):
                super().__init__("ashyterm.mymodule")
                # self.logger and self._config_paths are now available
    """

    def __init__(self, logger_name: Optional[str] = None):
        """Initialize the component with logger and config paths.

        Args:
            logger_name: The logger name. If None, uses the class's module path.
        """
        if logger_name is None:
            logger_name = (
                f"ashyterm.{self.__class__.__module__}.{self.__class__.__name__}"
            )
        self.logger = get_logger(logger_name)
        self._config_paths = get_config_paths()

    @property
    def config_paths(self):
        """Get the configuration paths."""
        return self._config_paths


class LoggerMixin:
    """Mixin class that provides logger functionality.

    Use this when you need just the logger without config paths,
    or when inheriting from another class that conflicts with
    ConfigurableComponent.

    Usage:
        class MyWidget(Gtk.Box, LoggerMixin):
            def __init__(self):
                Gtk.Box.__init__(self)
                LoggerMixin.__init__(self, "ashyterm.ui.mywidget")
    """

    def __init__(self, logger_name: Optional[str] = None):
        """Initialize the logger.

        Args:
            logger_name: The logger name. If None, uses the class's module path.
        """
        if logger_name is None:
            logger_name = (
                f"ashyterm.{self.__class__.__module__}.{self.__class__.__name__}"
            )
        self.logger = get_logger(logger_name)
