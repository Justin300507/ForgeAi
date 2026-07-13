"""
Experiment 099: Live Validation of Cross-Type Attribute Access Repair (Exp098).

Measures whether Exp098's extended _patch_attr_access_mismatches()
(app/services/deterministic_patcher.py -- now covers Pydantic schema
classes in addition to SQLAlchemy models, plus curated "name"/
"password" credential-field synonyms) activates correctly during a
REAL generation, with ideas chosen to be reproducible/likely to hit the
confirmed shapes: todo (Exp097's exact confirmed architecture/incident
shape) and a dedicated auth-heavy idea (registration/reset/verification
-- heavy on User/UserCreate-shaped code).

Per this experiment's constraints: one or two Cerebras canaries only,
no implementation changes unless a new deterministic issue surfaces.

Instrumentation, non-invasive (production function called for real,
only observed) -- same methodology as Exp079/082/086/089/093/096:
wraps `app.services.deterministic_patcher._patch_attr_access_mismatches`
to diff every route file it touches, recording before/after content so
a "was this substitution correct" check can be done directly against
the actual generated model/schema source, not just trusting the patch
count.

Usage:
    python scripts/exp099_canary.py --label exp099-validation-r1 --no-deploy --provider cerebras --apps todo
    python scripts/exp099_canary.py --label exp099-validation-r2 --no-deploy --provider cerebras --idea-key auth_heavy
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

INVOCATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp099_attr_mismatch_invocations.json"

_invocations: list[dict] = []
_current_app_key = {"key": None}


def _make_instrumented_patch_fn(original_fn):
    def wrapper(project_path):
        routes_dir = Path(project_path) / "app" / "routes"
        before_files = {}
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
                    before_lines = before_content.splitlines()
                    after_lines = after_content.splitlines()
                    diff = [
                        {"before": b, "after": a}
                        for b, a in zip(before_lines, after_lines) if b != a
                    ]
                    changed_files.append({"file": name, "diff": diff})
            _invocations.append({
                "app": _current_app_key["key"],
                "patched_count": result,
                "changed_files": changed_files,
            })
        return result
    return wrapper


def _install_instrumentation():
    import app.services.deterministic_patcher as dp
    original = dp._patch_attr_access_mismatches
    dp._patch_attr_access_mismatches = _make_instrumented_patch_fn(original)
    return dp, original


def _uninstall_instrumentation(dp, original):
    dp._patch_attr_access_mismatches = original


_AUTH_HEAVY_IDEA = (BENCHMARKS_DIR / "14_auth.txt").read_text(encoding="utf-8").strip()

_CUSTOM_IDEAS = {
    "auth_heavy": _AUTH_HEAVY_IDEA,
}


def main():
    parser = argparse.ArgumentParser(description="Experiment 099 canary: live validation of Exp098's cross-type attribute-access repair")
    parser.add_argument("--label", default="exp099-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    parser.add_argument("--apps", default="", help="comma-separated app keys from CANARY_APPS, e.g. todo")
    parser.add_argument("--idea-key", default="", help="key into _CUSTOM_IDEAS, e.g. auth_heavy")
    args = parser.parse_args()

    jobs: list[tuple[str, str]] = []
    if args.apps:
        wanted = set(args.apps.split(","))
        jobs += [(k, (BENCHMARKS_DIR / f).read_text(encoding="utf-8").strip())
                 for k, f in CANARY_APPS if k in wanted]
    if args.idea_key:
        jobs.append((args.idea_key, _CUSTOM_IDEAS[args.idea_key]))
    if not jobs:
        print("No matching --apps/--idea-key given.")
        sys.exit(1)

    dp, original_fn = _install_instrumentation()
    _acquire_lock(args.label)
    try:
        deploy = not args.no_deploy
        history = _load_history()
        prior_by_app = {}
        if history["runs"]:
            last_run = history["runs"][-1]
            prior_by_app = {r["app"]: r for r in last_run["results"]}

        results = []
        for idea_key, idea in jobs:
            _current_app_key["key"] = idea_key
            r = _check_result(idea_key, idea, deploy, provider=args.provider)
            results.append(r)

        print(f"\n{'='*70}\n  EXP099 CANARY SUMMARY  ({args.label})\n{'='*70}")
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

        print(f"\n_patch_attr_access_mismatches() activations (content changed): {len(_invocations)}")
        for inv in _invocations:
            print(f"  app={inv['app']} patched_files={inv['patched_count']}")
            for cf in inv["changed_files"]:
                print(f"    {cf['file']}:")
                for d in cf["diff"]:
                    print(f"      - {d['before'].strip()}")
                    print(f"      + {d['after'].strip()}")

        print(f"\n{'='*70}")
        if any_regression:
            print("  EXP099 CANARY: regression(s) detected vs. prior run")
        else:
            print("  EXP099 CANARY: no regression detected")
        if not _invocations:
            print("  EXP099 CANARY: patch did not activate this run -- either no "
                  "attribute-mismatch pattern was generated, or it didn't need one")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(dp, original_fn)


if __name__ == "__main__":
    main()
