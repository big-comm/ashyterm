"""Command Button Widget — button representing a command in the manager."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gio, GObject, Gtk, Pango

from ....data.command_manager_models import (
    CommandButton,
    DisplayMode,
    get_command_button_manager,
)
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _


class CommandButtonWidget(Gtk.Button):
    """
    A button widget representing a command in the Command Manager.
    Supports editing, deleting, restoring defaults, hiding, duplicating, and pinning.
    """

    __gsignals__ = {
        "command-activated": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "command-activated-all": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),  # Execute in all terminals
        "edit-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "delete-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "restore-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "hide-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "duplicate-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "pin-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "unpin-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
    }

    def __init__(self, command: CommandButton):
        super().__init__()
        self.command = command
        self.add_css_class("command-button")

        self._build_ui()
        self._setup_context_menu()

        self.connect("clicked", self._on_clicked)

    def _build_ui(self):
        """Build the button content based on display mode."""
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        if self.command.display_mode in (
            DisplayMode.ICON_ONLY,
            DisplayMode.ICON_AND_TEXT,
        ):
            icon = Gtk.Image.new_from_icon_name(self.command.icon_name)
            icon.set_icon_size(Gtk.IconSize.NORMAL)
            content_box.append(icon)

        if self.command.display_mode in (
            DisplayMode.TEXT_ONLY,
            DisplayMode.ICON_AND_TEXT,
        ):
            label = Gtk.Label(label=self.command.name)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            content_box.append(label)

        self.set_child(content_box)
        get_tooltip_helper().add_tooltip(self, self.command.description)

    def _setup_context_menu(self):
        """Setup right-click context menu for all buttons (builtin and custom)."""
        menu = Gio.Menu()

        # Execute in all terminals - available for all commands
        menu.append(_("Execute in All Terminals"), "button.execute_all")

        # Edit option - available for all commands
        menu.append(_("Edit"), "button.edit")

        # Duplicate option - available for all commands
        menu.append(_("Duplicate"), "button.duplicate")

        # Get command manager to check customization/pin status
        command_manager = get_command_button_manager()

        # Pin/Unpin option - available for all commands
        if command_manager.is_command_pinned(self.command.id):
            menu.append(_("Unpin from Toolbar"), "button.unpin")
        else:
            menu.append(_("Pin to Toolbar"), "button.pin")

        if self.command.is_builtin:
            # For builtin commands: show restore if customized, always show hide
            if command_manager.is_builtin_customized(self.command.id):
                menu.append(_("Restore Default"), "button.restore")
            menu.append(_("Hide"), "button.hide")
        else:
            # For custom commands: show delete
            menu.append(_("Delete"), "button.delete")

        # Action group
        action_group = Gio.SimpleActionGroup()

        # Execute in all terminals action
        execute_all_action = Gio.SimpleAction.new("execute_all", None)
        execute_all_action.connect(
            "activate", lambda *_: self.emit("command-activated-all", self.command)
        )
        action_group.add_action(execute_all_action)

        edit_action = Gio.SimpleAction.new("edit", None)
        edit_action.connect(
            "activate", lambda *_: self.emit("edit-requested", self.command)
        )
        action_group.add_action(edit_action)

        duplicate_action = Gio.SimpleAction.new("duplicate", None)
        duplicate_action.connect(
            "activate", lambda *_: self.emit("duplicate-requested", self.command)
        )
        action_group.add_action(duplicate_action)

        # Pin/Unpin actions
        if command_manager.is_command_pinned(self.command.id):
            unpin_action = Gio.SimpleAction.new("unpin", None)
            unpin_action.connect(
                "activate", lambda *_: self.emit("unpin-requested", self.command)
            )
            action_group.add_action(unpin_action)
        else:
            pin_action = Gio.SimpleAction.new("pin", None)
            pin_action.connect(
                "activate", lambda *_: self.emit("pin-requested", self.command)
            )
            action_group.add_action(pin_action)

        if self.command.is_builtin:
            if command_manager.is_builtin_customized(self.command.id):
                restore_action = Gio.SimpleAction.new("restore", None)
                restore_action.connect(
                    "activate", lambda *_: self.emit("restore-requested", self.command)
                )
                action_group.add_action(restore_action)

            hide_action = Gio.SimpleAction.new("hide", None)
            hide_action.connect(
                "activate", lambda *_: self.emit("hide-requested", self.command)
            )
            action_group.add_action(hide_action)
        else:
            delete_action = Gio.SimpleAction.new("delete", None)
            delete_action.connect(
                "activate", lambda *_: self.emit("delete-requested", self.command)
            )
            action_group.add_action(delete_action)

        self.insert_action_group("button", action_group)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.add_css_class("ashyterm-popover")
        popover.set_parent(self)

        # Right-click gesture
        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)  # Right click
        gesture.connect("pressed", lambda g, n, x, y: popover.popup())
        self.add_controller(gesture)

        # Keyboard context menu (Shift+F10 / Menu key)
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect(
            "key-pressed",
            lambda _c, kv, _kc, st: (
                (popover.popup() or True)
                if kv == Gdk.KEY_Menu
                or (kv == Gdk.KEY_F10 and st & Gdk.ModifierType.SHIFT_MASK)
                else False
            ),
        )
        self.add_controller(key_ctrl)

    def _on_clicked(self, button):
        """Handle button click."""
        self.emit("command-activated", self.command)
