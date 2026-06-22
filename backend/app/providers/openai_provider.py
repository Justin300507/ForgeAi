import os

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "gpt-4o-mini"
FALLBACK_MODEL = "gpt-4o"

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Add it to backend/.env to use the V9 pipeline."
            )
        _client = OpenAI(api_key=api_key, timeout=120)
    return _client


def generate(
    prompt: str,
    max_tokens: int = 4000,
    model: str = DEFAULT_MODEL,
) -> str:
    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        if hasattr(response, "usage") and response.usage:
            u = response.usage
            print(
                f"[OPENAI] model={model} "
                f"Prompt={u.prompt_tokens} "
                f"Completion={u.completion_tokens} "
                f"Total={u.total_tokens}"
            )
        return response.choices[0].message.content
    except Exception as e:
        if model == DEFAULT_MODEL:
            # Fallback to gpt-4o if mini unavailable
            response = client.chat.completions.create(
                model=FALLBACK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.2,
            )
            return response.choices[0].message.content
        raise
