from typing import List, Optional, Union, Callable, Any, Dict
import threading
import time
import weakref
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")

from gi.repository import Gtk, Gdk, GLib
from gi.repository import Vte

from ..sessions.models import SessionItem
from ..settings.manager import SettingsManager
from ..settings.config import VTE_AVAILABLE
from ..ui.menus import create_terminal_menu, setup_context_menu
from .spawner import get_spawner

# Import new utility systems
from ..utils.logger import get_logger, log_terminal_event, log_error_with_context
from ..utils.exceptions import (
    TerminalError, TerminalCreationError, TerminalSpawnError, VTENotAvailableError,
    handle_exception, ErrorCategory, ErrorSeverity, AshyTerminalError
)
from ..utils.security import validate_session_data
from ..utils.platform import get_platform_info, get_environment_manager


class TerminalRegistry:
    """Registry for tracking terminal instances and their metadata."""
    
    def __init__(self):
        self.logger = get_logger('ashyterm.terminal.registry')
        self._terminals: Dict[int, Dict[str, Any]] = {}
        self._terminal_refs: Dict[int, weakref.ReferenceType] = {}
        self._lock = threading.RLock()
        self._next_id = 1
    
    def register_terminal(self, terminal: Vte.Terminal, 
                         terminal_type: str, identifier: Union[str, SessionItem]) -> int:
        """
        Register a terminal with the registry.
        
        Args:
            terminal: Terminal widget
            terminal_type: Type of terminal ('local' or 'ssh')
            identifier: Terminal identifier
            
        Returns:
            Terminal ID
        """
        with self._lock:
            terminal_id = self._next_id
            self._next_id += 1
            
            # Store terminal metadata
            self._terminals[terminal_id] = {
                'type': terminal_type,
                'identifier': identifier,
                'created_at': time.time(),
                'process_id': None,
                'status': 'initializing'
            }
            
            # Store weak reference to terminal
            def cleanup_callback(ref):
                self._cleanup_terminal_ref(terminal_id)
            
            self._terminal_refs[terminal_id] = weakref.ref(terminal, cleanup_callback)
            
            self.logger.debug(f"Terminal registered: ID={terminal_id}, type={terminal_type}")
            return terminal_id
    
    def update_terminal_process(self, terminal_id: int, process_id: int) -> None:
        """Update terminal with process ID."""
        with self._lock:
            if terminal_id in self._terminals:
                self._terminals[terminal_id]['process_id'] = process_id
                self._terminals[terminal_id]['status'] = 'running'
                self.logger.debug(f"Terminal {terminal_id} process updated: PID={process_id}")
    
    def update_terminal_status(self, terminal_id: int, status: str) -> None:
        """Update terminal status."""
        with self._lock:
            if terminal_id in self._terminals:
                self._terminals[terminal_id]['status'] = status
                self.logger.debug(f"Terminal {terminal_id} status updated: {status}")
    
    def get_terminal(self, terminal_id: int) -> Optional[Vte.Terminal]:
        """Get terminal by ID."""
        with self._lock:
            ref = self._terminal_refs.get(terminal_id)
            if ref:
                return ref()
            return None
    
    def get_terminal_info(self, terminal_id: int) -> Optional[Dict[str, Any]]:
        """Get terminal metadata."""
        with self._lock:
            return self._terminals.get(terminal_id, {}).copy()
    
    def unregister_terminal(self, terminal_id: int) -> bool:
        """Unregister terminal from registry."""
        with self._lock:
            if terminal_id in self._terminals:
                del self._terminals[terminal_id]
                if terminal_id in self._terminal_refs:
                    del self._terminal_refs[terminal_id]
                self.logger.debug(f"Terminal unregistered: ID={terminal_id}")
                return True
            return False
    
    def _cleanup_terminal_ref(self, terminal_id: int) -> None:
        """Clean up terminal reference when terminal is garbage collected."""
        with self._lock:
            if terminal_id in self._terminal_refs:
                del self._terminal_refs[terminal_id]
                self.logger.debug(f"Terminal reference cleaned up: ID={terminal_id}")
    
    def get_all_terminal_ids(self) -> List[int]:
        """Get list of all registered terminal IDs."""
        with self._lock:
            return list(self._terminals.keys())
    
    def get_terminal_count(self) -> int:
        """Get number of registered terminals."""
        with self._lock:
            return len(self._terminals)


