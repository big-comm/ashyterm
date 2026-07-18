# ashyterm/terminal/ai_assistant.py

"""AI assistant integration for AshyTerm terminals."""

from __future__ import annotations

import threading
import weakref
from typing import Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject

from ..data.ai_history_manager import get_ai_history_manager
from ..utils.logger import get_logger, log_swallowed_exception
from ..utils.translation_utils import _
from .ai_os_context import build_system_prompt, sanitize_os_value
from .ai_provider_format import (
    build_gemini_conversation as _build_gemini_conversation_impl,
    build_openai_messages as _build_openai_messages_impl,
    extract_gemini_content as _extract_gemini_content_impl,
    extract_openai_content as _extract_openai_content_impl,
    parse_sse_line as _parse_sse_line_impl,
)
from .ai_providers import (
    build_local_headers as _build_local_headers,
    perform_openai_compat as _perform_openai_compat,
    resolve_local_url as _resolve_local_url,
)
from .ai_response_parser import (
    clean_response as _clean_response_impl,
    extract_json_object as _extract_json_object_impl,
    normalize_commands as _normalize_commands_impl,
    parse_assistant_payload as _parse_assistant_payload_impl,
)

# Lazy-loaded requests module (avoid import overhead on startup)
_requests_module = None


def _get_requests():
    """Get the requests module, importing lazily on first use."""
    global _requests_module
    if _requests_module is None:
        import requests  # type: ignore[import-untyped]

        _requests_module = requests
    return _requests_module


