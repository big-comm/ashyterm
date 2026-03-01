# ashyterm/utils/accessibility.py
"""
Accessibility helpers for GTK4 widgets.

Provides simple wrappers around the GTK4 Accessible API so that
Orca and other screen readers can identify interactive widgets.
"""

from gi.repository import Gtk


def set_label(widget: Gtk.Widget, label: str) -> None:
    """Set the accessible label (name) on a widget."""
    widget.update_property(
        [Gtk.AccessibleProperty.LABEL],
        [label],
    )


def set_description(widget: Gtk.Widget, description: str) -> None:
    """Set the accessible description on a widget."""
    widget.update_property(
        [Gtk.AccessibleProperty.DESCRIPTION],
        [description],
    )


def set_role_description(widget: Gtk.Widget, description: str) -> None:
    """Set the accessible role description on a widget."""
    widget.update_property(
        [Gtk.AccessibleProperty.ROLE_DESCRIPTION],
        [description],
    )


def set_labelled_by(widget: Gtk.Widget, label_widget: Gtk.Widget) -> None:
    """Set the LABELLED_BY relation between a widget and its label."""
    widget.update_relation(
        [Gtk.AccessibleRelation.LABELLED_BY],
        [label_widget],
    )
