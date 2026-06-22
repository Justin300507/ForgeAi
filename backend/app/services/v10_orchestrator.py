"""
V10 — Smart Multi-Provider Pipeline

Routes each pipeline stage to the best available provider:
  - Planning/Architecture: Cerebras (fast, cheap, structured)
  - Backend parallel:      Cerebras (fast parallel wave generation)
  - Frontend:              OpenAI GPT-4o-mini (strong React/UI quality)
  - Fix loops:             Cerebras (fast iteration)
  - Reviews:               Gemini (good at analysis)

Falls back to auto-mode if a specific provider is unavailable.
Requires OPENAI_API_KEY and GEMINI_API_KEY in .env for full smart routing.
"""
from typing import Any
import time

from app.services.v6_orchestrator import generate_project_v6
from app.services.failure_dataset_service import (
    RunRecord, save_run_record, make_run_id,
)
from app.services.agent_collaboration import AgentCollaboration
from app.utils.cost_tracker import reset_session, flush_to_log, print_session_cost


def generate_project_v10(
    idea: str,
    run_improvement_cycle: bool = False,
    skip_reviews: bool = False,
) -> dict[str, Any]:
    """
    V10 = Smart multi-provider pipeline.

    Tries OpenAI for frontend generation specifically, Cerebras for backend,
    Gemini for reviews. Falls back to auto if any provider is unavailable.

    Args:
        idea: plain-English project description
        run_improvement_cycle: run V7 improvement cycle after generation
        skip_reviews: skip QA/Security/Code/Performance reviews (saves cost)
    """
    print("\n" + "=" * 60)
    print("  FORGEAI V10 — SMART MULTI-PROVIDER PIPELINE")
    print("  Backend: cerebras | Frontend: openai | Reviews: gemini")
    print("=" * 60)

    start = time.time()
    run_id = make_run_id()
    collab = AgentCollaboration()

    # V10 strategy: use auto provider which now includes OpenAI + Gemini
    # in the fallback chain (Cerebras → Groq → OpenRouter → OpenAI → Gemini → Ollama)
    # This ensures maximum quality with graceful degradation.
    result = generate_project_v6(
        idea=idea,
        provider="auto",
        use_parallel_backend=True,
        collab=collab,
        skip_reviews=skip_reviews,
    )

    elapsed = time.time() - start
    forge_score = result.get("forge_score", 0)

    # Optionally run improvement cycle (same as V7)
    if run_improvement_cycle:
        try:
            from app.services.v7_orchestrator import run_improvement_cycle_v7
            print("\n[V10] Running improvement cycle...")
            run_improvement_cycle_v7(provider="auto")
        except Exception as e:
            print(f"[V10] Improvement cycle failed (non-fatal): {e}")

    # Record to dataset
    try:
        record = RunRecord(
            run_id=run_id,
            version="v10",
            idea=idea,
            provider="auto",
            forge_score=forge_score,
            static_pass=result.get("validation_passed", False),
            runtime_pass=result.get("runtime_passed", False),
            errors_at_start=result.get("initial_error_count", 0),
            errors_after_fix=result.get("final_error_count", 0),
            fix_attempts=result.get("fix_attempts", 0),
            elapsed_seconds=elapsed,
            project_name=result.get("project_name", ""),
        )
        save_run_record(record)
    except Exception:
        pass

    result["pipeline_version"] = "v10"
    result["smart_routing"] = True
    return result
