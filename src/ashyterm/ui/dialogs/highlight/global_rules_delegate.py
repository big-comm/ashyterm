"""Global highlight rules delegate for HighlightDialog."""

from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk

from ....settings.highlights import HighlightRule
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ..base_dialog import (
    BaseDialog,
    create_icon_button,
    show_delete_confirmation_dialog,
)
from ._constants import get_rule_subtitle
from .rule_edit_dialog import RuleEditDialog

if TYPE_CHECKING:
    from .highlight_dialog import HighlightDialog


class GlobalRulesDelegate:
    """Manages the global highlight rules list and CRUD."""

    def __init__(self, dialog: "HighlightDialog") -> None:
        self.dlg = dialog

    def setup_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the global rules list group."""
        self.dlg._rules_group = Adw.PreferencesGroup(
            title=_("Global Highlight Rules"),
        )
        page.add(self.dlg._rules_group)

        add_row = Adw.ActionRow(
            title=_("➕ Add New Global Rule"),
        )
        add_row.set_activatable(True)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_rule_clicked,
            valign=Gtk.Align.CENTER,
            css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
        )
        add_row.add_suffix(add_btn)
        add_row.set_activatable_widget(add_btn)
        self.dlg._rules_group.add(add_row)

        reset_row = Adw.ActionRow(
            title=_("Reset Global Rules"),
        )
        reset_row.set_activatable(True)
        reset_btn = create_icon_button(
            "view-refresh-symbolic",
            on_clicked=self._on_reset_global_rules_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        reset_row.add_suffix(reset_btn)
        reset_row.set_activatable_widget(reset_btn)
        self.dlg._rules_group.add(reset_row)

    def populate_rules(self) -> None:
        """Populate the global rules list from the manager."""
        for row in self.dlg._rule_rows:
            self.dlg._rules_group.remove(row)
        self.dlg._rule_rows.clear()

        for index, rule in enumerate(self.dlg._manager.rules):
            row = self._create_rule_row(rule, index)
            self.dlg._rules_group.add(row)
            self.dlg._rule_rows.append(row)

    def _create_rule_row(
        self, rule: HighlightRule, index: int
    ) -> Adw.ActionRow:
        """Create an action row for a highlight rule with inline edit/delete icons."""
        escaped_name = GLib.markup_escape_text(rule.name)
        row = Adw.ActionRow()
        row.set_title(escaped_name)
        row.set_subtitle(GLib.markup_escape_text(get_rule_subtitle(rule)))

        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        self.apply_color_to_box(
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

        edit_btn = create_icon_button(
            "document-edit-symbolic",
            tooltip=_("Edit rule"),
            on_clicked=self._on_edit_rule_clicked,
            callback_args=(index,),
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        row.add_suffix(edit_btn)

        delete_btn = create_icon_button(
            "user-trash-symbolic",
            tooltip=_("Delete rule"),
            on_clicked=self._on_delete_rule_clicked,
            callback_args=(index,),
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        row.add_suffix(delete_btn)

        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect(
            BaseDialog.SIGNAL_NOTIFY_ACTIVE,
            self._on_rule_switch_toggled,
            index,
        )
        row.add_suffix(switch)

        row._rule_index = index
        row._rule_switch = switch
        row._color_box = color_box

        return row

    @staticmethod
    def apply_color_to_box(box: Gtk.Box, hex_color: str) -> None:
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

        context.add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        box.add_css_class("rule-color-indicator")
        box._css_provider = css_provider

    def _get_rule_color_display(self, rule: HighlightRule) -> str:
        """Get the first color from a rule for display."""
        if rule.colors and rule.colors[0]:
            return self.dlg._manager.resolve_color(rule.colors[0])
        return "#ffffff"

    # -- event handlers -------------------------------------------------------

    def _on_rule_switch_toggled(
        self, switch: Gtk.Switch, _pspec, index: int
    ) -> None:
        """Handle rule enable/disable toggle."""
        self.dlg._manager.set_rule_enabled(index, switch.get_active())
        self.dlg._manager.save_global_rules_to_user()
        self.dlg._manager.save_config()
        self.dlg.emit("settings-changed")

    def _on_add_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule button click."""
        dialog = RuleEditDialog(self.dlg, is_new=True)
        dialog.connect("rule-saved", self._on_new_rule_saved)
        dialog.present()

    def _on_new_rule_saved(
        self, dialog: RuleEditDialog, rule: HighlightRule
    ) -> None:
        """Handle saving a new rule."""
        self.dlg._manager.add_rule(rule)
        self.dlg._manager.save_global_rules_to_user()
        self.dlg._manager.save_config()
        self.populate_rules()
        self.dlg.emit("settings-changed")
        self.dlg.add_toast(
            Adw.Toast(title=_("Rule added: {}").format(rule.name))
        )

    def _on_edit_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle edit rule button click."""
        rule = self.dlg._manager.get_rule(index)
        if rule:
            dialog = RuleEditDialog(self.dlg, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_rule_edited, index)
            dialog.present()

    def _on_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle saving an edited rule."""
        self.dlg._manager.update_rule(index, rule)
        self.dlg._manager.save_global_rules_to_user()
        self.dlg._manager.save_config()
        self.populate_rules()
        self.dlg.emit("settings-changed")
        self.dlg.add_toast(
            Adw.Toast(title=_("Rule updated: {}").format(rule.name))
        )

    def _on_delete_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle delete rule button click."""
        rule = self.dlg._manager.get_rule(index)
        if not rule:
            return

        rule_name = rule.name

        def on_confirm() -> None:
            self.dlg._manager.remove_rule(index)
            self.dlg._manager.save_global_rules_to_user()
            self.dlg._manager.save_config()
            self.populate_rules()
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

    def _on_reset_global_rules_clicked(self, button: Gtk.Button) -> None:
        """Handle reset global rules button click."""

        def on_confirm() -> None:
            self.dlg._manager.reset_global_rules()
            self.dlg._manager.save_config()
            self.populate_rules()
            self.dlg.emit("settings-changed")
            self.dlg.add_toast(
                Adw.Toast(title=_("Global rules reset to defaults"))
            )

        show_delete_confirmation_dialog(
            parent=self.dlg,
            heading=_("Reset Global Rules?"),
            body=_(
                "This will restore global rules to system defaults. Context customizations will be preserved."
            ),
            on_confirm=on_confirm,
            delete_label=_("Reset"),
        )
