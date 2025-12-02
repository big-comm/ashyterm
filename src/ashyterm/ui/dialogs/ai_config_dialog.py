# ashyterm/ui/dialogs/ai_config_dialog.py

"""AI Assistant configuration dialog."""

import threading
from typing import List, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ...settings.manager import SettingsManager
from ...utils.logger import get_logger
from ...utils.translation_utils import _


class AIConfigDialog(Adw.PreferencesWindow):
    """Dialog for configuring AI assistant settings."""

    __gsignals__ = {
        "setting-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    # Provider configurations
    PROVIDERS = [
        ("groq", "Groq", "https://api.groq.com/openai/v1"),
        ("gemini", "Gemini", "https://generativelanguage.googleapis.com"),
        ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
        ("local", "Local (Ollama/LM Studio)", "http://localhost:11434/v1"),
    ]

    DEFAULT_MODELS = {
        "groq": "llama-3.1-8b-instant",
        "gemini": "gemini-2.5-flash",
        "openrouter": "openrouter/polaris-alpha",
        "local": "llama3.2",
    }

    def __init__(self, parent_window, settings_manager: SettingsManager):
        super().__init__(
            title=_("Configure AI Assistant"),
            transient_for=parent_window,
            modal=True,
            default_width=600,
            default_height=550,
            search_enabled=False,
        )
        self.logger = get_logger("ashyterm.ui.dialogs.ai_config")
        self.settings_manager = settings_manager
        self._openrouter_models: List[Tuple[str, str]] = []
        self._fetching_models = False
        
        self._setup_ui()
        self.logger.info("AI config dialog initialized")

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        page = Adw.PreferencesPage(title=_("AI Assistant"))
        self.add(page)

        # Enable/Disable group - MUST be first element user sees
        enable_group = Adw.PreferencesGroup(
            title=_("Status"),
            description=_("Enable or disable the AI Assistant feature."),
        )
        page.add(enable_group)

        self.enable_switch = Adw.SwitchRow(
            title=_("Enable AI Assistant"),
            subtitle=_("Show the AI Assistant button in the header bar."),
        )
        self.enable_switch.set_active(
            self.settings_manager.get("ai_assistant_enabled", False)
        )
        self.enable_switch.connect("notify::active", self._on_enable_changed)
        enable_group.add(self.enable_switch)

        # Provider selection group
        provider_group = Adw.PreferencesGroup(
            title=_("Provider"),
            description=_("Select the AI service provider to use."),
        )
        page.add(provider_group)

        # Provider combo row
        self.provider_row = Adw.ComboRow(
            title=_("Provider"),
            subtitle=_("Choose between cloud providers or local models."),
        )
        provider_model = Gtk.StringList.new([label for _, label, _ in self.PROVIDERS])
        self.provider_row.set_model(provider_model)
        
        # Set current provider
        current_provider = self.settings_manager.get("ai_assistant_provider", "groq")
        provider_index = self._get_provider_index(current_provider)
        self.provider_row.set_selected(provider_index)
        self.provider_row.connect("notify::selected", self._on_provider_changed)
        provider_group.add(self.provider_row)

        # Base URL row (for local providers)
        self.base_url_row = Adw.EntryRow(
            title=_("Base URL"),
        )
        self.base_url_row.set_text(
            self.settings_manager.get("ai_local_base_url", "http://localhost:11434/v1")
        )
        self.base_url_row.connect("changed", self._on_base_url_changed)
        provider_group.add(self.base_url_row)

        # API Key group
        api_group = Adw.PreferencesGroup(
            title=_("Authentication"),
            description=_("API credentials for the selected provider."),
        )
        page.add(api_group)

        # API Key row
        self.api_key_row = Adw.PasswordEntryRow(
            title=_("API Key"),
        )
        self.api_key_row.set_text(
            self.settings_manager.get("ai_assistant_api_key", "")
        )
        self.api_key_row.connect("changed", self._on_api_key_changed)
        api_group.add(self.api_key_row)

        # Model selection group
        model_group = Adw.PreferencesGroup(
            title=_("Model"),
            description=_("Select or specify the AI model to use."),
        )
        page.add(model_group)

        # Model entry row
        self.model_row = Adw.EntryRow(
            title=_("Model Identifier"),
        )
        self.model_row.set_text(
            self.settings_manager.get("ai_assistant_model", "")
        )
        self.model_row.connect("changed", self._on_model_changed)
        model_group.add(self.model_row)

        # Fetch models button (for OpenRouter)
        self.fetch_models_row = Adw.ActionRow(
            title=_("Fetch Available Models"),
            subtitle=_("Query OpenRouter for a list of available models."),
        )
        self.fetch_models_button = Gtk.Button(label=_("Fetch Models"))
        self.fetch_models_button.set_valign(Gtk.Align.CENTER)
        self.fetch_models_button.connect("clicked", self._on_fetch_models_clicked)
        self.fetch_models_row.add_suffix(self.fetch_models_button)
        self.fetch_models_row.set_activatable_widget(self.fetch_models_button)
        model_group.add(self.fetch_models_row)

        # Model dropdown (for OpenRouter fetched models)
        self.model_dropdown_row = Adw.ComboRow(
            title=_("Available Models"),
            subtitle=_("Select a model from the fetched list."),
        )
        self.model_dropdown_row.connect("notify::selected", self._on_model_selected)
        model_group.add(self.model_dropdown_row)

        # OpenRouter-specific settings group
        self.openrouter_group = Adw.PreferencesGroup(
            title=_("OpenRouter Settings"),
            description=_("Additional settings for OpenRouter API rankings."),
        )
        page.add(self.openrouter_group)

        # Site URL row
        self.site_url_row = Adw.EntryRow(
            title=_("Site URL (optional)"),
        )
        self.site_url_row.set_text(
            self.settings_manager.get("ai_openrouter_site_url", "")
        )
        self.site_url_row.connect("changed", self._on_site_url_changed)
        self.openrouter_group.add(self.site_url_row)

        # Site name row
        self.site_name_row = Adw.EntryRow(
            title=_("Site Name (optional)"),
        )
        self.site_name_row.set_text(
            self.settings_manager.get("ai_openrouter_site_name", "")
        )
        self.site_name_row.connect("changed", self._on_site_name_changed)
        self.openrouter_group.add(self.site_name_row)

        # Update UI based on current provider
        self._update_ui_for_provider(current_provider)

    def _get_provider_index(self, provider_id: str) -> int:
        """Get the index of a provider in the PROVIDERS list."""
        for i, (pid, _name, _desc) in enumerate(self.PROVIDERS):
            if pid == provider_id:
                return i
        return 0

    def _get_selected_provider_id(self) -> str:
        """Get the currently selected provider ID."""
        index = self.provider_row.get_selected()
        if 0 <= index < len(self.PROVIDERS):
            return self.PROVIDERS[index][0]
        return "groq"

    def _update_ui_for_provider(self, provider_id: str) -> None:
        """Update UI elements based on the selected provider."""
        is_local = provider_id == "local"
        is_openrouter = provider_id == "openrouter"

        # Show/hide base URL for local provider
        self.base_url_row.set_visible(is_local)

        # Show/hide API key (local may not need it)
        self.api_key_row.set_sensitive(not is_local or False)  # Local may or may not need API key

        # Show/hide fetch models button (only for OpenRouter)
        self.fetch_models_row.set_visible(is_openrouter)
        self.model_dropdown_row.set_visible(is_openrouter and len(self._openrouter_models) > 0)

        # Show/hide OpenRouter-specific settings
        self.openrouter_group.set_visible(is_openrouter)

        # Update model placeholder
        default_model = self.DEFAULT_MODELS.get(provider_id, "")
        self.model_row.set_text(
            self.settings_manager.get("ai_assistant_model", "") or default_model
        )

        # Update subtitles based on provider
        if provider_id == "groq":
            self.model_row.set_title(_("Model Identifier"))
            self.api_key_row.set_title(_("Groq API Key"))
        elif provider_id == "gemini":
            self.model_row.set_title(_("Model Identifier"))
            self.api_key_row.set_title(_("Google AI Studio API Key"))
        elif provider_id == "openrouter":
            self.model_row.set_title(_("Model Identifier"))
            self.api_key_row.set_title(_("OpenRouter API Key"))
        elif provider_id == "local":
            self.model_row.set_title(_("Model Name"))
            self.api_key_row.set_title(_("API Key (if required)"))

    def _on_provider_changed(self, combo_row, _param) -> None:
        """Handle provider selection change."""
        provider_id = self._get_selected_provider_id()
        self.settings_manager.set("ai_assistant_provider", provider_id)
        self._update_ui_for_provider(provider_id)
        self.emit("setting-changed", "ai_assistant_provider", provider_id)

    def _on_base_url_changed(self, entry_row) -> None:
        """Handle base URL change."""
        url = entry_row.get_text().strip()
        self.settings_manager.set("ai_local_base_url", url)
        self.emit("setting-changed", "ai_local_base_url", url)

    def _on_api_key_changed(self, entry_row) -> None:
        """Handle API key change."""
        key = entry_row.get_text().strip()
        self.settings_manager.set("ai_assistant_api_key", key)
        self.emit("setting-changed", "ai_assistant_api_key", key)

    def _on_model_changed(self, entry_row) -> None:
        """Handle model change."""
        model = entry_row.get_text().strip()
        self.settings_manager.set("ai_assistant_model", model)
        self.emit("setting-changed", "ai_assistant_model", model)

    def _on_model_selected(self, combo_row, _param) -> None:
        """Handle model selection from dropdown."""
        index = combo_row.get_selected()
        if 0 <= index < len(self._openrouter_models):
            model_id, _ = self._openrouter_models[index]
            self.model_row.set_text(model_id)

    def _on_site_url_changed(self, entry_row) -> None:
        """Handle site URL change."""
        url = entry_row.get_text().strip()
        self.settings_manager.set("ai_openrouter_site_url", url)
        self.emit("setting-changed", "ai_openrouter_site_url", url)

    def _on_site_name_changed(self, entry_row) -> None:
        """Handle site name change."""
        name = entry_row.get_text().strip()
        self.settings_manager.set("ai_openrouter_site_name", name)
        self.emit("setting-changed", "ai_openrouter_site_name", name)

    def _on_enable_changed(self, switch_row, _param) -> None:
        """Handle enable/disable change."""
        enabled = switch_row.get_active()
        self.settings_manager.set("ai_assistant_enabled", enabled)
        self.emit("setting-changed", "ai_assistant_enabled", enabled)

    def _on_fetch_models_clicked(self, button) -> None:
        """Fetch available models from OpenRouter."""
        if self._fetching_models:
            return

        api_key = self.settings_manager.get("ai_assistant_api_key", "").strip()
        if not api_key:
            self._show_toast(_("Please enter an API key first."))
            return

        self._fetching_models = True
        button.set_sensitive(False)
        button.set_label(_("Fetching..."))

        thread = threading.Thread(
            target=self._fetch_openrouter_models,
            args=(api_key,),
            daemon=True,
        )
        thread.start()

    def _fetch_openrouter_models(self, api_key: str) -> None:
        """Fetch models from OpenRouter API in a background thread."""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            response = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers=headers,
                timeout=30,
            )

            if response.status_code >= 400:
                GLib.idle_add(self._on_fetch_models_error, f"HTTP {response.status_code}")
                return

            data = response.json()
            models = data.get("data", [])

            # Extract model id and name
            model_list = []
            for model in models:
                model_id = model.get("id", "")
                model_name = model.get("name", model_id)
                if model_id:
                    model_list.append((model_id, model_name))

            # Sort by name
            model_list.sort(key=lambda x: x[1].lower())

            GLib.idle_add(self._on_fetch_models_success, model_list)

        except Exception as e:
            GLib.idle_add(self._on_fetch_models_error, str(e))

    def _on_fetch_models_success(self, models: List[Tuple[str, str]]) -> None:
        """Handle successful model fetch."""
        self._fetching_models = False
        self.fetch_models_button.set_sensitive(True)
        self.fetch_models_button.set_label(_("Fetch Models"))

        self._openrouter_models = models

        if models:
            # Update dropdown
            model_names = [f"{name} ({mid})" for mid, name in models]
            self.model_dropdown_row.set_model(Gtk.StringList.new(model_names))
            self.model_dropdown_row.set_visible(True)
            self._show_toast(_("Fetched {count} models.").format(count=len(models)))
        else:
            self._show_toast(_("No models found."))

    def _on_fetch_models_error(self, error: str) -> None:
        """Handle model fetch error."""
        self._fetching_models = False
        self.fetch_models_button.set_sensitive(True)
        self.fetch_models_button.set_label(_("Fetch Models"))
        self._show_toast(_("Failed to fetch models: {error}").format(error=error))

    def _show_toast(self, message: str) -> None:
        """Show a toast notification."""
        toast = Adw.Toast(title=message)
        self.add_toast(toast)
