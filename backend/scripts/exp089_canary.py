"""
Experiment 089: Live Validation of ORM Dictionary Response Repair (Exp088).

Measures whether Exp088's extension to _patch_orm_response_model()
(app/services/deterministic_patcher.py) -- injecting a model_validate()
conversion for nested ORM collections returned under a generic
dict/Dict response_model -- actually activates during a real generation,
and whether it correctly eliminates the PydanticSerializationError class
Exp087 root-caused. Same live-validation methodology as Exp079/082/086:
reuses run_canary.py's internals (_check_result, _load_history,
_save_history, _acquire_lock, _release_lock, BENCHMARKS_DIR) WITHOUT
modifying that file.

Per this experiment's own constraints: one Cerebras canary unless
evidence requires expansion, no implementation changes unless a new
deterministic root cause surfaces.

Instrumentation, non-invasive (production functions called for real,
only observed): wraps `app.services.deterministic_patcher._patch_orm_response_model`
(the module-level name -- run_deterministic_patches()'s own internal
call resolves this name at call time via the module's global namespace,
so patching it here affects the real call site without touching
production code) to log every invocation where the output differs from
the input, distinguishing a genuine Exp088 conversion injection
(the diff contains "model_validate") from an unrelated substitution
(response_model=X -> response_model=Schema, no conversion needed).

Usage:
    python scripts/exp089_canary.py --label exp089-validation-r1 --no-deploy --provider cerebras
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import run_canary
from run_canary import (
    BENCHMARKS_DIR, _check_result, _load_history, _save_history,
    _acquire_lock, _release_lock, _regressed,
)

EXP089_APPS = [
    ("todo", "01_todo.txt"),
]

INVOCATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp089_orm_dict_response_invocations.json"

_invocations: list[dict] = []
_current_app_key = {"key": None}
_raw_results_by_app: dict[str, dict] = {}


def _make_instrumented_patch_fn(original_fn):
    def wrapper(content, filepath, project_path=None):
        result = original_fn(content, filepath, project_path=project_path)
        if result != content:
            injected_conversion = "model_validate" in result and "model_validate" not in content
            _invocations.append({
                "app": _current_app_key["key"],
                "filepath": filepath,
                "changed": True,
                "injected_conversion": injected_conversion,
            })
        return result
    return wrapper


def _install_instrumentation():
    import app.services.deterministic_patcher as dp
    original = dp._patch_orm_response_model
    dp._patch_orm_response_model = _make_instrumented_patch_fn(original)
    return dp, original


def _uninstall_instrumentation(dp, original):
    dp._patch_orm_response_model = original


def _make_result_capturing_generate_project_v15(original_fn):
    def wrapper(*args, **kwargs):
        result = original_fn(*args, **kwargs)
        key = _current_app_key["key"]
        if key is not None:
            _raw_results_by_app[key] = result
        return result
    return wrapper


def _install_result_capture():
    original = run_canary.generate_project_v15
    run_canary.generate_project_v15 = _make_result_capturing_generate_project_v15(original)
    return original


def _uninstall_result_capture(original):
    run_canary.generate_project_v15 = original


def main():
    parser = argparse.ArgumentParser(description="Experiment 089 canary: live validation of Exp088's ORM dict response repair")
    parser.add_argument("--label", default="exp089-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    args = parser.parse_args()

    apps = EXP089_APPS

    dp, original_patch_fn = _install_instrumentation()
    original_generate_fn = _install_result_capture()
    _acquire_lock(args.label)
    try:
        deploy = not args.no_deploy
        history = _load_history()
        prior_by_app = {}
        if history["runs"]:
            last_run = history["runs"][-1]
            prior_by_app = {r["app"]: r for r in last_run["results"]}

        results = []
        final_route_dirs = {}
        for idea_key, filename in apps:
            idea = (BENCHMARKS_DIR / filename).read_text(encoding="utf-8").strip()
            _current_app_key["key"] = idea_key
            r = _check_result(idea_key, idea, deploy, provider=args.provider)
            results.append(r)
            raw_result = _raw_results_by_app.get(idea_key, {})
            project_path = raw_result.get("project_path")
            if not project_path:
                project_name = raw_result.get("project_name")
                if project_name:
                    project_path = str(_BACKEND_ROOT.parent / "generated_projects" / project_name)
            final_route_dirs[idea_key] = project_path

        print(f"\n{'='*70}\n  EXP089 CANARY SUMMARY  ({args.label})\n{'='*70}")
        any_regression = False
        for r in results:
            prev = prior_by_app.get(r["app"])
            regressions = _regressed(prev, r) if prev else []
            status = "REGRESSION" if regressions else ("BASELINE" if not prev else "OK")
            print(f"  [{status:10}] {r['app']:10} score={r['forge_score']:.1f}  "
                  f"build={r['build_ok']} runtime={r['runtime_ok']} crud={r['crud_ok']} "
                  f"browser={r['browser_ok']} deployed={r['deployed']}  ({r['elapsed_s']}s)")
            for reason in regressions:
                print(f"                 -- {reason}")
                any_regression = True

        history["runs"].append({
            "label": args.label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "deploy": deploy,
            "results": results,
        })
        history["runs"] = history["runs"][-50:]
        _save_history(history)

        INVOCATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        INVOCATIONS_PATH.write_text(
            json.dumps({
                "label": args.label,
                "invocations": _invocations,
                "final_project_paths": final_route_dirs,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        conversions = [inv for inv in _invocations if inv["injected_conversion"]]
        print(f"\n_patch_orm_response_model() calls that changed content: {len(_invocations)}")
        print(f"  of which injected an Exp088 conversion: {len(conversions)}")
        for inv in _invocations:
            print(f"  app={inv['app']} file={inv['filepath']} injected_conversion={inv['injected_conversion']}")

        print(f"\n{'='*70}")
        if any_regression:
            print("  EXP089 CANARY: regression(s) detected vs. prior run")
        else:
            print("  EXP089 CANARY: no regression detected")
        if not conversions:
            print("  EXP089 CANARY: no Exp088 conversion injected this run -- either no "
                  "dict-response ORM pattern was generated, or it didn't need one")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(dp, original_patch_fn)
        _uninstall_result_capture(original_generate_fn)


if __name__ == "__main__":
    main()
