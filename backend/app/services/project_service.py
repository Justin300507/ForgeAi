import time
import traceback

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

def generate_project(
    idea,
    provider="auto"
):

    start = time.time()

    plan = generate_plan(
        idea,
        provider
    )

    architecture = generate_architecture(
        plan,
        provider
    )

    backend = generate_backend(
        architecture,
        provider
    )

    frontend = generate_frontend(
        architecture,
        provider
    )

    all_files = []

    all_files.extend(backend["files"])
    all_files.extend(frontend["files"])

    project_path = write_files(
        plan["project_name"],
        all_files
    )
    initialize_git(
    project_path
    )

    total_time = round(
        time.time() - start,
        2
    )

    metadata_path = save_metadata(
        project_path,
        plan,
        provider,
        total_time
    )

    validation = validate_project(
        project_path
    )

    max_fix_attempts = 2

    for attempt in range(max_fix_attempts):

        if validation["passed"]:
            break

        print(
            f"\n=== FIX ATTEMPT {attempt + 1} ==="
        )

        current_errors = validation["errors"][:]

        for error in current_errors:

            try:

                print(
                    f"\nFixing: {error}"
                )

                fix = generate_fix(
                    error,
                    provider
                )

                print(
                    "Generated Fix:",
                    fix
                )

                if not isinstance(
                    fix,
                    dict
                ):

                    print(
                        "Invalid fix format"
                    )

                    continue

                if not fix.get(
                    "path"
                ):

                    print(
                        "Missing fix path"
                    )

                    continue

                if not fix.get(
                    "content"
                ):

                    print(
                        "Missing fix content"
                    )

                    continue

                write_fix(
                    project_path,
                    fix
                )
                save_fix_log(
                    project_path,
                    error,
                    fix
                )

                print(
                    f"Written: {fix['path']}"
                )

            except Exception as e:

                print(
                    f"Fix Agent Failed: {str(e)}"
                )

                traceback.print_exc()

        validation = validate_project(
            project_path
        )

        print(
            f"\nRevalidation Result: {validation}"
        )

    if validation["passed"]:

        print(
            "\n✅ Project Validation Passed"
        )

    else:

        print(
            "\n❌ Validation Still Failed"
        )

        print(
            f"Remaining Errors: {validation['errors']}"
        )

    zip_path = create_zip(
        project_path
    )
    validation_stats = {
    "passed": validation["passed"],
    "error_count": len(validation["errors"]),
    "fix_attempts": attempt + 1,
    "generation_time": total_time
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
        "stats": validation_stats,
        "generation_time_seconds": total_time
    }
