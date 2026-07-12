"""
Experiment 093: Live Validation of Ownership Assignment Repair (Exp092).

Measures whether Exp092's _patch_missing_ownership_assignment()
(app/services/deterministic_patcher.py) actually activates during a real
generation, and whether it correctly eliminates the ownership-FK-omission
class of JourneyCRUDFailure Exp091 root-caused (17/23, 74%, 0% same-run
self-heal per Exp090). Same live-validation methodology as
Exp079/082/086/089: reuses run_canary.py's internals (_check_result,
_load_history, _save_history, _acquire_lock, _release_lock,
BENCHMARKS_DIR) WITHOUT modifying that file.

Per this experiment's own constraints: one or two Cerebras canaries only,
prefers todo/CRM ideas (historically associated with ownership failures --
todo's Task.user_id and CRM's owner_id/user_id collision both directly
confirmed live in prior cycles), no implementation changes unless a new
deterministic root cause surfaces.

Instrumentation, non-invasive (production functions called for real,
only observed): wraps
`app.services.deterministic_patcher._patch_missing_ownership_assignment`
(the module-level name -- run_deterministic_patches()'s own internal
call resolves this name at call time via the module's global namespace,
so patching it here affects the real call site without touching
production code) to log every invocation where the output differs from
the input.

Usage:
    python scripts/exp093_canary.py --label exp093-validation-r1 --no-deploy --provider cerebras --apps todo
    python scripts/exp093_canary.py --label exp093-validation-r2 --no-deploy --provider cerebras --apps crm
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
    BENCHMARKS_DIR, CANARY_APPS, _check_result, _load_history, _save_history,
    _acquire_lock, _release_lock, _regressed,
)

INVOCATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp093_ownership_assignment_invocations.json"

_invocations: list[dict] = []
_current_app_key = {"key": None}
_raw_results_by_app: dict[str, dict] = {}


def _make_instrumented_patch_fn(original_fn):
    def wrapper(project_path):
        before_files = {}
        routes_dir = Path(project_path) / "app" / "routes"
        if routes_dir.exists():
            for rf in routes_dir.glob("*.py"):
                try:
                    before_files[rf.name] = rf.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        result = original_fn(project_path)
        if result:
            changed_files = []
            for name, before_content in before_files.items():
                after_path = routes_dir / name
                try:
                    after_content = after_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if after_content != before_content:
                    injected_lines = [
                        line for line in after_content.splitlines()
                        if line not in before_content.splitlines() and ".id" in line and "=" in line
                    ]
                    changed_files.append({"file": name, "injected_lines": injected_lines})
            _invocations.append({
                "app": _current_app_key["key"],
                "patched_count": result,
                "changed_files": changed_files,
            })
        return result
    return wrapper


def _install_instrumentation():
    import app.services.deterministic_patcher as dp
    original = dp._patch_missing_ownership_assignment
    dp._patch_missing_ownership_assignment = _make_instrumented_patch_fn(original)
    return dp, original


def _uninstall_instrumentation(dp, original):
    dp._patch_missing_ownership_assignment = original


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
    parser = argparse.ArgumentParser(description="Experiment 093 canary: live validation of Exp092's ownership assignment repair")
    parser.add_argument("--label", default="exp093-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    parser.add_argument("--apps", default="todo", help="comma-separated app keys from CANARY_APPS, e.g. todo,crm")
    args = parser.parse_args()

    wanted = set(args.apps.split(","))
    apps = [(k, f) for k, f in CANARY_APPS if k in wanted]
    if not apps:
        print(f"No matching apps for --apps {args.apps!r} in CANARY_APPS: {CANARY_APPS}")
        sys.exit(1)

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

        print(f"\n{'='*70}\n  EXP093 CANARY SUMMARY  ({args.label})\n{'='*70}")
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
        prior_invocations = []
        if INVOCATIONS_PATH.exists():
            try:
                prior_invocations = json.loads(INVOCATIONS_PATH.read_text(encoding="utf-8")).get("all_runs", [])
            except Exception:
                pass
        prior_invocations.append({"label": args.label, "invocations": _invocations})
        INVOCATIONS_PATH.write_text(
            json.dumps({"all_runs": prior_invocations}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"\n_patch_missing_ownership_assignment() activations (content changed): {len(_invocations)}")
        for inv in _invocations:
            print(f"  app={inv['app']} patched_files={inv['patched_count']}")
            for cf in inv["changed_files"]:
                print(f"    {cf['file']}:")
                for line in cf["injected_lines"]:
                    print(f"      + {line.strip()}")

        print(f"\n{'='*70}")
        if any_regression:
            print("  EXP093 CANARY: regression(s) detected vs. prior run")
        else:
            print("  EXP093 CANARY: no regression detected")
        if not _invocations:
            print("  EXP093 CANARY: patch did not activate this run -- either no "
                  "ownership-omission pattern was generated, or it didn't need one")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(dp, original_patch_fn)
        _uninstall_result_capture(original_generate_fn)


if __name__ == "__main__":
    main()
