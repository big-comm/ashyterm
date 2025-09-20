# ashyterm/data/command_data.py
from ..utils.translation_utils import _

# The data structure was changed to group command variations.
# The list has been reordered to prioritize essential commands for beginners.
NATIVE_COMMANDS = [
    # Category: Essentials: File & Directory Navigation
    {
        "category": _("Essentials: File & Directory Navigation"),
        "command": "ls",
        "general_description": _(
            "Lists the contents of a directory, like opening a folder to see what's inside."
        ),
        "variations": [
            {
                "name": "ls",
                "description": _(
                    "Shows files and directories in the current location."
                ),
            },
            {
                "name": "ls -lah",
                "description": _(
                    "Shows a detailed list, including hidden files and human-readable sizes."
                ),
            },
        ],
    },
    {
        "category": _("Essentials: File & Directory Navigation"),
        "command": "cd",
        "general_description": _("Changes the current directory."),
        "variations": [
            {
                "name": "cd /path/to/directory",
                "description": _("Navigates to a specific directory."),
            },
            {
                "name": "cd ..",
                "description": _("Goes up one level to the parent directory."),
            },
            {
                "name": "cd ~",
                "description": _("Goes directly to your user's home directory."),
            },
            {
                "name": "cd -",
                "description": _("Returns to the last directory you were in."),
            },
        ],
    },
    {
        "category": _("Essentials: File & Directory Navigation"),
        "command": "pwd",
        "general_description": _("Shows which directory you are currently in."),
        "variations": [
            {
                "name": "pwd",
                "description": _("Displays the full path of your current location."),
            },
        ],
    },
    {
        "category": _("Essentials: File & Directory Navigation"),
        "command": "mkdir",
        "general_description": _("Creates new directories."),
        "variations": [
            {
                "name": "mkdir new_directory",
                "description": _("Creates a single directory."),
            },
            {
                "name": "mkdir -p path/to/new/directory",
                "description": _(
                    "Creates a full path of directories, even if the parent directories don't exist."
                ),
            },
        ],
    },
    {
        "category": _("Essentials: File & Directory Navigation"),
        "command": "cp",
        "general_description": _(
            "Copies files or directories from one place to another."
        ),
        "variations": [
            {
                "name": "cp source_file destination_file",
                "description": _("Copies a single file."),
            },
            {
                "name": "cp -r source_directory/ destination_directory/",
                "description": _("Copies a directory and everything inside it."),
            },
        ],
    },
    {
        "category": _("Essentials: File & Directory Navigation"),
        "command": "mv",
        "general_description": _("Moves or renames files and directories."),
        "variations": [
            {
                "name": "mv old_name new_name",
                "description": _("Renames a file or directory."),
            },
            {
                "name": "mv file.txt /new/directory/",
                "description": _("Moves a file to a new location."),
            },
        ],
    },
    {
        "category": _("Essentials: File & Directory Navigation"),
        "command": "rm",
        "general_description": _(
            "Deletes files and directories. CAUTION: this is permanent! Tip: Before deleting, replace 'rm' with 'ls' to preview what will be removed."
        ),
        "variations": [
            {"name": "rm file.txt", "description": _("Deletes a single file.")},
            {
                "name": "rm -r directory/",
                "description": _("Deletes a directory and everything inside it."),
            },
            {
                "name": "rm -i file.txt",
                "description": _("Asks for confirmation before deleting each file."),
            },
            {
                "name": "rm -f file.txt",
                "description": _(
                    "Forces deletion without asking (use with extreme caution)."
                ),
            },
        ],
    },
    # Category: Essentials: File I/O & Pipes
    {
        "category": _("Essentials: File I/O & Pipes"),
        "command": "> >> |",
        "general_description": _("Controls how data flows between commands and files."),
        "variations": [
            {
                "name": "command > file.txt",
                "description": _(
                    "Redirect: Puts the output of a command into a file, overwriting it if it exists."
                ),
            },
            {
                "name": "command >> file.txt",
                "description": _(
                    "Append: Adds the output of a command to the end of a file."
                ),
            },
            {
                "name": "command1 | command2",
                "description": _(
                    "Pipe: Connects the output of command1 to the input of command2, creating a workflow."
                ),
            },
        ],
    },
    {
        "category": _("Essentials: File I/O & Pipes"),
        "command": "cat",
        "general_description": _("Displays the content of a text file on the screen."),
        "variations": [
            {
                "name": 'echo "$(<file.txt)"',
                "description": _(
                    "A modern and efficient way to display a file's content."
                ),
            },
            {
                "name": "cat file.txt",
                "description": _("Displays the content of a single file."),
            },
            {
                "name": "cat file1.txt file2.txt",
                "description": _(
                    "Displays the content of multiple files, one after the other."
                ),
            },
            {
                "name": "cat -n file.txt",
                "description": _("Displays the content with line numbers."),
            },
        ],
    },
    {
        "category": _("Essentials: File I/O & Pipes"),
        "command": "touch",
        "general_description": _(
            "Creates multiple empty files at once or updates a file's modification timestamp."
        ),
        "variations": [
            {
                "name": "touch file1.txt file2.txt",
                "description": _("Creates several empty files at once."),
            },
            {
                "name": "touch existing_file.txt",
                "description": _(
                    "Updates the modification date and time of a file to the current time."
                ),
            },
        ],
    },
    {
        "category": _("Essentials: File I/O & Pipes"),
        "command": "head",
        "general_description": _("Displays the beginning of a text file."),
        "variations": [
            {
                "name": "head file.txt",
                "description": _("Shows the first 10 lines of a file."),
            },
            {
                "name": "head -n 20 file.txt",
                "description": _("Shows the first 20 lines of a file."),
            },
        ],
    },
    {
        "category": _("Essentials: File I/O & Pipes"),
        "command": "tail",
        "general_description": _("Displays the end of a text file."),
        "variations": [
            {
                "name": "tail file.txt",
                "description": _("Shows the last 10 lines of a file."),
            },
            {
                "name": "tail -f file.log",
                "description": _(
                    "Follows a file in real-time, showing new lines as they are added."
                ),
            },
        ],
    },
    # Category: Essentials: Search & System Information
    {
        "category": _("Essentials: Search & System Information"),
        "command": "grep",
        "general_description": _("Searches for a word or text pattern within files."),
        "variations": [
            {
                "name": "grep 'word' file.txt",
                "description": _(
                    "Finds and displays all lines containing 'word' in the file."
                ),
            },
            {
                "name": "grep -i 'word' file.txt",
                "description": _("Searches for the word, ignoring case differences."),
            },
            {
                "name": "grep -R 'word' .",
                "description": _(
                    "Searches for 'word' in all files in the current directory and its subdirectories."
                ),
            },
        ],
    },
    {
        "category": _("Essentials: Search & System Information"),
        "command": "find",
        "general_description": _("A powerful tool for finding files and directories."),
        "variations": [
            {
                "name": "find . -name '*.txt'",
                "description": _(
                    "Finds all files ending with .txt in the current directory and subdirectories."
                ),
            },
            {
                "name": "find . -type d -name 'backup*'",
                "description": _(
                    "Finds only directories whose names start with 'backup'."
                ),
            },
            {
                "name": "find . -mtime -7",
                "description": _("Finds files that were modified in the last 7 days."),
            },
            {
                "name": "find . -size +100M",
                "description": _("Finds files larger than 100MB."),
            },
            {
                "name": "find . -empty",
                "description": _("Finds empty files and directories."),
            },
        ],
    },
    {
        "category": _("Essentials: Search & System Information"),
        "command": "whoami",
        "general_description": _("Shows the username of the currently logged-in user."),
        "variations": [
            {
                "name": "whoami",
                "description": _("Displays your current username."),
            },
        ],
    },
    {
        "category": _("Essentials: Search & System Information"),
        "command": "date",
        "general_description": _("Displays the current system date and time."),
        "variations": [
            {"name": "date", "description": _("Shows the current date and time.")},
            {
                "name": "date +'%Y-%m-%d %H:%M:%S'",
                "description": _("Shows the date and time in a custom format."),
            },
        ],
    },
    {
        "category": _("Essentials: Search & System Information"),
        "command": "df",
        "general_description": _("Shows the free and used space on storage disks."),
        "variations": [
            {
                "name": "df -h",
                "description": _(
                    "Shows disk usage in a human-readable format (KB, MB, GB)."
                ),
            },
        ],
    },
    {
        "category": _("Essentials: Search & System Information"),
        "command": "free",
        "general_description": _("Shows the amount of used and free RAM."),
        "variations": [
            {
                "name": "free -h",
                "description": _(
                    "Shows memory usage in a human-readable format (KB, MB, GB)."
                ),
            },
        ],
    },
    {
        "category": _("Essentials: Search & System Information"),
        "command": "du",
        "general_description": _(
            "Shows the disk space that files and directories are using."
        ),
        "variations": [
            {
                "name": "du -sh *",
                "description": _(
                    "Shows the total size of each item in the current directory in a summary format."
                ),
            },
            {
                "name": "du -h",
                "description": _(
                    "Shows the size of each directory and subdirectory in a human-readable format."
                ),
            },
        ],
    },
    {
        "category": _("Essentials: Search & System Information"),
        "command": "uptime",
        "general_description": _(
            "Shows how long the computer has been running without a restart."
        ),
        "variations": [
            {
                "name": "uptime",
                "description": _(
                    "Displays the uptime, how many users are logged in, and the system load."
                ),
            },
        ],
    },
    # Category: Software Management
    {
        "category": _("Software Management"),
        "command": "pacman",
        "general_description": _(
            "The primary tool for installing, updating, and removing software on Arch-based Linux systems."
        ),
        "variations": [
            {
                "name": "sudo pacman -Syu",
                "description": _(
                    "Synchronizes with repositories and upgrades all installed packages."
                ),
            },
            {
                "name": "sudo pacman -Sy package_name",
                "description": _("Installs a new software package."),
            },
            {
                "name": "pacman -Ss search_term",
                "description": _("Searches for a package in the repositories."),
            },
            {
                "name": "pacman -Qs search_term",
                "description": _("Searches for an already installed package."),
            },
            {
                "name": "pacman -Si package_name",
                "description": _(
                    "Shows detailed information about a package from the repository."
                ),
            },
            {
                "name": "pacman -Qi package_name",
                "description": _(
                    "Shows detailed information about an installed package."
                ),
            },
            {
                "name": "sudo pacman -Rns package_name",
                "description": _("Removes a package and its unneeded dependencies."),
            },
            {
                "name": "sudo pacman -Fy",
                "description": _(
                    "Updates the database of which package owns which file."
                ),
            },
            {
                "name": "pacman -F filename",
                "description": _("Finds which package a specific file belongs to."),
            },
        ],
    },
    {
        "category": _("Software Management"),
        "command": "yay",
        "general_description": _(
            "An AUR helper for Arch-based systems that wraps pacman and adds support for the Arch User Repository."
        ),
        "variations": [
            {
                "name": "yay -Syu",
                "description": _(
                    "Upgrades all packages from official repositories and the AUR."
                ),
            },
            {
                "name": "yay -S package_name",
                "description": _(
                    "Installs a package from the official repositories or the AUR."
                ),
            },
            {
                "name": "yay -Ss search_term",
                "description": _(
                    "Searches for a package in both official repositories and the AUR."
                ),
            },
            {
                "name": "yay -Rns package_name",
                "description": _("Removes a package and its dependencies."),
            },
        ],
    },
    {
        "category": _("Software Management"),
        "command": "flatpak",
        "general_description": _(
            "A universal system for installing and running applications that works across different Linux distributions."
        ),
        "variations": [
            {
                "name": "flatpak install flathub org.gimp.GIMP",
                "description": _(
                    "Installs an application from a remote repository like Flathub."
                ),
            },
            {
                "name": "flatpak run org.gimp.GIMP",
                "description": _("Runs an installed Flatpak application."),
            },
            {
                "name": "flatpak update",
                "description": _("Updates all installed Flatpak applications."),
            },
            {
                "name": "flatpak search gimp",
                "description": _(
                    "Searches for applications in the configured remotes."
                ),
            },
            {
                "name": "flatpak list",
                "description": _("Lists all installed Flatpak applications."),
            },
            {
                "name": "flatpak uninstall org.gimp.GIMP",
                "description": _("Removes an installed Flatpak application."),
            },
        ],
    },
    # Category: Permissions & Ownership
    {
        "category": _("Permissions & Ownership"),
        "command": "sudo",
        "general_description": _(
            "Executes a single command with administrative (root) privileges. Use with care."
        ),
        "variations": [
            {
                "name": "sudo pacman -Syu",
                "description": _(
                    "Runs the 'pacman -Syu' command as the administrator, which is required for system updates."
                ),
            },
        ],
    },
    {
        "category": _("Permissions & Ownership"),
        "command": "chmod",
        "general_description": _(
            "Changes the permissions of files and directories (who can read, write, or execute them)."
        ),
        "variations": [
            {
                "name": "chmod +x script.sh",
                "description": _(
                    "Adds execute (x) permission, allowing the file to be run as a program."
                ),
            },
            {
                "name": "chmod 755 file",
                "description": _(
                    "Sets permissions using numbers: owner can read/write/execute (7), group and others can read/execute (5)."
                ),
            },
        ],
    },
    {
        "category": _("Permissions & Ownership"),
        "command": "chown",
        "general_description": _("Changes the owner and group of a file or directory."),
        "variations": [
            {
                "name": "sudo chown user:group file.txt",
                "description": _(
                    "Makes 'user' the owner and 'group' the group for the file."
                ),
            },
        ],
    },
    # Category: Process & Network Management
    {
        "category": _("Process & Network Management"),
        "command": "ps",
        "general_description": _(
            "Shows the programs (processes) currently running on the system."
        ),
        "variations": [
            {
                "name": "ps aux",
                "description": _("Shows a detailed list of all running programs."),
            },
        ],
    },
    {
        "category": _("Process & Network Management"),
        "command": "kill",
        "general_description": _(
            "Terminates a program (process) that is frozen or unresponsive."
        ),
        "variations": [
            {
                "name": "kill 1234",
                "description": _("Politely tries to close the program with ID 1234."),
            },
            {
                "name": "kill -9 1234",
                "description": _(
                    "Forcibly terminates the program with ID 1234 (use as a last resort)."
                ),
            },
        ],
    },
    {
        "category": _("Process & Network Management"),
        "command": "killall",
        "general_description": _(
            "Terminates all processes with a specific name, like closing all windows of an app."
        ),
        "variations": [
            {
                "name": "killall firefox",
                "description": _(
                    "Closes all running instances of the 'firefox' program."
                ),
            },
            {
                "name": "killall -i chrome",
                "description": _(
                    "Asks for confirmation before terminating each 'chrome' process."
                ),
            },
        ],
    },
    {
        "category": _("Process & Network Management"),
        "command": "ping",
        "general_description": _(
            "Tests if there is a connection to another computer or website."
        ),
        "variations": [
            {
                "name": "ping google.com",
                "description": _("Checks if you can communicate with google.com."),
            },
            {
                "name": "ping -c 4 google.com",
                "description": _(
                    "Sends only 4 test packets instead of running continuously."
                ),
            },
        ],
    },
    # Category: System & User Control
    {
        "category": _("System & User Control"),
        "command": "shutdown",
        "general_description": _(
            "Safely turns off or schedules a shutdown for the computer."
        ),
        "variations": [
            {
                "name": "sudo shutdown now",
                "description": _("Shuts down the system immediately."),
            },
            {
                "name": "sudo shutdown +15",
                "description": _("Schedules a shutdown for 15 minutes from now."),
            },
        ],
    },
    {
        "category": _("System & User Control"),
        "command": "reboot",
        "general_description": _("Restarts the computer."),
        "variations": [
            {
                "name": "sudo reboot",
                "description": _("Restarts the system immediately."),
            },
        ],
    },
    {
        "category": _("System & User Control"),
        "command": "su",
        "general_description": _(
            "Switches to another user account in the current terminal session."
        ),
        "variations": [
            {
                "name": "su username",
                "description": _(
                    "Switches to the specified user's account (requires their password)."
                ),
            },
        ],
    },
    # Category: Version Control with Git
    {
        "category": _("Version Control with Git"),
        "command": "git",
        "general_description": _(
            "A tool for saving the history of changes in code projects, like a 'save point' in a game."
        ),
        "variations": [
            {
                "name": "git init",
                "description": _(
                    "Initializes a new 'history album' (repository) in a directory."
                ),
            },
            {
                "name": "git clone [url]",
                "description": _(
                    "Downloads a copy of a project that already exists elsewhere."
                ),
            },
            {
                "name": "git status",
                "description": _(
                    "Shows which files have been modified, added, or deleted."
                ),
            },
            {
                "name": "git add [file]",
                "description": _(
                    "Prepares a modified file to be saved in the history ('stages' the change)."
                ),
            },
            {
                "name": "git commit -m 'message'",
                "description": _(
                    "Saves the staged changes to the history with a description of what was done."
                ),
            },
            {
                "name": "git pull",
                "description": _("Downloads the latest updates from a remote project."),
            },
            {
                "name": "git push",
                "description": _(
                    "Uploads your saved changes (commits) to the remote project."
                ),
            },
            {
                "name": "git log",
                "description": _("Shows the history of all commits (saves) made."),
            },
            {
                "name": "git branch",
                "description": _(
                    "Lists all the 'timelines' (branches) of the project."
                ),
            },
            {
                "name": "git switch [branch-name]",
                "description": _(
                    "Switches to another 'timeline' (branch) to work on something new."
                ),
            },
        ],
    },
    # Category: Archives & Compression
    {
        "category": _("Archives & Compression"),
        "command": "tar",
        "general_description": _(
            "Groups multiple files and directories into a single package (.tar file) or extracts them."
        ),
        "variations": [
            {
                "name": "tar -czvf archive.tar.gz directory/",
                "description": _("Creates a compressed package from a directory."),
            },
            {
                "name": "tar -xvf archive.tar.gz",
                "description": _("Extracts files from a compressed package."),
            },
            {
                "name": "tar -cvf archive.tar directory/",
                "description": _("Creates a package without compression."),
            },
        ],
    },
    {
        "category": _("Archives & Compression"),
        "command": "zip",
        "general_description": _(
            "Creates compressed .zip files, a format widely used on all operating systems."
        ),
        "variations": [
            {
                "name": "zip archive.zip file1.txt file2.txt",
                "description": _(
                    "Creates a zip file containing one or more specified files."
                ),
            },
            {
                "name": "zip -r archive.zip directory/",
                "description": _(
                    "Creates a zip file containing a directory and everything inside it."
                ),
            },
        ],
    },
    {
        "category": _("Archives & Compression"),
        "command": "unzip",
        "general_description": _("Extracts files from a .zip archive."),
        "variations": [
            {
                "name": "unzip archive.zip",
                "description": _(
                    "Extracts all files from the archive into the current directory."
                ),
            },
            {
                "name": "unzip archive.zip -d /path/to/destination",
                "description": _("Extracts files to a specific destination directory."),
            },
        ],
    },
    # Category: Useful Commands (Optional)
    {
        "category": _("Useful Commands (Optional)"),
        "command": "htop",
        "general_description": _(
            "A visual 'Task Manager' for the terminal. (May require installation)"
        ),
        "variations": [
            {
                "name": "htop",
                "description": _(
                    "Interactively shows running programs, CPU, and memory usage."
                ),
            },
        ],
    },
    {
        "category": _("Useful Commands (Optional)"),
        "command": "nvtop",
        "general_description": _(
            "Monitors the usage of video cards (GPUs), especially NVIDIA. (May require installation)"
        ),
        "variations": [
            {
                "name": "nvtop",
                "description": _(
                    "Shows GPU usage, video memory, and temperature in real-time."
                ),
            },
        ],
    },
    {
        "category": _("Useful Commands (Optional)"),
        "command": "lspci",
        "general_description": _(
            "Lists all devices connected to the computer's PCI slots (graphics card, network, etc.)."
        ),
        "variations": [
            {
                "name": "lspci",
                "description": _("Shows a list of all PCI devices."),
            },
        ],
    },
    {
        "category": _("Useful Commands (Optional)"),
        "command": "lsusb",
        "general_description": _("Lists all devices connected to the USB ports."),
        "variations": [
            {
                "name": "lsusb",
                "description": _("Shows a list of all connected USB devices."),
            },
        ],
    },
    {
        "category": _("Useful Commands (Optional)"),
        "command": "rg",
        "general_description": _(
            "A modern and super-fast version of 'grep' for searching text. (May require installation: ripgrep)"
        ),
        "variations": [
            {
                "name": "rg 'word'",
                "description": _(
                    "Recursively searches for 'word' in the current directory very quickly."
                ),
            },
        ],
    },
    {
        "category": _("Useful Commands (Optional)"),
        "command": "fd",
        "general_description": _(
            "A modern and more intuitive version of 'find' for finding files. (May require installation: fd-find)"
        ),
        "variations": [
            {
                "name": "fd '.txt'",
                "description": _("Finds all files containing '.txt' in their name."),
            },
        ],
    },
    {
        "category": _("Useful Commands (Optional)"),
        "command": "jq",
        "general_description": _(
            "A tool for processing and viewing data in JSON format. (May require installation)"
        ),
        "variations": [
            {
                "name": "cat data.json | jq '.'",
                "description": _(
                    "Displays a JSON file with colors and formatting for easy reading."
                ),
            },
        ],
    },
    # Category: Text Processing (Intermediate)
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "sort",
        "general_description": _("Sorts the lines of a file."),
        "variations": [
            {
                "name": "sort file.txt",
                "description": _("Sorts lines in alphabetical order."),
            },
            {
                "name": "sort -n numbers.txt",
                "description": _("Sorts lines in numerical order."),
            },
            {
                "name": "sort -r file.txt",
                "description": _("Sorts lines in reverse order."),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "uniq",
        "general_description": _("Removes adjacent duplicate lines."),
        "variations": [
            {
                "name": "sort file.txt | uniq",
                "description": _("First sorts the file, then removes duplicate lines."),
            },
            {
                "name": "sort file.txt | uniq -c",
                "description": _("Counts how many times each line appears."),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "wc",
        "general_description": _("Counts lines, words, and characters in a file."),
        "variations": [
            {
                "name": "wc -l file.txt",
                "description": _("Counts only the number of lines."),
            },
            {
                "name": "wc -w file.txt",
                "description": _("Counts only the number of words."),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "cut",
        "general_description": _("Cuts and extracts columns of text from a file."),
        "variations": [
            {
                "name": "cut -d ':' -f 1 /etc/passwd",
                "description": _(
                    "Extracts the first column of text, using ':' as the separator."
                ),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "paste",
        "general_description": _("Merges the lines of multiple files side-by-side."),
        "variations": [
            {
                "name": "paste file1.txt file2.txt",
                "description": _("Combines the lines of two files into columns."),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "tr",
        "general_description": _("Translates or deletes characters from text."),
        "variations": [
            {
                "name": "echo 'hello world' | tr 'a-z' 'A-Z'",
                "description": _("Converts text from lowercase to uppercase."),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "rev",
        "general_description": _("Reverses the order of characters in each line."),
        "variations": [
            {
                "name": "echo 'desserts' | rev",
                "description": _("Reverses a string (result: 'stressed')."),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "tac",
        "general_description": _(
            "Displays the content of a file backwards (last line first)."
        ),
        "variations": [
            {
                "name": "tac file.txt",
                "description": _("Displays the lines of a file in reverse order."),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "seq",
        "general_description": _("Prints a sequence of numbers."),
        "variations": [
            {"name": "seq 10", "description": _("Prints numbers from 1 to 10.")},
            {
                "name": "seq 0 2 10",
                "description": _("Prints numbers from 0 to 10, incrementing by 2."),
            },
        ],
    },
    {
        "category": _("Text Processing (Intermediate)"),
        "command": "bc",
        "general_description": _("A calculator for the terminal."),
        "variations": [
            {
                "name": "echo '5 + 3' | bc",
                "description": _("Performs a basic mathematical calculation."),
            },
            {
                "name": "echo 'scale=2; 10/3' | bc",
                "description": _("Performs division with 2 decimal places."),
            },
        ],
    },
    # Category: Advanced: Scripting & Shell
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "if",
        "general_description": _(
            "Executes command blocks only if a condition is true."
        ),
        "variations": [
            {
                "name": "if [[ -e 'file.txt' ]]; then\n    echo 'File exists'\nfi",
                "description": _("Tests if a file exists."),
            },
            {
                "name": 'if [[ "$VAR" -eq 5 ]]; then\n    echo "VAR is 5"\nfi',
                "description": _("Tests if a numeric variable is equal to 5."),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "for",
        "general_description": _(
            "Creates a loop that repeats an action for each item in a list."
        ),
        "variations": [
            {
                "name": "for i in 1 2 3; do\n    echo $i\ndone",
                "description": _(
                    "Repeats the 'echo' command for the numbers 1, 2, and 3."
                ),
            },
            {
                "name": 'for f in *.txt; do\n    echo "$f"\ndone',
                "description": _(
                    "Performs an action for every file that ends with .txt."
                ),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "while",
        "general_description": _(
            "Creates a loop that continues to execute as long as a condition is true."
        ),
        "variations": [
            {
                "name": "COUNT=0\nwhile [ $COUNT -lt 5 ]; do\n    echo $COUNT\n    ((COUNT++))\ndone",
                "description": _(
                    "Executes the code block as long as the COUNT variable is less than 5."
                ),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "until",
        "general_description": _(
            "Creates a loop that continues to execute until a condition becomes true."
        ),
        "variations": [
            {
                "name": "COUNT=5\nuntil [ $COUNT -eq 0 ]; do\n    echo $COUNT\n    ((COUNT--))\ndone",
                "description": _(
                    "Executes the code block until the COUNT variable is equal to 0."
                ),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "read",
        "general_description": _("Reads user input and stores it in a variable."),
        "variations": [
            {
                "name": "read -p 'Enter your name: ' NAME\necho \"Hello, $NAME!\"",
                "description": _(
                    "Displays a prompt and waits for the user to type something."
                ),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "test",
        "general_description": _(
            "Checks if a condition is true or false, used for making decisions in scripts."
        ),
        "variations": [
            {
                "name": '[[ -z "$name" ]]',
                "description": _("Checks if a variable is empty."),
            },
            {
                "name": '[[ -n "$name" ]]',
                "description": _("Checks if a variable is not empty."),
            },
            {
                "name": '[[ "$user" == "bruno" ]]',
                "description": _("Checks if two pieces of text are exactly the same."),
            },
            {
                "name": '[[ "$user" != "guest" ]]',
                "description": _("Checks if two pieces of text are different."),
            },
            {
                "name": "[[ 5 -eq 10 ]]",
                "description": _("Checks if two numbers are equal."),
            },
            {
                "name": "[[ 3 -ne 7 ]]",
                "description": _("Checks if two numbers are not equal."),
            },
            {
                "name": "[[ 5 -lt 10 ]]",
                "description": _("Checks if the first number is less than the second."),
            },
            {
                "name": "[[ 10 -le 10 ]]",
                "description": _(
                    "Checks if the first number is less than or equal to the second."
                ),
            },
            {
                "name": "[[ 15 -gt 10 ]]",
                "description": _(
                    "Checks if the first number is greater than the second."
                ),
            },
            {
                "name": "[[ 10 -ge 10 ]]",
                "description": _(
                    "Checks if the first number is greater than or equal to the second."
                ),
            },
            {
                "name": '[[ "$email" =~ "@.*\\.com$" ]]',
                "description": _(
                    "Checks if text matches a pattern (e.g., is a valid email)."
                ),
            },
            {
                "name": '[[ ! -z "$input" ]]',
                "description": _("NOT: Reverses the result of a check."),
            },
            {
                "name": '[[ -n "$user" && -n "$pass" ]]',
                "description": _("AND: Checks if both conditions are true."),
            },
            {
                "name": '[[ "$role" == "admin" || "$role" == "root" ]]',
                "description": _("OR: Checks if at least one condition is true."),
            },
            {
                "name": '[[ -e "/home/bruno/file.txt" ]]',
                "description": _("Checks if a file or directory exists."),
            },
            {
                "name": '[[ -r "/etc/passwd" ]]',
                "description": _("Checks if you have permission to read a file."),
            },
            {
                "name": '[[ -d "/home/bruno" ]]',
                "description": _("Checks if the path is a directory."),
            },
            {
                "name": '[[ -w "/tmp/test.txt" ]]',
                "description": _("Checks if you have permission to write to a file."),
            },
            {
                "name": '[[ -s "/var/log/syslog" ]]',
                "description": _("Checks if a file is not empty."),
            },
            {
                "name": '[[ -f "/home/bruno/script.sh" ]]',
                "description": _(
                    "Checks if the path is a regular file (not a directory)."
                ),
            },
            {
                "name": '[[ -x "/usr/bin/bash" ]]',
                "description": _("Checks if you have permission to run a file."),
            },
            {
                "name": '[[ "new.txt" -nt "old.txt" ]]',
                "description": _("Checks if the first file is newer than the second."),
            },
            {
                "name": '[[ "old.txt" -ot "new.txt" ]]',
                "description": _("Checks if the first file is older than the second."),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "array",
        "general_description": _(
            "Array operations for storing multiple values in a single variable."
        ),
        "variations": [
            {
                "name": "files=('script.sh' 'config.txt' 'data.log')",
                "description": _("Creates a list with several items."),
            },
            {
                "name": 'echo "${files[0]}"',
                "description": _("Accesses the first item in the list."),
            },
            {
                "name": 'echo "${files[-1]}"',
                "description": _("Accesses the last item in the list."),
            },
            {
                "name": 'echo "${files[@]}"',
                "description": _("Displays all items in the list."),
            },
            {
                "name": 'echo "${#files[@]}"',
                "description": _("Shows how many items are in the list."),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "brace",
        "general_description": _("Brace expansion to quickly generate text sequences."),
        "variations": [
            {
                "name": "echo {a,b,c}.txt",
                "description": _("Generates: a.txt b.txt c.txt"),
            },
            {
                "name": "echo {1..5}",
                "description": _("Generates a sequence of numbers: 1 2 3 4 5."),
            },
            {
                "name": "mkdir -p project/{src,docs,tests}",
                "description": _("Creates multiple directories at once."),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "Parameter Expansion",
        "general_description": _(
            "Advanced ways to modify or get information from variables."
        ),
        "variations": [
            {
                "name": "${#variable}",
                "description": _("Gets the length of the text in a variable."),
            },
            {
                "name": "${variable:-default}",
                "description": _("Uses 'default' if the variable is empty or not set."),
            },
            {
                "name": "${variable:=default}",
                "description": _(
                    "Uses 'default' if empty, and also sets the variable to 'default'."
                ),
            },
            {
                "name": "${variable:?error_message}",
                "description": _(
                    "Shows an error and stops the script if the variable is empty."
                ),
            },
            {
                "name": "${variable:+value_if_set}",
                "description": _(
                    "Uses 'value_if_set' only if the variable has content."
                ),
            },
            {
                "name": "${variable:3}",
                "description": _("Gets text starting from the 4th character."),
            },
            {
                "name": "${variable:3:5}",
                "description": _(
                    "Gets 5 characters of text, starting from the 4th character."
                ),
            },
            {
                "name": "${variable#prefix}",
                "description": _(
                    "Removes the shortest matching 'prefix' from the beginning."
                ),
            },
            {
                "name": "${variable##prefix}",
                "description": _(
                    "Removes the longest matching 'prefix' from the beginning."
                ),
            },
            {
                "name": "${variable%suffix}",
                "description": _(
                    "Removes the shortest matching 'suffix' from the end."
                ),
            },
            {
                "name": "${variable%%suffix}",
                "description": _("Removes the longest matching 'suffix' from the end."),
            },
            {
                "name": "${variable/old/new}",
                "description": _("Replaces the first match of 'old' with 'new'."),
            },
            {
                "name": "${variable//old/new}",
                "description": _("Replaces all matches of 'old' with 'new'."),
            },
            {
                "name": "${variable/#old/new}",
                "description": _(
                    "Replaces 'old' with 'new' only if it's at the beginning."
                ),
            },
            {
                "name": "${variable/%old/new}",
                "description": _("Replaces 'old' with 'new' only if it's at the end."),
            },
            {
                "name": "${variable^}",
                "description": _("Changes the first letter to uppercase."),
            },
            {
                "name": "${variable^^}",
                "description": _("Changes all letters to uppercase."),
            },
            {
                "name": "${variable,}",
                "description": _("Changes the first letter to lowercase."),
            },
            {
                "name": "${variable,,}",
                "description": _("Changes all letters to lowercase."),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "Regular Expressions (Regex)",
        "general_description": _(
            "A 'mini-language' for finding text patterns. These are building blocks for tools like grep, sed, and awk."
        ),
        "variations": [
            {
                "name": ".",
                "description": _(
                    "Dot: Matches any single character. 'h.t' finds 'hot', 'hat', and 'h t'."
                ),
            },
            {
                "name": "*",
                "description": _(
                    "Asterisk: Matches zero or more repetitions of the preceding item. 'a*' finds '', 'a', 'aa'."
                ),
            },
            {
                "name": "+",
                "description": _(
                    "Plus: Matches one or more repetitions of the preceding item. 'a+' finds 'a', 'aa', but not ''."
                ),
            },
            {
                "name": "?",
                "description": _(
                    "Question Mark: Matches the preceding item zero or one time (makes it optional). 'colou?r' matches 'color' and 'colour'."
                ),
            },
            {
                "name": "^",
                "description": _(
                    "Caret: Matches the beginning of a line. '^Hello' only finds lines that start with 'Hello'."
                ),
            },
            {
                "name": "$",
                "description": _(
                    "Dollar Sign: Matches the end of a line. 'end$' only finds lines that end with 'end'."
                ),
            },
            {
                "name": "[ ]",
                "description": _(
                    "Brackets: Matches any of the characters inside. '[abc]' finds 'a', 'b', or 'c'."
                ),
            },
            {
                "name": "[^ ]",
                "description": _(
                    "Negated Brackets: Matches any character NOT inside. '[^abc]' finds 'd', 'e', 'f', etc."
                ),
            },
            {
                "name": "( )",
                "description": _(
                    "Parentheses: Groups expressions. '(ab)+' finds 'ab', 'abab', 'ababab'."
                ),
            },
            {
                "name": "|",
                "description": _(
                    "Pipe: Acts as an 'OR'. 'cat|dog' finds 'cat' or 'dog'."
                ),
            },
            {
                "name": "{n,m}",
                "description": _(
                    "Curly Braces: Matches a specific number of repetitions. e.g., 'a{2,4}' finds 'aa', 'aaa', 'aaaa'."
                ),
            },
            {
                "name": "\\d",
                "description": _("Digit: Matches any single number from 0 to 9."),
            },
            {
                "name": "\\w",
                "description": _(
                    "Word Character: Matches any letter, number, or underscore."
                ),
            },
            {
                "name": "\\s",
                "description": _(
                    "Whitespace: Matches any space, tab, or newline character."
                ),
            },
            {
                "name": "\\",
                "description": _(
                    "Backslash: Escapes a special character, treating it literally. '\\.' matches a literal dot."
                ),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "sed",
        "general_description": _(
            "A command-line text editor, great for replacing words in files."
        ),
        "variations": [
            {
                "name": "sed 's/old/new/g' file.txt",
                "description": _(
                    "Replaces all occurrences of 'old' with 'new' in the file."
                ),
            },
            {
                "name": "sed '/^#/d' config.txt",
                "description": _("Deletes all lines that start with # (comments)."),
            },
            {
                "name": "sed -i 's/old/new/g' file.txt",
                "description": _(
                    "Saves the modification directly in the original file."
                ),
            },
        ],
    },
    {
        "category": _("Advanced: Scripting & Shell"),
        "command": "awk",
        "general_description": _(
            "An advanced tool for processing text, especially column-based data."
        ),
        "variations": [
            {
                "name": "awk '{print $1}' file.txt",
                "description": _("Displays only the first column (word) of each line."),
            },
            {
                "name": "awk '{print $NF}' file.txt",
                "description": _("Displays only the last column of each line."),
            },
            {
                "name": "awk '$3 > 100 {print $1}' data.txt",
                "description": _(
                    "Displays the value of column 1 only if the value of column 3 is greater than 100."
                ),
            },
        ],
    },
]
