"""
Ashy Terminal - A modern terminal emulator with session management.

This package provides a comprehensive GTK4/Adwaita-based terminal emulator with advanced features:

Core Features:
- Session management for SSH and local terminals
- Hierarchical folder organization for sessions
- Modern tabbed interface with drag-and-drop
- Customizable appearance and keyboard shortcuts
- Copy/paste functionality between sessions and folders
- Context-aware menus and actions

Enhanced Features:
- Comprehensive security validation and auditing
- Encrypted password storage with industry-standard cryptography
- Automatic backup and recovery system
- Platform-aware configuration and compatibility
- Structured logging with multiple levels and rotation
- Real-time input validation and sanitization
- Thread-safe operations throughout
- Custom exception hierarchy with detailed error information

Security Features:
- SSH key validation and secure storage
- Hostname and input sanitization
- Security auditing for SSH configurations
- Encrypted storage for sensitive data
- Secure file permissions and access control

Backup & Recovery:
- Automatic backup scheduling
- Version control for configurations
- Recovery from corrupted data
- Export/import functionality
- Backup integrity verification

Platform Support:
- Linux (primary)
- macOS (supported)
- Windows (limited support)
- BSD variants (basic support)
"""

import sys
import os
import warnings
from pathlib import Path
from typing import Optional, Dict, Any, List

# Package metadata
__version__ = "1.0.1"
__version_info__ = (1, 0, 1)
__author__ = "BigCommunity Team"
__email__ = "contact@communitybig.org"
__license__ = "MIT"
__copyright__ = "© 2024 BigCommunity"
__url__ = "https://communitybig.org/"
__description__ = "A modern terminal emulator with session management"

# API exports
__all__ = [
    # Metadata
    "__version__",
    "__version_info__",
    "__author__", 
    "__email__",
    "__license__",
    "__copyright__",
    "__url__",
    "__description__",
    
    # Core availability flags
    "VTE_AVAILABLE",
    "UTILS_AVAILABLE", 
    "CRYPTO_AVAILABLE",
    "PLATFORM_INFO",
    
    # System information
    "get_system_info",
    "check_dependencies",
    "get_requirements_status",
    
    # Initialization functions
    "initialize_application",
    "cleanup_application",
    "is_initialized",
    
    # Error classes
    "AshyTerminalError",
    "InitializationError",
]

# Compatibility and feature flags
VTE_AVAILABLE = False
UTILS_AVAILABLE = False
CRYPTO_AVAILABLE = False
PLATFORM_INFO = None

# Initialization state
_initialized = False
_initialization_errors = []
_logger = None


def _setup_basic_logging():
    """Set up basic logging before full system is available."""
    try:
        import logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S'
        )
        return logging.getLogger('ashyterm.init')
    except Exception:
        return None


def _check_python_version():
    """Check Python version compatibility."""
    min_version = (3, 8)
    current_version = sys.version_info[:2]
    
    if current_version < min_version:
        error_msg = (
            f"Python {min_version[0]}.{min_version[1]}+ required, "
            f"got {current_version[0]}.{current_version[1]}"
        )
        print(f"ERROR: {error_msg}")
        return False, error_msg
    
    return True, None


def _check_gtk_requirements():
    """Check and initialize GTK requirements."""
    global VTE_AVAILABLE
    
    try:
        # Check GTK4/Adwaita availability
        import gi
        
        try:
            gi.require_version("Gtk", "4.0")
            gi.require_version("Adw", "1")
            from gi.repository import Gtk, Adw
        except (ImportError, ValueError) as e:
            error_msg = f"Required GTK4/Adwaita libraries not found: {e}"
            return False, error_msg
        
        # Check VTE availability (optional but important)
        try:
            gi.require_version("Vte", "3.91")
            from gi.repository import Vte
            VTE_AVAILABLE = True
        except (ImportError, ValueError) as e:
            VTE_AVAILABLE = False
            warnings.warn(
                f"VTE 3.91 not found: {e}. Terminal functionality will be limited. "
                "Install gir1.2-vte-2.91 for full functionality.",
                ImportWarning,
                stacklevel=2
            )
        
        return True, None
        
    except ImportError as e:
        error_msg = f"GTK/GI not available: {e}"
        return False, error_msg


