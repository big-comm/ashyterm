# ashyterm/ui/dialogs/base_dialog.py

from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ...settings.config import get_config_paths
from ...utils.logger import get_logger
from ...utils.translation_utils import _


class BaseDialog(Adw.Window):
    """Base dialog class with enhanced functionality and error handling.

    Provides constants for common CSS classes and signals to reduce duplication.
    """

    # Common CSS classes
    CSS_CLASS_ERROR = "error"
    CSS_CLASS_SUCCESS = "success"
    CSS_CLASS_FLAT = "flat"
    CSS_CLASS_CIRCULAR = "circular"
    CSS_CLASS_SUGGESTED = "suggested-action"
    CSS_CLASS_BOXED_LIST = "boxed-list"
    CSS_CLASS_DIM_LABEL = "dim-label"

    # Common signal names
    SIGNAL_CHANGED = "changed"
    SIGNAL_CLICKED = "clicked"
    SIGNAL_NOTIFY_VALUE = "notify::value"
    SIGNAL_NOTIFY_SELECTED = "notify::selected"
    SIGNAL_NOTIFY_ACTIVE = "notify::active"
    SIGNAL_NOTIFY_STATE_SET = "state-set"

    # Common messages
    MSG_VALIDATION_ERROR = _("Validation Error")
    MSG_ENTER_COMMAND_NAME = _("Enter a command name")
    MSG_DELETE_CONFIRMATION = _("Are you sure you want to delete '{}'?")
    MSG_DELETE_RULE_HEADING = _("Delete Rule?")

    def __init__(
        self,
        parent_window,
        dialog_title: str,
        auto_setup_toolbar: bool = False,
        **kwargs,
    ):
        default_props = {
            "title": dialog_title,
            "modal": True,
            "transient_for": parent_window,
            "hide_on_close": False,
        }
        default_props.update(kwargs)
        super().__init__(**default_props)

        # Add CSS class for theming
        self.add_css_class("ashyterm-dialog")

        self.logger = get_logger(
            f"ashyterm.ui.dialogs.{self.__class__.__name__.lower()}"
        )
        self.parent_window = parent_window
        self.config_paths = get_config_paths()
        self._validation_errors: List[str] = []
        self._has_changes = False

        # Toolbar components (created only if auto_setup_toolbar is True)
        self._toolbar_view: Optional[Adw.ToolbarView] = None
        self._header_bar: Optional[Adw.HeaderBar] = None
        self._cancel_button: Optional[Gtk.Button] = None
        self._scrolled_window: Optional[Gtk.ScrolledWindow] = None

        if auto_setup_toolbar:
            self._setup_toolbar()

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)
        # Hook into lifecycle to track active modals safely
        if self.parent_window and hasattr(self.parent_window, "active_modals_count"):
            self.parent_window.active_modals_count += 1
            # Connect to destroy to decrement count
            self.connect("destroy", self._on_dialog_destroyed)

    def _on_dialog_destroyed(self, _widget):
        """Decrement active modal count when destroyed."""
        if self.parent_window and hasattr(self.parent_window, "active_modals_count"):
            self.parent_window.active_modals_count = max(
                0, self.parent_window.active_modals_count - 1
            )

    def _setup_toolbar(self) -> None:
        """Set up the ToolbarView, HeaderBar, and Cancel button."""
        self._toolbar_view = Adw.ToolbarView()

        # Create HeaderBar
        self._header_bar = Adw.HeaderBar()

        # Create Cancel button
        self._cancel_button = Gtk.Button(label=_("Cancel"))
        self._cancel_button.connect("clicked", self._on_cancel_clicked)
        self._header_bar.pack_start(self._cancel_button)

        self._toolbar_view.add_top_bar(self._header_bar)

        # Create scrolled window for content
        self._scrolled_window = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        self._toolbar_view.set_content(self._scrolled_window)

        self.set_content(self._toolbar_view)

    @property
    def header_bar(self) -> Optional[Adw.HeaderBar]:
        """Get the dialog's header bar (only available if auto_setup_toolbar=True)."""
        return self._header_bar

    @property
    def toolbar_view(self) -> Optional[Adw.ToolbarView]:
        """Get the dialog's toolbar view (only available if auto_setup_toolbar=True)."""
        return self._toolbar_view

    def set_body_content(self, widget: Gtk.Widget) -> None:
        """Set the main content widget inside the scrolled area.

        Args:
            widget: The widget to place inside the dialog's content area.

        Raises:
            RuntimeError: If auto_setup_toolbar was not enabled.
        """
        if self._scrolled_window is None:
            raise RuntimeError(
                "set_body_content requires auto_setup_toolbar=True in __init__"
            )
        self._scrolled_window.set_child(widget)

    def add_header_button(self, widget: Gtk.Widget, pack_start: bool = False) -> None:
        """Add a button or widget to the header bar.

        Args:
            widget: The widget to add (typically a Gtk.Button)
            pack_start: If True, pack at the start (left). If False, pack at the end (right).

        Raises:
            RuntimeError: If auto_setup_toolbar was not enabled.
        """
        if self._header_bar is None:
            raise RuntimeError(
                "add_header_button requires auto_setup_toolbar=True in __init__"
            )
        if pack_start:
            self._header_bar.pack_start(widget)
        else:
            self._header_bar.pack_end(widget)

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self._on_cancel_clicked(None)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_cancel_clicked(self, button):
        self.close()

    def present(self):
        """Present the dialog, hiding any tooltips first.

        Implements a fallback mechanism to recover UI responsiveness if the
        dialog fails to appear due to GTK4 window mapping issues.
        """
        # Hide all tooltips to prevent them from interfering with the dialog
        from ...utils.tooltip_helper import get_tooltip_helper

        try:
            get_tooltip_helper().hide_all()
        except Exception:
            pass

        # Track if we successfully mapped
        self._dialog_mapped = False

        def on_map(_widget):
            self._dialog_mapped = True

        # Connect to map signal to track successful display
        map_handler = self.connect("map", on_map)

        super().present()

        # Check visibility after a delay and recover if needed
        def _check_and_recover():
            self.disconnect(map_handler)
            if not self._dialog_mapped:
                self.logger.warning(
                    f"Dialog {self.__class__.__name__} failed to map within timeout. "
                    "Disabling modal to recover UI."
                )
                # Disable modal to unlock the parent window
                self.set_modal(False)
                # Force show
                self.set_visible(True)
                # Try to raise to top
                if hasattr(self, "get_surface"):
                    surface = self.get_surface()
                    if surface:
                        surface.raise_()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(500, _check_and_recover)

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

    def _validate_required_field(self, entry, field_name: str) -> bool:
        """Validate a required field. Works with both Gtk.Entry and Adw.EntryRow."""
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

    # =========================================================================
    # Form Field Creation Helpers
    # =========================================================================

    def _create_entry_row(
        self,
        title: str,
        text: str = "",
        subtitle: Optional[str] = None,
        on_changed: Optional[Callable] = None,
        css_classes: Optional[List[str]] = None,
    ) -> Adw.EntryRow:
        """Create an Adw.EntryRow with common configuration.

        Args:
            title: The row title/label.
            text: Initial text value.
            subtitle: Optional tooltip text (EntryRow doesn't support subtitle).
            on_changed: Optional callback for "changed" signal.
            css_classes: Optional list of CSS classes to add.

        Returns:
            Configured Adw.EntryRow instance.
        """
        row = Adw.EntryRow(title=title)
        # Note: EntryRow doesn't have set_subtitle, subtitle param is ignored
        # or can be used as tooltip in caller code
        if text:
            row.set_text(text)
        if on_changed:
            row.connect("changed", on_changed)
        if css_classes:
            for cls in css_classes:
                row.add_css_class(cls)
        return row

    def _create_password_row(
        self,
        title: str,
        text: str = "",
        subtitle: Optional[str] = None,
        on_changed: Optional[Callable] = None,
    ) -> Adw.PasswordEntryRow:
        """Create an Adw.PasswordEntryRow with common configuration.

        Args:
            title: The row title/label.
            text: Initial text value.
            subtitle: Optional tooltip text (PasswordEntryRow doesn't support subtitle).
            on_changed: Optional callback for "changed" signal.

        Returns:
            Configured Adw.PasswordEntryRow instance.
        """
        row = Adw.PasswordEntryRow(title=title)
        # Note: PasswordEntryRow doesn't have set_subtitle
        if text:
            row.set_text(text)
        if on_changed:
            row.connect("changed", on_changed)
        return row

    def _create_switch_row(
        self,
        title: str,
        subtitle: str = "",
        active: bool = False,
        on_changed: Optional[Callable[[bool], None]] = None,
    ) -> Adw.SwitchRow:
        """Create an Adw.SwitchRow with common configuration.

        Args:
            title: The row title/label.
            subtitle: Optional subtitle text.
            active: Initial switch state.
            on_changed: Optional callback receiving the new boolean state.

        Returns:
            Configured Adw.SwitchRow instance.
        """
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_active(active)
        if on_changed:
            row.connect(
                self.SIGNAL_NOTIFY_ACTIVE, lambda r, _: on_changed(r.get_active())
            )
        return row

    def _create_spin_row(
        self,
        title: str,
        value: float,
        min_val: float,
        max_val: float,
        step: float = 1.0,
        subtitle: Optional[str] = None,
        on_changed: Optional[Callable[[float], None]] = None,
    ) -> Adw.SpinRow:
        """Create an Adw.SpinRow with common configuration.

        Args:
            title: The row title/label.
            value: Initial value.
            min_val: Minimum value.
            max_val: Maximum value.
            step: Step increment.
            subtitle: Optional subtitle text.
            on_changed: Optional callback receiving the new value.

        Returns:
            Configured Adw.SpinRow instance.
        """
        row = Adw.SpinRow.new_with_range(min_val, max_val, step)
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_value(value)
        if on_changed:
            row.connect(
                self.SIGNAL_NOTIFY_VALUE, lambda r, _: on_changed(r.get_value())
            )
        return row

    def _create_combo_row(
        self,
        title: str,
        items: List[str],
        selected_index: int = 0,
        subtitle: Optional[str] = None,
        on_changed: Optional[Callable[[int], None]] = None,
    ) -> Adw.ComboRow:
        """Create an Adw.ComboRow with common configuration.

        Args:
            title: The row title/label.
            items: List of string items for the dropdown.
            selected_index: Initially selected index.
            subtitle: Optional subtitle text.
            on_changed: Optional callback receiving the new selected index.

        Returns:
            Configured Adw.ComboRow instance.
        """
        row = Adw.ComboRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_model(Gtk.StringList.new(items))
        row.set_selected(selected_index)
        if on_changed:
            row.connect(
                self.SIGNAL_NOTIFY_SELECTED, lambda r, _: on_changed(r.get_selected())
            )
        return row

    def _create_preferences_group(
        self,
        title: str = "",
        description: str = "",
    ) -> Adw.PreferencesGroup:
        """Create an Adw.PreferencesGroup with common configuration.

        Args:
            title: The group title.
            description: Optional group description.

        Returns:
            Configured Adw.PreferencesGroup instance.
        """
        group = Adw.PreferencesGroup()
        if title:
            group.set_title(title)
        if description:
            group.set_description(description)
        return group


