"""
V9 — OpenAI ChatGPT Pipeline

V7 pipeline forced to use OpenAI GPT-4o-mini (with GPT-4o fallback) for all stages.
Requires OPENAI_API_KEY in .env.
"""
from typing import Any

from app.services.v7_orchestrator import generate_project_v7


def generate_project_v9(
    idea: str,
    run_improvement_cycle: bool = False,
    skip_reviews: bool = True,
) -> dict[str, Any]:
    """
    V9 = V7 pipeline running entirely on OpenAI GPT-4o-mini.

    Args:
        idea: plain-English project description
        run_improvement_cycle: whether to run rule evolution (default off for speed)
        skip_reviews: skip QA/Security/Code/Performance reviews (default True to save cost)
    """
    print("\n" + "=" * 60)
    print("  FORGEAI V9 — OPENAI CHATGPT PIPELINE")
    print("  Provider: openai (GPT-4o-mini → GPT-4o fallback)")
    print("=" * 60)

    return generate_project_v7(
        idea=idea,
        provider="openai",
        run_improvement_cycle=run_improvement_cycle,
        skip_reviews=skip_reviews,
    )
