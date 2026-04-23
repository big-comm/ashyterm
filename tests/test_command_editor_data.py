"""Tests for pure-data helpers that back CommandEditorDialog."""

import pytest

from ashyterm.data.command_manager_models import CommandFormField, FieldType
from ashyterm.ui.dialogs.command_manager.command_editor_data import (
    FIELD_TYPE_ICONS,
    build_form_command_template,
    build_form_fields_list,
    field_type_display_names,
    field_type_from_string,
    field_type_to_string,
    form_field_to_dict,
    get_field_extra_config,
    get_field_preview,
    new_field_data,
)


# ── field_type_to/from_string ────────────────────────────────


class TestFieldTypeStrings:
    @pytest.mark.parametrize(
        "string_form,enum_form",
        [
            ("text", FieldType.TEXT),
            ("password", FieldType.PASSWORD),
            ("text_area", FieldType.TEXT_AREA),
            ("switch", FieldType.SWITCH),
            ("dropdown", FieldType.DROPDOWN),
            ("radio", FieldType.RADIO),
            ("multi_select", FieldType.MULTI_SELECT),
            ("number", FieldType.NUMBER),
            ("slider", FieldType.SLIDER),
            ("file_path", FieldType.FILE_PATH),
            ("directory_path", FieldType.DIRECTORY_PATH),
            ("date_time", FieldType.DATE_TIME),
            ("color", FieldType.COLOR),
        ],
    )
    def test_roundtrip(self, string_form, enum_form):
        assert field_type_from_string(string_form) is enum_form
        assert field_type_to_string(enum_form) == string_form

    def test_unknown_string_degrades_to_text(self):
        assert field_type_from_string("legacy-value") is FieldType.TEXT
        assert field_type_from_string("") is FieldType.TEXT


# ── get_field_preview ────────────────────────────────────────


class TestFieldPreview:
    def test_command_text_emits_literal_default(self):
        assert get_field_preview({"type": "command_text", "default": "ls"}) == "ls"
        assert get_field_preview({"type": "command_text", "default": ""}) == ""

    def test_switch_prefers_on_flag_then_off_value_then_id(self):
        on_only = {"type": "switch", "id": "x", "command_flag": "--verbose"}
        assert get_field_preview(on_only) == "[--verbose]"

        off_only = {"type": "switch", "id": "x", "off_value": "--quiet"}
        assert get_field_preview(off_only) == "[--quiet]"

        no_flags = {"type": "switch", "id": "x"}
        assert get_field_preview(no_flags) == "{x}"

    def test_regular_field_emits_default_when_set(self):
        assert get_field_preview({"type": "text", "id": "f", "default": "hello"}) == "hello"

    def test_falls_back_to_placeholder_then_label_then_id(self):
        assert (
            get_field_preview(
                {"type": "text", "id": "f", "placeholder": "email"}
            )
            == "<email>"
        )
        assert (
            get_field_preview({"type": "text", "id": "f", "label": "  Name  "})
            == "<Name>"
        )
        assert get_field_preview({"type": "text", "id": "f"}) == "{f}"

    def test_integer_defaults_are_stringified(self):
        # Non-string defaults are coerced for the preview.
        out = get_field_preview({"type": "number", "id": "n", "default": 42})
        assert out == "42"


# ── build_form_command_template ──────────────────────────────


class TestCommandTemplate:
    def test_mixed_fields_assemble_with_spaces(self):
        fields = [
            {"type": "command_text", "default": "ssh"},
            {"type": "text", "id": "host"},
            {"type": "switch", "id": "verbose", "command_flag": "-v"},
        ]
        assert (
            build_form_command_template(fields)
            == "ssh {host} {verbose}"
        )

    def test_command_text_with_empty_default_is_skipped(self):
        fields = [
            {"type": "command_text", "default": ""},
            {"type": "text", "id": "host"},
        ]
        assert build_form_command_template(fields) == "{host}"

    def test_empty_fields_list_yields_empty_string(self):
        assert build_form_command_template([]) == ""

    def test_field_without_id_emits_empty_placeholder(self):
        # Degenerate state the editor can reach mid-edit; must not crash.
        assert build_form_command_template([{"type": "text"}]) == "{}"


# ── get_field_extra_config ───────────────────────────────────


