# ashyterm/ui/dialogs/command_manager/command_field_rows.py
"""Field-type-specific row builders for CommandEditorDialog.

Each builder extends an ``Adw.ExpanderRow`` with the rows the editor
needs for one kind of form field (text, number, switch, dropdown, …).
Moving them here drops ~330 lines of UI scaffolding out of the main
dialog and lets the "which field type shows which controls" mapping
stay auditable in one file.

All builders take ``dialog`` so they can call back into
``_update_preview`` / ``_update_form_preview`` — those live on the
dialog because they touch widgets outside the expander.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ....utils.accessibility import set_label as a11y_label
from ....utils.translation_utils import _
from ..base_dialog import BaseDialog

if TYPE_CHECKING:
    from .command_editor_dialog import CommandEditorDialog


# ── static command text (field_type == "command_text") ──────


def add_cmd_text_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
    type_icons: Dict[str, str],
    field_type: str,
) -> None:
    """Rows for a static literal command fragment."""
    text_row = Adw.EntryRow(title=_("Command text"))
    text_row.set_text(str(field_data.get("default", "")))

    def on_cmd_text_changed(r):
        val = r.get_text()
        field_data["default"] = val
        expander.set_title(
            f"{type_icons.get(field_type, '💬')} {val}"
            if val
            else f"{type_icons.get(field_type, '💬')} {_('(empty)')}"
        )
        dialog._update_preview()

    text_row.connect("changed", on_cmd_text_changed)
    expander.add_row(text_row)


# ── base rows shared by non-static fields ───────────────────


def add_field_base_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
    type_icons: Dict[str, str],
    field_type: str,
) -> None:
    """ID + Label rows shown above every non-static field's controls."""
    id_row = Adw.EntryRow(title=_("ID"))
    id_row.set_text(field_data.get("id", ""))

    def on_id_changed(row):
        new_id = row.get_text()
        field_data["id"] = new_id
        expander.set_title(f"{type_icons.get(field_type, '📝')} {{{new_id}}}")
        dialog._update_preview()

    id_row.connect("changed", on_id_changed)
    expander.add_row(id_row)

    label_row = Adw.EntryRow(title=_("Label"))
    label_row.set_text(field_data.get("label", ""))

    def on_label_changed(r):
        field_data["label"] = r.get_text()
        dialog._update_preview()
        dialog._update_form_preview()

    label_row.connect("changed", on_label_changed)
    expander.add_row(label_row)


# ── plain-text / number ──────────────────────────────────────


