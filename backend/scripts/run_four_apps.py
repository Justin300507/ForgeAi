"""
One-off runner for the user's 4-app quality pass (habit_tracker, crm,
expense_tracker, snake_and_ladders) -- NOT a change to run_canary.py's fixed
3-app canary (todo/blog_cms/crm), which stays untouched per CLAUDE.md.

Calls generate_project_v15() directly (same live pipeline entrypoint
run_canary.py uses) for each app, sequentially (to avoid splitting Cerebras/
Gemini/Groq quota across 4 concurrent generations), with deploy=True so any
app crossing FORGE_DEPLOY_THRESHOLD deploys immediately.

Usage:
    python scripts/run_four_apps.py                # all 4
    python scripts/run_four_apps.py --apps habit,crm
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.v15_orchestrator import generate_project_v15

APPS = {
    "habit": (
        "habit_tracker",
        "A habit tracker with streaks, badges, dark mode, and weekly reports",
    ),
    "crm": (
        "simple_crm",
        (_BACKEND_ROOT.parent / "benchmarks" / "golden" / "04_crm.txt")
        .read_text(encoding="utf-8").strip(),
    ),
    "expense": (
        "personal_expense_tracker",
        (_BACKEND_ROOT.parent / "benchmarks" / "golden" / "02_expense_tracker.txt")
        .read_text(encoding="utf-8").strip(),
    ),
    "snake": (
        "snake_and_ladders",
        "A snake and ladders game app. Users register and log in, create or "
        "join a game with a room code, and play snake and ladders online "
        "with 2-4 players taking turns rolling dice, moving a token up a "
        "10x10 board, hitting snakes/ladders, with a winner declared when a "
        "player reaches square 100.",
    ),
}

RESULTS_PATH = _BACKEND_ROOT / "benchmark_results" / "four_app_pass.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps", default="habit,crm,expense,snake")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--no-deploy", action="store_true")
    args = parser.parse_args()

    wanted = [a.strip() for a in args.apps.split(",") if a.strip()]
    results = []

    for key in wanted:
        if key not in APPS:
            print(f"Unknown app key: {key}")
            continue
        name, idea = APPS[key]
        print(f"\n{'='*70}\n  RUN: {key} ({name})\n{'='*70}")
        t0 = time.time()
        try:
            result = generate_project_v15(
                idea=idea,
                provider=args.provider,
                deploy=not args.no_deploy,
                deploy_to="both",
            )
            elapsed = round(time.time() - t0, 1)
            score = result.get("forge_score", {})
            print(
                f"  DONE {key}: score={score} "
                f"deployed={result.get('deployed')} "
                f"project_path={result.get('project_path')} "
                f"elapsed={elapsed}s"
            )
            results.append({
                "app": key, "name": name, "elapsed_s": elapsed,
                "forge_score": score,
                "deployed": result.get("deployed"),
                "deploy_urls": result.get("deploy_urls") or result.get("deployment"),
                "project_path": result.get("project_path"),
                "crashed": False,
            })
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            print(f"  CRASHED {key}: {type(exc).__name__}: {exc}")
            results.append({
                "app": key, "name": name, "elapsed_s": elapsed,
                "crashed": True, "error": f"{type(exc).__name__}: {exc}",
            })

        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'='*70}\n  ALL DONE -- summary written to {RESULTS_PATH}\n{'='*70}")
    for r in results:
        print(" ", r["app"], "->", r.get("forge_score"), "deployed:", r.get("deployed"), "crashed:", r.get("crashed"))


if __name__ == "__main__":
    main()
