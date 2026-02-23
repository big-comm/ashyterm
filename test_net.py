import requests

print("Connecting to openrouter...")
try:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    payload = {"model": "google/gemini-2.5-flash", "messages": [{"role": "user", "content": "olá"}], "stream": True}
    resp = requests.post(url, headers=headers, json=payload, timeout=10, stream=True)
    print("Code:", resp.status_code)
    for line in resp.iter_lines():
        print("Line:", line)
except Exception as e:
    print("Error:", e)
