"""Small helper dialogs for the highlight system.

Contains: ContextNameDialog, AddTriggerDialog, AddIgnoredCommandDialog.
"""

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

from ....settings.highlights import get_highlight_manager
from ....settings.manager import get_settings_manager
from ....utils.logger import get_logger
from ....utils.translation_utils import _
from ..base_dialog import BaseDialog


class ContextNameDialog(Adw.Dialog):
    """
    Dialog for creating a new command context.

    Provides a simple form to enter the command name for a new context.
    """

    __gsignals__ = {
        "context-created": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent: Gtk.Widget):
        """
        Initialize the context name dialog.

        Args:
            parent: Parent widget for the dialog.
        """
        super().__init__()
        self.add_css_class("ashyterm-dialog")
        self.logger = get_logger("ashyterm.ui.dialogs.context_name")
        self._parent = parent
        self._manager = get_highlight_manager()

        self.set_title(_("New Command"))
        self.set_content_width(350)
        self.set_content_height(200)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        self._create_btn = Gtk.Button(label=_("Create"))
        self._create_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        self._create_btn.set_sensitive(False)
        self._create_btn.connect("clicked", self._on_create_clicked)
        header.pack_end(self._create_btn)

        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        toolbar_view.set_content(content_box)

        name_group = Adw.PreferencesGroup(
            description=_("Enter the command name (e.g., ping, docker, git)")
        )
        self._name_row = Adw.EntryRow(title=_("Command Name"))
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        """Handle name entry change."""
        name = entry.get_text().strip().lower()

        if not name:
            self._validation_label.set_text(BaseDialog.MSG_ENTER_COMMAND_NAME)
            self._validation_label.remove_css_class("error")
            self._create_btn.set_sensitive(False)
            return

        if name in self._manager.get_context_names():
            self._validation_label.set_text(_("Command already exists"))
            self._validation_label.add_css_class("error")
            self._create_btn.set_sensitive(False)
            return

        if not name.replace("_", "").replace("-", "").isalnum():
            self._validation_label.set_text(_("Use only letters, numbers, - and _"))
            self._validation_label.add_css_class("error")
            self._create_btn.set_sensitive(False)
            return

        self._validation_label.set_text(_("✓ Valid name"))
        self._validation_label.remove_css_class("error")
        self._validation_label.add_css_class("success")
        self._create_btn.set_sensitive(True)

    def _on_create_clicked(self, button: Gtk.Button) -> None:
        """Handle create button click."""
        name = self._name_row.get_text().strip().lower()
        self.emit("context-created", name)
        self.close()


class AddTriggerDialog(Adw.Dialog):
    """
    Dialog for adding or editing a trigger command for a context.

    Triggers are command names that activate the highlighting context.
    """

    __gsignals__ = {
        "trigger-added": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(
        self,
        parent: Gtk.Widget,
        context_name: str,
        existing_trigger: Optional[str] = None,
    ):
        super().__init__()
        self.add_css_class("ashyterm-dialog")
        self.logger = get_logger("ashyterm.ui.dialogs.add_trigger")
        self._parent = parent
        self._context_name = context_name
        self._existing_trigger = existing_trigger
        self._manager = get_highlight_manager()

        title = _("Edit Trigger") if existing_trigger else _("Add Trigger")
        self.set_title(title)
        self.set_content_width(350)
        self.set_content_height(200)

        self._setup_ui()

        if existing_trigger:
            self._name_row.set_text(existing_trigger)

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        btn_label = _("Save") if self._existing_trigger else _("Add")
        self._save_btn = Gtk.Button(label=btn_label)
        self._save_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        self._save_btn.set_sensitive(False)
        self._save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(self._save_btn)

        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        toolbar_view.set_content(content_box)

        name_group = Adw.PreferencesGroup(
            description=_(
                "Enter a command name that should activate the '{}' command rules."
            ).format(self._context_name)
        )
        self._name_row = Adw.EntryRow(title=_("Trigger Command"))
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        """Handle name entry change."""
        name = entry.get_text().strip().lower()

        if not name:
            self._validation_label.set_text(BaseDialog.MSG_ENTER_COMMAND_NAME)
            self._validation_label.remove_css_class("error")
            self._save_btn.set_sensitive(False)
            return

        context = self._manager.get_context(self._context_name)
        if context and name in context.triggers and name != self._existing_trigger:
            self._validation_label.set_text(_("Trigger already exists"))
            self._validation_label.add_css_class("error")
            self._save_btn.set_sensitive(False)
            return

        self._validation_label.set_text(_("✓ Valid trigger"))
        self._validation_label.remove_css_class("error")
        self._validation_label.add_css_class("success")
        self._save_btn.set_sensitive(True)

    def _on_save_clicked(self, button: Gtk.Button) -> None:
        """Handle save button click."""
        name = self._name_row.get_text().strip().lower()
        self.emit("trigger-added", name)
        self.close()


class AddIgnoredCommandDialog(Adw.Dialog):
    """
    Dialog for adding a command to the ignored list.

    Commands in the ignored list will have highlighting disabled
    to preserve their native ANSI coloring.
    """

    __gsignals__ = {
        "command-added": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent: Gtk.Widget):
        super().__init__()
        self.add_css_class("ashyterm-dialog")
        self.logger = get_logger("ashyterm.ui.dialogs.add_ignored_cmd")
        self._parent = parent

        self.set_title(_("Add Ignored Command"))
        self.set_content_width(350)
        self.set_content_height(200)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        self._add_btn = Gtk.Button(label=_("Add"))
        self._add_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        self._add_btn.set_sensitive(False)
        self._add_btn.connect("clicked", self._on_add_clicked)
        header.pack_end(self._add_btn)

        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        toolbar_view.set_content(content_box)

        name_group = Adw.PreferencesGroup(
            description=_(
                "Commands with native coloring (grep, ls, git, etc.) should be added here."
            )
        )
        self._name_row = Adw.EntryRow(title=_("Command Name"))
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        """Handle name entry change."""
        name = entry.get_text().strip().lower()

        if not name:
            self._validation_label.set_text(BaseDialog.MSG_ENTER_COMMAND_NAME)
            self._validation_label.remove_css_class("error")
            self._add_btn.set_sensitive(False)
            return

        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])
        if name in ignored_commands:
            self._validation_label.set_text(_("Command already in list"))
            self._validation_label.add_css_class("error")
            self._add_btn.set_sensitive(False)
            return

        if not name.replace("_", "").replace("-", "").isalnum():
            self._validation_label.set_text(_("Use only letters, numbers, - and _"))
            self._validation_label.add_css_class("error")
            self._add_btn.set_sensitive(False)
            return

        self._validation_label.set_text(_("✓ Valid command name"))
        self._validation_label.remove_css_class("error")
        self._validation_label.add_css_class("success")
        self._add_btn.set_sensitive(True)

    def _on_add_clicked(self, button: Gtk.Button) -> None:
        """Handle add button click."""
        name = self._name_row.get_text().strip().lower()
        self.emit("command-added", name)
        self.close()
