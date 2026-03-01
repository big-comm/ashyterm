"""RuleEditDialog for creating or editing a highlight rule."""

import re
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ....settings.highlights import HighlightRule, get_highlight_manager
from ....settings.manager import get_settings_manager
from ....utils.translation_utils import _
from ...widgets.regex_text_view import RegexTextView
from ..base_dialog import BaseDialog, create_icon_button
from .color_entry_row import ColorEntryRow


class RuleEditDialog(BaseDialog):
    """
    Dialog for creating or editing a highlight rule.

    Provides form fields for rule name, regex pattern, and multi-group
    color selection with theme-aware logical color names.

    Inherits from BaseDialog for consistent UI and reduced boilerplate.
    """

    __gsignals__ = {
        "rule-saved": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    _SIZE_KEY_WIDTH = "rule_edit_dialog_width"
    _SIZE_KEY_HEIGHT = "rule_edit_dialog_height"
    _DEFAULT_WIDTH = 850
    _DEFAULT_HEIGHT = 600

    def __init__(
        self,
        parent: Gtk.Widget,
        rule: Optional[HighlightRule] = None,
        is_new: bool = True,
    ):
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

        title = _("New Rule") if is_new else _("Edit Rule")
        super().__init__(
            parent_window,
            title,
            auto_setup_toolbar=True,
            default_width=saved_width,
            default_height=saved_height,
        )

        self._parent = parent
        self._rule = rule or HighlightRule(name="", pattern="", colors=["white"])
        self._is_new = is_new
        self._manager = get_highlight_manager()

        self._color_rows: list[ColorEntryRow] = []

        self.connect("close-request", self._on_close_request)

        self._setup_ui()
        self._load_rule_data()

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
        self._save_btn = Gtk.Button(label=_("Save"))
        self._save_btn.add_css_class(self.CSS_CLASS_SUGGESTED)
        self._save_btn.connect("clicked", self._on_save_clicked)
        self.add_header_button(self._save_btn, pack_start=False)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        self.set_body_content(content_box)

        # Name entry
        name_group = Adw.PreferencesGroup()
        self._name_row = Adw.EntryRow(title=_("Rule Name"))
        self._name_row.connect("changed", self._on_input_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        # Pattern entry with regex syntax highlighting
        pattern_group = Adw.PreferencesGroup(
            title=_("Pattern"),
            description=_(
                "Python regex syntax. Capture groups () can have individual colors."
            ),
        )

        pattern_action_row = Adw.ActionRow(title=_("Regex Pattern"))
        pattern_action_row.set_subtitle(_("Syntax highlighted"))

        pattern_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pattern_container.set_valign(Gtk.Align.CENTER)
        pattern_container.set_hexpand(True)

        self._pattern_text_view = RegexTextView(single_line=True)
        self._pattern_text_view.set_hexpand(True)
        self._pattern_text_view.set_size_request(300, 32)
        self._pattern_text_view.add_css_class("card")
        self._pattern_text_view.connect_changed(self._on_pattern_changed)

        pattern_frame = Gtk.Frame()
        pattern_frame.set_child(self._pattern_text_view)
        pattern_frame.set_hexpand(True)
        pattern_frame.add_css_class("view")
        pattern_container.append(pattern_frame)

        help_btn = create_icon_button(
            "help-about-symbolic",
            tooltip=_("Regex reference"),
            on_clicked=self._on_regex_help_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        pattern_container.append(help_btn)

        pattern_action_row.add_suffix(pattern_container)
        pattern_group.add(pattern_action_row)
        content_box.append(pattern_group)

        # Validation status
        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        self._validation_label.set_wrap(True)
        content_box.append(self._validation_label)

        # Colors group
        self._colors_group = Adw.PreferencesGroup(
            title=GLib.markup_escape_text(_("Colors & Effects")),
            description=_("First color applies to entire match if no groups."),
        )
        content_box.append(self._colors_group)

        add_color_row = Adw.ActionRow(title=_("Add Color"))
        add_color_row.set_activatable(True)
        add_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_add_color_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        add_color_row.add_suffix(add_btn)
        add_color_row.set_activatable_widget(add_btn)
        self._colors_group.add(add_color_row)
        self._add_color_row = add_color_row

        # Description entry
        desc_group = Adw.PreferencesGroup()
        self._desc_row = Adw.EntryRow(title=_("Description (optional)"))
        desc_group.add(self._desc_row)
        content_box.append(desc_group)

        self._apply_regex_textview_css()

    def _apply_regex_textview_css(self) -> None:
        """Regex textview CSS is loaded globally from components.css."""
        pass

    def _load_rule_data(self) -> None:
        """Load existing rule data into form fields."""
        self._name_row.set_text(self._rule.name)
        self._pattern_text_view.set_text(self._rule.pattern)
        self._desc_row.set_text(self._rule.description)

        colors = self._rule.colors or ["white"]
        for idx, color_name in enumerate(colors):
            self._add_color_row_widget(idx + 1, color_name or "white")

        self._validate_input()

    def _add_color_row_widget(self, group_index: int, color_name: str) -> None:
        """Add a color entry row to the colors group."""
        row = ColorEntryRow(group_index, color_name)
        row.connect("color-changed", self._on_color_changed)
        row.connect("remove-requested", self._on_remove_color, row)

        self._colors_group.remove(self._add_color_row)
        self._colors_group.add(row)
        self._colors_group.add(self._add_color_row)

        self._color_rows.append(row)

    def _on_add_color_clicked(self, button: Gtk.Button) -> None:
        """Handle add color button click."""
        group_index = len(self._color_rows) + 1
        self._add_color_row_widget(group_index, "white")

    def _on_remove_color(self, row: ColorEntryRow, target_row: ColorEntryRow) -> None:
        """Handle remove color button click."""
        if len(self._color_rows) <= 1:
            return

        self._colors_group.remove(target_row)
        self._color_rows.remove(target_row)

        for idx, current_row in enumerate(self._color_rows):
            current_row.set_title(_("Group {}").format(idx + 1))
            current_row._group_index = idx + 1

    def _on_color_changed(self, row: ColorEntryRow) -> None:
        """Handle color selection change."""
        pass

    def _on_input_changed(self, widget) -> None:
        """Handle input changes to validate form."""
        self._validate_input()

    def _on_pattern_changed(self, widget) -> None:
        """Handle pattern changes - update colors count suggestion."""
        self._validate_input()

        pattern = self._pattern_text_view.get_text().strip()
        if pattern:
            is_valid, _error = self._manager.validate_pattern(pattern)
            if is_valid:
                try:
                    compiled = re.compile(pattern)
                    num_groups = compiled.groups

                    if num_groups > 0:
                        desc = _(
                            "Pattern has {} capture group(s). Add colors for each group."
                        ).format(num_groups)
                        self._colors_group.set_description(desc)
                    else:
                        desc = _(
                            "Pattern has no capture groups. First color applies to entire match."
                        )
                        self._colors_group.set_description(desc)
                except Exception as e:
                    self.logger.debug(f"Capture group detection failed: {e}")

    def _validate_input(self) -> None:
        """Validate the current input and update UI accordingly."""
        name = self._name_row.get_text().strip()
        pattern = self._pattern_text_view.get_text().strip()

        if not name:
            self._validation_label.set_text(_("Rule name is required"))
            self._validation_label.add_css_class(self.CSS_CLASS_ERROR)
            self._save_btn.set_sensitive(False)
            return

        if not pattern:
            self._validation_label.set_text(_("Pattern is required"))
            self._validation_label.add_css_class(self.CSS_CLASS_ERROR)
            self._save_btn.set_sensitive(False)
            return

        is_valid, error_msg = self._manager.validate_pattern(pattern)

        if not is_valid:
            self._validation_label.set_text(_("Invalid regex: {}").format(error_msg))
            self._validation_label.add_css_class(self.CSS_CLASS_ERROR)
            self._validation_label.remove_css_class(self.CSS_CLASS_SUCCESS)
            self._save_btn.set_sensitive(False)
        else:
            self._validation_label.set_text(_("✓ Valid pattern"))
            self._validation_label.remove_css_class(self.CSS_CLASS_ERROR)
            self._validation_label.add_css_class(self.CSS_CLASS_SUCCESS)
            self._save_btn.set_sensitive(True)

    def _on_regex_help_clicked(self, button: Gtk.Button) -> None:
        """Show regex reference dialog."""
        dialog = Adw.Dialog()
        dialog.set_title(_("Regex Reference"))
        dialog.set_content_width(600)
        dialog.set_content_height(600)

        toolbar_view = Adw.ToolbarView()
        dialog.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        toolbar_view.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        scrolled.set_child(content)

        def add_group(title, items, subtitle_lines=1):
            group = Adw.PreferencesGroup(title=title)
            for pattern, desc in items:
                row = Adw.ActionRow(
                    title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc
                )
                row.set_title_lines(1)
                row.set_subtitle_lines(subtitle_lines)
                group.add(row)
            content.append(group)

        add_group(
            _("Basic Patterns"),
            [
                (".", _("Any character except newline")),
                ("\\d", _("Any digit [0-9]")),
                ("\\w", _("Word character [a-zA-Z0-9_]")),
                ("\\s", _("Whitespace character")),
                ("\\D, \\W, \\S", _("Negations of above")),
                ("^", _("Start of line")),
                ("$", _("End of line")),
                ("\\b", _("Word boundary")),
            ],
        )

        add_group(
            _("Quantifiers"),
            [
                ("*", _("0 or more times")),
                ("+", _("1 or more times")),
                ("?", _("0 or 1 time")),
                ("{n}", _("Exactly n times")),
                ("{n,m}", _("Between n and m times")),
                ("*?, +?, ??", _("Non-greedy versions")),
            ],
        )

        add_group(
            _("Groups & Alternatives"),
            [
                ("(abc)", _("Capture group (gets a color)")),
                ("(?:abc)", _("Non-capturing group")),
                ("a|b", _("Alternation (a or b)")),
                ("[abc]", _("Character class (a, b, or c)")),
                ("[^abc]", _("Negated class (not a, b, c)")),
                ("[a-z]", _("Character range")),
            ],
        )

        add_group(
            _("Examples"),
            [
                ("\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b", _("IPv4 address")),
                ("error|fail|fatal", _("Match error keywords")),
                ("(\\w+)=(\\w+)", _("Key=value pairs (2 groups)")),
                ("^\\s*#.*$", _("Comment lines")),
            ],
            subtitle_lines=1,
        )

        dialog.present(self)

    def _on_save_clicked(self, button: Gtk.Button) -> None:
        """Handle save button click."""
        name = self._name_row.get_text().strip()
        pattern = self._pattern_text_view.get_text().strip()
        description = self._desc_row.get_text().strip()

        colors: list[str | None] = [row.color_name for row in self._color_rows]
        if not colors:
            colors = ["white"]

        rule = HighlightRule(
            name=name,
            pattern=pattern,
            colors=colors,
            enabled=self._rule.enabled if not self._is_new else True,
            description=description,
        )

        self.emit("rule-saved", rule)
        self.close()
