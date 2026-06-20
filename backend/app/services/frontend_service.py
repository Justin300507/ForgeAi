import json
import time

from fastapi import HTTPException

from app.prompts.frontend_prompt import build_frontend_prompt
from app.models.frontend_models import FrontendPlan
from app.providers.ai_provider import generate_content

def generate_frontend(
architecture,
provider="auto",
max_tokens=1500
):

````
try:

    print("\n=== START FRONTEND ===")

    start = time.time()

    prompt = build_frontend_prompt(
        architecture
    )

    text = generate_content(
        prompt,
        provider,
        max_tokens=max_tokens
    )

    print(
        f"Frontend Time: {time.time() - start:.2f}s"
    )

    with open(
        "frontend_response.txt",
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

    clean_text = clean_text.replace(
        "\t",
        " "
    )

    print(
        f"Frontend Response Length: {len(clean_text)}"
    )

    try:

        data = json.loads(
            clean_text
        )

    except json.JSONDecodeError as e:

        print(
            "\n=== FRONTEND JSON ERROR ==="
        )

        print(e)

        with open(
            "frontend_failed_response.txt",
            "w",
            encoding="utf-8"
        ) as f:

            f.write(clean_text)

        raise HTTPException(
            status_code=500,
            detail="Frontend returned invalid JSON."
        )

    if not isinstance(
        data,
        dict
    ):

        raise HTTPException(
            status_code=500,
            detail="Frontend response is not a JSON object."
        )

    if "files" not in data:

        raise HTTPException(
            status_code=500,
            detail="Frontend response missing 'files' field."
        )

    validated = FrontendPlan(
        **data
    )

    print(
        "=== END FRONTEND ==="
    )

    return validated.model_dump()

except HTTPException:

    raise

except Exception as e:

    print(
        "FRONTEND ERROR:",
        e
    )

    raise HTTPException(
        status_code=500,
        detail=(
            f"ForgeAI frontend generation failed: {str(e)}"
        )
    )
````
