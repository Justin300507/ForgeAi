import json
import time

from fastapi import HTTPException

from app.prompts.architect_prompt import build_architect_prompt
from app.models.architecture_models import ArchitecturePlan
from app.providers.ai_provider import generate_content


def generate_architecture(
    project_plan,
    provider="auto",
    max_tokens=1800
):

    try:

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

            print(
                "\nArchitect Response Preview:"
            )

            print(
                clean_text[:500]
            )

            with open(
                "architect_failed_response.txt",
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    clean_text
                )

            raise HTTPException(
                status_code=500,
                detail=(
                    "Architect returned invalid JSON."
                )
            )

        if not isinstance(
            data,
            dict
        ):

            raise HTTPException(
                status_code=500,
                detail=(
                    "Architect response is not a JSON object."
                )
            )

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

        required_fields = [
            "api_endpoints",
            "database_schema",
            "folder_structure",
            "frontend_structure"
        ]

        missing = [
            field
            for field in required_fields
            if field not in data
        ]

        if missing:

            raise HTTPException(
                status_code=500,
                detail=(
                    f"Architect missing fields: {missing}"
                )
            )

        validated = ArchitecturePlan(
            **data
        )

        print(
            "=== END ARCHITECT ==="
        )

        return validated.model_dump()

    except HTTPException:

        raise

    except Exception as e:

        print(
            "ARCHITECT ERROR:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"ForgeAI architecture generation failed: {str(e)}"
            )
        )