"""
Experiment 082 (Live Validation of Regenerate Module Reactivation):
end-to-end proof that RetryManager.next_strategy() actually SELECTS
REGENERATE_MODULE at attempt 3 for the 'contract' pattern now that
Exp081's generation-tag migration has run -- not just that
strategy_memory.should_skip() returns False in isolation (already covered
by test_exp081_strategy_memory_versioning.py).

Why this test exists: the live canary in Exp082 (blog_cms, cerebras)
resolved on attempt 1/5 via patch_file (score 79.7 -> 91.0, deploy-ready)
and never escalated far enough to reach attempt 3, so the live run itself
could not observe RetryManager actually choosing REGENERATE_MODULE.
Escalating that far depends on the LLM's own output happening to need a
second and third repair round -- non-deterministic, not something worth
gambling more Cerebras spend on. This test proves the SAME code path
deterministically offline: it drives RetryManager through a simulated
"patch_file doesn't improve the score twice" sequence for a 'contract'
diagnostic and asserts attempt 3 is REGENERATE_MODULE, not a skip
straight to REGENERATE_ARCH.

Run directly: python tests/reliability/test_exp082_retrymanager_reactivation.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.retry.strategy_memory as sm
from app.retry.manager import RetryManager
from app.core.context import (
    Diagnostic, ErrorCategory, ErrorSeverity, FixStrategy,
    GenerationContext, StageStatus, VerificationResult,
)


def _isolated_store(tmpdir: str) -> Path:
    path = Path(tmpdir) / "strategy_outcomes.json"
    sm._STORE_PATH = path
    return path


def _write_raw(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _contract_ctx() -> GenerationContext:
    ctx = GenerationContext(job_id="t", idea="t", project_path=None, project_name="t")
    ctx.static_results = [
        VerificationResult(
            stage="contract_conformance",
            status=StageStatus.FAILED,
            diagnostics=[
                Diagnostic(
                    error_id="e1",
                    category=ErrorCategory.CONTRACT,
                    severity=ErrorSeverity.HIGH,
                    source="static",
                    message="Contract violation: schema mismatch: PostCreate.title required",
                ),
            ],
        ),
    ]
    return ctx


def test_regenerate_module_selected_at_attempt_3_after_migration():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        # Exact pre-migration production shape (Exp080's own evidence).
        _write_raw(path, {
            "contract": {
                "patch_file": {"successes": 50, "tries": 126},
                "regenerate_arch": {"successes": 9, "tries": 42},
                "regenerate_module": {"successes": 0, "tries": 3},
                "switch_model": {"successes": 0, "tries": 3},
            },
        })

        rm = RetryManager(max_attempts=5)
        ctx = _contract_ctx()

        cfg1 = rm.next_strategy(ctx)
        assert cfg1.strategy == FixStrategy.PATCH_FILE
        rm.record_result(cfg1, score_before=70.0, score_after=70.0)  # no improvement

        cfg2 = rm.next_strategy(ctx)
        assert cfg2.strategy == FixStrategy.PATCH_FILE
        rm.record_result(cfg2, score_before=70.0, score_after=70.0)  # no improvement

        cfg3 = rm.next_strategy(ctx)
        assert cfg3.strategy == FixStrategy.REGENERATE_MODULE, (
            f"expected REGENERATE_MODULE at attempt 3 post-migration, got {cfg3.strategy} "
            "-- this is the exact selection Exp079 found permanently skipped"
        )


def test_regenerate_arch_still_selected_pre_migration_shape_without_generation_fix():
    # Negative control: confirms the OLD (pre-Exp081) behavior really was
    # what Exp079/080 found -- i.e. this test's positive counterpart above
    # is meaningfully different, not a tautology. Simulated by writing an
    # entry already stamped with the CURRENT generation but still 0/3 --
    # a strategy that has genuinely, currently proven ineffective must
    # still be skipped; the fix is about STALE evidence, not about making
    # regenerate_module immune to ever being skipped again.
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "contract": {
                "patch_file": {"successes": 50, "tries": 126, "generation": 1},
                "regenerate_arch": {"successes": 9, "tries": 42},
                "regenerate_module": {"successes": 0, "tries": 3, "generation": 2},
                "switch_model": {"successes": 0, "tries": 3},
            },
        })

        rm = RetryManager(max_attempts=5)
        ctx = _contract_ctx()

        cfg1 = rm.next_strategy(ctx)
        rm.record_result(cfg1, score_before=70.0, score_after=70.0)
        cfg2 = rm.next_strategy(ctx)
        rm.record_result(cfg2, score_before=70.0, score_after=70.0)
        cfg3 = rm.next_strategy(ctx)

        assert cfg3.strategy != FixStrategy.REGENERATE_MODULE, (
            "an entry already on the CURRENT generation with a genuine 0/3 "
            "record must still be skipped -- staleness invalidation must not "
            "make a strategy permanently immune to ever being blacklisted again"
        )


def test_full_migration_plus_retrymanager_end_to_end_matches_live_run():
    # Reproduces exactly what happened live in Exp082: real pre-migration
    # snapshot -> RetryManager runs -> regenerate_module becomes eligible.
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "AttributeError": {
                "patch_file": {"successes": 0, "tries": 2},
                "regenerate_module": {"successes": 0, "tries": 3},
                "switch_model": {"successes": 0, "tries": 1},
            },
            "contract": {
                "patch_file": {"successes": 50, "tries": 126},
                "regenerate_arch": {"successes": 9, "tries": 42},
                "regenerate_module": {"successes": 0, "tries": 3},
                "switch_model": {"successes": 0, "tries": 3},
            },
        })

        # Migration fires on the very first strategy_memory call, exactly
        # as it did the moment Exp082's live canary made its first
        # dominant_pattern()/should_skip() call.
        assert sm.should_skip("contract", "regenerate_module") is False
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["contract"]["regenerate_module"] == {
            "successes": 0, "tries": 0, "generation": 2,
        }
        assert on_disk["AttributeError"]["regenerate_module"] == {
            "successes": 0, "tries": 0, "generation": 2,
        }
        # Untouched patterns/strategies stay exactly as before.
        assert on_disk["contract"]["patch_file"] == {"successes": 50, "tries": 126}

        rm = RetryManager(max_attempts=5)
        ctx = _contract_ctx()
        cfg1 = rm.next_strategy(ctx)
        rm.record_result(cfg1, 70.0, 70.0)
        cfg2 = rm.next_strategy(ctx)
        rm.record_result(cfg2, 70.0, 70.0)
        cfg3 = rm.next_strategy(ctx)
        assert cfg3.strategy == FixStrategy.REGENERATE_MODULE


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
