"""ContextRulesDialog for editing rules of a specific command context."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ....settings.highlights import HighlightRule, get_highlight_manager
from ....settings.manager import get_settings_manager
from ....utils.icons import icon_image
from ....utils.translation_utils import _
from ...widgets.action_rows import ManagedListRow
from ..base_dialog import (
    BaseDialog,
    create_icon_button,
    show_delete_confirmation_dialog,
)
from ._constants import get_rule_subtitle
from .rule_edit_dialog import RuleEditDialog
from .small_dialogs import AddTriggerDialog


class ContextRulesDialog(BaseDialog):
    """
    Dialog for editing rules of a specific command context.

    Opens when user clicks on a context row, providing a focused
    interface for managing context-specific highlighting rules.

    Inherits from BaseDialog for consistent UI and reduced boilerplate.
    """

    __gsignals__ = {
        "context-updated": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    _SIZE_KEY_WIDTH = "context_rules_dialog_width"
    _SIZE_KEY_HEIGHT = "context_rules_dialog_height"
    _DEFAULT_WIDTH = 850
    _DEFAULT_HEIGHT = 600

    def __init__(self, parent: Gtk.Widget, context_name: str):
        settings = get_settings_manager()
        saved_width = settings.get(self._SIZE_KEY_WIDTH, self._DEFAULT_WIDTH)
        saved_height = settings.get(self._SIZE_KEY_HEIGHT, self._DEFAULT_HEIGHT)

        parent_window = None
        if isinstance(parent, Gtk.Window):
            parent_window = parent
        elif hasattr(parent, "get_root"):
            root = parent.get_root()
            if isinstance(root, Gtk.Window):
                parent_window = root

        super().__init__(
            parent_window,
            _("Command: {}").format(context_name),
            auto_setup_toolbar=True,
            default_width=saved_width,
            default_height=saved_height,
        )

        self._parent = parent
        self._context_name = context_name
        self._manager = get_highlight_manager()
        self._context_rule_rows: list[Adw.ActionRow] = []

        self.connect("close-request", self._on_close_request)

        self._setup_ui()
        self._load_context_data()

    def _on_close_request(self, window) -> bool:
        """Save window size when closing."""
        settings = get_settings_manager()
        width = self.get_width()
        height = self.get_height()

        if (
            settings.get(self._SIZE_KEY_WIDTH, 0) != width
            or settings.get(self._SIZE_KEY_HEIGHT, 0) != height
        ):
            settings.set(self._SIZE_KEY_WIDTH, width)
            settings.set(self._SIZE_KEY_HEIGHT, height)

        return False

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_body_content(content_box)

        self._prefs_page = Adw.PreferencesPage()
        content_box.append(self._prefs_page)

        # Context settings group
        settings_group = Adw.PreferencesGroup()
        self._prefs_page.add(settings_group)

        self._enable_row = Adw.SwitchRow(
            title=_("Enable Command Rules"),
        )
        self._enable_row.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_enable_toggled
        )
        settings_group.add(self._enable_row)

        self._use_global_row = Adw.SwitchRow(
            title=_("Include Global Rules"),
        )
        self._use_global_row.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_use_global_toggled
        )
        settings_group.add(self._use_global_row)

        reset_row = Adw.ActionRow(
            title=_("Reset to System Default"),
        )
        reset_row.set_activatable(True)
        reset_btn = create_icon_button(
            "edit-undo-symbolic",
            on_clicked=self._on_reset_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        reset_row.add_suffix(reset_btn)
        reset_row.set_activatable_widget(reset_btn)
        settings_group.add(reset_row)

        # Triggers group
        self._triggers_group = Adw.PreferencesGroup(
            title=_("Triggers"),
            description=_(
                "Command names or patterns that activate this rule set. "
                "When a command starting with a trigger is detected, these highlighting rules are applied."
            ),
        )
        self._prefs_page.add(self._triggers_group)

        add_trigger_row = Adw.ActionRow(
            title=_("➕ Add Trigger"),
        )
        add_trigger_row.set_activatable(True)
        add_trigger_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_trigger_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[self.CSS_CLASS_SUGGESTED],
        )
        add_trigger_row.add_suffix(add_trigger_btn)
        add_trigger_row.set_activatable_widget(add_trigger_btn)
        self._triggers_group.add(add_trigger_row)
        self._add_trigger_row = add_trigger_row

        self._trigger_rows: list[Adw.ActionRow] = []

        # Rules group
        self._rules_group = Adw.PreferencesGroup(
            title=_("Highlight Rules"),
        )
        self._prefs_page.add(self._rules_group)

        add_row = Adw.ActionRow(
            title=_("➕ Add New Rule"),
        )
        add_row.set_activatable(True)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_rule_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[self.CSS_CLASS_SUGGESTED],
        )
        add_row.add_suffix(add_btn)
        add_row.set_activatable_widget(add_btn)
        self._rules_group.add(add_row)

    def _load_context_data(self) -> None:
        """Load context data into the form."""
        context = self._manager.get_context(self._context_name)
        if not context:
            return

        self._enable_row.handler_block_by_func(self._on_enable_toggled)
        self._enable_row.set_active(context.enabled)
        self._enable_row.handler_unblock_by_func(self._on_enable_toggled)

        self._use_global_row.handler_block_by_func(self._on_use_global_toggled)
        self._use_global_row.set_active(context.use_global_rules)
        self._use_global_row.handler_unblock_by_func(self._on_use_global_toggled)

        self._populate_triggers()
        self._populate_rules()

    def _populate_triggers(self) -> None:
        """Populate the triggers list."""
        for row in self._trigger_rows:
            self._triggers_group.remove(row)
        self._trigger_rows.clear()

        context = self._manager.get_context(self._context_name)
        if not context:
            return

        for trigger in context.triggers:
            row = self._create_trigger_row(trigger)
            self._triggers_group.add(row)
            self._trigger_rows.append(row)

    def _create_trigger_row(self, trigger: str) -> Adw.ActionRow:
        """Create an action row for a trigger with edit and delete buttons."""
        row = ManagedListRow(
            title=trigger,
            show_reorder=False,
            show_actions=True,
            show_toggle=False,
        )

        icon = icon_image("utilities-terminal-symbolic")
        icon.set_opacity(0.6)
        row.add_prefix(icon)

        row.connect("edit-clicked", self._on_edit_trigger_clicked, trigger)
        row.connect("delete-clicked", self._on_delete_trigger_clicked, trigger)

        context = self._manager.get_context(self._context_name)
        if context and len(context.triggers) <= 1:
            row.set_actions_sensitive(delete=False)
            row.set_delete_tooltip(_("Cannot remove the last trigger"))

        return row

    def _on_add_trigger_clicked(self, button: Gtk.Button) -> None:
        """Handle add trigger button click."""
        dialog = AddTriggerDialog(self, self._context_name)
        dialog.connect("trigger-added", self._on_trigger_added)
        dialog.present(self)

    def _on_trigger_added(self, dialog, trigger: str) -> None:
        """Handle new trigger added."""
        context = self._manager.get_context(self._context_name)
        if context and trigger not in context.triggers:
            context.triggers.append(trigger)
            self._manager.save_context_to_user(context)
            self._populate_triggers()
            self.emit("context-updated")

    def _on_edit_trigger_clicked(self, button: Gtk.Button, old_trigger: str) -> None:
        """Handle edit trigger button click."""
        dialog = AddTriggerDialog(self, self._context_name, old_trigger)
        dialog.connect("trigger-added", self._on_trigger_edited, old_trigger)
        dialog.present(self)

    def _on_trigger_edited(self, dialog, new_trigger: str, old_trigger: str) -> None:
        """Handle trigger edit."""
        context = self._manager.get_context(self._context_name)
        if context:
            if old_trigger in context.triggers:
                idx = context.triggers.index(old_trigger)
                context.triggers[idx] = new_trigger
            self._manager.save_context_to_user(context)
            self._populate_triggers()
            self.emit("context-updated")

    def _on_delete_trigger_clicked(self, button: Gtk.Button, trigger: str) -> None:
        """Handle delete trigger button click."""
        context = self._manager.get_context(self._context_name)
        if not context or len(context.triggers) <= 1:
            return

        def on_confirm() -> None:
            ctx = self._manager.get_context(self._context_name)
            if ctx and trigger in ctx.triggers:
                ctx.triggers.remove(trigger)
                self._manager.save_context_to_user(ctx)
                self._populate_triggers()
                self.emit("context-updated")

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Remove Trigger?"),
            body=_('Remove "{}" from the triggers list?').format(trigger),
            on_confirm=on_confirm,
            delete_label=_("Remove"),
        )

    def _populate_rules(self) -> None:
        """Populate the rules list."""
        for row in self._context_rule_rows:
            self._rules_group.remove(row)
        self._context_rule_rows.clear()

        context = self._manager.get_context(self._context_name)
        if not context:
            return

        for index, rule in enumerate(context.rules):
            row = self._create_rule_row(rule, index, len(context.rules))
            self._rules_group.add(row)
            self._context_rule_rows.append(row)

    def _create_rule_row(
        self, rule: HighlightRule, index: int, total: int
    ) -> Adw.ActionRow:
        """Create an action row for a rule with inline reorder, edit, delete, switch."""
        escaped_name = GLib.markup_escape_text(rule.name)

        row = ManagedListRow(
            title=f"#{index + 1} {escaped_name}",
            subtitle=GLib.markup_escape_text(get_rule_subtitle(rule)),
            show_reorder=True,
            show_actions=True,
            show_toggle=True,
            is_first=(index == 0),
            is_last=(index == total - 1),
        )

        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        if rule.colors and rule.colors[0]:
            hex_color = self._manager.resolve_color(rule.colors[0])
            self._apply_color_to_box(color_box, hex_color)
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)

        row.set_active(rule.enabled)

        row.connect("move-up-clicked", self._on_move_rule_up, index)
        row.connect("move-down-clicked", self._on_move_rule_down, index)
        row.connect("edit-clicked", self._on_edit_rule, index)
        row.connect("delete-clicked", self._on_delete_rule, index)
        row.connect("toggled", self._on_rule_toggle_managed, index)

        return row

    def _apply_color_to_box(self, box: Gtk.Box, hex_color: str) -> None:
        """Apply a color as background to a box widget."""
        css_provider = Gtk.CssProvider()
        css = f"""
        .rule-color-indicator {{
            background-color: {hex_color};
            border-radius: 50%;
            border: 1px solid alpha(currentColor, 0.3);
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))

        context = box.get_style_context()
        if hasattr(box, "_css_provider"):
            context.remove_provider(box._css_provider)

        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box.add_css_class("rule-color-indicator")
        box._css_provider = css_provider

    def _on_enable_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle enable toggle."""
        self._manager.set_context_enabled(self._context_name, switch.get_active())
        self._manager.save_config()
        self.emit("context-updated")

    def _on_use_global_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle use global rules toggle."""
        self._manager.set_context_use_global_rules(
            self._context_name, switch.get_active()
        )
        self._manager.save_config()
        self.emit("context-updated")

    def _on_reset_clicked(self, button: Gtk.Button) -> None:
        """Handle reset button click."""

        def on_confirm() -> None:
            if self._manager.delete_user_context(self._context_name):
                self._load_context_data()
                self.emit("context-updated")

        show_delete_confirmation_dialog(
            parent=self,
            heading=_("Reset to System Default?"),
            body=_(
                'This will remove your customizations for "{}" and revert to system rules.'
            ).format(self._context_name),
            on_confirm=on_confirm,
            delete_label=_("Reset"),
        )

    def _on_add_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule button click."""
        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_rule_saved)
        dialog.present()

    def _on_rule_saved(self, dialog: RuleEditDialog, rule: HighlightRule) -> None:
        """Handle new rule saved."""
        self._manager.add_rule_to_context(self._context_name, rule)
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")

    def _on_rule_toggle_managed(self, row, active: bool, index: int) -> None:
        """Handle rule enable/disable toggle from ManagedListRow."""
        self._manager.set_context_rule_enabled(self._context_name, index, active)
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self.emit("context-updated")

    def _on_rule_toggle(self, switch: Gtk.Switch, _pspec, index: int) -> None:
        """Handle rule enable/disable toggle."""
        self._manager.set_context_rule_enabled(
            self._context_name, index, switch.get_active()
        )
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self.emit("context-updated")

    def _on_move_rule_up(self, button: Gtk.Button, index: int) -> None:
        """Move a rule up."""
        if index <= 0:
            return
        self._manager.move_context_rule(self._context_name, index, index - 1)
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")

    def _on_move_rule_down(self, button: Gtk.Button, index: int) -> None:
        """Move a rule down."""
        ctx = self._manager.get_context(self._context_name)
        if not ctx or index >= len(ctx.rules) - 1:
            return
        self._manager.move_context_rule(self._context_name, index, index + 1)
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")

    def _on_edit_rule(self, button: Gtk.Button, index: int) -> None:
        """Handle edit rule button click."""
        context = self._manager.get_context(self._context_name)
        if context and 0 <= index < len(context.rules):
            rule = context.rules[index]
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_rule_edited, index)
            dialog.present()

    def _on_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle rule edit saved."""
        self._manager.update_context_rule(self._context_name, index, rule)
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")

    def _on_delete_rule(self, button: Gtk.Button, index: int) -> None:
        """Handle delete rule button click."""
        context = self._manager.get_context(self._context_name)
        if not context or index >= len(context.rules):
            return

        rule = context.rules[index]

        def on_confirm() -> None:
            self._manager.remove_context_rule(self._context_name, index)
            ctx = self._manager.get_context(self._context_name)
            if ctx:
                self._manager.save_context_to_user(ctx)
            self._populate_rules()
            self.emit("context-updated")

        show_delete_confirmation_dialog(
            parent=self,
            heading=BaseDialog.MSG_DELETE_RULE_HEADING,
            body=BaseDialog.MSG_DELETE_CONFIRMATION.format(rule.name),
            on_confirm=on_confirm,
        )
