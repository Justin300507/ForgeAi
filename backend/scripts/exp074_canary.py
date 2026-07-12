"""
Experiment 074: Live Validation of AST-Scoped Attribute Repair (Exp073).

Measures whether Exp073's AST-scoped rewrite of
_patch_attr_access_mismatches() actually prevents the Exp072-confirmed
corruption under REAL generation. Reuses run_canary.py's internals
(_check_result, _load_history, _save_history, _acquire_lock,
_release_lock, _regressed, BENCHMARKS_DIR) WITHOUT modifying that file --
same precedent Exp072's own scripts/exp072_canary.py established for a
non-standard app set (this run's own prompt is the sign-off, scoped to
this measurement, not a permanent CANARY_APPS change).

App set for THIS experiment (per its own prompt): Todo, Blog CMS,
Inventory -- deliberately NOT CRM (Exp072 already showed CRM stable and
this experiment's mission targets the two apps that hit the auth
corruption plus the app category Exp072 flagged for inventory-specific
issues).

Instrumentation: monkeypatches
app.services.deterministic_patcher._patch_attr_access_mismatches with a
wrapper that snapshots every app/routes/*.py file's content immediately
before and after each real invocation, diffs them, and records exactly
which object.attr -> object.attr' rewrites happened (or didn't) per app.
This is pure runtime wrapping in THIS script -- deterministic_patcher.py
itself is never touched, per this experiment's "no further code changes"
rule. Results are written to benchmark_results/exp074_patcher_invocations.json
in addition to the normal canary_history.json entry.

Usage:
    python scripts/exp074_canary.py --label exp074-validation-r1 --no-deploy --provider cerebras
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
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

EXP074_APPS = [
    ("todo", "01_todo.txt"),
    ("blog_cms", "09_blog.txt"),
    ("inventory", "18_inventory.txt"),
]

INVOCATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp074_patcher_invocations.json"

_DOT_ATTR_RE = re.compile(r'(\w+)\.(\w+)\b')

_current_app = {"key": None}
_invocations = []


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


def _diff_rewrites(before: str, after: str) -> list[dict]:
    """Line-level diff between before/after route-file content, extracting
    every `obj.attr` -> `obj.attr2` rewrite pair for the SAME object name on
    matching line numbers (the shape this patcher can legally produce)."""
    rewrites = []
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        for old_line, new_line in zip(before_lines[i1:i2], after_lines[j1:j2]):
            old_attrs = {(m.group(1), m.group(2)) for m in _DOT_ATTR_RE.finditer(old_line)}
            new_attrs = {(m.group(1), m.group(2)) for m in _DOT_ATTR_RE.finditer(new_line)}
            removed = old_attrs - new_attrs
            added = new_attrs - old_attrs
            for obj, old_attr in removed:
                match = next((a for o2, a in added if o2 == obj), None)
                if match:
                    rewrites.append({
                        "object": obj, "original_attribute": old_attr,
                        "replacement_attribute": match,
                        "before_line": old_line.strip(), "after_line": new_line.strip(),
                    })
    return rewrites


def _make_instrumented_patcher(original_fn):
    def wrapper(project_path: Path):
        before = _snapshot_routes(project_path)
        n = original_fn(project_path)
        after = _snapshot_routes(project_path)
        for fname, before_content in before.items():
            after_content = after.get(fname)
            if before_content is None or after_content is None or before_content == after_content:
                continue
            rewrites = _diff_rewrites(before_content, after_content)
            _invocations.append({
                "app": _current_app["key"],
                "file": fname,
                "patched_count_returned": n,
                "rewrites": rewrites,
            })
        return n
    return wrapper


def _install_instrumentation():
    import app.services.deterministic_patcher as dp
    original = dp._patch_attr_access_mismatches
    dp._patch_attr_access_mismatches = _make_instrumented_patcher(original)
    return dp, original


def _uninstall_instrumentation(dp, original):
    dp._patch_attr_access_mismatches = original


def main():
    parser = argparse.ArgumentParser(description="Experiment 074 canary: Todo/Blog CMS/Inventory, Exp073 live validation")
    parser.add_argument("--label", default="exp074-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    apps = EXP074_APPS
    if args.only:
        wanted = set(a.strip() for a in args.only.split(","))
        apps = [(k, f) for k, f in EXP074_APPS if k in wanted]
        if not apps:
            print(f"No matching apps for --only={args.only!r}; available: {[k for k, _ in EXP074_APPS]}")
            sys.exit(2)

    dp, original = _install_instrumentation()
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

        print(f"\n{'='*70}\n  EXP074 CANARY SUMMARY  ({args.label})\n{'='*70}")
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
            json.dumps({"label": args.label, "invocations": _invocations}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"\n_patch_attr_access_mismatches() invocations that changed a file: {len(_invocations)}")
        for inv in _invocations:
            print(f"  app={inv['app']} file={inv['file']} rewrites={len(inv['rewrites'])}")
            for rw in inv["rewrites"]:
                print(f"      {rw['object']}.{rw['original_attribute']} -> {rw['object']}.{rw['replacement_attribute']}")

        print(f"\n{'='*70}")
        if any_regression:
            print("  EXP074 CANARY: regression(s) detected vs. prior run")
        else:
            print("  EXP074 CANARY: no regression vs. prior run")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(dp, original)


if __name__ == "__main__":
    main()
