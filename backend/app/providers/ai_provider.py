from app.providers.gemini_provider import generate as gemini_generate
from app.providers.openrouter_provider import generate as openrouter_generate
from app.providers.groq_provider import generate as groq_generate


def generate_content(
    prompt,
    provider="auto",
    max_tokens=4000
):

    if provider == "gemini":

        print("Using Gemini")

        return gemini_generate(
            prompt
        )

    if provider == "groq":

        print("Using Groq")

        return groq_generate(
            prompt,
            max_tokens=max_tokens
        )

    if provider == "openrouter":

        print("Using OpenRouter")

        return openrouter_generate(
            prompt,
            max_tokens=max_tokens
        )

    try:

        print("Using Gemini")

        return gemini_generate(
            prompt
        )

    except Exception as e:

        print(
            f"Gemini failed: {e}"
        )

    try:

        print("Using Groq")

        return groq_generate(
            prompt,
            max_tokens=max_tokens
        )

    except Exception as e:

        print(
            f"Groq failed: {e}"
        )

    print(
        "Using OpenRouter"
    )

    return openrouter_generate(
        prompt,
        max_tokens=max_tokens
    )