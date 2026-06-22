from app.services.gemini_service import client
from google import genai as _genai


def generate(prompt: str, max_tokens: int = 8000) -> str:
    config = _genai.types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        temperature=0.2,
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=config,
    )

    if not response or not response.text:
        raise Exception("Gemini returned empty or blocked response")

    return response.text