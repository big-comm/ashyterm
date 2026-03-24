"""Context management delegate for HighlightDialog.

Handles context (command-specific) highlighting: context list, context rules,
context CRUD, and rule reordering.
"""

from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk

from ....settings.highlights import HighlightContext, HighlightRule
from ....utils.icons import icon_image
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ..base_dialog import (
    BaseDialog,
    create_icon_button,
    show_delete_confirmation_dialog,
)
from ._constants import get_rule_subtitle
from .context_rules_dialog import ContextRulesDialog
from .rule_edit_dialog import RuleEditDialog
from .small_dialogs import ContextNameDialog

if TYPE_CHECKING:
    from .highlight_dialog import HighlightDialog


class ContextDelegate:
    """Manages command-specific highlighting contexts and their rules."""

    def __init__(self, dialog: "HighlightDialog") -> None:
        self.dlg = dialog

    # -- settings group -------------------------------------------------------

    def setup_context_settings_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context-aware settings group."""
        context_settings_group = Adw.PreferencesGroup(
            title=_("Per-Command Highlighting"),
        )
        page.add(context_settings_group)

        self.dlg._context_aware_toggle = Adw.SwitchRow(
            title=_("Enable Command Detection"),
        )
        self.dlg._context_aware_toggle.set_active(
            self.dlg._manager.context_aware_enabled
        )
        self.dlg._context_aware_toggle.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_context_aware_toggled
        )
        context_settings_group.add(self.dlg._context_aware_toggle)

    # -- context selector group -----------------------------------------------

    def setup_context_selector_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context selector group with bulk actions."""
        self.dlg._context_selector_group = Adw.PreferencesGroup(
            title=_("Commands"),
        )
        page.add(self.dlg._context_selector_group)

        add_context_row = Adw.ActionRow(
            title=_("➕ Add Command"),
        )
        add_context_row.set_activatable(True)
        add_context_row.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_context_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
        )
        add_context_row.add_suffix(add_btn)
        add_context_row.set_activatable_widget(add_btn)
        self.dlg._add_context_row = add_context_row
        self.dlg._context_selector_group.add(add_context_row)

        bulk_actions_row = Adw.ActionRow(
            title=_("Bulk Actions"),
        )

        enable_all_btn = Gtk.Button(label=_("Enable All"))
        enable_all_btn.set_valign(Gtk.Align.CENTER)
        enable_all_btn.add_css_class(BaseDialog.CSS_CLASS_SUGGESTED)
        enable_all_btn.connect("clicked", self._on_enable_all_contexts)
        bulk_actions_row.add_suffix(enable_all_btn)

        disable_all_btn = Gtk.Button(label=_("Disable All"))
        disable_all_btn.set_valign(Gtk.Align.CENTER)
        disable_all_btn.add_css_class("destructive-action")
        disable_all_btn.connect("clicked", self._on_disable_all_contexts)
        bulk_actions_row.add_suffix(disable_all_btn)

        self.dlg._context_selector_group.add(bulk_actions_row)

        reset_contexts_row = Adw.ActionRow(
            title=_("Reset All Commands"),
        )
        reset_contexts_row.set_activatable(True)
        reset_contexts_btn = create_icon_button(
            "view-refresh-symbolic",
            on_clicked=self._on_reset_all_contexts_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        reset_contexts_row.add_suffix(reset_contexts_btn)
        reset_contexts_row.set_activatable_widget(reset_contexts_btn)
        self.dlg._context_selector_group.add(reset_contexts_row)

        self.dlg._context_list_group = Adw.PreferencesGroup(
            title=_("Available Commands"),
        )
        page.add(self.dlg._context_list_group)

        self.dlg._context_rows = {}

    # -- context rules group --------------------------------------------------

    def setup_context_rules_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context rules list group with reorder support."""
        self.dlg._context_rules_group = Adw.PreferencesGroup(
            title=_("Command Rules"),
            description=_(
                "Rules specific to the selected command. Order matters!"
            ),
        )
        page.add(self.dlg._context_rules_group)

        self.dlg._context_header_row = Adw.ActionRow(
            title=_("No command selected"),
            subtitle=_("Select a command from the list above"),
        )
        self.dlg._context_rules_group.add(self.dlg._context_header_row)

        self.dlg._context_enable_row = Adw.SwitchRow(
            title=_("Enable Command Rules"),
            subtitle=_("Apply rules when this command is detected"),
        )
        self.dlg._context_enable_row.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_context_enable_toggled
        )
        self.dlg._context_rules_group.add(self.dlg._context_enable_row)

        self.dlg._use_global_rules_row = Adw.SwitchRow(
            title=_("Include Global Rules"),
            subtitle=_(
                "Also apply global rules alongside command-specific rules"
            ),
        )
        self.dlg._use_global_rules_row.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE, self._on_use_global_rules_toggled
        )
        self.dlg._context_rules_group.add(self.dlg._use_global_rules_row)

        self.dlg._reset_context_row = Adw.ActionRow(
            title=_("Reset to System Default"),
            subtitle=_(
                "Remove user customization and revert to system rules"
            ),
        )
        self.dlg._reset_context_row.set_activatable(True)
        reset_btn = create_icon_button(
            "edit-undo-symbolic",
            on_clicked=self._on_reset_context_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        self.dlg._reset_context_row.add_suffix(reset_btn)
        self.dlg._reset_context_row.set_activatable_widget(reset_btn)
        self.dlg._reset_context_row.set_sensitive(False)
        self.dlg._context_rules_group.add(self.dlg._reset_context_row)

        self.dlg._context_rules_list_group = Adw.PreferencesGroup(
            title=_("Rules (in execution order)"),
            description=_(
                "Use arrows to reorder rules. Rules are matched from top to bottom."
            ),
        )
        page.add(self.dlg._context_rules_list_group)

        add_rule_row = Adw.ActionRow(
            title=_("➕ Add Rule to This Command"),
            subtitle=_(
                "Create a new highlighting pattern for the selected command"
            ),
        )
        add_rule_row.set_activatable(True)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_context_rule_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
        )
        add_rule_row.add_suffix(add_btn)
        add_rule_row.set_activatable_widget(add_btn)
        self.dlg._add_context_rule_row = add_rule_row
        self.dlg._context_rules_list_group.add(add_rule_row)

    # -- populate methods -----------------------------------------------------

    def populate_contexts(self) -> None:
        """Populate the context list with toggle rows for each context."""
        for row in self.dlg._context_rows.values():
            self.dlg._context_list_group.remove(row)
        self.dlg._context_rows.clear()

        context_names = sorted(self.dlg._manager.get_context_names())

        enabled_count = sum(
            1
            for name in context_names
            if (ctx := self.dlg._manager.get_context(name)) is not None
            and ctx.enabled
        )

        self.dlg._context_selector_group.set_description(
            _("{total} command(s), {enabled} enabled").format(
                total=len(context_names), enabled=enabled_count
            )
        )

        for name in context_names:
            ctx = self.dlg._manager.get_context(name)
            if not ctx:
                continue

            row = self._create_context_list_row(name, ctx)
            self.dlg._context_rows[name] = row
            self.dlg._context_list_group.add(row)

    def populate_context_rules(self) -> None:
        """Populate rules for the selected context."""
        for row in self.dlg._context_rule_rows:
            self.dlg._context_rules_list_group.remove(row)
        self.dlg._context_rule_rows.clear()

        if not self.dlg._selected_context:
            self.dlg._context_enable_row.set_sensitive(False)
            self.dlg._use_global_rules_row.set_sensitive(False)
            self.dlg._add_context_rule_row.set_sensitive(False)
            self.dlg._reset_context_row.set_sensitive(False)
            self.dlg._context_header_row.set_title(_("No command selected"))
            self.dlg._context_header_row.set_subtitle(
                _("Select a command from the list above")
            )
            self.dlg._context_rules_list_group.set_description(
                _("Select a command to view its rules")
            )
            return

        self.dlg._context_enable_row.set_sensitive(True)
        self.dlg._use_global_rules_row.set_sensitive(True)
        self.dlg._add_context_rule_row.set_sensitive(True)
        self.dlg._reset_context_row.set_sensitive(True)

        context = self.dlg._manager.get_context(self.dlg._selected_context)
        if not context:
            self.dlg._context_header_row.set_title(self.dlg._selected_context)
            self.dlg._context_header_row.set_subtitle(_("Command not found"))
            return

        trigger_info = ", ".join(context.triggers[:3])
        if len(context.triggers) > 3:
            trigger_info += "..."
        self.dlg._context_header_row.set_title(self.dlg._selected_context)
        self.dlg._context_header_row.set_subtitle(
            _("Triggers: {triggers}").format(triggers=trigger_info)
        )

        status = _("Enabled") if context.enabled else _("Disabled")
        rule_count = len(context.rules)
        self.dlg._context_rules_list_group.set_description(
            _("{count} rule(s) • {status} • Use arrows to reorder").format(
                count=rule_count, status=status
            )
        )

        self.dlg._context_enable_row.handler_block_by_func(
            self._on_context_enable_toggled
        )
        self.dlg._context_enable_row.set_active(context.enabled)
        self.dlg._context_enable_row.handler_unblock_by_func(
            self._on_context_enable_toggled
        )

        self.dlg._use_global_rules_row.handler_block_by_func(
            self._on_use_global_rules_toggled
        )
        self.dlg._use_global_rules_row.set_active(context.use_global_rules)
        self.dlg._use_global_rules_row.handler_unblock_by_func(
            self._on_use_global_rules_toggled
        )

        for index, rule in enumerate(context.rules):
            row = self._create_context_rule_row(
                rule, index, len(context.rules)
            )
            self.dlg._context_rules_list_group.add(row)
            self.dlg._context_rule_rows.append(row)

    # -- row builders ---------------------------------------------------------

    def _create_context_list_row(self, name: str, ctx) -> Adw.ActionRow:
        """Create a row for a context in the list."""
        rule_count = len(ctx.rules)
        trigger_info = ", ".join(ctx.triggers)
        row = Adw.ActionRow(
            title=trigger_info,
            subtitle=_("{count} rules").format(count=rule_count),
        )
        row.set_activatable(False)

        icon = icon_image("utilities-terminal-symbolic")
        icon.set_opacity(0.6)
        row.add_prefix(icon)

        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(edit_btn, _("Edit command rules"))
        edit_btn.connect("clicked", self._on_edit_context_clicked, name)
        row.add_suffix(edit_btn)

        if self.dlg._manager.has_user_context_override(name):
            delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
            delete_btn.add_css_class("flat")
            delete_btn.set_valign(Gtk.Align.CENTER)
            get_tooltip_helper().add_tooltip(delete_btn, _("Delete command"))
            delete_btn.connect(
                "clicked", self._on_delete_context_clicked, name
            )
            row.add_suffix(delete_btn)

        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(ctx.enabled)
        switch.connect("state-set", self._on_context_toggle, name)
        row.add_suffix(switch)

        row._context_switch = switch
        return row

    def _create_context_rule_row(
        self, rule: HighlightRule, index: int, total_rules: int = 0
    ) -> Adw.ExpanderRow:
        """Create an expander row for a context-specific rule with reorder buttons."""
        escaped_name = GLib.markup_escape_text(rule.name)
        row = Adw.ExpanderRow()
        row.set_title(f"#{index + 1} {escaped_name}")
        row.set_subtitle(GLib.markup_escape_text(get_rule_subtitle(rule)))

        reorder_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        reorder_box.set_valign(Gtk.Align.CENTER)
        reorder_box.set_margin_end(4)

        up_btn = Gtk.Button(icon_name="go-up-symbolic")
        up_btn.add_css_class("flat")
        up_btn.add_css_class("circular")
        up_btn.set_size_request(24, 24)
        up_btn.set_sensitive(index > 0)
        up_btn.connect("clicked", self._on_move_rule_up, index)
        get_tooltip_helper().add_tooltip(up_btn, _("Move up"))
        reorder_box.append(up_btn)

        down_btn = Gtk.Button(icon_name="go-down-symbolic")
        down_btn.add_css_class("flat")
        down_btn.add_css_class("circular")
        down_btn.set_size_request(24, 24)
        down_btn.set_sensitive(index < total_rules - 1)
        down_btn.connect("clicked", self._on_move_rule_down, index)
        get_tooltip_helper().add_tooltip(down_btn, _("Move down"))
        reorder_box.append(down_btn)

        row.add_prefix(reorder_box)

        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        from .global_rules_delegate import GlobalRulesDelegate

        GlobalRulesDelegate.apply_color_to_box(
            color_box, self._get_rule_color_display(rule)
        )
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)

        if rule.colors and len(rule.colors) > 1:
            colors_badge = Gtk.Label(label=f"{len(rule.colors)}")
            colors_badge.add_css_class("dim-label")
            colors_badge.set_valign(Gtk.Align.CENTER)
            get_tooltip_helper().add_tooltip(
                colors_badge, _("{} colors").format(len(rule.colors))
            )
            row.add_suffix(colors_badge)

        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE,
            self._on_context_rule_switch_toggled,
            index,
        )
        row.add_suffix(switch)

        actions_row = Adw.ActionRow(title=_("Actions"))

        edit_btn = Gtk.Button(label=_("Edit"))
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.connect(
            "clicked", self._on_edit_context_rule_clicked, index
        )
        actions_row.add_suffix(edit_btn)

        delete_btn = Gtk.Button(label=_("Delete"))
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect(
            "clicked", self._on_delete_context_rule_clicked, index
        )
        actions_row.add_suffix(delete_btn)

        row.add_row(actions_row)
        return row

    def _get_rule_color_display(self, rule: HighlightRule) -> str:
        """Get the first color from a rule for display."""
        if rule.colors and rule.colors[0]:
            return self.dlg._manager.resolve_color(rule.colors[0])
        return "#ffffff"

    # -- context selection ----------------------------------------------------

    def select_context(self, context_name: str) -> None:
        """Select a context for editing."""
        self.dlg._selected_context = context_name

        for name, row in self.dlg._context_rows.items():
            if name == context_name:
                row.add_css_class("accent")
            else:
                row.remove_css_class("accent")

        self.dlg._reset_context_row.set_sensitive(
            bool(self.dlg._selected_context)
        )
        self.populate_context_rules()

    # -- event handlers -------------------------------------------------------

    def _on_context_aware_toggled(
        self, switch: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle context-aware toggle."""
        self.dlg._manager.context_aware_enabled = switch.get_active()
        self.dlg._manager.save_config()
        self.dlg.emit("settings-changed")

    def _on_context_toggle(
        self, switch: Gtk.Switch, state: bool, context_name: str
    ) -> bool:
        """Handle context toggle from the list."""
        self.dlg._manager.set_context_enabled(context_name, state)
        self.dlg._manager.save_config()

        context_names = self.dlg._manager.get_context_names()
        enabled_count = sum(
            1
            for name in context_names
            if (ctx := self.dlg._manager.get_context(name)) is not None
            and ctx.enabled
        )
        self.dlg._context_selector_group.set_description(
            _("{total} command(s), {enabled} enabled").format(
                total=len(context_names), enabled=enabled_count
            )
        )

        if context_name == self.dlg._selected_context:
            self.dlg._context_enable_row.handler_block_by_func(
                self._on_context_enable_toggled
            )
            self.dlg._context_enable_row.set_active(state)
            self.dlg._context_enable_row.handler_unblock_by_func(
                self._on_context_enable_toggled
            )

        self.dlg.emit("settings-changed")
        return False

    def _on_edit_context_clicked(
        self, button: Gtk.Button, context_name: str
    ) -> None:
        """Handle edit context button click."""
        self._open_context_dialog(context_name)

    def _on_delete_context_clicked(
        self, button: Gtk.Button, context_name: str
    ) -> None:
        """Handle delete context button click."""
        ctx = self.dlg._manager.get_context(context_name)
        if not ctx or not self.dlg._manager.has_user_context_override(
            context_name
        ):
            return

        def on_confirm() -> None:
            if self.dlg._manager.delete_user_context(context_name):
                self.populate_contexts()
                self.dlg.emit("settings-changed")
                self.dlg.add_toast(
                    Adw.Toast(
                        title=_("Command deleted: {}").format(context_name)
                    )
                )

        show_delete_confirmation_dialog(
            parent=self.dlg,
            heading=_("Delete Command?"),
            body=_(
                'Are you sure you want to delete "{}"? This will remove all custom rules for this command.'
            ).format(context_name),
            on_confirm=on_confirm,
        )

    def _open_context_dialog(self, context_name: str) -> None:
        """Open the context rules dialog for a specific context."""
        dialog = ContextRulesDialog(self.dlg, context_name)
        dialog.connect("context-updated", self._on_context_dialog_updated)
        dialog.present()

    def _on_context_dialog_updated(self, dialog) -> None:
        """Handle updates from the context rules dialog."""
        self.populate_contexts()
        self.dlg.emit("settings-changed")

    def _on_enable_all_contexts(self, button: Gtk.Button) -> None:
        """Enable all contexts."""
        for name in self.dlg._manager.get_context_names():
            self.dlg._manager.set_context_enabled(name, True)
        self.dlg._manager.save_config()
        self.populate_contexts()
        self.dlg.emit("settings-changed")

    def _on_disable_all_contexts(self, button: Gtk.Button) -> None:
        """Disable all contexts."""
        for name in self.dlg._manager.get_context_names():
            self.dlg._manager.set_context_enabled(name, False)
        self.dlg._manager.save_config()
        self.populate_contexts()
        self.dlg.emit("settings-changed")

    def _on_context_enable_toggled(
        self, switch: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle context enable toggle from detail view."""
        if self.dlg._selected_context:
            state = switch.get_active()
            self.dlg._manager.set_context_enabled(
                self.dlg._selected_context, state
            )
            self.dlg._manager.save_config()

            if self.dlg._selected_context in self.dlg._context_rows:
                row = self.dlg._context_rows[self.dlg._selected_context]
                for child in row:
                    if isinstance(child, Gtk.Switch):
                        child.handler_block_by_func(self._on_context_toggle)
                        child.set_active(state)
                        child.handler_unblock_by_func(self._on_context_toggle)
                        break

            context_names = self.dlg._manager.get_context_names()
            enabled_count = sum(
                1
                for name in context_names
                if (ctx := self.dlg._manager.get_context(name)) is not None
                and ctx.enabled
            )
            self.dlg._context_selector_group.set_description(
                _(
                    "{total} command(s), {enabled} enabled. Click to toggle, select to edit."
                ).format(total=len(context_names), enabled=enabled_count)
            )

            self.dlg.emit("settings-changed")

    def _on_use_global_rules_toggled(
        self, switch: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle use global rules toggle."""
        if self.dlg._selected_context:
            self.dlg._manager.set_context_use_global_rules(
                self.dlg._selected_context, switch.get_active()
            )
            self.dlg._manager.save_config()
            self.dlg.emit("settings-changed")

    def _on_context_rule_switch_toggled(
        self, switch: Gtk.Switch, _pspec, index: int
    ) -> None:
        """Handle context rule enable/disable toggle."""
        if self.dlg._selected_context:
            self.dlg._manager.set_context_rule_enabled(
                self.dlg._selected_context, index, switch.get_active()
            )
            self.dlg._manager.save_config()
            self.dlg.emit("settings-changed")

    def _on_move_rule_up(self, button: Gtk.Button, index: int) -> None:
        """Move a rule up in the order."""
        if not self.dlg._selected_context or index <= 0:
            return
        self.dlg._manager.move_context_rule(
            self.dlg._selected_context, index, index - 1
        )
        self.dlg._manager.save_config()
        self.populate_context_rules()
        self.dlg.emit("settings-changed")

    def _on_move_rule_down(self, button: Gtk.Button, index: int) -> None:
        """Move a rule down in the order."""
        if not self.dlg._selected_context:
            return
        ctx = self.dlg._manager.get_context(self.dlg._selected_context)
        if not ctx or index >= len(ctx.rules) - 1:
            return
        self.dlg._manager.move_context_rule(
            self.dlg._selected_context, index, index + 1
        )
        self.dlg._manager.save_config()
        self.populate_context_rules()
        self.dlg.emit("settings-changed")

    def _on_add_context_clicked(self, button: Gtk.Button) -> None:
        """Handle add context button click."""
        dialog = ContextNameDialog(self.dlg)
        dialog.connect("context-created", self._on_context_created)
        dialog.present(self.dlg)

    def _on_context_created(self, dialog, context_name: str) -> None:
        """Handle new context creation."""
        context = HighlightContext(
            command_name=context_name,
            triggers=[context_name],
            rules=[],
            enabled=True,
            description=f"Custom rules for {context_name}",
        )
        self.dlg._manager.add_context(context)
        self.dlg._manager.save_context_to_user(context)
        self.populate_contexts()

        self.dlg.emit("settings-changed")
        self.dlg.add_toast(
            Adw.Toast(title=_("Command created: {}").format(context_name))
        )

        self._open_context_dialog(context_name)

    def _on_reset_context_clicked(self, button: Gtk.Button) -> None:
        """Handle reset context to system default button click."""
        if not self.dlg._selected_context:
            return

        context_name = self.dlg._selected_context

        def on_confirm() -> None:
            if self.dlg._manager.delete_user_context(context_name):
                self.populate_contexts()
                self.dlg.emit("settings-changed")
                self.dlg.add_toast(
                    Adw.Toast(
                        title=_("Command reset: {}").format(context_name)
                    )
                )
            else:
                self.dlg.add_toast(
                    Adw.Toast(title=_("No user customization to reset"))
                )

        show_delete_confirmation_dialog(
            parent=self.dlg,
            heading=_("Reset to System Default?"),
            body=_(
                'This will remove your customizations for "{}" and revert to system rules.'
            ).format(context_name),
            on_confirm=on_confirm,
            delete_label=_("Reset"),
        )

    def _on_add_context_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule to context button click."""
        if not self.dlg._selected_context:
            return

        dialog = RuleEditDialog(self.dlg, is_new=True)
        dialog.connect("rule-saved", self._on_context_rule_saved)
        dialog.present()

    def _on_context_rule_saved(
        self, dialog: RuleEditDialog, rule: HighlightRule
    ) -> None:
        """Handle saving a new context rule."""
        if self.dlg._selected_context:
            self.dlg._manager.add_rule_to_context(
                self.dlg._selected_context, rule
            )
            context = self.dlg._manager.get_context(
                self.dlg._selected_context
            )
            if context:
                self.dlg._manager.save_context_to_user(context)
            self.populate_context_rules()
            self.dlg.emit("settings-changed")
            self.dlg.add_toast(
                Adw.Toast(title=_("Rule added: {}").format(rule.name))
            )

    def _on_edit_context_rule_clicked(
        self, button: Gtk.Button, index: int
    ) -> None:
        """Handle edit context rule button click."""
        if not self.dlg._selected_context:
            return

        context = self.dlg._manager.get_context(self.dlg._selected_context)
        if context and 0 <= index < len(context.rules):
            rule = context.rules[index]
            dialog = RuleEditDialog(self.dlg, rule=rule, is_new=False)
            dialog.connect(
                "rule-saved", self._on_context_rule_edited, index
            )
            dialog.present()

    def _on_context_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle saving an edited context rule."""
        if self.dlg._selected_context:
            self.dlg._manager.update_context_rule(
                self.dlg._selected_context, index, rule
            )
            context = self.dlg._manager.get_context(
                self.dlg._selected_context
            )
            if context:
                self.dlg._manager.save_context_to_user(context)
            self.populate_context_rules()
            self.dlg.emit("settings-changed")
            self.dlg.add_toast(
                Adw.Toast(title=_("Rule updated: {}").format(rule.name))
            )

    def _on_delete_context_rule_clicked(
        self, button: Gtk.Button, index: int
    ) -> None:
        """Handle delete context rule button click."""
        if not self.dlg._selected_context:
            return

        context = self.dlg._manager.get_context(self.dlg._selected_context)
        if not context or index >= len(context.rules):
            return

        rule = context.rules[index]
        rule_name = rule.name
        selected_ctx = self.dlg._selected_context

        def on_confirm() -> None:
            self.dlg._manager.remove_context_rule(selected_ctx, index)
            ctx = self.dlg._manager.get_context(selected_ctx)
            if ctx:
                self.dlg._manager.save_context_to_user(ctx)
            self.populate_context_rules()
            self.dlg.emit("settings-changed")
            self.dlg.add_toast(
                Adw.Toast(title=_("Rule deleted: {}").format(rule_name))
            )

        show_delete_confirmation_dialog(
            parent=self.dlg,
            heading=BaseDialog.MSG_DELETE_RULE_HEADING,
            body=BaseDialog.MSG_DELETE_CONFIRMATION.format(rule_name),
            on_confirm=on_confirm,
        )

    def _on_reset_all_contexts_clicked(self, button: Gtk.Button) -> None:
        """Handle reset all contexts button click."""

        def on_confirm() -> None:
            self.dlg._manager.reset_all_contexts()
            self.dlg._manager.save_config()
            self.populate_contexts()
            self.dlg.emit("settings-changed")
            self.dlg.add_toast(
                Adw.Toast(title=_("All commands reset to defaults"))
            )

        show_delete_confirmation_dialog(
            parent=self.dlg,
            heading=_("Reset All Commands?"),
            body=_(
                "This will restore all commands to system defaults. Global rules will be preserved."
            ),
            on_confirm=on_confirm,
            delete_label=_("Reset"),
        )
