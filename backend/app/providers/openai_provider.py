import os

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "gpt-4o-mini"
FALLBACK_MODEL = "gpt-4o"

# Auto mode has an independent Cerebras fallback.  Keep the inexpensive
# OpenAI-mini attempt long enough for a normal generation, but bounded below
# Cerebras's 60-second request window so an unavailable OpenAI leg cannot
# stall the whole generation before the fallback gets a chance to run.
_REQUEST_TIMEOUT_SECONDS = 45.0
_SDK_MAX_RETRIES = 0

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
        # The OpenAI SDK retries connection failures, 408s, 429s, and 5xxs
        # twice by default.  ForgeAI owns cross-provider retry/fallback, so
        # disable SDK retries and let a single bounded attempt hand off to
        # Cerebras deterministically.
        _client = OpenAI(
            api_key=api_key,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            max_retries=_SDK_MAX_RETRIES,
        )
    return _client


def generate(
    prompt: str,
    max_tokens: int = 4000,
    model: str = DEFAULT_MODEL,
    allow_model_escalation: bool = False,
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
        if model == DEFAULT_MODEL and allow_model_escalation:
            # Direct callers can explicitly request a higher-quality retry.
            # The automatic provider policy leaves this off so a failed mini
            # request proceeds straight to Cerebras rather than silently
            # spending another OpenAI call.
            response = client.chat.completions.create(
                model=FALLBACK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.2,
            )
            return response.choices[0].message.content
        raise
