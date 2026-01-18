# ashyterm/ui/widgets/action_rows.py
"""
Action Rows - Specialized row widgets for lists with common actions.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GObject

from ..dialogs.base_dialog import create_icon_button
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _


class ManagedListRow(Adw.ActionRow):
    """
    A specialized ActionRow for manageable lists.

    Provides built-in support for:
    - Reordering (Up/Down buttons)
    - Actions (Edit/Delete buttons)
    - State (Toggle switch)
    - Custom prefix/suffix widgets
    """

    __gsignals__ = {
        "edit-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "delete-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "move-up-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "move-down-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        show_reorder: bool = False,
        show_actions: bool = True,
        show_toggle: bool = False,
        is_first: bool = False,
        is_last: bool = False,
        **kwargs,
    ):
        super().__init__(title=title, subtitle=subtitle, **kwargs)

        self._show_reorder = show_reorder
        self._show_actions = show_actions
        self._show_toggle = show_toggle

        self._setup_ui(is_first, is_last)

    def _setup_ui(self, is_first: bool, is_last: bool):
        # 1. Reorder buttons (Prefix)
        if self._show_reorder:
            self._reorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self._reorder_box.set_valign(Gtk.Align.CENTER)
            self._reorder_box.set_margin_end(8)

            self._up_btn = Gtk.Button(icon_name="go-up-symbolic")
            self._up_btn.add_css_class("flat")
            self._up_btn.add_css_class("circular")
            self._up_btn.set_size_request(24, 24)
            self._up_btn.set_sensitive(not is_first)
            self._up_btn.connect("clicked", lambda _: self.emit("move-up-clicked"))
            get_tooltip_helper().add_tooltip(self._up_btn, _("Move up"))
            self._reorder_box.append(self._up_btn)

            self._down_btn = Gtk.Button(icon_name="go-down-symbolic")
            self._down_btn.add_css_class("flat")
            self._down_btn.add_css_class("circular")
            self._down_btn.set_size_request(24, 24)
            self._down_btn.set_sensitive(not is_last)
            self._down_btn.connect("clicked", lambda _: self.emit("move-down-clicked"))
            get_tooltip_helper().add_tooltip(self._down_btn, _("Move down"))
            self._reorder_box.append(self._down_btn)

            self.add_prefix(self._reorder_box)

        # 2. Action buttons (Suffix)
        if self._show_actions:
            self._edit_btn = create_icon_button(
                "document-edit-symbolic",
                tooltip=_("Edit"),
                on_clicked=lambda _: self.emit("edit-clicked"),
                flat=True,
                valign=Gtk.Align.CENTER,
            )
            self.add_suffix(self._edit_btn)

            self._delete_btn = create_icon_button(
                "user-trash-symbolic",
                tooltip=_("Delete"),
                on_clicked=lambda _: self.emit("delete-clicked"),
                flat=True,
                valign=Gtk.Align.CENTER,
            )
            self.add_suffix(self._delete_btn)

        # 3. Toggle switch (Suffix - rightmost)
        if self._show_toggle:
            self._switch = Gtk.Switch()
            self._switch.set_valign(Gtk.Align.CENTER)
            self._switch.connect("notify::active", self._on_switch_toggled)
            self.add_suffix(self._switch)

    def _on_switch_toggled(self, switch, _pspec):
        self.emit("toggled", switch.get_active())

    def set_reorder_sensitive(self, is_first: bool, is_last: bool):
        """Update sensitivity of reorder buttons."""
        if hasattr(self, "_up_btn"):
            self._up_btn.set_sensitive(not is_first)
        if hasattr(self, "_down_btn"):
            self._down_btn.set_sensitive(not is_last)

    def set_active(self, active: bool):
        """Set the toggle switch state."""
        if hasattr(self, "_switch"):
            # Block signal to avoid recursion if connected via set_active
            self._switch.handler_block_by_func(self._on_switch_toggled)
            self._switch.set_active(active)
            self._switch.handler_unblock_by_func(self._on_switch_toggled)

    def get_active(self) -> bool:
        """Get the toggle switch state."""
        if hasattr(self, "_switch"):
            return self._switch.get_active()
        return False

    def set_actions_sensitive(self, edit: bool = True, delete: bool = True):
        """Update sensitivity of action buttons."""
        if hasattr(self, "_edit_btn"):
            self._edit_btn.set_sensitive(edit)
        if hasattr(self, "_delete_btn"):
            self._delete_btn.set_sensitive(delete)

    def set_delete_tooltip(self, text: str):
        """Update tooltip for delete button."""
        if hasattr(self, "_delete_btn"):
            get_tooltip_helper().add_tooltip(self._delete_btn, text)


class ManagedExpanderRow(Adw.ExpanderRow):
    """
    A specialized ExpanderRow for manageable lists.

    Provides built-in support for:
    - Reordering (Up/Down buttons)
    - Actions (Delete button)
    - Custom suffix widgets
    """

    __gsignals__ = {
        "delete-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "move-up-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "move-down-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        show_reorder: bool = True,
        show_delete: bool = True,
        is_first: bool = False,
        is_last: bool = False,
        **kwargs,
    ):
        super().__init__(title=title, subtitle=subtitle, **kwargs)

        self._show_reorder = show_reorder
        self._show_delete = show_delete

        self._setup_ui(is_first, is_last)

    def _setup_ui(self, is_first: bool, is_last: bool):
        # 1. Reorder buttons (Suffix - ExpanderRow doesn't support prefix well for actions)
        if self._show_reorder:
            self._up_btn = Gtk.Button(icon_name="go-up-symbolic")
            self._up_btn.add_css_class("flat")
            self._up_btn.add_css_class("circular")
            self._up_btn.set_valign(Gtk.Align.CENTER)
            self._up_btn.set_sensitive(not is_first)
            self._up_btn.connect("clicked", lambda _: self.emit("move-up-clicked"))
            get_tooltip_helper().add_tooltip(self._up_btn, _("Move up"))
            self.add_suffix(self._up_btn)

            self._down_btn = Gtk.Button(icon_name="go-down-symbolic")
            self._down_btn.add_css_class("flat")
            self._down_btn.add_css_class("circular")
            self._down_btn.set_valign(Gtk.Align.CENTER)
            self._down_btn.set_sensitive(not is_last)
            self._down_btn.connect("clicked", lambda _: self.emit("move-down-clicked"))
            get_tooltip_helper().add_tooltip(self._down_btn, _("Move down"))
            self.add_suffix(self._down_btn)

        # 2. Delete button (Suffix)
        if self._show_delete:
            self._delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
            self._delete_btn.add_css_class("flat")
            self._delete_btn.add_css_class("circular")
            self._delete_btn.add_css_class("error")
            self._delete_btn.set_valign(Gtk.Align.CENTER)
            self._delete_btn.connect("clicked", lambda _: self.emit("delete-clicked"))
            get_tooltip_helper().add_tooltip(self._delete_btn, _("Remove"))
            self.add_suffix(self._delete_btn)

    def set_reorder_sensitive(self, is_first: bool, is_last: bool):
        """Update sensitivity of reorder buttons."""
        if hasattr(self, "_up_btn"):
            self._up_btn.set_sensitive(not is_first)
        if hasattr(self, "_down_btn"):
            self._down_btn.set_sensitive(not is_last)
