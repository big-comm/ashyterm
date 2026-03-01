# ashyterm/ui/dialogs/quick_connect_dialog.py
"""Quick Connect dialog — connect to SSH hosts from ~/.ssh/config."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ...sessions.models import SessionItem
from ...utils.ssh_config_parser import SSHConfigParser, SSHConfigHost
from ...utils.translation_utils import _


class QuickConnectDialog(Adw.Dialog):
    """Dialog listing SSH hosts from ~/.ssh/config for quick connection."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._hosts: list[SSHConfigHost] = []
        self._selected_host: SSHConfigHost | None = None

        self.set_title(_("Quick Connect"))
        self.set_content_width(420)
        self.set_content_height(460)

        self._build_ui()
        self._load_hosts()

    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        toolbar.set_content(content)

        # Search entry
        self._search_entry = Gtk.SearchEntry(
            placeholder_text=_("Filter hosts…"),
            hexpand=True,
        )
        self._search_entry.add_css_class("search-entry-inline")
        self._search_entry.set_margin_start(12)
        self._search_entry.set_margin_end(12)
        self._search_entry.set_margin_top(8)
        self._search_entry.set_margin_bottom(8)
        self._search_entry.connect("search-changed", self._on_search_changed)
        content.append(self._search_entry)

        # Scrolled list
        scrolled = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        content.append(scrolled)

        self._list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["boxed-list"],
        )
        self._list_box.set_margin_start(12)
        self._list_box.set_margin_end(12)
        self._list_box.set_margin_bottom(12)
        scrolled.set_child(self._list_box)

        # Empty state
        self._empty_status = Adw.StatusPage(
            title=_("No SSH Hosts Found"),
            description=_("Add hosts to ~/.ssh/config to see them here."),
            icon_name="network-server-symbolic",
            vexpand=True,
        )
        self._empty_status.set_visible(False)
        content.append(self._empty_status)

    def _load_hosts(self) -> None:
        parser = SSHConfigParser()
        config_path = Path("~/.ssh/config")
        self._hosts = parser.parse(config_path)
        self._populate_list(self._hosts)

    def _populate_list(self, hosts: list[SSHConfigHost]) -> None:
        # Clear existing rows
        while (child := self._list_box.get_first_child()) is not None:
            self._list_box.remove(child)

        if not hosts:
            self._list_box.set_visible(False)
            self._empty_status.set_visible(True)
            return

        self._list_box.set_visible(True)
        self._empty_status.set_visible(False)

        for host in hosts:
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(host.alias),
                activatable=True,
            )
            subtitle_parts = []
            if host.user and host.hostname:
                subtitle_parts.append(f"{host.user}@{host.hostname}")
            elif host.hostname:
                subtitle_parts.append(host.hostname)
            if host.port and host.port != 22:
                subtitle_parts.append(f":{host.port}")
            if subtitle_parts:
                row.set_subtitle(GLib.markup_escape_text("".join(subtitle_parts)))

            row.add_suffix(
                Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"])
            )

            row.connect("activated", self._on_host_activated, host)
            self._list_box.append(row)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip().lower()
        if not query:
            self._populate_list(self._hosts)
            return
        filtered = [
            h
            for h in self._hosts
            if query in h.alias.lower()
            or (h.hostname and query in h.hostname.lower())
            or (h.user and query in h.user.lower())
        ]
        self._populate_list(filtered)

    def _on_host_activated(self, _row, host: SSHConfigHost) -> None:
        self.close()
        session = SessionItem(
            name=host.alias,
            session_type="ssh",
            host=host.hostname or host.alias,
            user=host.user or "",
            port=host.port or 22,
            auth_type="key",
            auth_value=host.identity_file or "",
            x11_forwarding=host.forward_x11 or False,
            source="ssh_config",
        )
        tab_manager = getattr(self.window, "tab_manager", None)
        if tab_manager:
            tab_manager.create_ssh_tab(session)
