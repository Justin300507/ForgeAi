"""
Frontend Quality Benchmark — V7.1

Generates 5 apps with the updated frontend prompt and measures:
  - Vision Score  (AI multimodal analysis of screenshots)
  - Forge Score   (composite backend + frontend score)
  - Frontend Build (did npm build succeed?)
  - Playwright    (did pages render without JS errors?)

Output:
  - Terminal table with all scores
  - benchmark/frontend_YYYYMMDD_HHMM/gallery.html  — visual gallery of screenshots
  - benchmark/frontend_YYYYMMDD_HHMM/results.json  — raw numbers

Usage:
  cd backend
  $env:PYTHONIOENCODING = "utf-8"
  .\\venv\\Scripts\\activate
  python benchmark_frontend.py [--provider auto]
"""
import sys
import os
import json
import base64
import time
import argparse
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

IDEAS_V75 = [
    "A job board platform with job listings, applications, employers, and candidate profiles",
    "A book library system with books, authors, members, borrowings, and reservations",
]

IDEAS = IDEAS_V75

BENCH_ROOT = Path(__file__).parent / "benchmark"


def _ascii(s: str) -> str:
    return str(s).encode("ascii", errors="replace").decode("ascii")


def run_benchmark(provider: str = "auto") -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = BENCH_ROOT / f"frontend_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"  FRONTEND QUALITY BENCHMARK — {stamp}")
    print(f"  Provider: {provider}")
    print(f"  Output:   {out_dir}")
    print(f"  Apps:     {len(IDEAS)}")
    print(f"{'='*70}\n")

    from app.services.v7_orchestrator import generate_project_v7
    from app.runtime.frontend_runner import FrontendRunner
    from app.services.frontend_fix_service import run_frontend_fix_loop
    from app.runtime.playwright_runner import run_playwright_tests
    from app.runtime.vision_validator import run_vision_validation
    from app.services.forge_score_service import calculate_forge_score

    results = []

    for idx, idea in enumerate(IDEAS, 1):
        print(f"\n{'─'*70}")
        print(f"  App {idx}/{len(IDEAS)}: {idea[:65]}")
        print(f"{'─'*70}")
        app_start = time.time()

        row = {
            "idx": idx,
            "idea": idea,
            "project_name": "",
            "project_path": "",
            "forge_score": 0,
            "vision_score": None,
            "build_passed": False,
            "playwright_passed": False,
            "runtime_passed": False,
            "validation_passed": False,
            "screenshots": [],
            "vision_issues": [],
            "error": None,
            "elapsed": 0,
        }

        try:
            # ── Step 1: Generate with V7 pipeline ──────────────────────────
            v7 = generate_project_v7(
                idea,
                provider=provider,
                run_improvement_cycle=False,
                skip_reviews=True,
            )
            project_path = v7.get("project_path", "")
            project_name = v7.get("project_name", f"app_{idx}")
            architecture = v7.get("architecture", {})
            validation = v7.get("validation", {}) or {}
            runtime_result = v7.get("runtime") or {}

            row["project_name"] = project_name
            row["project_path"] = project_path
            row["validation_passed"] = validation.get("passed", False)
            row["runtime_passed"] = bool(runtime_result.get("success"))
            row["forge_score"] = (v7.get("forge_score") or {}).get("score", 0)

            print(f"  Generated: {project_name}")
            print(f"  Validation: {'PASS' if row['validation_passed'] else 'FAIL'}")
            print(f"  Runtime:    {'PASS' if row['runtime_passed'] else 'FAIL'}")

            if not project_path or not os.path.isdir(project_path):
                row["error"] = "project_path missing or not a directory"
                results.append(row)
                continue

            # ── Step 2: Frontend Build ──────────────────────────────────────
            print(f"\n  --- Frontend Build ---")
            frontend_build_result = None
            try:
                runner = FrontendRunner()
                frontend_build_result = run_frontend_fix_loop(
                    project_path, runner, provider, max_attempts=4
                )
                if frontend_build_result.get("node_missing"):
                    print("  Node.js not found — build skipped")
                elif frontend_build_result.get("success"):
                    print("  Build: PASS")
                    row["build_passed"] = True
                else:
                    errs = frontend_build_result.get("errors", [])
                    print(f"  Build: FAIL ({len(errs)} errors)")
            except Exception as be:
                print(f"  Build error: {be}")
                frontend_build_result = {"success": False, "node_missing": False}

            # ── Step 3: Playwright Screenshots ─────────────────────────────
            playwright_result = None
            if row["build_passed"]:
                print(f"\n  --- Playwright ---")
                try:
                    playwright_result = run_playwright_tests(
                        project_path, architecture, capture_screenshots=True
                    )
                    if playwright_result.skipped:
                        print(f"  Playwright skipped: {playwright_result.skip_reason}")
                    elif playwright_result.success:
                        print(f"  Playwright: PASS ({playwright_result.pages_checked} pages)")
                        row["playwright_passed"] = True
                    else:
                        print(f"  Playwright: FAIL — blanks={playwright_result.blank_pages}")
                    row["screenshots"] = getattr(playwright_result, "screenshots", [])
                except Exception as pe:
                    print(f"  Playwright error: {pe}")

            # ── Step 4: Vision Validation ───────────────────────────────────
            vision_result = None
            if playwright_result and not getattr(playwright_result, "skipped", True):
                print(f"\n  --- Vision Validation ---")
                try:
                    existing_shots = getattr(playwright_result, "screenshots", [])
                    vision_result = run_vision_validation(
                        project_path, architecture,
                        existing_screenshots=existing_shots or None,
                    )
                    if vision_result.skipped:
                        print(f"  Vision skipped: {vision_result.skip_reason}")
                    else:
                        print(f"  Vision Score: {vision_result.ui_score}/100")
                        row["vision_score"] = vision_result.ui_score
                        row["vision_issues"] = [
                            {"severity": i.severity, "page": i.page, "description": i.description}
                            for i in vision_result.issues
                        ]
                except Exception as ve:
                    print(f"  Vision error: {ve}")

            # ── Step 5: Recompute Forge Score with vision ───────────────────
            forge = calculate_forge_score(
                validation,
                runtime_result,
                frontend_build_result,
                playwright_result,
                vision_result=vision_result,
            )
            row["forge_score"] = forge["score"]
            print(f"\n  Forge Score: {forge['score']}/100 ({forge['grade']})")

        except Exception as e:
            import traceback
            row["error"] = str(e)
            traceback.print_exc()

        row["elapsed"] = round(time.time() - app_start, 1)
        results.append(row)

    # ── Summary Table ───────────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print(f"  BENCHMARK RESULTS")
    print(f"{'='*70}")
    print(f"  {'App':<28} {'Vision':>7} {'Forge':>6} {'Build':>6} {'Runtime':>8}")
    print(f"  {'─'*28} {'─'*7} {'─'*6} {'─'*6} {'─'*8}")
    for r in results:
        vision_str = f"{r['vision_score']}/100" if r["vision_score"] is not None else "  skip"
        build_str  = "PASS" if r["build_passed"] else "FAIL"
        rt_str     = "PASS" if r["runtime_passed"] else "FAIL"
        name = _ascii(r["project_name"])[:28] or _ascii(r["idea"])[:28]
        print(f"  {name:<28} {vision_str:>7} {r['forge_score']:>5}/100 {build_str:>6} {rt_str:>8}")

    vision_scores = [r["vision_score"] for r in results if r["vision_score"] is not None]
    forge_scores  = [r["forge_score"] for r in results]

    avg_vision = round(sum(vision_scores) / len(vision_scores), 1) if vision_scores else None
    avg_forge  = round(sum(forge_scores) / len(forge_scores), 1)   if forge_scores  else 0
    builds_ok  = sum(1 for r in results if r["build_passed"])
    runtime_ok = sum(1 for r in results if r["runtime_passed"])

    print(f"  {'─'*58}")
    avg_v_str = f"{avg_vision}/100" if avg_vision is not None else "  skip"
    print(f"  {'AVERAGE':<28} {avg_v_str:>7} {avg_forge:>5}/100 {builds_ok}/{len(results)} builds  {runtime_ok}/{len(results)} runtime")

    goal_met = avg_vision is not None and avg_vision >= 60
    print(f"\n  Goal (Vision avg >= 60):  {'✅ MET' if goal_met else '❌ NOT YET'}")
    if not goal_met and avg_vision is not None:
        print(f"  Gap: need +{60 - avg_vision:.1f} more Vision points on average")

    print(f"{'='*70}")

    # ── Save JSON ───────────────────────────────────────────────────────────
    safe_results = []
    for r in results:
        sr = {k: v for k, v in r.items() if k != "screenshots"}
        sr["screenshot_count"] = len(r["screenshots"])
        safe_results.append(sr)

    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps({
        "timestamp": stamp,
        "provider": provider,
        "summary": {
            "avg_vision": avg_vision,
            "avg_forge": avg_forge,
            "builds_passed": builds_ok,
            "runtime_passed": runtime_ok,
            "goal_met": goal_met,
        },
        "apps": safe_results,
    }, indent=2), encoding="utf-8")
    print(f"\n  Results saved: {results_path}")

    # ── Generate Gallery HTML ───────────────────────────────────────────────
    gallery_path = out_dir / "gallery.html"
    _write_gallery(gallery_path, results, stamp, avg_vision, avg_forge)
    print(f"  Gallery:      {gallery_path}")


