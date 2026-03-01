# ashyterm/ui/dialogs/command_palette.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ...utils.translation_utils import _

# Commands exposed in the palette: (action_name, label, category, prefix)
# prefix: "win" or "app"
PALETTE_COMMANDS = [
    # Tabs & Panes
    ("new-local-tab", _("New Tab"), _("Tabs"), "win"),
    ("close-tab", _("Close Tab"), _("Tabs"), "win"),
    ("next-tab", _("Next Tab"), _("Tabs"), "win"),
    ("previous-tab", _("Previous Tab"), _("Tabs"), "win"),
    ("move-tab-left", _("Move Tab Left"), _("Tabs"), "win"),
    ("move-tab-right", _("Move Tab Right"), _("Tabs"), "win"),
    # Splitting
    ("split-horizontal", _("Split Horizontally"), _("Split"), "win"),
    ("split-vertical", _("Split Vertically"), _("Split"), "win"),
    ("close-pane", _("Close Active Pane"), _("Split"), "win"),
    # Terminal
    ("copy", _("Copy"), _("Terminal"), "win"),
    ("paste", _("Paste"), _("Terminal"), "win"),
    ("select-all", _("Select All"), _("Terminal"), "win"),
    ("clear-session", _("Clear Session"), _("Terminal"), "win"),
    ("toggle-search", _("Search in Terminal"), _("Terminal"), "win"),
    ("toggle-broadcast", _("Broadcast to All Tabs"), _("Terminal"), "win"),
    # Zoom
    ("zoom-in", _("Zoom In"), _("Zoom"), "win"),
    ("zoom-out", _("Zoom Out"), _("Zoom"), "win"),
    ("zoom-reset", _("Reset Zoom"), _("Zoom"), "win"),
    # AI
    ("ai-assistant", _("Ask AI Assistant"), _("AI"), "win"),
    ("configure-ai", _("Configure AI"), _("AI"), "win"),
    ("ask-ai-selection", _("Ask AI About Selection"), _("AI"), "win"),
    # Panels
    ("toggle-sidebar", _("Toggle Sessions Panel"), _("View"), "win"),
    ("toggle-file-manager", _("Toggle File Manager"), _("View"), "win"),
    ("show-command-manager", _("Command Manager"), _("View"), "win"),
    # Session management
    ("add-session-root", _("New SSH Session"), _("Sessions"), "win"),
    ("add-folder-root", _("New Session Folder"), _("Sessions"), "win"),
    ("quick-connect", _("Quick Connect (SSH Config)"), _("Sessions"), "win"),
    ("save-layout", _("Save Layout"), _("Sessions"), "win"),
    # Application
    ("new-window", _("New Window"), _("Application"), "win"),
    ("shortcuts", _("Keyboard Shortcuts"), _("Application"), "win"),
    ("preferences", _("Preferences"), _("Application"), "win"),
    ("highlight-settings", _("Highlight Settings"), _("Application"), "win"),
    ("about", _("About"), _("Application"), "app"),
    ("backup-now", _("Backup Now"), _("Application"), "app"),
    ("restore-backup", _("Restore Backup"), _("Application"), "app"),
    ("quit", _("Quit"), _("Application"), "app"),
]


