# Ashy Terminal Enhancement Implementation Prompts

## Overview
This document provides detailed implementation prompts for enhancing the Ashy Terminal application with advanced GTK4/Libadwaita features. Each enhancement focuses on improving the user experience through better responsive design, adaptive layouts, and advanced terminal control.

## Enhancement 1: Adw.Breakpoint for Sophisticated Responsive Design

### Current State Analysis
The application currently uses basic responsive design with flap-based sidebar management. While functional, it lacks sophisticated breakpoint-based adaptive behavior that could provide better user experience across different screen sizes.

### Implementation Requirements

**Objective:** Implement Adw.Breakpoint system to create sophisticated responsive design that adapts the UI based on window size and device characteristics.

**Key Components to Modify:**
- `window.py`: Main window class
- `app.py`: Application-level breakpoint management
- `settings/manager.py`: Settings integration for breakpoint preferences

**Detailed Implementation Steps:**

1. **Add Breakpoint Management to Window Class**
   ```python
   # In window.py, add breakpoint management
   def _setup_breakpoints(self):
       """Set up adaptive breakpoints for responsive design."""
       # Create breakpoints for different screen sizes
       self._compact_breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600px"))
       self._medium_breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900px"))
       self._large_breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1200px"))

       # Add breakpoint handlers
       self._compact_breakpoint.connect("apply", self._on_compact_breakpoint_apply)
       self._compact_breakpoint.connect("unapply", self._on_compact_breakpoint_unapply)
       self._medium_breakpoint.connect("apply", self._on_medium_breakpoint_apply)
       self._medium_breakpoint.connect("unapply", self._on_medium_breakpoint_unapply)

       # Add breakpoints to window
       self.add_breakpoint(self._compact_breakpoint)
       self.add_breakpoint(self._medium_breakpoint)
       self.add_breakpoint(self._large_breakpoint)
   ```

2. **Implement Breakpoint Handlers**
   ```python
   def _on_compact_breakpoint_apply(self, breakpoint):
       """Handle compact breakpoint activation."""
       # Auto-hide sidebar in compact mode
       self.settings_manager.set("auto_hide_sidebar", True)
       self.flap.set_reveal_flap(False)
       self.toggle_sidebar_button.set_active(False)

       # Adjust tab bar for compact screens
       self.scrolled_tab_bar.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
       self._update_tab_layout()

       # Compact toolbar buttons
       self._set_compact_toolbar_mode(True)

   def _on_compact_breakpoint_unapply(self, breakpoint):
       """Handle compact breakpoint deactivation."""
       # Restore normal sidebar behavior
       auto_hide = self.settings_manager.get("auto_hide_sidebar", False)
       if not auto_hide:
           self.flap.set_reveal_flap(True)
           self.toggle_sidebar_button.set_active(True)

       # Restore tab bar
       self._update_tab_layout()

       # Restore toolbar buttons
       self._set_compact_toolbar_mode(False)
   ```

3. **Add Settings Integration**
   ```python
   # In settings manager, add breakpoint preferences
   BREAKPOINT_SETTINGS = {
       "responsive_design_enabled": True,
       "compact_mode_behavior": "auto_hide",  # auto_hide, overlay, minimize
       "medium_mode_behavior": "normal",     # normal, compact
       "large_mode_behavior": "expanded"     # expanded, normal
   }
   ```

4. **Update CSS for Breakpoint-Specific Styling**
   ```css
   /* Compact mode styles */
   .compact-mode .sidebar-toolbar-view {
       min-width: 250px;
   }

   .compact-mode .custom-tab-button {
       padding-left: 8px;
       padding-right: 8px;
   }

   /* Medium mode styles */
   .medium-mode .sidebar-session-tree row {
       padding: 6px 8px;
   }

   /* Large mode styles */
   .large-mode .sidebar-toolbar-view {
       min-width: 400px;
   }
   ```

**Expected Benefits:**
- Better user experience on different screen sizes
- Automatic adaptation to device capabilities
- Improved touch interface on tablets
- Better space utilization

**Testing Requirements:**
- Test on various screen sizes (mobile, tablet, desktop)
- Verify breakpoint transitions work smoothly
- Test with different DPI settings
- Validate accessibility compliance

