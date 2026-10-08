"""Reversible visibility controls for saved and imported sessions."""

from typing import Any

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw, GObject

from ...sessions.models import SessionItem
from ...utils.translation_utils import _


class SessionVisibilityDialog(Adw.PreferencesDialog):
    """Keep every session available while choosing which favorites to display."""

    def __init__(self, parent_window: Any) -> None:
        super().__init__(
            title=_("Gerenciar favoritos"), search_enabled=True,
            content_width=540, content_height=520,
        )
        self._operations = parent_window.session_operations
        page = Adw.PreferencesPage()
        self.add(page)
        group = Adw.PreferencesGroup(
            title=_("Mostrar nos favoritos"),
            description=_("Desative as entradas que deseja ocultar. Ative novamente para reexibi-las."),
        )
        page.add(group)
        sessions = sorted(parent_window.session_store, key=lambda item: item.name.casefold())
        if not sessions:
            group.add(Adw.ActionRow(title=_("Nenhum favorito cadastrado.")))
        for session in sessions:
            row = Adw.SwitchRow(
                title=session.name, subtitle=self._subtitle(session),
                use_markup=False, active=not session.hidden,
            )
            row.connect("notify::active", self._on_visibility_changed, session)
            group.add(row)

    @staticmethod
    def _subtitle(session: SessionItem) -> str:
        if session.is_local():
            location = _("Terminal local")
        else:
            location = f"{session.user}@{session.host}" if session.user else session.host
            if session.port != 22:
                location += f":{session.port}"
        details = [location]
        if session.folder_path:
            details.append(session.folder_path)
        if session.source == "ssh_config":
            details.append(_("Importado do SSH"))
        return " · ".join(details)

    def _on_visibility_changed(
        self, row: Adw.SwitchRow, _param: GObject.ParamSpec, session: SessionItem
    ) -> None:
        hidden = not row.get_active()
        if session.hidden == hidden:
            return
        result = self._operations.set_session_hidden(session, hidden)
        if not result.success:
            row.set_active(not session.hidden)
            self.add_toast(Adw.Toast(title=result.message))
