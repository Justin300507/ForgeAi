import json

from app.providers.ai_provider import (
    generate_content
)

from app.prompts.missing_file_prompt import (
    build_missing_file_prompt
)


def generate_missing_file(
    filepath,
    error,
    provider="auto"
):

    prompt = build_missing_file_prompt(
        filepath,
        error
    )

    text = generate_content(
        prompt,
        provider,
        max_tokens=2000
    )

    text = text.replace(
        "```json",
        ""
    )

    text = text.replace(
        "```",
        ""
    )

    text = text.strip()

    try:

        return json.loads(
            text
        )

    except Exception as e:

        print(
            "\n=== MISSING FILE AGENT ERROR ==="
        )

        print(e)

        print(text)

        return {
            "path": filepath,
            "content": ""
        }