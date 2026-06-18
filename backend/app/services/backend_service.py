import json
import time

from app.prompts.backend_prompt import build_backend_prompt
from app.models.backend_models import BackendPlan
from app.providers.ai_provider import generate_content


def generate_backend(
    architecture,
    provider="auto",
    max_tokens=1500
):

    print("\n=== START BACKEND ===")

    start = time.time()

    prompt = build_backend_prompt(
        architecture
    )

    text = generate_content(
        prompt,
        provider,
        max_tokens=max_tokens
    )

    print(
        f"Backend Response Length: {len(text)}"
    )

    print(
        f"Backend Time: {time.time() - start:.2f}s"
    )

    with open(
        "backend_response.txt",
        "w",
        encoding="utf-8"
    ) as f:
        f.write(text)

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

        for file in data["files"]:

            path = file["path"]

            path = path.strip()

            path = path.replace(
                "__init__.igma py",
                "__init__.py"
            )

            path = path.replace(
                "__init__.igma",
                "__init__.py"
            )

            file["path"] = path

            content = file["content"]

            if any(
                ord(c) > 127
                for c in path
            ):
                raise Exception(
                    f"Invalid file path generated: {path}"
                )

            if any(
                ord(c) > 127
                for c in content
            ):
                raise Exception(
                    f"Non ASCII characters found in {path}"
                )

            if not (
                path.endswith(".py")
                or path.endswith(".txt")
            ):

                with open(
                    "backend_invalid_paths.txt",
                    "a",
                    encoding="utf-8"
                ) as f:
                    f.write(f"{path}\n")

                raise Exception(
                    f"Invalid file extension: {path}"
                )

    except json.JSONDecodeError as e:

        print(
            "\n=== BACKEND JSON ERROR ==="
        )

        print(e)

        with open(
            "backend_failed_response.txt",
            "w",
            encoding="utf-8"
        ) as f:
            f.write(clean_text)

        print(
            "Saved failed backend response"
        )

        raise

    validated = BackendPlan(
        **data
    )

    print(
        "=== END BACKEND ==="
    )

    return validated.model_dump()