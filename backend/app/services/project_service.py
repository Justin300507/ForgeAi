import time
import traceback
from collections import defaultdict
import re
import os
from app.services.planner_service import generate_plan
from app.services.architect_service import generate_architecture
from app.services.backend_service import generate_backend
from app.services.frontend_service import generate_frontend
from app.services.file_writer_service import write_files
from app.services.zip_service import create_zip
from app.services.metadata_service import save_metadata
from app.services.validator_service import validate_project
from app.services.fixer_service import generate_fix
from app.services.fix_writer_service import write_fix
from app.services.fix_log_service import save_fix_log
from app.services.git_service import initialize_git
from app.services.runtime_validator_service import validate_runtime
from app.services.runtime_fix_service import generate_runtime_fix
from app.services.missing_file_service import generate_missing_file


def generate_project(idea, provider="auto"):

    start = time.time()

    plan = generate_plan(idea, provider)
    architecture = generate_architecture(plan, provider)
    backend = generate_backend(architecture, provider)
    frontend = generate_frontend(architecture, provider)

    all_files = []
    all_files.extend(backend["files"])
    all_files.extend(frontend["files"])

    unique_files = {}
    for file in all_files:
        path = file.get("path", "")
        if not path:
            continue
        if path in unique_files:
            print(f"Duplicate file detected: {path}")
        unique_files[path] = file

    all_files = list(unique_files.values())
    print(f"Unique Files: {len(all_files)}")

    project_path = write_files(plan["project_name"], all_files)
    initialize_git(project_path)

    total_time = round(time.time() - start, 2)
    metadata_path = save_metadata(project_path, plan, provider, total_time)
    validation = validate_project(project_path)

    max_fix_attempts = 2
    fix_attempts_used = 0
    runtime_result = None

    for attempt in range(max_fix_attempts):

        if validation["passed"]:
            break

        fix_attempts_used = attempt + 1
        print(f"\n=== FIX ATTEMPT {attempt + 1} ===")

        grouped_errors = defaultdict(list)

        for error in validation["errors"]:
            if error.startswith("Unknown dependency:"):
                grouped_errors["app/requirements.txt"].append(error)

            match = re.search(r"(app[\\/].+\.(?:py|txt)|routes[\\/].+\.py)", error)
            if match:
                filepath = match.group(1)
                if filepath.startswith("routes"):
                    filepath = os.path.join("app", filepath)
                grouped_errors[filepath].append(error)

        for filepath, file_errors in grouped_errors.items():
            try:
                absolute_path = os.path.join(project_path, filepath)

                if not os.path.exists(absolute_path):
                    print(f"Generating Missing File: {filepath}")
                    fix = generate_missing_file(
                        filepath, "\n".join(file_errors), provider
                    )
                    if fix and fix.get("content"):
                        write_fix(project_path, fix)
                        save_fix_log(project_path, "Missing File", fix)
                    continue

                with open(absolute_path, "r", encoding="utf-8") as f:
                    file_content = f.read()

                print(f"\nFixing File: {filepath}")
                fix = generate_fix(filepath, file_content, file_errors, provider)

                if (
                    not isinstance(fix, dict)
                    or not fix.get("path")
                    or not fix.get("content")
                ):
                    continue

                write_fix(project_path, fix)
                save_fix_log(project_path, "\n".join(file_errors), fix)

            except Exception as e:
                print(f"Fix Agent Failed: {str(e)}")
                traceback.print_exc()

        validation = validate_project(project_path)
        print(f"\nRevalidation Result: {validation}")

        if validation["passed"]:
            print("\n=== RUNTIME VALIDATION ===")
            max_runtime_fix_attempts = 2

            try:
                for runtime_attempt in range(max_runtime_fix_attempts + 1):
                    runtime_result = validate_runtime(project_path)
                    print(f"Runtime Result: {runtime_result}")

                    if runtime_result and runtime_result.get("success", False):
                        print("\n✅ Runtime Validation Passed")
                        break

                    if runtime_attempt == max_runtime_fix_attempts:
                        print("\n❌ Runtime Fix Attempts Exhausted")
                        break

                    print(f"\n=== RUNTIME FIX ATTEMPT {runtime_attempt + 1} ===")
                    runtime_fix = generate_runtime_fix(
                        runtime_result, project_path, provider
                    )
                    print("Generated Runtime Fix:")
                    print(runtime_fix)

                    if not (runtime_fix and isinstance(runtime_fix, dict)):
                        continue
                    if not (runtime_fix.get("path") and runtime_fix.get("content")):
                        continue

                    write_fix(project_path, runtime_fix)
                    save_fix_log(project_path, str(runtime_result), runtime_fix)
                    print("Runtime Fix Written")

            except Exception as e:
                runtime_result = {"success": False, "error": str(e)}
                print(f"Runtime Validation Failed: {e}")

    zip_path = create_zip(project_path)

    validation_stats = {
        "passed": validation["passed"],
        "error_count": len(validation["errors"]),
        "fix_attempts": fix_attempts_used,
        "runtime_passed": (
            runtime_result.get("success", False) if runtime_result else False
        ),
        "runtime_error_type": (
            runtime_result.get("parsed_error", {}).get("type")
            if runtime_result
            else None
        ),
        "generation_time": total_time,
    }

    return {
        "plan": plan,
        "architecture": architecture,
        "backend": backend,
        "frontend": frontend,
        "project_path": project_path,
        "zip_path": zip_path,
        "metadata_path": metadata_path,
        "validation": validation,
        "runtime": runtime_result,
        "stats": validation_stats,
        "generation_time_seconds": total_time,
    }