## Enhancement 2: Adw.NavigationSplitView for Improved Adaptive Layouts

### Current State Analysis
The application uses Adw.Flap for sidebar management, which works but lacks the sophisticated adaptive behavior provided by Adw.NavigationSplitView.

### Implementation Requirements

**Objective:** Replace or enhance the current flap-based sidebar with Adw.NavigationSplitView for better adaptive layouts and improved navigation patterns.

**Key Components to Modify:**
- `window.py`: Replace flap with NavigationSplitView
- `ui/actions.py`: Update action handlers
- `settings/manager.py`: Add navigation preferences

**Detailed Implementation Steps:**

1. **Replace Flap with NavigationSplitView**
   ```python
   # In window.py _setup_ui method
   def _setup_navigation_split_view(self):
       """Set up NavigationSplitView for adaptive layouts."""
       # Create navigation split view
       self.nav_split_view = Adw.NavigationSplitView()
       self.nav_split_view.set_sidebar_width_fraction(0.25)
       self.nav_split_view.set_min_sidebar_width(300)
       self.nav_split_view.set_max_sidebar_width(500)

       # Create sidebar widget
       self.sidebar_widget = self._create_navigation_sidebar()

       # Create content widget
       self.content_widget = self._create_navigation_content()

       # Set up the split view
       self.nav_split_view.set_sidebar(self.sidebar_widget)
       self.nav_split_view.set_content(self.content_widget)

       # Add to main layout
       main_box.append(self.nav_split_view)
   ```

2. **Create Navigation-Aware Sidebar**
   ```python
   def _create_navigation_sidebar(self):
       """Create sidebar optimized for NavigationSplitView."""
       # Create navigation page for sidebar
       sidebar_page = Adw.NavigationPage.new(self.sidebar_box, _("Sessions"))

       # Add navigation header
       header = Adw.HeaderBar()
       header.set_title_widget(Adw.WindowTitle.new(_("Sessions"), None))

       # Add back button for collapsed state
       back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
       back_button.connect("clicked", self._on_sidebar_back_clicked)
       header.pack_start(back_button)

       sidebar_page.set_header(header)

       return sidebar_page
   ```

3. **Implement Navigation State Management**
   ```python
   def _on_navigation_split_view_notify_collapsed(self, split_view, pspec):
       """Handle navigation split view collapse state changes."""
       is_collapsed = split_view.get_collapsed()

       if is_collapsed:
           # Show back button in sidebar header
           self.sidebar_back_button.set_visible(True)
           # Update content area for collapsed state
           self._update_content_for_collapsed_state()
       else:
           # Hide back button
           self.sidebar_back_button.set_visible(False)
           # Restore full content layout
           self._update_content_for_expanded_state()

   def _on_sidebar_back_clicked(self, button):
       """Handle back button click in collapsed sidebar."""
       # Collapse the sidebar
       self.nav_split_view.set_show_sidebar(False)
   ```

4. **Add Navigation Preferences**
   ```python
   # In settings manager
   NAVIGATION_SETTINGS = {
       "sidebar_width_fraction": 0.25,
       "min_sidebar_width": 300,
       "max_sidebar_width": 500,
       "show_sidebar_on_start": True,
       "remember_sidebar_state": True
   }
   ```

**Expected Benefits:**
- Better adaptive behavior on different screen sizes
- Improved navigation patterns
- Better integration with GNOME design patterns
- Enhanced accessibility

**Testing Requirements:**
- Test sidebar collapse/expand behavior
- Verify navigation on different screen sizes
- Test keyboard navigation
- Validate with screen readers

## Enhancement 3: VtePty Explicit Management for Advanced Terminal Control

### Current State Analysis
The application uses VTE's spawn_async method but doesn't explicitly manage VtePty objects, missing opportunities for advanced terminal control features.

### Implementation Requirements

**Objective:** Implement explicit VtePty management to enable advanced terminal control features like PTY resizing, signal handling, and better process management.

**Key Components to Modify:**
- `terminal/spawner.py`: Add VtePty management
- `terminal/manager.py`: Integrate PTY control
- `terminal/tabs.py`: Add PTY-aware features

**Detailed Implementation Steps:**

