import json
import time

from fastapi import HTTPException

from app.prompts.planner_prompt import build_planner_prompt
from app.models.project_models import ProjectPlan
from app.providers.ai_provider import generate_content


def generate_plan(
    idea,
    provider="auto",
    max_tokens=1500
):
    try:

        print("\n=== START PLANNER ===")

        start = time.time()

        prompt = build_planner_prompt(
            idea
        )

        text = generate_content(
            prompt,
            provider
        )

        print(
            f"Planner Response Length: {len(text)}"
        )

        # Save raw planner output for debugging
        try:
            with open(
                "planner_response.txt",
                "w",
                encoding="utf-8"
            ) as f:
                f.write(text)
        except Exception as save_error:
            print(
                f"Failed to save planner response: {save_error}"
            )

        print(
            f"Planner Time: {time.time() - start:.2f}s"
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

        data = json.loads(
            clean_text
        )

        # Detect LLM error responses

        if isinstance(data, dict) and "error" in data:

            raise Exception(
                f"Planner returned error object: {data['error']}"
            )

        # Required schema validation

        required_fields = [
            "project_name",
            "description",
            "target_users",
            "core_features",
            "future_features",
            "tech_stack",
            "database_entities",
            "api_modules",
            "pages",
            "backend_modules",
            "roadmap"
        ]

        missing = [
            field
            for field in required_fields
            if field not in data
        ]

        if missing:

            raise Exception(
                f"Planner missing fields: {missing}"
            )

        validated = ProjectPlan(
            **data
        )

        print(
            "=== END PLANNER ==="
        )

        return validated.model_dump()

    except json.JSONDecodeError as e:

        print(
            "PLANNER JSON ERROR:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Planner returned invalid JSON."
            )
        )

    except Exception as e:

        print(
            "PLANNER ERROR:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"ForgeAI generation failed: {str(e)}"
            )
        )