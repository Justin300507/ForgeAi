import json
import time

from fastapi import HTTPException

from app.prompts.planner_prompt import build_planner_prompt
from app.models.project_models import ProjectPlan
from app.providers.ai_provider import generate_content

def generate_plan(
    idea,
    provider="auto"
):
    try:

        print("\n=== START PLANNER ===")

        start = time.time()

        prompt = build_planner_prompt(idea)

        text = generate_content(
    prompt,
    provider
)

        print(f"Planner Response Length: {len(text)}")
        print(f"Planner Time: {time.time() - start:.2f}s")

        clean_text = text
        clean_text = clean_text.replace("```json", "")
        clean_text = clean_text.replace("```", "")
        clean_text = clean_text.strip()

        data = json.loads(clean_text)

        validated = ProjectPlan(**data)

        print("=== END PLANNER ===")

        return validated.model_dump()

    except Exception as e:

        print("PLANNER ERROR:", e)

        raise HTTPException(
            status_code=500,
            detail=f"ForgeAI generation failed: {str(e)}"
        )