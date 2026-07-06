import json
import time

from fastapi import HTTPException

from app.prompts.planner_prompt import build_planner_prompt
from app.models.project_models import ProjectPlan
from app.providers.ai_provider import generate_content
from app.utils.json_cleaner import extract_json
from app.utils.llm_cache import get_cached, set_cached


def generate_plan(
    idea,
    provider="auto",
    max_tokens=4000
):
    try:

        cached = get_cached("planner", {"idea": idea})

        if cached:
            print("\n=== PLANNER CACHE HIT — skipping LLM call ===")
            return cached

        print("\n=== START PLANNER ===")

        start = time.time()

        prompt = build_planner_prompt(idea)

        text = generate_content(
            prompt,
            provider,
            max_tokens=max_tokens,
            stage="planning",
        )

        if not text:
            raise HTTPException(
                status_code=500,
                detail="Planner LLM returned empty response (token limit likely exceeded)."
            )

        print(f"Planner Response Length: {len(text)}")

        try:
            with open(
                "planner_response.txt", "w", encoding="utf-8"
            ) as f:
                f.write(text)
        except Exception as save_error:
            print(f"Failed to save planner response: {save_error}")

        print(f"Planner Time: {time.time() - start:.2f}s")

        data = extract_json(text)

        if isinstance(data, dict) and "error" in data:
            raise Exception(f"Planner returned error object: {data['error']}")

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
            field for field in required_fields if field not in data
        ]

        if missing:
            raise Exception(f"Planner missing fields: {missing}")

        validated = ProjectPlan(**data)
        result = validated.model_dump()

        set_cached("planner", {"idea": idea}, result)

        print("=== END PLANNER ===")

        return result

    except json.JSONDecodeError as e:

        print("PLANNER JSON ERROR:", e)

        raise HTTPException(
            status_code=500,
            detail="Planner returned invalid JSON."
        )

    except Exception as e:

        print("PLANNER ERROR:", e)

        raise HTTPException(
            status_code=500,
            detail=f"ForgeAI generation failed: {str(e)}"
        )