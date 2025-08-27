import gi

gi.require_version("Gtk", "4.0")
import re
from datetime import datetime

from gi.repository import Gio, GObject


class FileItem(GObject.GObject):
    """Data model for an item in the file manager."""

    LS_RE = re.compile(
        r"^(?P<perms>[\w-]{10})\s+"
        r"(?P<links>\d+)\s+"
        r"(?P<owner>[\w\d._-]+)\s+"
        r"(?P<group>[\w\d._-]+)\s+"
        r"(?P<size>\d+)\s+"
        r"(?P<datetime>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+[+-]\d{4})\s+"
        r"(?P<name>.+?)(?: -> (?P<link_target>.+))?$"
    )

    # Define GObject properties
    __gproperties__ = {
        "name": (str, "Name", "File name", "", GObject.ParamFlags.READABLE),
        "permissions": (
            str,
            "Permissions",
            "File permissions",
            "",
            GObject.ParamFlags.READABLE,
        ),
        "size": (
            int,
            "Size",
            "File size",
            0,
            GObject.G_MAXINT,
            0,
            GObject.ParamFlags.READABLE,
        ),
        "owner": (str, "Owner", "File owner", "", GObject.ParamFlags.READABLE),
        "group": (str, "Group", "File group", "", GObject.ParamFlags.READABLE),
        "is-directory": (
            bool,
            "Is Directory",
            "Whether item is a directory",
            False,
            GObject.ParamFlags.READABLE,
        ),
        "is-link": (
            bool,
            "Is Link",
            "Whether item is a symbolic link",
            False,
            GObject.ParamFlags.READABLE,
        ),
        "icon-name": (
            str,
            "Icon Name",
            "Icon name for the file",
            "",
            GObject.ParamFlags.READABLE,
        ),
    }

    def __init__(
        self, name, perms, size, date, owner, group, is_link=False, link_target=""
    ):
        super().__init__()
        self._name = name
        self._permissions = perms
        self._size = size
        self._date = date
        self._owner = owner
        self._group = group
        self._is_link = is_link
        self._link_target = link_target

    def do_get_property(self, prop):
        """Handle property access."""
        if prop.name == "name":
            return self._name
        elif prop.name == "permissions":
            return self._permissions
        elif prop.name == "size":
            return self._size
        elif prop.name == "owner":
            return self._owner
        elif prop.name == "group":
            return self._group
        elif prop.name == "is-directory":
            return self._permissions.startswith("d")
        elif prop.name == "is-link":
            return self._permissions.startswith("l")
        elif prop.name == "icon-name":
            return self.icon_name
        else:
            raise AttributeError(f"Unknown property {prop.name}")

    @property
    def name(self) -> str:
        return self._name

    @property
    def permissions(self) -> str:
        return self._permissions

    @property
    def size(self) -> int:
        return self._size

    @property
    def date(self) -> datetime:
        return self._date

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def group(self) -> str:
        return self._group

    @property
    def is_directory(self) -> bool:
        return self._permissions.startswith("d")

    @property
    def is_link(self) -> bool:
        return self._permissions.startswith("l")

    @property
    def icon_name(self) -> str:
        if self.is_directory:
            return "folder-symbolic"
        if self.is_link:
            return "emblem-symbolic-link-symbolic"
        mime_type, _ = Gio.content_type_guess(self.name, None)
        if not mime_type:
            return "text-x-generic-symbolic"
        gicon = Gio.content_type_get_icon(mime_type)
        if isinstance(gicon, Gio.ThemedIcon) and gicon.get_names():
            return gicon.get_names()[0]
        return "text-x-generic-symbolic"

    @classmethod
    def from_ls_line(cls, line: str):
        match = cls.LS_RE.match(line)
        if not match:
            return None
        data = match.groupdict()
        try:
            # Parse the datetime string (format: 2024-01-15 10:30:00.000000000 +0000)
            datetime_str = data["datetime"]
            # Extract just the date and time part, ignoring microseconds and timezone
            date_part = datetime_str.split(".")[0]
            date_obj = datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            date_obj = datetime.now()
        return cls(
            name=data["name"],
            perms=data["perms"],
            size=int(data["size"]),
            date=date_obj,
            owner=data["owner"],
            group=data["group"],
            is_link=data["perms"].startswith("l"),
            link_target=data.get("link_target", ""),
        )
