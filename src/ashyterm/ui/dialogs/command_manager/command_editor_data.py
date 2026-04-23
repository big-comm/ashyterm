"""Pure-data transformations for CommandEditorDialog.

The editor dialog shows either a "simple" free-form command or a
structured form built from field definitions. Both modes eventually
produce a :class:`CommandButton`, which requires turning the editor's
widget state into a template string + a list of :class:`CommandFormField`.

The mapping rules are pure — no GTK, no side effects — so they live
here so the dialog can delegate and so the rules can be unit-tested
without mounting the dialog.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ....data.command_manager_models import CommandFormField, FieldType
from ....utils.translation_utils import _


# Emoji-based type icons used in the field-row title ("📄 {file_id}").
# Exposed as a constant so the editor, preview, and tests all agree.
FIELD_TYPE_ICONS: Dict[str, str] = {
    "command_text": "💬",
    "text": "⌨️",
    "text_area": "📝",
    "password": "🔑",
    "switch": "🔘",
    "dropdown": "📋",
    "radio": "⚪",
    "multi_select": "☑️",
    "number": "🔢",
    "slider": "📊",
    "file_path": "📄",
    "directory_path": "📁",
    "date_time": "📅",
    "color": "🎨",
}


def field_type_display_names() -> Dict[str, str]:
    """Localized human-readable names used in the field-row subtitle.

    Returned as a fresh dict each call because the translations are
    resolved against whatever locale is active when the editor runs.
    """
    return {
        "command_text": _("Command Text"),
        "text": _("Text Input"),
        "text_area": _("Text Area"),
        "password": _("Password"),
        "switch": _("Switch"),
        "dropdown": _("Dropdown"),
        "radio": _("Radio Buttons"),
        "multi_select": _("Multi-Select"),
        "number": _("Number"),
        "slider": _("Slider"),
        "file_path": _("File"),
        "directory_path": _("Directory"),
        "date_time": _("Date/Time"),
        "color": _("Color"),
    }


def new_field_data(
    field_type: str,
    *,
    position: int,
) -> Dict[str, Any]:
    """Build a fresh field-data dict for a newly-added editor row.

    ``position`` is the 1-based slot the field will occupy, used to
    mint a default ``id`` the user can rename later.
    """
    field_id = (
        f"part_{position}"
        if field_type == "command_text"
        else f"field_{position}"
    )

    default: bool | int | str
    if field_type == "switch":
        default = False
    elif field_type == "slider":
        default = 50
    elif field_type == "color":
        default = "#000000"
    else:
        # text/date_time/command_text/everything-else share the empty string.
        default = ""

    field_data: Dict[str, Any] = {
        "type": field_type,
        "id": field_id,
        "template_key": "",
        "label": "" if field_type != "command_text" else _("Command"),
        "default": default,
        "placeholder": "",
        "tooltip": "",
        "command_flag": "",
        "off_value": "",
        "options": [],
    }

    # Type-specific extra keys. Kept flat on the top-level dict because
    # the row builders read them the same way.
    if field_type == "slider":
        field_data["min_value"] = 0
        field_data["max_value"] = 100
        field_data["step"] = 1
    elif field_type == "text_area":
        field_data["rows"] = 4
    elif field_type == "date_time":
        field_data["format"] = "%Y-%m-%d %H:%M"
    elif field_type == "color":
        field_data["color_format"] = "hex"

    return field_data


# Keep the field-type string tables in one place so the mapping in
# both directions cannot drift apart.
_STRING_TO_FIELD_TYPE: Dict[str, FieldType] = {
    "text": FieldType.TEXT,
    "password": FieldType.PASSWORD,
    "text_area": FieldType.TEXT_AREA,
    "switch": FieldType.SWITCH,
    "dropdown": FieldType.DROPDOWN,
    "radio": FieldType.RADIO,
    "multi_select": FieldType.MULTI_SELECT,
    "number": FieldType.NUMBER,
    "slider": FieldType.SLIDER,
    "file_path": FieldType.FILE_PATH,
    "directory_path": FieldType.DIRECTORY_PATH,
    "date_time": FieldType.DATE_TIME,
    "color": FieldType.COLOR,
}
_FIELD_TYPE_TO_STRING: Dict[FieldType, str] = {v: k for k, v in _STRING_TO_FIELD_TYPE.items()}


def field_type_from_string(value: str) -> FieldType:
    """Map a persisted string field-type to the :class:`FieldType` enum.

    Unknown values degrade to ``FieldType.TEXT`` — the dialog should
    still render something rather than crash on a legacy value.
    """
    return _STRING_TO_FIELD_TYPE.get(value, FieldType.TEXT)


def field_type_to_string(field_type: FieldType) -> str:
    """Inverse of :func:`field_type_from_string`."""
    return _FIELD_TYPE_TO_STRING.get(field_type, "text")


def get_field_preview(field_data: Dict) -> str:
    """Return the rendered preview fragment for a single form field.

    ``command_text`` fields are static — their literal default is
    emitted. Switches emit their flag/off value. Other fields fall
    back through default → placeholder → label → ``{id}``.
    """
    field_type = field_data.get("type", "text")
    field_id = field_data.get("id", "")

    if field_type == "command_text":
        return field_data.get("default", "")

    if field_type == "switch":
        on_val = field_data.get("command_flag", "")
        off_val = field_data.get("off_value", "")
        if on_val:
            return f"[{on_val}]"
        if off_val:
            return f"[{off_val}]"
        return f"{{{field_id}}}"

    default = field_data.get("default", "")
    if default:
        return str(default)

    placeholder = field_data.get("placeholder", "")
    if placeholder:
        return f"<{placeholder}>"

    label = field_data.get("label", "").strip()
    if label:
        return f"<{label}>"

    return f"{{{field_id}}}"


def build_form_command_template(fields_data: List[Dict]) -> str:
    """Join form fields into a command template string.

    ``command_text`` fields contribute their literal default; every
    other field contributes ``{id}`` so runtime substitution can
    inject the user's answer.
    """
    parts: List[str] = []
    for field_data in fields_data:
        if field_data.get("type", "text") == "command_text":
            value = field_data.get("default", "")
            if value:
                parts.append(value)
        else:
            parts.append(f"{{{field_data.get('id', '')}}}")
    return " ".join(parts)


def get_field_extra_config(field_data: dict) -> dict:
    """Extract type-specific extra config (rows, bounds, format…) from a
    field data dict. Returns an empty dict for plain field types."""
    extra_config: dict = {}
    ft = field_data.get("type", "text")
    if ft == "text_area":
        extra_config["rows"] = field_data.get("rows", 4)
    elif ft == "slider":
        extra_config["min_value"] = field_data.get("min_value", 0)
        extra_config["max_value"] = field_data.get("max_value", 100)
        extra_config["step"] = field_data.get("step", 1)
    elif ft == "date_time":
        extra_config["format"] = field_data.get("format", "%Y-%m-%d %H:%M")
    elif ft == "color":
        extra_config["color_format"] = field_data.get("color_format", "hex")
    return extra_config


def build_form_fields_list(fields_data: List[Dict]) -> List[CommandFormField]:
    """Materialize editor data into :class:`CommandFormField` objects.

    ``command_text`` entries are dropped — they live inside the
    template string (see :func:`build_form_command_template`), not as
    user-facing fields.
    """
    form_fields: List[CommandFormField] = []
    for i, field_data in enumerate(fields_data):
        if field_data.get("type") == "command_text":
            continue
        form_fields.append(
            CommandFormField(
                id=field_data.get("id", f"field_{i}"),
                label=field_data.get("label", ""),
                field_type=field_type_from_string(field_data.get("type", "text")),
                default_value=str(field_data.get("default", "")),
                placeholder=field_data.get("placeholder", ""),
                tooltip=field_data.get("tooltip", ""),
                required=False,
                command_flag=field_data.get("command_flag", ""),
                off_value=field_data.get("off_value", ""),
                options=field_data.get("options", []),
                template_key=field_data.get("template_key", ""),
                extra_config=get_field_extra_config(field_data),
            )
        )
    return form_fields


def form_field_to_dict(field: CommandFormField) -> Dict:
    """Inverse of the :class:`CommandFormField` constructor — turn a
    field back into an editor-friendly dict. Extras in ``extra_config``
    are flattened into the top level so the UI can read them uniformly.
    """
    field_data: Dict = {
        "type": field_type_to_string(field.field_type),
        "id": field.id,
        "template_key": field.template_key or field.id,
        "label": field.label,
        "default": field.default_value,
        "placeholder": field.placeholder,
        "tooltip": field.tooltip or "",
        "command_flag": field.command_flag or "",
        "off_value": field.off_value or "",
        "options": list(field.options) if field.options else [],
    }
    extra = field.extra_config or {}
    for key in ("rows", "min_value", "max_value", "step", "format", "color_format"):
        if key in extra:
            field_data[key] = extra[key]
    return field_data
