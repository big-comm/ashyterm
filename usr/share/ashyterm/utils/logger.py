"""
Structured logging system for Ashy Terminal.

This module provides a centralized logging system with different levels,
formatters, and handlers for debugging, monitoring, and error tracking.
"""

import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union
from enum import Enum


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
        # Create logs directory
        self.log_dir = Path.home() / ".config" / "ashyterm" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Log file paths
        self.main_log_file = self.log_dir / "ashyterm.log"
        self.error_log_file = self.log_dir / "ashyterm_errors.log"
        self.debug_log_file = self.log_dir / "ashyterm_debug.log"
        
        # Log settings
        self.max_file_size = 10 * 1024 * 1024  # 10MB
        self.backup_count = 5
        self.console_level = LogLevel.INFO
        self.file_level = LogLevel.DEBUG
        self.error_file_level = LogLevel.ERROR


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output."""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }
    
    def format(self, record):
        # Add color to levelname
        levelname = record.levelname
        if levelname in self.COLORS:
            colored_levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
            record.levelname = colored_levelname
        
        # Format the message
        formatted = super().format(record)
        
        # Reset levelname for other handlers
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
        """Set up the logger with handlers and formatters."""
        with self._lock:
            # Guard against re-configuration using a custom attribute.
            # This is more reliable than checking for existing handlers.
            if getattr(self._logger, "_ashyterm_configured", False):
                return

            # CRITICAL FIX: Disable propagation to the root logger.
            # This prevents messages from being handled twice if the root logger
            # also has handlers configured (which is a common cause of duplicate logs).
            self._logger.propagate = False

            self._logger.setLevel(logging.DEBUG)
            
            # Console handler
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.config.console_level.value)
            console_formatter = ColoredFormatter(
                fmt='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
                datefmt='%H:%M:%S'
            )
            console_handler.setFormatter(console_formatter)
            self._logger.addHandler(console_handler)
            
            # Main file handler (rotating)
            main_file_handler = logging.handlers.RotatingFileHandler(
                self.config.main_log_file,
                maxBytes=self.config.max_file_size,
                backupCount=self.config.backup_count,
                encoding='utf-8'
            )
            main_file_handler.setLevel(self.config.file_level.value)
            file_formatter = logging.Formatter(
                fmt='%(asctime)s | %(name)s | %(levelname)s | %(funcName)s:%(lineno)d | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            main_file_handler.setFormatter(file_formatter)
            self._logger.addHandler(main_file_handler)
            
            # Error file handler (errors only)
            error_file_handler = logging.handlers.RotatingFileHandler(
                self.config.error_log_file,
                maxBytes=self.config.max_file_size,
                backupCount=self.config.backup_count,
                encoding='utf-8'
            )
            error_file_handler.setLevel(self.config.error_file_level.value)
            error_file_handler.setFormatter(file_formatter)
            self._logger.addHandler(error_file_handler)
            
            # Debug file handler (debug only, if enabled)
            if os.environ.get('ASHYTERM_DEBUG', '').lower() in ('1', 'true', 'yes'):
                debug_file_handler = logging.handlers.RotatingFileHandler(
                    self.config.debug_log_file,
                    maxBytes=self.config.max_file_size,
                    backupCount=2,
                    encoding='utf-8'
                )
                debug_file_handler.setLevel(logging.DEBUG)
                debug_formatter = logging.Formatter(
                    fmt='%(asctime)s | %(name)s | DEBUG | %(funcName)s:%(lineno)d | %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                )
                debug_file_handler.setFormatter(debug_formatter)
                debug_file_handler.addFilter(lambda record: record.levelno == logging.DEBUG)
                self._logger.addHandler(debug_file_handler)

            # Mark this logger as configured by our system.
            self._logger._ashyterm_configured = True

    def debug(self, message: str, **kwargs):
        """Log debug message."""
        self._logger.debug(message, **kwargs)
    
    def info(self, message: str, **kwargs):
        """Log info message."""
        self._logger.info(message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message."""
        self._logger.warning(message, **kwargs)
    
    def error(self, message: str, exc_info: bool = False, **kwargs):
        """Log error message."""
        self._logger.error(message, exc_info=exc_info, **kwargs)
    
    def critical(self, message: str, exc_info: bool = True, **kwargs):
        """Log critical message."""
        self._logger.critical(message, exc_info=exc_info, **kwargs)
    
    def exception(self, message: str, **kwargs):
        """Log exception with traceback."""
        self._logger.exception(message, **kwargs)