def _check_utility_systems():
    """Check availability of utility systems."""
    global UTILS_AVAILABLE, CRYPTO_AVAILABLE, PLATFORM_INFO
    
    try:
        # Try to import utility modules
        from .utils.logger import get_logger
        from .utils.platform import get_platform_info
        from .utils.exceptions import AshyTerminalError
        from .utils.crypto import is_encryption_available
        
        UTILS_AVAILABLE = True
        CRYPTO_AVAILABLE = is_encryption_available()
        PLATFORM_INFO = get_platform_info()
        
        return True, None
        
    except ImportError as e:
        UTILS_AVAILABLE = False
        error_msg = f"Utility systems not available: {e}"
        return False, error_msg


def _initialize_error_handling():
    """Initialize enhanced error handling if available."""
    try:
        if UTILS_AVAILABLE:
            from .utils.exceptions import AshyTerminalError, handle_exception
            from .utils.logger import get_logger
            
            # Set up global exception handling
            def enhanced_excepthook(exc_type, exc_value, exc_traceback):
                """Enhanced exception handler."""
                if issubclass(exc_type, KeyboardInterrupt):
                    sys.__excepthook__(exc_type, exc_value, exc_traceback)
                    return
                
                logger = get_logger('ashyterm.global')
                logger.critical(
                    f"Uncaught exception: {exc_type.__name__}: {exc_value}",
                    exc_info=(exc_type, exc_value, exc_traceback)
                )
            
            sys.excepthook = enhanced_excepthook
            return True
        
        return False
        
    except Exception as e:
        warnings.warn(f"Failed to initialize error handling: {e}", RuntimeWarning)
        return False


def get_system_info() -> Dict[str, Any]:
    """
    Get comprehensive system information.
    
    Returns:
        Dictionary with system information
    """
    info = {
        'package': {
            'name': 'ashyterm',
            'version': __version__,
            'version_info': __version_info__,
            'author': __author__,
            'license': __license__
        },
        'python': {
            'version': sys.version,
            'version_info': sys.version_info,
            'executable': sys.executable,
            'platform': sys.platform
        },
        'features': {
            'vte_available': VTE_AVAILABLE,
            'utils_available': UTILS_AVAILABLE,
            'crypto_available': CRYPTO_AVAILABLE,
            'initialized': _initialized
        },
        'errors': _initialization_errors.copy()
    }
    
    # Add platform information if available
    if PLATFORM_INFO:
        try:
            info['platform'] = {
                'type': PLATFORM_INFO.platform_type.value,
                'system': PLATFORM_INFO.system_name,
                'release': PLATFORM_INFO.platform_release,
                'architecture': PLATFORM_INFO.architecture,
                'is_64bit': PLATFORM_INFO.is_64bit,
                'default_shell': PLATFORM_INFO.default_shell,
                'config_dir': str(PLATFORM_INFO.config_dir),
                'ssh_dir': str(PLATFORM_INFO.ssh_dir)
            }
        except Exception as e:
            info['platform'] = {'error': str(e)}
    
    # Add GTK information
    try:
        import gi
        info['gtk'] = {
            'gi_version': gi.version_info,
            'gtk_available': True
        }
        
        if VTE_AVAILABLE:
            from gi.repository import Vte
            info['vte'] = {
                'available': True,
                'version': getattr(Vte, '_version', 'unknown')
            }
    except Exception as e:
        info['gtk'] = {'error': str(e)}
    
    return info


