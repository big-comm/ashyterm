import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

try:
    gi.require_version("Vte", "3.91")
    VTE_AVAILABLE = True
except (ValueError, ImportError):
    VTE_AVAILABLE = False

APP_ID = "org.ashy.term"
APP_TITLE = "Ashy Term"
CONFIG_DIR = os.path.expanduser("~/.config/ashyterm")
SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

SSH_CONNECT_TIMEOUT = 5 # Segundos

os.makedirs(CONFIG_DIR, exist_ok=True) # Cria o diretório de configuração se não existir

DEFAULT_SETTINGS = {
    "color_scheme": 0, # Índice no COLOR_SCHEME_MAP
    "transparency": 0, # Percentual (0-100)
    "font": "Monospace 10",
    # As chaves aqui devem corresponder aos nomes das GAction usadas em set_accels_for_action (sem o prefixo "win.")
    # e também às chaves usadas em CommTerminalWindow._update_keyboard_shortcuts e PreferencesDialog.
    "shortcuts": {
        "new-local-tab": "<Control>t",    # MODIFICADO: chave alinhada com o nome da ação GAction
        "close-tab": "<Control>w",        # MODIFICADO: chave alinhada (era "close_tab", mas ação é "close-tab")
        "copy": "<Control><Shift>c",
        "paste": "<Control><Shift>v",
    },
    "sidebar_visible": True # Visibilidade inicial da barra lateral
}

COLOR_SCHEMES = {
    "system_default": { # Adapta-se ao tema do sistema (GTK pode usar cores diferentes)
        "foreground": "#ffffff", # Cor padrão se o tema não especificar
        "background": "#000000", # Cor padrão se o tema não especificar
        "palette": [ # Paleta de 16 cores ANSI
            "#000000", "#cc0000", "#4e9a06", "#c4a000",
            "#3465a4", "#75507b", "#06989a", "#d3d7cf",
            "#555753", "#ef2929", "#8ae234", "#fce94f",
            "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"
        ]
    },
    "light": {
        "foreground": "#000000",
        "background": "#ffffff",
        "palette": [
            "#000000", "#cc0000", "#4e9a06", "#c4a000",
            "#3465a4", "#75507b", "#06989a", "#555753", # Cor 7 (branco) mais escura
            "#888a85", "#ef2929", "#8ae234", "#fce94f", # Cor 8 (preto brilhante) mais clara
            "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"
        ]
    },
    "dark": {
        "foreground": "#ffffff",
        "background": "#1c1c1c", # Um cinza escuro, não preto puro
        "palette": [
            "#000000", "#cc0000", "#4e9a06", "#c4a000",
            "#3465a4", "#75507b", "#06989a", "#d3d7cf",
            "#555753", "#ef2929", "#8ae234", "#fce94f",
            "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"
        ]
    },
    "solarized_light": {
        "foreground": "#657b83", # base00
        "background": "#fdf6e3", # base3
        "palette": [
            "#073642", "#dc322f", "#859900", "#b58900", # base02, red, green, yellow
            "#268bd2", "#d33682", "#2aa198", "#eee8d5", # blue, magenta, cyan, base2
            "#002b36", "#cb4b16", "#586e75", "#657b83", # base03, orange, base01, base00
            "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"  # base0, violet, base1, base3
        ]
    },
    "solarized_dark": {
        "foreground": "#839496", # base0
        "background": "#002b36", # base03
        "palette": [
            "#073642", "#dc322f", "#859900", "#b58900", # base02, red, green, yellow
            "#268bd2", "#d33682", "#2aa198", "#eee8d5", # blue, magenta, cyan, base2
            "#002b36", "#cb4b16", "#586e75", "#657b83", # base03 (usado como preto brilhante), orange, base01, base00
            "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"  # base0 (usado como branco), violet, base1, base3
        ]
    }
}

# Mapeia o índice do combobox de preferências para o nome da chave em COLOR_SCHEMES
COLOR_SCHEME_MAP = [
    "system_default",
    "light",
    "dark",
    "solarized_light",
    "solarized_dark"
]