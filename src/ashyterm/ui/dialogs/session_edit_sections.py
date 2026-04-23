# ashyterm/ui/dialogs/session_edit_sections.py
"""Section builders for SessionEditDialog.

The dialog is laid out as a vertical stack of :class:`Adw.PreferencesGroup`
sections. Each builder here creates one section and attaches the
widgets it produces back onto the owning dialog as instance
attributes (``dialog.folder_combo``, ``dialog.output_highlighting_row``,
…). Splitting them out drops ~200 lines of widget boilerplate from
the dialog and lets each section's layout live in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...utils.accessibility import set_label as a11y_label
from ...utils.platform import get_ssh_directory
from ...utils.translation_utils import _
from ..widgets.bash_text_view import BashTextView
from .base_dialog import BaseDialog
from .session_edit_form import tri_state_to_selected

if TYPE_CHECKING:
    from .session_edit_dialog import SessionEditDialog


_HIGHLIGHTING_ATTRS: tuple[str, ...] = (
    "output_highlighting",
    "command_specific_highlighting",
    "cat_colorization",
    "shell_input_highlighting",
)


def create_tristate_combo_row(
    *,
    title: str,
    subtitle: str,
    initial_value: Optional[bool],
    on_changed: Callable,
) -> Adw.ComboRow:
    """Build an Adw.ComboRow bound to an Automatic/Enabled/Disabled tri-state.

    The widget presents three items so the user can opt out of the
    global highlighting preference per-session (Automatic) without
    losing the ability to force either state.
    """
    row = Adw.ComboRow(title=title, subtitle=subtitle)
    row.set_model(
        Gtk.StringList.new([_("Automatic"), _("Enabled"), _("Disabled")])
    )
    row.set_selected(tri_state_to_selected(initial_value))
    row.connect(BaseDialog.SIGNAL_NOTIFY_SELECTED, on_changed)
    return row


def add_folder_expander(
    dialog: "SessionEditDialog", group: Adw.PreferencesGroup
) -> None:
    """Attach the "Organization → Folder" collapsible row to ``group``.

    Builds a display map ``{indented_name: path}`` so the combo can
    show nesting visually. The active session's folder is
    pre-selected.
    """
    folder_expander = Adw.ExpanderRow(
        title=_("Organization"),
        subtitle=_("Choose where to store this session"),
    )
    folder_row = Adw.ComboRow(
        title=_("Folder"),
        subtitle=_("Select a folder to organize this session"),
    )

    folder_model = Gtk.StringList()
    folder_model.append(_("Root"))
    dialog.folder_paths_map = {_("Root"): ""}

    folders = sorted(
        (
            dialog.folder_store.get_item(i)
            for i in range(dialog.folder_store.get_n_items())
        ),
        key=lambda f: f.path,
    )
    for folder in folders:
        display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
        folder_model.append(display_name)
        dialog.folder_paths_map[display_name] = folder.path

    folder_row.set_model(folder_model)

    selected_index = 0
    for i, (_display, path_val) in enumerate(dialog.folder_paths_map.items()):
        if path_val == dialog.editing_session.folder_path:
            selected_index = i
            break
    folder_row.set_selected(selected_index)
    folder_row.connect(
        BaseDialog.SIGNAL_NOTIFY_SELECTED, dialog._on_folder_changed
    )
    dialog.folder_combo = folder_row

    folder_expander.add_row(folder_row)
    group.add(folder_expander)


def add_highlighting_expander(
    dialog: "SessionEditDialog", group: Adw.PreferencesGroup
) -> None:
    """Attach the per-session highlighting overrides block to ``group``.

    Four tri-state combos (output, command-specific, cat, shell input)
    gated by a master switch. The expander auto-opens and the switch
    auto-enables when any existing override is non-default — that's
    the signal the user has already customized this session.
    """
    dialog._updating_highlighting_ui = False

    expander = Adw.ExpanderRow(
        title=_("Highlighting"),
        subtitle=_("Override global highlighting preferences per session"),
    )
    warning_row = Adw.ActionRow(
        title=_("Experimental Feature"),
        subtitle=_(
            "Per-session highlighting overrides are experimental and may change."
        ),
    )
    warning_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
    expander.add_row(warning_row)

    has_custom = any(
        getattr(dialog.editing_session, key, None) is not None
        for key in _HIGHLIGHTING_ATTRS
    )

    dialog.highlighting_customize_switch = Adw.SwitchRow(
        title=_("Customize highlighting for this session"),
        subtitle=_(
            "When off, this session uses the global highlighting settings"
        ),
    )
    dialog.highlighting_customize_switch.set_active(has_custom)
    dialog.highlighting_customize_switch.connect(
        BaseDialog.SIGNAL_NOTIFY_ACTIVE,
        dialog._on_highlighting_customize_switch_changed,
    )
    expander.add_row(dialog.highlighting_customize_switch)

    dialog.output_highlighting_row = create_tristate_combo_row(
        title=_("Output Highlighting"),
        subtitle=_("Enable/disable output highlighting for this session"),
        initial_value=getattr(dialog.editing_session, "output_highlighting", None),
        on_changed=dialog._on_highlighting_override_changed,
    )
    expander.add_row(dialog.output_highlighting_row)

    dialog.command_specific_highlighting_row = create_tristate_combo_row(
        title=_("Command-Specific Highlighting"),
        subtitle=_("Use context-aware rules for specific commands"),
        initial_value=getattr(
            dialog.editing_session, "command_specific_highlighting", None
        ),
        on_changed=dialog._on_highlighting_override_changed,
    )
    expander.add_row(dialog.command_specific_highlighting_row)

    dialog.cat_colorization_row = create_tristate_combo_row(
        title=_("{} Command Colorization").format("cat"),
        subtitle=_("Colorize file content output using syntax highlighting"),
        initial_value=getattr(dialog.editing_session, "cat_colorization", None),
        on_changed=dialog._on_highlighting_override_changed,
    )
    expander.add_row(dialog.cat_colorization_row)

    dialog.shell_input_highlighting_row = create_tristate_combo_row(
        title=_("Shell Input Highlighting"),
        subtitle=_("Highlight commands as you type at the shell prompt"),
        initial_value=getattr(
            dialog.editing_session, "shell_input_highlighting", None
        ),
        on_changed=dialog._on_highlighting_override_changed,
    )
    expander.add_row(dialog.shell_input_highlighting_row)

    dialog._set_highlighting_overrides_visible(has_custom)
    expander.set_expanded(has_custom)

    group.add(expander)


def create_local_terminal_section(
    dialog: "SessionEditDialog", parent: Adw.PreferencesPage
) -> None:
    """Add the Local Terminal preferences page section.

    Two rows: "Start in Folder" (entry + browse button) and "Startup
    Commands" (collapsible ``BashTextView`` so the user can stash a
    short snippet without expanding the whole section).
    """
    local_group = Adw.PreferencesGroup()

    # ── Start-in-folder row ───────────────────────────────────
    working_dir_row = Adw.ActionRow(
        title=_("Start in Folder"),
        subtitle=_("Start the terminal in this folder"),
    )
    working_dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    working_dir_box.set_valign(Gtk.Align.CENTER)
    working_dir_box.set_hexpand(True)

    dialog.local_working_dir_entry = Gtk.Entry(
        text=dialog.editing_session.local_working_directory or "",
        placeholder_text=_("Default (Home directory)"),
        hexpand=True,
        width_chars=25,
    )
    a11y_label(dialog.local_working_dir_entry, _("Working directory"))
    dialog.local_working_dir_entry.connect(
        "changed", dialog._on_local_working_dir_changed
    )

    browse_button = Gtk.Button(
        icon_name="folder-open-symbolic",
        tooltip_text=_("Browse for folder"),
        css_classes=[BaseDialog.CSS_CLASS_FLAT],
    )
    browse_button.set_valign(Gtk.Align.CENTER)
    browse_button.connect("clicked", dialog._on_browse_working_dir_clicked)

    working_dir_box.append(dialog.local_working_dir_entry)
    working_dir_box.append(browse_button)
    working_dir_row.add_suffix(working_dir_box)
    local_group.add(working_dir_row)

    # ── Startup commands (collapsible BashTextView) ───────────
    startup_expander = Adw.ExpanderRow(
        title=_("Startup Commands"),
        subtitle=_("Commands executed when the terminal starts"),
    )
    startup_container = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, spacing=0
    )

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_min_content_height(130)
    scrolled.set_max_content_height(220)
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.add_css_class("startup-commands-container")

    dialog.local_startup_command_view = BashTextView(
        auto_resize=False, min_lines=3, max_lines=10
    )
    dialog.local_startup_command_view.set_top_margin(10)
    dialog.local_startup_command_view.set_bottom_margin(10)
    dialog.local_startup_command_view.set_left_margin(12)
    dialog.local_startup_command_view.set_right_margin(12)
    dialog.local_startup_command_view.add_css_class("startup-commands-text")

    dialog.local_startup_command_view.set_text(
        dialog.editing_session.local_startup_command or ""
    )
    dialog.local_startup_command_view.get_buffer().connect(
        "changed", dialog._on_local_startup_command_changed
    )
    dialog._apply_bash_colors(dialog.local_startup_command_view)

    scrolled.set_child(dialog.local_startup_command_view)
    startup_container.append(scrolled)
    startup_expander.add_row(startup_container)

    # Auto-expand when the session already has startup commands so
    # the user sees them without needing to click the expander.
    has_commands = bool(
        dialog.editing_session.local_startup_command
        and dialog.editing_session.local_startup_command.strip()
    )
    startup_expander.set_expanded(has_commands)
    local_group.add(startup_expander)

    dialog.startup_commands_group = None
    dialog.local_terminal_group = local_group
    parent.add(local_group)


def _add_ssh_identity_rows(
    dialog: "SessionEditDialog", ssh_group: Adw.PreferencesGroup
) -> None:
    """Username + host + port + auth-method + key-path + password rows.

    Stored on the dialog under both the new row attribute names
    (``user_row``, ``host_row``, …) and their legacy aliases
    (``user_entry`` = ``user_row``, etc.) so nothing downstream breaks.
    """
    dialog.user_row = dialog._create_entry_row(
        title=_("Username"),
        text=dialog.editing_session.user or "",
        on_changed=dialog._on_user_changed,
    )
    ssh_group.add(dialog.user_row)
    dialog.user_entry = dialog.user_row

    dialog.host_row = dialog._create_entry_row(
        title=_("Server Address"),
        text=dialog.editing_session.host or "",
        on_changed=dialog._on_host_changed,
    )
    ssh_group.add(dialog.host_row)
    dialog.host_entry = dialog.host_row

    dialog.port_row = dialog._create_spin_row(
        title=_("Port"),
        value=dialog.editing_session.port or 22,
        min_val=1,
        max_val=65535,
        on_changed=lambda val: dialog._on_port_changed(val),
    )
    ssh_group.add(dialog.port_row)
    dialog.port_entry = dialog.port_row

    # Auth method. Password is the default for new sessions because it
    # needs the fewest clicks — the user can switch to SSH Key if they
    # set one up.
    dialog.auth_combo = Adw.ComboRow(
        title=_("Authentication Method"),
        subtitle=_("Choose how to authenticate with the server"),
    )
    dialog.auth_combo.set_model(Gtk.StringList.new([_("SSH Key"), _("Password")]))
    if dialog.is_new_item:
        dialog.auth_combo.set_selected(1)
    else:
        dialog.auth_combo.set_selected(
            0 if dialog.editing_session.uses_key_auth() else 1
        )
    dialog.auth_combo.connect(
        BaseDialog.SIGNAL_NOTIFY_SELECTED, dialog._on_auth_changed
    )
    ssh_group.add(dialog.auth_combo)

    # Key path row: entry + Browse button.
    key_value = (
        dialog.editing_session.auth_value
        if dialog.editing_session.uses_key_auth()
        else ""
    )
    dialog.key_row = Adw.ActionRow(
        title=_("SSH Key File"),
        subtitle=_("Path to your private key file"),
    )
    key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    key_box.set_valign(Gtk.Align.CENTER)
    key_box.set_hexpand(True)

    dialog.key_path_entry = Gtk.Entry(
        text=key_value,
        placeholder_text=f"{get_ssh_directory()}/id_rsa",
        hexpand=True,
        width_chars=30,
    )
    a11y_label(dialog.key_path_entry, _("SSH key file path"))
    dialog.key_path_entry.connect("changed", dialog._on_key_path_changed)

    dialog.browse_button = Gtk.Button(
        icon_name="folder-open-symbolic",
        tooltip_text=_("Browse for SSH key file"),
        css_classes=[BaseDialog.CSS_CLASS_FLAT],
    )
    dialog.browse_button.set_valign(Gtk.Align.CENTER)
    dialog.browse_button.connect("clicked", dialog._on_browse_key_clicked)

    key_box.append(dialog.key_path_entry)
    key_box.append(dialog.browse_button)
    dialog.key_row.add_suffix(key_box)
    ssh_group.add(dialog.key_row)
    dialog.key_box = dialog.key_row  # legacy alias used for visibility toggles

    # Password. The stored value (from_dict) is only present when the
    # session is on password auth; otherwise we start empty.
    password_value = (
        dialog.editing_session.auth_value
        if dialog.editing_session.uses_password_auth()
        else ""
    )
    dialog.password_row = Adw.PasswordEntryRow(title=_("Password"))
    dialog.password_row.set_text(password_value)
    dialog.password_row.connect("changed", dialog._on_password_changed)
    ssh_group.add(dialog.password_row)
    dialog.password_entry = dialog.password_row
    dialog.password_box = dialog.password_row

    # Warn the user when the OS doesn't have a keyring backend — in
    # that case passwords end up in the settings file.
    from ...utils.crypto import is_encryption_available

    if not is_encryption_available():
        keyring_row = Adw.ActionRow(
            title=_("Password Storage"),
            subtitle=_(
                "System keyring not available - password will be stored "
                "in plain text"
            ),
        )
        keyring_row.add_prefix(
            Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        )
        ssh_group.add(keyring_row)


def _add_post_login_rows(
    dialog: "SessionEditDialog", expander: Adw.ExpanderRow
) -> None:
    """Attach the post-login command toggle and its ``BashTextView``.

    The text view sits inside a container that's hidden/shown by the
    switch so the expander stays compact when post-login is disabled.
    """
    dialog.post_login_switch = Adw.SwitchRow(
        title=_("Run Command After Login"),
        subtitle=_("Execute commands automatically after SSH connects"),
    )
    is_enabled = dialog.editing_session.post_login_command_enabled
    dialog.post_login_switch.set_active(is_enabled)
    dialog.post_login_switch.connect(
        BaseDialog.SIGNAL_NOTIFY_ACTIVE, dialog._on_post_login_toggle
    )
    expander.add_row(dialog.post_login_switch)

    dialog.post_login_command_container = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, spacing=0
    )
    dialog.post_login_command_container.set_visible(is_enabled)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_min_content_height(100)
    scrolled.set_max_content_height(160)
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.add_css_class("startup-commands-container")

    dialog.post_login_text_view = BashTextView(
        auto_resize=False, min_lines=2, max_lines=6
    )
    dialog.post_login_text_view.set_top_margin(10)
    dialog.post_login_text_view.set_bottom_margin(10)
    dialog.post_login_text_view.set_left_margin(12)
    dialog.post_login_text_view.set_right_margin(12)
    dialog.post_login_text_view.add_css_class("startup-commands-text")
    dialog.post_login_text_view.set_text(
        dialog.editing_session.post_login_command or ""
    )
    dialog.post_login_text_view.get_buffer().connect(
        "changed", dialog._on_post_login_command_changed
    )
    dialog._apply_bash_colors(dialog.post_login_text_view)

    scrolled.set_child(dialog.post_login_text_view)
    dialog.post_login_command_container.append(scrolled)
    expander.add_row(dialog.post_login_command_container)

    # Legacy aliases still referenced by the validator / form collector.
    dialog.post_login_entry = dialog.post_login_text_view
    dialog.post_login_expander = None
    dialog.post_login_command_row = dialog.post_login_switch


def _add_forwarding_and_sftp_rows(
    dialog: "SessionEditDialog", expander: Adw.ExpanderRow
) -> None:
    """Attach ProxyJump + X11 forwarding + SFTP toggles and directory rows."""
    dialog.proxy_jump_entry = dialog._create_entry_row(
        title=_("Proxy Jump (e.g. user@bastion or h1,h2)"),
        text=dialog.editing_session.proxy_jump or "",
        on_changed=dialog._on_validated_entry_changed,
    )
    expander.add_row(dialog.proxy_jump_entry)

    dialog.x11_switch = Adw.SwitchRow(
        title=_("Enable X11 Forwarding"),
        subtitle=_("Allow graphical applications from remote server"),
    )
    dialog.x11_switch.set_active(dialog.editing_session.x11_forwarding)
    dialog.x11_switch.connect(
        BaseDialog.SIGNAL_NOTIFY_ACTIVE, dialog._on_x11_toggled
    )
    expander.add_row(dialog.x11_switch)

    dialog.sftp_switch = Adw.SwitchRow(
        title=_("Enable SFTP Session"),
        subtitle=_("Use default directories when opening SFTP"),
    )
    dialog.sftp_switch.set_active(dialog.editing_session.sftp_session_enabled)
    dialog.sftp_switch.connect(
        BaseDialog.SIGNAL_NOTIFY_ACTIVE, dialog._on_sftp_toggle
    )
    expander.add_row(dialog.sftp_switch)

    dialog.sftp_local_entry = dialog._create_entry_row(
        title=_("SFTP Local Directory"),
        text=dialog.editing_session.sftp_local_directory or "",
        on_changed=dialog._on_validated_entry_changed,
    )
    expander.add_row(dialog.sftp_local_entry)
    dialog.sftp_local_row = dialog.sftp_local_entry

    dialog.sftp_remote_entry = dialog._create_entry_row(
        title=_("SFTP Remote Directory"),
        text=dialog.editing_session.sftp_remote_directory or "",
        on_changed=dialog._on_sftp_remote_changed,
    )
    expander.add_row(dialog.sftp_remote_entry)
    dialog.sftp_remote_row = dialog.sftp_remote_entry


def create_ssh_options_group(
    dialog: "SessionEditDialog", ssh_group: Adw.PreferencesGroup
) -> None:
    """Attach the collapsible "SSH Options" expander to ``ssh_group``.

    Layout: post-login + X11 + SFTP toggles + SFTP directory rows +
    port-forwarding widgets. Each sub-block lives in its own helper
    above to keep the flow readable.
    """
    expander = Adw.ExpanderRow(
        title=_("SSH Options"),
        subtitle=_("Post-login commands, forwarding, and SFTP"),
    )

    _add_post_login_rows(dialog, expander)
    dialog.post_login_command_group = ssh_group

    _add_forwarding_and_sftp_rows(dialog, expander)

    # Port forwarding widgets (separate module already; just host them).
    dialog._create_port_forward_widgets_expander(expander)

    ssh_group.add(expander)
    dialog.ssh_options_group = ssh_group

    dialog._update_post_login_command_state()
    dialog._update_sftp_state()


def create_ssh_section(
    dialog: "SessionEditDialog", parent: Adw.PreferencesPage
) -> None:
    """Attach the SSH configuration section to ``parent``.

    Two blocks: identity/auth rows on top of the group, then a
    collapsible "SSH Options" expander with everything else.
    """
    ssh_group = Adw.PreferencesGroup()
    _add_ssh_identity_rows(dialog, ssh_group)

    dialog.ssh_box = ssh_group
    parent.add(ssh_group)

    create_ssh_options_group(dialog, ssh_group)