def check_dependencies() -> Dict[str, Any]:
    """
    Check all dependencies and return detailed status.
    
    Returns:
        Dictionary with dependency check results
    """
    results = {
        'all_satisfied': True,
        'critical_missing': [],
        'optional_missing': [],
        'details': {}
    }
    
    # Check Python version
    python_ok, python_error = _check_python_version()
    results['details']['python'] = {
        'satisfied': python_ok,
        'error': python_error,
        'current_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        'required_version': "3.8+"
    }
    
    if not python_ok:
        results['all_satisfied'] = False
        results['critical_missing'].append('python')
    
    # Check GTK requirements
    gtk_ok, gtk_error = _check_gtk_requirements()
    results['details']['gtk'] = {
        'satisfied': gtk_ok,
        'error': gtk_error,
        'vte_available': VTE_AVAILABLE
    }
    
    if not gtk_ok:
        results['all_satisfied'] = False
        results['critical_missing'].append('gtk')
    elif not VTE_AVAILABLE:
        results['optional_missing'].append('vte')
    
    # Check utility systems
    utils_ok, utils_error = _check_utility_systems()
    results['details']['utils'] = {
        'satisfied': utils_ok,
        'error': utils_error,
        'crypto_available': CRYPTO_AVAILABLE
    }
    
    if not utils_ok:
        results['optional_missing'].append('utils')
    elif not CRYPTO_AVAILABLE:
        results['optional_missing'].append('cryptography')
    
    # Check optional dependencies
    optional_deps = {
        'cryptography': 'Password encryption',
        'sshpass': 'Password-based SSH authentication'
    }
    
    for dep, description in optional_deps.items():
        try:
            if dep == 'cryptography':
                import cryptography
                available = True
            elif dep == 'sshpass':
                import shutil
                available = shutil.which('sshpass') is not None
            else:
                __import__(dep)
                available = True
        except ImportError:
            available = False
        
        results['details'][dep] = {
            'satisfied': available,
            'description': description,
            'required': False
        }
        
        if not available and dep not in results['optional_missing']:
            results['optional_missing'].append(dep)
    
    return results


def get_requirements_status() -> str:
    """
    Get human-readable requirements status.
    
    Returns:
        Status string
    """
    deps = check_dependencies()
    
    if deps['all_satisfied'] and not deps['optional_missing']:
        return "✓ All dependencies satisfied"
    elif deps['all_satisfied']:
        optional_count = len(deps['optional_missing'])
        return f"✓ Core dependencies satisfied ({optional_count} optional features unavailable)"
    else:
        critical_count = len(deps['critical_missing'])
        return f"✗ {critical_count} critical dependencies missing"


class InitializationError(Exception):
    """Raised when package initialization fails."""
    
    def __init__(self, message: str, errors: List[str] = None):
        super().__init__(message)
        self.errors = errors or []


def initialize_application(force: bool = False) -> bool:
    """
    Initialize the application with all systems.
    
    Args:
        force: Force re-initialization even if already initialized
        
    Returns:
        True if initialization successful
        
    Raises:
        InitializationError: If critical initialization fails
    """
    global _initialized, _initialization_errors, _logger
    
    if _initialized and not force:
        return True
    
    _initialization_errors.clear()
    _logger = _setup_basic_logging()
    
    if _logger:
        _logger.info(f"Initializing Ashy Terminal v{__version__}")
    
    try:
        # Check critical dependencies
        deps = check_dependencies()
        
        if not deps['all_satisfied']:
            critical_errors = []
            for dep in deps['critical_missing']:
                error = deps['details'][dep].get('error', f'{dep} not available')
                critical_errors.append(error)
                _initialization_errors.append(error)
            
            raise InitializationError(
                f"Critical dependencies missing: {', '.join(deps['critical_missing'])}",
                critical_errors
            )
        
        # Initialize enhanced systems if available
        if UTILS_AVAILABLE:
            try:
                # Initialize configuration system
                from .settings.config import initialize_configuration
                initialize_configuration()
                
                # Set up enhanced logging
                from .utils.logger import get_logger
                _logger = get_logger('ashyterm.init')
                _logger.info("Enhanced logging system initialized")
                
                # Initialize error handling
                if _initialize_error_handling():
                    _logger.debug("Enhanced error handling initialized")
                
                # Initialize platform detection
                if PLATFORM_INFO:
                    _logger.info(f"Platform detected: {PLATFORM_INFO.platform_type.value}")
                
                # Initialize backup system
                try:
                    from .utils.backup import get_backup_manager
                    backup_manager = get_backup_manager()
                    _logger.debug("Backup system initialized")
                except Exception as e:
                    _logger.warning(f"Backup system initialization failed: {e}")
                    _initialization_errors.append(f"Backup system: {e}")
                
                # Initialize security system
                try:
                    from .utils.security import create_security_auditor
                    security_auditor = create_security_auditor()
                    _logger.debug("Security system initialized")
                except Exception as e:
                    _logger.warning(f"Security system initialization failed: {e}")
                    _initialization_errors.append(f"Security system: {e}")
                
                # Initialize encryption if available
                if CRYPTO_AVAILABLE:
                    try:
                        from .utils.crypto import get_secure_storage
                        secure_storage = get_secure_storage()
                        _logger.info("Encryption system available")
                    except Exception as e:
                        _logger.warning(f"Encryption system initialization failed: {e}")
                        _initialization_errors.append(f"Encryption: {e}")
                
            except Exception as e:
                error_msg = f"Enhanced systems initialization failed: {e}"
                _initialization_errors.append(error_msg)
                if _logger:
                    _logger.error(error_msg)
                # Don't raise error - basic functionality should still work
        
        # Log optional features status
        if _logger:
            _logger.info(f"VTE available: {VTE_AVAILABLE}")
            _logger.info(f"Utilities available: {UTILS_AVAILABLE}")
            _logger.info(f"Cryptography available: {CRYPTO_AVAILABLE}")
            
            if deps['optional_missing']:
                _logger.info(f"Optional features unavailable: {', '.join(deps['optional_missing'])}")
        
        _initialized = True
        
        if _logger:
            _logger.info("Ashy Terminal initialization completed successfully")
        
        return True
        
    except InitializationError:
        raise  # Re-raise initialization errors
    except Exception as e:
        error_msg = f"Unexpected initialization error: {e}"
        _initialization_errors.append(error_msg)
        
        if _logger:
            _logger.critical(error_msg, exc_info=True)
        
        raise InitializationError(error_msg, _initialization_errors)


