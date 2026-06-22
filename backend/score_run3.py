"""Vision score all 3 Run 3 apps."""
import sys, json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv; load_dotenv()

PROJECTS = ["note_taker_pro", "job_board_platform", "personal_finance_tracker"]
GENERATED_ROOT = Path(__file__).parent.parent / "generated_projects"

from app.runtime.playwright_runner import run_playwright_tests
from app.runtime.vision_validator import run_vision_validation

print(f"\n{'='*60}  RUN 3 VISION RE-SCORE  {'='*60}\n")
rows = []
for name in PROJECTS:
    project_path = str(GENERATED_ROOT / name)
    dist = GENERATED_ROOT / name / "dist"
    if not dist.exists():
        print(f"  {name}: no dist/ — skip"); rows.append({"name": name, "vision_score": None}); continue

    meta = GENERATED_ROOT / name / "metadata.json"
    arch = {}
    if meta.exists():
        try: arch = json.loads(meta.read_text(encoding="utf-8")).get("architecture", {})
        except: pass

    print(f"  {name}: capturing screenshots...")
    pw = run_playwright_tests(project_path, arch, capture_screenshots=True)
    if pw.skipped:
        print(f"    skipped: {pw.skip_reason}"); rows.append({"name": name, "vision_score": None}); continue

    print(f"    {len(pw.screenshots)} screenshots — scoring vision...")
    vr = run_vision_validation(project_path, arch, existing_screenshots=pw.screenshots)
    if vr.skipped:
        print(f"    vision skipped: {vr.skip_reason}"); rows.append({"name": name, "vision_score": None}); continue

    print(f"    Vision: {vr.ui_score}/100")
    for i in vr.issues[:3]:
        print(f"      [{i.severity}] {i.description[:80]}")
    rows.append({"name": name, "vision_score": vr.ui_score})

print(f"\n{'='*60}")
print(f"  {'App':<32} {'Vision':>8}")
print(f"  {'─'*32} {'─'*8}")
scored = [r for r in rows if r.get("vision_score") is not None]
for r in rows:
    v = f"{r['vision_score']}/100" if r.get("vision_score") is not None else "skip"
    print(f"  {r['name']:<32} {v:>8}")
if scored:
    avg = round(sum(r["vision_score"] for r in scored) / len(scored), 1)
    print(f"  {'─'*40}")
    print(f"  {'AVERAGE':<32} {avg:>5}/100")
    print(f"\n  Goal (>= 60): {'✅ MET' if avg >= 60 else f'❌ need +{60-avg:.0f}'}")
print(f"{'='*60}")
