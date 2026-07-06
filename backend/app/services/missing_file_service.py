from app.providers.ai_provider import (
    generate_content
)

from app.prompts.missing_file_prompt import (
    build_missing_file_prompt
)
from app.utils.json_cleaner import extract_json
import json


def generate_missing_file(
    filepath,
    error,
    provider="auto",
    project_path=None
):

    prompt = build_missing_file_prompt(
        filepath,
        error,
        project_path=project_path
    )

    text = generate_content(
        prompt,
        provider,
        max_tokens=8000,
        stage="missing_file",
    )

    try:

        return extract_json(text)

    except (json.JSONDecodeError, ValueError) as e:

        print(
            "\n=== MISSING FILE AGENT ERROR ==="
        )

        print(e)

        print(text)

        return {
            "path": filepath,
            "content": ""
        }