def add_text_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    placeholder_row = Adw.EntryRow(title=_("Placeholder"))
    placeholder_row.set_text(str(field_data.get("placeholder", "")))
    placeholder_row.connect(
        "changed",
        lambda r: (
            field_data.update({"placeholder": r.get_text()}),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(placeholder_row)

    default_row = Adw.EntryRow(title=_("Default"))
    default_row.set_text(str(field_data.get("default", "")))
    default_row.connect(
        "changed",
        lambda r: (
            field_data.update({"default": r.get_text()}),
            dialog._update_preview(),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(default_row)


def add_number_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    default_row = Adw.EntryRow(title=_("Default"))
    default_row.set_text(str(field_data.get("default", "")))
    default_row.connect(
        "changed",
        lambda r: (
            field_data.update({"default": r.get_text()}),
            dialog._update_preview(),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(default_row)


# ── switch ───────────────────────────────────────────────────


def add_switch_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    on_row = Adw.EntryRow(title=_("On value"))
    on_row.set_text(field_data.get("command_flag", ""))
    on_row.connect(
        "changed",
        lambda r: (
            field_data.update({"command_flag": r.get_text()}),
            dialog._update_preview(),
        ),
    )
    expander.add_row(on_row)

    off_row = Adw.EntryRow(title=_("Off value"))
    off_row.set_text(field_data.get("off_value", ""))
    off_row.connect(
        "changed",
        lambda r: (
            field_data.update({"off_value": r.get_text()}),
            dialog._update_preview(),
        ),
    )
    expander.add_row(off_row)

    default_row = Adw.SwitchRow(title=_("Default on"))
    default_row.set_active(bool(field_data.get("default", False)))
    default_row.connect(
        "notify::active",
        lambda r, _p: (
            field_data.update({"default": r.get_active()}),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(default_row)


# ── dropdown (options list + per-option row) ────────────────


def add_dropdown_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    options_header = Adw.ActionRow(title=_("Options"))
    add_btn = Gtk.Button(
        icon_name="list-add-symbolic",
        css_classes=[BaseDialog.CSS_CLASS_FLAT, BaseDialog.CSS_CLASS_CIRCULAR],
        valign=Gtk.Align.CENTER,
    )
    a11y_label(add_btn, _("Add option"))
    options_header.add_suffix(add_btn)
    expander.add_row(options_header)

    listbox = Gtk.ListBox(
        selection_mode=Gtk.SelectionMode.NONE,
        css_classes=[BaseDialog.CSS_CLASS_BOXED_LIST],
    )
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        margin_start=12,
        margin_end=12,
        margin_bottom=8,
    )
    box.append(listbox)
    expander.add_row(box)

    def sync_cb():
        sync_dropdown_options(dialog, listbox, field_data)

    def add_opt(val: str = "", label: str = "") -> None:
        listbox.append(create_option_row(val, label, sync_cb, listbox))

    add_btn.connect("clicked", lambda _b: (add_opt(), sync_cb()))

    for opt in field_data.get("options", []):
        if isinstance(opt, (tuple, list)) and len(opt) >= 2:
            add_opt(str(opt[0]), str(opt[1]))
        else:
            add_opt(str(opt), str(opt))


def sync_dropdown_options(
    dialog: "CommandEditorDialog",
    listbox: Gtk.ListBox,
    field_data: Dict,
) -> None:
    """Harvest current option rows from ``listbox`` into ``field_data["options"]``."""
    opts = []
    idx = 0
    while row := listbox.get_row_at_index(idx):
        if hasattr(row, "_val_entry") and hasattr(row, "_lbl_entry"):
            val = row._val_entry.get_text().strip()
            label = row._lbl_entry.get_text().strip()
            if val or label:
                opts.append((val or label, label or val))
        idx += 1
    field_data["options"] = opts
    dialog._update_form_preview()


def create_option_row(
    value: str,
    label: str,
    sync_cb: Callable[[], None],
    listbox: Gtk.ListBox,
) -> Adw.ActionRow:
    """Build one option row with value/label entries + up/down/remove buttons."""
    row = Adw.ActionRow()
    ve = Gtk.Entry(
        placeholder_text=_("Value"), width_chars=10, valign=Gtk.Align.CENTER
    )
    ve.set_text(value)
    a11y_label(ve, _("Option value"))
    le = Gtk.Entry(
        placeholder_text=_("Label"),
        width_chars=15,
        valign=Gtk.Align.CENTER,
        hexpand=True,
    )
    le.set_text(label)
    a11y_label(le, _("Option label"))

    def move(delta: int) -> None:
        idx = row.get_index()
        new_idx = idx + delta
        if new_idx >= 0:
            listbox.remove(row)
            listbox.insert(row, new_idx)
            sync_cb()

    u = Gtk.Button(
        icon_name="go-up-symbolic",
        css_classes=[BaseDialog.CSS_CLASS_FLAT, BaseDialog.CSS_CLASS_CIRCULAR],
    )
    a11y_label(u, _("Move up"))
    d = Gtk.Button(
        icon_name="go-down-symbolic",
        css_classes=[BaseDialog.CSS_CLASS_FLAT, BaseDialog.CSS_CLASS_CIRCULAR],
    )
    a11y_label(d, _("Move down"))
    r = Gtk.Button(
        icon_name="user-trash-symbolic",
        css_classes=[
            BaseDialog.CSS_CLASS_FLAT,
            BaseDialog.CSS_CLASS_CIRCULAR,
            BaseDialog.CSS_CLASS_ERROR,
        ],
    )
    a11y_label(r, _("Remove option"))

    u.connect("clicked", lambda _b: move(-1))
    d.connect("clicked", lambda _b: move(1))
    r.connect("clicked", lambda _b: (listbox.remove(row), sync_cb()))
    ve.connect("changed", lambda _e: sync_cb())
    le.connect("changed", lambda _e: sync_cb())

    row.add_prefix(ve)
    row.add_suffix(le)
    row.add_suffix(u)
    row.add_suffix(d)
    row.add_suffix(r)
    row._val_entry, row._lbl_entry = ve, le
    return row


# ── path / password / textarea ───────────────────────────────


def add_path_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    row = Adw.EntryRow(title=_("Default"))
    row.set_text(str(field_data.get("default", "")))
    row.connect(
        "changed",
        lambda r: (
            field_data.update({"default": r.get_text()}),
            dialog._update_preview(),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(row)


def add_password_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    row = Adw.EntryRow(title=_("Placeholder"))
    row.set_text(str(field_data.get("placeholder", "")))
    row.connect(
        "changed",
        lambda r: (
            field_data.update({"placeholder": r.get_text()}),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(row)


def add_textarea_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    add_text_rows(dialog, expander, field_data)
    row = Adw.SpinRow.new_with_range(2, 20, 1)
    row.set_title(_("Rows"))
    row.set_value(field_data.get("rows", 4))
    row.connect(
        BaseDialog.SIGNAL_NOTIFY_VALUE,
        lambda r, _p: field_data.update({"rows": int(r.get_value())}),
    )
    expander.add_row(row)


# ── slider / datetime / color ────────────────────────────────


def add_slider_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    def add_spin(title: str, key: str, fallback: float) -> None:
        r = Adw.SpinRow.new_with_range(-9999, 9999, 1)
        r.set_title(title)
        r.set_value(float(field_data.get(key, fallback)))
        r.connect(
            BaseDialog.SIGNAL_NOTIFY_VALUE,
            lambda r, _p: (
                field_data.update({key: r.get_value()}),
                dialog._update_form_preview(),
            ),
        )
        expander.add_row(r)

    add_spin(_("Minimum"), "min_value", 0)
    add_spin(_("Maximum"), "max_value", 100)
    add_spin(_("Step"), "step", 1)

    default_row = Adw.EntryRow(title=_("Default"))
    default_row.set_text(str(field_data.get("default", "50")))
    default_row.connect(
        "changed",
        lambda r: (
            field_data.update({"default": r.get_text()}),
            dialog._update_preview(),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(default_row)


def add_datetime_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    r = Adw.EntryRow(title=_("Format"))
    r.set_text(str(field_data.get("format", "%Y-%m-%d %H:%M")))
    r.connect(
        "changed",
        lambda r: (
            field_data.update({"format": r.get_text()}),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(r)


def add_color_rows(
    dialog: "CommandEditorDialog",
    expander: Adw.ExpanderRow,
    field_data: Dict,
) -> None:
    r = Adw.ComboRow(title=_("Format"))
    r.set_model(
        Gtk.StringList.new(
            [_("Hex (#RRGGBB)"), _("RGB (R,G,B)"), _("None")]
        )
    )
    r.set_selected(
        {"hex": 0, "rgb": 1}.get(field_data.get("color_format", "hex"), 2)
    )
    r.connect(
        "notify::selected",
        lambda r, _p: (
            field_data.update(
                {"color_format": ["hex", "rgb", "none"][r.get_selected()]}
            ),
            dialog._update_preview(),
        ),
    )
    expander.add_row(r)

    default_row = Adw.EntryRow(title=_("Default Hex"))
    default_row.set_text(str(field_data.get("default", "#ffffff")))
    default_row.connect(
        "changed",
        lambda r: (
            field_data.update({"default": r.get_text()}),
            dialog._update_preview(),
            dialog._update_form_preview(),
        ),
    )
    expander.add_row(default_row)
