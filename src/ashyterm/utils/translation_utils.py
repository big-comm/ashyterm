#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# translation_utils.py - Utilities for translation support
#
import gettext
import os
from typing import Callable

# Determine locale directory (works in AppImage and system install)
locale_dir = "/usr/share/locale"  # Default for system install

# Check if we're in an AppImage
if "APPIMAGE" in os.environ or "APPDIR" in os.environ:
    # Running from AppImage
    # translation_utils.py is in: src/ashyterm/utils/translation_utils.py
    # We need to get to: usr/share/locale
    script_dir = os.path.dirname(os.path.abspath(__file__))  # src/ashyterm/utils
    ashyterm_dir = os.path.dirname(script_dir)  # src/ashyterm
    src_dir = os.path.dirname(ashyterm_dir)  # src
    appdir_root = os.path.dirname(src_dir)  # AppDir root (squashfs-root)
    appimage_locale = os.path.join(appdir_root, "usr", "share", "locale")  # usr/share/locale

    if os.path.isdir(appimage_locale):
        locale_dir = appimage_locale

# Configure the translation text domain for ashyterm
gettext.bindtextdomain("ashyterm", locale_dir)
gettext.textdomain("ashyterm")

# Export _ directly as the translation function with explicit type
_: Callable[[str], str] = gettext.gettext
