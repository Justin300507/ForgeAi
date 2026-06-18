import json

from app.providers.ai_provider import generate_content
from app.prompts.fixer_prompt import build_fixer_prompt


def generate_fix(
    error,
    provider="auto"
):

    prompt = build_fixer_prompt(error)

    text = generate_content(
        prompt,
        provider
    )

    text = text.replace("```json", "")
    text = text.replace("```", "")
    text = text.strip()

    try:

        return json.loads(text)

    except Exception as e:

        print("\n=== FIX AGENT JSON ERROR ===")
        print(e)

        print("\n=== RAW RESPONSE ===")
        print(text)

        print("=====================\n")

        return {
            "path": "",
            "content": ""
        }