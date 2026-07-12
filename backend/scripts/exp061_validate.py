"""
Experiment 061: live validation of the Exp060 Diagnostic contract
migration. Validation only -- no fixes implemented, no additional
validators migrated, no production code changed.

Captures per-validator-invocation detail (validator_name, category,
severity, file_path, legacy-adapter-used) and per-repair detail (target
file written) DURING a real generate_project_v15() run, purely by
monkeypatching three functions to observe their calls -- none of their
implementations are touched, so this cannot change generation behavior.
Patches are applied to the module-level bindings actually used at each
call site (e.g. v6_orchestrator.write_fix, not fix_writer_service.write_fix
-- the same "from X import Y creates a local binding" lesson Exp057
found the hard way), and every wrapper calls straight through to the
real function before/after recording, so timing and return values are
unaffected.

Monkeypatched (observe only, call through unchanged):
  - app.services.validator_service.validate_project
      -> captures errors/diagnostics per call (Q1: do migrated
         validators emit correct Diagnostics)
  - app.verification.engine._run_static_validators
      -> captures the FINAL VerificationResult.diagnostics, post
         adapter-boundary -- validator_name set = native (migrated),
         None = legacy regex fallback (Q4, Q6)
  - app.services.v6_orchestrator.write_fix
      -> captures which file each repair attempt actually targeted (Q2, Q3)

Usage:
    python scripts/exp061_validate.py --round 1
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.v15_orchestrator import generate_project_v15
from run_canary import CANARY_APPS, BENCHMARKS_DIR, _acquire_lock, _release_lock

OUT_DIR = _BACKEND_ROOT / "benchmark_results" / "exp061"


def _install_observers():
    import app.services.validator_service as validator_service
    import app.verification.engine as engine
    import app.services.v6_orchestrator as v6_orchestrator

    observed = {
        "validate_project_calls": [],   # [{errors: [...], diagnostics: [{...}], ...}]
        "static_validator_stage_calls": [],  # [{diagnostics: [{validator_name, category, severity, file_path, legacy}], ...}]
        "write_fix_calls": [],          # [{path, has_content}]
    }

    _orig_validate_project = validator_service.validate_project

    def _wrapped_validate_project(project_path):
        t0 = time.perf_counter()
        result = _orig_validate_project(project_path)
        observed["validate_project_calls"].append({
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "errors": list(result.get("errors", [])),
            "diagnostics": [
                {
                    "validator_name": d.validator_name,
                    "category": d.category.value if d.category else None,
                    "severity": d.severity.value if d.severity else None,
                    "file_path": d.file_path,
                    "message": d.message,
                }
                for d in result.get("diagnostics", [])
            ],
        })
        return result

    validator_service.validate_project = _wrapped_validate_project

    _orig_run_static = engine._run_static_validators

    def _wrapped_run_static(ctx):
        result = _orig_run_static(ctx)
        observed["static_validator_stage_calls"].append({
            "status": result.status.value,
            "diagnostics": [
                {
                    "validator_name": d.validator_name,
                    "legacy_adapter_used": d.validator_name is None,
                    "category": d.category.value if d.category else None,
                    "severity": d.severity.value if d.severity else None,
                    "file_path": d.file_path,
                    "message": d.message[:200],
                }
                for d in result.diagnostics
            ],
        })
        return result

    engine._run_static_validators = _wrapped_run_static

    _orig_write_fix = v6_orchestrator.write_fix

    def _wrapped_write_fix(project_path, fix):
        result = _orig_write_fix(project_path, fix)
        observed["write_fix_calls"].append({
            "path": (fix or {}).get("path"),
            "has_content": bool((fix or {}).get("content")),
            "write_succeeded": result,
        })
        return result

    v6_orchestrator.write_fix = _wrapped_write_fix

    def _restore():
        validator_service.validate_project = _orig_validate_project
        engine._run_static_validators = _orig_run_static
        v6_orchestrator.write_fix = _orig_write_fix

    return observed, _restore


def run_round(round_num: int, provider: str = "auto", deploy: bool = False) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _acquire_lock(f"exp061-round{round_num}")
    try:
        idea_map = dict(CANARY_APPS)
        idea = (BENCHMARKS_DIR / idea_map["todo"]).read_text(encoding="utf-8").strip()

        observed, restore = _install_observers()
        print(f"\n{'='*70}\n  EXP061 round {round_num}: todo (observed)\n{'='*70}")
        buf = io.StringIO()
        t0 = time.time()
        crashed = False
        error = None
        result: dict = {}
        try:
            with contextlib.redirect_stdout(buf):
                result = generate_project_v15(idea=idea, provider=provider, deploy=deploy)
        except Exception as exc:
            crashed = True
            error = f"{type(exc).__name__}: {exc}"
        finally:
            restore()
        elapsed = round(time.time() - t0, 1)
        log_text = buf.getvalue()
        sys.stdout.write(log_text[-4000:])

        (OUT_DIR / f"round{round_num}_todo.log").write_text(log_text, encoding="utf-8")
        record = {
            "round": round_num,
            "app": "todo",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "crashed": crashed,
            "error": error,
            "elapsed_s": elapsed,
            "result": result,
            "observed": observed,
        }
        (OUT_DIR / f"round{round_num}_todo.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        n_migrated = sum(
            1 for call in observed["static_validator_stage_calls"]
            for d in call["diagnostics"] if not d["legacy_adapter_used"]
        )
        n_legacy = sum(
            1 for call in observed["static_validator_stage_calls"]
            for d in call["diagnostics"] if d["legacy_adapter_used"]
        )
        print(f"[exp061] round {round_num}: crashed={crashed} score={result.get('forge_score')} "
              f"fix_attempts={result.get('fix_attempts')} elapsed={elapsed}s "
              f"native_diagnostics={n_migrated} legacy_fallback_diagnostics={n_legacy} "
              f"write_fix_calls={len(observed['write_fix_calls'])}")
        return record
    finally:
        _release_lock()


def main():
    parser = argparse.ArgumentParser(description="Exp061 live validator-contract validation")
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args()
    run_round(args.round, provider=args.provider, deploy=args.deploy)


if __name__ == "__main__":
    main()
