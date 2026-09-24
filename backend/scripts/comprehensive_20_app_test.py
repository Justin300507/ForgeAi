"""
Comprehensive 20-App Validation Test
=====================================

Runs 20 diverse prompts stratified by complexity (simple/medium/complex)
to identify bottlenecks in the ForgeAI generation pipeline.

Each app tracks:
  - Completion (Yes/No)
  - Stage where it failed (if applicable)
  - Forge Score (0-100)
  - Time elapsed
  - Error details

NOTE: Frontend build / browser verification is disabled for this run.
This machine has a network-level issue reaching registry.npmjs.org
(TLS handshake corruption, likely broken IPv6 path or router-level
filtering -- confirmed NOT an npm/Node CA config issue; --use-system-ca
made no difference) that makes `npm install` take ~30-70min per app and
frequently crash npm itself. We monkey-patch the same "node not found"
code path FrontendRunner already uses for node-less environments, so
frontend_build reports SKIPPED (not FAILED) and doesn't pollute results.
This means Frontend Load / Browser UX dimensions are N/A for every app
in this run -- we're measuring backend/generation quality only.

Output: structured JSON report + summary statistics

Usage:
    python scripts/comprehensive_20_app_test.py
    python scripts/comprehensive_20_app_test.py --provider openai
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

# --- Disable frontend build (see module docstring) -------------------------
import app.runtime.frontend_runner as _frontend_runner_mod
_frontend_runner_mod._find_npm = lambda: None
# ----------------------------------------------------------------------------

from app.services.v15_orchestrator import generate_project_v15

RESULTS_DIR = _BACKEND_ROOT / "benchmark_results" / "comprehensive_20_app_test"
RESULTS_FILE = RESULTS_DIR / f"results_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"

# Test suite: 20 apps stratified by complexity
TEST_APPS = [
    # ============ SIMPLE (7 apps) ============
    {
        "name": "simple_01_todo",
        "complexity": "simple",
        "idea": "Simple todo list app with add, check off, and delete tasks",
    },
    {
        "name": "simple_02_notes",
        "complexity": "simple",
        "idea": "Basic note-taking app where users can create, read, update, and delete notes",
    },
    {
        "name": "simple_03_counter",
        "complexity": "simple",
        "idea": "Simple counter app that increments, decrements, and resets a number",
    },
    {
        "name": "simple_04_password_generator",
        "complexity": "simple",
        "idea": "Password generator that creates random secure passwords with configurable length",
    },
    {
        "name": "simple_05_unit_converter",
        "complexity": "simple",
        "idea": "Unit converter for temperature, distance, and weight conversions",
    },
    {
        "name": "simple_06_expense_tracker",
        "complexity": "simple",
        "idea": "Basic expense tracker where users log daily expenses and see total",
    },
    {
        "name": "simple_07_quote_display",
        "complexity": "simple",
        "idea": "App that displays random inspirational quotes with a refresh button",
    },

    # ============ MEDIUM (7 apps) ============
    {
        "name": "medium_01_blog_with_comments",
        "complexity": "medium",
        "idea": "Blog platform with user authentication, posts, and nested comments. Users can create posts, other users can comment on them, and creators can delete comments",
    },
    {
        "name": "medium_02_task_board",
        "complexity": "medium",
        "idea": "Kanban-style task board with columns (todo, in progress, done). Users authenticate, create projects, add tasks to columns, and drag tasks between columns",
    },
    {
        "name": "medium_03_inventory_system",
        "complexity": "medium",
        "idea": "Inventory management system with categories, products, stock levels, and low-stock alerts. Track quantity changes and generate reports",
    },
    {
        "name": "medium_04_event_booking",
        "complexity": "medium",
        "idea": "Event booking system where admins create events with dates and capacity, users browse and book tickets. Show available seats, prevent overbooking",
    },
    {
        "name": "medium_05_recipe_sharing",
        "complexity": "medium",
        "idea": "Recipe sharing platform with user profiles, recipe creation, ratings, and favorite lists. Users can rate and bookmark recipes from other users",
    },
    {
        "name": "medium_06_poll_voting",
        "complexity": "medium",
        "idea": "Poll creation and voting app. Users create polls with multiple options, vote once per poll, and see real-time results with percentages",
    },
    {
        "name": "medium_07_project_collaboration",
        "complexity": "medium",
        "idea": "Project management app with teams, projects, and tasks. Team members can be assigned tasks, mark them complete, and view project progress",
    },

    # ============ COMPLEX (6 apps) ============
    {
        "name": "complex_01_social_network",
        "complexity": "complex",
        "idea": "Social network with user profiles, follow/unfollow, posts, likes, comments, and a feed showing posts from followed users. Include notifications for new followers and post interactions",
    },
    {
        "name": "complex_02_ecommerce",
        "complexity": "complex",
        "idea": "E-commerce platform with products in categories, shopping cart, checkout, orders, payment simulation, and order history. Users can search by category and sort by price",
    },
    {
        "name": "complex_03_chat_app",
        "complexity": "complex",
        "idea": "Real-time chat application with user authentication, one-on-one messaging, group chats, message history, and online status indicators. Use WebSocket for live message delivery",
    },
    {
        "name": "complex_04_crm_system",
        "complexity": "complex",
        "idea": "Customer relationship management system with contacts, deals, activities, and pipeline stages. Sales teams manage leads through stages, track interactions, generate forecasts",
    },
    {
        "name": "complex_05_workout_tracker",
        "complexity": "complex",
        "idea": "Fitness tracking app with user profiles, workout logging (exercises, sets, reps, weight), progress graphs, and workout plan templates. Users can follow plans and track progress over time",
    },
    {
        "name": "complex_06_classroom_lms",
        "complexity": "complex",
        "idea": "Learning management system with teachers creating courses, lessons, assignments with deadlines, student submissions, and grading. Include progress tracking and grade reports",
    },
]


def _safe_float(v, default=0.0) -> float:
    """float() that tolerates None (dimension key present but N/A/excluded)."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _extract_stage_failure(result: dict) -> str | None:
    """Infer which stage failed based on dimensions and errors."""
    dims = result.get("dimensions", [])

    stage_map = {
        "Compilation": "backend_build",
        "Runtime Startup": "runtime",
        "Integration": "crud",
        "Browser UX": "frontend",
        "Code Quality": "code_quality",
    }

    for dim in dims:
        if dim.get("status") == "FAILED":
            return stage_map.get(dim.get("name"), "unknown")

    score = _safe_float(result.get("forge_score"), 0)
    if score == 0:
        return "generation"

    return None


