# ashyterm/utils/logger.py

import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Union


class LogLevel(Enum):
    """Log levels for the application."""

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class LoggerConfig:
    """Configuration for the logging system."""

    def __init__(self):
        self.log_dir = Path.home() / ".config" / "ashyterm" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.main_log_file = self.log_dir / "ashyterm.log"
        self.error_log_file = self.log_dir / "ashyterm_errors.log"
        self.debug_log_file = self.log_dir / "ashyterm_debug.log"
        self.max_file_size = 10 * 1024 * 1024  # 10MB
        self.backup_count = 5
        self.log_to_file = False
        self.console_level = LogLevel.ERROR
        self.file_level = LogLevel.DEBUG
        self.error_file_level = LogLevel.ERROR


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
        "RESET": "\033[0m",
    }

    def format(self, record):
        levelname = record.levelname
        if levelname in self.COLORS:
            colored_levelname = (
                f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
            )
            record.levelname = colored_levelname
        formatted = super().format(record)
        record.levelname = levelname
        return formatted


class ThreadSafeLogger:
    """Thread-safe logger implementation."""

    def __init__(self, name: str, config: LoggerConfig):
        self.name = name
        self.config = config
        self._logger = logging.getLogger(name)
        self._lock = threading.Lock()
        self._setup_logger()

    def _setup_logger(self):
        """Set up the logger with handlers and formatters based on current config."""
        with self._lock:
            if self._logger.hasHandlers():
                self._logger.handlers.clear()

            self._logger.propagate = False
            self._logger.setLevel(logging.DEBUG)

            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.config.console_level.value)
            console_formatter = ColoredFormatter(
                fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
                datefmt="%H:%M:%S",
            )
            console_handler.setFormatter(console_formatter)
            self._logger.addHandler(console_handler)

            if self.config.log_to_file:
                file_formatter = logging.Formatter(
                    fmt="%(asctime)s | %(name)s | %(levelname)s | %(funcName)s:%(lineno)d | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )

                main_file_handler = logging.handlers.RotatingFileHandler(
                    self.config.main_log_file,
                    maxBytes=self.config.max_file_size,
                    backupCount=self.config.backup_count,
                    encoding="utf-8",
                )
                main_file_handler.setLevel(self.config.file_level.value)
                main_file_handler.setFormatter(file_formatter)
                self._logger.addHandler(main_file_handler)

                error_file_handler = logging.handlers.RotatingFileHandler(
                    self.config.error_log_file,
                    maxBytes=self.config.max_file_size,
                    backupCount=self.config.backup_count,
                    encoding="utf-8",
                )
                error_file_handler.setLevel(self.config.error_file_level.value)
                error_file_handler.setFormatter(file_formatter)
                self._logger.addHandler(error_file_handler)

            self._logger._ashyterm_configured = True

    def debug(self, message: str, **kwargs):
        self._logger.debug(message, **kwargs)

    def info(self, message: str, **kwargs):
        self._logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs):
        self._logger.warning(message, **kwargs)

    def error(self, message: str, exc_info: bool = False, **kwargs):
        self._logger.error(message, exc_info=exc_info, **kwargs)

    def critical(self, message: str, exc_info: bool = True, **kwargs):
        self._logger.critical(message, exc_info=exc_info, **kwargs)

    def exception(self, message: str, **kwargs):
        self._logger.exception(message, **kwargs)


