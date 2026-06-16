import json
import time

from fastapi import HTTPException

from app.services.gemini_service import client
from app.prompts.planner_prompt import build_planner_prompt
from app.models.project_models import ProjectPlan


def generate_plan(idea):

    prompt = build_planner_prompt(idea)

    try:

        for attempt in range(3):

            try:

                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )

                break

            except Exception:

                if attempt == 2:
                    raise

                time.sleep(2)

        clean_text = response.text
        clean_text = clean_text.replace("```json", "")
        clean_text = clean_text.replace("```", "")
        clean_text = clean_text.strip()

        data = json.loads(clean_text)

        validated = ProjectPlan(**data)

        return validated.model_dump()

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"ForgeAI generation failed: {str(e)}"
        )