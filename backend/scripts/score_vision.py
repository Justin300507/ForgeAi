"""
Quick Vision Scorer — runs vision validation on already-built projects.
No regeneration. Uses existing dist/ folders and takes fresh screenshots.

Usage:
  cd backend
  $env:PYTHONIOENCODING = "utf-8"
  .\\venv\\Scripts\\activate
  python score_vision.py
"""
import sys
import json
import os
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv
load_dotenv()

PROJECTS = [
    "taskmaster",
    "blog_platform",
    "recipe_manager",
    "expense_tracker_app",
    "inventory_system",
]

GENERATED_ROOT = _BACKEND_ROOT.parent / "generated_projects"


def main():
    from app.runtime.playwright_runner import run_playwright_tests
    from app.runtime.vision_validator import run_vision_validation

    print(f"\n{'='*60}")
    print(f"  VISION SCORER — Run 2 projects")
    print(f"{'='*60}\n")

    rows = []
    for name in PROJECTS:
        project_path = str(GENERATED_ROOT / name)
        dist = GENERATED_ROOT / name / "dist"

        if not dist.exists():
            print(f"  {name}: no dist/ — skipping (build didn't produce output)")
            rows.append({"name": name, "vision_score": None, "skip_reason": "no dist/"})
            continue

        # Load architecture from metadata
        meta_path = GENERATED_ROOT / name / "metadata.json"
        architecture = {}
        if meta_path.exists():
            try:
                architecture = json.loads(meta_path.read_text(encoding="utf-8")).get("architecture", {})
            except Exception:
                pass

        print(f"  {name}: taking screenshots...")
        try:
            playwright_result = run_playwright_tests(
                project_path, architecture, capture_screenshots=True
            )
            if playwright_result.skipped:
                print(f"    Playwright skipped: {playwright_result.skip_reason}")
                rows.append({"name": name, "vision_score": None, "skip_reason": playwright_result.skip_reason})
                continue

            shots = playwright_result.screenshots
            print(f"    {len(shots)} screenshots captured — running vision...")

            vision_result = run_vision_validation(
                project_path, architecture, existing_screenshots=shots
            )

            if vision_result.skipped:
                print(f"    Vision skipped: {vision_result.skip_reason}")
                rows.append({"name": name, "vision_score": None, "skip_reason": vision_result.skip_reason})
            else:
                print(f"    Vision Score: {vision_result.ui_score}/100")
                for issue in vision_result.issues[:3]:
                    print(f"      [{issue.severity}] {issue.description[:80]}")
                rows.append({"name": name, "vision_score": vision_result.ui_score, "issues": [
                    {"severity": i.severity, "description": i.description}
                    for i in vision_result.issues
                ]})

        except Exception as e:
            print(f"    Error: {e}")
            rows.append({"name": name, "vision_score": None, "skip_reason": str(e)})

    # Summary table
    print(f"\n{'='*60}")
    print(f"  VISION RESULTS")
    print(f"{'='*60}")
    print(f"  {'App':<28} {'Vision':>8}")
    print(f"  {'─'*28} {'─'*8}")
    scored = [r for r in rows if r.get("vision_score") is not None]
    for r in rows:
        v = f"{r['vision_score']}/100" if r.get("vision_score") is not None else "skip"
        print(f"  {r['name']:<28} {v:>8}")

    if scored:
        avg = round(sum(r["vision_score"] for r in scored) / len(scored), 1)
        print(f"  {'─'*36}")
        print(f"  {'AVERAGE':<28} {avg:>5}/100")
        goal = avg >= 60
        print(f"\n  Goal (avg Vision >= 60): {'✅ MET' if goal else f'❌ NOT YET — need +{60-avg:.0f} more points'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
