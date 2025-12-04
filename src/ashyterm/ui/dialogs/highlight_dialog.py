# ashyterm/ui/dialogs/highlight_dialog.py
"""
Highlight Colors Dialog for managing syntax highlighting rules.

This dialog provides a GNOME HIG-compliant interface for configuring
regex-based coloring rules that are applied to terminal output text.

Supports:
- Multi-group regex coloring (colors list for capture groups)
- Theme-aware logical color names
- Context-aware highlighting with command-specific rule sets
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from ...settings.highlights import (
    HighlightContext,
    HighlightRule,
    get_highlight_manager,
)
from ...settings.manager import get_settings_manager
from ...utils.logger import get_logger
from ...utils.translation_utils import _

# Available logical color names for selection
LOGICAL_COLOR_OPTIONS = [
    # Standard ANSI
    ("black", _("Black")),
    ("red", _("Red")),
    ("green", _("Green")),
    ("yellow", _("Yellow")),
    ("blue", _("Blue")),
    ("magenta", _("Magenta")),
    ("cyan", _("Cyan")),
    ("white", _("White")),
    # Bright variants
    ("bright_black", _("Bright Black")),
    ("bright_red", _("Bright Red")),
    ("bright_green", _("Bright Green")),
    ("bright_yellow", _("Bright Yellow")),
    ("bright_blue", _("Bright Blue")),
    ("bright_magenta", _("Bright Magenta")),
    ("bright_cyan", _("Bright Cyan")),
    ("bright_white", _("Bright White")),
    # Theme colors
    ("foreground", _("Foreground")),
    # Modifiers (bold variants)
    ("bold red", _("Bold Red")),
    ("bold green", _("Bold Green")),
    ("bold yellow", _("Bold Yellow")),
    ("bold blue", _("Bold Blue")),
    ("bold cyan", _("Bold Cyan")),
    ("bold white", _("Bold White")),
]


class ColorEntryRow(Adw.ActionRow):
    """
    A row for editing a single color in the colors list.
    
    Provides a dropdown to select a logical color name.
    """
    
    __gsignals__ = {
        "color-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "remove-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    
    def __init__(self, group_index: int, color_name: str = "white"):
        """
        Initialize the color entry row.
        
        Args:
            group_index: The capture group index (1-based for display)
            color_name: Initial logical color name
        """
        super().__init__()
        self._group_index = group_index
        self._color_name = color_name or "white"
        
        self.set_title(_("Group {}").format(group_index))
        self.set_subtitle(_("Color for capture group {}").format(group_index))
        
        self._setup_ui()
        self._load_color()
    
    def _setup_ui(self) -> None:
        """Setup the row UI components."""
        # Color dropdown
        self._color_dropdown = Gtk.DropDown()
        self._color_model = Gtk.StringList()
        
        # Add color options
        for color_id, color_label in LOGICAL_COLOR_OPTIONS:
            self._color_model.append(color_label)
        
        self._color_dropdown.set_model(self._color_model)
        self._color_dropdown.set_valign(Gtk.Align.CENTER)
        self._color_dropdown.connect("notify::selected", self._on_color_selected)
        self.add_suffix(self._color_dropdown)
        
        # Remove button
        remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.set_tooltip_text(_("Remove"))
        remove_btn.connect("clicked", lambda b: self.emit("remove-requested"))
        self.add_suffix(remove_btn)
        
        # Color preview box
        self._color_box = Gtk.Box()
        self._color_box.set_size_request(24, 24)
        self._color_box.set_valign(Gtk.Align.CENTER)
        self._color_box.add_css_class("circular")
        self.add_prefix(self._color_box)
    
    def _load_color(self) -> None:
        """Load the initial color into the dropdown."""
        # Find the color in options
        color_lower = self._color_name.lower()
        for idx, (color_id, _) in enumerate(LOGICAL_COLOR_OPTIONS):
            if color_id == color_lower:
                self._color_dropdown.set_selected(idx)
                break
        
        self._update_color_preview()
    
    def _on_color_selected(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Handle color selection change."""
        idx = dropdown.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and idx < len(LOGICAL_COLOR_OPTIONS):
            self._color_name = LOGICAL_COLOR_OPTIONS[idx][0]
            self._update_color_preview()
            self.emit("color-changed")
    
    def _update_color_preview(self) -> None:
        """Update the color preview box."""
        manager = get_highlight_manager()
        hex_color = manager.resolve_color(self._color_name)
        
        css_provider = Gtk.CssProvider()
        css = f"""
        .color-preview {{
            background-color: {hex_color};
            border-radius: 50%;
            border: 1px solid alpha(currentColor, 0.3);
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))
        
        context = self._color_box.get_style_context()
        if hasattr(self, "_css_provider"):
            context.remove_provider(self._css_provider)
        
        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._color_box.add_css_class("color-preview")
        self._css_provider = css_provider
    
    @property
    def color_name(self) -> str:
        """Get the selected color name."""
        return self._color_name
    
    @property
    def group_index(self) -> int:
        """Get the group index."""
        return self._group_index


class RuleEditDialog(Adw.Dialog):
    """
    Dialog for creating or editing a highlight rule.
    
    Provides form fields for rule name, regex pattern, and multi-group
    color selection with theme-aware logical color names.
    """
    
    __gsignals__ = {
        "rule-saved": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }
    
    def __init__(
        self,
        parent: Gtk.Widget,
        rule: HighlightRule = None,
        is_new: bool = True,
    ):
        """
        Initialize the rule edit dialog.
        
        Args:
            parent: Parent widget for the dialog.
            rule: Existing rule to edit, or None to create new.
            is_new: Whether this is a new rule or editing existing.
        """
        super().__init__()
        self.logger = get_logger("ashyterm.ui.dialogs.rule_edit")
        self._parent = parent
        self._rule = rule or HighlightRule(name="", pattern="", colors=["white"])
        self._is_new = is_new
        self._manager = get_highlight_manager()
        
        # Color entry rows
        self._color_rows: list[ColorEntryRow] = []
        
        self.set_title(_("New Rule") if is_new else _("Edit Rule"))
        self.set_content_width(600)
        self.set_content_height(700)
        
        self._setup_ui()
        self._load_rule_data()
    
    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Main container with toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)
        
        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        
        # Cancel button
        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)
        
        # Save button
        self._save_btn = Gtk.Button(label=_("Save"))
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(self._save_btn)
        
        toolbar_view.add_top_bar(header)
        
        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)
        
        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        scrolled.set_child(content_box)
        
        # Name entry
        name_group = Adw.PreferencesGroup()
        self._name_row = Adw.EntryRow(title=_("Rule Name"))
        self._name_row.connect("changed", self._on_input_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)
        
        # Pattern entry with regex help
        pattern_group = Adw.PreferencesGroup(
            title=_("Pattern"),
            description=_("Use Python regular expression syntax. Capture groups () can have individual colors."),
        )
        
        # Pattern row with help button
        self._pattern_row = Adw.EntryRow(title=_("Regex Pattern"))
        self._pattern_row.add_css_class("monospace")
        self._pattern_row.connect("changed", self._on_pattern_changed)
        
        # Regex help button
        help_btn = Gtk.Button(icon_name="help-about-symbolic")
        help_btn.add_css_class("flat")
        help_btn.set_valign(Gtk.Align.CENTER)
        help_btn.set_tooltip_text(_("Regex reference"))
        help_btn.connect("clicked", self._on_regex_help_clicked)
        self._pattern_row.add_suffix(help_btn)
        
        pattern_group.add(self._pattern_row)
        content_box.append(pattern_group)
        
        # Validation status
        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        self._validation_label.set_wrap(True)
        content_box.append(self._validation_label)
        
        # Colors group
        self._colors_group = Adw.PreferencesGroup(
            title=_("Colors"),
            description=_("Assign colors to capture groups. First color applies to entire match if no groups."),
        )
        content_box.append(self._colors_group)
        
        # Add color button
        add_color_row = Adw.ActionRow(title=_("Add Color"))
        add_color_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_color_row.add_suffix(add_btn)
        add_color_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_color_clicked)
        self._colors_group.add(add_color_row)
        self._add_color_row = add_color_row
        
        # Description entry
        desc_group = Adw.PreferencesGroup()
        self._desc_row = Adw.EntryRow(title=_("Description (optional)"))
        desc_group.add(self._desc_row)
        content_box.append(desc_group)
        
        # Apply monospace CSS to pattern entry
        self._apply_monospace_css()
    
    def _apply_monospace_css(self) -> None:
        """Apply monospace font CSS to the pattern entry."""
        css_provider = Gtk.CssProvider()
        css = """
        .monospace {
            font-family: monospace;
        }
        """
        css_provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    
    def _load_rule_data(self) -> None:
        """Load existing rule data into form fields."""
        self._name_row.set_text(self._rule.name)
        self._pattern_row.set_text(self._rule.pattern)
        self._desc_row.set_text(self._rule.description)
        
        # Load colors
        colors = self._rule.colors or ["white"]
        for idx, color_name in enumerate(colors):
            self._add_color_row_widget(idx + 1, color_name or "white")
        
        self._validate_input()
    
    def _add_color_row_widget(self, group_index: int, color_name: str) -> None:
        """Add a color entry row to the colors group."""
        row = ColorEntryRow(group_index, color_name)
        row.connect("color-changed", self._on_color_changed)
        row.connect("remove-requested", self._on_remove_color, row)
        
        # Insert before the add button
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
            # Must have at least one color
            return
        
        self._colors_group.remove(target_row)
        self._color_rows.remove(target_row)
        
        # Renumber remaining rows
        for idx, row in enumerate(self._color_rows):
            row.set_title(_("Group {}").format(idx + 1))
            row._group_index = idx + 1
    
    def _on_color_changed(self, row: ColorEntryRow) -> None:
        """Handle color selection change."""
        pass  # Colors are read on save
    
    def _on_input_changed(self, widget) -> None:
        """Handle input changes to validate form."""
        self._validate_input()
    
    def _on_pattern_changed(self, widget) -> None:
        """Handle pattern changes - update colors count suggestion."""
        self._validate_input()
        
        # Suggest number of color rows based on capture groups
        pattern = self._pattern_row.get_text().strip()
        if pattern:
            is_valid, _ = self._manager.validate_pattern(pattern)
            if is_valid:
                import re
                try:
                    compiled = re.compile(pattern)
                    num_groups = compiled.groups
                    
                    # Update description with group info
                    if num_groups > 0:
                        self._colors_group.set_description(
                            _("Pattern has {} capture group(s). Add colors for each group.").format(num_groups)
                        )
                    else:
                        self._colors_group.set_description(
                            _("Pattern has no capture groups. First color applies to entire match.")
                        )
                except Exception:
                    pass
    
    def _validate_input(self) -> None:
        """Validate the current input and update UI accordingly."""
        name = self._name_row.get_text().strip()
        pattern = self._pattern_row.get_text().strip()
        
        # Check for required fields
        if not name:
            self._validation_label.set_text(_("Rule name is required"))
            self._validation_label.add_css_class("error")
            self._save_btn.set_sensitive(False)
            return
        
        if not pattern:
            self._validation_label.set_text(_("Pattern is required"))
            self._validation_label.add_css_class("error")
            self._save_btn.set_sensitive(False)
            return
        
        # Validate regex pattern
        is_valid, error_msg = self._manager.validate_pattern(pattern)
        
        if not is_valid:
            self._validation_label.set_text(_("Invalid regex: {}").format(error_msg))
            self._validation_label.add_css_class("error")
            self._validation_label.remove_css_class("success")
            self._save_btn.set_sensitive(False)
        else:
            self._validation_label.set_text(_("✓ Valid pattern"))
            self._validation_label.remove_css_class("error")
            self._validation_label.add_css_class("success")
            self._save_btn.set_sensitive(True)
    
    def _on_regex_help_clicked(self, button: Gtk.Button) -> None:
        """Show regex reference dialog."""
        dialog = Adw.Dialog()
        dialog.set_title(_("Regex Reference"))
        dialog.set_content_width(500)
        dialog.set_content_height(500)
        
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
        
        # Basic patterns
        basic_group = Adw.PreferencesGroup(title=_("Basic Patterns"))
        basic_items = [
            (".", _("Any character except newline")),
            ("\\d", _("Any digit [0-9]")),
            ("\\w", _("Word character [a-zA-Z0-9_]")),
            ("\\s", _("Whitespace character")),
            ("\\D, \\W, \\S", _("Negations of above")),
            ("^", _("Start of line")),
            ("$", _("End of line")),
            ("\\b", _("Word boundary")),
        ]
        for pattern, desc in basic_items:
            row = Adw.ActionRow(title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc)
            row.set_title_lines(1)
            basic_group.add(row)
        content.append(basic_group)
        
        # Quantifiers
        quant_group = Adw.PreferencesGroup(title=_("Quantifiers"))
        quant_items = [
            ("*", _("0 or more times")),
            ("+", _("1 or more times")),
            ("?", _("0 or 1 time")),
            ("{n}", _("Exactly n times")),
            ("{n,m}", _("Between n and m times")),
            ("*?, +?, ??", _("Non-greedy versions")),
        ]
        for pattern, desc in quant_items:
            row = Adw.ActionRow(title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc)
            row.set_title_lines(1)
            quant_group.add(row)
        content.append(quant_group)
        
        # Groups and alternatives
        groups_group = Adw.PreferencesGroup(title=_("Groups & Alternatives"))
        groups_items = [
            ("(abc)", _("Capture group (gets a color)")),
            ("(?:abc)", _("Non-capturing group")),
            ("a|b", _("Alternation (a or b)")),
            ("[abc]", _("Character class (a, b, or c)")),
            ("[^abc]", _("Negated class (not a, b, c)")),
            ("[a-z]", _("Character range")),
        ]
        for pattern, desc in groups_items:
            row = Adw.ActionRow(title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc)
            row.set_title_lines(1)
            groups_group.add(row)
        content.append(groups_group)
        
        # Examples
        examples_group = Adw.PreferencesGroup(title=_("Examples"))
        examples_items = [
            ("\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b", _("IPv4 address")),
            ("error|fail|fatal", _("Match error keywords")),
            ("(\\w+)=(\\w+)", _("Key=value pairs (2 groups)")),
            ("^\\s*#.*$", _("Comment lines")),
        ]
        for pattern, desc in examples_items:
            row = Adw.ActionRow(title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc)
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            examples_group.add(row)
        content.append(examples_group)
        
        dialog.present(self)
    
    def _on_save_clicked(self, button: Gtk.Button) -> None:
        """Handle save button click."""
        name = self._name_row.get_text().strip()
        pattern = self._pattern_row.get_text().strip()
        description = self._desc_row.get_text().strip()
        
        # Collect colors from rows
        colors = [row.color_name for row in self._color_rows]
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


class ContextRulesDialog(Adw.Dialog):
    """
    Dialog for editing rules of a specific command context.
    
    Opens when user clicks on a context row, providing a focused
    interface for managing context-specific highlighting rules.
    """
    
    __gsignals__ = {
        "context-updated": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    
    def __init__(self, parent: Gtk.Widget, context_name: str):
        """
        Initialize the context rules dialog.
        
        Args:
            parent: Parent widget for the dialog.
            context_name: Name of the context to edit.
        """
        super().__init__()
        self.logger = get_logger("ashyterm.ui.dialogs.context_rules")
        self._parent = parent
        self._context_name = context_name
        self._manager = get_highlight_manager()
        self._context_rule_rows: list[Adw.ActionRow] = []
        
        self.set_title(_("Context: {}").format(context_name))
        self.set_content_width(700)
        self.set_content_height(600)
        
        self._setup_ui()
        self._load_context_data()
    
    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Main container with toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)
        
        # Header bar with standard close button
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(False)
        
        toolbar_view.add_top_bar(header)
        
        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)
        
        # Main content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scrolled.set_child(content_box)
        
        # Preferences page for consistent styling
        self._prefs_page = Adw.PreferencesPage()
        content_box.append(self._prefs_page)
        
        # Context settings group
        settings_group = Adw.PreferencesGroup(
            title=_("Context Settings"),
        )
        self._prefs_page.add(settings_group)
        
        # Enable toggle
        self._enable_row = Adw.SwitchRow(
            title=_("Enable Context"),
            subtitle=_("Apply rules when this command is detected"),
        )
        self._enable_row.connect("notify::active", self._on_enable_toggled)
        settings_group.add(self._enable_row)
        
        # Use global rules toggle
        self._use_global_row = Adw.SwitchRow(
            title=_("Include Global Rules"),
            subtitle=_("Also apply global rules alongside context-specific rules"),
        )
        self._use_global_row.connect("notify::active", self._on_use_global_toggled)
        settings_group.add(self._use_global_row)
        
        # Reset button
        reset_row = Adw.ActionRow(
            title=_("Reset to System Default"),
            subtitle=_("Remove user customization and revert to system rules"),
        )
        reset_row.set_activatable(True)
        reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("flat")
        reset_row.add_suffix(reset_btn)
        reset_row.set_activatable_widget(reset_btn)
        reset_btn.connect("clicked", self._on_reset_clicked)
        settings_group.add(reset_row)
        
        # Triggers group
        self._triggers_group = Adw.PreferencesGroup(
            title=_("Triggers"),
            description=_("Commands that activate this context. Add, edit or remove triggers to customize when this context applies."),
        )
        self._prefs_page.add(self._triggers_group)
        
        # Add trigger button
        add_trigger_row = Adw.ActionRow(
            title=_("➕ Add Trigger"),
            subtitle=_("Add another command that activates this context"),
        )
        add_trigger_row.set_activatable(True)
        add_trigger_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_trigger_btn.set_valign(Gtk.Align.CENTER)
        add_trigger_btn.add_css_class("suggested-action")
        add_trigger_row.add_suffix(add_trigger_btn)
        add_trigger_row.set_activatable_widget(add_trigger_btn)
        add_trigger_btn.connect("clicked", self._on_add_trigger_clicked)
        self._triggers_group.add(add_trigger_row)
        self._add_trigger_row = add_trigger_row
        
        # Container for trigger rows
        self._trigger_rows: list[Adw.ActionRow] = []
        
        # Rules group
        self._rules_group = Adw.PreferencesGroup(
            title=_("Highlight Rules"),
            description=_("Use arrows to reorder. Rules are matched from top to bottom."),
        )
        self._prefs_page.add(self._rules_group)
        
        # Add rule button - first and prominent
        add_row = Adw.ActionRow(
            title=_("➕ Add New Rule"),
            subtitle=_("Create a new highlighting pattern for this context"),
        )
        add_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_row.add_suffix(add_btn)
        add_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_rule_clicked)
        self._rules_group.add(add_row)
    
    def _load_context_data(self) -> None:
        """Load context data into the form."""
        context = self._manager.get_context(self._context_name)
        if not context:
            return
        
        # Block signal handlers during load
        self._enable_row.handler_block_by_func(self._on_enable_toggled)
        self._enable_row.set_active(context.enabled)
        self._enable_row.handler_unblock_by_func(self._on_enable_toggled)
        
        self._use_global_row.handler_block_by_func(self._on_use_global_toggled)
        self._use_global_row.set_active(context.use_global_rules)
        self._use_global_row.handler_unblock_by_func(self._on_use_global_toggled)
        
        # Populate triggers
        self._populate_triggers()
        
        # Populate rules
        self._populate_rules()
    
    def _populate_triggers(self) -> None:
        """Populate the triggers list."""
        # Clear existing trigger rows
        for row in self._trigger_rows:
            self._triggers_group.remove(row)
        self._trigger_rows.clear()
        
        context = self._manager.get_context(self._context_name)
        if not context:
            return
        
        # Add trigger rows
        for trigger in context.triggers:
            row = self._create_trigger_row(trigger)
            self._triggers_group.add(row)
            self._trigger_rows.append(row)
    
    def _create_trigger_row(self, trigger: str) -> Adw.ActionRow:
        """Create an action row for a trigger with edit and delete buttons."""
        row = Adw.ActionRow(title=trigger)
        row.set_subtitle(_("Activates this context"))
        
        # Terminal icon prefix
        icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        icon.set_opacity(0.6)
        row.add_prefix(icon)
        
        # Edit button - always visible
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.set_tooltip_text(_("Edit trigger"))
        edit_btn.connect("clicked", self._on_edit_trigger_clicked, trigger)
        row.add_suffix(edit_btn)
        
        # Delete button - always visible but disabled if only one trigger
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.set_tooltip_text(_("Remove trigger"))
        delete_btn.connect("clicked", self._on_delete_trigger_clicked, trigger)
        
        context = self._manager.get_context(self._context_name)
        if context and len(context.triggers) <= 1:
            delete_btn.set_sensitive(False)
            delete_btn.set_tooltip_text(_("Cannot remove the last trigger"))
        
        row.add_suffix(delete_btn)
        
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
            # Replace old trigger with new
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
        
        dialog = Adw.AlertDialog(
            heading=_("Remove Trigger?"),
            body=_('Remove "{}" from the triggers list?').format(trigger),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_trigger_confirmed, trigger)
        dialog.present(self)
    
    def _on_delete_trigger_confirmed(self, dialog: Adw.AlertDialog, response: str, trigger: str) -> None:
        """Handle delete trigger confirmation."""
        if response == "remove":
            context = self._manager.get_context(self._context_name)
            if context and trigger in context.triggers:
                context.triggers.remove(trigger)
                self._manager.save_context_to_user(context)
                self._populate_triggers()
                self.emit("context-updated")
    
    def _populate_rules(self) -> None:
        """Populate the rules list."""
        # Clear existing rule rows
        for row in self._context_rule_rows:
            self._rules_group.remove(row)
        self._context_rule_rows.clear()
        
        context = self._manager.get_context(self._context_name)
        if not context:
            return
        
        # Add rule rows
        for index, rule in enumerate(context.rules):
            row = self._create_rule_row(rule, index, len(context.rules))
            self._rules_group.add(row)
            self._context_rule_rows.append(row)
    
    def _create_rule_row(self, rule: HighlightRule, index: int, total: int) -> Adw.ActionRow:
        """Create an action row for a rule with inline reorder, edit, delete, switch."""
        escaped_name = GLib.markup_escape_text(rule.name)
        subtitle_text = rule.description if rule.description else (
            rule.pattern[:40] + "..." if len(rule.pattern) > 40 else rule.pattern
        )
        escaped_subtitle = GLib.markup_escape_text(subtitle_text)
        
        row = Adw.ActionRow()
        row.set_title(f"#{index + 1} {escaped_name}")
        row.set_subtitle(escaped_subtitle)
        
        # Reorder buttons prefix
        reorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        reorder_box.set_valign(Gtk.Align.CENTER)
        reorder_box.set_margin_end(4)
        
        up_btn = Gtk.Button(icon_name="go-up-symbolic")
        up_btn.add_css_class("flat")
        up_btn.add_css_class("circular")
        up_btn.set_size_request(24, 24)
        up_btn.set_sensitive(index > 0)
        up_btn.connect("clicked", self._on_move_rule_up, index)
        up_btn.set_tooltip_text(_("Move up"))
        reorder_box.append(up_btn)
        
        down_btn = Gtk.Button(icon_name="go-down-symbolic")
        down_btn.add_css_class("flat")
        down_btn.add_css_class("circular")
        down_btn.set_size_request(24, 24)
        down_btn.set_sensitive(index < total - 1)
        down_btn.connect("clicked", self._on_move_rule_down, index)
        down_btn.set_tooltip_text(_("Move down"))
        reorder_box.append(down_btn)
        
        row.add_prefix(reorder_box)
        
        # Color indicator
        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        if rule.colors and rule.colors[0]:
            hex_color = self._manager.resolve_color(rule.colors[0])
            self._apply_color_to_box(color_box, hex_color)
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)
        
        # Edit button (icon)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.set_tooltip_text(_("Edit rule"))
        edit_btn.connect("clicked", self._on_edit_rule, index)
        row.add_suffix(edit_btn)
        
        # Delete button (icon)
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.set_tooltip_text(_("Delete rule"))
        delete_btn.connect("clicked", self._on_delete_rule, index)
        row.add_suffix(delete_btn)
        
        # Enable switch (rightmost)
        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_rule_toggle, index)
        row.add_suffix(switch)
        
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
        self._manager.set_context_use_global_rules(self._context_name, switch.get_active())
        self._manager.save_config()
        self.emit("context-updated")
    
    def _on_reset_clicked(self, button: Gtk.Button) -> None:
        """Handle reset button click."""
        dialog = Adw.AlertDialog(
            heading=_("Reset to System Default?"),
            body=_('This will remove your customizations for "{}" and revert to system rules.').format(
                self._context_name
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_reset_confirmed)
        dialog.present(self)
    
    def _on_reset_confirmed(self, dialog: Adw.AlertDialog, response: str) -> None:
        """Handle reset confirmation."""
        if response == "reset":
            if self._manager.delete_user_context(self._context_name):
                self._load_context_data()
                self.emit("context-updated")
    
    def _on_add_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule button click."""
        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_rule_saved)
        dialog.present(self)
    
    def _on_rule_saved(self, dialog: RuleEditDialog, rule: HighlightRule) -> None:
        """Handle new rule saved."""
        self._manager.add_rule_to_context(self._context_name, rule)
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")
    
    def _on_rule_toggle(self, switch: Gtk.Switch, _pspec, index: int) -> None:
        """Handle rule enable/disable toggle."""
        self._manager.set_context_rule_enabled(self._context_name, index, switch.get_active())
        self._manager.save_config()
        self.emit("context-updated")
    
    def _on_move_rule_up(self, button: Gtk.Button, index: int) -> None:
        """Move a rule up."""
        if index <= 0:
            return
        self._manager.move_context_rule(self._context_name, index, index - 1)
        self._manager.save_config()
        self._populate_rules()
        self.emit("context-updated")
    
    def _on_move_rule_down(self, button: Gtk.Button, index: int) -> None:
        """Move a rule down."""
        ctx = self._manager.get_context(self._context_name)
        if not ctx or index >= len(ctx.rules) - 1:
            return
        self._manager.move_context_rule(self._context_name, index, index + 1)
        self._manager.save_config()
        self._populate_rules()
        self.emit("context-updated")
    
    def _on_edit_rule(self, button: Gtk.Button, index: int) -> None:
        """Handle edit rule button click."""
        context = self._manager.get_context(self._context_name)
        if context and 0 <= index < len(context.rules):
            rule = context.rules[index]
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_rule_edited, index)
            dialog.present(self)
    
    def _on_rule_edited(self, dialog: RuleEditDialog, rule: HighlightRule, index: int) -> None:
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
        dialog = Adw.AlertDialog(
            heading=_("Delete Rule?"),
            body=_('Are you sure you want to delete "{}"?').format(rule.name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirmed, index, rule.name)
        dialog.present(self)
    
    def _on_delete_confirmed(self, dialog: Adw.AlertDialog, response: str, index: int, rule_name: str) -> None:
        """Handle delete confirmation."""
        if response == "delete":
            self._manager.remove_context_rule(self._context_name, index)
            context = self._manager.get_context(self._context_name)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_rules()
            self.emit("context-updated")


class HighlightDialog(Adw.PreferencesWindow):
    """
    Main dialog for managing syntax highlighting settings.
    
    Provides controls for global activation settings, a list of
    customizable highlight rules, and context-aware highlighting
    with command-specific rule sets.
    """
    
    __gsignals__ = {
        "settings-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    
    def __init__(self, parent_window: Gtk.Window):
        """
        Initialize the highlight dialog.
        
        Args:
            parent_window: Parent window for the dialog.
        """
        super().__init__(
            title=_("Highlight Colors"),
            transient_for=parent_window,
            modal=False,
            hide_on_close=True,
            default_width=800,
            default_height=700,
            search_enabled=True,
        )
        self.logger = get_logger("ashyterm.ui.dialogs.highlight")
        self._parent_window = parent_window
        self._manager = get_highlight_manager()
        self._rule_rows: list[Adw.ExpanderRow] = []
        self._context_rule_rows: list[Adw.ExpanderRow] = []
        self._selected_context: str = ""
        
        self._setup_ui()
        self._load_settings()
        
        self.logger.info("HighlightDialog initialized")
    
    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Create main page for Global Rules
        self._global_page = Adw.PreferencesPage(
            title=_("Global Rules"),
            icon_name="emblem-default-symbolic",
        )
        self.add(self._global_page)
        
        # Welcome/explanation text
        self._setup_welcome_banner(self._global_page)
        
        # Activation group
        self._setup_activation_group(self._global_page)
        
        # Ignored commands group (collapsible - placed before Global Rules as it's compact)
        self._setup_ignored_commands_group(self._global_page)
        
        # Global rules group (last, as it can be a longer list)
        self._setup_rules_group(self._global_page)
        
        # Create second page for Context-Aware Rules
        self._context_page = Adw.PreferencesPage(
            title=_("Context Rules"),
            icon_name="utilities-terminal-symbolic",
        )
        self.add(self._context_page)
        
        # Context settings group
        self._setup_context_settings_group(self._context_page)
        
        # Context selector group (clicking a context opens a dialog)
        self._setup_context_selector_group(self._context_page)
    
    def _setup_welcome_banner(self, page: Adw.PreferencesPage) -> None:
        """Setup the welcome/explanation text at the top."""
        welcome_group = Adw.PreferencesGroup(
            description=_(
                "Syntax highlighting enhances terminal output readability by colorizing patterns "
                "like errors, warnings, IP addresses, and more."
                "\n"
                "* Many highlight rules can slow down display of large outputs.."
            ),
        )
        page.add(welcome_group)
    
    def _setup_activation_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the activation settings group."""
        activation_group = Adw.PreferencesGroup(
            title=_("Activation"),
            description=_(
                "Control when syntax highlighting is applied to terminal output."
            ),
        )
        page.add(activation_group)
        
        # Enable for local terminals toggle
        self._local_toggle = Adw.SwitchRow(
            title=_("Local Terminals"),
            subtitle=_("Apply highlighting to local shell sessions"),
        )
        self._local_toggle.set_active(self._manager.enabled_for_local)
        self._local_toggle.connect("notify::active", self._on_local_toggled)
        activation_group.add(self._local_toggle)
        
        # Enable for SSH terminals toggle
        self._ssh_toggle = Adw.SwitchRow(
            title=_("SSH Sessions"),
            subtitle=_("Apply highlighting to SSH remote sessions"),
        )
        self._ssh_toggle.set_active(self._manager.enabled_for_ssh)
        self._ssh_toggle.connect("notify::active", self._on_ssh_toggled)
        activation_group.add(self._ssh_toggle)
    
    def _setup_ignored_commands_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the ignored commands group as a collapsible section."""
        self._ignored_commands_group = Adw.PreferencesGroup(
            title=_("Ignored Commands"),
        )
        page.add(self._ignored_commands_group)
        
        # Main expander row that contains all ignored commands
        self._ignored_expander = Adw.ExpanderRow(
            title=_("Highlighting disabled for these commands"),
            subtitle=_("Click to expand"),
        )
        self._ignored_expander.set_enable_expansion(True)
        self._ignored_expander.set_expanded(False)  # Collapsed by default
        
        # Add icon prefix
        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.set_opacity(0.6)
        self._ignored_expander.add_prefix(icon)
        
        self._ignored_commands_group.add(self._ignored_expander)
        
        # Restore defaults row (inside expander, at the top)
        restore_row = Adw.ActionRow(
            title=_("Restore Defaults"),
            subtitle=_("Reset to the default ignored commands list"),
        )
        restore_row.set_activatable(True)
        restore_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        restore_btn.set_valign(Gtk.Align.CENTER)
        restore_btn.add_css_class("flat")
        restore_row.add_suffix(restore_btn)
        restore_row.set_activatable_widget(restore_btn)
        restore_btn.connect("clicked", self._on_restore_ignored_defaults_clicked)
        self._ignored_expander.add_row(restore_row)
        
        # Add command button (inside expander) - prominent style
        add_cmd_row = Adw.ActionRow(
            title=_("➕ Add Ignored Command"),
            subtitle=_("Add a command that should preserve its native colors"),
        )
        add_cmd_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_cmd_row.add_suffix(add_btn)
        add_cmd_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_ignored_command_clicked)
        self._add_ignored_cmd_row = add_cmd_row
        self._ignored_expander.add_row(add_cmd_row)
        
        # Container for command rows inside the expander
        self._ignored_command_rows: dict[str, Adw.ActionRow] = {}
        
        # Populate initial list (after add button)
        self._populate_ignored_commands()
    
    def _populate_ignored_commands(self) -> None:
        """Populate the ignored commands list from settings."""
        # Clear existing rows from expander (but keep the add button)
        for row in list(self._ignored_command_rows.values()):
            self._ignored_expander.remove(row)
        self._ignored_command_rows.clear()
        
        # Get current ignored commands
        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])
        
        # Sort and add rows to expander (after the add button)
        for cmd in sorted(ignored_commands):
            row = self._create_ignored_command_row(cmd)
            self._ignored_command_rows[cmd] = row
            self._ignored_expander.add_row(row)
        
        # Update expander subtitle with count
        count = len(ignored_commands)
        self._ignored_expander.set_subtitle(
            _("{count} command(s) • Click to expand/collapse").format(count=count)
        )
    
    def _create_ignored_command_row(self, cmd: str) -> Adw.ActionRow:
        """Create a row for an ignored command with remove button."""
        row = Adw.ActionRow(title=cmd)
        row.set_subtitle(_("Native coloring preserved"))
        
        # Remove button
        remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.set_tooltip_text(_("Remove from ignored list"))
        remove_btn.connect("clicked", self._on_remove_ignored_command, cmd)
        row.add_suffix(remove_btn)
        
        return row
    
    def _on_add_ignored_command_clicked(self, button: Gtk.Button) -> None:
        """Handle add ignored command button click."""
        dialog = AddIgnoredCommandDialog(self)
        dialog.connect("command-added", self._on_ignored_command_added)
        dialog.present(self)
    
    def _on_ignored_command_added(self, dialog, command: str) -> None:
        """Handle new ignored command added."""
        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])
        
        if command not in ignored_commands:
            ignored_commands.append(command)
            ignored_commands.sort()
            settings.set("ignored_highlight_commands", ignored_commands)
            
            # Refresh highlighter's ignored commands cache
            from ...terminal.highlighter import get_output_highlighter
            get_output_highlighter().refresh_ignored_commands()
            
            self._populate_ignored_commands()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Command added: {}").format(command)))
    
    def _on_remove_ignored_command(self, button: Gtk.Button, command: str) -> None:
        """Handle remove ignored command button click - show confirmation."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Remove Ignored Command?"),
            body=_('Remove "{}" from the ignored commands list? Highlighting will be applied to this command\'s output.').format(command),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_remove_ignored_confirmed, command)
        dialog.present()
    
    def _on_remove_ignored_confirmed(self, dialog: Adw.MessageDialog, response: str, command: str) -> None:
        """Handle remove ignored command confirmation."""
        dialog.close()
        if response == "remove":
            settings = get_settings_manager()
            ignored_commands = settings.get("ignored_highlight_commands", [])
            
            if command in ignored_commands:
                ignored_commands.remove(command)
                settings.set("ignored_highlight_commands", ignored_commands)
                
                # Refresh highlighter's ignored commands cache
                from ...terminal.highlighter import get_output_highlighter
                get_output_highlighter().refresh_ignored_commands()
                
                self._populate_ignored_commands()
                self.emit("settings-changed")
                self.add_toast(Adw.Toast(title=_("Command removed: {}").format(command)))
    
    def _on_restore_ignored_defaults_clicked(self, button: Gtk.Button) -> None:
        """Handle restore defaults button click for ignored commands."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Restore Default Ignored Commands?"),
            body=_("This will replace your current ignored commands list with the system defaults. Custom additions will be lost."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore Defaults"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_restore_ignored_defaults_confirmed)
        dialog.present()
    
    def _on_restore_ignored_defaults_confirmed(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle restore defaults confirmation."""
        dialog.close()
        if response == "restore":
            from ...settings.config import DefaultSettings
            default_ignored = DefaultSettings.get_defaults().get("ignored_highlight_commands", [])
            
            settings = get_settings_manager()
            settings.set("ignored_highlight_commands", list(default_ignored))
            
            # Refresh highlighter's ignored commands cache
            from ...terminal.highlighter import get_output_highlighter
            get_output_highlighter().refresh_ignored_commands()
            
            self._populate_ignored_commands()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Restored default ignored commands")))
    
    def _setup_context_settings_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context-aware settings group."""
        context_settings_group = Adw.PreferencesGroup(
            title=_("Context-Aware Highlighting"),
            description=_(
                "Automatically apply command-specific rules when certain commands are detected."
            ),
        )
        page.add(context_settings_group)
        
        # Enable context-aware highlighting toggle
        self._context_aware_toggle = Adw.SwitchRow(
            title=_("Enable Context Detection"),
            subtitle=_("Detect running commands and apply specific highlight rules"),
        )
        self._context_aware_toggle.set_active(self._manager.context_aware_enabled)
        self._context_aware_toggle.connect("notify::active", self._on_context_aware_toggled)
        context_settings_group.add(self._context_aware_toggle)
    
    def _setup_context_selector_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context selector group with bulk actions."""
        self._context_selector_group = Adw.PreferencesGroup(
            title=_("Command Contexts"),
            description=_(
                "Context rules apply only when a specific command is running (e.g., ping, docker, git). "
                "Toggle contexts on/off or click a row to edit its rules."
            ),
        )
        page.add(self._context_selector_group)
        
        # ADD NEW CONTEXT - prominent at the top
        add_context_row = Adw.ActionRow(
            title=_("➕ Create New Context"),
            subtitle=_("Add custom highlighting rules for a specific command"),
        )
        add_context_row.set_activatable(True)
        add_context_row.add_css_class("suggested-action")
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_context_row.add_suffix(add_btn)
        add_context_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_context_clicked)
        self._add_context_row = add_context_row
        self._context_selector_group.add(add_context_row)
        
        # Bulk action buttons
        bulk_actions_row = Adw.ActionRow(
            title=_("Bulk Actions"),
            subtitle=_("Quickly enable or disable all contexts"),
        )
        
        enable_all_btn = Gtk.Button(label=_("Enable All"))
        enable_all_btn.set_valign(Gtk.Align.CENTER)
        enable_all_btn.add_css_class("suggested-action")
        enable_all_btn.connect("clicked", self._on_enable_all_contexts)
        bulk_actions_row.add_suffix(enable_all_btn)
        
        disable_all_btn = Gtk.Button(label=_("Disable All"))
        disable_all_btn.set_valign(Gtk.Align.CENTER)
        disable_all_btn.add_css_class("destructive-action")
        disable_all_btn.connect("clicked", self._on_disable_all_contexts)
        bulk_actions_row.add_suffix(disable_all_btn)
        
        self._context_selector_group.add(bulk_actions_row)
        
        # Scrolled container for context list
        self._context_list_group = Adw.PreferencesGroup(
            title=_("Available Contexts"),
            description=_("Click a context to select it, then scroll down to customize its rules"),
        )
        page.add(self._context_list_group)
        
        # Context rows will be added dynamically
        self._context_rows: dict[str, Adw.ActionRow] = {}
    
    def _setup_context_rules_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context rules list group with reorder support."""
        self._context_rules_group = Adw.PreferencesGroup(
            title=_("Context Rules"),
            description=_("Rules specific to the selected command. Order matters!"),
        )
        page.add(self._context_rules_group)
        
        # Context header with enable/settings
        self._context_header_row = Adw.ActionRow(
            title=_("No context selected"),
            subtitle=_("Select a context from the list above"),
        )
        self._context_rules_group.add(self._context_header_row)
        
        # Context enable toggle
        self._context_enable_row = Adw.SwitchRow(
            title=_("Enable Context"),
            subtitle=_("Apply rules when this command is detected"),
        )
        self._context_enable_row.connect("notify::active", self._on_context_enable_toggled)
        self._context_rules_group.add(self._context_enable_row)
        
        # Use global rules toggle
        self._use_global_rules_row = Adw.SwitchRow(
            title=_("Include Global Rules"),
            subtitle=_("Also apply global rules alongside context-specific rules"),
        )
        self._use_global_rules_row.connect("notify::active", self._on_use_global_rules_toggled)
        self._context_rules_group.add(self._use_global_rules_row)
        
        # Reset to default button
        self._reset_context_row = Adw.ActionRow(
            title=_("Reset to System Default"),
            subtitle=_("Remove user customization and revert to system rules"),
        )
        self._reset_context_row.set_activatable(True)
        reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("flat")
        self._reset_context_row.add_suffix(reset_btn)
        self._reset_context_row.set_activatable_widget(reset_btn)
        reset_btn.connect("clicked", self._on_reset_context_clicked)
        self._reset_context_row.set_sensitive(False)
        self._context_rules_group.add(self._reset_context_row)
        
        # Rules list group (separate for better organization)
        self._context_rules_list_group = Adw.PreferencesGroup(
            title=_("Rules (in execution order)"),
            description=_("Use arrows to reorder rules. Rules are matched from top to bottom."),
        )
        page.add(self._context_rules_list_group)
        
        # Add rule to context button - make it prominent
        add_rule_row = Adw.ActionRow(
            title=_("➕ Add Rule to This Context"),
            subtitle=_("Create a new highlighting pattern for the selected command"),
        )
        add_rule_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_rule_row.add_suffix(add_btn)
        add_rule_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_context_rule_clicked)
        self._add_context_rule_row = add_rule_row
        self._context_rules_list_group.add(add_rule_row)
    
    def _setup_rules_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the global rules list group."""
        self._rules_group = Adw.PreferencesGroup(
            title=_("Global Highlight Rules"),
            description=_(
                "These rules apply to ALL terminal output, regardless of which command is running. "
                "Use for patterns you always want highlighted (errors, warnings, IP addresses, etc.). "
                "\n"
                "For command-specific rules, use the Context Rules tab."
            ),
        )
        page.add(self._rules_group)
        
        # Add rule button - make it more prominent
        add_row = Adw.ActionRow(
            title=_("➕ Add New Global Rule"),
            subtitle=_("Create a new pattern to highlight in all terminal output"),
        )
        add_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_row.add_suffix(add_btn)
        add_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_rule_clicked)
        self._rules_group.add(add_row)
        
        # Reset to defaults button
        reset_row = Adw.ActionRow(
            title=_("Reset to Defaults"),
            subtitle=_("Restore all default rules and contexts"),
        )
        reset_row.set_activatable(True)
        reset_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("flat")
        reset_row.add_suffix(reset_btn)
        reset_row.set_activatable_widget(reset_btn)
        reset_btn.connect("clicked", self._on_reset_clicked)
        self._rules_group.add(reset_row)
    
    def _load_settings(self) -> None:
        """Load current settings from the manager."""
        self._local_toggle.set_active(self._manager.enabled_for_local)
        self._ssh_toggle.set_active(self._manager.enabled_for_ssh)
        self._context_aware_toggle.set_active(self._manager.context_aware_enabled)
        self._populate_rules()
        self._populate_contexts()
    
    def _populate_contexts(self) -> None:
        """Populate the context list with toggle rows for each context."""
        # Clear existing context rows
        for row in list(self._context_rows.values()):
            self._context_list_group.remove(row)
        self._context_rows.clear()
        
        # Get all contexts sorted by name
        context_names = sorted(self._manager.get_context_names())
        
        # Count enabled contexts
        enabled_count = sum(
            1 for name in context_names
            if self._manager.get_context(name) and self._manager.get_context(name).enabled
        )
        
        # Update selector group description
        self._context_selector_group.set_description(
            _("{total} context(s), {enabled} enabled").format(
                total=len(context_names), enabled=enabled_count
            )
        )
        
        # Create a row for each context
        for name in context_names:
            ctx = self._manager.get_context(name)
            if not ctx:
                continue
            
            row = self._create_context_list_row(name, ctx)
            self._context_rows[name] = row
            self._context_list_group.add(row)
    
    def _create_context_list_row(self, name: str, ctx) -> Adw.ActionRow:
        """Create a row for a context in the list with inline edit, delete, switch buttons."""
        rule_count = len(ctx.rules)
        
        row = Adw.ActionRow(
            title=name,
            subtitle=_("{count} rules").format(count=rule_count),
        )
        row.set_activatable(False)  # Not clickable as full row
        
        # Terminal icon prefix
        icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        icon.set_opacity(0.6)
        row.add_prefix(icon)
        
        # Edit button (icon)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.set_tooltip_text(_("Edit context rules"))
        edit_btn.connect("clicked", self._on_edit_context_clicked, name)
        row.add_suffix(edit_btn)
        
        # Delete button (icon) - only for user-modified contexts
        if self._manager.has_user_context_override(name):
            delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
            delete_btn.add_css_class("flat")
            delete_btn.set_valign(Gtk.Align.CENTER)
            delete_btn.set_tooltip_text(_("Delete context"))
            delete_btn.connect("clicked", self._on_delete_context_clicked, name)
            row.add_suffix(delete_btn)
        
        # Enable/disable switch (rightmost)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(ctx.enabled)
        switch.connect("state-set", self._on_context_toggle, name)
        row.add_suffix(switch)
        
        # Store switch reference for later updates
        row._context_switch = switch
        
        return row
    
    def _on_edit_context_clicked(self, button: Gtk.Button, context_name: str) -> None:
        """Handle edit context button click."""
        self._open_context_dialog(context_name)
    
    def _on_delete_context_clicked(self, button: Gtk.Button, context_name: str) -> None:
        """Handle delete context button click."""
        ctx = self._manager.get_context(context_name)
        if not ctx or not self._manager.has_user_context_override(context_name):
            return
        
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete Context?"),
            body=_('Are you sure you want to delete "{}"? This will remove all custom rules for this command.').format(context_name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_context_confirmed, context_name)
        dialog.present()
    
    def _on_delete_context_confirmed(self, dialog: Adw.MessageDialog, response: str, context_name: str) -> None:
        """Handle delete context confirmation."""
        dialog.close()
        if response == "delete":
            if self._manager.delete_user_context(context_name):
                self._populate_contexts()
                self.emit("settings-changed")
                self.add_toast(Adw.Toast(title=_("Context deleted: {}").format(context_name)))
    
    def _on_context_toggle(self, switch: Gtk.Switch, state: bool, context_name: str) -> bool:
        """Handle context toggle from the list."""
        self._manager.set_context_enabled(context_name, state)
        self._manager.save_config()
        
        # Update the description count
        context_names = self._manager.get_context_names()
        enabled_count = sum(
            1 for name in context_names
            if self._manager.get_context(name) and self._manager.get_context(name).enabled
        )
        self._context_selector_group.set_description(
            _("{total} context(s), {enabled} enabled").format(
                total=len(context_names), enabled=enabled_count
            )
        )
        
        # If this is the selected context, update its detail view
        if context_name == self._selected_context:
            self._context_enable_row.handler_block_by_func(self._on_context_enable_toggled)
            self._context_enable_row.set_active(state)
            self._context_enable_row.handler_unblock_by_func(self._on_context_enable_toggled)
        
        self.emit("settings-changed")
        return False  # Don't block the default handler
    
    def _on_context_row_activated(self, row: Adw.ActionRow, context_name: str) -> None:
        """Handle context row activation - open context rules dialog."""
        self._open_context_dialog(context_name)
    
    def _open_context_dialog(self, context_name: str) -> None:
        """Open the context rules dialog for a specific context."""
        dialog = ContextRulesDialog(self, context_name)
        dialog.connect("context-updated", self._on_context_dialog_updated)
        dialog.present(self)
    
    def _on_context_dialog_updated(self, dialog) -> None:
        """Handle updates from the context rules dialog."""
        self._populate_contexts()
        self.emit("settings-changed")
    
    def _select_context(self, context_name: str) -> None:
        """Select a context for editing."""
        self._selected_context = context_name
        
        # Update visual selection (highlight the selected row)
        for name, row in self._context_rows.items():
            if name == context_name:
                row.add_css_class("accent")
            else:
                row.remove_css_class("accent")
        
        # Enable reset button
        self._reset_context_row.set_sensitive(bool(self._selected_context))
        
        # Update the context rules section
        self._populate_context_rules()
    
    def _on_enable_all_contexts(self, button: Gtk.Button) -> None:
        """Enable all contexts."""
        for name in self._manager.get_context_names():
            self._manager.set_context_enabled(name, True)
        self._manager.save_config()
        self._populate_contexts()
        self.emit("settings-changed")
    
    def _on_disable_all_contexts(self, button: Gtk.Button) -> None:
        """Disable all contexts."""
        for name in self._manager.get_context_names():
            self._manager.set_context_enabled(name, False)
        self._manager.save_config()
        self._populate_contexts()
        self.emit("settings-changed")
    
    def _populate_context_rules(self) -> None:
        """Populate rules for the selected context."""
        # Clear existing context rule rows
        for row in self._context_rule_rows:
            self._context_rules_list_group.remove(row)
        self._context_rule_rows.clear()
        
        if not self._selected_context:
            self._context_enable_row.set_sensitive(False)
            self._use_global_rules_row.set_sensitive(False)
            self._add_context_rule_row.set_sensitive(False)
            self._reset_context_row.set_sensitive(False)
            # Update header
            self._context_header_row.set_title(_("No context selected"))
            self._context_header_row.set_subtitle(_("Select a context from the list above"))
            self._context_rules_list_group.set_description(_("Select a context to view its rules"))
            return
        
        self._context_enable_row.set_sensitive(True)
        self._use_global_rules_row.set_sensitive(True)
        self._add_context_rule_row.set_sensitive(True)
        self._reset_context_row.set_sensitive(True)
        
        # Get context
        context = self._manager.get_context(self._selected_context)
        if not context:
            self._context_header_row.set_title(self._selected_context)
            self._context_header_row.set_subtitle(_("Context not found"))
            return
        
        # Update header with context info
        trigger_info = ", ".join(context.triggers[:3])
        if len(context.triggers) > 3:
            trigger_info += "..."
        self._context_header_row.set_title(self._selected_context)
        self._context_header_row.set_subtitle(_("Triggers: {triggers}").format(triggers=trigger_info))
        
        # Update rules group description
        status = _("Enabled") if context.enabled else _("Disabled")
        rule_count = len(context.rules)
        self._context_rules_list_group.set_description(
            _("{count} rule(s) • {status} • Use arrows to reorder").format(
                count=rule_count, status=status
            )
        )
        
        # Block signal handler during programmatic update
        self._context_enable_row.handler_block_by_func(self._on_context_enable_toggled)
        self._context_enable_row.set_active(context.enabled)
        self._context_enable_row.handler_unblock_by_func(self._on_context_enable_toggled)
        
        # Update use global rules toggle
        self._use_global_rules_row.handler_block_by_func(self._on_use_global_rules_toggled)
        self._use_global_rules_row.set_active(context.use_global_rules)
        self._use_global_rules_row.handler_unblock_by_func(self._on_use_global_rules_toggled)
        
        # Add rule rows for this context with reorder buttons
        for index, rule in enumerate(context.rules):
            row = self._create_context_rule_row(rule, index, len(context.rules))
            self._context_rules_list_group.add(row)
            self._context_rule_rows.append(row)
    
    def _get_rule_color_display(self, rule: HighlightRule) -> str:
        """Get the first color from a rule for display."""
        if rule.colors and rule.colors[0]:
            return self._manager.resolve_color(rule.colors[0])
        return "#ffffff"
    
    def _create_context_rule_row(self, rule: HighlightRule, index: int, total_rules: int = 0) -> Adw.ExpanderRow:
        """Create an expander row for a context-specific rule with reorder buttons."""
        # Escape markup characters to prevent GTK parsing errors
        escaped_name = GLib.markup_escape_text(rule.name)
        subtitle_text = rule.description if rule.description else (
            rule.pattern[:40] + "..." if len(rule.pattern) > 40 else rule.pattern
        )
        escaped_subtitle = GLib.markup_escape_text(subtitle_text)
        
        row = Adw.ExpanderRow()
        row.set_title(f"#{index + 1} {escaped_name}")
        row.set_subtitle(escaped_subtitle)
        
        # Reorder buttons prefix (box with up/down arrows)
        reorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        reorder_box.set_valign(Gtk.Align.CENTER)
        reorder_box.set_margin_end(4)
        
        up_btn = Gtk.Button(icon_name="go-up-symbolic")
        up_btn.add_css_class("flat")
        up_btn.add_css_class("circular")
        up_btn.set_size_request(24, 24)
        up_btn.set_sensitive(index > 0)
        up_btn.connect("clicked", self._on_move_rule_up, index)
        up_btn.set_tooltip_text(_("Move up"))
        reorder_box.append(up_btn)
        
        down_btn = Gtk.Button(icon_name="go-down-symbolic")
        down_btn.add_css_class("flat")
        down_btn.add_css_class("circular")
        down_btn.set_size_request(24, 24)
        down_btn.set_sensitive(index < total_rules - 1)
        down_btn.connect("clicked", self._on_move_rule_down, index)
        down_btn.set_tooltip_text(_("Move down"))
        reorder_box.append(down_btn)
        
        row.add_prefix(reorder_box)
        
        # Color indicator (shows first color)
        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        self._apply_color_to_box(color_box, self._get_rule_color_display(rule))
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)
        
        # Colors count badge
        if rule.colors and len(rule.colors) > 1:
            colors_badge = Gtk.Label(label=f"{len(rule.colors)}")
            colors_badge.add_css_class("dim-label")
            colors_badge.set_valign(Gtk.Align.CENTER)
            colors_badge.set_tooltip_text(_("{} colors").format(len(rule.colors)))
            row.add_suffix(colors_badge)
        
        # Enable/disable switch suffix
        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_context_rule_switch_toggled, index)
        row.add_suffix(switch)
        
        # Expanded content - action buttons
        actions_row = Adw.ActionRow(title=_("Actions"))
        
        edit_btn = Gtk.Button(label=_("Edit"))
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.connect("clicked", self._on_edit_context_rule_clicked, index)
        actions_row.add_suffix(edit_btn)
        
        delete_btn = Gtk.Button(label=_("Delete"))
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", self._on_delete_context_rule_clicked, index)
        actions_row.add_suffix(delete_btn)
        
        row.add_row(actions_row)
        
        return row
    
    def _on_move_rule_up(self, button: Gtk.Button, index: int) -> None:
        """Move a rule up in the order."""
        if not self._selected_context or index <= 0:
            return
        self._manager.move_context_rule(self._selected_context, index, index - 1)
        self._manager.save_config()
        self._populate_context_rules()
        self.emit("settings-changed")
    
    def _on_move_rule_down(self, button: Gtk.Button, index: int) -> None:
        """Move a rule down in the order."""
        if not self._selected_context:
            return
        ctx = self._manager.get_context(self._selected_context)
        if not ctx or index >= len(ctx.rules) - 1:
            return
        self._manager.move_context_rule(self._selected_context, index, index + 1)
        self._manager.save_config()
        self._populate_context_rules()
        self.emit("settings-changed")
    
    def _on_context_aware_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle context-aware toggle."""
        self._manager.context_aware_enabled = switch.get_active()
        self._manager.save_config()
        self.emit("settings-changed")
    
    def _on_context_enable_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle context enable toggle from detail view."""
        if self._selected_context:
            state = switch.get_active()
            self._manager.set_context_enabled(self._selected_context, state)
            self._manager.save_config()
            
            # Update the list row switch
            if self._selected_context in self._context_rows:
                row = self._context_rows[self._selected_context]
                # The switch is the first child of prefix
                for child in row:
                    if isinstance(child, Gtk.Switch):
                        child.handler_block_by_func(self._on_context_toggle)
                        child.set_active(state)
                        child.handler_unblock_by_func(self._on_context_toggle)
                        break
            
            # Update the description count
            context_names = self._manager.get_context_names()
            enabled_count = sum(
                1 for name in context_names
                if self._manager.get_context(name) and self._manager.get_context(name).enabled
            )
            self._context_selector_group.set_description(
                _("{total} context(s), {enabled} enabled. Click to toggle, select to edit.").format(
                    total=len(context_names), enabled=enabled_count
                )
            )
            
            self.emit("settings-changed")
    
    def _on_use_global_rules_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle use global rules toggle."""
        if self._selected_context:
            self._manager.set_context_use_global_rules(self._selected_context, switch.get_active())
            self._manager.save_config()
            self.emit("settings-changed")
    
    def _on_context_rule_switch_toggled(self, switch: Gtk.Switch, _pspec, index: int) -> None:
        """Handle context rule enable/disable toggle."""
        if self._selected_context:
            self._manager.set_context_rule_enabled(
                self._selected_context, index, switch.get_active()
            )
            self._manager.save_config()
            self.emit("settings-changed")
    
    def _on_add_context_clicked(self, button: Gtk.Button) -> None:
        """Handle add context button click."""
        dialog = ContextNameDialog(self)
        dialog.connect("context-created", self._on_context_created)
        dialog.present(self)
    
    def _on_context_created(self, dialog, context_name: str) -> None:
        """Handle new context creation."""
        context = HighlightContext(
            command_name=context_name,
            triggers=[context_name],
            rules=[],
            enabled=True,
            description=f"Custom rules for {context_name}",
        )
        self._manager.add_context(context)
        self._manager.save_context_to_user(context)
        self._populate_contexts()
        
        self.emit("settings-changed")
        self.add_toast(Adw.Toast(title=_("Context created: {}").format(context_name)))
        
        # Open the dialog for the new context so user can add rules
        self._open_context_dialog(context_name)
    
    def _on_reset_context_clicked(self, button: Gtk.Button) -> None:
        """Handle reset context to system default button click."""
        if not self._selected_context:
            return
        
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Reset to System Default?"),
            body=_('This will remove your customizations for "{}" and revert to system rules.').format(
                self._selected_context
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_reset_context_confirmed)
        dialog.present()
    
    def _on_reset_context_confirmed(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle reset context confirmation."""
        dialog.close()
        if response == "reset" and self._selected_context:
            name = self._selected_context
            if self._manager.delete_user_context(name):
                self._populate_contexts()
                self.emit("settings-changed")
                self.add_toast(Adw.Toast(title=_("Context reset: {}").format(name)))
            else:
                self.add_toast(Adw.Toast(title=_("No user customization to reset")))
    
    def _on_add_context_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule to context button click."""
        if not self._selected_context:
            return
        
        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_context_rule_saved)
        dialog.present(self)
    
    def _on_context_rule_saved(self, dialog: RuleEditDialog, rule: HighlightRule) -> None:
        """Handle saving a new context rule."""
        if self._selected_context:
            self._manager.add_rule_to_context(self._selected_context, rule)
            # Save to user directory to create override
            context = self._manager.get_context(self._selected_context)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule added: {}").format(rule.name)))
    
    def _on_edit_context_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle edit context rule button click."""
        if not self._selected_context:
            return
        
        context = self._manager.get_context(self._selected_context)
        if context and 0 <= index < len(context.rules):
            rule = context.rules[index]
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_context_rule_edited, index)
            dialog.present(self)
    
    def _on_context_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle saving an edited context rule."""
        if self._selected_context:
            self._manager.update_context_rule(self._selected_context, index, rule)
            # Save to user directory to create override
            context = self._manager.get_context(self._selected_context)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule updated: {}").format(rule.name)))
    
    def _on_delete_context_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle delete context rule button click."""
        if not self._selected_context:
            return
        
        context = self._manager.get_context(self._selected_context)
        if not context or index >= len(context.rules):
            return
        
        rule = context.rules[index]
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete Rule?"),
            body=_('Are you sure you want to delete "{}"?').format(rule.name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_context_rule_confirmed, index, rule.name)
        dialog.present()
    
    def _on_delete_context_rule_confirmed(
        self,
        dialog: Adw.MessageDialog,
        response: str,
        index: int,
        rule_name: str,
    ) -> None:
        """Handle delete context rule confirmation."""
        dialog.close()
        if response == "delete" and self._selected_context:
            self._manager.remove_context_rule(self._selected_context, index)
            # Save to user directory to create override
            context = self._manager.get_context(self._selected_context)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule deleted: {}").format(rule_name)))
    
    def _on_local_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle local terminals toggle."""
        self._manager.enabled_for_local = switch.get_active()
        self._manager.save_config()
        self.emit("settings-changed")
    
    def _on_ssh_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle SSH terminals toggle."""
        self._manager.enabled_for_ssh = switch.get_active()
        self._manager.save_config()
        self.emit("settings-changed")
    
    def _populate_rules(self) -> None:
        """Populate the global rules list from the manager."""
        # Clear existing rule rows
        for row in self._rule_rows:
            self._rules_group.remove(row)
        self._rule_rows.clear()
        
        # Add rules
        for index, rule in enumerate(self._manager.rules):
            row = self._create_rule_row(rule, index)
            self._rules_group.add(row)
            self._rule_rows.append(row)
    
    def _create_rule_row(self, rule: HighlightRule, index: int) -> Adw.ActionRow:
        """Create an action row for a highlight rule with inline edit/delete icons."""
        # Escape markup characters to prevent GTK parsing errors
        escaped_name = GLib.markup_escape_text(rule.name)
        subtitle_text = rule.description if rule.description else (
            rule.pattern[:40] + "..." if len(rule.pattern) > 40 else rule.pattern
        )
        escaped_subtitle = GLib.markup_escape_text(subtitle_text)
        
        row = Adw.ActionRow()
        row.set_title(escaped_name)
        row.set_subtitle(escaped_subtitle)
        
        # Color indicator prefix (shows first color)
        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        self._apply_color_to_box(color_box, self._get_rule_color_display(rule))
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)
        
        # Colors count badge
        if rule.colors and len(rule.colors) > 1:
            colors_badge = Gtk.Label(label=f"{len(rule.colors)}")
            colors_badge.add_css_class("dim-label")
            colors_badge.set_valign(Gtk.Align.CENTER)
            colors_badge.set_tooltip_text(_("{} colors").format(len(rule.colors)))
            row.add_suffix(colors_badge)
        
        # Edit button (icon)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.set_tooltip_text(_("Edit rule"))
        edit_btn.connect("clicked", self._on_edit_rule_clicked, index)
        row.add_suffix(edit_btn)
        
        # Delete button (icon)
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.set_tooltip_text(_("Delete rule"))
        delete_btn.connect("clicked", self._on_delete_rule_clicked, index)
        row.add_suffix(delete_btn)
        
        # Enable/disable switch suffix (rightmost)
        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_rule_switch_toggled, index)
        row.add_suffix(switch)
        
        # Store index for reference
        row._rule_index = index
        row._rule_switch = switch
        row._color_box = color_box
        
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
        # Store and remove old provider
        if hasattr(box, "_css_provider"):
            context.remove_provider(box._css_provider)
        
        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box.add_css_class("rule-color-indicator")
        box._css_provider = css_provider
    
    def _on_rule_switch_toggled(self, switch: Gtk.Switch, _pspec, index: int) -> None:
        """Handle rule enable/disable toggle."""
        self._manager.set_rule_enabled(index, switch.get_active())
        self._manager.save_config()
        self.emit("settings-changed")
    
    def _on_add_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule button click."""
        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_new_rule_saved)
        dialog.present(self)
    
    def _on_new_rule_saved(self, dialog: RuleEditDialog, rule: HighlightRule) -> None:
        """Handle saving a new rule."""
        self._manager.add_rule(rule)
        self._manager.save_config()
        self._populate_rules()
        self.emit("settings-changed")
        
        self.add_toast(Adw.Toast(title=_("Rule added: {}").format(rule.name)))
    
    def _on_edit_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle edit rule button click."""
        rule = self._manager.get_rule(index)
        if rule:
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_rule_edited, index)
            dialog.present(self)
    
    def _on_rule_edited(self, dialog: RuleEditDialog, rule: HighlightRule, index: int) -> None:
        """Handle saving an edited rule."""
        self._manager.update_rule(index, rule)
        self._manager.save_config()
        self._populate_rules()
        self.emit("settings-changed")
        
        self.add_toast(Adw.Toast(title=_("Rule updated: {}").format(rule.name)))
    
    def _on_delete_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle delete rule button click."""
        rule = self._manager.get_rule(index)
        if not rule:
            return
        
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete Rule?"),
            body=_('Are you sure you want to delete "{}"?').format(rule.name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirmed, index, rule.name)
        dialog.present()
    
    def _on_delete_confirmed(
        self,
        dialog: Adw.MessageDialog,
        response: str,
        index: int,
        rule_name: str,
    ) -> None:
        """Handle delete confirmation response."""
        dialog.close()
        if response == "delete":
            self._manager.remove_rule(index)
            self._manager.save_config()
            self._populate_rules()
            self.emit("settings-changed")
            
            self.add_toast(Adw.Toast(title=_("Rule deleted: {}").format(rule_name)))
    
    def _on_reset_clicked(self, button: Gtk.Button) -> None:
        """Handle reset to defaults button click."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Reset to Defaults?"),
            body=_("This will remove all user customizations and revert to system defaults. This cannot be undone."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_reset_confirmed)
        dialog.present()
    
    def _on_reset_confirmed(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle reset confirmation response."""
        dialog.close()
        if response == "reset":
            self._manager.reset_to_defaults()
            self._manager.save_config()
            self._populate_rules()
            self._populate_contexts()
            self.emit("settings-changed")
            
            self.add_toast(Adw.Toast(title=_("Reset to system defaults")))


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
        self.logger = get_logger("ashyterm.ui.dialogs.context_name")
        self._parent = parent
        self._manager = get_highlight_manager()
        
        self.set_title(_("New Context"))
        self.set_content_width(350)
        self.set_content_height(200)
        
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Main container with toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)
        
        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        
        # Cancel button
        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)
        
        # Create button
        self._create_btn = Gtk.Button(label=_("Create"))
        self._create_btn.add_css_class("suggested-action")
        self._create_btn.set_sensitive(False)
        self._create_btn.connect("clicked", self._on_create_clicked)
        header.pack_end(self._create_btn)
        
        toolbar_view.add_top_bar(header)
        
        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        toolbar_view.set_content(content_box)
        
        # Command name entry
        name_group = Adw.PreferencesGroup(
            description=_("Enter the command name (e.g., ping, docker, git)")
        )
        self._name_row = Adw.EntryRow(title=_("Command Name"))
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)
        
        # Validation label
        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)
    
    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        """Handle name entry change."""
        name = entry.get_text().strip().lower()
        
        if not name:
            self._validation_label.set_text(_("Enter a command name"))
            self._validation_label.remove_css_class("error")
            self._create_btn.set_sensitive(False)
            return
        
        # Check if context already exists
        if name in self._manager.get_context_names():
            self._validation_label.set_text(_("Context already exists"))
            self._validation_label.add_css_class("error")
            self._create_btn.set_sensitive(False)
            return
        
        # Validate name (alphanumeric + underscore)
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
    
    def __init__(self, parent: Gtk.Widget, context_name: str, existing_trigger: str = None):
        """
        Initialize the add/edit trigger dialog.
        
        Args:
            parent: Parent widget for the dialog.
            context_name: Name of the context this trigger belongs to.
            existing_trigger: If editing, the current trigger name.
        """
        super().__init__()
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
        self._save_btn.add_css_class("suggested-action")
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
            description=_("Enter a command name that should activate the '{}' context.").format(self._context_name)
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
            self._validation_label.set_text(_("Enter a command name"))
            self._validation_label.remove_css_class("error")
            self._save_btn.set_sensitive(False)
            return
        
        # Check if already in triggers (unless editing the same one)
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
        """
        Initialize the add ignored command dialog.
        
        Args:
            parent: Parent widget for the dialog.
        """
        super().__init__()
        self.logger = get_logger("ashyterm.ui.dialogs.add_ignored_cmd")
        self._parent = parent
        
        self.set_title(_("Add Ignored Command"))
        self.set_content_width(350)
        self.set_content_height(200)
        
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Main container with toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)
        
        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        
        # Cancel button
        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)
        
        # Add button
        self._add_btn = Gtk.Button(label=_("Add"))
        self._add_btn.add_css_class("suggested-action")
        self._add_btn.set_sensitive(False)
        self._add_btn.connect("clicked", self._on_add_clicked)
        header.pack_end(self._add_btn)
        
        toolbar_view.add_top_bar(header)
        
        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        toolbar_view.set_content(content_box)
        
        # Command name entry
        name_group = Adw.PreferencesGroup(
            description=_("Commands with native coloring (grep, ls, git, etc.) should be added here.")
        )
        self._name_row = Adw.EntryRow(title=_("Command Name"))
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)
        
        # Validation label
        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)
    
    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        """Handle name entry change."""
        name = entry.get_text().strip().lower()
        
        if not name:
            self._validation_label.set_text(_("Enter a command name"))
            self._validation_label.remove_css_class("error")
            self._add_btn.set_sensitive(False)
            return
        
        # Check if already in list
        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])
        if name in ignored_commands:
            self._validation_label.set_text(_("Command already in list"))
            self._validation_label.add_css_class("error")
            self._add_btn.set_sensitive(False)
            return
        
        # Validate name (alphanumeric + underscore + hyphen)
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