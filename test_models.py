import requests

def test_groq():
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer gsk_dummy"} # Will probably return 401 without real key, but if they follow spec let's see shape
    try:
        r = requests.get(url, headers=headers)
        print("Groq:", r.status_code)
    except Exception as e:
        print("Groq Error:", e)

def test_gemini():
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    headers = {"x-goog-api-key": "dummy"}
    try:
        r = requests.get(url, headers=headers)
        print("Gemini:", r.status_code, r.text[:100])
    except Exception as e:
        print("Gemini Error:", e)

test_groq()
test_gemini()
