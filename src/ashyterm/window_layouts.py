"""Window layout mixin."""


class WindowLayoutsMixin:
    """Mixin: thin wrapper around state_manager's layout CRUD."""

    def move_layout(self, layout_name: str, old_folder: str, new_folder: str) -> None:
        self.state_manager.move_layout(layout_name, old_folder, new_folder)
