"""
Exp115: the visual-judge critical hard-block fired on an app whose every
runtime stage passed.

Confirmed live (exp114-milestone-r5, forge_blog_cms): journey 11/11,
runtime startup PASSED, frontend build PASSED, no blank page, no console
errors — and a CACHED judge verdict ("failing to load any initial data",
served as an LLM-cache HIT from an earlier, broken round's byte-identical
prompt) still blocked the deploy of an 87.6/B app. The gate now requires
runtime corroboration (runtime/frontend_build/browser stage failure, or a
blank page / console errors) before a judge-critical verdict blocks.

Run directly: python tests/reliability/test_exp115_judge_gate_contradiction.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.context import GenerationContext
from app.core.context import StageStatus, VerificationResult, ErrorSeverity


def _ctx(judge_critical=True, failed_stage=None, score=88.0):
    ctx = GenerationContext("job", "idea", Path("."), "test_app")
    if judge_critical:
        ctx.llm_judge_severity = ErrorSeverity.CRITICAL
        ctx.llm_judge_confidence = 0.95
    for stage in ("runtime", "frontend_build", "browser"):
        ctx.static_results.append(VerificationResult(
            stage=stage,
            status=StageStatus.FAILED if stage == failed_stage else StageStatus.PASSED,
        ))

    class _Score:
        overall = score
    ctx.record_score(_Score())
    return ctx


def _latest_ok(ctx):
    # keep the numeric threshold out of the way for these tests
    ctx.DEPLOY_THRESHOLD = 80.0
    return ctx


def test_uncorroborated_judge_critical_does_not_block():
    ctx = _latest_ok(_ctx(judge_critical=True))
    reason = ctx.deployment_block_reason
    assert reason is None or "visual judge" not in str(reason), reason


def test_judge_critical_with_failed_runtime_still_blocks():
    ctx = _latest_ok(_ctx(judge_critical=True, failed_stage="runtime"))
    reason = ctx.deployment_block_reason
    assert reason is not None
    # either the judge block or the critical-stage block fires — both are
    # legitimate blocks when runtime really failed
    assert "visual judge" in reason or "critical stage" in reason


def test_judge_critical_with_browser_console_errors_blocks():
    ctx = _latest_ok(_ctx(judge_critical=True))

    class _BR:
        skipped = False
        blank_page = False
        page_loaded = True
        console_errors = ["TypeError: x is undefined"]
        diagnostics = []
    ctx.browser_result = _BR()
    reason = ctx.deployment_block_reason
    assert reason is not None and "visual judge" in reason


def test_no_judge_verdict_unchanged():
    ctx = _latest_ok(_ctx(judge_critical=False))
    assert ctx.deployment_block_reason is None


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
