"""
Experiment 086: Live Validation of Cross-File Auth Field Validation (Exp085).

Measures whether Exp085's extension to check_auth_completeness() (cross-
file request-schema field-consistency detection, wired into the existing
ensure_auth_completeness() repair path) actually activates during a real
generation, and whether it correctly eliminates the SignupRequest.username
AttributeError class Exp083 measured at 53% of recent failures. Same
live-validation methodology as Exp079/082: reuses run_canary.py's
internals (_check_result, _load_history, _save_history, _acquire_lock,
_release_lock, BENCHMARKS_DIR) WITHOUT modifying that file.

Per this experiment's own constraints: prefers the historical failing
benchmark (golden/01_todo.txt, the "todo" canary app -- the exact idea
text behind all 9 of Exp083's recorded SignupRequest.username failures),
minimal Cerebras usage, no implementation changes unless a NEW root cause
surfaces.

Instrumentation, non-invasive (production functions called for real, only
observed): wraps `app.services.v6_orchestrator.ensure_auth_completeness`
(the name as bound in v6_orchestrator's own module namespace -- the
actual call site inside both Architecture Repair blocks) to log every
invocation: the before/after AuthCompletenessResult (complete, reason,
field_mismatches) and the repair status ("complete"/"repaired"/"failed").

Usage:
    python scripts/exp086_canary.py --label exp086-validation-r1 --no-deploy --provider cerebras
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

EXP086_APPS = [
    ("todo", "01_todo.txt"),
]

INVOCATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp086_auth_completeness_invocations.json"

_invocations: list[dict] = []
_raw_results_by_app: dict[str, dict] = {}
_current_app_key = {"key": None}


def _result_to_dict(r) -> dict:
    return {
        "complete": r.complete,
        "reason": r.reason,
        "field_mismatches": list(r.field_mismatches),
        "missing_required": list(r.missing_required),
    }


def _make_instrumented_ensure_auth_completeness(original_fn):
    def wrapper(project_path, project_name=""):
        result = original_fn(project_path, project_name=project_name)
        _invocations.append({
            "app": _current_app_key["key"],
            "project_name": project_name,
            "status": result["status"],
            "before": _result_to_dict(result["before"]),
            "after": _result_to_dict(result["after"]) if result.get("after") is not None else None,
        })
        return result
    return wrapper


def _install_instrumentation():
    import app.services.v6_orchestrator as v6
    original = v6.ensure_auth_completeness
    v6.ensure_auth_completeness = _make_instrumented_ensure_auth_completeness(original)
    return v6, original


def _uninstall_instrumentation(v6, original):
    v6.ensure_auth_completeness = original


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


def _final_auth_route_content(project_path: Path) -> str | None:
    for candidate in (project_path / "app" / "routes" / "auth_routes.py",):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace")
    return None


def main():
    parser = argparse.ArgumentParser(description="Experiment 086 canary: live validation of Exp085's cross-file auth field validation")
    parser.add_argument("--label", default="exp086-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    args = parser.parse_args()

    apps = EXP086_APPS

    v6, original_ensure_fn = _install_instrumentation()
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
        final_auth_contents = {}
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
            if project_path and Path(project_path).exists():
                final_auth_contents[idea_key] = _final_auth_route_content(Path(project_path))
            else:
                final_auth_contents[idea_key] = None

        print(f"\n{'='*70}\n  EXP086 CANARY SUMMARY  ({args.label})\n{'='*70}")
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
                "final_auth_route_contains_username_bug": {
                    k: (bool(v) and "req.username" in v) for k, v in final_auth_contents.items()
                },
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"\nensure_auth_completeness() invocations (Architecture Repair fired): {len(_invocations)}")
        for inv in _invocations:
            print(f"  app={inv['app']} status={inv['status']} "
                  f"before.complete={inv['before']['complete']} "
                  f"before.field_mismatches={inv['before']['field_mismatches']}")

        for app_key, content in final_auth_contents.items():
            has_bug = bool(content) and "req.username" in content
            print(f"\nfinal auth_routes.py for {app_key}: "
                  f"{'CONTAINS req.username (BUG PRESENT)' if has_bug else 'clean (no req.username)'}")

        print(f"\n{'='*70}")
        if any_regression:
            print("  EXP086 CANARY: regression(s) detected vs. prior run")
        else:
            print("  EXP086 CANARY: no regression detected")
        if not _invocations:
            print("  EXP086 CANARY: Architecture Repair did not fire this run -- "
                  "mechanism not exercised live (see Exp085's offline replay for direct proof)")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(v6, original_ensure_fn)
        _uninstall_result_capture(original_generate_fn)


if __name__ == "__main__":
    main()
