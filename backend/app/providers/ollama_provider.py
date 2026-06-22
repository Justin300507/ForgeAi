import requests


def generate(
    prompt,
    model="qwen2.5-coder:7b",
    max_tokens=6000
):

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": max_tokens
                }
            },
            timeout=600
        )

        response.raise_for_status()

    except requests.exceptions.RequestException as e:
        raise Exception(
            f"Ollama (final fallback) failed — is `ollama serve` running? {e}"
        )

    data = response.json()

    if "response" not in data:
        raise Exception(f"Ollama returned unexpected payload: {data}")

    return data["response"]