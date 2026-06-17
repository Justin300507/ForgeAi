from app.providers.gemini_provider import generate as gemini_generate
from app.providers.openrouter_provider import generate as openrouter_generate


def generate_content(
    prompt,
    provider="auto"
):

    if provider == "gemini":

        print("Using Gemini")

        return gemini_generate(prompt)

    if provider == "openrouter":

        print("Using OpenRouter")

        return openrouter_generate(prompt)

    try:

        print("Using Gemini")

        return gemini_generate(prompt)

    except Exception as e:

        print("Gemini failed:", e)

        print("Using OpenRouter")

        return openrouter_generate(prompt)