"""
V11 Autonomous Deployment Benchmark

Tests 2 apps end-to-end:
  Generate → Validate → Deploy (Railway) → Health Check

Checks per app:
  ✓ Static validation passed
  ✓ Runtime passed
  ✓ Docker deployed
  ✓ URL returned
  ✓ Health check passed
  ✓ /health responds 2xx
  ✓ / responds 2xx

Usage:
  cd backend
  $env:PYTHONIOENCODING = "utf-8"
  .\\venv\\Scripts\\activate
  python benchmark_v11.py

Cost control: skip_reviews=True, run_improvement_cycle=False
"""
import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Minimal ideas — avoids expensive planner complexity
IDEAS = [
    "A job board with job listings, employers, and applicants",
    "A library system with books, members, and borrowing records",
]

BENCH_ROOT = _BACKEND_ROOT / "benchmark"
RUN_DIR = BENCH_ROOT / f"v11_{datetime.now().strftime('%Y%m%d_%H%M')}"
RUN_DIR.mkdir(parents=True, exist_ok=True)


def run_benchmark():
    # Lazy import — avoids loading all services before env is set up
    from app.services.v11_orchestrator import generate_project_v11

    results = []
    print("\n" + "=" * 70)
    print("  V11 AUTONOMOUS DEPLOYMENT BENCHMARK")
    print(f"  Apps: {len(IDEAS)} | Mode: skip_reviews | Provider: auto → railway")
    print("=" * 70)

    for idx, idea in enumerate(IDEAS, 1):
        print(f"\n{'─'*70}")
        print(f"  [{idx}/{len(IDEAS)}] {idea}")
        print(f"{'─'*70}")
        t0 = time.time()
        result = {"idea": idea, "error": None}

        try:
            raw = generate_project_v11(
                idea=idea,
                provider="auto",
                deploy_provider="railway",
                run_improvement_cycle=False,
                skip_reviews=True,
            )

            v11 = raw.get("v11_extras", {})
            deployment = v11.get("deployment", {})
            health = v11.get("health_report", {})
            forge = raw.get("forge_score", {})
            runtime = raw.get("runtime") or {}

            result.update({
                "project_name":     raw.get("project_name", ""),
                "static_passed":    bool((raw.get("validation") or {}).get("passed")),
                "runtime_passed":   bool(runtime.get("success")),
                "deployed":         bool(deployment.get("success")),
                "url":              deployment.get("url"),
                "deploy_error":     deployment.get("error"),
                "health_passed":    bool(health.get("success")),
                "health_score":     health.get("score"),
                "health_checks":    health.get("checks", {}),
                "forge_score":      forge.get("score", 0),
                "forge_grade":      forge.get("grade", "F"),
                "deploy_score":     (v11.get("deployment_score") or {}).get("score", 0),
                "elapsed":          round(time.time() - t0, 1),
            })

        except Exception as exc:
            result["error"] = str(exc)
            result["elapsed"] = round(time.time() - t0, 1)

        results.append(result)
        _print_result(result)

    _print_summary(results)
    _save(results)
    return results


def _print_result(r: dict):
    ok = lambda v: "✓" if v else "✗"
    print(f"\n  Project      : {r.get('project_name', '—')}")
    print(f"  Static       : {ok(r.get('static_passed'))}  Runtime: {ok(r.get('runtime_passed'))}")
    print(f"  Deployed     : {ok(r.get('deployed'))}  Health:  {ok(r.get('health_passed'))}")
    if r.get("url"):
        print(f"  Live URL     : {r['url']}")
    if r.get("deploy_error") and not r.get("deployed"):
        print(f"  Deploy Error : {r['deploy_error'][:120]}")
    if r.get("health_checks"):
        for path, check in r["health_checks"].items():
            status = check.get("status_code", check.get("error", "?"))
            flag = ok(check.get("ok"))
            print(f"  {flag} {path:<12} {status}")
    print(f"  Forge Score  : {r.get('forge_score', 0)}/100 ({r.get('forge_grade', 'F')})  "
          f"Deploy Score: {r.get('deploy_score', 0)}/100  "
          f"Time: {r.get('elapsed', 0)}s")
    if r.get("error"):
        print(f"  ERROR: {r['error'][:200]}")


def _print_summary(results: list):
    print("\n" + "=" * 70)
    print("  V11 BENCHMARK SUMMARY")
    print("=" * 70)
    total = len(results)
    deployed = sum(1 for r in results if r.get("deployed"))
    healthy  = sum(1 for r in results if r.get("health_passed"))
    runtime  = sum(1 for r in results if r.get("runtime_passed"))
    scores   = [r.get("forge_score", 0) for r in results]
    avg      = round(sum(scores) / len(scores), 1) if scores else 0

    print(f"  Apps:          {total}")
    print(f"  Runtime Pass:  {runtime}/{total}")
    print(f"  Deployed:      {deployed}/{total}")
    print(f"  Health Pass:   {healthy}/{total}")
    print(f"  Avg Forge:     {avg}/100")
    print()
    for r in results:
        url = r.get("url") or "—"
        status = "LIVE" if r.get("health_passed") else ("DEPLOYED" if r.get("deployed") else "FAILED")
        print(f"  {status:<8}  {r.get('project_name', r['idea'][:30]):<30}  {url}")
    print("=" * 70)
    print(f"\n  Results saved to: {RUN_DIR / 'results.json'}")


def _save(results: list):
    (RUN_DIR / "results.json").write_text(
        json.dumps({"run": datetime.now().isoformat(), "results": results}, indent=2)
    )


if __name__ == "__main__":
    run_benchmark()
