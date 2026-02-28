import os
import sys

from gi.repository import GLib, Gio
from ashyterm.settings import SettingsManager
from ashyterm.terminal.ai_assistant import TerminalAiAssistant

# Load standard settings
settings = SettingsManager()

key = settings.get("ai_assistant_api_key", "").strip()
print("Key length:", len(key))
print("Provider:", settings.get("ai_assistant_provider", ""))
print("Model:", settings.get("ai_assistant_model", ""))

# Just do a quick request bypassing everything inside Assistant
import requests
url = "https://openrouter.ai/api/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {key}"
}
payload = {
    "model": settings.get("ai_assistant_model", ""),
    "messages": [{"role": "user", "content": "olá"}],
    "stream": True
}
try:
    resp = requests.post(url, headers=headers, json=payload, timeout=10, stream=True)
    print("Status:", resp.status_code)
    for line in resp.iter_lines():
        if line:
            print(line.decode("utf-8"))
except Exception as e:
    print("Error:", e)
