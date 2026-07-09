import signal
import sys

if __package__ is None:
    import pathlib

    parent_dir = pathlib.Path(__file__).parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    __package__ = "ashyterm"

_logger_module = None
_translation_module = None


def _get_logger_funcs():
    global _logger_module
    if _logger_module is None:
        from .utils import logger as _logger_module
    return _logger_module


def _get_translation():
    global _translation_module
    if _translation_module is None:
        from .utils import translation_utils as _translation_module
    return _translation_module._


def setup_signal_handlers() -> None:
    """SIGINT/SIGTERM → graceful Gtk.Application.quit().

    Handler body avoids re-importing gi because the import machinery can
    be unstable mid-signal (e.g. right after a fork).
    """
    _ = _get_translation()

    def signal_handler(sig, frame):
        print("\n" + _("Received signal {}, shutting down gracefully...").format(sig))
        try:
            gtk_module = sys.modules.get("gi.repository.Gtk") or sys.modules.get(
                "gi.repository"
            )
            app = None
            if gtk_module is not None:
                app_cls = getattr(gtk_module, "Application", None)
                if app_cls is not None:
                    app = app_cls.get_default()
            if app is not None:
                app.quit()
            else:
                sys.exit(0)
        except Exception:
            sys.exit(0)

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except Exception as e:
        print(_("Warning: Could not set up signal handlers: {}").format(e))


def _configure_wayland_input_method() -> None:
    import os

    if os.environ.get(
        "XDG_SESSION_TYPE", ""
    ).lower() == "wayland" and not os.environ.get("GTK_IM_MODULE"):
        os.environ["GTK_IM_MODULE"] = "simple"


def _configure_prelaunch_logging(logger_mod) -> None:
    pre_args_debug = "--debug" in sys.argv or "-d" in sys.argv
    pre_args_log_level = next(
        (
            sys.argv[index + 1]
            for index, argument in enumerate(sys.argv[:-1])
            if argument == "--log-level"
        ),
        None,
    )
    if pre_args_debug:
        logger_mod.enable_debug_mode()
        return
    if not pre_args_log_level:
        return
    try:
        logger_mod.set_console_log_level(pre_args_log_level)
    except KeyError:
        print(f"Warning: Invalid log level '{pre_args_log_level}' provided.")


def _print_help() -> None:
    print(
        "Usage: ashyterm [OPTIONS] [DIRECTORY]\n"
        "\n"
        "Ashy Terminal - A modern terminal emulator with session management\n"
        "\n"
        "Options:\n"
        "  -h, --help                       Show this help message and exit\n"
        "  -v, --version                    Show version and exit\n"
        "  -d, --debug                      Enable debug mode\n"
        "  --log-level LEVEL                Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)\n"
        "  -w, --working-directory DIR      Set the working directory for the initial terminal\n"
        "  -e, -x, --execute COMMAND ...    Execute command in the terminal\n"
        "  --close-after-execute            Close terminal after executing command\n"
        "  --ssh [USER@]HOST[:PORT][:/PATH] Connect to SSH host\n"
        "  --new-window                     Force opening a new window\n"
        "\n"
        "For more information, visit: https://communitybig.org/"
    )


def main() -> int:
    """Application entry point."""
    _configure_wayland_input_method()
    logger_mod = _get_logger_funcs()
    _ = _get_translation()
    _configure_prelaunch_logging(logger_mod)

    logger = logger_mod.get_logger("ashyterm.main")

    try:
        import setproctitle

        setproctitle.setproctitle("ashyterm")
        logger.info("Process title set to 'ashyterm'.")
    except Exception as e:
        logger.error(f"Failed to set process title: {e}", exc_info=True)

    # --version / --help short-circuit before loading GTK.
    if "--version" in sys.argv or "-v" in sys.argv:
        from .settings.config import APP_VERSION

        print(f"ashyterm {APP_VERSION}")
        return 0

    if "--help" in sys.argv or "-h" in sys.argv:
        _print_help()
        return 0

    setup_signal_handlers()

    try:
        logger.info("Creating application instance")

        # Lazy import: GTK/Adw/VTE stay unloaded for --help/--version paths above.
        from .app import CommTerminalApp

        app = CommTerminalApp()
        return app.run(sys.argv)
    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")
        return 0
    except Exception as e:
        logger.critical(f"A fatal error occurred: {e}", exc_info=True)
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        alert = Gtk.AlertDialog()
        alert.set_message(_("Fatal Application Error"))
        alert.set_detail(_("Could not start Ashy Terminal.\n\nError: {}").format(e))
        alert.set_modal(True)
        alert.show(None)
        return 1


if __name__ == "__main__":
    sys.exit(main())
