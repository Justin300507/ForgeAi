"""
Quick E2E test: generate a Railway todo app and report the score.
Run: python run_todo_test.py
"""
import sys, os, json, time

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Load .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

IDEA = "A todo app with user login, task creation, completion tracking, and due dates"

print(f"\n{'='*70}")
print(f"  FORGEAI TODO APP TEST — targeting 100/100")
print(f"  Idea: {IDEA}")
print(f"{'='*70}\n")

start = time.time()

try:
    from app.services.v7_orchestrator import generate_project_v7

    result = generate_project_v7(
        idea=IDEA,
        provider="auto",
        run_improvement_cycle=False,
        skip_reviews=True,
        frontend_target="web",
    )
except Exception as exc:
    print(f"\n[FATAL] Pipeline crashed: {exc}")
    import traceback; traceback.print_exc()
    sys.exit(1)

elapsed = round(time.time() - start, 1)

# ── Extract key metrics ────────────────────────────────────────────────────────
project_name = result.get("project_name", "?")
project_path = result.get("project_path", "?")
forge_score  = result.get("forge_score")
validation   = result.get("validation") or {}
runtime      = result.get("runtime") or {}

if isinstance(forge_score, dict):
    score_val = forge_score.get("score", 0)
    grade     = forge_score.get("grade", "?")
else:
    score_val = forge_score or 0
    grade     = "A" if score_val == 100 else "F" if score_val < 60 else "C"

static_passed  = bool(validation.get("passed"))
runtime_passed = bool(runtime.get("success"))
static_errors  = validation.get("errors", [])
runtime_errors = runtime.get("errors", []) if isinstance(runtime.get("errors"), list) else []

print(f"\n{'='*70}")
print(f"  RESULT SUMMARY")
print(f"{'='*70}")
print(f"  Project:        {project_name}")
print(f"  Forge Score:    {score_val}/100  (Grade: {grade})")
print(f"  Static passed:  {static_passed}")
print(f"  Runtime passed: {runtime_passed}")
print(f"  Time:           {elapsed}s")

if static_errors:
    print(f"\n  Static errors ({len(static_errors)}):")
    for e in static_errors[:20]:
        if isinstance(e, dict):
            print(f"    [{e.get('validator','?')}] {e.get('file','?')}: {str(e.get('message',''))[:100]}")
        else:
            print(f"    {str(e)[:120]}")

if runtime_errors:
    print(f"\n  Runtime errors ({len(runtime_errors)}):")
    for e in runtime_errors[:10]:
        print(f"    {str(e)[:120]}")

if not static_errors and not runtime_errors:
    print(f"\n  No errors! Score breakdown:")
    if isinstance(forge_score, dict):
        for k, v in forge_score.items():
            if k not in ("score", "grade"):
                print(f"    {k}: {v}")

# Save result
out_file = os.path.join(os.path.dirname(__file__), "todo_test_result.json")
with open(out_file, "w", encoding="utf-8") as f:
    def _safe(obj):
        if isinstance(obj, dict): return {k: _safe(v) for k,v in obj.items()}
        if isinstance(obj, list): return [_safe(i) for i in obj]
        try: json.dumps(obj); return obj
        except: return str(obj)
    json.dump(_safe(result), f, indent=2)

print(f"\n  Full result saved to: todo_test_result.json")
print(f"{'='*70}\n")

sys.exit(0 if score_val == 100 else 1)
