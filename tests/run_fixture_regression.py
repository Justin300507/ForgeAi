import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.backend_service import generate_backend
from app.services.frontend_service import generate_frontend
from app.services.file_writer_service import write_files
from app.services.validator_service import validate_project
from app.services.metadata_service import save_metadata
from app.services.endpoint_coverage_service import calculate_endpoint_coverage
from app.services.forge_score_service import calculate_forge_score


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "test_fixtures")
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots")


def _load_snapshot(fixture_name):

    path = os.path.join(SNAPSHOTS_DIR, f"{fixture_name}.json")

    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_snapshot(fixture_name, data):

    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOTS_DIR, f"{fixture_name}.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def run_fixture(fixture_filename, provider="groq"):

    fixture_name = fixture_filename.replace(".json", "")
    fixture_path = os.path.join(FIXTURES_DIR, fixture_filename)

    with open(fixture_path, "r", encoding="utf-8") as f:
        raw = f.read()

    if not raw.strip():
        print(f"\n=== FIXTURE: {fixture_name} ===")
        print(f"⚠️  Skipping — {fixture_path} is empty.")
        return None

    try:
        fixture_data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\n=== FIXTURE: {fixture_name} ===")
        print(f"⚠️  Skipping — invalid JSON in {fixture_path}: {e}")
        return None

    plan = fixture_data["plan"]
    architecture = fixture_data["architecture"]

    print(f"\n=== FIXTURE: {fixture_name} ===")
    print("Using fixed architecture from fixture — Planner/Architect skipped, no LLM cost for those stages")

    start = time.time()

    backend = generate_backend(architecture, provider)
    frontend = generate_frontend(architecture, provider)

    all_files = backend["files"] + frontend["files"]
    unique_files = {f["path"]: f for f in all_files if f.get("path")}
    all_files = list(unique_files.values())

    project_path = write_files(plan["project_name"], all_files)
    save_metadata(
        project_path, plan, architecture, provider, round(time.time() - start, 2)
    )

    validation = validate_project(project_path)
    coverage = calculate_endpoint_coverage(architecture, project_path)
    score = calculate_forge_score(validation, None)

    current_result = {
        "passed": validation["passed"],
        "error_count": len(validation["errors"]),
        "errors": sorted(validation["errors"]),
        "endpoint_coverage": coverage["coverage"],
        "forge_score": score["score"],
        "forge_grade": score["grade"]
    }

    print(
        f"Passed: {current_result['passed']} | "
        f"Errors: {current_result['error_count']} | "
        f"Coverage: {current_result['endpoint_coverage']}% | "
        f"Score: {current_result['forge_score']} ({current_result['forge_grade']})"
    )

    baseline = _load_snapshot(fixture_name)

    if baseline is None:
        print("No baseline — saving this as the new baseline.")
        _save_snapshot(fixture_name, current_result)
        return True

    score_delta = current_result["forge_score"] - baseline["forge_score"]
    added = [e for e in current_result["errors"] if e not in baseline["errors"]]
    removed = [e for e in baseline["errors"] if e not in current_result["errors"]]

    print(f"Score change vs baseline: {'+' if score_delta >= 0 else ''}{score_delta}")

    if added:
        print("⚠️  NEW errors not in baseline:")
        for e in added:
            print(f"   + {e}")

    if removed:
        print("✅ Errors no longer present:")
        for e in removed:
            print(f"   - {e}")

    if not added and not removed:
        print("✅ No change from baseline.")

    if added or score_delta < 0:

        update = input(
            "\nResult looks worse or different from baseline. Update "
            "baseline anyway? Only do this if EXPECTED. (y/n): "
        )

        if update.lower() == "y":
            _save_snapshot(fixture_name, current_result)

        return False

    if score_delta > 0:
        print("Score improved — updating baseline automatically.")
        _save_snapshot(fixture_name, current_result)

    return True


def main():

    if not os.path.exists(FIXTURES_DIR):
        print(f"No fixtures directory found at {FIXTURES_DIR}")
        return

    fixtures = [f for f in os.listdir(FIXTURES_DIR) if f.endswith(".json")]

    if not fixtures:
        print(f"No fixture files found in {FIXTURES_DIR}")
        return

    all_passed = True

    for fixture in fixtures:
        passed = run_fixture(fixture)
        all_passed = all_passed and passed

    print("\n" + "=" * 40)

    if all_passed:
        print("✅ ALL FIXTURES CONSISTENT WITH (OR BETTER THAN) BASELINE")
    else:
        print("⚠️  SOME FIXTURES REGRESSED — review the diffs above")


if __name__ == "__main__":
    main()