# ashyterm/filemanager/models.py
import gi

gi.require_version("Gtk", "4.0")
import re
from datetime import datetime

from gi.repository import Gio, GObject


class FileItem(GObject.GObject):
    """Data model for an item in the file manager."""

    LS_RE = re.compile(
        r"^(?P<perms>[-dlpscb?][rwxSsTt-]{9})(?:[.+@])?\s+"
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
        self._link_target = link_target
        # Task 4: Calculate icon name once and cache it to avoid repeated Gio.content_type_guess calls
        self._cached_icon_name = self._resolve_icon_name()

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
    def is_directory_like(self) -> bool:
        """Returns True if the item is a directory or a link to a directory."""
        return self.is_directory or (
            self.is_link and self._link_target and self._link_target.endswith("/")
        )

    def _resolve_icon_name(self) -> str:
        """Task 4: Calculate icon name once during initialization."""
        # For links to directories, always use the folder icon.
        # This relies on the `ls --classify` command appending a '/' to the link target.
        if (
            self._permissions.startswith("l")
            and self._link_target
            and self._link_target.endswith("/")
        ):
            return "folder-symbolic"

        # For actual directories.
        if self._permissions.startswith("d"):
            return "folder-symbolic"

        # For all other cases (files and links to files), guess the icon from the name.
        mime_type, _ = Gio.content_type_guess(self._name, None)
        if mime_type:
            gicon = Gio.content_type_get_icon(mime_type)
            if isinstance(gicon, Gio.ThemedIcon) and gicon.get_names():
                return gicon.get_names()[0]

        return "text-x-generic-symbolic"

    @property
    def icon_name(self) -> str:
        """Task 4: Return cached icon name for performance."""
        return self._cached_icon_name

    @classmethod
    def from_ls_line(cls, line: str):
        """Task 2: Optimized parsing using str.split instead of regex.

        str.split is orders of magnitude faster than regex for columnar data.
        We expect 9 parts: perms, links, owner, group, size, date, time, timezone, name
        """
        try:
            # Task 2: Fast path using str.split
            parts = line.split(maxsplit=8)
            if len(parts) < 9:
                # Fallback to regex for edge cases
                return cls._from_ls_line_regex(line)

            perms, links, owner, group, size, date_ymd, time_hms, time_zone, name = (
                parts
            )

            # Fast datetime parsing
            # Expected format: 2024-01-15 10:30:00.000000000
            date_str = f"{date_ymd} {time_hms.split('.')[0]}"
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                date_obj = datetime.now()

            # Fast name cleanup - handle symlinks and type indicators
            link_target = ""
            if " -> " in name:
                name, link_target = name.split(" -> ", 1)

            # Remove file type indicators added by --classify
            if name and name[-1] in "/@=*|>":
                name = name[:-1]

            return cls(
                name=name,
                perms=perms,
                size=int(size),
                date=date_obj,
                owner=owner,
                group=group,
                is_link=perms.startswith("l"),
                link_target=link_target,
            )

        except (ValueError, IndexError):
            # Fallback to Regex for edge cases
            return cls._from_ls_line_regex(line)

    @classmethod
    def _from_ls_line_regex(cls, line: str):
        """Fallback regex parser for edge cases."""
        match = cls.LS_RE.match(line)
        if not match:
            return None
        data = match.groupdict()
        try:
            datetime_str = data["datetime"]
            date_part = datetime_str.split(".")[0]
            date_obj = datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            date_obj = datetime.now()
        name = data["name"]
        name = name.rstrip("/@=*|>")
        return cls(
            name=name,
            perms=data["perms"],
            size=int(data["size"]),
            date=date_obj,
            owner=data["owner"],
            group=data["group"],
            is_link=data["perms"].startswith("l"),
            link_target=data.get("link_target", ""),
        )
