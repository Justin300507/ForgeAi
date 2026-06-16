import json

from app.services.gemini_service import client
from app.prompts.architect_prompt import build_architect_prompt
from app.models.architecture_models import ArchitecturePlan


def generate_architecture(project_plan):

    prompt = build_architect_prompt(project_plan)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    clean_text = response.text
    clean_text = clean_text.replace("```json", "")
    clean_text = clean_text.replace("```", "")
    clean_text = clean_text.strip()

    data = json.loads(clean_text)

    validated = ArchitecturePlan(**data)

    return validated.model_dump()