def cleanup_application() -> None:
    """Clean up application resources."""
    global _initialized
    
    try:
        if UTILS_AVAILABLE and _initialized:
            # Use enhanced logging if available
            from .utils.logger import get_logger, log_app_shutdown
            logger = get_logger('ashyterm.cleanup')
            logger.info("Starting application cleanup")
            
            try:
                # Clean up backup system
                from .utils.backup import get_backup_manager
                backup_manager = get_backup_manager()
                # Backup manager cleanup is automatic
                logger.debug("Backup system cleaned up")
            except Exception as e:
                logger.warning(f"Backup cleanup failed: {e}")
            
            log_app_shutdown()
        elif _logger:
            _logger.info("Application cleanup completed")
        
        _initialized = False
        
    except Exception as e:
        if _logger:
            _logger.error(f"Cleanup failed: {e}")
        else:
            print(f"Cleanup failed: {e}")


def is_initialized() -> bool:
    """Check if application is initialized."""
    return _initialized


# Export error classes
try:
    if UTILS_AVAILABLE:
        from .utils.exceptions import AshyTerminalError
    else:
        # Fallback error class if utils not available
        class AshyTerminalError(Exception):
            """Base exception for Ashy Terminal (fallback)."""
            pass
except ImportError:
    class AshyTerminalError(Exception):
        """Base exception for Ashy Terminal (fallback)."""
        pass


# Automatic initialization on import
def _auto_initialize():
    """Automatically initialize on module import."""
    try:
        # Only do basic checks on import, full init happens when needed
        python_ok, python_error = _check_python_version()
        if not python_ok:
            print(f"CRITICAL: {python_error}")
            print("Please upgrade Python to continue.")
            return
        
        gtk_ok, gtk_error = _check_gtk_requirements()
        if not gtk_ok:
            print(f"CRITICAL: {gtk_error}")
            print("Please install required GTK4/Adwaita libraries:")
            print("  Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adwaita-1")
            print("  Fedora: sudo dnf install python3-gobject gtk4-devel libadwaita-devel")
            print("  Arch: sudo pacman -S python-gobject gtk4 libadwaita")
            return
        
        # Check utility systems (non-critical)
        _check_utility_systems()
        
        # If we get here, basic requirements are met
        if not VTE_AVAILABLE:
            warnings.warn(
                "VTE library not found. Terminal functionality will be limited. "
                "Install gir1.2-vte-2.91 for full functionality.",
                ImportWarning,
                stacklevel=2
            )
        
    except Exception as e:
        warnings.warn(f"Package initialization check failed: {e}", RuntimeWarning)


# Run automatic initialization
_auto_initialize()


# Module-level convenience functions
def main():
    """Main entry point when run as module."""
    try:
        from .main import main as app_main
        return app_main()
    except ImportError as e:
        print(f"Error: Could not import main module: {e}")
        print("Make sure all dependencies are installed.")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)