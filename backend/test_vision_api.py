import os, json, urllib.request, urllib.error
from dotenv import load_dotenv
load_dotenv()

key = os.environ.get("OPENROUTER_API_KEY", "")
tiny_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="

models = [
    "meta-llama/llama-3.2-11b-vision-instruct",
    "google/gemini-2.0-flash",
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini",
    "qwen/qwen-vl-plus",
]

for model in models:
    payload = json.dumps({
        "model": model,
        "max_tokens": 30,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tiny_png}"}},
            {"type": "text", "text": "What color is this image?"}
        ]}]
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"][:80]
            print(f"OK  {model}: {content}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:250]
        print(f"{e.code} {model}: {body}")
    except Exception as e:
        print(f"ERR {model}: {e}")
