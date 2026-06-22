import sys
from app.services.project_service import generate_project

result = generate_project(
    "A simple todo list app with task priorities and due dates",
    provider="cerebras"
)

score = result["forge_score"]
fb = result.get("frontend_build") or {}
validation = result["validation"]
runtime = result.get("runtime") or {}

print()
print("=== V3 RESULT ===")
print(f"Forge Score   : {score['score']}/100 ({score['grade']})")
print(f"Validation    : {'PASS' if validation['passed'] else 'FAIL'}")
print(f"Runtime       : {'PASS' if runtime.get('success') else 'FAIL'}")

if fb.get("node_missing"):
    print("Frontend Build: SKIPPED (Node not found)")
else:
    status = "PASS" if fb.get("success") else "FAIL"
    nerrors = len(fb.get("errors", []))
    build_time = fb.get("build_time", 0)
    print(f"Frontend Build: {status} — {nerrors} errors — {build_time}s")
    if fb.get("errors"):
        for e in fb["errors"][:5]:
            print(f"  {e}")

if validation.get("errors"):
    print("\nValidation errors:")
    for e in validation["errors"][:5]:
        print(f"  {e}")
