import os

from app.services.gemini_service import client
from google import genai as _genai

# gemini-2.5-flash (the previous hardcoded model) was retired by Google in
# July 2026: generateContent returns 404 "no longer available" even though
# ListModels still lists it. A single hardcoded model name turns every such
# retirement into a total Gemini-leg outage — the auto chain then dumps ALL
# generation load onto Groq's 12k-TPM free tier, which collapses (413s), and
# the whole pipeline fails. Candidates are tried in order per call: a 404
# retirement blacklists that model for the process lifetime, a 503 "high
# demand" just falls through to the next candidate for this call. Thinking
# stays disabled (thinking tokens cost 6x output price); Groq (free) handles
# JSON-critical small tasks, Gemini handles code generation.
_MODEL_CANDIDATES = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
]
if os.getenv("GEMINI_MODEL"):
    _MODEL_CANDIDATES.insert(0, os.getenv("GEMINI_MODEL"))

_retired_models: set[str] = set()


def current_model() -> str:
    """The model the next generate() call will try first (used as the cost-tracking label)."""
    for name in _MODEL_CANDIDATES:
        if name not in _retired_models:
            return name
    return _MODEL_CANDIDATES[-1]


def _is_retired(exc: Exception) -> bool:
    msg = str(exc)
    return "404" in msg and "no longer available" in msg


def _is_overloaded(exc: Exception) -> bool:
    msg = str(exc)
    return "503" in msg and "UNAVAILABLE" in msg


def generate(prompt: str, max_tokens: int = 8000, thinking_budget: int = 0) -> str:
    config = _genai.types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        temperature=0.2,
        thinking_config=_genai.types.ThinkingConfig(thinking_budget=thinking_budget),
    )
    last_exc: Exception | None = None
    for model in _MODEL_CANDIDATES:
        if model in _retired_models:
            continue
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            if not response or not response.text:
                raise Exception(f"Gemini ({model}) returned empty or blocked response")
            return response.text
        except Exception as e:
            last_exc = e
            if _is_retired(e):
                _retired_models.add(model)
                print(f"  [gemini] {model} retired by Google (404) — blacklisted for this process")
                continue
            if _is_overloaded(e):
                print(f"  [gemini] {model} overloaded (503) — trying next candidate")
                continue
            raise

    raise last_exc if last_exc else Exception("No Gemini model candidates available")