# Re-export create_icon_button from utils.icons for convenience
from ...utils.icons import create_icon_button  # noqa: E402, F401


def show_delete_confirmation_dialog(
    parent: Gtk.Window,
    heading: str,
    body: str,
    on_confirm: Callable[[], None],
    delete_label: str = _("Delete"),
    cancel_label: str = _("Cancel"),
) -> None:
    """Show a standard delete confirmation dialog.

    This is a common utility function to reduce code duplication for
    delete confirmation dialogs throughout the application.

    Args:
        parent: Parent window for the dialog.
        heading: Dialog heading/title.
        body: Dialog body message.
        on_confirm: Callback to execute when delete is confirmed.
        delete_label: Label for the delete button.
        cancel_label: Label for the cancel button.
    """
    dialog = Adw.MessageDialog(
        transient_for=parent,
        heading=heading,
        body=body,
    )
    dialog.add_response("cancel", cancel_label)
    dialog.add_response("delete", delete_label)
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

    def on_response(dlg: Adw.MessageDialog, response: str) -> None:
        dlg.close()
        if response == "delete":
            on_confirm()

    dialog.connect("response", on_response)

    # Hide any tooltips before showing the dialog
    from ...utils.tooltip_helper import get_tooltip_helper

    try:
        get_tooltip_helper().hide_all()
    except Exception:
        pass

    dialog.present()


