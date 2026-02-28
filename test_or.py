import sys
import os
import json
from gi.repository import GLib

# mock all the things
import ashyterm.terminal.ai_assistant as aias
import requests

api_key = os.getenv("OPENROUTER_API_KEY", "")

url = "https://openrouter.ai/api/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}
payload = {
    "model": "google/gemini-2.5-flash",
    "messages": [{"role": "user", "content": "olá"}],
    "stream": True
}
try:
    resp = requests.post(url, headers=headers, json=payload, timeout=10, stream=True)
    print("Status code:", resp.status_code)
    for line in resp.iter_lines():
        if line:
            print(line.decode("utf-8"))
except Exception as e:
    print(e)