class TestExtraConfig:
    def test_text_area_reads_rows(self):
        assert get_field_extra_config({"type": "text_area", "rows": 10}) == {"rows": 10}
        assert get_field_extra_config({"type": "text_area"}) == {"rows": 4}

    def test_slider_reads_bounds_and_step(self):
        data = {"type": "slider", "min_value": 5, "max_value": 50, "step": 5}
        assert get_field_extra_config(data) == {
            "min_value": 5,
            "max_value": 50,
            "step": 5,
        }

    def test_slider_defaults(self):
        assert get_field_extra_config({"type": "slider"}) == {
            "min_value": 0,
            "max_value": 100,
            "step": 1,
        }

    def test_date_time_reads_format(self):
        assert get_field_extra_config({"type": "date_time", "format": "%H:%M"}) == {
            "format": "%H:%M"
        }
        assert get_field_extra_config({"type": "date_time"}) == {
            "format": "%Y-%m-%d %H:%M"
        }

    def test_color_reads_format(self):
        assert get_field_extra_config({"type": "color", "color_format": "rgb"}) == {
            "color_format": "rgb"
        }

    def test_plain_types_have_no_extra_config(self):
        for ft in ("text", "switch", "dropdown", "number", "file_path"):
            assert get_field_extra_config({"type": ft}) == {}


# ── build_form_fields_list ───────────────────────────────────


class TestBuildFormFields:
    def test_command_text_is_dropped(self):
        fields = [
            {"type": "command_text", "default": "ls"},
            {"type": "text", "id": "host", "label": "Host"},
        ]
        out = build_form_fields_list(fields)
        assert len(out) == 1
        assert out[0].id == "host"

    def test_fields_carry_all_editor_metadata(self):
        data = {
            "type": "dropdown",
            "id": "env",
            "label": "Env",
            "default": "prod",
            "placeholder": "Pick one",
            "tooltip": "Deployment target",
            "command_flag": "--env",
            "off_value": "",
            "options": ["dev", "prod"],
            "template_key": "env",
        }
        out = build_form_fields_list([data])
        assert len(out) == 1
        field = out[0]
        assert field.field_type is FieldType.DROPDOWN
        assert field.label == "Env"
        assert field.default_value == "prod"
        assert field.placeholder == "Pick one"
        assert field.tooltip == "Deployment target"
        assert field.command_flag == "--env"
        assert field.options == ["dev", "prod"]
        assert field.template_key == "env"
        # required is always False from the editor — editor doesn't expose
        # the flag and relies on runtime UX instead.
        assert field.required is False

    def test_field_without_id_gets_positional_id(self):
        out = build_form_fields_list([{"type": "text"}])
        assert out[0].id == "field_0"
        assert out[0].field_type is FieldType.TEXT

    def test_numeric_default_is_stringified(self):
        out = build_form_fields_list([{"type": "number", "id": "n", "default": 42}])
        assert out[0].default_value == "42"

    def test_extra_config_is_attached_for_slider(self):
        out = build_form_fields_list(
            [{"type": "slider", "id": "x", "min_value": 0, "max_value": 10, "step": 2}]
        )
        assert out[0].extra_config == {"min_value": 0, "max_value": 10, "step": 2}


# ── form_field_to_dict ───────────────────────────────────────


class TestFormFieldToDict:
    def test_roundtrip_through_build_and_to_dict(self):
        """A dict the editor would emit should survive a build→to_dict trip
        with its user-visible keys intact. This is the guarantee the
        editor relies on when it populates the UI from a stored
        CommandButton.
        """
        original = {
            "type": "slider",
            "id": "vol",
            "template_key": "vol",
            "label": "Volume",
            "default": "50",
            "placeholder": "",
            "tooltip": "",
            "command_flag": "",
            "off_value": "",
            "options": [],
            "min_value": 0,
            "max_value": 100,
            "step": 5,
        }
        fields = build_form_fields_list([original])
        rebuilt = form_field_to_dict(fields[0])

        for key in original:
            assert rebuilt[key] == original[key], f"mismatch on {key!r}"

    def test_template_key_defaults_to_id_when_missing(self):
        field = CommandFormField(
            id="abc",
            label="",
            field_type=FieldType.TEXT,
            template_key="",
        )
        out = form_field_to_dict(field)
        assert out["template_key"] == "abc"

    def test_none_options_becomes_empty_list(self):
        field = CommandFormField(
            id="x",
            label="",
            field_type=FieldType.DROPDOWN,
            options=None,  # type: ignore[arg-type]
        )
        out = form_field_to_dict(field)
        assert out["options"] == []

    def test_date_time_extra_config_is_flattened(self):
        field = CommandFormField(
            id="when",
            label="",
            field_type=FieldType.DATE_TIME,
            extra_config={"format": "%H:%M"},
        )
        out = form_field_to_dict(field)
        assert out["format"] == "%H:%M"


