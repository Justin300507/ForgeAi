"""
Experiment 076: Live Validation of NOT NULL UPDATE Repair (Exp075).

Measures whether Exp075's new `preflight.py::_fix_update_notnull_field_loss`
actually fires and correctly guards a real, live-generated PUT/PATCH
handler -- same live-validation methodology Exp074 used for Exp073.
Reuses run_canary.py's internals (_check_result, _load_history,
_save_history, _acquire_lock, _release_lock, BENCHMARKS_DIR) WITHOUT
modifying that file.

Per this experiment's own constraints: ONE app only (`inventory` -- the
same app Exp074 found the live incident in, so this run both re-tests the
exact scenario and gives Exp075's replay something real to compare
against), minimal Cerebras usage, no implementation changes unless a NEW
root cause surfaces.

Instrumentation: patches the ALREADY-REGISTERED preflight entry for
"fix_update_notnull_field_loss" in place (the registry stores a direct
function reference captured at decoration time, so reassigning the
module attribute alone would not affect the live registry -- this walks
`preflight._fixes` and swaps that one entry's callable for an
instrumented wrapper) that snapshots every `app/routes/*.py` file
immediately before and after the real invocation and diffs them. Source
files are never touched; production behavior (the actual guard-insertion
logic) is completely unchanged -- the wrapper only observes.

Usage:
    python scripts/exp076_canary.py --label exp076-validation-r1 --no-deploy --provider cerebras
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

from run_canary import (
    BENCHMARKS_DIR, _check_result, _load_history, _save_history,
    _acquire_lock, _release_lock, _regressed,
)

EXP076_APPS = [
    ("inventory", "18_inventory.txt"),
]

INVOCATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp076_patcher_invocations.json"

_current_app = {"key": None}
_invocations = []
_model_snapshots = {"before": None, "after": None}


def _snapshot_routes(project_path: Path) -> dict:
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return {}
    out = {}
    for rf in routes_dir.glob("*.py"):
        try:
            out[rf.name] = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            out[rf.name] = None
    return out


def _snapshot_models(project_path: Path) -> dict:
    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return {}
    out = {}
    for mf in models_dir.glob("*.py"):
        try:
            out[mf.name] = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            out[mf.name] = None
    return out


def _diff_added_guard_lines(before: str, after: str) -> list[dict]:
    """This fix's only possible edit shape: one unguarded `target.field =
    source.field` line replaced by two lines (`if source.field is not
    None:` + the same assignment, now indented). Detected via a simple
    line-count-aware diff rather than SequenceMatcher, since the shape is
    fixed and known."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    edits = []
    bi = 0
    for ai in range(len(after_lines)):
        if bi >= len(before_lines):
            break
        if after_lines[ai] == before_lines[bi]:
            bi += 1
            continue
        # Candidate: after[ai] is a new "if ... is not None:" guard line,
        # and after[ai+1] should equal before[bi] (the original line, now
        # indented one level deeper).
        if after_lines[ai].strip().startswith("if ") and after_lines[ai].strip().endswith("is not None:"):
            if ai + 1 < len(after_lines) and after_lines[ai + 1].strip() == before_lines[bi].strip():
                edits.append({
                    "guard_line": after_lines[ai].strip(),
                    "original_line": before_lines[bi].strip(),
                    "guarded_line": after_lines[ai + 1].strip(),
                })
                bi += 1
                continue
        # Anything else means an unexpected divergence -- stop trusting
        # this alignment; record what's left as "unexplained" for manual review.
        break
    return edits


def _make_instrumented_fix(original_fn):
    def wrapper(project_path: Path, diagnostics: list):
        before = _snapshot_routes(project_path)
        _model_snapshots["before"] = _snapshot_models(project_path)
        changed = original_fn(project_path, diagnostics)
        after = _snapshot_routes(project_path)
        _model_snapshots["after"] = _snapshot_models(project_path)
        for fname, before_content in before.items():
            after_content = after.get(fname)
            if before_content is None or after_content is None or before_content == after_content:
                continue
            edits = _diff_added_guard_lines(before_content, after_content)
            _invocations.append({
                "app": _current_app["key"],
                "file": fname,
                "fix_returned_changed": changed,
                "edits": edits,
                "line_count_before": len(before_content.splitlines()),
                "line_count_after": len(after_content.splitlines()),
            })
        return changed
    return wrapper


def _install_instrumentation():
    import app.repair.preflight as preflight_module
    original = None
    for i, (prio, name, fn) in enumerate(preflight_module.preflight._fixes):
        if name == "fix_update_notnull_field_loss":
            original = fn
            preflight_module.preflight._fixes[i] = (prio, name, _make_instrumented_fix(fn))
            break
    if original is None:
        raise RuntimeError("fix_update_notnull_field_loss not found in preflight registry -- Exp075 fix missing?")
    return preflight_module, original


def _uninstall_instrumentation(preflight_module, original):
    for i, (prio, name, fn) in enumerate(preflight_module.preflight._fixes):
        if name == "fix_update_notnull_field_loss":
            preflight_module.preflight._fixes[i] = (prio, name, original)
            break


def main():
    parser = argparse.ArgumentParser(description="Experiment 076 canary: live validation of Exp075's UPDATE NOT NULL repair")
    parser.add_argument("--label", default="exp076-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    args = parser.parse_args()

    apps = EXP076_APPS

    preflight_module, original = _install_instrumentation()
    _acquire_lock(args.label)
    try:
        deploy = not args.no_deploy
        history = _load_history()
        prior_by_app = {}
        if history["runs"]:
            last_run = history["runs"][-1]
            prior_by_app = {r["app"]: r for r in last_run["results"]}

        results = []
        for idea_key, filename in apps:
            _current_app["key"] = idea_key
            idea = (BENCHMARKS_DIR / filename).read_text(encoding="utf-8").strip()
            results.append(_check_result(idea_key, idea, deploy, provider=args.provider))

        print(f"\n{'='*70}\n  EXP076 CANARY SUMMARY  ({args.label})\n{'='*70}")
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
                "model_files_changed": (
                    _model_snapshots["before"] != _model_snapshots["after"]
                    if _model_snapshots["before"] is not None else None
                ),
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"\n_fix_update_notnull_field_loss() invocations that changed a file: {len(_invocations)}")
        for inv in _invocations:
            print(f"  app={inv['app']} file={inv['file']} fix_returned_changed={inv['fix_returned_changed']} edits={len(inv['edits'])}")
            for e in inv["edits"]:
                print(f"      {e['guard_line']}")
                print(f"        {e['guarded_line']}")
        print(f"model files changed by this fix's invocation: {_model_snapshots['before'] != _model_snapshots['after']}")

        print(f"\n{'='*70}")
        if any_regression:
            print("  EXP076 CANARY: regression(s) detected vs. prior run")
        else:
            print("  EXP076 CANARY: no regression vs. prior run")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(preflight_module, original)


if __name__ == "__main__":
    main()
