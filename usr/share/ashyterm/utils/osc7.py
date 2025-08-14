"""
OSC7 Terminal Address Detection Utility

This module provides functionality to detect and parse OSC7 escape sequences
from terminal output to track current working directory changes.

OSC7 format: \033]7;file://hostname/path\007 or \033]7;file://hostname/path\033\\
"""

import re
import socket
from typing import Optional, NamedTuple
from urllib.parse import unquote
from pathlib import Path

from .logger import get_logger
from .platform import get_platform_info


class OSC7Info(NamedTuple):
    """Information extracted from OSC7 sequence."""
    hostname: str
    path: str
    display_path: str  # Human-readable path for display


class OSC7Parser:
    """Parser for OSC7 escape sequences."""
    
    # OSC7 pattern - matches both \007 and \033\\ terminators
    OSC7_PATTERN = re.compile(
        rb'\x1b\]7;file://([^/\x07\x1b]*)(/?[^\x07\x1b]*?)(?:\x07|\x1b\\)',
        re.IGNORECASE
    )
    
    def __init__(self):
        """Initialize OSC7 parser."""
        self.logger = get_logger('ashyterm.utils.osc7')
        self.platform_info = get_platform_info()
        
        # Cache for path shortening
        self._home_path = str(Path.home())
        
        self.logger.debug("OSC7 parser initialized")
    
    def parse_osc7(self, data: bytes) -> Optional[OSC7Info]:
        """
        Parse OSC7 escape sequences from terminal output.
        
        Args:
            data: Raw bytes from terminal output
            
        Returns:
            OSC7Info if valid sequence found, None otherwise
        """
        try:
            matches = self.OSC7_PATTERN.findall(data)
            if not matches:
                return None
            
            # Use the last match if multiple found
            hostname_bytes, path_bytes = matches[-1]
            
            # Decode bytes to strings
            try:
                hostname = hostname_bytes.decode('utf-8', errors='replace')
                raw_path = path_bytes.decode('utf-8', errors='replace')
            except UnicodeDecodeError as e:
                self.logger.warning(f"Failed to decode OSC7 sequence: {e}")
                return None
            
            # URL decode the path
            try:
                decoded_path = unquote(raw_path)
            except Exception as e:
                self.logger.warning(f"Failed to URL decode path '{raw_path}': {e}")
                decoded_path = raw_path
            
            # Normalize and validate path
            normalized_path = self._normalize_path(decoded_path)
            if not normalized_path:
                return None
            
            # Create display path (shortened for UI)
            display_path = self._create_display_path(normalized_path)
            
            osc7_info = OSC7Info(
                hostname=hostname or 'localhost',
                path=normalized_path,
                display_path=display_path
            )
            
            self.logger.debug(f"Parsed OSC7: {osc7_info}")
            return osc7_info
            
        except Exception as e:
            self.logger.error(f"OSC7 parsing failed: {e}")
            return None
    
    def _normalize_path(self, path: str) -> Optional[str]:
        """
        Normalize and validate the path from OSC7.
        
        Args:
            path: Raw path from OSC7 sequence
            
        Returns:
            Normalized path or None if invalid
        """
        try:
            if not path:
                return None
            
            # Handle empty or root paths
            if not path or path == '/':
                return '/'
            
            # Remove trailing slashes except for root
            normalized = path.rstrip('/')
            if not normalized:
                normalized = '/'
            
            # Basic validation - must be absolute path
            if not normalized.startswith('/'):
                self.logger.warning(f"OSC7 path is not absolute: '{path}'")
                return None
            
            # Additional validation for obviously invalid paths
            if len(normalized) > 4096:  # Reasonable path length limit
                self.logger.warning(f"OSC7 path too long: {len(normalized)} chars")
                return None
            
            return normalized
            
        except Exception as e:
            self.logger.error(f"Path normalization failed for '{path}': {e}")
            return None
    
    def _create_display_path(self, path: str) -> str:
        """
        Create a user-friendly display version of the path.
        
        Args:
            path: Normalized absolute path
            
        Returns:
            Display-friendly path string
        """
        try:
            if not path or path == '/':
                return '/'
            
            # Replace home directory with ~
            if path.startswith(self._home_path):
                if path == self._home_path:
                    return path
                else:
                    return '~' + path[len(self._home_path):]
            
            # For very long paths, show only the last few components
            path_parts = path.split('/')
            if len(path_parts) > 4:  # More than 3 directories deep
                return '.../' + '/'.join(path_parts[-3:])
            
            return path
            
        except Exception as e:
            self.logger.warning(f"Display path creation failed for '{path}': {e}")
            return path  # Fallback to original path


class OSC7Buffer:
    """Buffer for collecting partial OSC7 sequences across multiple data chunks."""
    
    def __init__(self, max_size: int = 2048):
        """
        Initialize OSC7 buffer.
        
        Args:
            max_size: Maximum buffer size to prevent memory issues
        """
        self.logger = get_logger('ashyterm.utils.osc7.buffer')
        self.max_size = max_size
        self._buffer = bytearray()
        
    def add_data(self, data: bytes) -> bytes:
        """
        Add data to buffer and return complete buffer for parsing.
        
        Args:
            data: New data chunk from terminal
            
        Returns:
            Complete buffer data ready for parsing
        """
        try:
            # Add new data to buffer
            self._buffer.extend(data)
            
            # Prevent buffer from growing too large
            if len(self._buffer) > self.max_size:
                # Keep only the last portion that might contain a complete sequence
                self._buffer = self._buffer[-self.max_size//2:]
                self.logger.debug("OSC7 buffer trimmed to prevent overflow")
            
            return bytes(self._buffer)
            
        except Exception as e:
            self.logger.error(f"OSC7 buffer add_data failed: {e}")
            self.clear()
            return data  # Return just the new data as fallback
    
    def clear_processed(self, parsed_data: bytes) -> None:
        """
        Clear buffer up to the end of successfully parsed data.
        
        Args:
            parsed_data: Data that was successfully parsed
        """
        try:
            if parsed_data and len(parsed_data) <= len(self._buffer):
                # Remove the parsed portion
                self._buffer = self._buffer[len(parsed_data):]
                
        except Exception as e:
            self.logger.error(f"OSC7 buffer clear_processed failed: {e}")
            self.clear()
    
    def clear(self) -> None:
        """Clear the entire buffer."""
        self._buffer.clear()
    
    def size(self) -> int:
        """Get current buffer size."""
        return len(self._buffer)


# Utility functions for easy integration
def create_osc7_parser() -> OSC7Parser:
    """Create and return a new OSC7 parser instance."""
    return OSC7Parser()


def parse_osc7_quick(data: bytes) -> Optional[OSC7Info]:
    """
    Quick one-off OSC7 parsing without maintaining a parser instance.
    
    Args:
        data: Terminal output data
        
    Returns:
        OSC7Info if found, None otherwise
    """
    parser = OSC7Parser()
    return parser.parse_osc7(data)


def format_tab_title(base_title: str, osc7_info: OSC7Info, 
                    show_hostname: bool = False) -> str:
    """
    Format a tab title with OSC7 directory information.
    
    Args:
        base_title: Original tab title
        osc7_info: OSC7 information
        show_hostname: Whether to include hostname in title
        
    Returns:
        Formatted tab title
    """
    try:
        if show_hostname and osc7_info.hostname != socket.gethostname():
            return f"{base_title} - {osc7_info.hostname}:{osc7_info.display_path}"
        else:
            return f"{osc7_info.display_path}"
            
    except Exception:
        return base_title  # Fallback to original title
