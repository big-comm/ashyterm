"""Main application window — composition of mixins.

All public API remains importable from this module:
    from ashyterm.window import CommTerminalWindow, PASTE_START, PASTE_END, APP_TITLE, MSG_NO_ACTIVE_TERMINAL
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, GLib, Gtk

from .settings.manager import SettingsManager
from .utils.logger import get_logger
from .utils.translation_utils import _
from .window_actions import MSG_NO_ACTIVE_TERMINAL, PASTE_END, PASTE_START, WindowActionsMixin
from .window_file_drop import FileDragDropManager
from .window_lifecycle import WindowLifecycleMixin
from .window_layouts import WindowLayoutsMixin
from .window_tabs import WindowTabsMixin

# Re-export constants for API compatibility
APP_TITLE = _("Ashy Terminal")

# Re-export mixins for external access if needed
__all__ = [
    "CommTerminalWindow",
    "APP_TITLE",
    "MSG_NO_ACTIVE_TERMINAL",
    "PASTE_START",
    "PASTE_END",
    "WindowActionsMixin",
    "WindowTabsMixin",
    "WindowLifecycleMixin",
    "WindowLayoutsMixin",
]


class CommTerminalWindow(
    WindowLifecycleMixin,
    WindowTabsMixin,
    WindowActionsMixin,
    WindowLayoutsMixin,
    FileDragDropManager,
    Adw.ApplicationWindow,
):
    """Main application window. Central orchestrator via mixin composition."""

    def __init__(self, application, settings_manager: SettingsManager, **kwargs):
        super().__init__(application=application)
        self.logger = get_logger("ashyterm.window")
        self.logger.info("Initializing main window")

        self.settings_manager = settings_manager
        self.is_main_window = True

        self._lifecycle_init_common()

        # Kwargs originate from CLI args or from CommTerminalApp detach flow.
        self.initial_working_directory = kwargs.get("initial_working_directory")
        self.initial_execute_command = kwargs.get("initial_execute_command")
        self.close_after_execute = kwargs.get("close_after_execute", False)
        self.initial_ssh_target = kwargs.get("initial_ssh_target")
        self._is_for_detached_tab = kwargs.get("_is_for_detached_tab", False)
        self.detached_terminals_data = kwargs.get("detached_terminals_data")
        self.detached_file_manager = kwargs.get("detached_file_manager")

        self._setup_initial_window_size()
        self.set_title(APP_TITLE)
        self.set_icon_name(None)

        self._create_managers_and_ui()
        self._connect_component_signals()
        self._setup_window_events()

        # Detach flow: adopt pre-built terminals from the source window.
        if self._is_for_detached_tab and self.detached_terminals_data:
            self.logger.info(
                f"Re-registering and reconnecting signals for {len(self.detached_terminals_data)} terminals."
            )
            for term_data in self.detached_terminals_data:
                terminal_widget = term_data["widget"]
                terminal_id = term_data["id"]
                terminal_info = term_data["info"]

                self.terminal_manager.registry.reregister_terminal(
                    terminal=terminal_widget,
                    terminal_id=terminal_id,
                    terminal_info=terminal_info,
                )

                self.terminal_manager._setup_terminal_events(
                    terminal=terminal_widget,
                    identifier=terminal_info.get("identifier"),
                    terminal_id=terminal_id,
                )

                pane = terminal_widget.get_parent().get_parent()
                if isinstance(pane, Adw.ToolbarView) and hasattr(pane, "close_button"):
                    old_close_button = pane.close_button
                    old_move_button = pane.move_button
                    button_container = old_close_button.get_parent()

                    if button_container:
                        new_close_button = Gtk.Button(
                            tooltip_text=_("Close Pane"),
                        )
                        from .utils.icons import icon_image

                        new_close_button.set_child(icon_image("window-close-symbolic"))
                        new_close_button.add_css_class("flat")
                        new_close_button.connect(
                            "clicked",
                            lambda _, term=terminal_widget: self.tab_manager.close_pane(
                                term
                            ),
                        )

                        new_move_button = Gtk.Button(
                            tooltip_text=_("Move to New Tab"),
                        )
                        new_move_button.set_child(
                            icon_image("select-rectangular-symbolic")
                        )
                        new_move_button.add_css_class("flat")
                        new_move_button.connect(
                            "clicked",
                            lambda _, term=terminal_widget: (
                                self.tab_manager._on_move_to_tab_callback(term)
                            ),
                        )

                        button_container.remove(old_move_button)
                        button_container.remove(old_close_button)
                        button_container.append(new_move_button)
                        button_container.append(new_close_button)

                        pane.move_button = new_move_button
                        pane.close_button = new_close_button

                        self.logger.info(
                            f"Reconnected UI controls for terminal {terminal_id}"
                        )

        self._apply_initial_visual_settings()

        # Deferred: session tree load + repair-notice toast.
        def _deferred_init():
            if not self._is_for_detached_tab:
                self._load_initial_data()
            if self.settings_manager._was_repaired:
                self.settings_manager._was_repaired = False
                self.toast_overlay.add_toast(
                    Adw.Toast(
                        title=_(
                            "Settings were automatically repaired after detecting corruption."
                        )
                    )
                )
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_deferred_init)

        self.logger.info("Main window initialization completed")
