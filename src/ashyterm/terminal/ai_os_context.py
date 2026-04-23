"""OS/locale detection + system-prompt builder for the AI assistant.

These helpers used to live on ``TerminalAiAssistant`` but none of them
depend on assistant state — they just inspect ``/etc/os-release``,
``/etc/lsb-release`` and the process locale. Keeping them here lets
the prompt assembly stay unit-testable and keeps the assistant class
focused on request orchestration.
"""

from __future__ import annotations

import locale
import os
import re
from typing import Tuple

from ..utils.logger import log_swallowed_exception


# Conservative allowlist for values read from /etc/os-release and
# similar. Everything outside this set is stripped so the system
# prompt cannot be hijacked via an adversarial OS field.
_OS_VALUE_PATTERN = re.compile(r"[^\w .\-()/:,]")
_OS_VALUE_MAX_LEN = 100


def sanitize_os_value(value: str) -> str:
    """Return ``value`` clipped to ``_OS_VALUE_MAX_LEN`` and stripped of
    characters outside the prompt-safe allowlist.
    """
    sanitized = _OS_VALUE_PATTERN.sub("", value)
    return sanitized[:_OS_VALUE_MAX_LEN]


def parse_os_release(path: str = "/etc/os-release") -> Tuple[str, str]:
    """Parse an ``os-release``-style file and return ``(os_name, base_distro)``.

    Both strings are sanitized. Missing keys yield ``"Linux"`` / ``""``.
    Read failures are logged and return the same defaults.
    """
    os_name = "Linux"
    base_distro = ""
    try:
        with open(path, "r") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    raw = line.split("=", 1)[1].strip().strip('"')
                    os_name = sanitize_os_value(raw)
                elif line.startswith("ID_LIKE="):
                    raw = line.split("=", 1)[1].strip().strip('"')
                    base_distro = sanitize_os_value(raw)
    except Exception as exc:
        log_swallowed_exception(exc)
    return os_name, base_distro


def parse_lsb_release(path: str = "/etc/lsb-release") -> str:
    """Parse an ``lsb-release``-style file and return ``os_name``."""
    os_name = "Linux"
    try:
        with open(path, "r") as f:
            for line in f:
                if line.startswith("DISTRIB_DESCRIPTION="):
                    raw = line.split("=", 1)[1].strip().strip('"')
                    os_name = sanitize_os_value(raw)
                    break
    except Exception as exc:
        log_swallowed_exception(exc)
    return os_name


def detect_os_context() -> str:
    """Return a human-readable OS name for the system prompt.

    Prefers ``/etc/os-release`` (and includes ``ID_LIKE`` when present),
    then falls back to ``/etc/lsb-release``, then plain ``"Linux"``.
    """
    if os.path.exists("/etc/os-release"):
        os_name, base_distro = parse_os_release()
        if base_distro:
            return f"{os_name} (based on {base_distro})"
        return os_name
    if os.path.exists("/etc/lsb-release"):
        return parse_lsb_release()
    return "Linux"


_LANG_MAP = {
    "pt": "Portuguese",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "cs": "Czech",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "no": "Norwegian",
    "hu": "Hungarian",
    "ro": "Romanian",
    "bg": "Bulgarian",
    "el": "Greek",
    "he": "Hebrew",
    "hr": "Croatian",
    "sk": "Slovak",
    "et": "Estonian",
    "is": "Icelandic",
}


def language_from_locale(lang_code: str | None) -> str:
    """Resolve a locale code (e.g. ``pt_BR``) to its English name.

    Returns ``"English"`` for unknown or empty inputs so the prompt
    template always has a sane fallback.
    """
    if not lang_code:
        return "English"
    prefix = lang_code.split("_")[0].lower()
    return _LANG_MAP.get(prefix, "English")


def detect_language() -> str:
    """Resolve the system's default locale to a language name."""
    try:
        lang_code = locale.getdefaultlocale()[0] or "en_US"
    except Exception:
        return "English"
    return language_from_locale(lang_code)


def build_system_prompt(template: str) -> str:
    """Render ``template`` with detected ``language`` and ``os_context``."""
    return template.format(
        language=detect_language(),
        os_context=detect_os_context(),
    )
