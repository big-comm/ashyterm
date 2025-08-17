# ashyterm/terminal/tabs.py

from typing import Optional, Callable, List
import threading
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Adw, Gio, Gdk, GLib, Pango
from gi.repository import Vte

from ..sessions.models import SessionItem
from .manager import TerminalManager

# Import new utility systems
from ..utils.logger import get_logger
from ..utils.translation_utils import _


class TerminalPaneWithTitleBar(Gtk.Box):
    """A terminal pane with an integrated, Adwaita-native title bar."""

    def __init__(self, terminal: Vte.Terminal, title: str = "Terminal"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.terminal = terminal
        self._title = title
        self.title_bar = self._create_title_bar()
        self.append(self.title_bar)

        current_parent = terminal.get_parent()
        if isinstance(current_parent, Gtk.ScrolledWindow):
            self.scrolled_window = current_parent
            if grandparent := current_parent.get_parent():
                grandparent.set_child(None)
        else:
            self.scrolled_window = Gtk.ScrolledWindow()
            self.scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            if current_parent:
                current_parent.set_child(None)

        if terminal.get_parent() != self.scrolled_window:
            self.scrolled_window.set_child(terminal)

        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)
        self.append(self.scrolled_window)
        self.on_close_requested: Optional[Callable[[Vte.Terminal], None]] = None

    def _create_title_bar(self) -> Adw.HeaderBar:
        css = """
        headerbar { min-height: 0px; }
        tabbar { margin: -8px; }
        .terminal-tab-view headerbar entry,
        .terminal-tab-view headerbar spinbutton,
        .terminal-tab-view headerbar button { 
            margin-top: -10px; 
            margin-bottom: -10px; 
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        title_bar = Adw.HeaderBar()
        title_bar.set_show_end_title_buttons(False)
        title_bar.set_show_start_title_buttons(False)
        self.title_label = Gtk.Label(
            label=self._title, ellipsize=Pango.EllipsizeMode.END, xalign=0.0
        )
        title_bar.set_title_widget(self.title_label)
        close_button = Gtk.Button(
            icon_name="window-close-symbolic", tooltip_text="Close Pane"
        )
        close_button.connect("clicked", self._on_close_clicked)
        title_bar.pack_end(close_button)
        return title_bar

    def _on_close_clicked(self, button) -> None:
        if self.on_close_requested:
            self.on_close_requested(self.terminal)

    def set_title(self, title: str) -> None:
        self._title = title
        if hasattr(self, "title_label"):
            self.title_label.set_text(title)

    def get_terminal(self) -> Vte.Terminal:
        return self.terminal


class TabManager:
    def __init__(self, terminal_manager: TerminalManager):
        self.logger = get_logger("ashyterm.tabs.manager")
        self.terminal_manager = terminal_manager
        self.tab_view = Adw.TabView()
        self.tab_bar = Adw.TabBar(view=self.tab_view)
        self.tab_bar.get_style_context().add_class("tabbar")

        # Justification: This CSS block performs three optimizations:
        # 1. Compacts the HeaderBar and applies negative margin to the tab bar for an integrated look.
        # 2. The new `.tabbar .tab .label` rule sets the text truncation mode to 'start'.
        #    This ensures that when a tab title is too long, the end of the text
        #    remains visible, which is ideal for displaying directory paths.
        # 3. Adds a rule to ensure that menu separators inside a terminal panel
        #    retain the default appearance, fixing the issue of thick lines.
        css = """
        headerbar.main-header-bar {
            min-height: 0;
            padding: 0;
            border: none;
            box-shadow: none;
        }
        .tabbar { 
            margin: -8px; 
        }
        .tabbar .tab .label {
            -gtk-ellipsize-mode: start;
        }
        /*
        * FIX: Force menu separators inside popovers to have a standard 1px height.
        * This targets any popover menu within our main tab view, fixing the issue
        * where separators become thick after creating a split pane.
        * Increased priority and more specific selectors to override conflicting rules.
        */
        popover.menu menuitem separator,
        .terminal-tab-view popover.menu menuitem separator {
            border-top: 1px solid @borders !important;
            margin: 6px 0 !important;
            min-height: 1px !important;
            max-height: 1px !important;
            padding: 0 !important;
            background: none !important;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._closing_pages = set()
        self._individual_pane_closes = set()
        self._last_focused_terminal = None
        self._setup_tab_components()

        self.on_quit_application = None
        self.logger.info("Tab manager initialized")

    def _setup_tab_components(self) -> None:
        self.tab_view.set_vexpand(True)
        self.tab_view.connect("close-page", self._on_close_page_request)
        self.terminal_manager.set_terminal_exit_handler(
            self._on_terminal_process_exited
        )

    def get_tab_view(self) -> Adw.TabView:
        return self.tab_view

    def get_tab_bar(self) -> Adw.TabBar:
        return self.tab_bar

    def create_local_tab(self, title: str = "Local", working_directory: Optional[str] = None) -> Optional[Adw.TabPage]:
        if working_directory:
            self.logger.debug(f"Creating local tab '{title}' with working directory: {working_directory}")
        else:
            self.logger.debug(f"Creating local tab '{title}' with default working directory")
            
        terminal = self.terminal_manager.create_local_terminal(
            title, working_directory=working_directory
        )
        if not terminal:
            return None
        return self._create_tab_for_terminal(terminal, title, "")

    def create_ssh_tab(self, session: SessionItem) -> Optional[Adw.TabPage]:
        terminal = self.terminal_manager.create_ssh_terminal(session)
        if not terminal:
            return None
        return self._create_tab_for_terminal(
            terminal, session.name, "network-server-symbolic"
        )

    def _create_tab_for_terminal(
        self, terminal: Vte.Terminal, title: str, icon_name: str
    ) -> Optional[Adw.TabPage]:
        scrolled_window = Gtk.ScrolledWindow(child=terminal)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        # CORREÇÃO ESTRUTURAL: Use Adw.Bin como o contêiner raiz da aba.
        # Isso nos permite substituir seu conteúdo mais tarde durante um split.
        root_container = Adw.Bin()
        root_container.set_child(scrolled_window)

        page = self.tab_view.add_page(root_container, None)
        page.set_title(title)
        if icon_name:
            page.set_icon(Gio.ThemedIcon.new(icon_name))

        terminal.ashy_parent_page = page
        if not hasattr(page, "_base_title"):
            page._base_title = title

        self.tab_view.set_selected_page(page)
        self._schedule_terminal_focus(terminal, title)

        GLib.idle_add(self.update_all_tab_titles)
        GLib.idle_add(self._update_tab_bar_visibility)
        return page

    def _on_pane_focus_in(self, controller, terminal):
        self._last_focused_terminal = weakref.ref(terminal)

    def get_selected_terminal(self) -> Optional[Vte.Terminal]:
        if self._last_focused_terminal and (terminal := self._last_focused_terminal()):
            if (
                terminal.get_ancestor(Adw.TabView) == self.tab_view
                and terminal.get_realized()
            ):
                return terminal
        page = self.tab_view.get_selected_page()
        if not page:
            return None
        terminals = self.get_all_terminals_in_page(page)
        return terminals[0] if terminals else None

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.HORIZONTAL)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.VERTICAL)

    def _split_terminal(
        self, focused_terminal: Vte.Terminal, orientation: Gtk.Orientation
    ) -> None:
        with self._creation_lock:
            page = self.get_page_for_terminal(focused_terminal)
            if not page:
                self.logger.error(
                    "Cannot split: could not find parent page for terminal."
                )
                return

            # 1. Crie o novo terminal e seu painel (lógica existente, está correta)
            terminal_id = getattr(focused_terminal, "terminal_id", None)
            info = self.terminal_manager.registry.get_terminal_info(terminal_id)
            identifier = info.get("identifier") if info else "Local"
            new_terminal = None
            new_pane_title = "Terminal"
            if isinstance(identifier, SessionItem):
                new_pane_title = identifier.name
                new_terminal = (
                    self.terminal_manager.create_ssh_terminal(identifier)
                    if identifier.is_ssh()
                    else self.terminal_manager.create_local_terminal(identifier.name)
                )
            else:
                new_pane_title = "Local"
                new_terminal = self.terminal_manager.create_local_terminal(
                    new_pane_title
                )

            if not new_terminal:
                self.logger.error("Failed to create new terminal for split.")
                return

            new_terminal.ashy_parent_page = page
            new_pane = TerminalPaneWithTitleBar(new_terminal, new_pane_title)
            new_pane.on_close_requested = self.close_pane
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_pane_focus_in, new_terminal)
            new_terminal.add_controller(focus_controller)

            # 2. Identifique o widget exato a ser substituído e seu contêiner pai.
            widget_to_replace = focused_terminal.get_parent()  # Gtk.ScrolledWindow
            if isinstance(widget_to_replace.get_parent(), TerminalPaneWithTitleBar):
                widget_to_replace = widget_to_replace.get_parent()

            container = widget_to_replace.get_parent()
            if not container:
                self.logger.error(
                    "Cannot split: focused terminal is not in a container."
                )
                self.terminal_manager.remove_terminal(new_terminal)
                return

            # 3. Se o widget a ser substituído for um ScrolledWindow (primeiro split),
            #    envolva-o em nosso painel customizado.
            pane_being_split = widget_to_replace
            if isinstance(pane_being_split, Gtk.ScrolledWindow):
                title = page.get_title() if page else "Terminal"
                pane_being_split = TerminalPaneWithTitleBar(focused_terminal, title)
                pane_being_split.on_close_requested = self.close_pane

            # 4. Crie o novo Gtk.Paned que conterá o split.
            new_split_paned = Gtk.Paned(orientation=orientation)
            new_split_paned.set_end_child(new_pane)  # Adiciona o novo painel primeiro

            # 5. LÓGICA CRÍTICA: Desvincule o painel existente de seu pai
            #    e adicione-o ao novo Gtk.Paned.
            if isinstance(container, Gtk.Paned):
                is_start_child = container.get_start_child() == widget_to_replace
                if is_start_child:
                    container.set_start_child(None)
                else:
                    container.set_end_child(None)

                # Agora que está desvinculado, adicione-o ao novo split
                new_split_paned.set_start_child(pane_being_split)

                # Coloque o novo split de volta no lugar do antigo
                if is_start_child:
                    container.set_start_child(new_split_paned)
                else:
                    container.set_end_child(new_split_paned)

            elif isinstance(container, Adw.Bin):
                container.set_child(None)  # Desvincula
                new_split_paned.set_start_child(
                    pane_being_split
                )  # Vincula ao novo split
                container.set_child(new_split_paned)  # Coloca o novo split na aba

            else:
                self.logger.error(
                    f"Cannot split: unknown container type {type(container)}"
                )
                self.terminal_manager.remove_terminal(new_terminal)
                return

            GLib.idle_add(lambda: self._set_paned_position(new_split_paned))
            self._schedule_terminal_focus(new_terminal, "New Split")
            GLib.idle_add(self.update_all_tab_titles)

    def _set_paned_position(self, paned: Gtk.Paned) -> bool:
        alloc = paned.get_allocation()
        total_size = (
            alloc.width
            if paned.get_orientation() == Gtk.Orientation.HORIZONTAL
            else alloc.height
        )
        if total_size > 0:
            paned.set_position(total_size // 2)
        return False

    def close_pane(self, focused_terminal: Vte.Terminal) -> None:
        with self._cleanup_lock:
            terminal_id = getattr(focused_terminal, "terminal_id", None)
            if terminal_id:
                self._individual_pane_closes.add(terminal_id)
            self.terminal_manager.remove_terminal(focused_terminal, force_kill_group=False)

    def _find_pane_and_parent(self, terminal: Vte.Terminal) -> tuple:
        widget = terminal
        while widget:
            parent = widget.get_parent()
            if isinstance(
                widget, (TerminalPaneWithTitleBar, Gtk.ScrolledWindow)
            ) and isinstance(parent, Gtk.Paned):
                return widget, parent
            widget = parent
        return None, None

    def get_all_terminals_in_page(self, page: Adw.TabPage) -> List[Vte.Terminal]:
        terminals = []
        if root_widget := page.get_child():
            self._find_terminals_recursive(root_widget, terminals)
        return terminals

    def _find_terminals_recursive(
        self, widget, terminals_list: List[Vte.Terminal]
    ) -> None:
        # Caso base 1: Encontramos nosso contêiner customizado.
        if isinstance(widget, TerminalPaneWithTitleBar):
            terminals_list.append(widget.get_terminal())
            return

        # Caso base 2: Encontramos um terminal diretamente (primeiro terminal da aba).
        if isinstance(widget, Gtk.ScrolledWindow) and isinstance(
            widget.get_child(), Vte.Terminal
        ):
            terminals_list.append(widget.get_child())
            return

        # Passo recursivo para Gtk.Paned (splits).
        if isinstance(widget, Gtk.Paned):
            if start_child := widget.get_start_child():
                self._find_terminals_recursive(start_child, terminals_list)
            if end_child := widget.get_end_child():
                self._find_terminals_recursive(end_child, terminals_list)
            return

        # Passo recursivo para outros contêineres (como o Adw.Bin da página da aba).
        if hasattr(widget, "get_child") and (child := widget.get_child()):
            self._find_terminals_recursive(child, terminals_list)

    def update_terminal_title_in_splits(
        self, terminal: Vte.Terminal, new_title: str
    ) -> None:
        pane, _ = self._find_pane_and_parent(terminal)
        if pane and isinstance(pane, TerminalPaneWithTitleBar):
            pane.set_title(new_title)
        page = self.get_page_for_terminal(terminal)
        if page:
            page._base_title = new_title
            self.set_tab_title(page, new_title)

    def _on_close_page_request(self, tab_view, page) -> bool:
        # Previne chamadas múltiplas para a mesma aba
        if id(page) in self._closing_pages:
            return True

        self._closing_pages.add(id(page))

        terminals_in_page = self.get_all_terminals_in_page(page)
        self.logger.info(
            f"User requested close for tab '{page.get_title()}' with {len(terminals_in_page)} terminals."
        )

        # Ação principal: Itere por todos os terminais na aba e inicie o processo de término para cada um.
        # A nova lógica robusta em `remove_terminal` garantirá que todos os processos
        # (incluindo os de splits) sejam finalizados corretamente.
        for terminal in terminals_in_page:
            self.terminal_manager.remove_terminal(terminal, force_kill_group=True)

        # Retornar True informa ao Adw.TabView que estamos tratando o fechamento manualmente.
        # A aba só será realmente fechada pela função _on_terminal_process_exited
        # quando o último terminal nela confirmar sua saída.
        return True

    def _on_terminal_process_exited(
        self, terminal: Vte.Terminal, child_status: int, identifier
    ) -> None:
        with self._cleanup_lock:
            terminal_id = getattr(terminal, "terminal_id", "N/A")
            page = self.get_page_for_terminal(terminal)

            if not page:
                self.terminal_manager._cleanup_terminal(terminal, terminal_id)
                return

            # CRITICAL FIX: Check if this is a split pane BEFORE cleanup
            pane_to_remove, parent_paned = self._find_pane_and_parent(terminal)
            is_split_pane = parent_paned is not None
            
            # Get remaining terminals in this page BEFORE cleanup
            remaining_terminals_in_page = [
                t for t in self.get_all_terminals_in_page(page) 
                if getattr(t, "terminal_id", None) != terminal_id
            ]

            # Cleanup terminal resources
            self.terminal_manager._cleanup_terminal(terminal, terminal_id)

            if is_split_pane:
                # FIX: For splits, only remove UI pane - don't check global count
                self.logger.debug(f"Removing split pane for terminal {terminal_id}.")
                self._remove_pane_ui(pane_to_remove, parent_paned)
                GLib.idle_add(self.update_all_tab_titles)
                # DO NOT check if last terminal - splits should not close application
                return

            # Only for terminals that are NOT splits (last terminal in tab)
            if not remaining_terminals_in_page:
                # This was the last terminal in the tab
                if id(page) in self._closing_pages:
                    # Close initiated by user (clicked X)
                    self.logger.debug(f"Finishing user-initiated close for page of terminal {terminal_id}.")
                    self.tab_view.close_page_finish(page, True)
                    self._closing_pages.discard(id(page))
                elif self.terminal_manager.settings_manager.get("auto_close_tab", True):
                    # Process exited naturally (typed exit)
                    self.logger.debug(f"Auto-closing page for terminal {terminal_id}.")
                    self.tab_view.close_page(page)
                else:
                    # Auto-close disabled
                    self.logger.debug(f"Auto-close disabled. Tab for terminal {terminal_id} remains open.")
                    page.set_title(f"{page.get_title()} [{_('Exited')}]")

                # FIX: Only check global count AFTER processing tab closure
                active_terminals_left = self.terminal_manager.registry.get_active_terminal_count()
                if active_terminals_left == 0:
                    self.logger.info("Last active terminal has exited. Requesting application quit.")
                    GLib.idle_add(self._quit_application)
                    return

                GLib.idle_add(self._update_tab_bar_visibility)

    def _schedule_terminal_focus(self, terminal: Vte.Terminal, title: str) -> None:
        def focus_terminal():
            if terminal and terminal.get_realized():
                terminal.grab_focus()
                return False
            return True

        GLib.timeout_add(100, focus_terminal)

    def get_page_for_terminal(self, terminal: Vte.Terminal) -> Optional[Adw.TabPage]:
        return getattr(terminal, "ashy_parent_page", None)

    def set_tab_title(self, page: Adw.TabPage, title: str) -> None:
        if not (page and title):
            return
        if not hasattr(page, "_base_title") or not page._base_title:
            page._base_title = title

        terminal_count = len(self.get_all_terminals_in_page(page))
        final_title = (
            f"{page._base_title} ({terminal_count})"
            if terminal_count > 1
            else page._base_title
        )
        page.set_title(final_title)

    def update_all_tab_titles(self) -> None:
        for i in range(self.get_tab_count()):
            page = self.tab_view.get_nth_page(i)
            if page:
                base_title = getattr(page, "_base_title", page.get_title())
                if " (" in base_title and base_title.endswith(")"):
                    base_title = base_title[: base_title.rfind(" (")]
                page._base_title = base_title
                self.set_tab_title(page, base_title)

    def get_tab_count(self) -> int:
        return self.tab_view.get_n_pages()

    def get_all_terminals(self) -> List[Vte.Terminal]:
        return [
            term
            for page in self.tab_view.get_pages()
            for term in self.get_all_terminals_in_page(page)
        ]

    def create_initial_tab_if_empty(self, working_directory: Optional[str] = None) -> Optional[Adw.TabPage]:
        if self.get_tab_count() == 0:
            if working_directory:
                self.logger.info(f"Creating initial tab with working directory: {working_directory}")
            return self.create_local_tab("Local", working_directory=working_directory)
        return None

    def copy_from_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            return self.terminal_manager.copy_selection(terminal)
        return False

    def paste_to_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            return self.terminal_manager.paste_clipboard(terminal)
        return False

    def select_all_in_current_terminal(self) -> None:
        if terminal := self.get_selected_terminal():
            self.terminal_manager.select_all(terminal)

    def zoom_in_current_terminal(self, step: float = 0.1) -> bool:
        if terminal := self.get_selected_terminal():
            return self.terminal_manager.zoom_in(terminal, step)
        return False

    def zoom_out_current_terminal(self, step: float = 0.1) -> bool:
        if terminal := self.get_selected_terminal():
            return self.terminal_manager.zoom_out(terminal, step)
        return False

    def zoom_reset_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            return self.terminal_manager.zoom_reset(terminal)
        return False

    def focus_next_pane(self) -> bool:
        """Focus the next pane in the current tab."""
        page = self.tab_view.get_selected_page()
        if not page:
            return False
        
        terminals = self.get_all_terminals_in_page(page)
        if len(terminals) <= 1:
            return False
        
        current_terminal = self.get_selected_terminal()
        if not current_terminal:
            terminals[0].grab_focus()
            return True
        
        try:
            current_index = terminals.index(current_terminal)
            next_index = (current_index + 1) % len(terminals)
            terminals[next_index].grab_focus()
            return True
        except ValueError:
            terminals[0].grab_focus()
            return True

    def focus_previous_pane(self) -> bool:
        """Focus the previous pane in the current tab."""
        page = self.tab_view.get_selected_page()
        if not page:
            return False
        
        terminals = self.get_all_terminals_in_page(page)
        if len(terminals) <= 1:
            return False
        
        current_terminal = self.get_selected_terminal()
        if not current_terminal:
            terminals[-1].grab_focus()
            return True
        
        try:
            current_index = terminals.index(current_terminal)
            prev_index = (current_index - 1) % len(terminals)
            terminals[prev_index].grab_focus()
            return True
        except ValueError:
            terminals[-1].grab_focus()
            return True

    def focus_pane_direction(self, direction: str) -> bool:
        """Focus pane in specified direction (up/down/left/right)."""
        page = self.tab_view.get_selected_page()
        if not page:
            return False
        
        terminals = self.get_all_terminals_in_page(page)
        if len(terminals) <= 1:
            return False
        
        current_terminal = self.get_selected_terminal()
        if not current_terminal:
            return False
        
        # Get current terminal container position
        current_container = self._get_terminal_container(current_terminal)
        if not current_container:
            return False
            
        current_allocation = current_container.get_allocation()
        current_x = current_allocation.x + current_allocation.width // 2
        current_y = current_allocation.y + current_allocation.height // 2
        
        best_terminal = None
        best_distance = float('inf')
        
        for terminal in terminals:
            if terminal == current_terminal:
                continue
                
            container = self._get_terminal_container(terminal)
            if not container:
                continue
                
            allocation = container.get_allocation()
            term_x = allocation.x + allocation.width // 2
            term_y = allocation.y + allocation.height // 2
            
            # Check if terminal is in the right direction
            valid_direction = False
            if direction == "up" and term_y < current_y:
                valid_direction = True
            elif direction == "down" and term_y > current_y:
                valid_direction = True
            elif direction == "left" and term_x < current_x:
                valid_direction = True
            elif direction == "right" and term_x > current_x:
                valid_direction = True
            
            if valid_direction:
                distance = ((term_x - current_x) ** 2 + (term_y - current_y) ** 2) ** 0.5
                if distance < best_distance:
                    best_distance = distance
                    best_terminal = terminal
        
        if best_terminal:
            best_terminal.grab_focus()
            return True
        
        return False

    def _get_terminal_container(self, terminal: Vte.Terminal):
        """Get the container that holds the terminal for position calculation."""
        widget = terminal.get_parent()  # ScrolledWindow
        
        while widget:
            parent = widget.get_parent()
            
            if isinstance(parent, Gtk.Paned):
                return widget
            
            if isinstance(parent, Adw.Bin):
                return widget
                
            widget = parent
        
        return widget

    def _update_tab_bar_visibility(self) -> None:
        self.tab_bar.set_visible(self.get_tab_count() > 1)

    def _quit_application(self) -> bool:
        if self.on_quit_application:
            self.on_quit_application()
        return False

    def _remove_pane_ui(self, pane_to_remove, parent_paned):
        # Identifica qual painel sobrevive
        is_start_child = parent_paned.get_start_child() == pane_to_remove
        survivor_pane = (
            parent_paned.get_end_child()
            if is_start_child
            else parent_paned.get_start_child()
        )

        if not survivor_pane:
            self.logger.warning("Survivor pane not found during UI cleanup.")
            return

        grandparent = parent_paned.get_parent()
        if not grandparent:
            self.logger.warning("Grandparent container not found during UI cleanup.")
            return

        # Desconecta os filhos do Gtk.Paned ANTES de movê-los.
        # Isso é crucial para evitar os avisos do GTK sobre foco.
        parent_paned.set_start_child(None)
        parent_paned.set_end_child(None)

        # Substitui o Gtk.Paned pelo painel sobrevivente no contêiner avô.
        if isinstance(grandparent, Gtk.Paned):
            is_grandparent_start = grandparent.get_start_child() == parent_paned
            if is_grandparent_start:
                grandparent.set_start_child(survivor_pane)
            else:
                grandparent.set_end_child(survivor_pane)
        elif hasattr(grandparent, "set_child"):  # Cobre Adw.Bin, etc.
            grandparent.set_child(survivor_pane)

        # Se o painel sobrevivente for o último, ele pode precisar ser "desembrulhado"
        # do nosso contêiner customizado para voltar a ser um simples terminal.
        is_last_split = not isinstance(grandparent, Gtk.Paned)
        if is_last_split and isinstance(survivor_pane, TerminalPaneWithTitleBar):
            self.logger.debug("Collapsing last split back to a simple terminal view.")
            survivor_terminal = survivor_pane.get_terminal()

            # Remove o terminal de seu ScrolledWindow atual
            old_scrolled_window = survivor_terminal.get_parent()
            if old_scrolled_window:
                old_scrolled_window.set_child(None)

            # Cria um novo ScrolledWindow para o terminal
            new_scrolled_window = Gtk.ScrolledWindow(child=survivor_terminal)
            new_scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )

            # Coloca o novo ScrolledWindow no contêiner da aba
            if hasattr(grandparent, "set_child"):
                grandparent.set_child(new_scrolled_window)

        GLib.idle_add(self.update_all_tab_titles)