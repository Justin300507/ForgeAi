import os

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "gpt-4o-mini"
FALLBACK_MODEL = "gpt-4o"

# OpenAI is the sole provider as of 2026-07-30 (Cerebras/Gemini/Groq removed,
# see ai_provider.py's _auto_chain docstring) -- there is no other leg to
# hand off to, so this timeout only needs to bound a genuinely hung request,
# not race a fallback. It must comfortably cover the largest single call in
# the pipeline: frontend_service.py requests up to max_tokens=14000 in one
# call (vs. the small per-file backend waves), which can legitimately take
# well over a minute on gpt-4o-mini. 45s was cutting that off mid-generation
# and burning all 3 retries on the same too-short ceiling.
_REQUEST_TIMEOUT_SECONDS = 120.0
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