1. **Create PTY Manager Class**
   ```python
   class PtyManager:
       """Manages VtePty objects for advanced terminal control."""

       def __init__(self):
           self.logger = get_logger("ashyterm.pty.manager")
           self._ptys = {}  # terminal_id -> VtePty mapping
           self._pty_watchers = {}  # pty -> watcher_id mapping

       def create_pty_for_terminal(self, terminal: Vte.Terminal, terminal_id: str):
           """Create and configure a PTY for the given terminal."""
           try:
               # Create new PTY
               pty = Vte.Pty.new_sync(Vte.PtyFlags.DEFAULT, None)

               # Configure PTY
               self._configure_pty(pty, terminal)

               # Set PTY on terminal
               terminal.set_pty(pty)

               # Store PTY reference
               self._ptys[terminal_id] = pty

               # Set up PTY monitoring
               self._setup_pty_monitoring(pty, terminal_id)

               self.logger.info(f"Created PTY for terminal {terminal_id}")
               return pty

           except Exception as e:
               self.logger.error(f"Failed to create PTY for terminal {terminal_id}: {e}")
               raise

       def _configure_pty(self, pty: Vte.Pty, terminal: Vte.Terminal):
           """Configure PTY with optimal settings."""
           # Set UTF-8 encoding
           pty.set_utf8(True)

           # Configure terminal size based on current allocation
           allocation = terminal.get_allocation()
           rows = terminal.get_row_count()
           cols = terminal.get_column_count()

           if rows > 0 and cols > 0:
               pty.set_size(rows, cols)

           # Set up PTY flags for optimal performance
           pty.set_flags(Vte.PtyFlags.DEFAULT)

       def _setup_pty_monitoring(self, pty: Vte.Pty, terminal_id: str):
           """Set up monitoring for PTY events."""
           # Monitor PTY for child process changes
           watcher_id = pty.connect("child-exited", self._on_pty_child_exited, terminal_id)
           self._pty_watchers[pty] = watcher_id

       def _on_pty_child_exited(self, pty, status, terminal_id):
           """Handle PTY child process exit."""
           self.logger.info(f"PTY child exited for terminal {terminal_id}, status: {status}")

           # Clean up PTY resources
           self.cleanup_pty(terminal_id)

       def resize_pty(self, terminal_id: str, rows: int, cols: int):
           """Resize the PTY for the given terminal."""
           if terminal_id in self._ptys:
               pty = self._ptys[terminal_id]
               try:
                   pty.set_size(rows, cols)
                   self.logger.debug(f"Resized PTY for terminal {terminal_id} to {rows}x{cols}")
               except Exception as e:
                   self.logger.error(f"Failed to resize PTY for terminal {terminal_id}: {e}")

       def cleanup_pty(self, terminal_id: str):
           """Clean up PTY resources for the given terminal."""
           if terminal_id in self._ptys:
               pty = self._ptys[terminal_id]

               # Disconnect watchers
               if pty in self._pty_watchers:
                   watcher_id = self._pty_watchers[pty]
                   pty.disconnect(watcher_id)
                   del self._pty_watchers[pty]

               # Close PTY
               try:
                   pty.close()
               except Exception as e:
                   self.logger.warning(f"Error closing PTY for terminal {terminal_id}: {e}")

               del self._ptys[terminal_id]
               self.logger.info(f"Cleaned up PTY for terminal {terminal_id}")
   ```

2. **Integrate PTY Manager into Terminal Spawner**
   ```python
   # In ProcessSpawner class
   def __init__(self):
       # ... existing initialization ...
       self.pty_manager = PtyManager()

   def spawn_local_terminal(self, terminal: Vte.Terminal, **kwargs):
       """Enhanced spawn with explicit PTY management."""
       terminal_id = kwargs.get('terminal_id', str(id(terminal)))

       # Create and configure PTY
       pty = self.pty_manager.create_pty_for_terminal(terminal, terminal_id)

       # Use PTY in spawn operation
       env = self.environment_manager.get_terminal_environment()
       env_list = [f"{k}={v}" for k, v in env.items()]

       terminal.spawn_async(
           Vte.PtyFlags.DEFAULT,
           working_dir,
           cmd,
           env_list,
           GLib.SpawnFlags.DEFAULT,
           None,
           None,
           -1,
           pty,  # Use our managed PTY
           callback,
           user_data
       )
   ```

