# AI Coding Assistant Instructions for Ashy Terminal

## Project Overview
Ashy Terminal is a modern GTK4/Adwaita-based terminal emulator written in Python, designed for developers and power users on Manjaro/BigLinux. It features advanced session management, SSH integration, SFTP file transfers, and robust security features.

## Architecture Overview

### Core Components
- **`app.py`**: Main Adw.Application class handling startup, settings, and global actions
- **`window.py`**: Main Adw.ApplicationWindow with tab management and UI layout
- **`terminal/manager.py`**: Central terminal lifecycle management using registry pattern
- **`terminal/tabs.py`**: Custom tab bar implementation with split-pane support
- **`sessions/models.py`**: GObject-based data models for sessions and folders
- **`settings/config.py`**: Configuration management with platform-aware paths
- **`filemanager/`**: SFTP integration and file transfer operations
- **`utils/`**: Security, logging, backup, and platform-specific utilities

### Key Design Patterns

#### 1. Registry Pattern for Terminal Management
```python
# Terminal registry tracks all active terminals with metadata
terminal_id = self.registry.register_terminal(terminal, "ssh", session)
info = self.registry.get_terminal_info(terminal_id)
```

#### 2. Event-Driven Architecture
```python
# GTK signal connections for terminal events
terminal.connect("child-exited", self._on_child_exited, identifier, terminal_id)
terminal.connect("notify::current-directory-uri", self._on_directory_uri_changed)
```

#### 3. OSC7 Directory Tracking
```python
# Automatic directory tracking via OSC7 escape sequences
osc7_info = OSC7Info(hostname=hostname, path=path, display_path=display_path)
```

#### 4. GObject Property System
```python
# Session models use GObject properties for data binding
@property
def name(self) -> str:
    return self._name

@name.setter
def name(self, value: str):
    self._name = sanitize_session_name(value)
    self._mark_modified()
```

## Critical Developer Workflows

### Local Development Setup
```bash
# Install dependencies
sudo pacman -S python python-gobject vte4 python-cryptography python-psutil

# Run from source
cd /path/to/comm-ashyterm/usr/share/ashyterm
python -m ashyterm.main
```

### Build and Package
```bash
# Build package (from pkgbuild/ directory)
makepkg -si

# Install built package
sudo pacman -U comm-ashyterm-*-x86_64.pkg.tar.zst
```

### Testing Commands
```bash
# Run with debug logging
python -m ashyterm.main --debug --log-level DEBUG

# Test SSH connections
python -m ashyterm.main --ssh user@hostname

# Execute command and exit
python -m ashyterm.main -e "ls -la" --close-after-execute
```

## Project-Specific Conventions

### 1. Security-First Approach
- **Input Sanitization**: All user inputs validated and sanitized
- **Encrypted Storage**: Passwords stored in system keyring via Secret Service API
- **Secure Permissions**: Config files created with 0o600 permissions
- **Path Validation**: All file paths normalized and validated against injection

### 2. Error Handling Pattern
```python
try:
    # Operation that might fail
    result = risky_operation()
except SpecificException as e:
    self.logger.error(f"Operation failed: {e}")
    handle_exception(e, "operation context", "module.name", reraise=True)
```

### 3. Logging Standards
```python
# Structured logging with context
logger = get_logger("ashyterm.module.submodule")
logger.info(f"Terminal created: {terminal_name} (ID: {terminal_id})")
log_terminal_event("created", terminal_name, "local terminal")
```

### 4. Translation Support
```python
# All user-facing strings wrapped for i18n
_("Save Session")
_("Connection failed: {}").format(error_message)
```

### 5. Configuration Path Management
```python
# Platform-aware config directories
CONFIG_DIR = "~/.config/ashyterm"
SESSIONS_FILE = f"{CONFIG_DIR}/sessions.json"
SETTINGS_FILE = f"{CONFIG_DIR}/settings.json"
BACKUP_DIR = f"{CONFIG_DIR}/backups"
```

## Integration Points & Dependencies

### GTK4/VTE Integration
```python
# Terminal creation with GTK4 widgets
terminal = Vte.Terminal()
terminal.set_vexpand(True)
terminal.set_hexpand(True)
terminal.set_mouse_autohide(True)
```

### SSH Session Management
```python
# SSH terminal creation with session validation
session_data = session.to_dict()
is_valid, errors = validate_session_data(session_data)
terminal = self.spawner.spawn_ssh_session(terminal, session, ...)
```

### SFTP File Operations
```python
# File manager integration
file_manager = FileManager(parent_window, terminal_manager, terminal)
transfer_manager = TransferManager(config_dir, operations)
```

## Data Flow & State Management

### Session Storage
- **Format**: JSON with metadata (created_at, modified_at, use_count)
- **Location**: `~/.config/ashyterm/sessions.json`
- **Encryption**: Passwords stored separately in system keyring

### Settings Management
- **Format**: JSON with defaults and user overrides
- **Location**: `~/.config/ashyterm/settings.json`
- **Real-time**: Settings applied immediately to active terminals

### Backup System
- **Automatic**: Configurable interval backups
- **Manual**: User-triggered backups
- **Format**: Compressed archives with metadata
- **Retention**: Configurable cleanup policy

## Common Development Tasks

### Adding New Terminal Features
1. Extend `TerminalManager` for new terminal types
2. Add settings in `DefaultSettings.get_defaults()`
3. Update UI in `window.py` and relevant dialogs
4. Add keyboard shortcuts in settings configuration

### Implementing Security Features
1. Use `InputSanitizer` for all user inputs
2. Store sensitive data via `crypto.py` utilities
3. Validate paths with `PathValidationError`
4. Log security events with appropriate severity

### Adding UI Components
1. Use Adwaita widgets (Adw.*) for consistency
2. Follow GTK4 patterns with template strings
3. Connect signals properly with error handling
4. Add CSS classes for custom styling

## Debugging & Troubleshooting

### Common Issues
- **VTE Import Errors**: Ensure `vte4` package is installed
- **Permission Errors**: Check `~/.config/ashyterm` ownership
- **SSH Connection Failures**: Use "Test Connection" in session editor
- **Display Issues**: Verify GTK4 and Adwaita versions

### Debug Commands
```bash
# Enable debug logging
python -m ashyterm.main --debug

# Check configuration
ls -la ~/.config/ashyterm/

# View logs
cat ~/.config/ashyterm/logs/ashyterm.log
```

## Code Quality Standards

### Testing
- Unit tests in `tests/` directory
- Integration tests for terminal operations
- Mock GTK components for UI testing

### Documentation
- Docstrings for all public methods
- README with setup and usage instructions
- Inline comments for complex logic

### Performance
- Lazy loading of heavy components
- Efficient terminal registry operations
- Background processing for file operations

Remember: This codebase prioritizes security, user experience, and maintainability. Always validate inputs, handle errors gracefully, and maintain the modular architecture when making changes.