"""Quick prompt list for AI chat conversations."""

from __future__ import annotations

import random

from ....utils.translation_utils import _


ALL_QUICK_PROMPTS = [
    ("📁", _("How do I find files by name or content?")),
    ("📊", _("How do I check disk, CPU, or memory usage?")),
    ("🔐", _("How do I change file permissions?")),
    ("🌿", _("How do I create and merge Git branches?")),
    ("🔑", _("How do I generate and use SSH keys?")),
    ("🐳", _("How do I manage Docker containers?")),
    ("📦", _("How do I install or update packages?")),
    ("⚙️", _("How do I schedule tasks with cron?")),
    ("🔍", _("How do I search and replace text in files?")),
    ("📋", _("How do I use pipes and redirections?")),
    ("🐍", _("How do I manage Python virtual environments?")),
    ("🔧", _("How do I set environment variables?")),
]


def get_random_quick_prompts(count: int = 6) -> list[tuple[str, str]]:
    """Get a random selection of quick prompts."""
    return random.sample(ALL_QUICK_PROMPTS, min(count, len(ALL_QUICK_PROMPTS)))
