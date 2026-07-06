"""Save Playwright screenshots from run 2 projects to disk for visual inspection."""
import sys, json, base64
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
from dotenv import load_dotenv
load_dotenv()

PROJECTS = ["taskmaster", "blog_platform", "recipe_manager", "expense_tracker_app", "inventory_system"]
GENERATED_ROOT = _BACKEND_ROOT.parent / "generated_projects"
OUT = _BACKEND_ROOT / "benchmark" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

from app.runtime.playwright_runner import run_playwright_tests

for name in PROJECTS:
    project_path = str(GENERATED_ROOT / name)
    dist = GENERATED_ROOT / name / "dist"
    if not dist.exists():
        print(f"  {name}: no dist/ — skip")
        continue

    meta = GENERATED_ROOT / name / "metadata.json"
    arch = {}
    if meta.exists():
        try: arch = json.loads(meta.read_text(encoding="utf-8")).get("architecture", {})
        except: pass

    print(f"  {name}: capturing...")
    result = run_playwright_tests(project_path, arch, capture_screenshots=True)
    if result.skipped:
        print(f"    skipped: {result.skip_reason}")
        continue

    for shot in result.screenshots:
        route = shot["route"].strip("/").replace("/", "_") or "root"
        out_file = OUT / f"{name}__{route}.png"
        out_file.write_bytes(base64.b64decode(shot["png_b64"]))
        print(f"    saved: {out_file.name}")

print(f"\nScreenshots saved to: {OUT}")
