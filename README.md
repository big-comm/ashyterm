# Ashy Terminal

<p align="center">
  <a href="https://github.com/big-comm/comm-ashyterm/releases"><img src="https://img.shields.io/badge/Version-1.1.0-blue.svg" alt="Version"/></a>
  <a href="https://bigcommunity.com">
  <img src="https://img.shields.io/badge/BigCommunity-Platform-blue" alt="BigCommunity Platform">
</a>
  <a href="https://github.com/big-comm/comm-ashyterm/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License"/></a>
</p>

**Ashy Terminal** is a modern, feature-rich terminal emulator built with GTK4 and Adwaita, designed for developers and power users. Developed by the **BigCommunity** team, it offers advanced session management, a highly customizable interface, and robust security features, making it a powerful companion for any workflow on Manjaro-based distributions like BigLinux.

## Screenshot

<p align="center">

   <!-- TODO: Replace this with a high-quality screenshot of the application -->
   <img width="1322" height="822" alt="ashyterm" src="https://github.com/user-attachments/assets/e3296fab-b975-4d12-8692-41178f72fc7e" alt="Ashy Terminal" />
</p>

## Example of use

![ashy](https://github.com/user-attachments/assets/cfab5152-ca80-499d-a13d-c8087a9da6d1)

## Key Features

- **Modern User Interface**: A clean and responsive UI built with the latest GTK4 and Adwaita libraries, providing a native look and feel on modern Linux desktops.
- **Advanced Session Management**:
    - Organize connections in nested folders.
    - Save and quickly connect to local terminals and remote SSH sessions.
    - Edit, duplicate, and move sessions with an intuitive tree view.
- **Powerful Terminal Functionality**:
    - **Tabs and Pane Splitting**: Supports multiple tabs and both horizontal and vertical pane splitting for complex layouts.
    - **SFTP Integration**: Connect to SSH sessions using a built-in SFTP terminal for easy file transfers via drag-and-drop.
    - **Directory Tracking**: Automatically tracks the current working directory (via OSC7) and updates tab titles for better context.
- **Deep Customization**:
    - **Color Schemes**: Comes with multiple built-in color schemes (Solarized, Dracula, Nord, etc.).
    - **Font and Transparency**: Easily configure the terminal font, size, and background transparency.
    - **Customizable Shortcuts**: Modify keyboard shortcuts for most common actions.
- **Security-Focused**:
    - **Encrypted Password Storage**: Securely stores SSH passwords using the system's secret service or a master passphrase.
    - **Input Validation & Sanitization**: Protects against common vulnerabilities by validating all user inputs.
    - **Secure File Permissions**: Automatically ensures configuration files are stored with secure permissions.
- **Robust and Resilient**:
    - **Automatic Backups**: A built-in backup system automatically saves your sessions and settings, protecting you from data loss.
    - **Structured Logging**: Comprehensive logging for easy debugging and troubleshooting.

## Dependencies

To build and run Ashy Terminal, you will need the following:

-   **Python 3.8+**
-   **GTK4** and **Adwaita 1.0+** libraries
-   **VTE for GTK4** (`vte-ng` or `vte4`, version 3.91 or higher)
-   **Python Libraries**:
    -   `PyGObject` (for GTK bindings)
    -   `cryptography` (for secure password storage)
    -   `psutil` (optional, for advanced process tracking)

On an Arch/Manjaro-based system, you can install them with:
```bash
sudo pacman -S python python-gobject vte4 python-cryptography python-psutil
```

## Installation

#### From Package (Recommended)

If a package is available for your distribution, install it using your package manager. For example:

```bash
sudo pacman -U comm-ashyterm-*-x86_64.pkg.tar.zst
```

#### From Source

1.  Clone the repository:
    ```bash
    git clone https://github.com/big-comm/comm-ashyterm.git
    cd comm-ashyterm
    ```

2.  Run the application directly:
    ```bash
    python -m ashyterm.main
    ```

3.  (Optional) To build and install a system package (Arch/Manjaro):
    ```bash
    # Assuming a PKGBUILD is present in the repository root
    makepkg -si
    ```

## Usage

#### Running the Application

Simply run the command from your terminal:

```bash
ashyterm
```

#### Command-Line Arguments

Ashy Terminal supports several command-line arguments for convenience:

```
Usage: comm-ashyterm [options] [directory]

Options:
  -h, --help                 Show this help message and exit
  -v, --version              Print application version
  -d, --debug                Enable debug mode with verbose logging
  --log-level LEVEL          Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  -w DIR, --working-directory DIR
                             Set the working directory for the initial terminal

Positional Arguments:
  directory                  Set the working directory (alternative to -w)
```

**Example:** Open Ashy Terminal directly in your projects folder:
```bash
ashyterm -w ~/Code/Projects/my-project
```

## Configuration

Ashy Terminal stores all its configuration files in the standard XDG config directory:

-   **Settings**: `~/.config/ashyterm/settings.json`
-   **Sessions & Folders**: `~/.config/ashyterm/sessions.json`
-   **Backups**: `~/.config/ashyterm/backups/`
-   **Logs**: `~/.config/ashyterm/logs/`
-   **Secure Storage**: `~/.config/ashyterm/secure/` (contains encrypted keys)

## Project Structure

```
/
├── ashyterm/
│   ├── app.py                # Main application class (Adw.Application)
│   ├── main.py               # Entry point script
│   ├── window.py             # Main window class (Adw.ApplicationWindow)
│   ├── helpers.py            # General helper functions
│   ├── sessions/             # Session and folder management
│   │   ├── models.py
│   │   ├── operations.py
│   │   ├── storage.py
│   │   └── tree.py
│   ├── terminal/             # Terminal creation and management
│   │   ├── manager.py
│   │   ├── spawner.py
│   │   └── tabs.py
│   ├── settings/             # Configuration and settings management
│   │   ├── config.py
│   │   └── manager.py
│   ├── ui/                   # UI components (dialogs, menus)
│   │   ├── dialogs.py
│   │   └── menus.py
│   └── utils/                # Core utility modules
│       ├── backup.py
│       ├── crypto.py
│       ├── exceptions.py
│       ├── logger.py
│       ├── osc7.py
│       ├── platform.py
│       └── ...
└── data/
    ├── comm-ashyterm.desktop # Desktop entry
    └── icons/                # Application icons
```

## Troubleshooting

-   **Terminal Does Not Start**: Ensure you have `vte4` (or `vte-ng`) installed and that it meets the version requirement. This is the most common cause of startup failure.
-   **Permission Errors**: The application automatically sets permissions for its configuration directory (`~/.config/ashyterm`). If you encounter errors, check the ownership and permissions of this folder.
-   **SSH Connection Fails**: Use the "Test Connection" button in the session editor to get detailed error messages. Common issues include incorrect hostnames, usernames, or invalid SSH key paths/permissions.

## Contributing

Contributions are welcome! If you'd like to help improve Ashy Terminal, please follow these steps:

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/my-new-feature`).
3.  Commit your changes (`git commit -m 'Add some amazing feature'`).
4.  Push to the branch (`git push origin feature/my-new-feature`).
5.  Open a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

-   The **BigCommunity** and **BigLinux** teams for their support and inspiration.
-   The developers of **GNOME**, **GTK**, and **VTE** for providing an amazing toolkit.
-   The **Manjaro** and **Arch Linux** communities.
