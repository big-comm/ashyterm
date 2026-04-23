# ashyterm/ui/dialogs/session_edit_port_forward.py
"""Port forwarding UI and logic for SessionEditDialog.

Extracted to enable isolated testing and reduce SessionEditDialog size.
"""

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ...utils.translation_utils import _
from .base_dialog import BaseDialog
from .session_edit_validators import validate_port_forward
from ..widgets.action_rows import ManagedListRow


# ── List Management ────────────────────────────────────────────────

def refresh_port_forward_list(
    port_forward_list: Gtk.ListBox,
    port_forwardings: list[dict],
    on_edit: Optional[Callable] = None,
    on_delete: Optional[Callable] = None,
) -> None:
    """Rebuild the port forward list from data.

    Args:
        port_forward_list: The ListBox to populate.
        port_forwardings: List of port forward dicts.
        on_edit: Callback(index) for edit clicks.
        on_delete: Callback(index) for delete clicks.
    """
    if not port_forward_list:
        return

    child = port_forward_list.get_first_child()
    while child:
        next_child = child.get_next_sibling()
        port_forward_list.remove(child)
        child = next_child

    if not port_forwardings:
        placeholder_row = Gtk.ListBoxRow()
        placeholder_row.set_selectable(False)
        placeholder_row.set_activatable(False)
        label = Gtk.Label(
            label=_("No port forwards configured."),
            xalign=0,
        )
        label.add_css_class(BaseDialog.CSS_CLASS_DIM_LABEL)
        placeholder_row.set_child(label)
        port_forward_list.append(placeholder_row)
        return

    for index, tunnel in enumerate(port_forwardings):
        remote_host_display = tunnel.get("remote_host") or _("SSH Server Address")
        subtitle_text = _(
            "{local_host}:{local_port} → {remote_host}:{remote_port}"
        ).format(
            local_host=tunnel.get("local_host", "localhost"),
            local_port=tunnel.get("local_port", 0),
            remote_host=remote_host_display,
            remote_port=tunnel.get("remote_port", 0),
        )

        row = ManagedListRow(
            title=tunnel.get("name", _("Tunnel")),
            subtitle=subtitle_text,
            show_reorder=False, show_actions=True, show_toggle=False,
        )
        if on_edit:
            row.connect("edit-clicked", on_edit, index)
        if on_delete:
            row.connect("delete-clicked", on_delete, index)
        port_forward_list.append(row)


# ── Dialog ─────────────────────────────────────────────────────────

def show_port_forward_dialog(
    parent: Gtk.Window,
    existing: Optional[dict] = None,
) -> Optional[dict]:
    """Show modal dialog to add/edit a port forward rule.

    Returns dict with rule data or None if cancelled.
    """
    is_edit = existing is not None

    dialog = Adw.Window(
        transient_for=parent, modal=True,
        default_width=600, default_height=600,
    )
    dialog.set_title(_("Edit Port Forward") if is_edit else _("Add Port Forward"))

    toolbar_view = Adw.ToolbarView()
    dialog.set_content(toolbar_view)

    header_bar = Adw.HeaderBar()
    header_bar.set_show_end_title_buttons(True)
    header_bar.set_show_start_title_buttons(False)

    cancel_button = Gtk.Button(label=_("Cancel"))
    cancel_button.connect("clicked", lambda b: dialog.close())
    header_bar.pack_start(cancel_button)

    save_button = Gtk.Button(
        label=_("Save"), css_classes=[BaseDialog.CSS_CLASS_SUGGESTED],
    )
    header_bar.pack_end(save_button)
    toolbar_view.add_top_bar(header_bar)

    widgets = _create_port_forward_ui(toolbar_view, existing)
    result: Optional[dict] = None

    def on_save(_button):
        nonlocal result
        data = _get_port_forward_data(widgets)
        errors = validate_port_forward(data)
        if errors:
            # Show error inline — caller can override with custom dialog
            err_dialog = Adw.AlertDialog(
                heading=_("Invalid Port Forward"),
                body="\n".join(errors),
            )
            err_dialog.add_response("ok", _("OK"))
            err_dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            err_dialog.present(dialog)
            return

        result = data
        dialog.close()

    save_button.connect("clicked", on_save)

    loop = GLib.MainLoop()
    dialog.connect("close-request", lambda _: loop.quit())
    dialog.present()
    loop.run()

    return result


def _create_port_forward_ui(
    toolbar_view: Adw.ToolbarView, existing: Optional[dict],
) -> dict:
    """Create UI content for port forward dialog. Returns widgets dict."""
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    toolbar_view.set_content(scrolled)

    prefs_page = Adw.PreferencesPage()
    scrolled.set_child(prefs_page)

    existing = existing or {}
    widgets = {}

    # Name
    group = Adw.PreferencesGroup()
    prefs_page.add(group)
    widgets["name"] = Adw.EntryRow(title=_("Tunnel Name"))
    widgets["name"].set_text(existing.get("name", ""))
    group.add(widgets["name"])

    # Local
    local_group = Adw.PreferencesGroup(title=_("Local Settings"))
    prefs_page.add(local_group)

    widgets["local_host"] = Adw.EntryRow(title=_("Local Address"))
    widgets["local_host"].set_text(existing.get("local_host", "localhost"))
    local_group.add(widgets["local_host"])

    widgets["local_port"] = Adw.SpinRow.new_with_range(1, 65535, 1)
    widgets["local_port"].set_title(_("Local Port"))
    widgets["local_port"].set_subtitle(
        _("Port on your machine (1025-65535 recommended)"),
    )
    widgets["local_port"].set_value(existing.get("local_port", 8080))
    local_group.add(widgets["local_port"])

    # Remote
    remote_group = Adw.PreferencesGroup(title=_("Remote Settings"))
    prefs_page.add(remote_group)

    use_custom = bool(existing.get("remote_host"))
    widgets["remote_toggle"] = Adw.SwitchRow(
        title=_("Use Custom Remote Address"),
        subtitle=_("Leave off to use the SSH server as target"),
        active=use_custom,
    )
    remote_group.add(widgets["remote_toggle"])

    widgets["remote_host"] = Adw.EntryRow(title=_("Remote Address"))
    widgets["remote_host"].set_text(existing.get("remote_host", ""))
    widgets["remote_host"].set_visible(use_custom)
    remote_group.add(widgets["remote_host"])

    def on_remote_toggle(switch, _p):
        widgets["remote_host"].set_visible(switch.get_active())

    from .base_dialog import BaseDialog
    widgets["remote_toggle"].connect(
        BaseDialog.SIGNAL_NOTIFY_ACTIVE, on_remote_toggle,
    )

    widgets["remote_port"] = Adw.SpinRow.new_with_range(1, 65535, 1)
    widgets["remote_port"].set_title(_("Remote Port"))
    widgets["remote_port"].set_subtitle(_("Port on the remote host (1-65535)"))
    widgets["remote_port"].set_value(existing.get("remote_port", 80))
    remote_group.add(widgets["remote_port"])

    return widgets


def _get_port_forward_data(widgets: dict) -> dict:
    """Extract data from port forward widgets."""
    is_custom_remote = widgets["remote_toggle"].get_active()
    return {
        "name": widgets["name"].get_text().strip() or _("Tunnel"),
        "local_host": widgets["local_host"].get_text().strip() or "localhost",
        "local_port": int(widgets["local_port"].get_value()),
        "remote_port": int(widgets["remote_port"].get_value()),
        "remote_host": widgets["remote_host"].get_text().strip()
            if is_custom_remote else "",
    }
