import time
import traceback
from collections import defaultdict
import re
import os
import ast
from typing import List, Dict, Any

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
from app.services.forge_score_service import (
    calculate_forge_score
)
from app.services.architecture_fix_service import (
    generate_architecture_fix
)
from app.services.endpoint_coverage_service import (
    calculate_endpoint_coverage
)
from app.utils.import_graph import collect_imports


ARCHITECTURE_ERROR_MARKERS = (
    "Architecture violation",
    "Missing endpoint",
    "Router export mismatch",
    "Missing symbol",
    "Missing APIRouter",
    "No endpoints found",
    "Undefined symbol",
)


def resolve_endpoint_file(error: str) -> str | None:
    """
    Resolve the file that should contain a missing endpoint.
    """
    match = re.search(r"(GET|POST|PUT|DELETE|PATCH)\s+(/\S+)", error)
    if not match:
        return None

    path = match.group(2)
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    if not segments:
        return None

    resource = segments[0].rstrip("s")
    return os.path.join("app", "routes", f"{resource}_routes.py")


def _sanitize_path(path: str) -> str:
    """
    Windows does not allow characters like ?, {, }, :, *, <, >, |, ".
    For our generated files we replace those with an underscore.
    This also normalises any duplicate path separators and converts
    forward‑slashes to the OS‑specific separator.
    """
    # Replace illegal characters with underscore
    safe = re.sub(r"[?{}:*<>|\"]", "_", path)
    # Normalise path separators (handles both / and \\ and collapses duplicates)
    safe = os.path.normpath(safe)
    return safe


def _collect_required_exports(project_path, target_files):
    all_imports = collect_imports(project_path)

    required = {}

    for target in target_files:
        module_dotted = (
            target.replace("/", ".").replace("\\", ".")
        )
        if module_dotted.endswith(".py"):
            module_dotted = module_dotted[:-3]

        symbols = {
            entry["symbol"]
            for entry in all_imports
            if entry["module"] == module_dotted
        }

        if symbols:
            required[target] = symbols

    return required


def _collect_required_endpoints(architecture, target_files):
    required = {}

    for endpoint in architecture.get("api_endpoints", []):
        file = endpoint.get("file")
        if file in target_files:
            required.setdefault(file, []).append(
                f"{endpoint.get('method')} {endpoint.get('path')}"
            )

    return required


def _collect_existing_symbols(project_path):
    existing = {}

    for subdir in ("schemas", "services"):
        dir_path = os.path.join(project_path, "app", subdir)

        if not os.path.exists(dir_path):
            continue

        for file in os.listdir(dir_path):
            if not file.endswith(".py") or file == "__init__.py":
                continue

            file_path = os.path.join(dir_path, file)
            rel_path = f"app/{subdir}/{file}"

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                tree = ast.parse(content)
            except Exception:
                continue

            names = []

            for node in tree.body:
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    names.append(node.name)

            if names:
                existing[rel_path] = names

    return existing


def _autocorrect_endpoint_paths(content, required_endpoints):
    for req in required_endpoints:
        method, full_path = req.split(" ", 1)
        method_lower = method.lower()

        exact_pattern = re.compile(
            rf'@\w+\.{method_lower}\(\s*["\']'
            + re.escape(full_path) + r'["\']'
        )

        if exact_pattern.search(content):
            continue

        suffix_pattern = re.compile(
            rf'(@\w+\.{method_lower}\(\s*["\'])([^"\']+)(["\'])'
        )

        def repl(m):
            existing_path = m.group(2)
            if full_path.endswith(existing_path) and existing_path != full_path:
                return m.group(1) + full_path + m.group(3)
            return m.group(0)

        content = suffix_pattern.sub(repl, content, count=1)

    return content