def create_mapped_combo_row(
    title: str,
    value_map: List[str],
    display_strings: List[str],
    current_value: str,
    on_change: Callable[[str], None],
    subtitle: Optional[str] = None,
) -> Adw.ComboRow:
    """Create a ComboRow with value mapping.

    This is a utility function to reduce code duplication when creating
    ComboRows that map between internal values and display strings.

    Args:
        title: The row title.
        value_map: List of internal values (e.g., ["new_tab", "new_window"]).
        display_strings: List of display strings (e.g., [_("Open new tab"), _("Open new window")]).
        current_value: The current internal value to select.
        on_change: Callback receiving the new internal value when selection changes.
        subtitle: Optional subtitle for the row.

    Returns:
        A configured Adw.ComboRow.
    """
    row = Adw.ComboRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)

    row.set_model(Gtk.StringList.new(display_strings))

    try:
        selected_index = value_map.index(current_value)
    except ValueError:
        selected_index = 0

    row.set_selected(selected_index)

    def _on_notify_selected(combo_row: Adw.ComboRow, _pspec) -> None:
        idx = combo_row.get_selected()
        if 0 <= idx < len(value_map):
            on_change(value_map[idx])

    row.connect(BaseDialog.SIGNAL_NOTIFY_SELECTED, _on_notify_selected)

    return row


