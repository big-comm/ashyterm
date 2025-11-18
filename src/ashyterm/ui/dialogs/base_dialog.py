# ashyterm/ui/dialogs/base_dialog.py

from typing import Any, Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ...settings.config import get_config_paths
from ...utils.logger import get_logger
from ...utils.translation_utils import _


class BaseDialog(Adw.Window):
    """Base dialog class with enhanced functionality and error handling."""

    def __init__(self, parent_window, dialog_title: str, **kwargs):
        default_props = {
            "title": dialog_title,
            "modal": True,
            "transient_for": parent_window,
            "hide_on_close": True,
        }
        default_props.update(kwargs)
        super().__init__(**default_props)

        self.logger = get_logger(
            f"ashyterm.ui.dialogs.{self.__class__.__name__.lower()}"
        )
        self.parent_window = parent_window
        self.config_paths = get_config_paths()
        self._validation_errors: List[str] = []
        self._has_changes = False
        self._original_data: Optional[Dict[str, Any]] = None

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self._on_cancel_clicked(None)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_cancel_clicked(self, button):
        self.close()

    def _mark_changed(self):
        self._has_changes = True

    def _show_error_dialog(
        self, title: str, message: str, details: Optional[str] = None
    ) -> None:
        try:
            dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
            if details:
                dialog.set_body_use_markup(True)
                full_body = (
                    f"{message}\n\n<small>{GLib.markup_escape_text(details)}</small>"
                )
                dialog.set_body(full_body)
            dialog.add_response("ok", _("OK"))
            dialog.present()
            self.logger.warning(f"Error dialog shown: {title} - {message}")
        except Exception as e:
            self.logger.error(f"Failed to show error dialog: {e}")

    def _show_warning_dialog(
        self, title: str, message: str, on_confirm: Optional[Callable] = None
    ) -> None:
        try:
            dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("confirm", _("Continue"))
            dialog.set_response_appearance(
                "confirm", Adw.ResponseAppearance.DESTRUCTIVE
            )

            def on_response(dlg, response_id):
                if response_id == "confirm" and on_confirm:
                    on_confirm()
                dlg.close()

            dialog.connect("response", on_response)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show warning dialog: {e}")

    def _validate_required_field(self, entry: Gtk.Entry, field_name: str) -> bool:
        value = entry.get_text().strip()
        if not value:
            entry.add_css_class("error")
            self._validation_errors.append(_("{} is required").format(field_name))
            return False
        else:
            entry.remove_css_class("error")
            return True

    def _clear_validation_errors(self):
        self._validation_errors.clear()