def _autocorrect_router_name(content, required_exports):
    router_names_required = [
        s for s in required_exports if s.endswith("_router")
    ]

    if not router_names_required:
        return content

    match = re.search(r'^(\w+)\s*=\s*APIRouter\(', content, re.MULTILINE)

    if not match:
        return content

    actual_name = match.group(1)
    expected_name = router_names_required[0]

    if actual_name == expected_name:
        return content

    return re.sub(
        rf'\b{re.escape(actual_name)}\b',
        expected_name,
        content
    )


def _read_current_files(project_path, subdir):
    files = []
    base = os.path.join(project_path, subdir)

    if not os.path.exists(base):
        return files

    for root, _, filenames in os.walk(base):
        for filename in filenames:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(
                full_path, project_path
            ).replace(os.sep, "/")

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            files.append({"path": rel_path, "content": content})

    return files


def generate_project(idea: str, provider: str = "auto") -> Dict[str, Any]:
    """
    Generates a single FastAPI + React project from an idea.
    The function follows the full pipeline:
    plan → architecture → backend → frontend → validation/fix loops → runtime validation.
    Returns a dictionary containing all artefacts and statistics for the project.
    """

    start = time.time()

    # ------------------------------------------------------------------
    # 1. Planning & Architecture
    # ------------------------------------------------------------------
    plan = generate_plan(idea, "cerebras")
    architecture = generate_architecture(plan, "cerebras")
    print("\n=== ARCHITECTURE ===")
    print(architecture)

    # ------------------------------------------------------------------
    # 2. Code Generation
    # ------------------------------------------------------------------
    backend = generate_backend(architecture, "cerebras")
    frontend = generate_frontend(architecture, "cerebras")
    all_files = []
    all_files.extend(backend["files"])
    all_files.extend(frontend["files"])

    # ------------------------------------------------------------------
    # 3. De‑duplicate files (same path may appear in both backend & frontend)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 4. Write files to disk and initialise a git repo
    # ------------------------------------------------------------------
    project_path = write_files(plan["project_name"], all_files)
    initialize_git(project_path)

    total_time = round(time.time() - start, 2)
    metadata_path = save_metadata(project_path, plan, architecture, provider, total_time)

    # ------------------------------------------------------------------
    # 5. Validation & Fix Loop
    # ------------------------------------------------------------------
    validation = validate_project(project_path)

    max_fix_attempts = 4          # increased from 2 to give more chances to auto‑fix
    fix_attempts_used = 0
    runtime_result = None

    for attempt in range(max_fix_attempts):
        if validation["passed"]:
            break

        fix_attempts_used = attempt + 1
        print(f"\n=== FIX ATTEMPT {attempt + 1} ===")

        grouped_errors = defaultdict(list)

        for error in validation["errors"]:
            # --------------------------------------------------------------
            # Group errors by the file they belong to – this makes the fix
            # agents work on a per‑file basis.
            # --------------------------------------------------------------
            if error.startswith("Unknown dependency:"):
                grouped_errors["app/requirements.txt"].append(error)
                continue

            if "Missing endpoint" in error or "Router export mismatch" in error:
                resolved = resolve_endpoint_file(error)
                if resolved:
                    grouped_errors[resolved].append(error)
                    continue

            match = re.search(
                r"(app[\\/][^\s:]+?\.(?:py|txt))",
                error
            )
            if match:
                filepath = match.group(1)
                grouped_errors[filepath].append(error)

        for filepath, file_errors in grouped_errors.items():
            try:
                # Sanitize the path for Windows compatibility
                safe_filepath = _sanitize_path(filepath)
                absolute_path = os.path.join(project_path, safe_filepath)

                # ----------------------------------------------------------
                # Orphan file – delete it
                # ----------------------------------------------------------
                if any("Orphan file:" in e for e in file_errors):
                    print(f"Deleting Orphan File: {filepath}")
                    if os.path.exists(absolute_path):
                        os.remove(absolute_path)
                    continue

                # ----------------------------------------------------------
                # Missing file – generate it via the missing‑file agent
                # ----------------------------------------------------------
                if not os.path.exists(absolute_path):
                    print(f"Generating Missing File: {filepath}")
                    fix = generate_missing_file(
                        filepath, "\n".join(file_errors), provider
                    )
                    if fix and fix.get("content"):
                        # Ensure the generated path is safe before writing
                        fix["path"] = _sanitize_path(fix["path"])
                        write_fix(project_path, fix)
                        save_fix_log(project_path, "Missing File", fix)
                    continue

                # ----------------------------------------------------------
                # Existing file – run the normal fix agent
                # ----------------------------------------------------------
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

                # Sanitize the path returned by the fix agent
                fix["path"] = _sanitize_path(fix["path"])

                # Autocorrect endpoint paths if required
                required_for_this_file = _collect_required_endpoints(
                    architecture, [filepath]
                ).get(filepath, [])

                if required_for_this_file:
                    fix["content"] = _autocorrect_endpoint_paths(
                        fix["content"], required_for_this_file
                    )

                # Autocorrect router name if required
                required_exports_for_file = _collect_required_exports(
                    project_path, [filepath]
                ).get(filepath, set())

                if required_exports_for_file:
                    fix["content"] = _autocorrect_router_name(
                        fix["content"], required_exports_for_file
                    )

                write_fix(project_path, fix)
                save_fix_log(project_path, "\n".join(file_errors), fix)

            except Exception as e:
                print(f"Fix Agent Failed: {str(e)}")
                traceback.print_exc()

        validation = validate_project(project_path)
        print(f"\nRevalidation Result: {validation}")

    # ------------------------------------------------------------------
    # 6. Architecture‑specific repairs (if any architecture errors remain)
    # ------------------------------------------------------------------
    architecture_errors = []
    if not validation["passed"]:
        for error in validation["errors"]:
            if any(marker in error for marker in ARCHITECTURE_ERROR_MARKERS):
                architecture_errors.append(error)

    if architecture_errors:
        print("\n=== ARCHITECTURE REPAIR ===")

        target_files = sorted({
            m.group(1)
            for e in architecture_errors
            for m in [re.search(r"(app[\\/][^\s:]+?\.py)", e)]
            if m
        })

        required_exports = _collect_required_exports(project_path, target_files)
        required_endpoints = _collect_required_endpoints(architecture, target_files)
        existing_symbols = _collect_existing_symbols(project_path)

        architecture_fix = generate_architecture_fix(
            architecture,
            architecture_errors,
            provider,
            required_exports=required_exports,
            required_endpoints=required_endpoints,
            existing_symbols=existing_symbols
        )
        if (
            architecture_fix
            and isinstance(architecture_fix, dict)
            and architecture_fix.get("files")
        ):
            for file in architecture_fix["files"]:
                # Sanitize the path before writing
                file["path"] = _sanitize_path(file["path"])

                req_for_file = required_endpoints.get(file.get("path"), [])

                if req_for_file and file.get("content"):
                    file["content"] = _autocorrect_endpoint_paths(
                        file["content"], req_for_file
                    )

                exports_for_file = required_exports.get(file.get("path"), set())

                if exports_for_file and file.get("content"):
                    file["content"] = _autocorrect_router_name(
                        file["content"], exports_for_file
                    )

                write_fix(
                    project_path,
                    file
                )
            validation = validate_project(
                project_path
            )
            print(
                f"\nArchitecture Revalidation: {validation}"
            )

    # ------------------------------------------------------------------
    # 7. Runtime Validation (only after all fixes have been attempted)
    # ------------------------------------------------------------------
    if validation["passed"]:
        print("\n=== RUNTIME VALIDATION ===")
        max_runtime_fix_attempts = 2

        try:
            for runtime_attempt in range(max_runtime_fix_attempts + 1):
                runtime_result = validate_runtime(project_path, architecture=architecture)
                print(f"Runtime Result: {runtime_result}")

                if runtime_result and runtime_result.get("success", False):
                    print("\nRuntime Validation Passed")
                    break

                if runtime_attempt == max_runtime_fix_attempts:
                    print("\nRuntime Fix Attempts Exhausted")
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

                # Sanitize runtime fix path before writing
                runtime_fix["path"] = _sanitize_path(runtime_fix["path"])

                write_fix(project_path, runtime_fix)
                save_fix_log(project_path, str(runtime_result), runtime_fix)
                print("Runtime Fix Written")

        except Exception as e:
            runtime_result = {"success": False, "error": str(e)}
            print(f"Runtime Validation Failed: {e}")

    # ------------------------------------------------------------------
    # 8. Packaging (zip) if everything succeeded
    # ------------------------------------------------------------------
    zip_path = None
    can_export = (
        validation["passed"]
        and runtime_result
        and runtime_result.get(
            "success",
            False
        )
    )
    if can_export:
        zip_path = create_zip(
            project_path
        )
    else:
        print("\nExport Blocked")
        print("Validation or Runtime Failed")

    # ------------------------------------------------------------------
    # 9. Scoring & Reporting
    # ------------------------------------------------------------------
    forge_score = calculate_forge_score(
        validation,
        runtime_result
    )
    print(f"\nForge Score: {forge_score}")

    endpoint_coverage = calculate_endpoint_coverage(
        architecture,
        project_path
    )

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

    final_backend_files = _read_current_files(project_path, "app")
    final_frontend_files = _read_current_files(project_path, "src")

    return {
        "plan": plan,
        "architecture": architecture,
        "forge_score": forge_score,
        "endpoint_coverage": endpoint_coverage,
        "backend": {"files": final_backend_files},
        "frontend": {"files": final_frontend_files},
        "project_path": project_path,
        "zip_path": zip_path,
        "metadata_path": metadata_path,
        "validation": validation,
        "runtime": runtime_result,
        "stats": validation_stats,
        "generation_time_seconds": total_time,
    }


