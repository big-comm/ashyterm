"""Translation setup: resolve locale dir, bind gettext, export ``_``."""

import gettext
import os
from typing import Callable

# Priority: ASHYTERM_LOCALE_DIR env → AppImage bundled locale → system default.
locale_dir = os.environ.get("ASHYTERM_LOCALE_DIR", "/usr/share/locale")

if "APPIMAGE" in os.environ or "APPDIR" in os.environ:
    # AppImage layout: .../src/ashyterm/utils/translation_utils.py → .../usr/share/locale
    script_dir = os.path.dirname(os.path.abspath(__file__))
    appdir_root = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
    appimage_locale = os.path.join(appdir_root, "usr", "share", "locale")
    if os.path.isdir(appimage_locale):
        locale_dir = appimage_locale

gettext.bindtextdomain("ashyterm", locale_dir)
gettext.textdomain("ashyterm")

_: Callable[[str], str] = gettext.gettext
