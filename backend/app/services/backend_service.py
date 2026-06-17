import json
import time

from app.prompts.backend_prompt import build_backend_prompt
from app.models.backend_models import BackendPlan
from app.providers.ai_provider import generate_content


def generate_backend(
    architecture,
    provider="auto"
):

    print("\n=== START BACKEND ===")

    start = time.time()

    prompt = build_backend_prompt(architecture)

    text = generate_content(
    prompt,
    provider
)

    print(f"Backend Response Length: {len(text)}")
    print(f"Backend Time: {time.time() - start:.2f}s")

    with open("backend_response.txt", "w", encoding="utf-8") as f:
        f.write(text)

    clean_text = text
    clean_text = clean_text.replace("```json", "")
    clean_text = clean_text.replace("```", "")
    clean_text = clean_text.strip()

    try:
        data = json.loads(clean_text)

    except json.JSONDecodeError as e:

        print("\n=== BACKEND JSON ERROR ===")
        print(e)

        raise

    validated = BackendPlan(**data)

    print("=== END BACKEND ===")

    return validated.model_dump()