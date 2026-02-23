import os
import requests

api_key = os.popen('gsettings get org.communitybig.ashyterm.ai ai-assistant-api-key').read().strip().strip("'")

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
    for line in resp.iter_lines():
        if line:
            print(line.decode("utf-8"))
except Exception as e:
    print("Exception!!", e)
