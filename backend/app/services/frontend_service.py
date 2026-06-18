import json

from app.prompts.frontend_prompt import build_frontend_prompt
from app.models.frontend_models import FrontendPlan
from app.providers.ai_provider import generate_content
import time


def generate_frontend(
    architecture,
    provider="auto",
    max_tokens=1500
):

    prompt = build_frontend_prompt(architecture)
    start = time.time()
    text = generate_content(
    prompt,
    provider
)
    print(f"Frontend Time: {time.time() - start:.2f}s")

    # Save raw AI response
    with open("frontend_response.txt", "w", encoding="utf-8") as f:
        f.write(text)

    clean_text = text
    clean_text = clean_text.replace("```json", "")
    clean_text = clean_text.replace("```", "")
    clean_text = clean_text.strip()
    clean_text = clean_text.replace("\t", " ")

    print(f"Frontend Response Length: {len(clean_text)}")

    try:
        data = json.loads(clean_text)

    except json.JSONDecodeError as e:

        print("\n========== FRONTEND JSON ERROR ==========")
        print(f"Error: {e}")
        print(f"Line: {e.lineno}")
        print(f"Column: {e.colno}")
        print(f"Position: {e.pos}")

        start = max(0, e.pos - 300)
        end = min(len(clean_text), e.pos + 300)

        print("\n========== ERROR CONTEXT ==========")
        print(clean_text[start:end])
        print("\n===================================\n")

        raise

    validated = FrontendPlan(**data)

    return validated.model_dump()