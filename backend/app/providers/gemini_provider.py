from app.services.gemini_service import client
from google import genai as _genai

# gemini-2.5-flash: $0.15/1M input, $0.60/1M output (thinking disabled)
# Thinking tokens cost $3.50/1M — 6x output price — disabled to cut costs.
# Groq (free) handles JSON-critical small tasks; Gemini handles code generation
# where format compliance is less critical and output length dominates cost.
_MODEL = "gemini-2.5-flash"


def generate(prompt: str, max_tokens: int = 8000, thinking_budget: int = 0) -> str:
    config = _genai.types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        temperature=0.2,
        thinking_config=_genai.types.ThinkingConfig(thinking_budget=thinking_budget),
    )
    response = client.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=config,
    )

    if not response or not response.text:
        raise Exception("Gemini returned empty or blocked response")

    return response.text