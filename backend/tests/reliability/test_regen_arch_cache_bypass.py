"""
Experiment 048: architecture regeneration (the "nuclear option" fix strategy,
fires only on the last repair attempt) must bypass the LLM response cache.

Root cause found via the 2026-07-11 canary: todo regressed (build_ok True ->
False) surviving a full REGENERATE_ARCH pass. The regen call reused the
cached V6 generation -- the exact pre-patch content, JSX bug included --
and unconditionally overwrote the on-disk files that an earlier fix round
had already patched, silently discarding that fix with zero attempts left
to redo it. This is generic to the regen strategy, not todo-specific: any
app that reaches attempt 5 is at risk.

Run directly: python tests/reliability/test_regen_arch_cache_bypass.py
"""
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.orchestrator import _regenerate_architecture
from app.retry.manager import StrategyConfig, FixStrategy


class _FakeCtx:
    def __init__(self, project_path):
        self.idea = "A todo list app with user accounts"
        self.project_path = project_path


def _fake_cfg():
    return StrategyConfig(
        attempt=5, strategy=FixStrategy.REGENERATE_ARCH, provider="auto",
        model_hint="strongest", regen_arch=True,
    )


def test_bypasses_cache_during_regen_call():
    seen_cache_env = {}

    def fake_generate_project_v6(idea, provider="auto"):
        seen_cache_env["during_call"] = os.environ.get("FORGE_LLM_CACHE")
        return {"project_path": None}

    with mock.patch.dict(os.environ, {"FORGE_LLM_CACHE": "1"}, clear=False):
        with mock.patch(
            "app.services.v6_orchestrator.generate_project_v6",
            side_effect=fake_generate_project_v6,
        ):
            _regenerate_architecture(_FakeCtx(Path(".")), _fake_cfg())
        assert seen_cache_env["during_call"] == "0", (
            "regen must disable FORGE_LLM_CACHE for its generate_project_v6 call"
        )
        assert os.environ.get("FORGE_LLM_CACHE") == "1", (
            "cache setting must be restored to its prior value after regen"
        )


def test_restores_cache_env_even_on_exception():
    def raising_generate_project_v6(idea, provider="auto"):
        raise RuntimeError("simulated provider failure")

    with mock.patch.dict(os.environ, {"FORGE_LLM_CACHE": "1"}, clear=False):
        with mock.patch(
            "app.services.v6_orchestrator.generate_project_v6",
            side_effect=raising_generate_project_v6,
        ):
            result = _regenerate_architecture(_FakeCtx(Path(".")), _fake_cfg())
        assert result == []  # caught internally, returns empty modified list
        assert os.environ.get("FORGE_LLM_CACHE") == "1"


def test_restores_unset_cache_env():
    # FORGE_LLM_CACHE not set at all beforehand -> must end up unset again,
    # not left behind as "0".
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("FORGE_LLM_CACHE", None)
        with mock.patch(
            "app.services.v6_orchestrator.generate_project_v6",
            return_value={"project_path": None},
        ):
            _regenerate_architecture(_FakeCtx(Path(".")), _fake_cfg())
        assert "FORGE_LLM_CACHE" not in os.environ


def test_restores_arbitrary_prior_cache_env_value():
    # The prior value might not be "0"/"1" at all (e.g. a stray "true" a
    # human set by hand) -- the restore must be exact-value, not just
    # "flip back to 1".
    with mock.patch.dict(os.environ, {"FORGE_LLM_CACHE": "banana"}, clear=False):
        with mock.patch(
            "app.services.v6_orchestrator.generate_project_v6",
            return_value={"project_path": None},
        ):
            _regenerate_architecture(_FakeCtx(Path(".")), _fake_cfg())
        assert os.environ.get("FORGE_LLM_CACHE") == "banana"


def test_idempotent_across_repeated_calls_no_state_leakage():
    # Exp052 Priority 4: calling the regen path twice in a row (as the
    # live retry loop could, on two separate attempt-5 escalations across
    # different generations sharing a process) must not leak env state
    # from the first call into the second -- each call's bypass-then-restore
    # must be self-contained.
    calls = []

    def fake_generate_project_v6(idea, provider="auto"):
        calls.append(os.environ.get("FORGE_LLM_CACHE"))
        return {"project_path": None}

    with mock.patch.dict(os.environ, {"FORGE_LLM_CACHE": "1"}, clear=False):
        with mock.patch(
            "app.services.v6_orchestrator.generate_project_v6",
            side_effect=fake_generate_project_v6,
        ):
            _regenerate_architecture(_FakeCtx(Path(".")), _fake_cfg())
            assert os.environ.get("FORGE_LLM_CACHE") == "1"
            _regenerate_architecture(_FakeCtx(Path(".")), _fake_cfg())
            assert os.environ.get("FORGE_LLM_CACHE") == "1"

    assert calls == ["0", "0"], "both calls must independently bypass the cache"


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
