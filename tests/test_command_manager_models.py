# tests/test_command_manager_models.py
"""Tests for CommandButton, CommandFormField, and CommandButtonManager."""

from unittest.mock import MagicMock, patch

import pytest

# Patch config dependencies before importing the module under test
_mock_config_paths = MagicMock()
_mock_logger = MagicMock()

with patch.dict(
    "sys.modules",
    {
        "ashyterm.settings.config": MagicMock(
            get_config_paths=MagicMock(return_value=_mock_config_paths)
        ),
        "ashyterm.utils.logger": MagicMock(get_logger=MagicMock(return_value=_mock_logger)),
        "ashyterm.utils.translation_utils": MagicMock(_=lambda x: x),
    },
):
    from ashyterm.data.command_manager_models import (
        CommandButton,
        CommandFormField,
        DisplayMode,
        ExecutionMode,
        FieldType,
        generate_id,
    )


# ── CommandFormField ──


class TestCommandFormField:
    def test_creation_defaults(self):
        f = CommandFormField(id="f1", label="Name")
        assert f.id == "f1"
        assert f.label == "Name"
        assert f.field_type == FieldType.TEXT
        assert f.default_value == ""
        assert f.required is False

    def test_to_dict_and_from_dict_roundtrip(self):
        f = CommandFormField(
            id="search",
            label="Search Term",
            field_type=FieldType.TEXT,
            default_value="hello",
            placeholder="type here",
            required=True,
            template_key="q",
        )
        d = f.to_dict()
        assert d["field_type"] == "text"
        assert d["id"] == "search"

        restored = CommandFormField.from_dict(d)
        assert restored.id == f.id
        assert restored.field_type == FieldType.TEXT
        assert restored.default_value == "hello"
        assert restored.required is True
        assert restored.template_key == "q"

    def test_dropdown_options_roundtrip(self):
        f = CommandFormField(
            id="level",
            label="Level",
            field_type=FieldType.DROPDOWN,
            options=[("1", "Low"), ("2", "High")],
        )
        d = f.to_dict()
        # asdict converts tuples to lists, but to_dict may preserve tuples
        for opt in d["options"]:
            assert len(opt) == 2
            assert len(opt) == 2

        restored = CommandFormField.from_dict(d)
        assert restored.options == [("1", "Low"), ("2", "High")]

    def test_switch_field(self):
        f = CommandFormField(
            id="verbose",
            label="Verbose",
            field_type=FieldType.SWITCH,
            command_flag="-v",
            off_value="",
        )
        assert f.command_flag == "-v"
        assert f.off_value == ""


# ── CommandButton ──


