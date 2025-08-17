#!/usr/bin/env python3
"""
VTE Hyperlink Test Script

This script tests VTE's hyperlink capabilities to identify what works
and what doesn't in your specific VTE version.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")

from gi.repository import Gtk, Adw, Vte, GLib, GObject
import sys
import subprocess
import webbrowser

class VTEHyperlinkTest(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("VTE Hyperlink Test")
        self.set_default_size(800, 600)
        
        # Create terminal
        self.terminal = Vte.Terminal()
        
        # Test hyperlink support
        self.test_hyperlink_support()
        
        # Setup UI
        self.setup_ui()
        
        # Spawn shell
        self.spawn_shell()
    
    def test_hyperlink_support(self):
        """Test what hyperlink features are available."""
        print("=== VTE HYPERLINK SUPPORT TEST ===")
        
        # Test 1: Check if set_allow_hyperlink exists
        if hasattr(self.terminal, 'set_allow_hyperlink'):
            print("✓ set_allow_hyperlink method available")
            try:
                self.terminal.set_allow_hyperlink(True)
                print("✓ set_allow_hyperlink(True) succeeded")
            except Exception as e:
                print(f"✗ set_allow_hyperlink(True) failed: {e}")
        else:
            print("✗ set_allow_hyperlink method NOT available")
        
        # Test 2: Check available signals
        print("\n=== AVAILABLE SIGNALS ===")
        try:
            signals = GObject.signal_list_names(self.terminal)
            hyperlink_signals = [s for s in signals if 'hyperlink' in s.lower()]
            
            if hyperlink_signals:
                print(f"✓ Hyperlink signals found: {hyperlink_signals}")
            else:
                print("✗ No hyperlink signals found")
            
            # Check for specific signals
            expected_signals = [
                "hyperlink-hover-uri-changed",
                "hyperlink_hover_uri_changed"
            ]
            
            for signal in expected_signals:
                if signal in signals:
                    print(f"✓ Signal '{signal}' available")
                else:
                    print(f"✗ Signal '{signal}' NOT available")
                    
        except Exception as e:
            print(f"✗ Failed to list signals: {e}")
        
        # Test 3: Check VTE version
        print(f"\n=== VTE VERSION INFO ===")
        try:
            if hasattr(Vte, '_version'):
                print(f"VTE version: {Vte._version}")
            else:
                print("VTE version: unknown")
                
            if hasattr(Vte, 'get_major_version'):
                major = Vte.get_major_version()
                minor = Vte.get_minor_version() 
                micro = Vte.get_micro_version()
                print(f"VTE version: {major}.{minor}.{micro}")
        except Exception as e:
            print(f"Failed to get VTE version: {e}")
    
    def setup_ui(self):
        """Setup the user interface."""
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)
        main_box.set_margin_start(10)
        main_box.set_margin_end(10)
        
        # Info label
        info_label = Gtk.Label()
        info_label.set_markup(
            "<b>VTE Hyperlink Test</b>\n\n"
            "This terminal should detect and make URLs clickable.\n"
            "Try typing: https://google.com\n"
            "Then hover and click on the URL."
        )
        info_label.set_halign(Gtk.Align.START)
        main_box.append(info_label)
        
        # Terminal in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(self.terminal)
        main_box.append(scrolled)
        
        # Test buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        # Insert URL button
        insert_url_btn = Gtk.Button(label="Insert Test URL")
        insert_url_btn.connect("clicked", self.on_insert_url)
        button_box.append(insert_url_btn)
        
        # Insert OSC8 URL button
        insert_osc8_btn = Gtk.Button(label="Insert OSC8 Hyperlink")
        insert_osc8_btn.connect("clicked", self.on_insert_osc8)
        button_box.append(insert_osc8_btn)
        
        # Test signals button
        test_signals_btn = Gtk.Button(label="Test Signals")
        test_signals_btn.connect("clicked", self.on_test_signals)
        button_box.append(test_signals_btn)
        
        main_box.append(button_box)
        
        self.set_content(main_box)
        
        # Setup terminal events
        self.setup_terminal_events()
    
    def setup_terminal_events(self):
        """Setup terminal event handlers."""
        print("\n=== SETTING UP TERMINAL EVENTS ===")
        
        # Try to connect to hyperlink signals
        signal_connected = False
        
        # Try main signal
        try:
            self.terminal.connect("hyperlink-hover-uri-changed", self.on_hyperlink_hover)
            print("✓ Connected to hyperlink-hover-uri-changed")
            signal_connected = True
        except Exception as e:
            print(f"✗ Failed to connect hyperlink-hover-uri-changed: {e}")
        
        # Try alternative signals
        alternatives = [
            "hyperlink_hover_uri_changed",
            "notify::hyperlink-hover-uri",
            "notify::current-hyperlink-uri"
        ]
        
        for signal_name in alternatives:
            if signal_connected:
                break
            try:
                self.terminal.connect(signal_name, self.on_hyperlink_hover_alt, signal_name)
                print(f"✓ Connected to {signal_name}")
                signal_connected = True
            except Exception as e:
                print(f"✗ Failed to connect {signal_name}: {e}")
        
        # Setup click handler
        click_gesture = Gtk.GestureClick()
        click_gesture.connect("pressed", self.on_terminal_clicked)
        self.terminal.add_controller(click_gesture)
        print("✓ Click handler setup")
        
        if not signal_connected:
            print("⚠ WARNING: No hyperlink signals could be connected!")
    
    def on_hyperlink_hover(self, terminal, uri, bbox):
        """Handle hyperlink hover signal."""
        print(f"HYPERLINK HOVER: uri='{uri}', bbox={bbox}")
        if uri:
            self.terminal._current_uri = uri
            print(f"✓ Hyperlink detected: {uri}")
        else:
            if hasattr(self.terminal, '_current_uri'):
                delattr(self.terminal, '_current_uri')
            print("✓ Hyperlink hover cleared")
    
    def on_hyperlink_hover_alt(self, terminal, *args):
        """Alternative hyperlink hover handler."""
        signal_name = args[-1] if args else "unknown"
        print(f"ALTERNATIVE HYPERLINK SIGNAL '{signal_name}': args={args}")
        
        # Try to find URI in args
        uri = None
        for arg in args[:-1]:  # Exclude signal name
            if isinstance(arg, str) and ('http' in arg or 'ftp' in arg):
                uri = arg
                break
        
        if uri:
            self.terminal._current_uri = uri
            print(f"✓ Hyperlink detected via {signal_name}: {uri}")
        else:
            if hasattr(self.terminal, '_current_uri'):
                delattr(self.terminal, '_current_uri')
            print(f"✓ Hyperlink cleared via {signal_name}")
    
    def on_terminal_clicked(self, gesture, n_press, x, y):
        """Handle terminal clicks."""
        print(f"TERMINAL CLICKED: position=({x}, {y}), n_press={n_press}")
        
        if hasattr(self.terminal, '_current_uri'):
            uri = self.terminal._current_uri
            print(f"✓ Opening hyperlink: {uri}")
            
            try:
                if sys.platform == 'win32':
                    subprocess.run(['cmd', '/c', 'start', '', uri])
                elif sys.platform == 'darwin':
                    subprocess.run(['open', uri])
                else:
                    try:
                        subprocess.run(['xdg-open', uri])
                    except FileNotFoundError:
                        webbrowser.open(uri)
                print(f"✓ Hyperlink opened successfully")
                return True
            except Exception as e:
                print(f"✗ Failed to open hyperlink: {e}")
        else:
            print("No hyperlink under cursor")
        
        return False
    
    def on_insert_url(self, button):
        """Insert a test URL."""
        test_url = "https://www.google.com"
        self.terminal.feed_child(f"echo 'Test URL: {test_url}'\n".encode())
        print(f"Inserted test URL: {test_url}")
    
    def on_insert_osc8(self, button):
        """Insert an OSC8 hyperlink."""
        # OSC8 format: \e]8;;URL\e\\TEXT\e]8;;\e\\
        osc8_link = "\033]8;;https://www.example.com\033\\Click here\033]8;;\033\\"
        self.terminal.feed_child(f"echo -e '{osc8_link}'\n".encode())
        print("Inserted OSC8 hyperlink")
    
    def on_test_signals(self, button):
        """Test signal emission manually."""
        print("Testing signal emission...")
        try:
            # Try to emit signals manually for testing
            GLib.timeout_add(1000, self.emit_test_signal)
        except Exception as e:
            print(f"Signal test failed: {e}")
    
    def emit_test_signal(self):
        """Emit a test signal."""
        print("Attempting to emit test hyperlink signal...")
        try:
            # This might not work, but it's worth trying
            self.terminal.emit("hyperlink-hover-uri-changed", "https://test.com", None)
        except Exception as e:
            print(f"Failed to emit signal: {e}")
        return False
    
    def spawn_shell(self):
        """Spawn a shell in the terminal."""
        try:
            self.terminal.spawn_sync(
                Vte.PtyFlags.DEFAULT,
                None,  # working directory
                ["/bin/bash", "-i"],  # command
                None,  # environment
                GLib.SpawnFlags.SEARCH_PATH,
                None,  # child setup
                None,  # cancellable
            )
            print("✓ Shell spawned successfully")
        except Exception as e:
            print(f"✗ Failed to spawn shell: {e}")

class VTETestApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.test.vte.hyperlink")
        self.connect("activate", self.on_activate)
    
    def on_activate(self, app):
        window = VTEHyperlinkTest(app)
        window.present()

if __name__ == "__main__":
    print("Starting VTE Hyperlink Test...")
    app = VTETestApp()
    app.run(sys.argv)