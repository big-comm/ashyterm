"""Small helper dialogs for the highlight system — refactored to use a shared base.

All three dialogs follow the same pattern: single text entry with live
validation and a Save/Create/Add action.  The base class handles the
ToolbarView/HeaderBar/EntryRow/validation-label boilerplate; subclasses
only provide title, labels, and validation logic.
"""

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

from ..base_dialog import BaseDialog
from ....utils.translation_utils import _


class SingleEntryDialog(Adw.Dialog):
    """Base dialog for single-text-entry with live validation.

    Subclass or instantiate with parameters to customise:
    - title, entry_title, description, button_label
    - validate(text) → (ok: bool, message: str)
    - on_submit(text) → emit signal or callback
    """

    def __init__(
        self,
        *,
        title: str,
        entry_title: str,
        description: str,
        button_label: str,
        validate: Callable[[str], tuple[bool, str]],
        on_submit: Callable[[str], None],
        width: int = 350,
        height: int = 200,
        initial_text: Optional[str] = None,
    ):
        super().__init__()
        self.add_css_class("ashyterm-dialog")
        self.set_title(title)
        self.set_content_width(width)
        self.set_content_height(height)

        self._validate_fn = validate
        self._on_submit_fn = on_submit
        self._initial_text = initial_text

        self._setup_ui(entry_title, description, button_label)

    def _setup_ui(self, entry_title: str, description: str, button_label: str) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        self._action_btn = Gtk.Button(label=button_label)
        self._action_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        self._action_btn.set_sensitive(False)
        self._action_btn.connect("clicked", self._on_action_clicked)
        header.pack_end(self._action_btn)

        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        toolbar_view.set_content(content_box)

        name_group = Adw.PreferencesGroup(description=description)
        self._name_row = Adw.EntryRow(title=entry_title)
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)

        if self._initial_text:
            self._name_row.set_text(self._initial_text)

    def _on_name_changed(self, _entry: Adw.EntryRow) -> None:
        name = self._name_row.get_text().strip().lower()
        ok, message = self._validate_fn(name)
        self._validation_label.set_text(message)
        if ok:
            self._validation_label.remove_css_class("error")
            self._validation_label.add_css_class("success")
        else:
            self._validation_label.remove_css_class("success")
            if message:
                self._validation_label.add_css_class("error")
        self._action_btn.set_sensitive(ok and bool(name))

    def _on_action_clicked(self, _button: Gtk.Button) -> None:
        self._on_submit_fn(self._name_row.get_text().strip().lower())
        self.close()


# ── Concrete dialogs using the base class ─────────────────────────────


class ContextNameDialog(Adw.Dialog):
    """Dialog for creating a new command context."""

    __gsignals__ = {
        "context-created": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent: Gtk.Widget):
        from ....settings.highlights import get_highlight_manager

        self._manager = get_highlight_manager()

        def validate(name: str) -> tuple[bool, str]:
            if not name:
                return (False, "")
            if name in self._manager.get_context_names():
                return (False, _("Command already exists"))
            if not name.replace("_", "").replace("-", "").isalnum():
                return (False, _("Use only letters, numbers, - and _"))
            return (True, _("✓ Valid name"))

        def on_submit(name: str) -> None:
            self.emit("context-created", name)

        super().__init__()
        self.add_css_class("ashyterm-dialog")

        content_width, content_height = 350, 200
        self.set_title(_("New Command"))
        self.set_content_width(content_width)
        self.set_content_height(content_height)

        entry_title = _("Command Name")
        description = _("Enter the command name (e.g., ping, docker, git)")
        button_label = _("Create")

        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        self._create_btn = Gtk.Button(label=button_label)
        self._create_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        self._create_btn.set_sensitive(False)
        self._create_btn.connect("clicked", self._on_create_clicked)
        header.pack_end(self._create_btn)
        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        toolbar_view.set_content(content_box)

        name_group = Adw.PreferencesGroup(description=description)
        self._name_row = Adw.EntryRow(title=entry_title)
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
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

    def _on_create_clicked(self, _button: Gtk.Button) -> None:
        name = self._name_row.get_text().strip().lower()
        self.emit("context-created", name)
        self.close()


class AddTriggerDialog(Adw.Dialog):
    """Dialog for adding or editing a trigger command for a context."""

    __gsignals__ = {
        "trigger-added": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(
        self,
        parent: Gtk.Widget,
        context_name: str,
        existing_trigger: Optional[str] = None,
    ):
        from ....settings.highlights import get_highlight_manager

        super().__init__()
        self.add_css_class("ashyterm-dialog")
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
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        btn_label = _("Save") if self._existing_trigger else _("Add")
        self._save_btn = Gtk.Button(label=btn_label)
        self._save_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        self._save_btn.set_sensitive(False)
        self._save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(self._save_btn)

        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        toolbar_view.set_content(content_box)

        name_group = Adw.PreferencesGroup(
            description=_(
                "Enter a command name that should activate the '{}' command rules."
            ).format(self._context_name),
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

    def _on_save_clicked(self, _button: Gtk.Button) -> None:
        name = self._name_row.get_text().strip().lower()
        self.emit("trigger-added", name)
        self.close()


class AddIgnoredCommandDialog(Adw.Dialog):
    """Dialog for adding a command to the ignored list."""

    __gsignals__ = {
        "command-added": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent: Gtk.Widget):
        from ....settings.manager import get_settings_manager

        super().__init__()
        self.add_css_class("ashyterm-dialog")
        self._settings = get_settings_manager()

        self.set_title(_("Add Ignored Command"))
        self.set_content_width(350)
        self.set_content_height(200)

        self._setup_ui()

    def _setup_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        self._add_btn = Gtk.Button(label=_("Add"))
        self._add_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        self._add_btn.set_sensitive(False)
        self._add_btn.connect("clicked", self._on_add_clicked)
        header.pack_end(self._add_btn)

        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        toolbar_view.set_content(content_box)

        name_group = Adw.PreferencesGroup(
            description=_(
                "Commands with native coloring (grep, ls, git, etc.) should be added here."
            ),
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
        name = entry.get_text().strip().lower()
        if not name:
            self._validation_label.set_text(BaseDialog.MSG_ENTER_COMMAND_NAME)
            self._validation_label.remove_css_class("error")
            self._add_btn.set_sensitive(False)
            return
        ignored_commands = self._settings.get("ignored_highlight_commands", [])
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

    def _on_add_clicked(self, _button: Gtk.Button) -> None:
        name = self._name_row.get_text().strip().lower()
        self.emit("command-added", name)
        self.close()
