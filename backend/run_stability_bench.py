"""
Stability Benchmark — run the same app idea N times in a row.

Answers the key question: is the pipeline reliably producing working apps,
or is success random?  A mature pipeline should pass 5/5 for a simple idea.

Usage:
    python run_stability_bench.py
    python run_stability_bench.py --idea "todo app" --runs 5
    python run_stability_bench.py --idea "gym tracker" --runs 10 --deploy none
    python run_stability_bench.py --no-deploy          # skip Railway/Cloudflare

Output:
    Run  Runtime  Deploy  Score  Time    LLM  Repairs  Result
    1    ✅       ✅      87     63s     12   3        PASS
    2    ✅       ✅      91     58s     11   2        PASS
    3    ❌       -       42     74s     18   9        FAIL (runtime)
    4    ✅       ✅      89     61s     13   3        PASS
    5    ✅       ✅      93     55s     10   2        PASS

    Pass rate:  4/5  (80%)
    Avg score:  76.4
    Avg time:   62.2s
    Avg LLM:    12.8 calls
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ForgeAI stability benchmark")
    p.add_argument("--idea", default="todo app",
                   help="App idea to repeat (default: 'todo app')")
    p.add_argument("--runs", type=int, default=5,
                   help="Number of consecutive runs (default: 5)")
    p.add_argument("--provider", default="auto",
                   help="LLM provider (default: auto)")
    p.add_argument("--deploy", default="none",
                   choices=["none", "cloudflare", "railway", "both"],
                   help="Deploy target (default: none — generation only)")
    p.add_argument("--no-deploy", action="store_true",
                   help="Shorthand for --deploy none")
    p.add_argument("--output", default="",
                   help="Save JSON results to this file")
    return p.parse_args()


def _extract_llm_calls(result: dict) -> int:
    """Pull total LLM call count from v6_score or cost summary."""
    # Try the LLM counter added to v6_orchestrator
    gen = result.get("generation", {})
    v6 = result.get("v6_score") or gen
    # Fall back to counting from cost tracker tokens
    return gen.get("llm_calls", 0)


def _run_one(idea: str, provider: str, deploy_to: str, run_idx: int) -> dict:
    """Run one V14 generation and return a summary dict."""
    from app.services.v14_orchestrator import generate_project_v14

    t0 = time.time()
    try:
        result = generate_project_v14(
            idea=idea,
            provider=provider,
            deploy_to=deploy_to,
            skip_reviews=True,
        )
    except Exception as exc:
        elapsed = round(time.time() - t0, 1)
        print(f"  [run {run_idx}] EXCEPTION: {exc}")
        return {
            "run": run_idx,
            "runtime_passed": False,
            "deploy_passed": False,
            "score": 0,
            "time_s": elapsed,
            "llm_calls": 0,
            "repairs": 0,
            "error": str(exc),
        }

    elapsed = round(time.time() - t0, 1)
    gen = result.get("generation", {})
    report = result.get("report", {})

    runtime_passed = bool(gen.get("runtime_passed"))
    deploy_passed = bool(report.get("frontend_url") or report.get("backend_url")) if deploy_to != "none" else None

    raw_score = gen.get("forge_score") or report.get("forge_score") or 0
    score = raw_score.get("score") if isinstance(raw_score, dict) else int(raw_score or 0)

    # Extract repair count from v6_score collab or from fix_attempts
    repairs = result.get("fix_attempts", 0)

    # LLM calls: sum from the printed counter (best effort)
    llm_calls = (
        result.get("llm_calls_total")
        or result.get("generation", {}).get("llm_calls", 0)
        or 0
    )

    return {
        "run": run_idx,
        "runtime_passed": runtime_passed,
        "deploy_passed": deploy_passed,
        "score": score,
        "time_s": elapsed,
        "llm_calls": llm_calls,
        "repairs": repairs,
        "project_name": gen.get("project_name", ""),
        "frontend_url": report.get("frontend_url"),
        "github_url": report.get("github_url"),
    }


def _icon(passed: bool | None) -> str:
    if passed is None:
        return "-"
    return "✅" if passed else "❌"


def _print_table(rows: list[dict], deploy_to: str) -> None:
    show_deploy = deploy_to != "none"
    header = f"{'Run':<5} {'Runtime':<9} {'Deploy':<8} {'Score':<7} {'Time':<8} {'Repairs':<9} {'Result'}"
    if not show_deploy:
        header = f"{'Run':<5} {'Runtime':<9} {'Score':<7} {'Time':<8} {'Repairs':<9} {'Result'}"

    print(f"\n{'─' * len(header)}")
    print(header)
    print(f"{'─' * len(header)}")

    for row in rows:
        rt = _icon(row["runtime_passed"])
        dp = _icon(row["deploy_passed"])
        result_label = "PASS" if row["runtime_passed"] else "FAIL"
        if not row["runtime_passed"] and row.get("error"):
            result_label = "ERROR"

        if show_deploy:
            print(
                f"{row['run']:<5} {rt:<9} {dp:<8} {row['score']:<7} "
                f"{row['time_s']:<8} {row['repairs']:<9} {result_label}"
            )
        else:
            print(
                f"{row['run']:<5} {rt:<9} {row['score']:<7} "
                f"{row['time_s']:<8} {row['repairs']:<9} {result_label}"
            )

    print(f"{'─' * len(header)}")


def _print_summary(rows: list[dict], idea: str, deploy_to: str) -> None:
    total = len(rows)
    passed = sum(1 for r in rows if r["runtime_passed"])
    scores = [r["score"] for r in rows]
    times = [r["time_s"] for r in rows]
    repairs_list = [r["repairs"] for r in rows]

    avg_score = round(sum(scores) / total, 1) if scores else 0
    avg_time = round(sum(times) / total, 1) if times else 0
    avg_repairs = round(sum(repairs_list) / total, 1) if repairs_list else 0

    print(f"\n  Idea:         '{idea}'")
    print(f"  Runs:         {total}")
    print(f"  Pass rate:    {passed}/{total}  ({round(passed/total*100)}%)")
    print(f"  Avg score:    {avg_score}/100")
    print(f"  Avg time:     {avg_time}s")
    print(f"  Avg repairs:  {avg_repairs}")

    # Stability verdict
    if passed == total:
        print(f"\n  ✅ STABLE — {passed}/{total} runs passed")
        if total >= 5:
            print(f"     Pipeline is ready to move to more complex apps.")
    elif passed >= total * 0.8:
        print(f"\n  ⚠️  MOSTLY STABLE — {passed}/{total} runs passed (target: {total}/{total})")
        print(f"     Investigate the {total - passed} failure(s) before moving on.")
    else:
        print(f"\n  ❌ UNSTABLE — only {passed}/{total} runs passed")
        print(f"     Fix the failure pattern before expanding to complex apps.")


def main() -> None:
    args = _parse_args()
    deploy_to = "none" if args.no_deploy else args.deploy

    # Must run from backend/ so imports work
    backend_dir = Path(__file__).parent
    sys.path.insert(0, str(backend_dir))
    os.chdir(backend_dir)

    from dotenv import load_dotenv
    load_dotenv()

    idea = args.idea
    n = args.runs

    print(f"\n{'=' * 60}")
    print(f"  STABILITY BENCHMARK")
    print(f"  Idea:    '{idea}'")
    print(f"  Runs:    {n}")
    print(f"  Deploy:  {deploy_to}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    rows: list[dict] = []

    for i in range(1, n + 1):
        print(f"\n{'#' * 60}")
        print(f"# RUN {i}/{n}")
        print(f"{'#' * 60}")

        row = _run_one(idea, args.provider, deploy_to, i)
        rows.append(row)

        # Print intermediate result so you can watch live
        status = "PASS" if row["runtime_passed"] else "FAIL"
        print(f"\n  ── RUN {i} RESULT: {status}  score={row['score']}  time={row['time_s']}s ──")

    # Final table
    _print_table(rows, deploy_to)
    _print_summary(rows, idea, deploy_to)

    # Save results
    out_path = args.output or f"stability_{idea.replace(' ', '_')}_{n}runs_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "idea": idea,
                "runs": n,
                "deploy_to": deploy_to,
                "timestamp": datetime.now().isoformat(),
                "rows": rows,
                "summary": {
                    "pass_rate": sum(1 for r in rows if r["runtime_passed"]) / n,
                    "avg_score": round(sum(r["score"] for r in rows) / n, 1),
                    "avg_time_s": round(sum(r["time_s"] for r in rows) / n, 1),
                },
            }, f, indent=2)
        print(f"\n  Results saved → {out_path}")
    except Exception as e:
        print(f"  (Could not save results: {e})")


if __name__ == "__main__":
    main()