class TestCommandButton:
    def _make_button(self, **kwargs):
        defaults = dict(
            id="test-1",
            name="Test",
            description="A test command",
            command_template="echo {msg}",
        )
        defaults.update(kwargs)
        return CommandButton(**defaults)

    def test_creation_defaults(self):
        b = self._make_button()
        assert b.display_mode == DisplayMode.ICON_AND_TEXT
        assert b.execution_mode == ExecutionMode.INSERT_ONLY
        assert b.is_builtin is False

    def test_to_dict_and_from_dict_roundtrip(self):
        b = self._make_button(
            display_mode=DisplayMode.ICON_ONLY,
            execution_mode=ExecutionMode.SHOW_DIALOG,
            category="network",
            sort_order=5,
        )
        d = b.to_dict()
        assert d["display_mode"] == "icon_only"
        assert d["execution_mode"] == "show_dialog"

        restored = CommandButton.from_dict(d)
        assert restored.id == b.id
        assert restored.display_mode == DisplayMode.ICON_ONLY
        assert restored.execution_mode == ExecutionMode.SHOW_DIALOG
        assert restored.category == "network"
        assert restored.sort_order == 5

    def test_build_command_no_fields(self):
        b = self._make_button(command_template="ls -la")
        assert b.build_command() == "ls -la"

    def test_build_command_text_field_escapes(self):
        """User-provided text values must be shell-escaped."""
        f = CommandFormField(id="path", label="Path", field_type=FieldType.TEXT)
        b = self._make_button(
            command_template="cat {path}",
            form_fields=[f],
        )
        # Spaces and special characters should be quoted
        result = b.build_command({"path": "/tmp/my file.txt"})
        assert "'/tmp/my file.txt'" in result

    def test_build_command_injection_prevention(self):
        """Shell metacharacters in user input must be escaped."""
        f = CommandFormField(id="name", label="Name", field_type=FieldType.TEXT)
        b = self._make_button(
            command_template="echo {name}",
            form_fields=[f],
        )
        result = b.build_command({"name": "hello; rm -rf /"})
        # shlex.quote wraps in single quotes
        assert "rm -rf" not in result or "'" in result
        assert ";" not in result.replace("'hello; rm -rf /'", "")

    def test_build_command_switch_on(self):
        f = CommandFormField(
            id="verbose",
            label="Verbose",
            field_type=FieldType.SWITCH,
            command_flag="-v",
            off_value="",
        )
        b = self._make_button(
            command_template="rsync {verbose} src dst",
            form_fields=[f],
        )
        result = b.build_command({"verbose": True})
        assert "-v" in result

    def test_build_command_switch_off(self):
        f = CommandFormField(
            id="verbose",
            label="Verbose",
            field_type=FieldType.SWITCH,
            command_flag="-v",
            off_value="",
        )
        b = self._make_button(
            command_template="rsync {verbose} src dst",
            form_fields=[f],
        )
        result = b.build_command({"verbose": False})
        assert "-v" not in result

    def test_build_command_switch_off_with_value(self):
        f = CommandFormField(
            id="color",
            label="Color",
            field_type=FieldType.SWITCH,
            command_flag="--color=always",
            off_value="--color=never",
        )
        b = self._make_button(
            command_template="ls {color}",
            form_fields=[f],
        )
        assert "--color=never" in b.build_command({"color": False})
        assert "--color=always" in b.build_command({"color": True})

    def test_build_command_uses_default_value(self):
        f = CommandFormField(
            id="count",
            label="Count",
            field_type=FieldType.NUMBER,
            default_value="10",
        )
        b = self._make_button(
            command_template="head -n {count} file",
            form_fields=[f],
        )
        result = b.build_command({})  # no values provided
        assert "10" in result

    def test_build_command_uses_template_key(self):
        f = CommandFormField(
            id="search_field",
            label="Search",
            field_type=FieldType.TEXT,
            template_key="q",
        )
        b = self._make_button(
            command_template="grep {q} .",
            form_fields=[f],
        )
        result = b.build_command({"search_field": "pattern"})
        # Single-word value may or may not be quoted depending on shlex
        assert "pattern" in result

    def test_build_command_number_not_escaped(self):
        """Number fields should not be shell-escaped."""
        f = CommandFormField(
            id="n", label="Lines", field_type=FieldType.NUMBER
        )
        b = self._make_button(
            command_template="tail -n {n} file",
            form_fields=[f],
        )
        result = b.build_command({"n": "42"})
        assert result == "tail -n 42 file"

    def test_build_command_multiple_fields(self):
        fields = [
            CommandFormField(id="src", label="Source", field_type=FieldType.TEXT),
            CommandFormField(
                id="recursive",
                label="Recursive",
                field_type=FieldType.SWITCH,
                command_flag="-r",
            ),
            CommandFormField(
                id="dst", label="Destination", field_type=FieldType.DIRECTORY_PATH
            ),
        ]
        b = self._make_button(
            command_template="cp {recursive} {src} {dst}",
            form_fields=fields,
        )
        result = b.build_command(
            {"src": "a.txt", "recursive": True, "dst": "/tmp/out"}
        )
        assert "-r" in result
        assert "a.txt" in result
        assert "/tmp/out" in result

    def test_build_command_empty_value(self):
        f = CommandFormField(id="opt", label="Opt", field_type=FieldType.TEXT)
        b = self._make_button(
            command_template="cmd {opt} end",
            form_fields=[f],
        )
        result = b.build_command({"opt": ""})
        assert result == "cmd end"

    def test_with_form_fields_roundtrip(self):
        fields = [
            CommandFormField(id="x", label="X", field_type=FieldType.TEXT),
            CommandFormField(
                id="y",
                label="Y",
                field_type=FieldType.SWITCH,
                command_flag="--yes",
            ),
        ]
        b = self._make_button(form_fields=fields)
        d = b.to_dict()
        restored = CommandButton.from_dict(d)
        assert len(restored.form_fields) == 2
        assert restored.form_fields[0].id == "x"
        assert restored.form_fields[1].command_flag == "--yes"


