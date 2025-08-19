#!/usr/bin/env python3
"""
GTK Cursor Tester Script
Move the mouse over each button to see the cursor.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib

class CursorTester(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="GTK Cursor Tester")
        self.set_default_size(600, 800)
        
        # List of cursor names to test
        self.cursor_names = [
            "default", "pointer", "hand1", "hand2", "grab", "grabbing",
            "move", "dnd-move", "fleur", "crosshair", "text", "wait",
            "help", "progress", "not-allowed", "alias", "copy", "cell",
            "context-menu", "no-drop", "vertical-text", "all-scroll",
            "nesw-resize", "nwse-resize", "ns-resize", "ew-resize",
            "n-resize", "s-resize", "e-resize", "w-resize",
            "ne-resize", "nw-resize", "se-resize", "sw-resize",
            "zoom-in", "zoom-out"
        ]
        
        self.setup_ui()
    
    def setup_ui(self):
        # Main scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(20)
        main_box.set_margin_bottom(20)
        main_box.set_margin_start(20)
        main_box.set_margin_end(20)
        
        # Title
        title = Gtk.Label()
        title.set_markup("<big><b>GTK Cursor Tester</b></big>")
        title.set_margin_bottom(10)
        main_box.append(title)
        
        # Instructions
        instructions = Gtk.Label()
        instructions.set_markup("<i>Move your mouse over each button to test the cursor.\nLook for hand-like cursors and note their names.</i>")
        instructions.set_margin_bottom(20)
        main_box.append(instructions)
        
        # Create buttons for each cursor
        for cursor_name in self.cursor_names:
            self.create_cursor_button(main_box, cursor_name)
        
        scrolled.set_child(main_box)
        self.set_child(scrolled)
    
    def create_cursor_button(self, parent, cursor_name):
        # Create button
        button = Gtk.Button()
        button.set_size_request(400, 50)
        
        # Button content
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_halign(Gtk.Align.CENTER)
        
        label = Gtk.Label(label=f"Cursor: {cursor_name}")
        label.set_hexpand(True)
        box.append(label)
        
        # Status label
        status_label = Gtk.Label(label="")
        status_label.add_css_class("dim-label")
        box.append(status_label)
        
        button.set_child(box)
        
        # Try to create the cursor
        try:
            cursor = Gdk.Cursor.new_from_name(cursor_name)
            status_label.set_text("✓ Available")
            status_label.remove_css_class("error")
            
            # Set up hover events
            motion_controller = Gtk.EventControllerMotion()
            motion_controller.connect("enter", self.on_hover_enter, cursor, cursor_name)
            motion_controller.connect("leave", self.on_hover_leave)
            button.add_controller(motion_controller)
            
        except Exception as e:
            status_label.set_text("✗ Not available")
            status_label.add_css_class("error")
            cursor = None
        
        # Click to copy name
        button.connect("clicked", self.on_button_clicked, cursor_name)
        
        parent.append(button)
    
    def on_hover_enter(self, controller, x, y, cursor, cursor_name):
        """Set cursor when mouse enters button."""
        try:
            button = controller.get_widget()
            button.set_cursor(cursor)
            print(f"Hovering over: {cursor_name}")
        except Exception as e:
            print(f"Error setting cursor {cursor_name}: {e}")
    
    def on_hover_leave(self, controller):
        """Reset cursor when mouse leaves button."""
        try:
            button = controller.get_widget()
            button.set_cursor(None)
        except Exception as e:
            print(f"Error resetting cursor: {e}")
    
    def on_button_clicked(self, button, cursor_name):
        """Copy cursor name to clipboard when clicked."""
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(cursor_name)
            print(f"Copied to clipboard: {cursor_name}")
            
            # Show toast-like notification
            self.show_notification(f"Copied: {cursor_name}")
            
        except Exception as e:
            print(f"Error copying to clipboard: {e}")
    
    def show_notification(self, message):
        """Show a temporary notification."""
        # Create overlay label
        overlay_label = Gtk.Label(label=message)
        overlay_label.add_css_class("osd")
        overlay_label.set_halign(Gtk.Align.CENTER)
        overlay_label.set_valign(Gtk.Align.END)
        overlay_label.set_margin_bottom(50)
        
        # Add overlay
        overlay = Gtk.Overlay()
        overlay.set_child(self.get_child())
        overlay.add_overlay(overlay_label)
        self.set_child(overlay)
        
        # Remove after 2 seconds
        GLib.timeout_add(2000, self.remove_notification, overlay)
    
    def remove_notification(self, overlay):
        """Remove notification overlay."""
        try:
            original_child = overlay.get_child()
            self.set_child(original_child)
        except Exception as e:
            print(f"Error removing notification: {e}")
        return False  # Don't repeat timeout

class CursorTesterApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.example.cursortester")
    
    def do_activate(self):
        window = CursorTester(self)
        window.present()

if __name__ == "__main__":
    # Add some basic CSS for styling
    css_provider = Gtk.CssProvider()
    css_provider.load_from_data(b"""
        .error { color: red; }
        .dim-label { opacity: 0.7; }
        .osd {
            background: rgba(0, 0, 0, 0.8);
            color: white;
            border-radius: 6px;
            padding: 12px 20px;
            font-weight: bold;
        }
    """)
    
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    
    app = CursorTesterApp()
    app.run()