class LoggerManager:
    """Centralized logger manager."""
    
    _instance: Optional['LoggerManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> 'LoggerManager':
        """Singleton pattern implementation."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self.config = LoggerConfig()
        self._loggers: Dict[str, ThreadSafeLogger] = {}
        self._setup_root_logger()
    
    def _setup_root_logger(self):
        """Set up the root logger configuration."""
        # Disable other loggers to prevent interference
        logging.getLogger('gi').setLevel(logging.WARNING)
        logging.getLogger('Vte').setLevel(logging.WARNING)
        logging.getLogger('Gtk').setLevel(logging.WARNING)
    
    def get_logger(self, name: str) -> ThreadSafeLogger:
        """
        Get or create a logger with the given name.
        
        Args:
            name: Logger name (usually module name)
            
        Returns:
            ThreadSafeLogger instance
        """
        if name not in self._loggers:
            with self._lock:
                if name not in self._loggers:
                    self._loggers[name] = ThreadSafeLogger(name, self.config)
        
        return self._loggers[name]
    
    def set_console_level(self, level: LogLevel):
        """Set console logging level for all loggers."""
        with self._lock:
            self.config.console_level = level
            # Update existing loggers
            for logger in self._loggers.values():
                for handler in logger._logger.handlers:
                    if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
                        handler.setLevel(level.value)
    
    def enable_debug_mode(self):
        """Enable debug mode with verbose logging."""
        self.set_console_level(LogLevel.DEBUG)
        os.environ['ASHYTERM_DEBUG'] = '1'
    
    def disable_debug_mode(self):
        """Disable debug mode."""
        self.set_console_level(LogLevel.INFO)
        os.environ.pop('ASHYTERM_DEBUG', None)
    
    def cleanup_old_logs(self, days_to_keep: int = 30):
        """
        Clean up old log files.
        
        Args:
            days_to_keep: Number of days to keep logs
        """
        try:
            cutoff_time = datetime.now().timestamp() - (days_to_keep * 24 * 60 * 60)
            
            for log_file in self.config.log_dir.glob("*.log*"):
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
                    
        except Exception as e:
            # Use basic logging since our logger might not be set up yet
            print(f"Error cleaning up old logs: {e}")
    
    def get_log_info(self) -> Dict[str, Any]:
        """
        Get information about current logging setup.
        
        Returns:
            Dictionary with logging information
        """
        return {
            'log_dir': str(self.config.log_dir),
            'main_log': str(self.config.main_log_file),
            'error_log': str(self.config.error_log_file),
            'debug_log': str(self.config.debug_log_file),
            'console_level': self.config.console_level.name,
            'file_level': self.config.file_level.name,
            'debug_enabled': os.environ.get('ASHYTERM_DEBUG', '').lower() in ('1', 'true', 'yes'),
            'active_loggers': list(self._loggers.keys())
        }


# Global logger manager instance
_logger_manager = LoggerManager()


def get_logger(name: str = None) -> ThreadSafeLogger:
    """
    Get a logger instance.
    
    Args:
        name: Logger name (defaults to calling module)
        
    Returns:
        ThreadSafeLogger instance
    """
    if name is None:
        # Get the calling module name
        import inspect
        frame = inspect.currentframe()
        try:
            caller_frame = frame.f_back
            caller_module = caller_frame.f_globals.get('__name__', 'unknown')
            name = caller_module
        finally:
            del frame
    
    return _logger_manager.get_logger(name)


def set_console_level(level: Union[LogLevel, str]):
    """
    Set console logging level globally.
    
    Args:
        level: LogLevel enum or string ('DEBUG', 'INFO', etc.)
    """
    if isinstance(level, str):
        level = LogLevel[level.upper()]
    _logger_manager.set_console_level(level)


def enable_debug_mode():
    """Enable debug mode for all loggers."""
    _logger_manager.enable_debug_mode()


def disable_debug_mode():
    """Disable debug mode for all loggers."""
    _logger_manager.disable_debug_mode()


def cleanup_old_logs(days_to_keep: int = 30):
    """Clean up old log files."""
    _logger_manager.cleanup_old_logs(days_to_keep)


def get_log_info() -> Dict[str, Any]:
    """Get logging system information."""
    return _logger_manager.get_log_info()


# Context manager for temporary log level changes
class TemporaryLogLevel:
    """Context manager for temporary log level changes."""
    
    def __init__(self, level: Union[LogLevel, str]):
        self.new_level = level if isinstance(level, LogLevel) else LogLevel[level.upper()]
        self.old_level = None
    
    def __enter__(self):
        self.old_level = _logger_manager.config.console_level
        _logger_manager.set_console_level(self.new_level)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        _logger_manager.set_console_level(self.old_level)


# Convenience functions for quick logging
def log_app_start():
    """Log application startup."""
    logger = get_logger('ashyterm.startup')
    logger.info("Ashy Terminal starting up")
    cleanup_old_logs()  # Clean up old logs on startup


def log_app_shutdown():
    """Log application shutdown."""
    logger = get_logger('ashyterm.shutdown')
    logger.info("Ashy Terminal shutting down")


def log_terminal_event(event_type: str, terminal_name: str, details: str = ""):
    """
    Log terminal-related events.
    
    Args:
        event_type: Type of event (created, closed, error, etc.)
        terminal_name: Name/identifier of the terminal
        details: Additional details about the event
    """
    logger = get_logger('ashyterm.terminal')
    message = f"Terminal '{terminal_name}' {event_type}"
    if details:
        message += f": {details}"
    logger.info(message)


def log_session_event(event_type: str, item_name: str, details: str = ""):
    """
    Log session or folder-related events.
    
    Args:
        event_type: Type of event (created, folder_modified, etc.)
        item_name: Name of the session or folder
        details: Additional details about the event
    """
    logger = get_logger('ashyterm.sessions')
    # Determina o tipo de item a partir do tipo de evento
    item_type = "Folder" if "folder" in event_type else "Session"
    message = f"{item_type} '{item_name}' {event_type.replace('folder_', '')}"
    if details:
        message += f": {details}"
    logger.info(message)


def log_error_with_context(error: Exception, context: str, logger_name: str = None):
    """
    Log an error with context information.
    
    Args:
        error: Exception that occurred
        context: Context where the error occurred
        logger_name: Name of logger to use (auto-detected if None)
    """
    logger = get_logger(logger_name)
    logger.error(f"Error in {context}: {str(error)}", exc_info=True)