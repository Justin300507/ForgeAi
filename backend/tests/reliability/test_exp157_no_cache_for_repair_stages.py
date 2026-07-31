"""
Exp157 (habit_tracker, 2026-07-31): the global LLM response cache in
ai_provider.py caches every generate_content() call by prompt hash,
unconditionally -- its own rationale ("identical prompt == identical
inputs, so serving the cached response is safe") holds for the initial
generation stages (planner/architect/backend/frontend: same idea should
produce the same output) but not for the repair loop. A "fix"/
"architecture_fix"/"runtime_fix" prompt is sent specifically BECAUSE a
previous attempt failed; the retry/escalation strategies exist on the
premise a later attempt gets a genuinely fresh shot.

Confirmed live: a "Frontend/browser failure" fix produced a broken
useAuth.jsx/AuthContext.jsx pair, got cached unconditionally before the
regression it caused was even detected, and replayed via cache HIT on a
later attempt -- even after Exp156's FixCache eviction correctly
cleared the SEPARATE, higher-level diagnostic-hash cache for the exact
same group. Two independent caching layers; this one had no eviction
mechanism at all (and none is added here -- it's just exempted).

Run directly: python tests/reliability/test_exp157_no_cache_for_repair_stages.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.providers import ai_provider


def _isolated_llm_cache_dir(tmpdir: str):
    import app.utils.llm_cache as llm_cache
    llm_cache.CACHE_DIR = tmpdir


def test_fix_stage_never_hits_cache_even_for_identical_prompts():
    with tempfile.TemporaryDirectory() as td:
        _isolated_llm_cache_dir(td)
        calls: list[str] = []

        def fake_uncached(prompt, provider, max_tokens, stage, thinking_budget):
            calls.append(stage)
            return f"response-{len(calls)}"

        with patch.object(ai_provider, "_generate_uncached", fake_uncached):
            r1 = ai_provider.generate_content("identical prompt", stage="fix")
            r2 = ai_provider.generate_content("identical prompt", stage="fix")

        assert len(calls) == 2, "a fix-stage call must never be served from cache"
        assert r1 != r2


def test_architecture_fix_and_runtime_fix_stages_also_exempted():
    with tempfile.TemporaryDirectory() as td:
        _isolated_llm_cache_dir(td)
        for stage in ("architecture_fix", "runtime_fix"):
            calls: list[str] = []

            def fake_uncached(prompt, provider, max_tokens, stage, thinking_budget):
                calls.append(stage)
                return f"response-{len(calls)}"

            with patch.object(ai_provider, "_generate_uncached", fake_uncached):
                r1 = ai_provider.generate_content("same prompt", stage=stage)
                r2 = ai_provider.generate_content("same prompt", stage=stage)

            assert len(calls) == 2, f"{stage} must never be served from cache"
            assert r1 != r2


def test_initial_generation_stages_still_cache_as_before():
    """The exemption must be scoped to repair stages only -- the whole
    point of this cache (free re-runs of the same idea) must keep
    working for planner/architect/backend/frontend/missing_file."""
    with tempfile.TemporaryDirectory() as td:
        _isolated_llm_cache_dir(td)
        for stage in ("backend_generation", "frontend_generation", "missing_file", "product_manager"):
            calls: list[str] = []

            def fake_uncached(prompt, provider, max_tokens, stage, thinking_budget):
                calls.append(stage)
                return f"response-{len(calls)}"

            with patch.object(ai_provider, "_generate_uncached", fake_uncached):
                r1 = ai_provider.generate_content(f"prompt for {stage}", stage=stage)
                r2 = ai_provider.generate_content(f"prompt for {stage}", stage=stage)

            assert len(calls) == 1, f"{stage} should still be served from cache on repeat"
            assert r1 == r2


def test_forge_llm_cache_env_var_still_disables_everything():
    with tempfile.TemporaryDirectory() as td:
        _isolated_llm_cache_dir(td)
        calls: list[str] = []

        def fake_uncached(prompt, provider, max_tokens, stage, thinking_budget):
            calls.append(stage)
            return f"response-{len(calls)}"

        with patch.dict(os.environ, {"FORGE_LLM_CACHE": "0"}):
            with patch.object(ai_provider, "_generate_uncached", fake_uncached):
                ai_provider.generate_content("prompt", stage="backend_generation")
                ai_provider.generate_content("prompt", stage="backend_generation")

        assert len(calls) == 2


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
