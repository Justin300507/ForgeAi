import json
import time

from app.prompts.architect_prompt import build_architect_prompt
from app.models.architecture_models import ArchitecturePlan
from app.providers.ai_provider import generate_content


def generate_architecture(project_plan):

    print("\n=== START ARCHITECT ===")

    start = time.time()

    prompt = build_architect_prompt(project_plan)

    text = generate_content(prompt)

    print(f"Architect Response Length: {len(text)}")
    print(f"Architect Time: {time.time() - start:.2f}s")

    clean_text = text
    clean_text = clean_text.replace("```json", "")
    clean_text = clean_text.replace("```", "")
    clean_text = clean_text.strip()

    data = json.loads(clean_text)

    validated = ArchitecturePlan(**data)

    print("=== END ARCHITECT ===")

    return validated.model_dump()