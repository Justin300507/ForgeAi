"""Exp118: deferred visual review remains N/A until it actually runs."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.context import GenerationContext, StageStatus, VerificationResult
from app.scoring.engine import ScoringEngine
from app.verification.engine import VerificationEngine


def _ctx():
    return GenerationContext("job", "idea", Path("."), "test_app")


def test_deferred_visual_judge_is_not_scored_as_perfect():
    visual = ScoringEngine().score(_ctx()).dim("Visual Judge")
    assert visual is not None
    assert visual.na
    assert visual.details == "visual review deferred"


def test_completed_clean_visual_judge_still_scores_normally():
    ctx = _ctx()
    ctx.llm_judge_evaluated = True
    visual = ScoringEngine().score(ctx).dim("Visual Judge")
    assert visual is not None
    assert not visual.na
    assert visual.score == 100


def test_engine_supports_a_per_run_visual_judge_override():
    engine = VerificationEngine(run_llm_judge=True)
    assert engine.run_llm_judge is True
    assert "run_llm_judge" in engine.run.__code__.co_varnames


def test_deployment_gate_stays_safe_when_visual_review_is_deferred():
    ctx = _ctx()
    ctx.static_results = [
        VerificationResult(stage="runtime", status=StageStatus.PASSED),
        VerificationResult(stage="frontend_build", status=StageStatus.PASSED),
    ]

    class _Score:
        overall = 100.0

    ctx.record_score(_Score())
    assert ctx.is_deployment_ready
    assert not ctx.llm_judge_evaluated


if __name__ == "__main__":
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL: {test.__name__}")
            traceback.print_exc()
    raise SystemExit(1 if failed else 0)
