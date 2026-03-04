# ashyterm/utils/theme_engine.py
"""
Theme Engine for generating dynamic application CSS based on color schemes.
"""

from typing import Dict, Any
import gi

gi.require_version("Adw", "1")
from gi.repository import Adw


class ThemeEngine:
    """Generates CSS for the application based on color scheme parameters."""

    @staticmethod
    def get_theme_params(
        scheme: Dict[str, Any], transparency: int = 0, terminal_transparency: int = 0
    ) -> Dict[str, Any]:
        """Extract and compute theme parameters from color scheme."""
        bg_color = scheme.get("background", "#000000")
        fg_color = scheme.get("foreground", "#ffffff")
        header_bg_color = scheme.get("headerbar_background", bg_color)

        # Calculate luminance for theme detection
        r = int(bg_color[1:3], 16) / 255
        g = int(bg_color[3:5], 16) / 255
        b = int(bg_color[5:7], 16) / 255
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        is_dark_theme = luminance < 0.5

        return {
            "bg_color": bg_color,
            "fg_color": fg_color,
            "header_bg_color": header_bg_color,
            "user_transparency": transparency,
            "terminal_transparency": terminal_transparency,
            "luminance": luminance,
            "is_dark_theme": is_dark_theme,
        }

    @classmethod
    def generate_app_css(cls, params: Dict[str, Any], gtk_theme_name: str) -> str:
        """Generates the full application CSS string."""
        css_parts = [
            cls._get_root_vars_css(params, gtk_theme_name),
            cls._get_headerbar_css(params, gtk_theme_name),
            cls._get_tabs_css(params, gtk_theme_name),
        ]
        return "".join(css_parts)

    @staticmethod
    def _get_root_vars_css(params: Dict[str, Any], gtk_theme_name: str) -> str:
        """Generate CSS root variables for Adwaita/GTK4 theming.

        When the background color is too dark (luminance < 0.05), skip applying
        custom colors to prevent readability issues with nearly-black backgrounds.
        """
        if gtk_theme_name != "terminal":
            return ""

        # Skip custom theming for very dark backgrounds (nearly black)
        # This prevents readability issues when background luminance is too low
        if params["luminance"] < 0.05:
            return ""

        fg = params["fg_color"]
        bg = params["bg_color"]
        header_bg = params["header_bg_color"]
        term_transparency = params.get("terminal_transparency", 0)

        # Use adaptive alpha calculation logic matching the terminal engine
        luminance = params["luminance"]
        boost_factor = 0.3
        adjustment_factor = 1.0 + (boost_factor * (1.0 - luminance))
        adjusted_transparency = min(100.0, term_transparency * adjustment_factor)
        final_alpha = max(0.0, min(1.0, 1.0 - (adjusted_transparency / 100.0) ** 1.6))
        
        if final_alpha < 1.0:
            bg_css = f"rgba({int(bg[1:3], 16)}, {int(bg[3:5], 16)}, {int(bg[5:7], 16)}, {final_alpha})"
            header_bg_css = f"rgba({int(header_bg[1:3], 16)}, {int(header_bg[3:5], 16)}, {int(header_bg[5:7], 16)}, {final_alpha})"
        else:
            bg_css = bg
            header_bg_css = header_bg

        return f"""
        :root {{
            /* Window and View Colors */
            --window-bg-color: {bg_css};
            --window-fg-color: {fg};
            --view-bg-color: {bg_css};
            --view-fg-color: {fg};
            
            /* Headerbar Colors */
            --headerbar-bg-color: {header_bg_css};
            --headerbar-fg-color: {fg};
            --headerbar-backdrop-color: {header_bg_css};
            --headerbar-shade-color: color-mix(in srgb, {header_bg}, black 7%);
            
            /* Popover and Dialog Colors */
            --popover-bg-color: {bg_css};
            --popover-fg-color: {fg};
            --dialog-bg-color: {bg_css};
            --dialog-fg-color: {fg};
            
            /* Card and Thumbnail Colors (Common in lists) */
            --card-bg-color: color-mix(in srgb, {bg}, white 5%);
            --card-fg-color: {fg};
            
            /* Sidebar (if using split view naming) */
            --sidebar-bg-color: {header_bg_css};
            --sidebar-fg-color: {fg};
        }}

        popover.ashyterm-popover {{
            background-color: transparent; 
            color: var(--popover-fg-color);
        }}

        popover.sidebar-popover {{
            background-color: transparent; 
            color: var(--sidebar-fg-color);
        }}

        popover.ashyterm-popover > contents,
        popover.ashyterm-popover > arrow {{
            background-color: var(--popover-bg-color);
            color: inherit;
        }}
        
        popover.ashyterm-popover listview,
        popover.sidebar-popover listview,
        popover.ashyterm-popover scrolledwindow,
        popover.sidebar-popover scrolledwindow {{
            background-color: transparent;
        }}
        """

    @staticmethod
    def _get_headerbar_css(params: Dict[str, Any], gtk_theme_name: str) -> str:
        """Generate CSS purely for headerbar transparency, if enabled."""
        user_transparency = params.get("user_transparency", 0)
        
        if user_transparency == 0:
            return ""
            
        header_opacity_float = (100 - user_transparency) / 100.0
        
        # Calculate raw absolute RGBA bypasses GTK CSS function limitations with `var()`
        if gtk_theme_name == "terminal":
            header_bg = params["header_bg_color"]
            term_transparency = params.get("terminal_transparency", 0)
            luminance = params["luminance"]
            
            # Recreate base terminal alpha factor
            boost_factor = 0.3
            adjustment_factor = 1.0 + (boost_factor * (1.0 - luminance))
            adjusted_term_transparency = min(100.0, term_transparency * adjustment_factor)
            term_alpha = max(0.0, min(1.0, 1.0 - (adjusted_term_transparency / 100.0) ** 1.6))
            
            # Multiply header transparency over terminal transparency
            final_alpha = term_alpha * header_opacity_float
            bg_css_value = f"rgba({int(header_bg[1:3], 16)}, {int(header_bg[3:5], 16)}, {int(header_bg[5:7], 16)}, {final_alpha})"
        else:
            style_manager = Adw.StyleManager.get_default()
            is_dark = style_manager.get_dark()
            fallback_bg = "#303030" if is_dark else "#f0f0f0"
            bg_css_value = f"rgba({int(fallback_bg[1:3], 16)}, {int(fallback_bg[3:5], 16)}, {int(fallback_bg[5:7], 16)}, {header_opacity_float})"

        selectors = """
        window headerbar.main-header-bar,
        headerbar.main-header-bar,
        .main-header-bar,
        .terminal-pane .header-bar,
        .top-bar,
        searchbar,
        searchbar > box,
        .command-toolbar
        """

        return f"""
        {selectors} {{
            background-color: {bg_css_value};
            background-image: none;
        }}
        {selectors.replace(",", ":backdrop,")}:backdrop {{
            background-color: {bg_css_value};
            background-image: none;
        }}
        """

    @staticmethod
    def _get_tabs_css(params: Dict[str, Any], gtk_theme_name: str) -> str:
        """Generate CSS for tab bar internal structure."""
        if gtk_theme_name == "terminal":
            fg = params["fg_color"]
            return f"""
            .scrolled-tab-bar viewport box .horizontal.active {{ 
                background-color: color-mix(in srgb, {fg}, transparent 78%); 
            }}
            """
        return ""
