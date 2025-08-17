#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Ashy Terminal - Nautilus Extension
Adds a context menu option to open terminal in directories using the
Ashy Terminal application.
"""

import gettext
import subprocess
from pathlib import Path
from urllib.parse import unquote

# Import 'gi' and explicitly require GTK and Nautilus versions.
# This is mandatory in modern PyGObject to prevent warnings and ensure API compatibility.
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Nautilus', '4.0')

from gi.repository import GObject, Nautilus

# --- Internationalization (i18n) Setup ---
APP_NAME = "ashyterm"

try:
    # Set the default domain for this script. gettext will automatically find
    # the message catalogs in the system's standard locale directories.
    gettext.textdomain(APP_NAME)
except Exception as e:
    print(f"Ashy Terminal Extension: Could not set up localization: {e}")

# Define the global translation function.
_ = gettext.gettext


class AshyTerminalExtension(GObject.GObject, Nautilus.MenuProvider):
    """
    Provides the context menu items for Nautilus to allow opening terminal in directories.
    """

    def __init__(self):
        """Initializes the extension."""
        super().__init__()
        self.app_executable = 'ashyterm'

    def get_file_items(self, files: list[Nautilus.FileInfo]) -> list[Nautilus.MenuItem]:
        """
        Returns menu items for the selected directories.
        The menu is only shown for directories.
        """
        # Only show menu for directories
        directories = [f for f in files if f.is_directory()]
        if not directories:
            return []

        num_dirs = len(directories)

        # Define the label based on the number of selected directories
        if num_dirs == 1:
            label = _('Open Ashy Terminal Here')
            name = 'AshyTerminal::OpenHere'
        else:
            label = _('Open Ashy Terminal in {0} Folders').format(num_dirs)
            name = 'AshyTerminal::OpenFolders'

        menu_item = Nautilus.MenuItem(name=name, label=label)
        menu_item.connect('activate', self._launch_application, directories)
        return [menu_item]

    def get_background_items(self, current_folder: Nautilus.FileInfo) -> list[Nautilus.MenuItem]:
        """
        Returns menu items for the background (empty space) context menu.
        This allows opening a terminal in the current directory.
        """
        if not current_folder:
            return []

        label = _('Open Ashy Terminal Here')
        name = 'AshyTerminal::OpenHereBackground'
        
        menu_item = Nautilus.MenuItem(name=name, label=label)
        menu_item.connect('activate', self._launch_application, [current_folder])
        return [menu_item]

    def _get_file_path(self, file_info: Nautilus.FileInfo) -> str | None:
        """
        Gets the local file path from a Nautilus.FileInfo object by parsing its URI.
        """
        uri = file_info.get_uri()
        if not uri.startswith('file://'):
            return None
        # Decode URL-encoded characters (e.g., %20 -> space) and remove the prefix.
        return unquote(uri[7:])

    def _launch_application(self, menu_item: Nautilus.MenuItem, directories: list[Nautilus.FileInfo]):
        """
        Launches the Ashy Terminal application in the selected directories.
        """
        dir_paths = []
        for directory in directories:
            path = self._get_file_path(directory)
            if path and Path(path).exists() and Path(path).is_dir():
                dir_paths.append(path)

        if not dir_paths:
            self._show_error_notification(
                _("No valid directories selected"),
                _("Could not get the path for the selected directories.")
            )
            return

        try:
            # Launch Ashy Terminal in each directory
            # For multiple directories, we can open multiple terminals or pass all paths
            cmd = [self.app_executable] + dir_paths
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception as e:
            print(f"Error launching '{self.app_executable}': {e}")
            self._show_error_notification(
                _("Ashy Terminal Launch Error"),
                _("Failed to start Ashy Terminal: {0}").format(str(e))
            )

    def _show_error_notification(self, title: str, message: str):
        """
        Displays a desktop error notification using 'notify-send'.
        """
        try:
            subprocess.run([
                'notify-send',
                '--icon=dialog-error',
                f'--app-name={APP_NAME}',
                title,
                message
            ], check=False)
        except FileNotFoundError:
            # Fallback if 'notify-send' is not installed.
            print(f"ERROR: [{title}] {message}")