# ── generate_id ──


class TestGenerateId:
    def test_returns_string(self):
        assert isinstance(generate_id(), str)

    def test_length_is_8(self):
        assert len(generate_id()) == 8

    def test_unique(self):
        ids = {generate_id() for _ in range(100)}
        assert len(ids) == 100


# ── CommandButtonManager (with temp directory) ──


class TestCommandButtonManager:
    @pytest.fixture
    def manager(self, tmp_path):
        """Create a fresh manager for each test (reset singleton)."""
        # Point the mock config to tmp_path BEFORE creating the manager
        _mock_config_paths.CONFIG_DIR = tmp_path

        from ashyterm.data.command_manager_models import CommandButtonManager

        # Reset singleton
        CommandButtonManager._instance = None
        mgr = CommandButtonManager()
        return mgr

    def test_has_builtin_commands(self, manager):
        builtins = manager.get_builtin_commands()
        assert len(builtins) > 0
        assert all(c.is_builtin for c in builtins)

    def test_add_custom_command(self, manager):
        cmd = CommandButton(
            id="custom-1",
            name="My Cmd",
            description="desc",
            command_template="echo hello",
        )
        manager.add_custom_command(cmd)
        found = manager.get_command_by_id("custom-1")
        assert found is not None
        assert found.name == "My Cmd"

        # Verify JSON saved
        assert manager.custom_commands_file.exists()

    def test_update_custom_command(self, manager):
        cmd = CommandButton(
            id="upd-1", name="Old", description="d", command_template="old"
        )
        manager.add_custom_command(cmd)

        cmd.name = "New"
        cmd.command_template = "new"
        manager.update_command(cmd)

        found = manager.get_command_by_id("upd-1")
        assert found.name == "New"
        assert found.command_template == "new"

    def test_hide_and_unhide_command(self, manager):
        builtins = manager.get_builtin_commands()
        cmd_id = builtins[0].id

        manager.hide_command(cmd_id)
        assert manager.is_command_hidden(cmd_id)

        # get_all_commands does not filter hidden — is_command_hidden is used by callers
        hidden_ids = manager.get_hidden_command_ids()
        assert cmd_id in hidden_ids

        manager.unhide_command(cmd_id)
        assert not manager.is_command_hidden(cmd_id)

    def test_pin_and_unpin_command(self, manager):
        builtins = manager.get_builtin_commands()
        cmd_id = builtins[0].id

        manager.pin_command(cmd_id)
        assert manager.is_command_pinned(cmd_id)

        manager.unpin_command(cmd_id)
        assert not manager.is_command_pinned(cmd_id)

    def test_get_pinned_commands(self, manager):
        builtins = manager.get_builtin_commands()
        cmd_id = builtins[0].id

        manager.pin_command(cmd_id)
        pinned = manager.get_pinned_commands()
        assert any(c.id == cmd_id for c in pinned)

    def test_get_categories(self, manager):
        cats = manager.get_categories()
        assert isinstance(cats, list)
        # Builtins should have categories
        assert len(cats) > 0

    def test_remove_custom_command(self, manager):
        cmd = CommandButton(
            id="rem-1", name="ToRemove", description="d", command_template="x"
        )
        manager.add_custom_command(cmd)
        assert manager.get_command_by_id("rem-1") is not None

        manager.remove_command("rem-1")
        assert manager.get_command_by_id("rem-1") is None
