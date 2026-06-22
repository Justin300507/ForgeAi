"""Re-run runtime validation against specific projects and print full traceback."""
import sys, json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from app.runtime.backend_runner import BackendRunner

PROJECTS = ["expense_tracker_app", "inventory_system"]
GENERATED_ROOT = Path(__file__).parent.parent / "generated_projects"

for name in PROJECTS:
    project_path = str(GENERATED_ROOT / name)
    backend_path = str(GENERATED_ROOT / name)
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    meta = GENERATED_ROOT / name / "metadata.json"
    arch = {}
    if meta.exists():
        try: arch = json.loads(meta.read_text(encoding="utf-8")).get("architecture", {})
        except: pass

    runner = BackendRunner()
    result = runner.run(backend_path, architecture=arch)
    print(f"  success: {result.success}")
    print(f"  exit_code: {result.exit_code}")
    if result.behavioral_issues:
        print(f"\n  BEHAVIORAL ISSUES:")
        for issue in result.behavioral_issues:
            print(f"    {issue}")
    if result.stderr:
        print(f"\n  STDERR (last 60 lines):")
        for line in result.stderr.splitlines()[-60:]:
            print(f"    {line}")
    if result.stdout:
        print(f"\n  STDOUT (last 20 lines):")
        for line in result.stdout.splitlines()[-20:]:
            print(f"    {line}")