# ── dialog integration ───────────────────────────────────────


class TestDialogIntegration:
    def test_dialog_delegators_exist(self):
        from ashyterm.ui.dialogs.command_manager.command_editor_dialog import (
            CommandEditorDialog,
        )

        # These are the thin delegator methods that used to house the
        # pure logic directly in the dialog class.
        for name in (
            "_get_field_preview",
            "_build_form_command_template",
            "_get_field_extra_config",
            "_build_form_fields_list",
            "_field_type_to_string",
            "_convert_field_to_dict",
        ):
            assert callable(getattr(CommandEditorDialog, name))


# ── FIELD_TYPE_ICONS / field_type_display_names ──────────────


class TestFieldTypeMaps:
    def test_every_known_field_type_has_an_icon(self):
        # Every string the editor accepts must have a visual glyph so
        # the ExpanderRow title isn't blank for new types.
        for key in (
            "command_text",
            "text",
            "text_area",
            "password",
            "switch",
            "dropdown",
            "radio",
            "multi_select",
            "number",
            "slider",
            "file_path",
            "directory_path",
            "date_time",
            "color",
        ):
            assert key in FIELD_TYPE_ICONS
            assert FIELD_TYPE_ICONS[key]  # non-empty

    def test_display_names_cover_every_icon_key(self):
        names = field_type_display_names()
        for key in FIELD_TYPE_ICONS:
            assert key in names, f"{key} missing from display names"
            assert names[key]


# ── new_field_data ───────────────────────────────────────────


class TestNewFieldData:
    def test_text_defaults_are_empty(self):
        fd = new_field_data("text", position=1)
        assert fd["type"] == "text"
        assert fd["id"] == "field_1"
        assert fd["default"] == ""
        assert fd["options"] == []
        # Type-specific extras don't apply to plain text.
        assert "rows" not in fd
        assert "min_value" not in fd

    def test_command_text_gets_special_id_prefix(self):
        fd = new_field_data("command_text", position=3)
        assert fd["id"] == "part_3"
        assert fd["label"]  # translated "Command" — non-empty
        assert fd["default"] == ""

    def test_switch_default_is_false(self):
        fd = new_field_data("switch", position=1)
        assert fd["default"] is False

    def test_slider_default_is_fifty_with_bounds(self):
        fd = new_field_data("slider", position=1)
        assert fd["default"] == 50
        assert fd["min_value"] == 0
        assert fd["max_value"] == 100
        assert fd["step"] == 1

    def test_color_default_is_black_with_hex_format(self):
        fd = new_field_data("color", position=1)
        assert fd["default"] == "#000000"
        assert fd["color_format"] == "hex"

    def test_text_area_gets_default_rows(self):
        fd = new_field_data("text_area", position=1)
        assert fd["rows"] == 4

    def test_date_time_carries_format_string(self):
        fd = new_field_data("date_time", position=1)
        assert fd["format"] == "%Y-%m-%d %H:%M"

    def test_position_is_one_based_in_id(self):
        # Position 0 (would never happen in practice) falls out to
        # "field_0"; it's simpler to document than to reject.
        fd = new_field_data("text", position=0)
        assert fd["id"] == "field_0"

    def test_unknown_field_type_still_returns_usable_dict(self):
        # The editor never emits "banana", but the helper shouldn't
        # crash if some caller hands over an unknown type.
        fd = new_field_data("banana", position=2)
        assert fd["type"] == "banana"
        assert fd["default"] == ""
        # No type-specific extras.
        assert "min_value" not in fd
        assert "rows" not in fd
