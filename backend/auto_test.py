"""Autonomous test runner - runs one generation and prints results."""
import sys
import os
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.project_service import generate_project

print("Starting test generation for todo app...")
try:
    result = generate_project("A todo app with tasks and user authentication", provider="auto")
    score = result["forge_score"]
    validation = result["validation"]
    runtime = result.get("runtime") or {}
    journey = runtime.get("journey") or {}

    print(f"\n{'='*50}")
    print(f"FORGE SCORE: {score['score']}/100 ({score['grade']})")
    print(f"Validation: {'PASS' if validation['passed'] else 'FAIL'}")
    print(f"Runtime: {'PASS' if runtime.get('success') else 'FAIL'}")

    errors = validation.get("errors", [])
    print(f"\nValidation Errors ({len(errors)}):")
    for e in errors[:30]:
        print(f"  - {e}")

    if journey:
        print(f"\nJourney: {'PASS' if journey.get('success') else 'FAIL'}")
        for step in journey.get("steps", []):
            icon = "OK  " if step["passed"] else "FAIL"
            print(f"  [{icon}] {step['name']}: {step['detail']}")

    if runtime.get("stderr"):
        stderr_tail = str(runtime["stderr"])[-800:]
        print(f"\nRuntime stderr (tail):\n{stderr_tail}")

    print(f"\nProject path: {result.get('project_path', 'N/A')}")

except Exception as e:
    import traceback
    print(f"ERROR: {e}")
    traceback.print_exc()