class CommandPalette(Adw.Dialog):
    """
    VS Code-style command palette for quick action access.
    Activated with Ctrl+Shift+P.
    """

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.app = window.get_application()
        self.settings_manager = window.settings_manager
        self._filtered_commands = list(PALETTE_COMMANDS)

        self.set_content_width(500)
        self.set_content_height(400)
        self.set_title("")
        self.add_css_class("command-palette")

        self._build_ui()

    def _build_ui(self):
        self._toolbar_view = Adw.ToolbarView()
        self.set_child(self._toolbar_view)

        # Close on click outside the content area
        click = Gtk.GestureClick.new()
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_overlay_pressed)
        self.add_controller(click)

        # Header with search
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.set_placeholder_text(_("Type a command…"))
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_activate)
        header.set_title_widget(self.search_entry)

        self._toolbar_view.add_top_bar(header)

        # Command list
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", self._on_row_activated)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(self.list_box)
        self._toolbar_view.set_content(scrolled)

        # Key controller for navigation — CAPTURE phase so ESC is handled
        # before the SearchEntry can absorb it
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        self._populate_list()

    def _populate_list(self):
        """Fill the list box with command rows."""
        while row := self.list_box.get_row_at_index(0):
            self.list_box.remove(row)

        for action_name, label, category, prefix in self._filtered_commands:
            row = self._create_command_row(action_name, label, category, prefix)
            self.list_box.append(row)

        # Select first row
        first = self.list_box.get_row_at_index(0)
        if first:
            self.list_box.select_row(first)

    def _create_command_row(
        self, action_name: str, label: str, category: str, prefix: str
    ) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._action_name = action_name
        row._prefix = prefix

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hbox.set_margin_top(8)
        hbox.set_margin_bottom(8)
        hbox.set_margin_start(12)
        hbox.set_margin_end(12)

        # Category badge
        cat_label = Gtk.Label(label=category)
        cat_label.add_css_class("dim-label")
        cat_label.add_css_class("caption")
        cat_label.set_size_request(80, -1)
        cat_label.set_xalign(0)
        hbox.append(cat_label)

        # Command name
        name_label = Gtk.Label(label=label, hexpand=True, xalign=0)
        hbox.append(name_label)

        # Shortcut keycap (if exists)
        full_action = f"{prefix}.{action_name}"
        accels = self.app.get_accels_for_action(full_action)
        if accels:
            shortcut_label = Adw.ShortcutLabel(accelerator=accels[0])
            shortcut_label.set_valign(Gtk.Align.CENTER)
            hbox.append(shortcut_label)

        row.set_child(hbox)
        return row

    def _on_search_changed(self, entry):
        query = entry.get_text().strip().lower()
        if not query:
            self._filtered_commands = list(PALETTE_COMMANDS)
        else:
            self._filtered_commands = [
                cmd
                for cmd in PALETTE_COMMANDS
                if query in cmd[1].lower()
                or query in cmd[2].lower()
                or query in cmd[0].lower()
            ]
        self._populate_list()

    def _on_search_activate(self, _entry):
        """Activate selected row on Enter."""
        selected = self.list_box.get_selected_row()
        if selected:
            self._activate_command(selected)

    def _on_row_activated(self, _list_box, row):
        self._activate_command(row)

    def _activate_command(self, row):
        action_name = row._action_name
        prefix = row._prefix
        self.close()
        GLib.idle_add(self._run_action, prefix, action_name)

    def _run_action(self, prefix, action_name):
        """Execute the action after the palette is closed."""
        full_name = f"{prefix}.{action_name}"
        if prefix == "app":
            self.app.activate_action(action_name)
        else:
            self.window.activate_action(full_name, None)
        return GLib.SOURCE_REMOVE

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP

        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            selected = self.list_box.get_selected_row()
            if not selected:
                return Gdk.EVENT_PROPAGATE

            idx = selected.get_index()
            if keyval == Gdk.KEY_Down:
                next_row = self.list_box.get_row_at_index(idx + 1)
            else:
                next_row = self.list_box.get_row_at_index(max(0, idx - 1))

            if next_row:
                self.list_box.select_row(next_row)
                next_row.grab_focus()
            return Gdk.EVENT_STOP

        # Redirect typing to search entry
        if not self.search_entry.has_focus():
            mods = state & Gtk.accelerator_get_default_mod_mask()
            if not mods and keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self.search_entry.grab_focus()

        return Gdk.EVENT_PROPAGATE

    def _on_overlay_pressed(self, gesture, _n_press, x, y):
        """Close palette when clicking outside the content area."""
        picked = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if (
            picked is None
            or picked is self
            or not picked.is_ancestor(self._toolbar_view)
        ):
            self.close()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
