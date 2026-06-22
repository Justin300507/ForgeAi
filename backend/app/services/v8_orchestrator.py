"""
V8 — Google Gemini Pipeline

V7 pipeline forced to use Google Gemini-2.5-Flash for all generation stages.
Use when Cerebras credits are exhausted or for Gemini-quality comparison.
"""
from typing import Any

from app.services.v7_orchestrator import generate_project_v7


def generate_project_v8(
    idea: str,
    run_improvement_cycle: bool = False,
    skip_reviews: bool = True,
) -> dict[str, Any]:
    """
    V8 = V7 pipeline running entirely on Google Gemini-2.5-Flash.

    Args:
        idea: plain-English project description
        run_improvement_cycle: whether to run rule evolution (default off for speed)
        skip_reviews: skip QA/Security/Code/Performance reviews (default True to save cost)
    """
    print("\n" + "=" * 60)
    print("  FORGEAI V8 — GOOGLE GEMINI PIPELINE")
    print("  Provider: gemini (Gemini-2.5-Flash)")
    print("=" * 60)

    return generate_project_v7(
        idea=idea,
        provider="gemini",
        run_improvement_cycle=run_improvement_cycle,
        skip_reviews=skip_reviews,
    )