def _run_test(app_spec: dict, provider: str = "auto") -> dict:
    """Run a single app generation and collect metrics."""
    name = app_spec["name"]
    idea = app_spec["idea"]
    complexity = app_spec["complexity"]

    print(f"\n{'='*70}", flush=True)
    print(f"  [{complexity.upper()}] {name}", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"  Prompt: {idea[:80]}...", flush=True)

    t0 = time.time()

    try:
        result = generate_project_v15(idea=idea, provider=provider, deploy=False)
        elapsed = time.time() - t0

        score = _safe_float(result.get("forge_score"), 0)
        completed = score > 0
        failure_stage = _extract_stage_failure(result) if not completed else None

        return {
            "name": name,
            "complexity": complexity,
            "completed": completed,
            "failure_stage": failure_stage,
            "forge_score": score,
            "elapsed_s": round(elapsed, 1),
            "crashed": False,
            "error": None,
            "dimensions": [
                {
                    "name": d.get("name"),
                    "status": d.get("status"),
                    "score": _safe_float(d.get("score"), None),
                }
                for d in result.get("dimensions", [])
            ],
            "retry_attempts": len(result.get("retry_history") or []),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        elapsed = time.time() - t0
        print(f"  ERROR: {type(exc).__name__}: {exc}", flush=True)

        return {
            "name": name,
            "complexity": complexity,
            "completed": False,
            "failure_stage": "generation",
            "forge_score": 0.0,
            "elapsed_s": round(elapsed, 1),
            "crashed": True,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "dimensions": [],
            "retry_attempts": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _compute_summary(results: list[dict]) -> dict:
    if not results:
        return {}

    completed = [r for r in results if r["completed"]]
    failed = [r for r in results if not r["completed"]]

    by_complexity = {}
    for complexity in ["simple", "medium", "complex"]:
        subset = [r for r in results if r["complexity"] == complexity]
        if subset:
            completed_subset = [r for r in subset if r["completed"]]
            by_complexity[complexity] = {
                "total": len(subset),
                "completed": len(completed_subset),
                "success_rate": round(len(completed_subset) / len(subset) * 100, 1),
                "avg_score": round(sum(r["forge_score"] for r in completed_subset) / len(completed_subset), 1) if completed_subset else 0,
                "avg_time_s": round(sum(r["elapsed_s"] for r in subset) / len(subset), 1),
            }

    failure_stages = {}
    for r in failed:
        stage = r.get("failure_stage", "unknown")
        failure_stages[stage] = failure_stages.get(stage, 0) + 1

    return {
        "total_apps": len(results),
        "completed": len(completed),
        "failed": len(failed),
        "overall_success_rate": round(len(completed) / len(results) * 100, 1),
        "avg_score": round(sum(r["forge_score"] for r in completed) / len(completed), 1) if completed else 0,
        "score_range": (
            min(r["forge_score"] for r in completed),
            max(r["forge_score"] for r in completed),
        ) if completed else (0, 0),
        "by_complexity": by_complexity,
        "failure_breakdown": failure_stages,
        "total_elapsed_s": round(sum(r["elapsed_s"] for r in results), 1),
        "avg_elapsed_s": round(sum(r["elapsed_s"] for r in results) / len(results), 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Comprehensive 20-App Validation Test")
    parser.add_argument("--provider", default="auto", help="LLM provider (auto/openai/gemini/groq)")
    parser.add_argument("--limit", type=int, default=len(TEST_APPS), help="Limit number of apps to test")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "="*70, flush=True)
    print("  COMPREHENSIVE 20-APP VALIDATION TEST", flush=True)
    print("  (frontend build/browser checks disabled -- see script docstring)", flush=True)
    print("="*70, flush=True)
    print(f"  Provider: {args.provider}", flush=True)
    print(f"  Total Apps: {min(args.limit, len(TEST_APPS))}", flush=True)
    print(f"  Start Time: {datetime.now(timezone.utc).isoformat()}", flush=True)
    print("="*70, flush=True)

    results = []
    for i, app_spec in enumerate(TEST_APPS[:args.limit], 1):
        print(f"\n[{i}/{min(args.limit, len(TEST_APPS))}]", end=" ", flush=True)
        result = _run_test(app_spec, provider=args.provider)
        results.append(result)

        status = "PASS" if result["completed"] else f"FAIL ({result['failure_stage']})"
        score_str = f"{result['forge_score']:.0f}" if result["completed"] else "0"
        print(f"\n  Result: {status} | Score: {score_str}/100 | Time: {result['elapsed_s']}s", flush=True)

        # Write incremental results after every app so a crash never loses progress
        partial = {
            "metadata": {
                "test_date": datetime.now(timezone.utc).isoformat(),
                "provider": args.provider,
                "platform": platform.system(),
                "frontend_checks": "disabled",
                "in_progress": True,
            },
            "summary": _compute_summary(results),
            "results": results,
        }
        RESULTS_FILE.write_text(json.dumps(partial, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = _compute_summary(results)

    output = {
        "metadata": {
            "test_date": datetime.now(timezone.utc).isoformat(),
            "provider": args.provider,
            "platform": platform.system(),
            "frontend_checks": "disabled",
            "in_progress": False,
        },
        "summary": summary,
        "results": results,
    }

    RESULTS_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "="*70, flush=True)
    print("  SUMMARY", flush=True)
    print("="*70, flush=True)
    print(f"  Overall Success Rate: {summary['overall_success_rate']}% ({summary['completed']}/{summary['total_apps']})", flush=True)
    print(f"  Avg Forge Score: {summary['avg_score']:.1f}/100", flush=True)
    print(f"  Total Time: {summary['total_elapsed_s']:.0f}s ({summary['avg_elapsed_s']:.1f}s avg per app)", flush=True)
    print(flush=True)

    if summary.get("by_complexity"):
        print("  BY COMPLEXITY:", flush=True)
        for complexity in ["simple", "medium", "complex"]:
            if complexity in summary["by_complexity"]:
                stats = summary["by_complexity"][complexity]
                print(f"    {complexity.upper():10} {stats['completed']:2}/{stats['total']:2} | {stats['success_rate']:5.1f}% | Score: {stats['avg_score']:6.1f} | Time: {stats['avg_time_s']:6.1f}s", flush=True)

    if summary.get("failure_breakdown"):
        print(flush=True)
        print("  FAILURE BREAKDOWN:", flush=True)
        for stage, count in sorted(summary["failure_breakdown"].items(), key=lambda x: x[1], reverse=True):
            print(f"    {stage:25} {count:2} failures", flush=True)

    print(flush=True)
    print(f"  Results saved to: {RESULTS_FILE}", flush=True)
    print("="*70, flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
