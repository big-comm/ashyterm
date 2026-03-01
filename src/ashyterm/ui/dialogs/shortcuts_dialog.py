# ashyterm/ui/dialogs/shortcuts_dialog.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from ...utils.translation_utils import _

# Centralized data structure for shortcuts.
SHORTCUT_DATA = [
    {
        "group_title": _("Tab &amp; Pane Management"),
        "shortcuts": [
            ("new-local-tab", _("New Tab")),
            ("close-tab", _("Close Tab")),
            ("next-tab", _("Next Tab")),
            ("previous-tab", _("Previous Tab")),
        ],
    },
    {
        "group_title": _("Splitting"),
        "shortcuts": [
            ("split-horizontal", _("Split Horizontally")),
            ("split-vertical", _("Split Vertically")),
            ("close-pane", _("Close Active Pane")),
        ],
    },
    {
        "group_title": _("Terminal Interaction"),
        "shortcuts": [
            ("copy", _("Copy")),
            ("paste", _("Paste")),
            ("select-all", _("Select All")),
            ("clear-session", _("Clear Session")),
            ("toggle-search", _("Search in Terminal")),
            ("toggle-broadcast", _("Send Command to All Tabs")),
            ("ai-assistant", _("Ask AI Assistant")),
        ],
    },
    {
        "group_title": _("Zoom"),
        "shortcuts": [
            ("zoom-in", _("Zoom In")),
            ("zoom-out", _("Zoom Out")),
            ("zoom-reset", _("Reset Zoom")),
        ],
    },
    {
        "group_title": _("Application &amp; Window"),
        "shortcuts": [
            ("toggle-sidebar", _("Sessions Panel")),
            ("toggle-file-manager", _("File Manager")),
            ("show-command-manager", _("Command Manager")),
            ("new-window", _("New Window")),
            ("preferences", _("Preferences")),
            ("quit", _("Quit Application")),
        ],
    },
]


