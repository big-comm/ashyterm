"""Tests for tooltip_helper — CSS generation, color logic, singleton."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestTooltipHelperSingleton:
    """Tests for TooltipHelper singleton pattern."""

    def test_get_tooltip_helper_returns_instance(self):
        from ashyterm.utils.tooltip_helper import TooltipHelper, get_tooltip_helper

        result = get_tooltip_helper()
        assert isinstance(result, TooltipHelper)

    def test_get_tooltip_helper_returns_same_instance(self):
        from ashyterm.utils.tooltip_helper import get_tooltip_helper

        t1 = get_tooltip_helper()
        t2 = get_tooltip_helper()
        assert t1 is t2


class TestAdjustTooltipBackground:
    """CSS now uses GTK named colors — no manual color adjustment."""
    pass


class TestIsDarkColor:
    """CSS now uses GTK named colors — no manual dark detection."""
    pass


class TestTooltipHelperInit:
    """Tests for TooltipHelper initialization state."""

    def test_initial_state(self):
        from ashyterm.utils.tooltip_helper import TooltipHelper

        helper = TooltipHelper()
        assert helper.active_popover is None
        assert helper.active_widget is None
        assert helper.show_timer_id is None
        assert helper.hide_timer_id is None
        assert helper._colors_initialized is False


class TestTooltipCSSStructure:
    """Tests for CSS template structure by inspecting _apply_css source."""

    def test_apply_css_contains_popover_selector(self):
        """Verify CSS template in _apply_css contains the popover selector."""
        import inspect
        from ashyterm.utils.tooltip_helper import TooltipHelper

        source = inspect.getsource(TooltipHelper._apply_css)
        assert "popover.custom-tooltip-static" in source

    def test_apply_css_contains_transition(self):
        import inspect
        from ashyterm.utils.tooltip_helper import TooltipHelper

        source = inspect.getsource(TooltipHelper._apply_css)
        assert "transition:" in source
        assert "opacity" in source

    def test_apply_css_contains_border(self):
        import inspect
        from ashyterm.utils.tooltip_helper import TooltipHelper

        source = inspect.getsource(TooltipHelper._apply_css)
        assert "border" in source
