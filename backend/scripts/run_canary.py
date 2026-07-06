"""
Canary benchmark: 3 apps (Todo, Blog CMS, CRM) run against the LIVE V15
pipeline, gating whether it's safe to continue to the next VNext report
milestone. Full 15/20-app ForgeBench runs are reserved for releases, major
architectural changes, or explicit request -- this exists for the fast,
cheap "did I just break something" check in between.

Ideas are read directly from benchmarks/golden/{01_todo,09_blog,04_crm}.txt
so the canary always matches the canonical suite wording.

Unlike ForgeBench's default ForgeAIGenerator (which calls the legacy
app.services.project_service.generate_project path), this calls
generate_project_v15() directly -- the actual pipeline changes land in,
and the one the live API serves under FORGE_PIPELINE_VERSION=v15.

Usage:
    python scripts/run_canary.py                  # run + compare to last canary
    python scripts/run_canary.py --label m1-contract
    python scripts/run_canary.py --no-deploy       # skip live deploy (faster, no side effects)

Exit code 0 = no regression (or first-ever baseline run). Exit code 1 =
regression detected in build/runtime/CRUD/browser/deployment/forge score.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.v15_orchestrator import generate_project_v15

BENCHMARKS_DIR = _BACKEND_ROOT.parent / "benchmarks" / "golden"
HISTORY_PATH   = _BACKEND_ROOT / "benchmark_results" / "canary_history.json"

CANARY_APPS = [
    ("todo",     "01_todo.txt"),
    ("blog_cms", "09_blog.txt"),
    ("crm",      "04_crm.txt"),
]

# Dimension names (see app/scoring/engine.py) mapped to the six things the
# canary checks for regression. A dimension that's N/A (didn't run) is
# treated as "unknown", not a pass or a fail.
_DIM_MAP = {
    "build":      "Compilation",
    "runtime":    "Runtime Startup",
    "crud":       "Integration",
    "browser":    "Browser UX",
}


def _load_history() -> dict:
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"runs": []}


def _save_history(data: dict):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _dim_lookup(dimensions: list[dict], name: str) -> dict | None:
    return next((d for d in dimensions if d["name"] == name), None)


def _check_result(idea_key: str, idea: str, deploy: bool) -> dict:
    print(f"\n{'='*70}\n  CANARY: {idea_key}\n{'='*70}")
    t0 = time.time()
    try:
        result = generate_project_v15(idea=idea, provider="auto", deploy=deploy)
    except Exception as exc:
        return {
            "app": idea_key, "crashed": True, "error": f"{type(exc).__name__}: {exc}",
            "forge_score": 0.0, "build_ok": False, "runtime_ok": False,
            "crud_ok": False, "browser_ok": False, "deployed": False,
            "elapsed_s": round(time.time() - t0, 1),
        }

    dims = result.get("dimensions", [])
    build_dim   = _dim_lookup(dims, _DIM_MAP["build"])
    runtime_dim = _dim_lookup(dims, _DIM_MAP["runtime"])
    crud_dim    = _dim_lookup(dims, _DIM_MAP["crud"])
    browser_dim = _dim_lookup(dims, _DIM_MAP["browser"])

    return {
        "app":          idea_key,
        "crashed":      False,
        "forge_score":  result.get("forge_score", 0.0),
        "grade":        result.get("grade", "F"),
        "build_ok":     bool(build_dim and build_dim["passed"]) if build_dim and not build_dim["na"] else None,
        "runtime_ok":   bool(runtime_dim and runtime_dim["passed"]) if runtime_dim and not runtime_dim["na"] else None,
        "crud_ok":      bool(crud_dim and crud_dim["passed"]) if crud_dim and not crud_dim["na"] else None,
        "browser_ok":   bool(browser_dim and browser_dim["passed"]) if browser_dim and not browser_dim["na"] else None,
        "deployed":     result.get("deployed", False),
        "fix_attempts": result.get("fix_attempts", 0),
        "elapsed_s":    round(time.time() - t0, 1),
    }


def _regressed(prev: dict, curr: dict) -> list[str]:
    """Compare against the prior canary run for this app. A metric that was
    True/passing before and is False/failing now is a regression. A metric
    that's None (dimension didn't run / N/A) on either side is skipped --
    nothing to compare."""
    reasons = []
    if curr["crashed"]:
        reasons.append("pipeline crashed")
        return reasons
    for key, label in (("build_ok", "build"), ("runtime_ok", "runtime"),
                       ("crud_ok", "CRUD"), ("browser_ok", "browser")):
        if prev.get(key) is True and curr.get(key) is False:
            reasons.append(f"{label} regressed (was passing, now failing)")
    if prev.get("deployed") and not curr.get("deployed"):
        reasons.append("deployment regressed (was deploying, now not)")
    # Forge score: allow small run-to-run noise, flag anything more than a
    # trivial drop as a real regression signal worth looking at.
    prev_score = prev.get("forge_score", 0.0)
    curr_score = curr.get("forge_score", 0.0)
    if prev_score - curr_score > 3.0:
        reasons.append(f"forge score dropped {prev_score:.1f} -> {curr_score:.1f}")
    return reasons


def main():
    parser = argparse.ArgumentParser(description="Canary benchmark: Todo/Blog CMS/CRM against V15Pipeline")
    parser.add_argument("--label", default=None, help="Label for this run (e.g. milestone name)")
    parser.add_argument("--no-deploy", action="store_true", help="Skip live deploy (faster, no side effects)")
    args = parser.parse_args()

    deploy = not args.no_deploy
    history = _load_history()
    prior_by_app = {}
    if history["runs"]:
        last_run = history["runs"][-1]
        prior_by_app = {r["app"]: r for r in last_run["results"]}

    results = []
    for idea_key, filename in CANARY_APPS:
        idea = (BENCHMARKS_DIR / filename).read_text(encoding="utf-8").strip()
        results.append(_check_result(idea_key, idea, deploy))

    print(f"\n{'='*70}\n  CANARY SUMMARY{'  (' + args.label + ')' if args.label else ''}\n{'='*70}")
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
    history["runs"] = history["runs"][-50:]  # keep last 50 canary runs
    _save_history(history)

    print(f"\n{'='*70}")
    if any_regression:
        print("  CANARY FAILED -- regression detected, do not proceed to next milestone")
        print(f"{'='*70}\n")
        sys.exit(1)
    else:
        print("  CANARY PASSED -- safe to continue")
        print(f"{'='*70}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
