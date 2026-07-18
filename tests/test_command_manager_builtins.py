"""Smoke tests for the builtin-commands catalog."""


from ashyterm.data.command_manager_builtins import (
    get_builtin_commands as builtins_entrypoint,
)
from ashyterm.data.command_manager_models import (
    CommandButton,
    DisplayMode,
    ExecutionMode,
    get_builtin_commands as model_entrypoint,
)


class TestBuiltinCommands:
    def test_returns_non_empty_list_of_command_buttons(self):
        commands = builtins_entrypoint()
        assert len(commands) > 0
        for cmd in commands:
            assert isinstance(cmd, CommandButton)

    def test_every_builtin_is_flagged_as_builtin(self):
        for cmd in builtins_entrypoint():
            assert cmd.is_builtin is True, f"{cmd.id} should have is_builtin=True"

    def test_ids_are_unique(self):
        ids = [c.id for c in builtins_entrypoint()]
        assert len(ids) == len(set(ids))

    def test_ids_follow_builtin_prefix_convention(self):
        # The manager relies on this prefix to distinguish builtins
        # from user commands in a few places.
        for cmd in builtins_entrypoint():
            assert cmd.id.startswith("builtin_"), f"{cmd.id} missing prefix"

    def test_every_command_declares_a_category(self):
        for cmd in builtins_entrypoint():
            assert cmd.category, f"{cmd.id} has no category"

    def test_expected_flagship_commands_are_present(self):
        ids = {c.id for c in builtins_entrypoint()}
        # A subset of well-known builtins we don't want to silently lose.
        assert "builtin_find" in ids
        assert "builtin_ls" in ids
        assert "builtin_compress" in ids
        assert "builtin_extract" in ids

    def test_commands_have_valid_enum_members(self):
        for cmd in builtins_entrypoint():
            assert isinstance(cmd.display_mode, DisplayMode)
            assert isinstance(cmd.execution_mode, ExecutionMode)

    def test_form_fields_have_template_keys(self):
        """Every form field must have a template_key so build_command
        can substitute it — otherwise the builtin is unreachable via
        the form dialog.
        """
        for cmd in builtins_entrypoint():
            if cmd.execution_mode != ExecutionMode.SHOW_DIALOG:
                continue
            for field in cmd.form_fields:
                assert field.template_key, (
                    f"{cmd.id}/{field.id} is missing template_key"
                )


# ── delegator contract ──────────────────────────────────────


class TestDelegator:
    def test_models_entrypoint_delegates_to_builtins_module(self):
        """The legacy import path
        ``from ashyterm.data.command_manager_models import get_builtin_commands``
        must still work and return the same command set.
        """
        from_models = {c.id for c in model_entrypoint()}
        from_builtins = {c.id for c in builtins_entrypoint()}
        assert from_models == from_builtins