def _write_gallery(
    path: Path,
    results: list,
    stamp: str,
    avg_vision,
    avg_forge: float,
) -> None:
    cards = ""
    for r in results:
        vision_str = f"{r['vision_score']}/100" if r["vision_score"] is not None else "N/A"
        vision_color = (
            "#16a34a" if (r["vision_score"] or 0) >= 70
            else "#d97706" if (r["vision_score"] or 0) >= 40
            else "#dc2626"
        )
        forge_color = (
            "#16a34a" if r["forge_score"] >= 90
            else "#d97706" if r["forge_score"] >= 70
            else "#dc2626"
        )
        badges = ""
        if r["runtime_passed"]: badges += '<span class="badge green">Runtime PASS</span>'
        else:                    badges += '<span class="badge red">Runtime FAIL</span>'
        if r["build_passed"]:    badges += '<span class="badge green">Build PASS</span>'
        else:                    badges += '<span class="badge red">Build FAIL</span>'
        if r["error"]:           badges += f'<span class="badge red">Error</span>'

        issues_html = ""
        for issue in r.get("vision_issues", [])[:5]:
            cls = {"critical": "red", "warning": "orange", "info": "gray"}.get(issue["severity"], "gray")
            issues_html += f'<li class="{cls}">[{issue["severity"]}] {_ascii(issue["description"])[:100]}</li>'

        # Embed first screenshot if available
        screenshot_html = ""
        shots = r.get("screenshots", [])
        if shots:
            # shots may be base64 strings or dicts
            first = shots[0]
            if isinstance(first, dict):
                b64 = first.get("data", "") or first.get("screenshot", "")
            else:
                b64 = first
            if b64:
                screenshot_html = f'<img src="data:image/png;base64,{b64}" alt="screenshot" style="width:100%;border-radius:6px;margin-top:0.75rem;">'

        name = _ascii(r["project_name"] or r["idea"])
        idea = _ascii(r["idea"])
        cards += f"""
<div class="card">
  <h2>{name}</h2>
  <p class="idea">{idea}</p>
  <div class="scores">
    <div class="score-box" style="border-color:{vision_color}">
      <div class="score-label">Vision</div>
      <div class="score-val" style="color:{vision_color}">{vision_str}</div>
    </div>
    <div class="score-box" style="border-color:{forge_color}">
      <div class="score-label">Forge</div>
      <div class="score-val" style="color:{forge_color}">{r['forge_score']}/100</div>
    </div>
  </div>
  <div class="badges">{badges}</div>
  {screenshot_html}
  {"<ul class='issues'>" + issues_html + "</ul>" if issues_html else ""}
</div>
"""

    avg_v_display = f"{avg_vision}/100" if avg_vision is not None else "N/A"
    goal_str = "✅ Goal Met (Vision ≥ 60)" if (avg_vision or 0) >= 60 else f"❌ Vision avg {avg_v_display} — goal is 60+"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ForgeAI Frontend Benchmark — {stamp}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; background: #f1f5f9; color: #1e293b; }}
  header {{ background: #1e1b4b; color: #fff; padding: 1.5rem 2rem; }}
  header h1 {{ font-size: 1.4rem; }}
  header p {{ opacity: .7; margin-top: .25rem; font-size: .9rem; }}
  .summary {{ background: #fff; margin: 1.5rem 2rem .5rem; padding: 1rem 1.5rem;
              border-radius: 10px; display: flex; gap: 2rem; align-items: center;
              box-shadow: 0 1px 4px #0001; }}
  .summary .stat {{ text-align: center; }}
  .summary .stat .val {{ font-size: 1.8rem; font-weight: 700; color: #4f46e5; }}
  .summary .stat .lbl {{ font-size: .75rem; color: #64748b; text-transform: uppercase; }}
  .goal {{ margin: 0 2rem .75rem; font-weight: 600; font-size: 1rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px,1fr));
           gap: 1.25rem; padding: 0.5rem 2rem 2rem; }}
  .card {{ background: #fff; border-radius: 12px; padding: 1.25rem;
           box-shadow: 0 1px 6px #0001; }}
  .card h2 {{ font-size: 1.05rem; color: #1e293b; margin-bottom: .25rem; }}
  .idea {{ font-size: .8rem; color: #64748b; margin-bottom: .75rem; }}
  .scores {{ display: flex; gap: .75rem; margin-bottom: .75rem; }}
  .score-box {{ border: 2px solid #e2e8f0; border-radius: 8px; padding: .5rem .75rem;
                text-align: center; min-width: 80px; }}
  .score-label {{ font-size: .65rem; color: #94a3b8; text-transform: uppercase; }}
  .score-val {{ font-size: 1.3rem; font-weight: 700; }}
  .badges {{ display: flex; flex-wrap: wrap; gap: .35rem; margin-bottom: .5rem; }}
  .badge {{ padding: .2rem .55rem; border-radius: 999px; font-size: .7rem; font-weight: 600; }}
  .badge.green {{ background: #dcfce7; color: #16a34a; }}
  .badge.red   {{ background: #fee2e2; color: #dc2626; }}
  .badge.orange {{ background: #fef3c7; color: #d97706; }}
  ul.issues {{ margin-top: .75rem; padding-left: 1rem; font-size: .78rem; color: #475569; }}
  ul.issues li {{ margin-bottom: .25rem; }}
  li.red {{ color: #dc2626; }}
  li.orange {{ color: #d97706; }}
  li.gray {{ color: #64748b; }}
</style>
</head>
<body>
<header>
  <h1>ForgeAI Frontend Benchmark</h1>
  <p>Generated {stamp} · V7.1 Frontend Quality Revolution</p>
</header>

<div class="summary">
  <div class="stat">
    <div class="val">{avg_v_display}</div>
    <div class="lbl">Avg Vision</div>
  </div>
  <div class="stat">
    <div class="val">{avg_forge}/100</div>
    <div class="lbl">Avg Forge</div>
  </div>
  <div class="stat">
    <div class="val">{len(results)}</div>
    <div class="lbl">Apps</div>
  </div>
</div>
<p class="goal">{goal_str}</p>

<div class="grid">
{cards}
</div>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ForgeAI Frontend Quality Benchmark")
    parser.add_argument("--provider", default="auto", help="LLM provider (default: auto)")
    args = parser.parse_args()

    # Add backend to path so imports work
    sys.path.insert(0, str(Path(__file__).parent))

    from dotenv import load_dotenv
    load_dotenv()

    run_benchmark(provider=args.provider)