3. **Add PTY-Aware Terminal Management**
   ```python
   # In TerminalManager class
   def create_terminal(self, **kwargs):
       """Create terminal with PTY management."""
       terminal = Vte.Terminal()

       # Register with PTY manager
       terminal_id = str(id(terminal))
       self.spawner.pty_manager.create_pty_for_terminal(terminal, terminal_id)

       # Connect to terminal size changes
       terminal.connect("notify::column-count", self._on_terminal_size_changed, terminal_id)
       terminal.connect("notify::row-count", self._on_terminal_size_changed, terminal_id)

       return terminal

   def _on_terminal_size_changed(self, terminal, pspec, terminal_id):
       """Handle terminal size changes to update PTY."""
       rows = terminal.get_row_count()
       cols = terminal.get_column_count()

       self.spawner.pty_manager.resize_pty(terminal_id, rows, cols)
   ```

4. **Add Advanced PTY Features**
   ```python
   def send_signal_to_pty(self, terminal_id: str, signal: int):
       """Send signal to PTY child process."""
       if terminal_id in self._ptys:
           pty = self._ptys[terminal_id]
           try:
               # Get child PID from PTY
               child_pid = pty.get_child_pid()
               if child_pid > 0:
                   os.kill(child_pid, signal)
                   self.logger.info(f"Sent signal {signal} to PTY child {child_pid}")
           except Exception as e:
               self.logger.error(f"Failed to send signal to PTY: {e}")

   def get_pty_info(self, terminal_id: str) -> dict:
       """Get detailed PTY information."""
       if terminal_id not in self._ptys:
           return {}

       pty = self._ptys[terminal_id]
       return {
           "fd": pty.get_fd(),
           "child_pid": pty.get_child_pid(),
           "size": pty.get_size(),
           "utf8": pty.get_utf8()
       }
   ```

**Expected Benefits:**
- Better terminal resizing behavior
- Advanced process signal handling
- Improved PTY resource management
- Enhanced terminal control capabilities

**Testing Requirements:**
- Test terminal resizing with different applications
- Verify signal handling (Ctrl+C, Ctrl+Z, etc.)
- Test with various terminal applications
- Validate PTY cleanup on terminal close

## Implementation Priority and Dependencies

### Phase 1: Foundation (Adw.Breakpoint)
- Lowest risk, highest immediate benefit
- Can be implemented incrementally
- Dependencies: GTK4, Libadwaita

### Phase 2: Navigation Enhancement (Adw.NavigationSplitView)
- Medium risk, good user experience improvement
- Requires UI restructuring
- Dependencies: Phase 1 completion

### Phase 3: Terminal Control (VtePty Management)
- Higher complexity, advanced features
- Requires deep understanding of PTY/terminal internals
- Dependencies: Stable terminal spawning system

## Testing and Validation Strategy

### Unit Testing
- Test breakpoint conditions and handlers
- Test PTY creation and management
- Test navigation state transitions

### Integration Testing
- Test responsive design across screen sizes
- Test terminal resizing and PTY management
- Test navigation patterns

### User Acceptance Testing
- Gather feedback on responsive behavior
- Test with various terminal applications
- Validate accessibility compliance

## Rollback Strategy

### Breakpoint Enhancement
- Can be disabled via settings
- Easy to remove breakpoint handlers
- Minimal impact on existing functionality

### Navigation Enhancement
- Can fallback to flap-based sidebar
- Settings to enable/disable NavigationSplitView
- Backward compatibility maintained

### PTY Enhancement
- Graceful degradation to current spawn method
- Feature flags to enable/disable PTY management
- Comprehensive error handling

## Success Metrics

### Performance Metrics
- UI responsiveness during breakpoint transitions
- Terminal spawn time with PTY management
- Memory usage with enhanced layouts

### User Experience Metrics
- User preference for responsive design
- Terminal control feature usage
- Navigation pattern effectiveness

### Code Quality Metrics
- Test coverage for new features
- Code maintainability scores
- Documentation completeness</content>
<parameter name="filePath">/home/bruno/codigo-pacotes/comm-ashyterm/PROMPT.md