class TerminalManager:
    """Enhanced terminal manager with comprehensive functionality."""
    
    def __init__(self, parent_window, settings_manager: SettingsManager):
        """
        Initialize terminal manager.
        
        Args:
            settings_manager: SettingsManager instance for applying settings
        """
        self.logger = get_logger('ashyterm.terminal.manager')
        self.parent_window = parent_window
        self.settings_manager = settings_manager
        self.platform_info = get_platform_info()
        self.environment_manager = get_environment_manager()
        
        # Terminal management
        self.registry = TerminalRegistry()
        self.spawner = get_spawner()
        
        # Thread safety
        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        
        # Security auditor removed
        self.security_auditor = None
        
        # Callbacks for terminal events
        self.on_terminal_child_exited: Optional[Callable] = None
        self.on_terminal_eof: Optional[Callable] = None
        self.on_terminal_focus_changed: Optional[Callable] = None
        self.on_terminal_should_close: Optional[Callable] = None
        
        # Statistics
        self._stats = {
            'terminals_created': 0,
            'terminals_failed': 0,
            'terminals_closed': 0
        }
        
        self.logger.info("Terminal manager initialized")
    
    def create_local_terminal(self, title: str = "Local Terminal") -> Optional[Vte.Terminal]:
        """
        Create a new local terminal with enhanced error handling.
        
        Args:
            title: Title for the terminal
            
        Returns:
            Configured Vte.Terminal widget or None if creation failed
        """
        with self._creation_lock:
            if not VTE_AVAILABLE:
                self.logger.error("VTE not available for local terminal creation")
                raise VTENotAvailableError()
            
            try:
                self.logger.debug(f"Creating local terminal: '{title}'")
                
                # Create base terminal
                terminal = self._create_base_terminal()
                if not terminal:
                    raise TerminalCreationError("base terminal creation failed", "local")
                
                # Register terminal
                terminal_id = self.registry.register_terminal(terminal, 'local', title)
                
                # Set up terminal-specific properties
                self._setup_terminal_events(terminal, title, terminal_id)
                
                # Spawn local process
                success = self.spawner.spawn_local_terminal(
                    terminal,
                    lambda t, pid, error, data: self._on_spawn_callback(t, pid, error, data, terminal_id),
                    title
                )
                
                if success:
                    self.logger.info(f"Local terminal created successfully: '{title}' (ID: {terminal_id})")
                    log_terminal_event("created", title, "local terminal")
                    self._stats['terminals_created'] += 1
                    return terminal
                else:
                    self.registry.unregister_terminal(terminal_id)
                    self._stats['terminals_failed'] += 1
                    raise TerminalSpawnError("shell", "Local process spawn failed")
                
            except Exception as e:
                self._stats['terminals_failed'] += 1
                self.logger.error(f"Failed to create local terminal '{title}': {e}")
                
                if isinstance(e, (TerminalCreationError, TerminalSpawnError, VTENotAvailableError)):
                    raise
                else:
                    raise TerminalCreationError(str(e), "local")
    
    def create_ssh_terminal(self, session: SessionItem) -> Optional[Vte.Terminal]:
        """
        Create a new SSH terminal with security validation.
        
        Args:
            session: SessionItem with SSH configuration
            
        Returns:
            Configured Vte.Terminal widget or None if creation failed
        """
        with self._creation_lock:
            if not VTE_AVAILABLE:
                self.logger.error("VTE not available for SSH terminal creation")
                raise VTENotAvailableError()
            
            try:
                self.logger.debug(f"Creating SSH terminal for session: '{session.name}'")
                
                # Validate session data
                session_data = session.to_dict()
                is_valid, errors = validate_session_data(session_data)
                if not is_valid:
                    error_msg = f"Session validation failed: {', '.join(errors)}"
                    raise TerminalCreationError(error_msg, "ssh")
                
                # Security audit removed
                
                # Create base terminal
                terminal = self._create_base_terminal()
                if not terminal:
                    raise TerminalCreationError("base terminal creation failed", "ssh")
                
                # Register terminal
                terminal_id = self.registry.register_terminal(terminal, 'ssh', session)
                
                # Set up terminal-specific properties
                self._setup_terminal_events(terminal, session, terminal_id)
                
                # Spawn SSH process
                success = self.spawner.spawn_ssh_session(
                    terminal,
                    session,
                    lambda t, pid, error, data: self._on_spawn_callback(t, pid, error, data, terminal_id),
                    session
                )
                
                if success:
                    self.logger.info(f"SSH terminal created successfully: '{session.name}' (ID: {terminal_id})")
                    log_terminal_event("created", session.name, f"SSH to {session.get_connection_string()}")
                    self._stats['terminals_created'] += 1
                    return terminal
                else:
                    self.registry.unregister_terminal(terminal_id)
                    self._stats['terminals_failed'] += 1
                    raise TerminalSpawnError(f"ssh://{session.get_connection_string()}", "SSH process spawn failed")
                
            except Exception as e:
                self._stats['terminals_failed'] += 1
                self.logger.error(f"Failed to create SSH terminal for '{session.name}': {e}")
                
                if isinstance(e, (TerminalCreationError, TerminalSpawnError, VTENotAvailableError)):
                    raise
                else:
                    raise TerminalCreationError(str(e), "ssh")
    
    def _create_base_terminal(self) -> Optional[Vte.Terminal]:
        """
        Create and configure a base terminal widget.
        
        Returns:
            Configured Vte.Terminal widget or None if creation failed
        """
        try:
            self.logger.debug("Creating base VTE terminal widget")
            terminal = Vte.Terminal()
            
            # Basic terminal properties
            terminal.set_vexpand(True)
            terminal.set_hexpand(True)
            terminal.set_mouse_autohide(True)
            terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            terminal.set_scroll_on_output(True)
            terminal.set_scroll_on_keystroke(True)
            
            # Platform-specific configurations
            if self.platform_info.is_windows():
                # Windows-specific terminal settings
                terminal.set_encoding('utf-8')
            
            # Apply current settings
            self.settings_manager.apply_terminal_settings(terminal, self.parent_window)
            
            # Set up context menu
            self._setup_context_menu(terminal)
            
            self.logger.debug("Base terminal created and configured")
            return terminal
            
        except Exception as e:
            self.logger.error(f"Base terminal creation failed: {e}")
            return None
    
    def _setup_terminal_events(self, terminal: Vte.Terminal, 
                              identifier: Union[str, SessionItem], 
                              terminal_id: int) -> None:
        """
        Set up event handlers for a terminal.
        
        Args:
            terminal: Terminal widget to configure
            identifier: String or SessionItem for identification
            terminal_id: Terminal registry ID
        """
        try:
            # Connect signal handlers with terminal ID
            terminal.connect("child-exited", self._on_terminal_child_exited, identifier, terminal_id)
            terminal.connect("eof", self._on_terminal_eof, identifier, terminal_id)
            
            # Set up focus handling
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_terminal_focus_in, terminal, terminal_id)
            focus_controller.connect("leave", self._on_terminal_focus_out, terminal, terminal_id)
            terminal.add_controller(focus_controller)
            
            # Set up click handling for focus
            click_controller = Gtk.GestureClick()
            click_controller.set_button(0)  # Any button
            click_controller.connect("pressed", self._on_terminal_clicked, terminal, terminal_id)
            terminal.add_controller(click_controller)
            
            # Store terminal ID as Python attribute - GTK4 compatibility
            terminal.terminal_id = terminal_id
            
            self.logger.debug(f"Terminal events configured for ID: {terminal_id}")
            
        except Exception as e:
            self.logger.error(f"Failed to setup terminal events for ID {terminal_id}: {e}")
    
    def _setup_context_menu(self, terminal: Vte.Terminal) -> None:
        """
        Set up right-click context menu for terminal.
        
        Args:
            terminal: Terminal widget to configure
        """
        try:
            right_click = Gtk.GestureClick()
            right_click.set_button(Gdk.BUTTON_SECONDARY)
            right_click.connect("released", self._on_terminal_right_click, terminal)
            terminal.add_controller(right_click)
            
            self.logger.debug("Terminal context menu configured")
            
        except Exception as e:
            self.logger.error(f"Context menu setup failed: {e}")
    
    def _on_terminal_right_click(self, gesture, n_press, x, y, terminal):
        """Handle right-click on terminal for context menu."""
        try:
            # Create and show context menu
            menu = create_terminal_menu(self.parent_window, terminal)
            setup_context_menu(terminal, menu, x, y)
            
            # Ensure terminal has focus
            terminal.grab_focus()
            
            # Notify focus change if callback is set
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, True)
                
        except Exception as e:
            self.logger.error(f"Terminal right click handling failed: {e}")
    
    def _on_terminal_clicked(self, gesture, n_press, x, y, terminal, terminal_id):
        """Handle terminal click for focus management."""
        try:
            terminal.grab_focus()
            
            # Update terminal status
            self.registry.update_terminal_status(terminal_id, 'focused')
            
            # Notify focus change if callback is set
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)
            
            return Gdk.EVENT_PROPAGATE
            
        except Exception as e:
            self.logger.error(f"Terminal click handling failed: {e}")
            return Gdk.EVENT_PROPAGATE
    
    def _on_terminal_focus_in(self, controller, terminal, terminal_id):
        """Handle terminal gaining focus."""
        try:
            self.registry.update_terminal_status(terminal_id, 'focused')
            
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)
                
        except Exception as e:
            self.logger.error(f"Terminal focus in handling failed: {e}")
    
    def _on_terminal_focus_out(self, controller, terminal, terminal_id):
        """Handle terminal losing focus."""
        try:
            self.registry.update_terminal_status(terminal_id, 'unfocused')
        except Exception as e:
            self.logger.error(f"Terminal focus out handling failed: {e}")

    def _on_terminal_child_exited(self, terminal: Vte.Terminal, child_status: int,
                                 identifier: Union[str, SessionItem], terminal_id: int) -> None:
        """
        Handle terminal child process exit.
        
        Args:
            terminal: Terminal widget
            child_status: Exit status of child process
            identifier: Terminal identifier
            terminal_id: Terminal registry ID
        """
        try:
            terminal_name = identifier if isinstance(identifier, str) else identifier.name
            self.logger.info(f"Terminal '{terminal_name}' (ID: {terminal_id}) child process exited with status: {child_status}")
            
            # Update registry
            self.registry.update_terminal_status(terminal_id, f'exited_{child_status}')
            
            # Log terminal event
            log_terminal_event("exited", terminal_name, f"status {child_status}")

            # Handle the exit based on the status code
            if child_status == 0:
                # Normal exit: schedule the tab to auto-close silently
                def close_tab_delayed():
                    try:
                        if self.on_terminal_should_close:
                            # This callback will trigger the actual tab closing in the window
                            self.on_terminal_should_close(terminal, child_status, identifier)
                    except Exception as e:
                        self.logger.error(f"Auto-close callback failed for terminal {terminal_id}: {e}")
                    return False  # Do not repeat
                
                # Use a very short delay to allow any final output to render before closing
                GLib.timeout_add(100, close_tab_delayed)
            else:
                # Abnormal exit: feed exit message to the terminal so the user can see it
                try:
                    if (terminal.get_realized() and
                        hasattr(terminal, 'feed') and
                        (not hasattr(terminal, 'is_closed') or not terminal.is_closed())):
                        
                        message = f"\r\n[Process in '{terminal_name}' terminated unexpectedly (status: {child_status})]\r\n"
                        terminal.feed(message.encode('utf-8'))
                except (GLib.Error, AttributeError) as e:
                    self.logger.debug(f"Could not feed exit message to '{terminal_name}': {e}")

            # Call external callback for any exit status (for potential external logic)
            if self.on_terminal_child_exited:
                self.on_terminal_child_exited(terminal, child_status, identifier)
                
        except Exception as e:
            self.logger.error(f"Terminal child exit handling failed for ID {terminal_id}: {e}")

    def _on_terminal_eof(self, terminal: Vte.Terminal, 
                        identifier: Union[str, SessionItem], 
                        terminal_id: int) -> None:
        """
        Handle terminal EOF signal.
        
        Args:
            terminal: Terminal widget
            identifier: Terminal identifier
            terminal_id: Terminal registry ID
        """
        try:
            terminal_name = identifier if isinstance(identifier, str) else identifier.name
            self.logger.info(f"Terminal '{terminal_name}' (ID: {terminal_id}) received EOF signal")
            
            # Update registry
            self.registry.update_terminal_status(terminal_id, 'eof')
            
            # Log terminal event
            log_terminal_event("eof", terminal_name, "EOF signal received")
            
            # Call external callback if set
            if self.on_terminal_eof:
                self.on_terminal_eof(terminal, identifier)
                
        except Exception as e:
            self.logger.error(f"Terminal EOF handling failed for ID {terminal_id}: {e}")
    
    def _on_spawn_callback(self, terminal: Vte.Terminal, pid: int,
                          error: Optional[GLib.Error], user_data: Any, 
                          terminal_id: int) -> None:
        """
        Handle spawn completion callback.
        
        Args:
            terminal: Terminal widget
            pid: Process ID
            error: Error if spawn failed
            user_data: User data from spawn call
            terminal_id: Terminal registry ID
        """
        try:
            if error:
                self.logger.error(f"Terminal spawn failed for ID {terminal_id}: {error.message}")
                self.registry.update_terminal_status(terminal_id, 'spawn_failed')
            else:
                self.logger.debug(f"Terminal spawn successful for ID {terminal_id}, PID: {pid}")
                self.registry.update_terminal_process(terminal_id, pid)
                
        except Exception as e:
            self.logger.error(f"Spawn callback handling failed for terminal ID {terminal_id}: {e}")
    
    def remove_terminal(self, terminal: Vte.Terminal) -> bool:
        """
        Remove a terminal from management and clean up.
        
        Args:
            terminal: Terminal to remove
            
        Returns:
            True if terminal was found and removed
        """
        with self._cleanup_lock:
            try:
                # Get terminal ID - GTK4 compatibility
                terminal_id = getattr(terminal, 'terminal_id', None)
                if terminal_id is None:
                    self.logger.warning("Terminal has no ID, cannot remove from registry")
                    return False
                
                # Get terminal info before removal
                terminal_info = self.registry.get_terminal_info(terminal_id)
                terminal_name = "Unknown"
                if terminal_info:
                    identifier = terminal_info.get('identifier', 'Unknown')
                    terminal_name = identifier if isinstance(identifier, str) else getattr(identifier, 'name', 'Unknown')
                
                # Close PTY if available
                pty = terminal.get_pty()
                if pty:
                    try:
                        pty.close()
                        self.logger.debug(f"PTY closed for terminal '{terminal_name}' (ID: {terminal_id})")
                    except GLib.Error as e:
                        self.logger.warning(f"Error closing PTY for terminal '{terminal_name}': {e.message}")
                
                # Remove from registry
                success = self.registry.unregister_terminal(terminal_id)
                
                if success:
                    self._stats['terminals_closed'] += 1
                    log_terminal_event("removed", terminal_name, "terminal cleanup")
                    self.logger.debug(f"Terminal removed successfully: '{terminal_name}' (ID: {terminal_id})")
                
                return success
                
            except Exception as e:
                self.logger.error(f"Terminal removal failed: {e}")
                return False
    
    def update_all_terminals(self) -> None:
        """Apply current settings to all managed terminals."""
        try:
            terminal_ids = self.registry.get_all_terminal_ids()
            updated_count = 0
            
            for terminal_id in terminal_ids:
                terminal = self.registry.get_terminal(terminal_id)
                if terminal and terminal.get_realized():
                    try:
                        self.settings_manager.apply_terminal_settings(terminal, self.parent_window)
                        updated_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to update terminal ID {terminal_id}: {e}")
            
            self.logger.info(f"Updated {updated_count} terminals with new settings")
            
        except Exception as e:
            self.logger.error(f"Failed to update terminals: {e}")
    
    def get_terminal_count(self) -> int:
        """Get number of managed terminals."""
        return self.registry.get_terminal_count()
    
    def get_terminals(self) -> List[Vte.Terminal]:
        """Get list of all managed terminals."""
        try:
            terminals = []
            terminal_ids = self.registry.get_all_terminal_ids()
            
            for terminal_id in terminal_ids:
                terminal = self.registry.get_terminal(terminal_id)
                if terminal:
                    terminals.append(terminal)
            
            return terminals
            
        except Exception as e:
            self.logger.error(f"Failed to get terminals list: {e}")
            return []
    
    def cleanup_all_terminals(self) -> None:
        """Fast cleanup without hanging."""
        try:
            self.logger.info("Fast terminal cleanup - skipping complex operations")
            
            # Just clear the registry without complex cleanup
            terminal_count = self.registry.get_terminal_count()
            
            # Force clear everything
            self.registry._terminals.clear()
            self.registry._terminal_refs.clear()
            
            self.logger.info(f"Fast cleanup completed: {terminal_count} terminals")
            
        except Exception as e:
            self.logger.error(f"Fast cleanup failed: {e}")
            # Don't let cleanup errors prevent shutdown
    
    def copy_selection(self, terminal: Vte.Terminal) -> bool:
        """
        Copy selected text from terminal to clipboard.
        
        Args:
            terminal: Terminal with selection
            
        Returns:
            True if copy was successful
        """
        try:
            if terminal.get_has_selection():
                terminal.copy_clipboard_format(Vte.Format.TEXT)
                self.logger.debug("Text copied to clipboard")
                return True
            else:
                self.logger.debug("No selection to copy")
                return False
                
        except GLib.Error as e:
            self.logger.warning(f"Copy operation failed: {e.message}")
            return False
    
    def paste_clipboard(self, terminal: Vte.Terminal) -> bool:
        """
        Paste clipboard content to terminal.
        
        Args:
            terminal: Terminal to paste to
            
        Returns:
            True if paste was initiated
        """
        try:
            terminal.paste_clipboard()
            self.logger.debug("Clipboard content pasted to terminal")
            return True
            
        except GLib.Error as e:
            self.logger.warning(f"Paste operation failed: {e.message}")
            return False
    
    def select_all(self, terminal: Vte.Terminal) -> None:
        """
        Select all text in terminal.
        
        Args:
            terminal: Terminal to select all in
        """
        try:
            terminal.select_all()
            self.logger.debug("All text selected in terminal")
            
        except GLib.Error as e:
            self.logger.warning(f"Select all operation failed: {e.message}")
            
    def zoom_in(self, terminal: Vte.Terminal, step: float = 0.1) -> bool:
        """
        Increase terminal font scale.
        
        Args:
            terminal: Terminal to zoom in
            step: Zoom step increment (default: 0.1 = 10%)
            
        Returns:
            True if zoom was successful
        """
        try:
            current_scale = terminal.get_font_scale()
            new_scale = min(current_scale + step, 3.0)  # Max 300%
            terminal.set_font_scale(new_scale)
            
            # Update settings
            self.settings_manager.set("font_scale", new_scale, save_immediately=False)
            
            self.logger.debug(f"Terminal zoomed in: {current_scale:.1f} -> {new_scale:.1f}")
            return True
            
        except Exception as e:
            self.logger.error(f"Zoom in failed: {e}")
            return False

    def zoom_out(self, terminal: Vte.Terminal, step: float = 0.1) -> bool:
        """
        Decrease terminal font scale.
        
        Args:
            terminal: Terminal to zoom out
            step: Zoom step decrement (default: 0.1 = 10%)
            
        Returns:
            True if zoom was successful
        """
        try:
            current_scale = terminal.get_font_scale()
            new_scale = max(current_scale - step, 0.3)  # Min 30%
            terminal.set_font_scale(new_scale)
            
            # Update settings
            self.settings_manager.set("font_scale", new_scale, save_immediately=False)
            
            self.logger.debug(f"Terminal zoomed out: {current_scale:.1f} -> {new_scale:.1f}")
            return True
            
        except Exception as e:
            self.logger.error(f"Zoom out failed: {e}")
            return False

    def zoom_reset(self, terminal: Vte.Terminal) -> bool:
        """
        Reset terminal font scale to 100%.
        
        Args:
            terminal: Terminal to reset zoom
            
        Returns:
            True if reset was successful
        """
        try:
            terminal.set_font_scale(1.0)
            
            # Update settings
            self.settings_manager.set("font_scale", 1.0, save_immediately=False)
            
            self.logger.debug("Terminal zoom reset to 100%")
            return True
            
        except Exception as e:
            self.logger.error(f"Zoom reset failed: {e}")
            return False

    def apply_zoom_to_all_terminals(self, scale: float) -> int:
        """
        Apply zoom scale to all managed terminals.
        
        Args:
            scale: Font scale to apply (1.0 = 100%)
            
        Returns:
            Number of terminals updated
        """
        try:
            terminal_ids = self.registry.get_all_terminal_ids()
            updated_count = 0
            
            for terminal_id in terminal_ids:
                terminal = self.registry.get_terminal(terminal_id)
                if terminal and terminal.get_realized():
                    try:
                        terminal.set_font_scale(scale)
                        updated_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to apply zoom to terminal ID {terminal_id}: {e}")
            
            # Update settings
            self.settings_manager.set("font_scale", scale, save_immediately=False)
            
            self.logger.info(f"Applied zoom {scale:.1f} to {updated_count} terminals")
            return updated_count
            
        except Exception as e:
            self.logger.error(f"Failed to apply zoom to all terminals: {e}")
            return 0
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get terminal manager statistics.
        
        Returns:
            Dictionary with statistics
        """
        try:
            stats = self._stats.copy()
            stats.update({
                'active_terminals': self.get_terminal_count(),
                'platform': self.platform_info.platform_type.value,
                'vte_available': VTE_AVAILABLE
            })
            return stats
            
        except Exception as e:
            self.logger.error(f"Failed to get statistics: {e}")
            return {'error': str(e)}
    
    def get_terminal_info(self, terminal: Vte.Terminal) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific terminal.
        
        Args:
            terminal: Terminal to get info for
            
        Returns:
            Terminal information dictionary or None
        """
        try:
            # GTK4 compatibility - use Python attributes
            terminal_id = getattr(terminal, 'terminal_id', None)
            if terminal_id is not None:
                return self.registry.get_terminal_info(terminal_id)
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to get terminal info: {e}")
            return None
        
    def has_active_ssh_sessions(self) -> bool:
        """Check if there are active SSH sessions in this terminal manager."""
        try:
            terminal_ids = self.registry.get_all_terminal_ids()
            
            for terminal_id in terminal_ids:
                terminal_info = self.registry.get_terminal_info(terminal_id)
                
                if terminal_info and terminal_info.get('type') == 'ssh':
                    # Use process_id from registry instead of calling get_child_pid
                    process_id = terminal_info.get('process_id')
                    status = terminal_info.get('status', '')
                    
                    if process_id and process_id > 0 and status not in ['exited', 'spawn_failed']:
                        return True
            
            return False
        except Exception as e:
            self.logger.error(f"Failed to check SSH sessions: {e}")
            return False

    def get_active_ssh_session_names(self) -> list:
        """Get list of active SSH session names."""
        try:
            ssh_sessions = []
            terminal_ids = self.registry.get_all_terminal_ids()
            
            for terminal_id in terminal_ids:
                terminal_info = self.registry.get_terminal_info(terminal_id)
                
                if terminal_info and terminal_info.get('type') == 'ssh':
                    process_id = terminal_info.get('process_id')
                    status = terminal_info.get('status', '')
                    
                    if process_id and process_id > 0 and status not in ['exited', 'spawn_failed']:
                        # Get session info from identifier
                        identifier = terminal_info.get('identifier')
                        if hasattr(identifier, 'name') and hasattr(identifier, 'get_connection_string'):
                            connection_string = identifier.get_connection_string()
                            ssh_sessions.append(f"{identifier.name} ({connection_string})")
                        else:
                            ssh_sessions.append(str(identifier))
            return ssh_sessions
        except Exception as e:
            self.logger.error(f"Failed to get SSH session names: {e}")
            return []

    def get_active_ssh_count(self) -> int:
        """Get count of active SSH sessions."""
        try:
            count = 0
            terminal_ids = self.registry.get_all_terminal_ids()
            
            for terminal_id in terminal_ids:
                terminal_info = self.registry.get_terminal_info(terminal_id)
                
                if terminal_info and terminal_info.get('type') == 'ssh':
                    process_id = terminal_info.get('process_id')
                    status = terminal_info.get('status', '')
                    
                    if process_id and process_id > 0 and status not in ['exited', 'spawn_failed']:
                        count += 1
            return count
        except Exception as e:
            self.logger.error(f"Failed to count SSH sessions: {e}")
            return 0