class LoggerManager:
    """Centralized logger manager."""

    _instance: Optional["LoggerManager"] = None
    # CORREÇÃO: Usar RLock para evitar deadlock em chamadas aninhadas
    _lock = threading.RLock()

    def __new__(cls) -> "LoggerManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self.config = LoggerConfig()
        self._loggers: Dict[str, ThreadSafeLogger] = {}
        self._setup_root_logger()

    def _setup_root_logger(self):
        logging.getLogger("gi").setLevel(logging.WARNING)
        logging.getLogger("Vte").setLevel(logging.WARNING)
        logging.getLogger("Gtk").setLevel(logging.WARNING)

    def get_logger(self, name: str) -> ThreadSafeLogger:
        if name not in self._loggers:
            with self._lock:
                if name not in self._loggers:
                    self._loggers[name] = ThreadSafeLogger(name, self.config)
        return self._loggers[name]

    def reconfigure_all_loggers(self):
        """Re-applies configuration to all existing logger instances."""
        with self._lock:
            for logger in self._loggers.values():
                logger._setup_logger()

    def set_console_level(self, level: LogLevel):
        with self._lock:
            self.config.console_level = level
            self.reconfigure_all_loggers()

    def set_log_to_file_enabled(self, enabled: bool):
        with self._lock:
            if self.config.log_to_file != enabled:
                self.config.log_to_file = enabled
                self.reconfigure_all_loggers()

    def enable_debug_mode(self):
        self.set_console_level(LogLevel.DEBUG)
        os.environ["ASHYTERM_DEBUG"] = "1"

    def disable_debug_mode(self):
        self.set_console_level(LogLevel.INFO)
        os.environ.pop("ASHYTERM_DEBUG", None)

    def cleanup_old_logs(self, days_to_keep: int = 30):
        try:
            cutoff_time = datetime.now().timestamp() - (days_to_keep * 24 * 60 * 60)
            for log_file in self.config.log_dir.glob("*.log*"):
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
        except Exception as e:
            print(f"Error cleaning up old logs: {e}")


_logger_manager = LoggerManager()


def get_logger(name: str = None) -> ThreadSafeLogger:
    """Get a logger instance."""
    if name is None:
        import inspect

        frame = inspect.currentframe()
        try:
            name = frame.f_back.f_globals.get("__name__", "unknown")
        finally:
            del frame
    return _logger_manager.get_logger(name)


def set_console_log_level(level_str: str):
    """Set console logging level globally from a string."""
    try:
        level = LogLevel[level_str.upper()]
        _logger_manager.set_console_level(level)
    except KeyError:
        get_logger().error(f"Invalid log level string: {level_str}")


def set_log_to_file_enabled(enabled: bool):
    """Enable or disable logging to files globally."""
    _logger_manager.set_log_to_file_enabled(enabled)


def enable_debug_mode():
    """Enable debug mode for all loggers."""
    _logger_manager.enable_debug_mode()


def disable_debug_mode():
    """Disable debug mode for all loggers."""
    _logger_manager.disable_debug_mode()


def cleanup_old_logs(days_to_keep: int = 30):
    """Clean up old log files."""
    _logger_manager.cleanup_old_logs(days_to_keep)


def log_app_start():
    """Log application startup."""
    logger = get_logger("ashyterm.startup")
    logger.info("Ashy Terminal starting up")
    cleanup_old_logs()


def log_app_shutdown():
    """Log application shutdown."""
    logger = get_logger("ashyterm.shutdown")
    logger.info("Ashy Terminal shutting down")


def log_terminal_event(event_type: str, terminal_name: str, details: str = ""):
    """Log terminal-related events."""
    logger = get_logger("ashyterm.terminal")
    message = f"Terminal '{terminal_name}' {event_type}"
    if details:
        message += f": {details}"
    logger.info(message)


def log_session_event(event_type: str, item_name: str, details: str = ""):
    """Log session or folder-related events."""
    logger = get_logger("ashyterm.sessions")
    item_type = "Folder" if "folder" in event_type else "Session"
    message = f"{item_type} '{item_name}' {event_type.replace('folder_', '')}"
    if details:
        message += f": {details}"
    logger.info(message)


def log_error_with_context(error: Exception, context: str, logger_name: str = None):
    """Log an error with context information."""
    logger = get_logger(logger_name)
    logger.error(f"Error in {context}: {str(error)}", exc_info=True)
