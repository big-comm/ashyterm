"""AI assistant integration for AshyTerm terminals."""

from __future__ import annotations

import json
import re
import textwrap
import threading
import weakref
from typing import Any, Dict, List, Optional, Tuple

import requests

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from ..utils.logger import get_logger


class TerminalAiAssistant:
    """Coordinates conversations with an external AI service."""

    DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
    DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
    _SYSTEM_PROMPT = (
        "You are the AshyTerm Assistant, a Linux specialist operating inside a terminal."
        " Answer only questions related to Linux, system administration, networking, command-line"
        " tools, shells (bash, sh, zsh), scripts, Python, Perl, and automation tasks."
        " For any topic outside that scope, politely state that you do not have knowledge about it."
        " Always respond in Portuguese unless the user explicitly asks for another language."
        " The response MUST be a JSON object with the fields: 'reply' (required string with the"
        " explanation) and 'commands' (optional list of objects containing 'command' and"
        " 'description' for standalone terminal commands that do not depend on scripts). Do not"
        " include any text outside the JSON. Use 'commands' only for commands the user could run"
        " directly without relying on a provided script. Never provide code snippets or a"
        " 'code_snippets' field. If the user requests code or scripts, reply with a short apology and"
        " explain that you are not configured to generate code."
        " Remember that the BigLinux and"
        " BigCommunity distributions are based on Manjaro and take that into account when replying."
    )

    def __init__(self, window, settings_manager, terminal_manager):
        self.logger = get_logger("ashyterm.terminal.ai_assistant")
        self._window_ref = weakref.ref(window)
        self.settings_manager = settings_manager
        self.terminal_manager = terminal_manager
        self._conversations: Dict[int, List[Dict[str, str]]] = {}
        self._terminal_refs: Dict[int, weakref.ReferenceType] = {}
        self._inflight: Dict[int, bool] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_enabled(self) -> bool:
        return self.settings_manager.get("ai_assistant_enabled", False)

    def missing_configuration(self) -> List[str]:
        missing = []
        provider = self.settings_manager.get("ai_assistant_provider", "").strip()
        api_key = self.settings_manager.get("ai_assistant_api_key", "").strip()
        if not provider:
            missing.append("provider")
            return missing
        if provider in {"groq", "gemini"}:
            if not api_key:
                missing.append("api_key")
        else:
            missing.append("provider")
        return missing

    def request_assistance(self, terminal, prompt: str) -> bool:
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

        worker = threading.Thread(
            target=self._process_request_thread, args=(terminal_id, prompt), daemon=True
        )
        worker.start()
        return True

    def clear_conversation_for_terminal(self, terminal) -> None:
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return
        with self._lock:
            self._cleanup_terminal_state(terminal_id)

    def clear_all_conversations(self) -> None:
        with self._lock:
            self._conversations.clear()
            self._terminal_refs.clear()
            self._inflight.clear()

    def handle_setting_changed(self, key: str, _old_value: Any, new_value: Any) -> None:
        if key == "ai_assistant_enabled" and not new_value:
            self.clear_all_conversations()
        if key in {"ai_assistant_provider", "ai_assistant_api_key"}:
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
                terminal, lambda _ref, tid=terminal_id: self._cleanup_terminal_state(tid)
            )
        return terminal_id

    def _process_request_thread(self, terminal_id: int, prompt: str) -> None:
        try:
            if self._should_decline_code_request(prompt):
                self._build_messages(terminal_id, prompt)
                refusal = (
                    "Desculpe, no momento não estou programado para gerar trechos de código."
                )
                self._record_assistant_message(terminal_id, refusal)
                GLib.idle_add(
                    self._display_assistant_reply,
                    terminal_id,
                    refusal,
                    [],
                    [],
                )
                return

            messages = self._build_messages(terminal_id, prompt)
            config = self._load_configuration()
            content = self._perform_request(config, messages)
            reply, commands, code_snippets = self._parse_assistant_payload(content)
            self._record_assistant_message(terminal_id, reply)
            GLib.idle_add(
                self._display_assistant_reply,
                terminal_id,
                reply,
                commands,
                code_snippets,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("AI assistant request failed: %s", exc)
            error_message = "Sorry, I couldn't complete the request: {}".format(
                exc
            )
            self._record_assistant_message(terminal_id, error_message)
            GLib.idle_add(
                self._display_error_reply,
                terminal_id,
                error_message,
            )
        finally:
            with self._lock:
                self._inflight.pop(terminal_id, None)

    def _build_messages(self, terminal_id: int, prompt: str) -> List[Dict[str, str]]:
        with self._lock:
            history = self._conversations.setdefault(terminal_id, [])
            history.append({"role": "user", "content": prompt})
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": self._SYSTEM_PROMPT}
            ]
            messages.extend(history)
            return messages

    def _load_configuration(self) -> Dict[str, str]:
        config = {
            "provider": self.settings_manager.get("ai_assistant_provider", "").strip(),
            "model": self.settings_manager.get("ai_assistant_model", "").strip(),
            "api_key": self.settings_manager.get("ai_assistant_api_key", "").strip(),
        }
        if not config["provider"]:
            raise RuntimeError(
                "Select a provider in Preferences > Terminal > AI Assistant."
            )
        if config["provider"] == "groq" and not config["model"]:
            config["model"] = self.DEFAULT_GROQ_MODEL
        elif config["provider"] == "gemini" and not config["model"]:
            config["model"] = self.DEFAULT_GEMINI_MODEL
        return config

    def _perform_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        provider = config["provider"]
        if provider == "groq":
            return self._perform_groq_request(config, messages)
        if provider == "gemini":
            return self._perform_gemini_request(config, messages)
        raise RuntimeError(
            f"Provider '{provider}' is not supported in this version."
        )

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

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to query the Gemini service: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP error {response.status_code}: {response.text.strip()}"
            )

        try:
            response_data = response.json()
        except ValueError as exc:
            raise RuntimeError("Gemini returned an invalid JSON response.") from exc

        candidates = response_data.get("candidates") or []
        if not candidates:
            raise RuntimeError("The server response did not contain any suggestions.")

        collected: List[str] = []
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if not parts:
                continue
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    collected.append(part["text"])

        if collected:
            return "\n".join(collected)

        raise RuntimeError("Gemini did not return any usable content.")

    def _perform_groq_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        api_key = config.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("Configure the Groq API key in Preferences.")

        model = config.get("model", "").strip() or self.DEFAULT_GROQ_MODEL

        payload_messages = self._build_openai_messages(messages)
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload: Dict[str, Any] = {"model": model, "messages": payload_messages}

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to query the Groq service: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP error {response.status_code}: {response.text.strip()}"
            )

        try:
            response_data = response.json()
        except ValueError as exc:
            raise RuntimeError("Groq returned an invalid JSON response.") from exc

        choices = response_data.get("choices") or []
        if not choices:
            raise RuntimeError("The server response did not contain any suggestions.")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("text")
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Groq did not return any usable content.")
        return content.strip()

    def _build_gemini_conversation(
        self, messages: List[Dict[str, str]]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        system_instruction = ""
        contents: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            text = message.get("content", "")
            if not text:
                continue
            if role == "system" and not system_instruction:
                system_instruction = text
                continue
            mapped_role = "model" if role == "assistant" else "user"
            contents.append({
                "role": mapped_role,
                "parts": [{"text": text}],
            })
        if not contents:
            contents.append({"role": "user", "parts": [{"text": ""}]})
        return system_instruction, contents

    def _build_openai_messages(
        self, messages: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        formatted: List[Dict[str, str]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if not isinstance(content, str) or not content:
                continue
            role_mapped = role
            if role not in {"system", "user", "assistant"}:
                role_mapped = "user"
            formatted.append({"role": role_mapped, "content": content})
        return formatted

    def _parse_assistant_payload(
        self, content: str
    ) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]]]:
        reply_text = self._clean_response(content)
        commands: List[Dict[str, str]] = []
        code_snippets: List[Dict[str, str]] = []

        try:
            payload = json.loads(reply_text)
        except json.JSONDecodeError:
            payload = self._extract_json_object(reply_text)

        if isinstance(payload, dict):
            reply_candidate = payload.get("reply")
            if isinstance(reply_candidate, str) and reply_candidate.strip():
                reply_text = reply_candidate.strip()
            commands_field = payload.get("commands")
            commands = self._normalize_commands(commands_field)

        return reply_text, commands, code_snippets

    def _clean_response(self, raw_content: str) -> str:
        clean = raw_content.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                clean = "\n".join(lines[1:-1]).strip()
        return clean

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        start = text.find("{")
        while start != -1:
            brace_level = 0
            for end in range(start, len(text)):
                char = text[end]
                if char == "{":
                    brace_level += 1
                elif char == "}":
                    brace_level -= 1
                    if brace_level == 0:
                        candidate = text[start : end + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
            start = text.find("{", start + 1)
        return None

    def _normalize_commands(self, value: Any) -> List[Dict[str, str]]:
        commands: List[Dict[str, str]] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    commands.append({"command": item.strip(), "description": ""})
                elif isinstance(item, dict):
                    candidate = item.get("command") or item.get("cmd")
                    description = item.get("description") or ""
                    if isinstance(candidate, str) and candidate.strip():
                        commands.append(
                            {
                                "command": candidate.strip(),
                                "description": description.strip()
                                if isinstance(description, str)
                                else "",
                            }
                        )
        elif isinstance(value, str) and value.strip():
            commands.append({"command": value.strip(), "description": ""})
        return commands

    def _normalize_code_snippets(self, value: Any) -> List[Dict[str, str]]:
        snippets: List[Dict[str, str]] = []
        if not isinstance(value, list):
            return snippets
        for item in value:
            if isinstance(item, dict):
                code = item.get("code") or ""
                if not isinstance(code, str) or not code.strip():
                    continue
                code = textwrap.dedent(code).strip()
                code, fenced_language = self._strip_code_fence(code)
                language = item.get("language") or ""
                if not language and fenced_language:
                    language = fenced_language
                description = item.get("description") or ""
                snippets.append(
                    {
                        "code": code,
                        "language": language.strip() if isinstance(language, str) else "",
                        "description": description.strip()
                        if isinstance(description, str)
                        else "",
                    }
                )
            elif isinstance(item, str) and item.strip():
                code_text = textwrap.dedent(item).strip()
                code_text, fenced_language = self._strip_code_fence(code_text)
                snippets.append(
                    {
                        "code": code_text,
                        "language": fenced_language,
                        "description": "",
                    }
                )
        return snippets

    @staticmethod
    def _strip_code_fence(code: str) -> Tuple[str, str]:
        """Remove Markdown fences and return code plus detected language."""
        trimmed = code.strip()
        if not trimmed.startswith("```"):
            return trimmed, ""
        lines = trimmed.splitlines()
        if not lines:
            return trimmed, ""
        first_line = lines[0]
        language = first_line[3:].strip()
        body_lines = lines[1:]
        while body_lines and body_lines[-1].strip() == "```":
            body_lines.pop()
        stripped_code = "\n".join(body_lines).strip()
        return stripped_code or trimmed, language

    @staticmethod
    def _should_decline_code_request(prompt: str) -> bool:
        """Detect requests that explicitly ask for code or scripts."""
        if not isinstance(prompt, str):
            return False
        lowered = prompt.lower()
        if not lowered:
            return False
        code_terms = {
            "codigo",
            "código",
            "code",
            "script",
            "shell script",
            "programa",
            "programação",
            "function",
            "função",
            "classe",
            "snippet",
            "trecho de código",
            "escreva um",
        }
        request_terms = {
            "gere",
            "gerar",
            "crie",
            "criar",
            "escreva",
            "escrever",
            "forneça",
            "mostrar",
            "mostre",
            "faça",
            "montar",
            "monta",
            "me dê",
            "me mostre",
            "me forneça",
            "poderia",
            "pode",
        }
        has_code_term = any(term in lowered for term in code_terms) or "```" in lowered
        has_request_term = any(term in lowered for term in request_terms)
        return has_code_term and has_request_term

    def _record_assistant_message(self, terminal_id: int, message: str) -> None:
        with self._lock:
            history = self._conversations.setdefault(terminal_id, [])
            history.append({"role": "assistant", "content": message})

    def _display_assistant_reply(
        self,
        terminal_id: int,
        reply: str,
        commands: List[Dict[str, str]],
        code_snippets: List[Dict[str, str]],
    ) -> bool:
        terminal = self._get_terminal(terminal_id)
        window = self._window_ref()
        if not terminal or not window:
            # Fallback to terminal output if window not available
            if terminal:
                terminal.feed(
                    ("\n[AI Assistant] {}\n".format(reply.strip())).encode("utf-8")
                )
                for info in commands:
                    command_text = info.get("command") if isinstance(info, dict) else ""
                    if command_text:
                        terminal.feed(
                            ("[AI Assistant] Command: {}\n".format(command_text)).encode(
                                "utf-8"
                            )
                        )
                for snippet in code_snippets:
                    code_text = snippet.get("code") if isinstance(snippet, dict) else ""
                    if code_text:
                        terminal.feed(
                            ("[AI Assistant] Code suggestion:\n{}\n".format(code_text)).encode(
                                "utf-8"
                            )
                        )
            return False

        try:
            formatted_reply = self._format_reply_for_dialog(reply)
            window.show_ai_response_dialog(
                terminal, formatted_reply, commands, code_snippets
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("Failed to show AI response dialog: %s", exc)
            terminal.feed(
                ("\n[AI Assistant] {}\n".format(self._format_reply_for_dialog(reply))).encode("utf-8")
            )
        return False

    @staticmethod
    def _format_reply_for_dialog(text: str) -> str:
        """Improve readability by normalizing inline code and list formatting."""
        if not isinstance(text, str):
            return ""

        cleaned = text
        cleaned = cleaned.replace("\r\n", "\n")
        cleaned = cleaned.replace("\\n", "\n").replace("\\t", "\t")
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        cleaned = re.sub(r"\s*\+\s*", " ", cleaned)
        cleaned = re.sub(r";\s*\n", "\n", cleaned)
        cleaned = re.sub(r";\s*(?=[A-ZÁÀÃÂÉÊÍÓÔÕÚÜÇ0-9])", ".\n", cleaned)
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
        cleaned = re.sub(r"(?<!\n)(\d+\.)", r"\n\1", cleaned)
        cleaned = re.sub(r"\n\s*(\d+)\s*(?=\n\d)\n", r"\n\1", cleaned)
        cleaned = re.sub(r"\n\s*-\s+", "\n• ", cleaned)
        cleaned = re.sub(r"\n\s*\*\s+", "\n• ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        lines = []
        previous_blank = False
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                if not previous_blank:
                    lines.append("")
                    previous_blank = True
                continue
            lines.append(line)
            previous_blank = False

        return "\n".join(lines).strip()

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

    def _queue_toast(self, message: str) -> None:
        def _show_toast():
            window = self._window_ref()
            if window and hasattr(window, "toast_overlay"):
                toast = Adw.Toast(title=message)
                window.toast_overlay.add_toast(toast)
            return False

        GLib.idle_add(_show_toast)
