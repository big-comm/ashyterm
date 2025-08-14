"""
OSC7 Terminal Directory Tracker

This module provides a terminal tracker that monitors VTE terminals for OSC7
escape sequences and updates tab titles accordingly.
"""

import threading
from typing import Dict, Optional, Callable, Any
from weakref import WeakKeyDictionary

import gi
gi.require_version("Vte", "3.91")
from gi.repository import Vte, GLib

from .osc7 import OSC7Parser, OSC7Buffer, OSC7Info, format_tab_title
from .logger import get_logger


class OSC7TerminalTracker:
    """
    Tracks OSC7 sequences from VTE terminals and manages directory information.
    """
    
    def __init__(self, settings_manager=None):
        """
        Initialize OSC7 terminal tracker.
        
        Args:
            settings_manager: Settings manager for configuration
        """
        self.logger = get_logger('ashyterm.utils.osc7.tracker')
        self.settings_manager = settings_manager
        
        # OSC7 processing
        self.parser = OSC7Parser()
        
        # Terminal tracking
        self._terminals: WeakKeyDictionary[Vte.Terminal, Dict[str, Any]] = WeakKeyDictionary()
        self._lock = threading.RLock()
        
        # Callback for tab title updates
        self.on_directory_changed: Optional[Callable[[Vte.Terminal, OSC7Info], None]] = None
        
        self.logger.debug("OSC7 terminal tracker initialized")
    
    def track_terminal(self, terminal: Vte.Terminal, base_title: str = "Terminal") -> None:
        """
        Start tracking OSC7 sequences for a terminal.
        
        Args:
            terminal: VTE terminal to track
            base_title: Base title for the terminal tab
        """
        if not self.is_osc7_enabled():
            self.logger.debug("OSC7 tracking disabled in settings")
            return
        
        try:
            with self._lock:
                if terminal in self._terminals:
                    self.logger.debug("Terminal already being tracked")
                    return
                
                # Create tracking data for this terminal
                self._terminals[terminal] = {
                    'base_title': base_title,
                    'buffer': OSC7Buffer(),
                    'current_dir': None,
                    'last_osc7': None
                }
                
                # Connect to terminal contents-changed signal
                # VTE sends this signal when terminal content changes
                terminal.connect("contents-changed", self._on_terminal_contents_changed)
                
                self.logger.info(f"Started OSC7 tracking for terminal: '{base_title}'")
                
        except Exception as e:
            self.logger.error(f"Failed to track terminal '{base_title}': {e}")
    
    def untrack_terminal(self, terminal: Vte.Terminal) -> None:
        """
        Stop tracking OSC7 sequences for a terminal.
        
        Args:
            terminal: VTE terminal to stop tracking
        """
        try:
            with self._lock:
                if terminal in self._terminals:
                    terminal_data = self._terminals[terminal]
                    base_title = terminal_data.get('base_title', 'Terminal')
                    
                    # The WeakKeyDictionary will automatically clean up the entry
                    # when the terminal is garbage collected
                    del self._terminals[terminal]
                    
                    self.logger.debug(f"Stopped OSC7 tracking for terminal: '{base_title}'")
                    
        except Exception as e:
            self.logger.error(f"Failed to untrack terminal: {e}")
    
    def _on_terminal_contents_changed(self, terminal: Vte.Terminal) -> None:
        """
        Handle terminal contents change - check for OSC7 sequences.
        
        This approach monitors the terminal's visible content for OSC7 sequences.
        Since VTE processes escape sequences internally, we need to look for
        changes in the terminal's current directory properties or parse recent output.
        
        Args:
            terminal: Terminal that had content change
        """
        try:
            with self._lock:
                if terminal not in self._terminals:
                    return
                
                terminal_data = self._terminals[terminal]
                
                # Try to get current working directory via VTE's internal tracking
                # This is more reliable than parsing OSC7 from output
                try:
                    # VTE internally tracks current directory via OSC7
                    current_uri = terminal.get_current_directory_uri()
                    if current_uri:
                        self._handle_directory_uri_change(terminal, terminal_data, current_uri)
                        return
                except Exception as e:
                    # get_current_directory_uri might not be available or working
                    self.logger.debug(f"Could not get current directory URI: {e}")
                
                # Fallback: Parse recent terminal output for OSC7 sequences
                # This is less reliable but works when VTE doesn't track directories
                self._parse_recent_terminal_output(terminal, terminal_data)
                
        except Exception as e:
            self.logger.error(f"Terminal contents change processing failed: {e}")
    
    def _handle_directory_uri_change(self, terminal: Vte.Terminal, 
                                   terminal_data: Dict[str, Any], 
                                   directory_uri: str) -> None:
        """
        Handle directory change detected from VTE's current directory URI.
        
        Args:
            terminal: Terminal that changed
            terminal_data: Terminal tracking data
            directory_uri: New directory URI (file:// format)
        """
        try:
            from urllib.parse import urlparse, unquote
            
            # Parse the file:// URI to get the path
            parsed_uri = urlparse(directory_uri)
            if parsed_uri.scheme != 'file':
                return
            
            path = unquote(parsed_uri.path)
            hostname = parsed_uri.hostname or 'localhost'
            
            # Create OSC7Info from the URI
            display_path = self.parser._create_display_path(path)
            osc7_info = OSC7Info(
                hostname=hostname,
                path=path,
                display_path=display_path
            )
            
            # Check if directory actually changed
            last_osc7 = terminal_data.get('last_osc7')
            if last_osc7 and last_osc7.path == osc7_info.path:
                return  # No change
            
            self._handle_osc7_detected(terminal, terminal_data, osc7_info)
            
        except Exception as e:
            self.logger.error(f"Directory URI change handling failed: {e}")
    
    def _parse_recent_terminal_output(self, terminal: Vte.Terminal, 
                                    terminal_data: Dict[str, Any]) -> None:
        """
        Parse recent terminal output for OSC7 sequences (fallback method).
        
        Args:
            terminal: Terminal to parse
            terminal_data: Terminal tracking data
        """
        try:
            # This is a fallback method when VTE doesn't provide directory URI
            # Unfortunately, VTE doesn't provide direct access to raw output stream
            # including escape sequences, so this is limited
            
            # We could try to get terminal text, but OSC7 sequences are typically
            # not visible in the terminal buffer as they're processed by VTE
            
            # For now, we'll rely primarily on the VTE directory URI method
            # and this serves as a placeholder for any future implementation
            
            self.logger.debug("Using fallback OSC7 parsing (limited functionality)")
            
        except Exception as e:
            self.logger.error(f"Fallback OSC7 parsing failed: {e}")
    
    def _handle_osc7_detected(self, terminal: Vte.Terminal, 
                            terminal_data: Dict[str, Any], 
                            osc7_info: OSC7Info) -> None:
        """
        Handle detected OSC7 sequence and update tab title.
        
        Args:
            terminal: Terminal that sent the OSC7
            terminal_data: Tracking data for the terminal
            osc7_info: Parsed OSC7 information
        """
        try:
            # Check if directory actually changed
            last_osc7 = terminal_data.get('last_osc7')
            if last_osc7 and last_osc7.path == osc7_info.path:
                return  # No change
            
            # Update tracking data
            terminal_data['current_dir'] = osc7_info.path
            terminal_data['last_osc7'] = osc7_info
            
            self.logger.debug(f"Directory changed to: {osc7_info.path}")
            
            # Schedule tab title update on main thread
            GLib.idle_add(self._update_tab_title_safe, terminal, terminal_data, osc7_info)
            
            # Call external callback if set
            if self.on_directory_changed:
                GLib.idle_add(self._call_callback_safe, terminal, osc7_info)
                
        except Exception as e:
            self.logger.error(f"OSC7 handling failed: {e}")
    
    def _update_tab_title_safe(self, terminal: Vte.Terminal, 
                             terminal_data: Dict[str, Any], 
                             osc7_info: OSC7Info) -> bool:
        """
        Safely update tab title on main thread.
        
        Args:
            terminal: Terminal to update title for
            terminal_data: Terminal tracking data
            osc7_info: OSC7 information
            
        Returns:
            False to remove from idle queue
        """
        try:
            base_title = terminal_data.get('base_title', 'Terminal')
            show_hostname = self.get_osc7_show_hostname()
            
            # Format new title
            new_title = format_tab_title(base_title, osc7_info, show_hostname)
            
            # Store formatted title for external access
            terminal_data['current_title'] = new_title
            
            self.logger.debug(f"Updated tab title to: '{new_title}'")
            
        except Exception as e:
            self.logger.error(f"Tab title update failed: {e}")
        
        return False  # Remove from idle queue
    
    def _call_callback_safe(self, terminal: Vte.Terminal, osc7_info: OSC7Info) -> bool:
        """
        Safely call external callback on main thread.
        
        Args:
            terminal: Terminal that changed directory
            osc7_info: OSC7 information
            
        Returns:
            False to remove from idle queue
        """
        try:
            if self.on_directory_changed:
                self.on_directory_changed(terminal, osc7_info)
        except Exception as e:
            self.logger.error(f"OSC7 callback failed: {e}")
        
        return False  # Remove from idle queue
    
    def get_current_directory(self, terminal: Vte.Terminal) -> Optional[str]:
        """
        Get current directory for a terminal.
        
        Args:
            terminal: Terminal to get directory for
            
        Returns:
            Current directory path or None if unknown
        """
        try:
            with self._lock:
                if terminal in self._terminals:
                    return self._terminals[terminal].get('current_dir')
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to get current directory: {e}")
            return None
    
    def get_current_title(self, terminal: Vte.Terminal) -> Optional[str]:
        """
        Get current formatted title for a terminal.
        
        Args:
            terminal: Terminal to get title for
            
        Returns:
            Current formatted title or None if not tracked
        """
        try:
            with self._lock:
                if terminal in self._terminals:
                    return self._terminals[terminal].get('current_title')
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to get current title: {e}")
            return None
    
    def update_base_title(self, terminal: Vte.Terminal, new_base_title: str) -> None:
        """
        Update base title for a terminal and refresh if directory is known.
        
        Args:
            terminal: Terminal to update
            new_base_title: New base title
        """
        try:
            with self._lock:
                if terminal in self._terminals:
                    terminal_data = self._terminals[terminal]
                    terminal_data['base_title'] = new_base_title
                    
                    # If we have current directory info, update the title
                    osc7_info = terminal_data.get('last_osc7')
                    if osc7_info:
                        GLib.idle_add(self._update_tab_title_safe, terminal, terminal_data, osc7_info)
                    
        except Exception as e:
            self.logger.error(f"Failed to update base title: {e}")
    
    def is_osc7_enabled(self) -> bool:
        """Check if OSC7 tracking is enabled in settings."""
        try:
            if self.settings_manager:
                return self.settings_manager.get("osc7_enabled", True)
            return True  # Default enabled
        except Exception:
            return True
    
    def get_osc7_show_hostname(self) -> bool:
        """Check if hostname should be shown in tab titles."""
        try:
            if self.settings_manager:
                return self.settings_manager.get("osc7_show_hostname", False)
            return False  # Default disabled
        except Exception:
            return False
    
    def get_tracked_terminal_count(self) -> int:
        """Get number of currently tracked terminals."""
        try:
            with self._lock:
                return len(self._terminals)
        except Exception:
            return 0
    
    def cleanup(self) -> None:
        """Clean up tracker resources."""
        try:
            with self._lock:
                self._terminals.clear()
            self.logger.debug("OSC7 terminal tracker cleaned up")
        except Exception as e:
            self.logger.error(f"OSC7 tracker cleanup failed: {e}")


# Global tracker instance
_global_tracker: Optional[OSC7TerminalTracker] = None
_tracker_lock = threading.Lock()


def get_osc7_tracker(settings_manager=None) -> OSC7TerminalTracker:
    """
    Get global OSC7 tracker instance.
    
    Args:
        settings_manager: Settings manager (used only on first call)
        
    Returns:
        OSC7TerminalTracker instance
    """
    global _global_tracker
    
    with _tracker_lock:
        if _global_tracker is None:
            _global_tracker = OSC7TerminalTracker(settings_manager)
        return _global_tracker


def cleanup_osc7_tracker() -> None:
    """Clean up global OSC7 tracker."""
    global _global_tracker
    
    with _tracker_lock:
        if _global_tracker:
            _global_tracker.cleanup()
            _global_tracker = None
