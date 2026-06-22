"""
V12 Evolution Service

Analyzes live deployment metrics with an LLM and generates an evolution plan:
  - What's slow
  - What's erroring
  - What to improve in the next generation
"""
import json
from typing import Any

from app.providers.ai_provider import generate_content
from app.prompts.evolution_prompt import build_evolution_prompt
from app.prompts.shared_contract import FASTAPI_CONTRACT


def generate_evolution_plan(
    idea: str,
    metrics: dict,
    architecture: dict,
    provider: str = "auto",
) -> dict[str, Any]:
    """
    Use an LLM to analyze live metrics and produce an improvement plan.

    Returns an evolution plan dict or a fallback dict if LLM fails.
    """
    print("  [V12] Analyzing live metrics with LLM...")

    if not metrics.get("issues") and metrics.get("healthy"):
        print("  [V12] Metrics look healthy — no improvement needed")
        return {
            "needs_improvement": False,
            "priority": "low",
            "improvements": [],
            "regeneration_context": "",
            "summary": "Application is performing well — no changes needed.",
        }

    prompt = build_evolution_prompt(
        idea=idea,
        metrics=metrics,
        architecture=architecture,
        shared_contract=FASTAPI_CONTRACT,
    )

    try:
        raw = generate_content(prompt, provider=provider, stage="evolution")
        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.rsplit("```", 1)[0].strip()

        plan = json.loads(cleaned)
        print(f"  [V12] Evolution plan: {plan.get('summary', '')[:100]}")
        return plan

    except Exception as exc:
        print(f"  [V12] Evolution LLM failed (non-fatal): {exc}")
        return {
            "needs_improvement": bool(metrics.get("issues")),
            "priority": "medium",
            "improvements": [],
            "regeneration_context": "",
            "summary": f"Metrics analysis failed: {exc}",
            "error": str(exc),
        }


def build_evolved_idea(idea: str, plan: dict) -> str:
    """Augment the original idea with evolution context for re-generation."""
    if not plan.get("needs_improvement") or not plan.get("regeneration_context"):
        return idea
    return (
        f"{idea}\n\n"
        f"[EVOLUTION CONTEXT — apply these improvements in the generated code:]\n"
        f"{plan['regeneration_context']}"
    )
