"""
Generate 5 apps using Cerebras and report forge scores.
Run from backend/ directory with the venv activated:
  python run_5_apps.py
"""
import sys
import os

# Make sure we're running from the backend directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.project_service import generate_project

IDEAS = [
    "A recipe management app with ingredients, categories, and meal planning",
    "A job board with job listings, company profiles, and candidate applications",
    "A gym membership tracker with members, workout logs, and subscription plans",
    "A personal finance tracker with income, expenses, and budget categories",
    "An event management system with events, tickets, and attendee registrations",
]

def main():
    results = {}
    for idx, idea in enumerate(IDEAS, 1):
        print(f"\n{'='*60}")
        print(f"APP {idx}/5: {idea}")
        print('='*60)
        try:
            result = generate_project(idea, provider="cerebras")
            score = result["forge_score"]
            validation = result["validation"]
            runtime = result["runtime"]
            fb = result.get("frontend_build") or {}
            fb_skipped = fb.get("node_missing", True)
            fb_passed = fb.get("success", False)

            results[idea] = {
                "score": score["score"],
                "grade": score["grade"],
                "validation_passed": validation["passed"],
                "errors": validation["errors"],
                "runtime_passed": runtime.get("success", False) if runtime else False,
                "frontend_build_passed": fb_passed,
                "frontend_build_skipped": fb_skipped,
                "project_path": result["project_path"],
            }
            print(f"\n>>> FORGE SCORE: {score['score']}/100 ({score['grade']})")
            print(f">>> Validation: {'PASS' if validation['passed'] else 'FAIL'}")
            print(f">>> Runtime: {'PASS' if results[idea]['runtime_passed'] else 'FAIL'}")
            if fb_skipped:
                print(">>> Frontend Build: SKIPPED (Node.js not installed)")
            else:
                print(f">>> Frontend Build: {'PASS' if fb_passed else 'FAIL'}")
            if validation["errors"]:
                print(f">>> Errors ({len(validation['errors'])}):")
                for e in validation["errors"][:10]:
                    print(f"    - {e}")
        except Exception as e:
            import traceback
            print(f"FAILED: {e}")
            traceback.print_exc()
            results[idea] = {"score": 0, "grade": "F", "error": str(e)}

    print("\n" + "="*60)
    print("FINAL SUMMARY")
    print("="*60)
    all_100 = True
    for idea, data in results.items():
        score = data.get("score", 0)
        grade = data.get("grade", "F")
        err = data.get("error", "")
        if score < 100:
            all_100 = False
        print(f"  [{score:3d}] {idea[:60]}")
        if err:
            print(f"         ERROR: {err}")
    print()
    if all_100:
        print("ALL 5 APPS SCORED 100/100!")
    else:
        print("Some apps scored below 100 - see errors above.")

if __name__ == "__main__":
    main()
