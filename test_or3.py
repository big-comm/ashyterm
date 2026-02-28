import os
import sys
import json
import logging
from gi.repository import GLib

import requests

api_key = os.getenv("OPENROUTER_API_KEY", "")
if not api_key:
    from ashyterm.settings import SettingsManager
    settings = SettingsManager()
    api_key = settings.get("ai_assistant_api_key", "").strip()

print(f"API Key present: {bool(api_key)}")

url = "https://openrouter.ai/api/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}
payload = {
    "model": "google/gemini-2.5-flash",
    "messages": [{"role": "user", "content": "hello"}],
    "stream": True
}
print("Sending request to openrouter...")
try:
    resp = requests.post(url, headers=headers, json=payload, timeout=10, stream=True)
    print("Status code:", resp.status_code)
    print("Headers:", resp.headers)
    for line in resp.iter_lines():
        if line:
            print(line.decode("utf-8"))
except Exception as e:
    print("Exception!!", e)