class ShortcutsDialog(Adw.PreferencesDialog):
    """
    Modern dialog for viewing and editing keyboard shortcuts.
    Uses Adw.PreferencesDialog with Adw.ShortcutLabel for keycap-styled display.
    """

    def __init__(self, parent_window):
        super().__init__(
            title=_("Keyboard Shortcuts"),
            search_enabled=False,
            content_width=600,
            content_height=550,
        )
        self.parent_window = parent_window
        self.app = parent_window.get_application()
        self.settings_manager = parent_window.settings_manager
        self.shortcut_rows: dict[str, tuple[Adw.ActionRow, Adw.ShortcutLabel]] = {}

        self._build_ui()

    def _build_ui(self):
        page = Adw.PreferencesPage()
        self.add(page)

        # Reset group at the top
        reset_group = Adw.PreferencesGroup()
        reset_row = Adw.ActionRow(
            title=_("Reset all shortcuts to defaults"), activatable=True
        )
        reset_row.connect("activated", self._on_reset_all_clicked)
        reset_row.add_suffix(Gtk.Image.new_from_icon_name("view-refresh-symbolic"))
        reset_group.add(reset_row)
        page.add(reset_group)

        # Shortcut groups
        for group_data in SHORTCUT_DATA:
            group = Adw.PreferencesGroup(title=group_data["group_title"])
            for action_name, title in group_data["shortcuts"]:
                row, shortcut_label = self._create_shortcut_row(action_name, title)
                group.add(row)
                self.shortcut_rows[action_name] = (row, shortcut_label)
            page.add(group)

    def _create_shortcut_row(
        self, action_name: str, title: str
    ) -> tuple[Adw.ActionRow, Adw.ShortcutLabel]:
        """Creates a row with Adw.ShortcutLabel suffix for keycap display."""
        action_prefix = "app" if action_name in ["quit"] else "win"
        full_action_name = f"{action_prefix}.{action_name}"
        accels = self.app.get_accels_for_action(full_action_name)
        current_accel = accels[0] if accels else ""

        shortcut_label = Adw.ShortcutLabel(accelerator=current_accel)
        shortcut_label.set_valign(Gtk.Align.CENTER)
        if not current_accel:
            shortcut_label.set_visible(False)

        not_set_label = Gtk.Label(label=_("Not set"))
        not_set_label.add_css_class("dim-label")
        not_set_label.set_valign(Gtk.Align.CENTER)
        not_set_label.set_visible(not current_accel)

        row = Adw.ActionRow(title=title, activatable=True)
        row.connect("activated", self._on_row_activated, action_name)
        row.add_suffix(not_set_label)
        row.add_suffix(shortcut_label)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))

        # Store reference to the "not set" label on the row for later update
        row._not_set_label = not_set_label

        return row, shortcut_label

    def _on_row_activated(self, _row, action_name: str):
        """Open shortcut capture dialog when row is clicked."""
        row, shortcut_label = self.shortcut_rows[action_name]
        self._show_capture_dialog(action_name, row, shortcut_label)

    def _show_capture_dialog(
        self,
        shortcut_key: str,
        row: Adw.ActionRow,
        shortcut_label: Adw.ShortcutLabel,
    ):
        """Capture a new keyboard shortcut."""
        current_shortcut = self.settings_manager.get_shortcut(shortcut_key)

        dialog = Adw.AlertDialog(
            heading=_("Set New Shortcut"),
            body=_("Press the new key combination for '{}', or Esc to cancel.").format(
                row.get_title()
            ),
        )
        dialog.set_close_response("cancel")

        # Show current shortcut and feedback for new one
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER
        )

        if current_shortcut:
            current_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            current_box.append(Gtk.Label(label=_("Current:")))
            current_box.append(Adw.ShortcutLabel(accelerator=current_shortcut))
            content_box.append(current_box)

        new_label = Gtk.Label(label=_("Press new shortcut…"))
        new_label.add_css_class("dim-label")
        content_box.append(new_label)

        conflict_label = Gtk.Label()
        conflict_label.add_css_class("error")
        conflict_label.set_visible(False)
        content_box.append(conflict_label)

        dialog.set_extra_child(content_box)

        key_controller = Gtk.EventControllerKey.new()
        new_shortcut_ref = [None]

        def on_key_pressed(_controller, keyval, _keycode, state):
            modifier_keys = {
                Gdk.KEY_Control_L,
                Gdk.KEY_Control_R,
                Gdk.KEY_Shift_L,
                Gdk.KEY_Shift_R,
                Gdk.KEY_Alt_L,
                Gdk.KEY_Alt_R,
                Gdk.KEY_Super_L,
                Gdk.KEY_Super_R,
            }
            if keyval in modifier_keys:
                return Gdk.EVENT_PROPAGATE

            if keyval == Gdk.KEY_Escape:
                new_shortcut_ref[0] = "cancel"
                dialog.response("cancel")
                return Gdk.EVENT_STOP

            shortcut_string = Gtk.accelerator_name(
                keyval, state & Gtk.accelerator_get_default_mod_mask()
            )
            new_shortcut_ref[0] = shortcut_string

            # Replace the "press new shortcut" label with visual ShortcutLabel
            new_label.set_visible(False)
            # Remove old preview if exists
            for child in list(content_box):
                if getattr(child, "_is_preview", False):
                    content_box.remove(child)
            preview_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            preview_box._is_preview = True
            preview_box.append(Gtk.Label(label=_("New:")))
            preview_box.append(Adw.ShortcutLabel(accelerator=shortcut_string))
            content_box.append(preview_box)

            # Check for conflicts
            conflict_action = self._find_conflict(shortcut_string, shortcut_key)
            if conflict_action:
                conflict_label.set_label(
                    _("Conflicts with: {}").format(
                        conflict_action.replace("-", " ").title()
                    )
                )
                conflict_label.set_visible(True)
            else:
                conflict_label.set_visible(False)

            return Gdk.EVENT_STOP

        key_controller.connect("key-pressed", on_key_pressed)
        dialog.add_controller(key_controller)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear"))
        dialog.add_response("save", _("Set Shortcut"))
        dialog.set_default_response("save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            action_prefix = "app" if shortcut_key in ["quit"] else "win"
            full_action_name = f"{action_prefix}.{shortcut_key}"
            new_shortcut = ""

            if (
                response_id == "save"
                and new_shortcut_ref[0]
                and new_shortcut_ref[0] != "cancel"
            ):
                new_shortcut = new_shortcut_ref[0]

            if response_id in ["save", "clear"]:
                self.settings_manager.set_shortcut(shortcut_key, new_shortcut)
                self.app.set_accels_for_action(
                    full_action_name, [new_shortcut] if new_shortcut else []
                )
                self._update_row_display(shortcut_key, new_shortcut)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _find_conflict(self, shortcut_string: str, current_key: str) -> str | None:
        """Find if a shortcut conflicts with another action."""
        for action_name in self.shortcut_rows:
            if action_name == current_key:
                continue
            stored = self.settings_manager.get_shortcut(action_name)
            if stored == shortcut_string:
                return action_name
        return None

    def _update_row_display(self, action_name: str, accel: str):
        """Update a row's ShortcutLabel after change."""
        row, shortcut_label = self.shortcut_rows[action_name]
        shortcut_label.set_accelerator(accel)
        shortcut_label.set_visible(bool(accel))
        if hasattr(row, "_not_set_label"):
            row._not_set_label.set_visible(not accel)

    def _on_reset_all_clicked(self, *_args):
        dialog = Adw.AlertDialog(
            heading=_("Reset All Shortcuts"),
            body=_(
                "This will reset all keyboard shortcuts to their default values. Continue?"
            ),
        )
        dialog.set_close_response("cancel")
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def on_response(dlg, response_id):
            if response_id == "reset":
                self.settings_manager.reset_to_defaults(["shortcuts"])
                for action_name in self.shortcut_rows:
                    shortcut = self.settings_manager.get_shortcut(action_name)
                    action_prefix = "app" if action_name in ["quit"] else "win"
                    full_action = f"{action_prefix}.{action_name}"
                    self.app.set_accels_for_action(
                        full_action, [shortcut] if shortcut else []
                    )
                    self._update_row_display(action_name, shortcut)

        dialog.connect("response", on_response)
        dialog.present(self)