def generate_multiple_projects(ideas: List[str], provider: str = "auto") -> Dict[str, Any]:
    """
    Runs the full generation pipeline for a list of ideas (default 6).
    For each idea a separate project folder is created under a common
    parent directory (the folder name is the project name returned by the
    planner).  The function returns a summary report containing the
    forge_score, endpoint_coverage and overall success status for each
    generated project.
    """
    results = {}
    for idx, idea in enumerate(ideas, start=1):
        print(f"\n{'=' * 20} Generating Project {idx}/{len(ideas)} {'=' * 20}")
        try:
            project_result = generate_project(idea, provider)
            results[project_result["plan"]["project_name"]] = {
                "forge_score": project_result["forge_score"],
                "endpoint_coverage": project_result["endpoint_coverage"],
                "validation_passed": project_result["validation"]["passed"],
                "runtime_passed": project_result["runtime"]["success"]
                if project_result["runtime"]
                else False,
                "project_path": project_result["project_path"],
                "zip_path": project_result["zip_path"],
            }
        except Exception as e:
            print(f"Error generating project for idea '{idea}': {e}")
            traceback.print_exc()
            results[f"idea_{idx}"] = {
                "error": str(e)
            }

    # Final summary report
    print("\n\n=== FINAL MULTI‑PROJECT SUMMARY ===")
    for proj, data in results.items():
        print(f"\nProject: {proj}")
        if "error" in data:
            print(f"  ❌ Generation failed: {data['error']}")
        else:
            print(f"  ✅ Forge Score          : {data['forge_score']}")
            print(f"  📊 Endpoint Coverage   : {data['endpoint_coverage']}")
            print(f"  ✅ Validation Passed   : {data['validation_passed']}")
            print(f"  ✅ Runtime Passed      : {data['runtime_passed']}")
            print(f"  📁 Project Path        : {data['project_path']}")
            print(f"  📦 Zip Path            : {data['zip_path']}")

    return results
