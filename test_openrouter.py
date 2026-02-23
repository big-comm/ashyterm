import os
import sys
import json
import logging
from ashyterm.terminal.ai_assistant import TerminalAiAssistant
from gi.repository import GLib

# mock settings
class MockSettings:
    def get(self, key, default=None):
        if key == "ai_assistant_provider": return "openrouter"
        if key == "ai_assistant_model": return "google/gemini-2.5-flash"
        if key == "ai_assistant_enabled": return True
        if key == "ai_assistant_api_key":
            return os.getenv("OPENROUTER_API_KEY", "")
        return default

class MockWin: pass
class MockTermManager: pass

def get_history_mgr(): pass
import ashyterm.terminal.ai_assistant
ashyterm.terminal.ai_assistant.get_ai_history_manager = lambda: get_history_mgr()

assistant = TerminalAiAssistant(MockWin(), MockSettings(), MockTermManager())
# We will just call the openai_compat request directly to see stream output

# Grab api key from the config file, wait, let's just make a script that prints what the stream gets