def create_action_row_with_buttons(
    title: str,
    subtitle: Optional[str] = None,
    icon_name: Optional[str] = None,
    edit_callback: Optional[Callable] = None,
    delete_callback: Optional[Callable] = None,
    toggle_callback: Optional[Callable[[bool], None]] = None,
    toggle_active: bool = True,
    delete_sensitive: bool = True,
    delete_tooltip: Optional[str] = None,
) -> Adw.ActionRow:
    """Create an ActionRow with common action buttons.

    This utility creates a standardized ActionRow with optional edit, delete,
    and toggle buttons, reducing boilerplate in dialog implementations.

    Args:
        title: The row title.
        subtitle: Optional subtitle text.
        icon_name: Optional icon to show as prefix.
        edit_callback: Callback for edit button click. If None, no edit button.
        delete_callback: Callback for delete button click. If None, no delete button.
        toggle_callback: Callback for switch toggle. If None, no switch.
        toggle_active: Initial state of the switch if toggle_callback is provided.
        delete_sensitive: Whether the delete button should be sensitive.
        delete_tooltip: Custom tooltip for delete button.

    Returns:
        Configured Adw.ActionRow with the specified buttons.
    """
    from ...utils.tooltip_helper import get_tooltip_helper

    row = Adw.ActionRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)

    # Add icon prefix if specified
    if icon_name:
        from ...utils.icons import icon_image

        icon = icon_image(icon_name)
        icon.set_opacity(0.6)
        row.add_prefix(icon)

    # Add edit button
    if edit_callback:
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(edit_btn, _("Edit"))
        edit_btn.connect("clicked", edit_callback)
        row.add_suffix(edit_btn)

    # Add delete button
    if delete_callback:
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.set_sensitive(delete_sensitive)
        tooltip = delete_tooltip or _("Delete")
        get_tooltip_helper().add_tooltip(delete_btn, tooltip)
        delete_btn.connect("clicked", delete_callback)
        row.add_suffix(delete_btn)

    # Add toggle switch
    if toggle_callback:
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(toggle_active)
        switch.connect(
            BaseDialog.SIGNAL_NOTIFY_STATE_SET, lambda s, state: toggle_callback(state)
        )
        row.add_suffix(switch)

    return row


def validate_directory_path(
    entry: Gtk.Entry,
    validation_errors: List[str],
    error_message: str,
    allow_empty: bool = True,
) -> bool:
    """Validate a directory path from an entry widget.

    Common validation logic for directory path fields, reducing
    duplication across dialog validation methods.

    Args:
        entry: The entry widget containing the path.
        validation_errors: List to append error messages to.
        error_message: Error message to show if validation fails.
        allow_empty: If True, empty paths are valid.

    Returns:
        True if valid, False otherwise.
    """
    from pathlib import Path

    path_text = entry.get_text().strip()

    if not path_text:
        if allow_empty:
            entry.remove_css_class("error")
            return True
        else:
            entry.add_css_class("error")
            validation_errors.append(error_message)
            return False

    try:
        path = Path(path_text).expanduser()
        if not path.exists() or not path.is_dir():
            entry.add_css_class("error")
            validation_errors.append(error_message)
            return False
        entry.remove_css_class("error")
        return True
    except Exception:
        entry.add_css_class("error")
        validation_errors.append(_("Invalid path format"))
        return False
