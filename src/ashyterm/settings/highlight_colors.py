"""Highlight colors — theme palette resolution + ANSI conversion."""

from typing import Any, Dict, List, Optional, Tuple

from .config import ColorSchemeMap, ColorSchemes
from .highlight_models import ANSI_COLOR_MAP, ANSI_MODIFIERS
from ..utils.logger import get_logger


LOGGER = get_logger("ashyterm.settings.highlight_colors")


class HighlightColorResolver:
    """Resolve logical color names to hex/ANSI using active theme palette."""

    def __init__(self):
        self._settings_manager = None
        self._color_cache: Dict[str, Dict[str, str]] = {}
        self._current_theme_name: str = ""

    def set_settings_manager(self, manager) -> None:
        """Attach settings manager for theme-aware colors. Invalidate cache."""
        self._settings_manager = manager
        self._color_cache.clear()

    def get_current_theme_palette(self) -> Dict[str, str]:
        """Fetch current theme palette from settings manager."""
        if not self._settings_manager:
            self._current_theme_name = "default"
            return self._get_default_palette()
        try:
            scheme_index = self._settings_manager.get("color_scheme", 1)
            scheme_order = ColorSchemeMap.SCHEME_ORDER
            if 0 <= scheme_index < len(scheme_order):
                scheme_name = scheme_order[scheme_index]
            else:
                scheme_name = "dracula"
            self._current_theme_name = scheme_name
            schemes = ColorSchemes.get_schemes()
            if scheme_name in schemes:
                scheme = schemes[scheme_name]
                return {
                    "foreground": scheme["foreground"],
                    "background": scheme["background"],
                    "cursor": scheme.get("cursor", scheme["foreground"]),
                    "palette": scheme["palette"],
                }
        except Exception as exc:
            LOGGER.warning(f"Failed to resolve theme palette, using default: {exc}")
        self._current_theme_name = "default"
        return self._get_default_palette()

    def _get_default_palette(self) -> Dict[str, Any]:
        """Fallback Dracula-inspired palette."""
        return {
            "foreground": "#f8f8f2", "background": "#282a36",
            "cursor": "#f8f8f2",
            "palette": [
                "#000000", "#ff5555", "#50fa7b", "#f1fa8c",
                "#bd93f9", "#ff79c6", "#8be9fd", "#bfbfbf",
                "#4d4d4d", "#ff6e67", "#5af78e", "#f4f99d",
                "#caa9fa", "#ff92d0", "#9aedfe", "#e6e6e6",
            ],
        }

    def resolve_color(self, color_name: str) -> str:
        """Logical color name → hex. Cached per theme."""
        if not color_name:
            return "#ffffff"
        palette = self.get_current_theme_palette()
        cache_key = self._current_theme_name or "default"
        if cache_key not in self._color_cache:
            self._color_cache[cache_key] = {}
        if color_name in self._color_cache[cache_key]:
            return self._color_cache[cache_key][color_name]
        parts = color_name.lower().split()
        base_color = parts[-1] if parts else "white"
        hex_color = self._resolve_base_color(base_color, palette)
        self._color_cache[cache_key][color_name] = hex_color
        return hex_color

    def _resolve_base_color(self, color_name: str, palette: Dict[str, str]) -> str:
        """Base color name → hex via theme palette or ANSI map."""
        if color_name == "foreground":
            return palette.get("foreground", "#ffffff")
        if color_name == "background":
            return palette.get("background", "#000000")
        if color_name == "cursor":
            return palette.get("cursor", "#ffffff")
        if color_name in ANSI_COLOR_MAP:
            idx = ANSI_COLOR_MAP[color_name]
            theme_palette: List[str] = palette.get("palette", [])  # type: ignore[assignment]
            if idx < len(theme_palette):
                return theme_palette[idx]
        if color_name.startswith("#"):
            return color_name
        return "#ffffff"  # fallback

    def resolve_color_to_ansi(self, color_name: str) -> str:
        """Logical color name → ANSI escape sequence."""
        if not color_name:
            return ""
        modifiers, base_color, bg_color = self._parse_color_spec(color_name)
        fg_code = self._get_foreground_ansi_code(base_color)
        bg_code = self._get_background_ansi_code(bg_color)
        ansi_parts = modifiers.copy()
        if fg_code:
            ansi_parts.append(fg_code)
        if bg_code:
            ansi_parts.append(bg_code)
        if ansi_parts:
            return f"\033[{';'.join(ansi_parts)}m"
        return ""

    def _parse_color_spec(self, color_name: str) -> Tuple[List[str], str, Optional[str]]:
        """Parse 'bold red on_blue' → (['1'], 'red', 'blue')."""
        parts = color_name.lower().split()
        modifiers: List[str] = []
        base_color = "white"
        bg_color: Optional[str] = None
        for part in parts:
            if part in ANSI_MODIFIERS:
                modifiers.append(ANSI_MODIFIERS[part])
            elif part.startswith("on_"):
                bg_color = part[3:]
            else:
                base_color = part
        return modifiers, base_color, bg_color

    def _get_foreground_ansi_code(self, base_color: str) -> Optional[str]:
        """Color name → foreground ANSI code (30-37 or 90-97)."""
        if base_color in ANSI_COLOR_MAP:
            ci = ANSI_COLOR_MAP[base_color]
            return str(30 + ci) if ci < 8 else str(90 + ci - 8)
        skip = ("foreground", "background", "cursor", "none", "default")
        if base_color not in skip:
            return "37"  # unknown → white
        return None

    def _get_background_ansi_code(self, bg_color: Optional[str]) -> Optional[str]:
        """Color name → background ANSI code (40-47 or 100-107)."""
        if bg_color and bg_color in ANSI_COLOR_MAP:
            ci = ANSI_COLOR_MAP[bg_color]
            return str(40 + ci) if ci < 8 else str(100 + ci - 8)
        return None

    @property
    def current_theme_name(self) -> str:
        """Current theme name for cache key."""
        return self._current_theme_name
