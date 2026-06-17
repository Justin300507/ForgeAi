from app.providers.gemini_provider import generate as gemini_generate
from app.providers.openrouter_provider import generate as openrouter_generate

USE_GEMINI = False


def generate_content(prompt):

    if USE_GEMINI:
        try:
            print("Using Gemini")
            return gemini_generate(prompt)

        except Exception as e:
            print("Gemini failed:", e)

    print("Using OpenRouter")

    try:
        response = openrouter_generate(prompt)

        print("OpenRouter Success")

        return response

    except Exception as e:

        print("OpenRouter failed:", e)

        raise Exception("All providers failed")