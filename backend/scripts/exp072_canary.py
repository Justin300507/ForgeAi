"""
Experiment 072: End-to-End Reliability Validation canary.

Measures real Exp064-071 impact via live /project/v15 generation.
Reuses run_canary.py's internals (_check_result, _load_history,
_save_history, _acquire_lock, _release_lock, _regressed, BENCHMARKS_DIR,
_DIM_MAP) WITHOUT modifying that file -- run_canary.py's own CANARY_APPS
is documented as "fixed 3-app canary, do not change without explicit
sign-off"; this experiment's own prompt is that sign-off for adding a
4th app (inventory), but the sign-off is scoped to this measurement run,
not a permanent change to the core script. Exactly the same precedent
Exp062's own scripts/exp062_cross_app.py already established for adding
inventory -- reused here, not reinvented.

Writes to the SAME canary_history.json in the SAME format run_canary.py
uses (not a separate output directory), so this run is automatically
picked up by app/memory/reliability_metrics.py's existing
compute_reliability_timeline()/compute_experiment_attribution()/
compute_observatory() -- no new comparison tooling needed.

Usage:
    python scripts/exp072_canary.py --label exp072-validation-r1 --no-deploy --provider cerebras
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from run_canary import (
    CANARY_APPS, BENCHMARKS_DIR, _check_result, _load_history, _save_history,
    _acquire_lock, _release_lock, _regressed,
)

EXP072_APPS = list(CANARY_APPS) + [("inventory", "18_inventory.txt")]


def main():
    parser = argparse.ArgumentParser(description="Experiment 072 canary: Todo/Blog CMS/CRM/Inventory")
    parser.add_argument("--label", default="exp072-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    parser.add_argument("--only", default=None,
                         help="Comma-separated subset of app keys to run, e.g. 'inventory' for a single-app repeat")
    args = parser.parse_args()

    apps = EXP072_APPS
    if args.only:
        wanted = set(a.strip() for a in args.only.split(","))
        apps = [(k, f) for k, f in EXP072_APPS if k in wanted]
        if not apps:
            print(f"No matching apps for --only={args.only!r}; available: {[k for k, _ in EXP072_APPS]}")
            sys.exit(2)

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
            idea = (BENCHMARKS_DIR / filename).read_text(encoding="utf-8").strip()
            results.append(_check_result(idea_key, idea, deploy, provider=args.provider))

        print(f"\n{'='*70}\n  EXP072 CANARY SUMMARY  ({args.label})\n{'='*70}")
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

        print(f"\n{'='*70}")
        if any_regression:
            print("  EXP072 CANARY: regression(s) detected vs. prior run")
        else:
            print("  EXP072 CANARY: no regression vs. prior run")
        print(f"{'='*70}\n")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
