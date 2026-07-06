"""
Validate-only script: runs static + runtime check on an existing generated project.
Zero LLM calls — deterministic only. Reports forge score.

Usage:
  python validate_only.py [project_path]
  python validate_only.py  # defaults to taskflow_pro
"""
import sys, os, json, time
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND_ROOT, ".env"))

PROJECT = sys.argv[1] if len(sys.argv) > 1 else "taskflow_pro"
PROJECT_PATH = os.path.join(_BACKEND_ROOT, "..", "generated_projects", PROJECT)
PROJECT_PATH = os.path.abspath(PROJECT_PATH)

if not os.path.exists(PROJECT_PATH):
    print(f"ERROR: Project not found at {PROJECT_PATH}")
    sys.exit(1)

print(f"\n{'='*60}")
print(f"  VALIDATE-ONLY — {PROJECT}")
print(f"  Path: {PROJECT_PATH}")
print(f"{'='*60}\n")

# ── Step 1: Apply deterministic patches (no LLM) ──────────────────────────
print("=== APPLYING DETERMINISTIC PATCHES ===")
from app.services.deterministic_patcher import run_deterministic_patches
from app.services.database_patcher import patch_database_py
n = run_deterministic_patches(PROJECT_PATH)
patch_database_py(PROJECT_PATH)
print(f"  Patched {n} files")

# ── Step 2: Static validation ───────────────────────────────────────────────
print("\n=== STATIC VALIDATION ===")
from app.services.validator_service import validate_project
validation = validate_project(PROJECT_PATH)
print(f"  Result: {'PASS' if validation['passed'] else 'FAIL'} — {len(validation['errors'])} errors")
if validation['errors']:
    for e in validation['errors'][:15]:
        print(f"    {e[:120]}")

# ── Step 3: Runtime validation (starts uvicorn, no LLM) ────────────────────
if not validation['passed']:
    print("\n  Static failed — skipping runtime")
    sys.exit(1)

print("\n=== RUNTIME VALIDATION ===")
start = time.time()
from app.services.runtime_validator_service import validate_runtime
# Load architecture from metadata
meta_file = os.path.join(PROJECT_PATH, "metadata.json")
architecture = {}
if os.path.exists(meta_file):
    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
    architecture = meta.get("architecture", {})

runtime = validate_runtime(PROJECT_PATH, architecture=architecture)
elapsed = round(time.time() - start, 1)
print(f"  Runtime: {'PASS' if runtime.get('success') else 'FAIL'} ({elapsed}s)")

if runtime.get("behavioral_issues"):
    print(f"  Timeouts/errors:")
    for i in runtime["behavioral_issues"]:
        print(f"    {i['method']} {i['path']}: {i['issue'][:80]}")

journey = runtime.get("journey") or {}
if journey and not journey.get("skipped"):
    icon = "PASS" if journey.get("success") else "FAIL"
    print(f"  Journey: {icon} — {journey.get('steps_passed')}/{journey.get('steps_passed',0)+journey.get('steps_failed',0)} steps")

# ── Step 4: Forge score ─────────────────────────────────────────────────────
from app.services.forge_score_service import calculate_forge_score
score = calculate_forge_score(validation, runtime)
print(f"\n{'='*60}")
print(f"  FORGE SCORE: {score['score']}/100 (Grade: {score['grade']})")
print(f"{'='*60}\n")

sys.exit(0 if score['score'] == 100 else 1)
