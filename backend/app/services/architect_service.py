import json
import time

from app.prompts.architect_prompt import build_architect_prompt
from app.models.architecture_models import ArchitecturePlan
from app.providers.ai_provider import generate_content


def generate_architecture(
    project_plan,
    provider="auto",
    max_tokens=1800
):

    print("\n=== START ARCHITECT ===")

    start = time.time()

    prompt = build_architect_prompt(
        project_plan
    )

    text = generate_content(
        prompt,
        provider,
        max_tokens=max_tokens
    )

    with open(
        "architect_response.txt",
        "w",
        encoding="utf-8"
    ) as f:

        f.write(text)

    print(
        f"Architect Response Length: {len(text)}"
    )

    print(
        f"Architect Time: {time.time() - start:.2f}s"
    )

    clean_text = text
    clean_text = clean_text.replace(
        "```json",
        ""
    )
    clean_text = clean_text.replace(
        "```",
        ""
    )
    clean_text = clean_text.strip()

    try:

        data = json.loads(
            clean_text
        )

    except json.JSONDecodeError as e:

        print(
            "\n=== ARCHITECT JSON ERROR ==="
        )

        print(e)

        with open(
            "architect_failed_response.txt",
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                clean_text
            )

        print(
            "Saved failed architect response"
        )

        raise

    allowed_keys = {
        "api_endpoints",
        "database_schema",
        "folder_structure",
        "frontend_structure"
    }

    data = {
        k: v
        for k, v in data.items()
        if k in allowed_keys
    }

    validated = ArchitecturePlan(
        **data
    )

    print(
        "=== END ARCHITECT ==="
    )

    return validated.model_dump()