class TerminalAiAssistant(GObject.Object):
    """Coordinates conversations with an external AI service."""

    __gsignals__ = {
        # Signal emitted when streaming message chunks arrive
        # Args: (chunk: str, is_done: bool)
        "streaming-chunk": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        # Signal emitted when a full response is ready
        # Args: (reply: str, commands: list)
        "response-ready": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        # Signal emitted on error
        # Args: (error_message: str)
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
    DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
    DEFAULT_OPENROUTER_MODEL = "openrouter/polaris-alpha"
    DEFAULT_LOCAL_MODEL = "llama3.2"
    DEFAULT_CEREBRAS_MODEL = "llama-3.3-70b"
    DEFAULT_GITHUB_MODEL = "gpt-4o-mini"
    DEFAULT_MISTRAL_MODEL = "mistral-small-latest"

    # PROMPT OTIMIZADO, DIRETO E DINÂMICO
    _SYSTEM_PROMPT_TEMPLATE = (
        "You are an expert Linux terminal assistant running on {os_context}."
        " Your goal is to provide accurate, safe, and executable command-line solutions."
        "\n\n"
        "**CRITICAL RULES:**\n"
        "1. **OUTPUT FORMAT:** You must respond with RAW JSON only. Do NOT wrap the output in markdown code blocks (like ```json ... ```).\n"
        '2. **JSON STRUCTURE:** {{ "reply": "<explanation using markdown>", "commands": ["<cmd1>", "<cmd2>"] }}\n'
        "3. **LANGUAGE:** Respond strictly in {language}.\n"
        "4. **SCOPE:** Answer only Linux, networking, coding, and sysadmin questions. Politely refuse off-topic requests.\n"
        "\n"
        "**FIELD DETAILS:**\n"
        "- 'reply': The explanation text. You MAY use Markdown (bold, lists, inline code) inside this string for readability.\n"
        "- 'commands': A list of standalone, executable shell commands appropriate for {os_context}. Do not include placeholders like '<file>' unless necessary.\n"
    )

    # Kept for backwards compatibility with existing tests + any
    # external caller that reaches in. The implementation lives in
    # ``ai_os_context.sanitize_os_value``.
    _sanitize_os_value = staticmethod(sanitize_os_value)

    @classmethod
    def _get_system_prompt(cls) -> str:
        """Render the system prompt with locale + OS context substituted in."""
        return build_system_prompt(cls._SYSTEM_PROMPT_TEMPLATE)

    def __init__(self, window, settings_manager, terminal_manager):
        super().__init__()
        self.logger = get_logger("ashyterm.terminal.ai_assistant")
        self._window_ref = weakref.ref(window)
        self.settings_manager = settings_manager
        self.terminal_manager = terminal_manager
        self._conversations: Dict[int, List[Dict[str, str]]] = {}
        self._max_conversation_messages = 40  # Keep last N messages per terminal
        self._terminal_refs: Dict[int, weakref.ReferenceType] = {}
        self._inflight: Dict[int, bool] = {}
        self._cancel_flags: Dict[int, bool] = {}
        self._active_responses: Dict[int, Any] = {}
        self._thread_local = threading.local()
        self._lock = threading.RLock()
        self._history_manager_instance = None  # Lazy loaded via property
        # Callbacks for streaming updates
        self._streaming_callback: Optional[Callable[[str, bool], None]] = None

    @property
    def _history_manager(self):
        """Lazy load the AI history manager on first access."""
        if self._history_manager_instance is None:
            self._history_manager_instance = get_ai_history_manager()
        return self._history_manager_instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_enabled(self) -> bool:
        return self.settings_manager.get("ai_assistant_enabled", False)

    def missing_configuration(self) -> List[str]:
        missing = []
        provider = self.settings_manager.get("ai_assistant_provider", "").strip()

        api_key = self.settings_manager.get(
            f"ai_assistant_{provider}_api_key", ""
        ).strip()
        if not api_key:
            api_key = self.settings_manager.get("ai_assistant_api_key", "").strip()
        if not provider:
            missing.append("provider")
            return missing
        if provider in {
            "groq",
            "gemini",
            "openrouter",
            "cerebras",
            "github",
            "mistral",
        }:
            if not api_key:
                missing.append("api_key")
        elif provider == "local":
            # Local providers may not need API key
            base_url = self.settings_manager.get("ai_local_base_url", "").strip()
            if not base_url:
                missing.append("base_url")
        else:
            missing.append("provider")
        return missing

    def request_assistance(
        self,
        terminal: Any,
        prompt: str,
        streaming_callback: Optional[Callable[[str, bool], None]] = None,
    ) -> bool:
        """Kick off an assistant request for the provided terminal."""
        if not prompt:
            return False
        if not self.is_enabled():
            self._queue_toast(
                "Enable the AI assistant in Preferences before requesting help."
            )
            return False
        try:
            terminal_id = self._ensure_terminal_reference(terminal)
        except ValueError:
            self._queue_toast("Unable to identify the active terminal.")
            return False

        with self._lock:
            if self._inflight.get(terminal_id):
                self._queue_toast(
                    "The assistant is still processing the previous request."
                )
                return False
            self._inflight[terminal_id] = True
            self._streaming_callback = streaming_callback

        # Save user message to history
        self._history_manager.add_user_message(prompt)

        from ..core.tasks import AsyncTaskManager

        AsyncTaskManager.get().submit_io(
            self._process_request_thread, terminal_id, prompt
        )
        return True

    def request_assistance_simple(
        self,
        prompt: str,
        streaming_callback: Optional[Callable[[str, bool], None]] = None,
    ) -> bool:
        """
        Request assistance without a specific terminal context.
        Used by the AI overlay panel.
        """
        if not prompt:
            return False
        if not self.is_enabled():
            self._queue_toast(
                "Enable the AI assistant in Preferences before requesting help."
            )
            return False

        # Use a special terminal_id for non-terminal requests
        terminal_id = -1  # Special ID for overlay panel

        with self._lock:
            if self._inflight.get(terminal_id):
                self._queue_toast(
                    "The assistant is still processing the previous request."
                )
                return False
            self._inflight[terminal_id] = True
            self._streaming_callback = streaming_callback

        # Save user message to history
        self._history_manager.add_user_message(prompt)

        from ..core.tasks import AsyncTaskManager

        AsyncTaskManager.get().submit_io(
            self._process_request_thread, terminal_id, prompt
        )
        return True

    def clear_conversation_for_terminal(self, terminal: Any) -> None:
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return
        with self._lock:
            self._cleanup_terminal_state(terminal_id)

    def clear_all_conversations(self) -> None:
        with self._lock:
            for tid in list(self._inflight.keys()):
                self.cancel_request(tid)
            self._conversations.clear()
            self._terminal_refs.clear()
            self._inflight.clear()
            self._cancel_flags.clear()
            self._active_responses.clear()

    def shutdown(self) -> None:
        """Clean up all state for window destruction."""
        self.clear_all_conversations()

    def cancel_request(self, terminal_id: int = -1) -> None:
        """Cancel an inflight request for the given terminal."""
        with self._lock:
            if self._inflight.get(terminal_id):
                self._cancel_flags[terminal_id] = True
                response = self._active_responses.get(terminal_id)
                if response:
                    try:
                        response.close()
                    except Exception as exc:
                        log_swallowed_exception(exc)

    def handle_setting_changed(self, key: str, _old_value: Any, new_value: Any) -> None:
        if key == "ai_assistant_enabled" and not new_value:
            self.clear_all_conversations()
        if (
            key
            in {
                "ai_assistant_provider",
                "ai_assistant_api_key",
                "ai_assistant_model",
                "ai_local_base_url",
            }
            or key.endswith("_api_key")
            or key.endswith("_model")
        ):
            self.clear_all_conversations()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_terminal_reference(self, terminal) -> int:
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            raise ValueError("terminal is missing a terminal_id attribute")

        if terminal_id not in self._terminal_refs:
            self._terminal_refs[terminal_id] = weakref.ref(
                terminal,
                lambda _ref, tid=terminal_id: self._cleanup_terminal_state(tid),  # type: ignore[misc]
            )
        return terminal_id

    def _process_request_thread(self, terminal_id: int, prompt: str) -> None:
        self._thread_local.terminal_id = terminal_id
        with self._lock:
            self._cancel_flags[terminal_id] = False

        try:
            messages = self._build_messages(terminal_id, prompt)
            config = self._load_configuration()

            # Check if we should use streaming
            if self._streaming_callback:
                content = self._perform_streaming_request(config, messages)
            else:
                content = self._perform_request(config, messages)

            reply, commands, code_snippets = self._parse_assistant_payload(content)
            self._record_assistant_message(terminal_id, reply)
            # Save to history with commands (convert dicts to strings for storage)
            command_strings_for_history = [
                cmd.get("command", "")
                for cmd in commands
                if isinstance(cmd, dict) and cmd.get("command")
            ]
            self._history_manager.add_assistant_message(
                reply, command_strings_for_history
            )

            GLib.idle_add(
                self._display_assistant_reply,
                terminal_id,
                reply,
                commands,
                code_snippets,
            )
        except Exception as exc:  # pylint: disable=broad-except
            from ..utils.security import redact_secrets

            self.logger.error(
                f"AI assistant request failed: {redact_secrets(str(exc))}"
            )
            error_message = "Sorry, I couldn't complete the request: {}".format(
                redact_secrets(str(exc))
            )
            self._record_assistant_message(terminal_id, error_message)
            GLib.idle_add(
                self._display_error_reply,
                terminal_id,
                error_message,
            )
            # Emit error signal
            GLib.idle_add(self.emit, "error", error_message)
        finally:
            with self._lock:
                self._inflight.pop(terminal_id, None)
                self._cancel_flags.pop(terminal_id, None)
                self._active_responses.pop(terminal_id, None)
                self._streaming_callback = None

    # Hard upper bound on a single prompt sent to an AI provider.
    _MAX_PROMPT_CHARS = 50_000
    _MAX_MESSAGE_CHARS = 50_000

    @staticmethod
    def _truncate_for_prompt(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n\n…[truncated {len(text) - limit} characters]"

    def _build_messages(self, terminal_id: int, prompt: str) -> List[Dict[str, str]]:
        prompt = self._truncate_for_prompt(prompt, self._MAX_PROMPT_CHARS)
        with self._lock:
            history = self._conversations.setdefault(terminal_id, [])
            history.append({"role": "user", "content": prompt})
            # Cap conversation length to prevent unbounded memory growth.
            if len(history) > self._max_conversation_messages:
                history[:] = history[-self._max_conversation_messages :]
            # Cap per-message size so long echoed replies don't bloat
            # subsequent requests (provider charges per token).
            for msg in history:
                if isinstance(msg.get("content"), str):
                    msg["content"] = self._truncate_for_prompt(
                        msg["content"], self._MAX_MESSAGE_CHARS
                    )
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": self._get_system_prompt()}
            ]
            messages.extend(history)
            return messages

    def _load_configuration(self) -> Dict[str, str]:
        provider = self.settings_manager.get("ai_assistant_provider", "").strip()

        api_key = self.settings_manager.get(
            f"ai_assistant_{provider}_api_key", ""
        ).strip()
        if not api_key:
            api_key = self.settings_manager.get("ai_assistant_api_key", "").strip()

        model = self.settings_manager.get(f"ai_assistant_{provider}_model", "").strip()
        if not model:
            model = self.settings_manager.get("ai_assistant_model", "").strip()

        config = {
            "provider": provider,
            "model": model,
            "api_key": api_key,
        }
        config["local_base_url"] = self.settings_manager.get(
            "ai_local_base_url", "http://localhost:11434/v1"
        ).strip()
        if not config["provider"]:
            raise RuntimeError(
                "Select a provider in Preferences > Terminal > AI Assistant."
            )
        if not config["model"]:
            config["model"] = self._default_model_for_provider(config["provider"])
        return config

    def _default_model_for_provider(self, provider: str) -> str:
        defaults = {
            "groq": self.DEFAULT_GROQ_MODEL,
            "gemini": self.DEFAULT_GEMINI_MODEL,
            "openrouter": self.DEFAULT_OPENROUTER_MODEL,
            "local": self.DEFAULT_LOCAL_MODEL,
            "cerebras": self.DEFAULT_CEREBRAS_MODEL,
            "github": self.DEFAULT_GITHUB_MODEL,
            "mistral": self.DEFAULT_MISTRAL_MODEL,
        }
        return defaults.get(provider, "")

    # -------------------------------------------------------------------------
    # Generic OpenAI-compatible API helpers (eliminates duplicate code)
    # -------------------------------------------------------------------------

    def _make_api_request(
        self,
        requests,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        provider_name: str,
        timeout: int,
    ) -> Dict[str, Any]:
        """Make HTTP request and return parsed JSON response."""
        try:
            terminal_id = getattr(self._thread_local, "terminal_id", -1)
            with self._lock:
                if self._cancel_flags.get(terminal_id):
                    raise RuntimeError(_("Request cancelled by user."))

            response = requests.post(
                url, headers=headers, json=payload, timeout=timeout
            )

            with self._lock:
                if self._cancel_flags.get(terminal_id):
                    response.close()
                    raise RuntimeError(_("Request cancelled by user."))

        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to query the {provider_name} service: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise RuntimeError(self._format_api_error(response, provider_name))

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"{provider_name} returned an invalid JSON response."
            ) from exc

    def _extract_openai_content(
        self, response_data: Dict[str, Any], provider_name: str
    ) -> str:
        return _extract_openai_content_impl(response_data, provider_name)

    def _openai_compat_request(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        provider_name: str,
        timeout: int = 60,
    ) -> str:
        """Generic non-streaming request for OpenAI-compatible APIs."""
        requests = _get_requests()
        response_data = self._make_api_request(
            requests, url, headers, payload, provider_name, timeout
        )
        return self._extract_openai_content(response_data, provider_name)

    def _parse_sse_line(self, line_str: str) -> Tuple[Optional[str], bool]:
        return _parse_sse_line_impl(line_str)

    def _process_streaming_response(self, response) -> str:
        """Process streaming response and return full content."""
        terminal_id = getattr(self._thread_local, "terminal_id", -1)
        try:
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return self._handle_json_response(response)
            full_content = self._consume_streaming_lines(response, terminal_id)
        except Exception:
            if self._cancel_flags.get(terminal_id):
                raise RuntimeError(_("Request cancelled by user."))
            raise
        finally:
            self._close_response(response)

        if self._streaming_callback:
            GLib.idle_add(self._streaming_callback, "", True)

        return full_content

    def _consume_streaming_lines(self, response, terminal_id: int) -> str:
        chunks: list[str] = []
        for line in response.iter_lines():
            if self._cancel_flags.get(terminal_id):
                raise RuntimeError(_("Request cancelled by user."))
            if not line:
                continue
            chunk, is_eof = self._parse_sse_line(line.decode("utf-8").strip())
            if is_eof:
                break
            if chunk:
                chunks.append(chunk)
                if self._streaming_callback:
                    GLib.idle_add(self._streaming_callback, chunk, False)
        return "".join(chunks)

    @staticmethod
    def _close_response(response) -> None:
        try:
            response.close()
        except Exception as exc:
            log_swallowed_exception(exc)

    def _handle_json_response(self, response) -> str:
        """Handle a non-streaming JSON response (server ignored stream=True)."""
        try:
            data = response.json()
        except ValueError:
            data = {}
        if "error" in data:
            raise RuntimeError(
                data["error"].get("message", "Unknown API error inside JSON")
            )
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                if self._streaming_callback:
                    GLib.idle_add(self._streaming_callback, content, True)
                return content
        raise RuntimeError(
            _("Provider returned JSON instead of an event stream: {}").format(
                str(data)[:100]
            )
        )

    def _openai_compat_streaming(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        provider_name: str,
        timeout: int = 60,
    ) -> str:
        """Generic streaming request for OpenAI-compatible APIs (SSE protocol)."""
        requests = _get_requests()
        payload["stream"] = True

        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=timeout, stream=True
            )
            terminal_id = getattr(self._thread_local, "terminal_id", -1)
            with self._lock:
                if self._cancel_flags.get(terminal_id):
                    response.close()
                    raise RuntimeError(_("Request cancelled by user."))
                self._active_responses[terminal_id] = response
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to query the {provider_name} service: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise RuntimeError(self._format_api_error(response, provider_name))

        return self._process_streaming_response(response)

    # -------------------------------------------------------------------------
    # Provider-specific dispatchers
    # -------------------------------------------------------------------------

    def _perform_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        provider = config["provider"]
        if provider == "groq":
            return self._perform_groq_request(config, messages)
        if provider == "gemini":
            return self._perform_gemini_request(config, messages)
        if provider == "openrouter":
            return self._perform_openrouter_request(config, messages)
        if provider == "local":
            return self._perform_local_request(config, messages)
        if provider == "cerebras":
            return self._perform_cerebras_request(config, messages)
        if provider == "github":
            return self._perform_github_request(config, messages)
        if provider == "mistral":
            return self._perform_mistral_request(config, messages)
        raise RuntimeError(f"Provider '{provider}' is not supported in this version.")

    def _perform_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        """Perform a streaming request, sending chunks via callback."""
        provider = config["provider"]
        if provider == "local":
            return self._perform_local_streaming_request(config, messages)
        if provider == "openrouter":
            return self._perform_openrouter_streaming_request(config, messages)
        if provider == "groq":
            return self._perform_groq_streaming_request(config, messages)
        if provider == "cerebras":
            return self._perform_cerebras_streaming_request(config, messages)
        if provider == "github":
            return self._perform_github_streaming_request(config, messages)
        if provider == "mistral":
            return self._perform_mistral_streaming_request(config, messages)
        # Fall back to non-streaming for providers that don't support it well
        return self._perform_request(config, messages)

    # ── Local-AI transport ──────────────────────────────────
    # Separate from the provider-table dispatch because its URL is
    # derived from the user-configured ``local_base_url`` setting.

    def _perform_local_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._openai_compat_request(
            _resolve_local_url(config),
            _build_local_headers(config),
            {
                "model": config.get("model", "").strip() or self.DEFAULT_LOCAL_MODEL,
                "messages": self._build_openai_messages(messages),
            },
            "Local AI",
            timeout=120,
        )

    def _perform_local_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._openai_compat_streaming(
            _resolve_local_url(config),
            _build_local_headers(config),
            {
                "model": config.get("model", "").strip() or self.DEFAULT_LOCAL_MODEL,
                "messages": self._build_openai_messages(messages),
            },
            "Local AI",
            timeout=120,
        )

    # ── OpenAI-compat providers ─────────────────────────────
    # All share the same Bearer-auth + {model, messages} shape, so
    # they dispatch through ``ai_providers.perform_openai_compat``.

    def _perform_openai_compat_provider(
        self,
        provider_key: str,
        config: Dict[str, str],
        messages: List[Dict[str, str]],
        *,
        streaming: bool,
    ) -> str:
        return _perform_openai_compat(
            provider_key,
            config=config,
            messages=messages,
            streaming=streaming,
            message_builder=self._build_openai_messages,
            request_fn=self._openai_compat_request,
            streaming_fn=self._openai_compat_streaming,
        )

    def _perform_groq_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "groq", config, messages, streaming=True
        )

    def _perform_openrouter_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "openrouter", config, messages, streaming=True
        )

    def _make_gemini_api_call(
        self, url: str, headers: Dict[str, str], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make API call to Gemini and return parsed response."""
        requests = _get_requests()
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to query the Gemini service: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(self._format_api_error(response, "Gemini"))

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("Gemini returned an invalid JSON response.") from exc

    def _extract_gemini_content(self, response_data: Dict[str, Any]) -> str:
        return _extract_gemini_content_impl(response_data)

    def _perform_gemini_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        api_key = config.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("Configure the Gemini API key in Preferences.")

        model = config.get("model", "").strip() or self.DEFAULT_GEMINI_MODEL
        system_instruction, contents = self._build_gemini_conversation(messages)

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload: Dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

        response_data = self._make_gemini_api_call(url, headers, payload)
        return self._extract_gemini_content(response_data)

    def _perform_groq_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "groq", config, messages, streaming=False
        )

    def _perform_openrouter_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "openrouter", config, messages, streaming=False
        )

    def _perform_cerebras_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "cerebras", config, messages, streaming=False
        )

    def _perform_cerebras_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "cerebras", config, messages, streaming=True
        )

    def _perform_github_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "github", config, messages, streaming=False
        )

    def _perform_github_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "github", config, messages, streaming=True
        )

    def _perform_mistral_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "mistral", config, messages, streaming=False
        )

    def _perform_mistral_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        return self._perform_openai_compat_provider(
            "mistral", config, messages, streaming=True
        )

    def _format_api_error(self, response: Any, provider_name: str) -> str:
        """Format error from HTTP response. Response type is requests.Response."""
        status = response.status_code
        fallback = response.text.strip() or _("Unknown error.")
        try:
            payload = response.json()
        except ValueError:
            return _("{provider} responded with HTTP {status}: {message}").format(
                provider=provider_name, status=status, message=fallback
            )

        error_obj = payload.get("error")
        if not isinstance(error_obj, dict):
            return _("{provider} responded with HTTP {status}: {message}").format(
                provider=provider_name, status=status, message=fallback
            )

        message = error_obj.get("message")
        metadata = error_obj.get("metadata", {})
        provider_detail = metadata.get("provider_name")
        raw_detail = metadata.get("raw")
        details = []
        if provider_detail:
            details.append(provider_detail)
        if raw_detail:
            details.append(raw_detail)
        extra = f" ({' | '.join(details)})" if details else ""

        clean_message = message or fallback
        base = _("{provider} responded with HTTP {status}: {message}{detail}").format(
            provider=provider_name,
            status=status,
            message=clean_message,
            detail=extra,
        )

        # Add helpful hint for common OpenRouter errors
        if provider_name == "OpenRouter" and status == 404:
            msg_lower = (clean_message or "").lower()
            if "guardrail" in msg_lower or "data policy" in msg_lower:
                base += "\n" + _(
                    "Tip: check your privacy settings at "
                    "https://openrouter.ai/settings/privacy — "
                    "some providers require enabling free endpoints "
                    "or allowing data collection."
                )

        return base

    def _build_gemini_conversation(
        self, messages: List[Dict[str, str]]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        return _build_gemini_conversation_impl(messages)

    def _build_openai_messages(
        self, messages: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        return _build_openai_messages_impl(messages)

    def _parse_assistant_payload(
        self, content: str
    ) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]]]:
        return _parse_assistant_payload_impl(content)

    def _clean_response(self, raw_content: str) -> str:
        return _clean_response_impl(raw_content)

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        return _extract_json_object_impl(text)

    def _normalize_commands(self, value: Any) -> List[Dict[str, str]]:
        return _normalize_commands_impl(value)

    def _record_assistant_message(self, terminal_id: int, message: str) -> None:
        with self._lock:
            history = self._conversations.setdefault(terminal_id, [])
            history.append({"role": "assistant", "content": message})
            # Cap conversation length to prevent unbounded memory growth
            if len(history) > self._max_conversation_messages:
                history[:] = history[-self._max_conversation_messages :]

    def _display_assistant_reply(
        self,
        terminal_id: int,
        reply: str,
        commands: List[Dict[str, str]],
        code_snippets: List[Dict[str, str]],
    ) -> bool:
        command_strings = [
            cmd.get("command", "") for cmd in commands if isinstance(cmd, dict)
        ]
        self.emit("response-ready", reply, command_strings)
        return False

    def _display_error_reply(self, terminal_id: int, message: str) -> bool:
        self._queue_toast(message)
        return False

    def _get_terminal(self, terminal_id: int):
        ref = self._terminal_refs.get(terminal_id)
        return ref() if ref else None

    def _cleanup_terminal_state(self, terminal_id: int) -> None:
        self._conversations.pop(terminal_id, None)
        self._terminal_refs.pop(terminal_id, None)
        self._inflight.pop(terminal_id, None)
        self._cancel_flags.pop(terminal_id, None)
        self._active_responses.pop(terminal_id, None)

    def _queue_toast(self, message: str) -> None:
        def _show_toast():
            window = self._window_ref()
            if window and hasattr(window, "toast_overlay"):
                toast = Adw.Toast(title=message)
                window.toast_overlay.add_toast(toast)
            return False

        GLib.idle_add(_show_toast)
