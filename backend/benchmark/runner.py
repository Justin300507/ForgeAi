"""
ForgeAI V3 Benchmark Runner

Usage (from backend/ with venv active):
    python benchmark/runner.py                     # run all 50 apps
    python benchmark/runner.py --n 10              # first 10 ideas
    python benchmark/runner.py --n 10 --offset 20  # ideas 20-30
    python benchmark/runner.py --label "after-fix" # tag the run
    python benchmark/runner.py --provider groq      # change LLM provider

Results are saved to benchmark/results.json and compared against the
previous run to detect regressions.
"""
import argparse
import os
import sys
import traceback

# Force UTF-8 output on Windows so checkmarks/crosses don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Run from the backend/ directory
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, os.getcwd())

from app.services.project_service import generate_project
from benchmark.ideas import BENCHMARK_IDEAS
from benchmark.tracker import record_run, compare_to_baseline, print_summary, print_regression_report


def run_benchmark(
    ideas: list[str],
    provider: str = "cerebras",
    label: str = "",
) -> list[dict]:
    results = []

    for idx, idea in enumerate(ideas, 1):
        print(f"\n{'=' * 60}")
        print(f"BENCHMARK {idx}/{len(ideas)}: {idea[:70]}")
        print("=" * 60)

        result_entry = {
            "idea": idea,
            "score": 0,
            "grade": "F",
            "validation_passed": False,
            "runtime_passed": False,
            "frontend_build_passed": False,
            "frontend_build_skipped": False,
            "errors": [],
            "error": None,
        }

        try:
            result = generate_project(idea, provider=provider)
            score_data = result["forge_score"]
            validation = result["validation"]
            runtime = result.get("runtime") or {}
            fb = result.get("frontend_build") or {}

            frontend_skipped = fb.get("node_missing", True)

            result_entry.update({
                "score": score_data["score"],
                "grade": score_data["grade"],
                "validation_passed": validation["passed"],
                "runtime_passed": runtime.get("success", False),
                "frontend_build_passed": fb.get("success", False) if not frontend_skipped else None,
                "frontend_build_skipped": frontend_skipped,
                "frontend_build_errors": fb.get("errors", [])[:5],
                "errors": validation["errors"][:10],
                "project_path": result.get("project_path"),
            })

            tag = "✓" if score_data["score"] == 100 else "✗"
            fb_tag = "(build skipped)" if frontend_skipped else ("build ✓" if fb.get("success") else "build ✗")
            print(f"\n  {tag} Score: {score_data['score']}/100 ({score_data['grade']})  {fb_tag}")

        except Exception as e:
            result_entry["error"] = str(e)
            print(f"\n  ✗ FAILED: {e}")
            traceback.print_exc()

        results.append(result_entry)

    return results


def print_per_app_table(results: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("PER-APP RESULTS")
    print("=" * 60)
    for r in results:
        score = r["score"]
        idea_short = r["idea"][:55]
        fb = "?" if r["frontend_build_skipped"] else ("✓" if r["frontend_build_passed"] else "✗")
        err = f"  ERROR: {r['error']}" if r.get("error") else ""
        print(f"  [{score:3d}] [build:{fb}] {idea_short}{err}")


def main():
    parser = argparse.ArgumentParser(description="ForgeAI V3 Benchmark Runner")
    parser.add_argument("--n", type=int, default=len(BENCHMARK_IDEAS), help="Number of apps to test")
    parser.add_argument("--offset", type=int, default=0, help="Start index into idea list")
    parser.add_argument("--provider", type=str, default="cerebras", help="LLM provider")
    parser.add_argument("--label", type=str, default="", help="Label for this run")
    args = parser.parse_args()

    ideas = BENCHMARK_IDEAS[args.offset: args.offset + args.n]
    print(f"\nForgeAI V3 Benchmark — {len(ideas)} apps — provider: {args.provider}")

    results = run_benchmark(ideas, provider=args.provider, label=args.label)

    print_per_app_table(results)

    run = record_run(results, provider=args.provider, label=args.label)
    print_summary(run)

    comparison = compare_to_baseline(run)
    print_regression_report(comparison)

    # Exit 1 if mean score dropped vs baseline
    if comparison and comparison["regressions"]:
        print("\nRegressions detected — exit 1")
        sys.exit(1)


if __name__ == "__main__":
    main()
