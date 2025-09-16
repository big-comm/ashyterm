# ashyterm/data/command_data.py
from ..utils.translation_utils import _

# The data structure was changed to group command variations.
NATIVE_COMMANDS = [
    {
        "category": _("File Navigation and Listing"),
        "command": "ls",
        "general_description": _("Lists the contents of a directory."),
        "variations": [
            {
                "name": "ls -l",
                "description": _(
                    "Uses a long listing format, showing permissions, owner, size and date."
                ),
            },
            {
                "name": "ls -a",
                "description": _(
                    "Shows all files, including hidden ones (that start with '.')."
                ),
            },
            {
                "name": "ls -lh",
                "description": _(
                    "Long format with human-readable file sizes (KB, MB, GB)."
                ),
            },
            {
                "name": "ls -t",
                "description": _(
                    "Sorts files by modification date, most recent first."
                ),
            },
        ],
    },
    {
        "category": _("File Navigation and Listing"),
        "command": "cd",
        "general_description": _("Changes the current working directory."),
        "variations": [
            {
                "name": "cd /path/to/directory",
                "description": _("Navigates to a specific absolute path."),
            },
            {
                "name": "cd ..",
                "description": _("Goes up one level, to the parent directory."),
            },
            {
                "name": "cd ~",
                "description": _(
                    "Goes directly to your 'home' directory. Same as 'cd' with no arguments."
                ),
            },
            {
                "name": "cd -",
                "description": _("Returns to the last directory you were in."),
            },
        ],
    },
    {
        "category": _("File Manipulation"),
        "command": "cp",
        "general_description": _("Copies files or directories."),
        "variations": [
            {
                "name": "cp source_file destination_file",
                "description": _("Copies a single file."),
            },
            {
                "name": "cp -r source_directory/ destination_directory/",
                "description": _(
                    "Copies a directory and all its contents recursively."
                ),
            },
            {
                "name": "cp -v source_file destination_file",
                "description": _("Verbose mode, shows what is being copied."),
            },
        ],
    },
    {
        "category": _("File Manipulation"),
        "command": "mv",
        "general_description": _("Moves or renames files and directories."),
        "variations": [
            {
                "name": "mv old_name new_name",
                "description": _(
                    "Renames a file or directory in the current location."
                ),
            },
            {
                "name": "mv file.txt /new/directory/",
                "description": _("Moves a file to a new location."),
            },
        ],
    },
    {
        "category": _("File Manipulation"),
        "command": "rm",
        "general_description": _(
            "Removes (deletes) files or directories. CAUTION: this action is permanent."
        ),
        "variations": [
            {"name": "rm file.txt", "description": _("Removes a single file.")},
            {
                "name": "rm -r directory/",
                "description": _("Removes a directory and all its contents."),
            },
            {
                "name": "rm -i file.txt",
                "description": _(
                    "Interactive mode, asks for confirmation before removing."
                ),
            },
            {
                "name": "rm -f file.txt",
                "description": _(
                    "Forces removal without asking for confirmation (use with caution)."
                ),
            },
        ],
    },
    {
        "category": _("Search and Filtering"),
        "command": "grep",
        "general_description": _(
            "Searches for a text pattern within files or other command outputs."
        ),
        "variations": [
            {
                "name": "grep 'word' file.txt",
                "description": _(
                    "Finds and displays all lines containing 'word' in the file."
                ),
            },
            {
                "name": "grep -i 'word' file.txt",
                "description": _(
                    "Searches ignoring case differences between uppercase and lowercase."
                ),
            },
            {
                "name": "grep -r 'word' .",
                "description": _(
                    "Searches recursively for 'word' in all files in the current directory."
                ),
            },
            {
                "name": "ls -l | grep 'txt'",
                "description": _(
                    "Filters the output of 'ls -l' to show only lines containing 'txt'."
                ),
            },
        ],
    },
    {
        "category": _("Flow Control (Shell Script)"),
        "command": "if",
        "general_description": _("Executes command blocks conditionally."),
        "variations": [
            {
                "name": "if [ -f 'file.txt' ]; then\n    echo 'File exists'\nfi",
                "description": _("Tests if a file exists and is a regular file."),
            },
            {
                "name": "if [ -d 'directory' ]; then\n    echo 'It is a directory'\nfi",
                "description": _("Tests if a path exists and is a directory."),
            },
            {
                "name": 'if [ "$VAR" -eq 5 ]; then\n    echo "VAR is 5"\nfi',
                "description": _("Tests if a numeric variable is equal to 5."),
            },
        ],
    },
    {
        "category": _("Flow Control (Shell Script)"),
        "command": "for",
        "general_description": _("Creates a loop that iterates over a list of items."),
        "variations": [
            {
                "name": "for i in 1 2 3; do\n    echo $i\n    sleep 1\ndone",
                "description": _("Iterates over an explicit list of numbers."),
            },
            {
                "name": 'for f in *.txt; do\n    echo "Processing $f"\n    # commands here\ndone',
                "description": _(
                    "Iterates over all files ending with .txt in the directory."
                ),
            },
        ],
    },
    {
        "category": _("Flow Control (Shell Script)"),
        "command": "while",
        "general_description": _(
            "Creates a loop that continues while a condition is true."
        ),
        "variations": [
            {
                "name": "COUNT=0\nwhile [ $COUNT -lt 5 ]; do\n    echo $COUNT\n    COUNT=$((COUNT+1))\ndone",
                "description": _(
                    "Executes the code block while the COUNT variable is less than 5."
                ),
            },
        ],
    },
    {
        "category": _("Flow Control (Shell Script)"),
        "command": "until",
        "general_description": _(
            "Creates a loop that continues while a condition is false."
        ),
        "variations": [
            {
                "name": "COUNT=5\nuntil [ $COUNT -eq 0 ]; do\n    echo $COUNT\n    COUNT=$((COUNT-1))\ndone",
                "description": _(
                    "Executes the code block until the COUNT variable equals 0."
                ),
            },
        ],
    },
    {
        "category": _("User Input (Shell Script)"),
        "command": "read",
        "general_description": _(
            "Reads a line from standard input and stores it in a variable."
        ),
        "variations": [
            {
                "name": "read NAME",
                "description": _(
                    "Waits for the user to type something and press Enter, saving it in the NAME variable."
                ),
            },
            {
                "name": "read -p 'Enter your name: ' NAME\necho \"Hello, $NAME!\"",
                "description": _(
                    "Shows a message (prompt) to the user before waiting for input."
                ),
            },
        ],
    },
    {
        "category": _("Parameter Expansion"),
        "command": "Parameter Expansion",
        "general_description": _(
            "Advanced shell mechanisms for manipulating variable values and strings."
        ),
        "variations": [
            {
                "name": 'name="bruno"\necho "$name"',
                "description": _("Basic variable substitution using braces."),
            },
            {
                "name": 'echo "${name/b/B}"',
                "description": _("String substitution - replaces first 'b' with 'B'."),
            },
            {
                "name": 'echo "${name:0:2}"',
                "description": _("Substring extraction - first 2 characters."),
            },
            {
                "name": 'echo "${name::2}"',
                "description": _("Substring from start - first 2 characters."),
            },
            {
                "name": 'echo "${name::-1}"',
                "description": _("Substring excluding last character."),
            },
            {
                "name": 'echo "${name:(-1)}"',
                "description": _("Substring from right - last character."),
            },
            {
                "name": 'echo "${name:(-2):1}"',
                "description": _("Substring from right - second to last character."),
            },
            {
                "name": 'length=2\necho "${name:0:length}"',
                "description": _("Substring using variable length parameter."),
            },
            {
                "name": 'str="/home/biglinux/project/main.py"\necho "${str%.py}"',
                "description": _("Remove file extension (.py)."),
            },
            {
                "name": 'echo "${str%.py}.bak"',
                "description": _("Replace file extension (.py to .bak)."),
            },
            {
                "name": 'echo "${str%/*}"',
                "description": _("Remove everything after last slash (get directory)."),
            },
            {
                "name": 'echo "${str##*.}"',
                "description": _("Extract file extension (longest match)."),
            },
            {
                "name": 'echo "${str##*/}"',
                "description": _("Extract filename from path (longest match)."),
            },
            {
                "name": 'echo "${str#*/}"',
                "description": _("Remove shortest prefix match up to first slash."),
            },
            {
                "name": 'echo "${str/project/scripts}"',
                "description": _(
                    "Replace first occurrence of 'project' with 'scripts'."
                ),
            },
            {
                "name": 'str="Hello bruno"\necho "${str:6:5}"',
                "description": _("Extract substring starting at position 6, length 5."),
            },
            {
                "name": 'echo "${str: -5:5}"',
                "description": _("Extract last 5 characters using negative offset."),
            },
            {
                "name": 'src="/home/biglinux/code/main.c"\nbase=${src##*/}',
                "description": _("Extract basename (filename) from path."),
            },
            {
                "name": "dir=${src%$base}",
                "description": _("Extract directory path by removing basename."),
            },
            {
                "name": "user_a=bruno\nuser_b=maria\necho ${!user_*}",
                "description": _("List all variable names starting with 'user_'."),
            },
            {
                "name": "name=bruno\npointer=name\necho ${!pointer}",
                "description": _("Indirect variable expansion using pointer."),
            },
            {
                "name": "${filename%suffix}",
                "description": _("Remove shortest suffix match."),
            },
            {
                "name": "${filename#prefix}",
                "description": _("Remove shortest prefix match."),
            },
            {
                "name": "${filename%%suffix}",
                "description": _("Remove longest suffix match."),
            },
            {
                "name": "${filename##prefix}",
                "description": _("Remove longest prefix match."),
            },
            {
                "name": "${filename/old/new}",
                "description": _("Replace first occurrence of 'old' with 'new'."),
            },
            {
                "name": "${filename//old/new}",
                "description": _("Replace all occurrences of 'old' with 'new'."),
            },
            {
                "name": "${filename/%old/new}",
                "description": _("Replace suffix 'old' with 'new'."),
            },
            {
                "name": "${filename/#old/new}",
                "description": _("Replace prefix 'old' with 'new'."),
            },
            {
                "name": "${filename:0:3}",
                "description": _("Extract substring (position 0, length 3)."),
            },
            {
                "name": "${filename:(-3):3}",
                "description": _("Extract substring from right (last 3 characters)."),
            },
            {
                "name": "${#filename}",
                "description": _("Get length of variable $filename."),
            },
            {
                "name": 'str="ASHY TERMINAL!"\necho "${str,}"',
                "description": _("Convert first character to lowercase."),
            },
            {
                "name": 'echo "${str,,}"',
                "description": _("Convert all characters to lowercase."),
            },
            {
                "name": 'str="ashy terminal!"\necho "${str^}"',
                "description": _("Convert first character to uppercase."),
            },
            {
                "name": 'echo "${str^^}"',
                "description": _("Convert all characters to uppercase."),
            },
            {
                "name": "${username:-guest}",
                "description": _("Use $username, or 'guest' if unset (or null)."),
            },
            {
                "name": "${username:=guest}",
                "description": _(
                    "Set $username to 'guest' if unset (or null), then use it."
                ),
            },
            {
                "name": "${username:+admin}",
                "description": _("Use 'admin' if $username is set (and not null)."),
            },
            {
                "name": "${username:?Username is required}",
                "description": _(
                    "Show error message and exit if $username is unset (or null)."
                ),
            },
            {
                "name": "${username-guest}",
                "description": _(
                    "Use $username, or 'guest' if unset (ignores null values)."
                ),
            },
        ],
    },
    {
        "category": _("File Manipulation"),
        "command": "mkdir",
        "general_description": _("Creates directories."),
        "variations": [
            {
                "name": "mkdir new_directory",
                "description": _("Creates a single directory."),
            },
            {
                "name": "mkdir -p path/to/nested/directory",
                "description": _(
                    "Creates nested directories, creating parent directories as needed."
                ),
            },
        ],
    },
    {
        "category": _("File Manipulation"),
        "command": "touch",
        "general_description": _("Creates empty files or updates file timestamps."),
        "variations": [
            {
                "name": "touch file.txt",
                "description": _(
                    "Creates an empty file or updates the timestamp of an existing file."
                ),
            },
            {
                "name": "touch file1.txt file2.txt file3.txt",
                "description": _("Creates multiple files at once."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "cat",
        "general_description": _("Concatenates and displays file contents."),
        "variations": [
            {
                "name": "cat file.txt",
                "description": _("Displays the contents of a file."),
            },
            {
                "name": "cat file1.txt file2.txt",
                "description": _("Concatenates and displays multiple files."),
            },
            {
                "name": "cat -n file.txt",
                "description": _("Displays file contents with line numbers."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "head",
        "general_description": _("Displays the beginning of files."),
        "variations": [
            {
                "name": "head file.txt",
                "description": _("Displays the first 10 lines of a file."),
            },
            {
                "name": "head -n 20 file.txt",
                "description": _("Displays the first 20 lines of a file."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "tail",
        "general_description": _("Displays the end of files."),
        "variations": [
            {
                "name": "tail file.txt",
                "description": _("Displays the last 10 lines of a file."),
            },
            {
                "name": "tail -f file.txt",
                "description": _(
                    "Follows the file, displaying new lines as they are added."
                ),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "sort",
        "general_description": _("Sorts lines of text files."),
        "variations": [
            {
                "name": "sort file.txt",
                "description": _("Sorts lines in alphabetical order."),
            },
            {
                "name": "sort -n numbers.txt",
                "description": _("Sorts lines numerically."),
            },
            {
                "name": "sort -r file.txt",
                "description": _("Sorts lines in reverse order."),
            },
            {
                "name": "sort -k 2 file.txt",
                "description": _("Sorts by second field/column."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "uniq",
        "general_description": _("Removes duplicate lines from sorted files."),
        "variations": [
            {
                "name": "sort file.txt | uniq",
                "description": _("Removes duplicate lines after sorting."),
            },
            {
                "name": "uniq -c file.txt",
                "description": _("Shows count of occurrences for each line."),
            },
            {
                "name": "uniq -d file.txt",
                "description": _("Shows only duplicate lines."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "wc",
        "general_description": _("Counts lines, words, and characters in files."),
        "variations": [
            {
                "name": "wc file.txt",
                "description": _("Shows lines, words, and characters count."),
            },
            {
                "name": "wc -l file.txt",
                "description": _("Shows only line count."),
            },
            {
                "name": "wc -w file.txt",
                "description": _("Shows only word count."),
            },
            {
                "name": "wc -c file.txt",
                "description": _("Shows only character count."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "cut",
        "general_description": _("Extracts sections from lines of files."),
        "variations": [
            {
                "name": "cut -d ':' -f 1 /etc/passwd",
                "description": _("Extracts first field using colon delimiter."),
            },
            {
                "name": "cut -c 1-10 file.txt",
                "description": _("Extracts first 10 characters from each line."),
            },
            {
                "name": "cut -d ',' -f 2,4 data.csv",
                "description": _("Extracts 2nd and 4th fields from CSV data."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "paste",
        "general_description": _("Merges lines from multiple files."),
        "variations": [
            {
                "name": "paste file1.txt file2.txt",
                "description": _("Merges corresponding lines from two files."),
            },
            {
                "name": "paste -d ',' file1.txt file2.txt",
                "description": _("Merges lines using comma as delimiter."),
            },
            {
                "name": "paste -s file.txt",
                "description": _("Merges all lines into a single line."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "tr",
        "general_description": _("Translates or deletes characters."),
        "variations": [
            {
                "name": "echo 'hello' | tr 'a-z' 'A-Z'",
                "description": _("Converts lowercase to uppercase."),
            },
            {
                "name": "tr -d '\\n' < file.txt",
                "description": _("Removes all newline characters."),
            },
            {
                "name": "tr -s ' ' < file.txt",
                "description": _("Squeezes multiple spaces into single space."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "rev",
        "general_description": _("Reverses lines character by character."),
        "variations": [
            {
                "name": "rev file.txt",
                "description": _("Reverses each line character by character."),
            },
            {
                "name": "echo 'hello' | rev",
                "description": _("Reverses a string."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "tac",
        "general_description": _("Concatenates and prints files in reverse order."),
        "variations": [
            {
                "name": "tac file.txt",
                "description": _("Prints file lines in reverse order."),
            },
            {
                "name": "tac file1.txt file2.txt",
                "description": _("Concatenates files in reverse order."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "seq",
        "general_description": _("Prints a sequence of numbers."),
        "variations": [
            {
                "name": "seq 10",
                "description": _("Prints numbers from 1 to 10."),
            },
            {
                "name": "seq 5 10",
                "description": _("Prints numbers from 5 to 10."),
            },
            {
                "name": "seq 0 2 20",
                "description": _("Prints even numbers from 0 to 20."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "bc",
        "general_description": _("Arbitrary precision calculator language."),
        "variations": [
            {
                "name": "echo '5 + 3' | bc",
                "description": _("Performs basic arithmetic calculation."),
            },
            {
                "name": "echo 'scale=2; 10/3' | bc",
                "description": _("Division with 2 decimal places precision."),
            },
            {
                "name": "bc -l",
                "description": _("Starts bc with math library for advanced functions."),
            },
        ],
    },
    {
        "category": _("System Information"),
        "command": "df",
        "general_description": _("Displays disk space usage."),
        "variations": [
            {
                "name": "df -h",
                "description": _("Shows disk usage in human-readable format."),
            },
            {
                "name": "df -i",
                "description": _("Shows inode usage instead of block usage."),
            },
        ],
    },
    {
        "category": _("System Information"),
        "command": "free",
        "general_description": _("Displays memory usage information."),
        "variations": [
            {
                "name": "free -h",
                "description": _("Shows memory usage in human-readable format."),
            },
            {
                "name": "free -s 5",
                "description": _("Continuously displays memory usage every 5 seconds."),
            },
        ],
    },
    {
        "category": _("System Information"),
        "command": "du",
        "general_description": _("Displays disk usage of files and directories."),
        "variations": [
            {
                "name": "du -h",
                "description": _("Shows disk usage in human-readable format."),
            },
            {
                "name": "du -sh *",
                "description": _("Shows total size of each item in current directory."),
            },
            {
                "name": "du -h --max-depth=1",
                "description": _("Shows disk usage one level deep."),
            },
            {
                "name": "du -ah /home/bruno",
                "description": _(
                    "Shows all files and directories with human-readable sizes."
                ),
            },
        ],
    },
    {
        "category": _("System Information"),
        "command": "uptime",
        "general_description": _("Shows how long the system has been running."),
        "variations": [
            {
                "name": "uptime",
                "description": _(
                    "Shows current time, uptime, users, and load average."
                ),
            },
        ],
    },
    {
        "category": _("System Information"),
        "command": "whoami",
        "general_description": _("Displays the current username."),
        "variations": [
            {
                "name": "whoami",
                "description": _("Prints the current logged-in username."),
            },
        ],
    },
    {
        "category": _("System Information"),
        "command": "pwd",
        "general_description": _("Prints the current working directory."),
        "variations": [
            {
                "name": "pwd",
                "description": _("Shows the full path of current directory."),
            },
        ],
    },
    {
        "category": _("System Information"),
        "command": "date",
        "general_description": _("Displays or sets the system date and time."),
        "variations": [
            {
                "name": "date",
                "description": _("Shows current date and time."),
            },
            {
                "name": "date +'%Y-%m-%d %H:%M:%S'",
                "description": _("Shows date and time in custom format."),
            },
            {
                "name": "date -u",
                "description": _("Shows UTC time instead of local time."),
            },
        ],
    },
    {
        "category": _("System Information"),
        "command": "cal",
        "general_description": _("Displays a calendar."),
        "variations": [
            {
                "name": "cal",
                "description": _("Shows calendar for current month."),
            },
            {
                "name": "cal 2024",
                "description": _("Shows calendar for entire year 2024."),
            },
            {
                "name": "cal 12 2024",
                "description": _("Shows calendar for December 2024."),
            },
        ],
    },
    {
        "category": _("Process Management"),
        "command": "ps",
        "general_description": _("Displays information about running processes."),
        "variations": [
            {
                "name": "ps aux",
                "description": _(
                    "Shows detailed information about all running processes."
                ),
            },
            {
                "name": "ps -ef",
                "description": _("Shows all processes in a different format."),
            },
        ],
    },
    {
        "category": _("Process Management"),
        "command": "kill",
        "general_description": _("Sends signals to processes."),
        "variations": [
            {
                "name": "kill 1234",
                "description": _("Sends SIGTERM signal to process with PID 1234."),
            },
            {
                "name": "kill -9 1234",
                "description": _("Forcefully kills process with PID 1234 (SIGKILL)."),
            },
        ],
    },
    {
        "category": _("Networking"),
        "command": "ping",
        "general_description": _("Tests network connectivity to a host."),
        "variations": [
            {
                "name": "ping google.com",
                "description": _("Tests connectivity to google.com."),
            },
            {
                "name": "ping -c 4 google.com",
                "description": _("Sends only 4 ping packets instead of continuous."),
            },
        ],
    },
    {
        "category": _("Archives"),
        "command": "tar",
        "general_description": _("Creates and manipulates archive files."),
        "variations": [
            {
                "name": "tar -cvf archive.tar directory/",
                "description": _("Creates a tar archive from a directory."),
            },
            {
                "name": "tar -xvf archive.tar",
                "description": _("Extracts files from a tar archive."),
            },
            {
                "name": "tar -czvf archive.tar.gz directory/",
                "description": _("Creates a compressed tar archive."),
            },
        ],
    },
    {
        "category": _("Bash Conditions"),
        "command": "test",
        "general_description": _("Conditional expressions for bash scripting."),
        "variations": [
            {
                "name": '[[ -z "$name" ]]',
                "description": _("Check if string is empty."),
            },
            {
                "name": '[[ -n "$name" ]]',
                "description": _("Check if string is not empty."),
            },
            {
                "name": '[[ "$user" == "bruno" ]]',
                "description": _("Check if strings are equal."),
            },
            {
                "name": '[[ "$user" != "guest" ]]',
                "description": _("Check if strings are not equal."),
            },
            {
                "name": "[[ 5 -eq 10 ]]",
                "description": _("Check if numbers are equal."),
            },
            {
                "name": "[[ 3 -ne 7 ]]",
                "description": _("Check if numbers are not equal."),
            },
            {
                "name": "[[ 5 -lt 10 ]]",
                "description": _("Check if first number is less than second."),
            },
            {
                "name": "[[ 10 -le 10 ]]",
                "description": _(
                    "Check if first number is less than or equal to second."
                ),
            },
            {
                "name": "[[ 15 -gt 10 ]]",
                "description": _("Check if first number is greater than second."),
            },
            {
                "name": "[[ 10 -ge 10 ]]",
                "description": _(
                    "Check if first number is greater than or equal to second."
                ),
            },
            {
                "name": '[[ "$email" =~ "@.*\\.com$" ]]',
                "description": _("Check if string matches regular expression."),
            },
            {
                "name": "(( count < 100 ))",
                "description": _(
                    "Check numeric conditions with arithmetic evaluation."
                ),
            },
            {
                "name": "[[ -o noclobber ]]",
                "description": _("Check if shell option is enabled."),
            },
            {
                "name": '[[ ! -z "$input" ]]',
                "description": _("Logical NOT - negate a condition."),
            },
            {
                "name": '[[ -n "$user" && -n "$pass" ]]',
                "description": _("Logical AND - both conditions must be true."),
            },
            {
                "name": '[[ "$role" == "admin" || "$role" == "root" ]]',
                "description": _("Logical OR - at least one condition must be true."),
            },
            {
                "name": '[[ -e "/home/bruno/file.txt" ]]',
                "description": _("Check if file exists."),
            },
            {
                "name": '[[ -r "/etc/passwd" ]]',
                "description": _("Check if file is readable."),
            },
            {
                "name": '[[ -h "/usr/bin/python" ]]',
                "description": _("Check if path is a symbolic link."),
            },
            {
                "name": '[[ -d "/home/bruno" ]]',
                "description": _("Check if path is a directory."),
            },
            {
                "name": '[[ -w "/tmp/test.txt" ]]',
                "description": _("Check if file is writable."),
            },
            {
                "name": '[[ -s "/var/log/syslog" ]]',
                "description": _("Check if file size is greater than 0 bytes."),
            },
            {
                "name": '[[ -f "/home/bruno/script.sh" ]]',
                "description": _("Check if path is a regular file."),
            },
            {
                "name": '[[ -x "/usr/bin/bash" ]]',
                "description": _("Check if file is executable."),
            },
            {
                "name": '[[ "/home/bruno/new.txt" -nt "/home/bruno/old.txt" ]]',
                "description": _("Check if first file is newer than second file."),
            },
            {
                "name": '[[ "/home/bruno/old.txt" -ot "/home/bruno/new.txt" ]]',
                "description": _("Check if first file is older than second file."),
            },
            {
                "name": '[[ "/home/bruno/file1.txt" -ef "/home/bruno/file2.txt" ]]',
                "description": _("Check if both paths refer to the same file."),
            },
        ],
    },
    {
        "category": _("Bash Arrays"),
        "command": "array",
        "general_description": _("Array operations and manipulations in bash."),
        "variations": [
            {
                "name": "files=('script.sh' 'config.txt' 'data.log')",
                "description": _("Define array with multiple elements."),
            },
            {
                "name": "files[0]='main.py'",
                "description": _("Set specific array element by index."),
            },
            {
                "name": "files[1]='utils.py'",
                "description": _("Set another array element by index."),
            },
            {
                "name": "files[2]='README.md'",
                "description": _("Set third array element by index."),
            },
            {
                "name": 'echo "${files[0]}"',
                "description": _("Access first element of array."),
            },
            {
                "name": 'echo "${files[-1]}"',
                "description": _("Access last element of array."),
            },
            {
                "name": 'echo "${files[@]}"',
                "description": _("Print all elements separated by spaces."),
            },
            {
                "name": 'echo "${#files[@]}"',
                "description": _("Get total number of elements in array."),
            },
            {
                "name": 'echo "${#files}"',
                "description": _("Get length of first element string."),
            },
            {
                "name": 'echo "${#files[2]}"',
                "description": _("Get length of specific element string."),
            },
            {
                "name": 'echo "${files[@]:2:3}"',
                "description": _(
                    "Extract range of elements (start at index 2, length 3)."
                ),
            },
            {
                "name": 'echo "${!files[@]}"',
                "description": _("Get all array indices/keys."),
            },
            {
                "name": 'files=("${files[@]}" "backup.zip")',
                "description": _("Add new element to end of array."),
            },
            {
                "name": "files+=('archive.tar')",
                "description": _("Append element using compound assignment."),
            },
            {
                "name": 'files=("${files[@]/scr*/}")',
                "description": _("Remove elements matching regex pattern."),
            },
            {
                "name": "unset files[1]",
                "description": _("Remove specific element by index."),
            },
            {
                "name": 'files_copy=("${files[@]}")',
                "description": _("Create duplicate of array."),
            },
            {
                "name": 'all_files=("${files[@]}" "${logs[@]}")',
                "description": _("Concatenate two arrays together."),
            },
            {
                "name": "lines=($(< tasks.txt))",
                "description": _("Read file into array, split by IFS (whitespace)."),
            },
        ],
    },
    {
        "category": _("Bash Brace Expansion"),
        "command": "brace",
        "general_description": _("Generate arbitrary strings using brace expansion."),
        "variations": [
            {
                "name": "echo {main,utils,config}.py",
                "description": _("Expand to: main.py utils.py config.py"),
            },
            {
                "name": "echo {dev,prod,test}",
                "description": _("Basic brace expansion without prefix/suffix."),
            },
            {
                "name": "echo {backup,archive}.tar.gz",
                "description": _("Add file extensions to multiple base names."),
            },
            {
                "name": "echo file_{1,2,3,4,5}.txt",
                "description": _("Create numbered file names."),
            },
            {
                "name": "echo {1..10}",
                "description": _("Generate sequence from 1 to 10."),
            },
            {
                "name": "echo {01..12}",
                "description": _("Generate zero-padded numbers (01, 02, ..., 12)."),
            },
            {
                "name": "echo {a..z}",
                "description": _("Generate alphabet sequence from a to z."),
            },
            {
                "name": "echo {A..F}",
                "description": _("Generate uppercase letter sequence."),
            },
            {
                "name": "echo {10..1}",
                "description": _("Generate descending sequence."),
            },
            {
                "name": "echo {001..010}",
                "description": _("Generate zero-padded sequence with leading zeros."),
            },
            {
                "name": "echo {{1..3},{7..9}}",
                "description": _("Nested braces: generates 1 2 3 7 8 9."),
            },
            {
                "name": "echo {server,client}_{dev,prod}.conf",
                "description": _("Multiple brace expansions in one expression."),
            },
            {
                "name": "echo /usr/{bin,lib,share}/",
                "description": _("Create directory paths with brace expansion."),
            },
            {
                "name": "echo {Jan,Feb,Mar}-{2024,2025}",
                "description": _("Combine different sets of values."),
            },
            {
                "name": "mkdir -p project_{src,docs,tests}",
                "description": _("Create multiple directories at once."),
            },
            {
                "name": "cp file.txt{,.backup}",
                "description": _(
                    "Create backup by copying file with .backup extension."
                ),
            },
            {
                "name": "echo {web,mobile}_{ios,android}",
                "description": _("Generate platform combinations."),
            },
        ],
    },
    {
        "category": _("File Search"),
        "command": "find",
        "general_description": _("Powerful file search and manipulation tool."),
        "variations": [
            {
                "name": "find . -name '*.txt'",
                "description": _(
                    "Find all .txt files in current directory and subdirectories."
                ),
            },
            {
                "name": "find /home/bruno -name 'config*'",
                "description": _(
                    "Find files starting with 'config' in user's home directory."
                ),
            },
            {
                "name": "find . -type f -name '*.log'",
                "description": _("Find only regular files with .log extension."),
            },
            {
                "name": "find . -type d -name 'backup*'",
                "description": _("Find only directories starting with 'backup'."),
            },
            {
                "name": "find . -name '*.tmp' -delete",
                "description": _("Find and delete all .tmp files."),
            },
            {
                "name": "find . -mtime -7",
                "description": _("Find files modified in the last 7 days."),
            },
            {
                "name": "find . -mtime +30",
                "description": _("Find files modified more than 30 days ago."),
            },
            {
                "name": "find . -size +100M",
                "description": _("Find files larger than 100MB."),
            },
            {
                "name": "find . -size -1k",
                "description": _("Find files smaller than 1KB."),
            },
            {
                "name": "find . -empty",
                "description": _("Find empty files and directories."),
            },
            {
                "name": "find . -perm 755",
                "description": _("Find files with specific permissions (755)."),
            },
            {
                "name": "find . -user bruno",
                "description": _("Find files owned by user 'bruno'."),
            },
            {
                "name": "find . -group developers",
                "description": _("Find files owned by group 'developers'."),
            },
            {
                "name": "find . -name '*.py' -exec chmod +x {} \\;",
                "description": _("Make all Python files executable."),
            },
            {
                "name": "find . -name '*.jpg' -exec mv {} /tmp/images/ \\;",
                "description": _("Move all JPG files to /tmp/images/ directory."),
            },
            {
                "name": "find . -name '*.log' -exec grep -l 'ERROR' {} \\;",
                "description": _("Find log files containing 'ERROR' text."),
            },
            {
                "name": "find . -maxdepth 2 -name '*.conf'",
                "description": _("Find .conf files only 2 levels deep."),
            },
            {
                "name": "find . -name '*.bak' -o -name '*.backup'",
                "description": _("Find files with .bak OR .backup extension."),
            },
            {
                "name": "find . -type f -name '*.txt' -a -size +1M",
                "description": _("Find .txt files that are also larger than 1MB."),
            },
            {
                "name": "find . -name '*.old' -print0 | xargs -0 rm",
                "description": _("Safely delete files with spaces in names."),
            },
            {
                "name": "find . -name '*.zip' -exec unzip -l {} \\;",
                "description": _("List contents of all ZIP files found."),
            },
        ],
    },
    {
        "category": _("Text Processing"),
        "command": "sed",
        "general_description": _("Stream editor for filtering and transforming text."),
        "variations": [
            {
                "name": "sed 's/old/new/g' file.txt",
                "description": _(
                    "Replace all occurrences of 'old' with 'new' in file.txt."
                ),
            },
            {
                "name": "sed 's/bruno/maria/g' names.txt",
                "description": _("Replace all instances of 'bruno' with 'maria'."),
            },
            {
                "name": "sed 's/error/warning/gi' log.txt",
                "description": _(
                    "Case-insensitive replacement of 'error' with 'warning'."
                ),
            },
            {
                "name": "sed 's/old/new/' file.txt",
                "description": _("Replace only the first occurrence per line."),
            },
            {
                "name": "sed 's/\\btest\\b/exam/g' document.txt",
                "description": _(
                    "Replace whole word 'test' with 'exam' using word boundaries."
                ),
            },
            {
                "name": "sed '2,5s/old/new/g' file.txt",
                "description": _("Replace only on lines 2 through 5."),
            },
            {
                "name": "sed '/^#/d' config.txt",
                "description": _("Delete all comment lines (starting with #)."),
            },
            {
                "name": "sed '/^$/d' file.txt",
                "description": _("Delete all empty lines."),
            },
            {
                "name": "sed '1,10!d' file.txt",
                "description": _("Print only lines 1 through 10."),
            },
            {
                "name": "sed -n '5p' file.txt",
                "description": _("Print only line number 5."),
            },
            {
                "name": "sed '1i\\# This is a header' file.txt",
                "description": _("Insert text before line 1."),
            },
            {
                "name": "sed '$a\\# End of file' file.txt",
                "description": _("Append text after the last line."),
            },
            {
                "name": "sed 's/.*/\\U&/' file.txt",
                "description": _("Convert all text to uppercase."),
            },
            {
                "name": "sed 's/.*/\\L&/' file.txt",
                "description": _("Convert all text to lowercase."),
            },
            {
                "name": "sed 's/\\(.*\\)/\\U\\1/' file.txt",
                "description": _("Alternative way to convert to uppercase."),
            },
            {
                "name": "sed 's/\\([a-z]\\)/\\U\\1/g' file.txt",
                "description": _("Capitalize first letter of each word."),
            },
            {
                "name": "sed 's/\\(.*\\)/[\\1]/' file.txt",
                "description": _("Wrap each line in square brackets."),
            },
            {
                "name": "sed 's/^/    /' file.txt",
                "description": _("Add 4 spaces indentation to each line."),
            },
            {
                "name": "sed 's/$/ - processed/' file.txt",
                "description": _("Append ' - processed' to end of each line."),
            },
            {
                "name": "sed -i 's/old/new/g' file.txt",
                "description": _("Edit file in-place (modify original file)."),
            },
            {
                "name": "sed -i.bak 's/old/new/g' file.txt",
                "description": _("Edit in-place and create backup file."),
            },
            {
                "name": "sed 's/\\(.*\\)/\\1 - line: \\1/' file.txt",
                "description": _("Duplicate content with backreference."),
            },
            {
                "name": "sed '/pattern/{s/old/new/g; s/foo/bar/g}' file.txt",
                "description": _("Multiple commands on lines matching pattern."),
            },
            {
                "name": "sed -e 's/old/new/g' -e '/^$/d' file.txt",
                "description": _("Execute multiple sed expressions."),
            },
            {
                "name": "sed '10,20s/old/new/g; 30,40s/foo/bar/g' file.txt",
                "description": _("Multiple address ranges with different operations."),
            },
        ],
    },
    {
        "category": _("Awk Commands"),
        "command": "awk",
        "general_description": _("Powerful text processing and pattern scanning tool."),
        "variations": [
            {
                "name": "awk '{print $1}' file.txt",
                "description": _("Print the first field (column) of each line."),
            },
            {
                "name": "awk '{print $1, $3}' data.txt",
                "description": _("Print the 1st and 3rd fields of each line."),
            },
            {
                "name": "awk '{print $NF}' file.txt",
                "description": _("Print the last field of each line."),
            },
            {
                "name": "awk '{print NR, $0}' file.txt",
                "description": _("Print line number followed by the entire line."),
            },
            {
                "name": "awk 'NR==5 {print}' file.txt",
                "description": _("Print only line number 5."),
            },
            {
                "name": "awk 'NR>=10 && NR<=20 {print}' file.txt",
                "description": _("Print lines 10 through 20."),
            },
            {
                "name": "awk '/bruno/ {print}' users.txt",
                "description": _("Print lines containing 'bruno'."),
            },
            {
                "name": "awk '$3 > 100 {print $1, $3}' sales.txt",
                "description": _("Print name and sales where sales > 100."),
            },
            {
                "name": "awk '{sum += $2} END {print \"Total:\", sum}' numbers.txt",
                "description": _("Calculate sum of values in column 2."),
            },
            {
                "name": "awk '{if ($2 > max) max = $2} END {print \"Max:\", max}' data.txt",
                "description": _("Find the maximum value in column 2."),
            },
            {
                "name": "awk 'BEGIN {FS=\":\"} {print $1, $3}' /etc/passwd",
                "description": _("Use colon as field separator for /etc/passwd."),
            },
            {
                "name": "awk 'BEGIN {OFS=\" - \"} {print $1, $2}' file.txt",
                "description": _("Set output field separator to ' - '."),
            },
            {
                "name": "awk 'length($0) > 80 {print NR, $0}' file.txt",
                "description": _(
                    "Print lines longer than 80 characters with line numbers."
                ),
            },
            {
                "name": "awk '{print toupper($0)}' file.txt",
                "description": _("Convert all text to uppercase."),
            },
            {
                "name": "awk '{print tolower($0)}' file.txt",
                "description": _("Convert all text to lowercase."),
            },
            {
                "name": 'awk \'BEGIN {print "Name\\tAge\\tCity"} {print $1"\\t"$2"\\t"$3}\' data.txt',
                "description": _("Add header and format output as table."),
            },
            {
                "name": 'awk \'NF > 3 {print "Line", NR, "has", NF, "fields"}\' file.txt',
                "description": _(
                    "Print lines with more than 3 fields and their field count."
                ),
            },
            {
                "name": "awk '{gsub(/old/, \"new\"); print}' file.txt",
                "description": _("Replace all occurrences of 'old' with 'new'."),
            },
            {
                "name": "awk '{split($1, arr, \"-\"); print arr[1], arr[2]}' file.txt",
                "description": _("Split first field on '-' and print both parts."),
            },
            {
                "name": "awk 'BEGIN {count=0} /ERROR/ {count++} END {print \"Errors:\", count}' log.txt",
                "description": _("Count lines containing 'ERROR'."),
            },
            {
                "name": "awk '{arr[$1]++} END {for (name in arr) print name, arr[name]}' names.txt",
                "description": _("Count occurrences of each unique value in column 1."),
            },
            {
                "name": 'awk \'BEGIN {print "Start processing"} {print "Processing:", $0} END {print "Done"}\' file.txt',
                "description": _(
                    "Use BEGIN and END blocks for initialization and cleanup."
                ),
            },
            {
                "name": "awk '{printf \"%-10s %5.2f\\n\", $1, $2}' data.txt",
                "description": _("Format output with specific widths and precision."),
            },
            {
                "name": "awk '$2 ~ /^2024/ {print}' dates.txt",
                "description": _("Print lines where column 2 starts with '2024'."),
            },
            {
                "name": "awk '{sub(/^[ \\t]+/, \"\"); print}' file.txt",
                "description": _("Remove leading whitespace from each line."),
            },
        ],
    },
]
