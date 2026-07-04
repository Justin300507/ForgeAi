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
from app.services.frontend_scaffold_service import ensure_app_jsx
from app.services.zip_service import create_zip, write_debug_report
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
    calculate_forge_score,
    build_pipeline_metrics,
)
from app.services.architecture_fix_service import (
    generate_architecture_fix
)
from app.runtime.frontend_runner import FrontendRunner
from app.services.frontend_fix_service import run_frontend_fix_loop
from app.services.endpoint_coverage_service import (
    calculate_endpoint_coverage
)
from app.utils.import_graph import collect_imports
from app.services.critic_service import run_critic
from app.services.production_critic_service import run_production_critic, print_production_report
from app.runtime.playwright_runner import run_playwright_tests
from app.memory.failure_memory import record_run as record_failure_run
from app.runtime.docker_validator import run_docker_validation
from app.utils.cost_tracker import reset_session, flush_to_log, print_session_cost
from app.services.architecture_tournament_service import run_architecture_tournament


ARCHITECTURE_ERROR_MARKERS = (
    "Architecture violation",
    "Missing endpoint",
    "Router export mismatch",
    "Missing symbol",
    "Missing APIRouter",
    "No endpoints found",
    "Undefined symbol",
)


def _patch_arch_fix_routes_into_main(project_path: str, written_paths: list) -> None:
    """After architecture repair, wire any newly written route files into main.py.

    Architecture repair may generate route files that main.py doesn't import yet.
    Without this patch the orphan validator deletes them on the next pass, creating
    an infinite repair → delete → repair loop.
    """
    main_py = os.path.join(project_path, "app", "main.py")
    if not os.path.exists(main_py):
        return
    try:
        with open(main_py, "r", encoding="utf-8") as f:
            content = f.read()
        changed = False
        for path in written_paths:
            path_fwd = path.replace("\\", "/")
            m = re.match(r"app/routes/(\w+)_routes\.py$", path_fwd)
            if not m:
                continue
            resource = m.group(1)
            router_name = f"{resource}_router"
            module = f"app.routes.{resource}_routes"
            import_line = f"from {module} import {router_name}"
            include_line = f"app.include_router({router_name})"
            if import_line not in content:
                last = None
                for lm in re.finditer(r"from app\.routes\.\w+ import \w+_router\n", content):
                    last = lm
                if last:
                    content = content[:last.end()] + import_line + "\n" + content[last.end():]
                else:
                    content = import_line + "\n" + content
                changed = True
            if include_line not in content:
                last = None
                for lm in re.finditer(r"app\.include_router\(\w+_router\)\n", content):
                    last = lm
                if last:
                    content = content[:last.end()] + include_line + "\n" + content[last.end():]
                else:
                    content += "\n" + include_line + "\n"
                changed = True
        if changed:
            with open(main_py, "w", encoding="utf-8") as f:
                f.write(content)
            print("  [arch_fix] patched main.py to wire new route files")
    except Exception as e:
        print(f"  [arch_fix] main.py patch failed: {e}")


def resolve_endpoint_file(error: str) -> str | None:
    """
    Resolve the file that should contain a missing endpoint.
    Prefers the explicit path embedded in the error message by the validator.
    Falls back to computing from the URL path only when no explicit file is present.
    """
    # Prefer the explicit file path the validator embedded in the message
    m = re.search(r"expected in (app[\\/][^\s]+\.py)", error)
    if m:
        return m.group(1).replace("\\", "/")

    match = re.search(r"(GET|POST|PUT|DELETE|PATCH)\s+(/\S+)", error)
    if not match:
        return None

    path = match.group(2)
    path_without_query = path.split('?')[0]
    segments = [s for s in path_without_query.split("/") if s and not s.startswith("{")]
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


def _sanitize_architecture_paths(architecture: Dict[str, Any]) -> None:
    """
    The architecture generated by the LLM may contain file names that
    include characters illegal on Windows (e.g. '?', '{', '}').  Those
    names are used later when we look for missing files, so we need to
    bring them in line with the sanitized file names we actually write
    to disk.  This function mutates the architecture dict in‑place.
    """
    for endpoint in architecture.get("api_endpoints", []):
        original = endpoint.get("file")
        if original:
            endpoint["file"] = _sanitize_path(original)


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


def generate_project(idea: str, provider: str = "auto", use_tournament: bool = False) -> Dict[str, Any]:
    """
    Generates a single FastAPI + React project from an idea.
    The function follows the full pipeline:
    plan → architecture → backend → frontend → validation/fix loops → runtime validation.
    Returns a dictionary containing all artefacts and statistics for the project.

    use_tournament: if True, runs V5.4 Architecture Tournament (3 candidate architectures,
    picks the one with the best static validation score).
    """

    start = time.time()
    reset_session()  # clear cost tracker for this run

    # Stage timing: records wall-clock seconds per pipeline stage
    _t = {}
    def _snap(name: str, t0: float) -> float:
        _t[name] = round(time.time() - t0, 2)
        return time.time()

    # Cost snapshot helper
    def _cost_now() -> float:
        try:
            from app.utils.cost_tracker import get_session_cost_usd
            return get_session_cost_usd()
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # 1. Planning & Architecture
    # ------------------------------------------------------------------
    _t0 = time.time()
    plan = generate_plan(idea, provider)
    _t0 = _snap("plan", _t0)

    tournament_result = None
    if use_tournament:
        print("\n=== V5.4 ARCHITECTURE TOURNAMENT MODE ===")
        tournament_result = run_architecture_tournament(plan, provider, n_candidates=3)
        architecture = tournament_result.winner_architecture
        print(f"Tournament complete: winner={tournament_result.winner_index+1}, "
              f"scores={tournament_result.scores}, duration={tournament_result.duration}s")
    else:
        architecture = generate_architecture(plan, provider)
    _t0 = _snap("architect", _t0)

    # Sanitize any illegal characters in architecture file paths
    _sanitize_architecture_paths(architecture)
    print("\n=== ARCHITECTURE ===")
    try:
        print(architecture)
    except UnicodeEncodeError:
        print(str(architecture).encode("ascii", errors="replace").decode("ascii"))

    # ------------------------------------------------------------------
    # 2. Code Generation
    # ------------------------------------------------------------------
    backend = generate_backend(architecture, provider)
    _t0 = _snap("backend", _t0)
    frontend = generate_frontend(architecture, provider)
    _t0 = _snap("frontend", _t0)
    all_files = []
    all_files.extend(backend["files"])
    all_files.extend(frontend["files"])

    # ------------------------------------------------------------------
    # 3. Normalise file paths (remove illegal characters) and de‑duplicate
    # ------------------------------------------------------------------
    sanitized_files = []
    for file in all_files:
        # Ensure every generated file path is safe for the OS
        file["path"] = _sanitize_path(file.get("path", ""))
        sanitized_files.append(file)

    unique_files = {}
    for file in sanitized_files:
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
    if ensure_app_jsx(project_path):
        print("  [scaffold] Synthesized missing src/App.jsx from existing pages")

    total_time = round(time.time() - start, 2)
    metadata_path = save_metadata(project_path, plan, architecture, provider, total_time)

    # ------------------------------------------------------------------
    # 5. Validation & Fix Loop
    # ------------------------------------------------------------------
    _t0 = time.time()
    validation = validate_project(project_path)
    _t["validation_first"] = round(time.time() - _t0, 2)

    # Capture first-pass result BEFORE any repairs — used for ForgeBench metrics
    first_pass_compile = validation.get("passed", False)
    first_pass_error_count = len(validation.get("errors", []))
    _cost_before_repairs = _cost_now()

    max_fix_attempts = 4          # increased from 2 to give more chances to auto‑fix
    fix_attempts_used = 0
    _repair_files_changed: set[str] = set()
    _repairs_start = time.time()
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
                continue

            # Frontend import errors: derive the missing .jsx file path
            frontend_match = re.search(
                r"Missing frontend import target:\s+(\S+)",
                error
            )
            if frontend_match:
                imp = frontend_match.group(1)
                # Preserve the import's own subdirectory (e.g. "./contexts/AuthContext"
                # -> "src/contexts/AuthContext.jsx") instead of collapsing everything to a
                # flat components/pages guess — the missing file must be created at the
                # exact path the bundler will actually resolve.
                imp_rel = imp
                while imp_rel.startswith("../") or imp_rel.startswith("./"):
                    imp_rel = imp_rel[3:] if imp_rel.startswith("../") else imp_rel[2:]
                imp_rel = imp_rel.replace(".jsx", "").replace(".js", "")
                if "/" in imp_rel:
                    grouped_errors[f"src/{imp_rel}.jsx"].append(error)
                    continue
                name = imp_rel
                for subdir in ("components", "pages"):
                    if subdir in imp:
                        grouped_errors[f"src/{subdir}/{name}.jsx"].append(error)
                        break
                else:
                    grouped_errors[f"src/{name}.jsx"].append(error)

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
                        filepath, "\n".join(file_errors), provider, project_path=project_path
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
                _repair_files_changed.add(filepath)   # track for repair efficiency

            except Exception as e:
                print(f"Fix Agent Failed: {str(e)}")
                traceback.print_exc()

        validation = validate_project(project_path)
        print(f"\nRevalidation Result: {validation}")

    _t["repairs"] = round(time.time() - _repairs_start, 2)
    _cost_after_repairs = _cost_now()

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
            # Wire any newly-written route files into main.py so they aren't
            # treated as orphans on the next validation pass.
            _patch_arch_fix_routes_into_main(
                project_path,
                [f["path"] for f in architecture_fix["files"]]
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
        _runtime_start = time.time()

        try:
            for runtime_attempt in range(max_runtime_fix_attempts + 1):
                # Re-inject auth_routes.py before every runtime attempt.
                # The validation fix loop may have overwritten it with broken LLM code.
                try:
                    from app.services.deterministic_patcher import _patch_auth_routes
                    from pathlib import Path as _Path
                    _patch_auth_routes(_Path(project_path))
                except Exception as _ar_err:
                    print(f"  [pre-runtime] auth_routes re-inject failed: {_ar_err}")

                # Clear stale SQLite DB so each runtime attempt starts with a clean
                # state. Without this, a user created in a broken earlier run
                # persists into the next attempt with a corrupted password hash,
                # causing login to return 401 even after auth is fixed.
                import os as _os
                for _db_name in ("app.db", "test.db", "database.db"):
                    _db_path = _os.path.join(project_path, _db_name)
                    if _os.path.exists(_db_path):
                        try:
                            _os.remove(_db_path)
                            print(f"  [pre-runtime] Cleared stale {_db_name}")
                        except Exception:
                            pass

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

        _t["runtime"] = round(time.time() - _runtime_start, 2)

    # ------------------------------------------------------------------
    # 7b. Autonomous Regeneration (V4)
    #     If static + runtime BOTH failed after all fix attempts,
    #     redesign the architecture from scratch and try once more.
    # ------------------------------------------------------------------
    runtime_succeeded = runtime_result and runtime_result.get("success", False)
    if not validation["passed"] and not runtime_succeeded:
        print("\n=== AUTONOMOUS REGENERATION (V4) ===")
        print("All fix attempts exhausted — redesigning architecture and regenerating...")
        try:
            from app.memory.failure_memory import build_prompt_injection
            learned_context = build_prompt_injection()

            # Re-run architect with learned context injected
            architecture = generate_architecture(plan, provider, extra_context=learned_context)
            print("New architecture generated")

            # Full backend + frontend regeneration
            backend_response = generate_backend(architecture, provider)
            frontend_response = generate_frontend(architecture, provider)
            all_files = backend_response.get("files", []) + frontend_response.get("files", [])
            project_path = write_files(plan["project_name"], all_files)
            print(f"Regenerated {len(all_files)} files")
            if ensure_app_jsx(project_path):
                print("  [scaffold] Synthesized missing src/App.jsx from existing pages")

            # One more validation + runtime pass
            validation = validate_project(project_path)
            print(f"Post-regen validation: {validation}")

            if validation["passed"]:
                runtime_result = validate_runtime(project_path, architecture=architecture)
                print(f"Post-regen runtime: {runtime_result.get('success', False) if runtime_result else False}")

        except Exception as regen_err:
            print(f"Autonomous regeneration failed: {regen_err}")

    # ------------------------------------------------------------------
    # 8. Frontend Build Validation (Vite/React)
    # ------------------------------------------------------------------
    frontend_build_result = None
    if validation["passed"] and runtime_result and runtime_result.get("success", False):
        print("\n=== FRONTEND BUILD VALIDATION ===")
        try:
            _frontend_runner = FrontendRunner()
            frontend_build_result = run_frontend_fix_loop(
                project_path, _frontend_runner, provider, max_attempts=6
            )
            if frontend_build_result.get("node_missing"):
                print("Node.js not found — frontend build skipped")
            elif frontend_build_result.get("success"):
                print("Frontend Build Passed")
            else:
                print(f"Frontend Build Failed — {len(frontend_build_result.get('errors', []))} errors")
        except Exception as e:
            print(f"Frontend build error: {e}")
            frontend_build_result = {"success": False, "error": str(e), "node_missing": False}

    # ------------------------------------------------------------------
    # 9a. Multi-agent Critic Review
    # ------------------------------------------------------------------
    critic_result = None
    if validation["passed"]:
        print("\n=== CRITIC REVIEW ===")
        try:
            critic_result = run_critic(project_path, provider)
            print(f"Critic score: {critic_result['score']}/100 — {critic_result['summary']}")
            if critic_result["critical_issues"]:
                for issue in critic_result["critical_issues"]:
                    print(f"  CRITICAL: {issue}")
            if critic_result["warnings"]:
                for w in critic_result["warnings"][:3]:
                    print(f"  WARN: {w}")
        except Exception as e:
            print(f"Critic error: {e}")
            critic_result = {"score": 75, "critical_issues": [], "warnings": [], "summary": "Critic unavailable."}

    # ------------------------------------------------------------------
    # 9b. Playwright Browser Automation
    # ------------------------------------------------------------------
    playwright_result = None
    frontend_built = frontend_build_result and frontend_build_result.get("success", False)
    if frontend_built:
        print("\n=== PLAYWRIGHT BROWSER TESTS ===")
        try:
            playwright_result = run_playwright_tests(
                project_path, architecture, capture_screenshots=True
            )
            if playwright_result.skipped:
                print(f"Playwright skipped: {playwright_result.skip_reason}")
            elif playwright_result.success:
                print(f"Playwright passed — {playwright_result.pages_checked} pages checked in {playwright_result.duration}s")
            else:
                print(f"Playwright failed — blanks: {playwright_result.blank_pages}, errors: {playwright_result.console_errors[:3]}")
        except Exception as e:
            print(f"Playwright error: {e}")
            playwright_result = None

    # 9b2. User journey now runs inside BackendRunner while the server is alive.
    # runtime_result["journey"] contains the serialized JourneyResult.
    # We expose it here for backwards compatibility with the return value.

    # ------------------------------------------------------------------
    # 9c. Vision-Based UI Validation (V4)
    # ------------------------------------------------------------------
    vision_result = None
    if frontend_built and playwright_result and not playwright_result.skipped:
        print("\n=== VISION UI VALIDATION (V4) ===")
        try:
            from app.runtime.vision_validator import run_vision_validation
            existing_shots = getattr(playwright_result, "screenshots", [])
            vision_result = run_vision_validation(
                project_path, architecture,
                existing_screenshots=existing_shots if existing_shots else None,
            )
            if vision_result.skipped:
                print(f"Vision skipped: {vision_result.skip_reason}")
            else:
                print(f"Vision score: {vision_result.ui_score}/100 ({len(vision_result.issues)} issues)")
                for issue in vision_result.issues:
                    print(f"  [{issue.severity}] {issue.page}: {issue.description}")
        except Exception as e:
            print(f"Vision validation error: {e}")

    # ------------------------------------------------------------------
    # 10. Packaging (zip) if everything succeeded
    # ------------------------------------------------------------------
    can_export = (
        validation["passed"]
        and runtime_result
        and runtime_result.get(
            "success",
            False
        )
    )
    if not can_export:
        print("\nRuntime/validation failed — zipping anyway with a build report for debugging")
        write_debug_report(project_path, validation=validation, runtime_result=runtime_result)
    zip_path = create_zip(
        project_path
    )

    # ------------------------------------------------------------------
    # 10a. Docker Deployment Validation (V5.1)
    # ------------------------------------------------------------------
    docker_result = None
    if can_export:
        print("\n=== DOCKER DEPLOYMENT VALIDATION (V5.1) ===")
        try:
            docker_result = run_docker_validation(project_path)
            if docker_result.skipped:
                print(f"Docker skipped: {docker_result.skip_reason}")
            elif docker_result.success:
                print(f"Docker PASS — build+run+health in {docker_result.duration}s")
            else:
                print(
                    f"Docker FAIL — build:{docker_result.build_passed} "
                    f"run:{docker_result.run_passed} health:{docker_result.health_passed} "
                    f"({docker_result.duration}s)"
                )
                if docker_result.error:
                    print(f"  Error: {docker_result.error[:200]}")
        except Exception as _de:
            print(f"Docker validation error: {_de}")

    # ------------------------------------------------------------------
    # 10b. Production Readiness Critic (V5.2) — separate from forge_score
    # ------------------------------------------------------------------
    production_result = None
    try:
        production_result = run_production_critic(project_path)
        print_production_report(production_result)
    except Exception as _prod_err:
        print(f"Production critic error: {_prod_err}")

    # ------------------------------------------------------------------
    # 10c. Repository Intelligence — README, API docs, ER diagram (V5.6)
    # ------------------------------------------------------------------
    repo_docs_result = None
    try:
        from app.services.repo_intelligence_service import generate_repo_docs
        repo_docs_result = generate_repo_docs(project_path, plan, architecture)
    except Exception as _rd_err:
        print(f"Repo docs error: {_rd_err}")

    # ------------------------------------------------------------------
    # 10. Scoring & Reporting
    # ------------------------------------------------------------------
    forge_score = calculate_forge_score(
        validation,
        runtime_result,
        frontend_build_result,
        playwright_result,
        vision_result=vision_result,
        docker_result=docker_result,
    )
    print(f"\nForge Score: {forge_score}")

    pipeline_metrics = build_pipeline_metrics(
        validation, runtime_result, frontend_build_result, playwright_result
    )
    print(f"Pipeline Metrics: {pipeline_metrics}")

    # V5.7: Print and persist cost summary
    try:
        print_session_cost()
        flush_to_log(
            project_name=plan.get("project_name", "unknown"),
            forge_score=forge_score.get("score", 0),
            total_wall_time_s=time.time() - start,
        )
    except Exception:
        pass

    # V4: Record this run in failure memory so future generations learn from it
    try:
        record_failure_run(
            project_name=plan.get("project_name", "unknown"),
            validation_errors=validation.get("errors", []),
            runtime_error_type=(
                runtime_result.get("parsed_error", {}).get("type")
                if runtime_result else None
            ),
            frontend_build_failed=(
                frontend_build_result is not None
                and not frontend_build_result.get("node_missing", False)
                and not frontend_build_result.get("success", True)
            ),
            score=forge_score.get("score", 0),
        )
    except Exception as _e:
        pass  # never let memory recording crash the pipeline

    # ------------------------------------------------------------------
    # Continuous Learning — update knowledge bases after every run
    # ------------------------------------------------------------------
    _runtime_success = runtime_result.get("success", False) if runtime_result else False
    _score = forge_score.get("score", 0)

    try:
        from app.knowledge.arch_db import arch_db
        arch_db.record(idea, architecture, plan, score=_score)
    except Exception:
        pass

    try:
        from app.knowledge.component_db import component_db
        component_db.record_run(
            project_path,
            success=_runtime_success,
            forge_score=_score,
        )
    except Exception:
        pass

    try:
        from app.providers.model_router import record_provider_outcome
        _provider_used = provider if provider != "auto" else "auto"
        record_provider_outcome(idea, _provider_used, _score, _runtime_success)
    except Exception:
        pass

    try:
        from app.knowledge.project_history import project_history, build_run_from_result
        _full_result = {
            "architecture": architecture,
            "validation": validation,
            "runtime": runtime_result,
            "frontend_build": frontend_build_result,
            "forge_score": forge_score,
            "pipeline_metrics": pipeline_metrics,
            "project_path": project_path,
            "stats": {"fix_attempts": fix_attempts_used},
            "generation_time_seconds": total_time,
        }
        _run = build_run_from_result(idea, _full_result, provider=provider)
        project_history.record(_run)
    except Exception:
        pass

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
        "frontend_build": frontend_build_result,
        "critic": critic_result,
        "production_critic": {
            "production_score": production_result.production_score if production_result else None,
            "categories": production_result.categories if production_result else {},
            "issue_count": len(production_result.issues) if production_result else 0,
            "critical_count": sum(1 for i in production_result.issues if i.severity == "critical") if production_result else 0,
            "skipped": production_result.skipped if production_result else True,
        } if production_result else None,
        "playwright": {
            "success": playwright_result.success if playwright_result else None,
            "pages_checked": playwright_result.pages_checked if playwright_result else 0,
            "console_errors": playwright_result.console_errors if playwright_result else [],
            "blank_pages": playwright_result.blank_pages if playwright_result else [],
            "duration": playwright_result.duration if playwright_result else 0,
        } if playwright_result else None,
        "vision": {
            "ui_score": vision_result.ui_score if vision_result else None,
            "success": vision_result.success if vision_result else None,
            "issues": [
                {"severity": i.severity, "page": i.page, "description": i.description}
                for i in (vision_result.issues if vision_result else [])
            ],
            "page_scores": vision_result.page_scores if vision_result else {},
            "skipped": vision_result.skipped if vision_result else True,
        } if vision_result else None,
        "docker": {
            "success": docker_result.success if docker_result else None,
            "build_passed": docker_result.build_passed if docker_result else None,
            "run_passed": docker_result.run_passed if docker_result else None,
            "health_passed": docker_result.health_passed if docker_result else None,
            "duration": docker_result.duration if docker_result else 0,
            "error": docker_result.error if docker_result else None,
            "skipped": docker_result.skipped if docker_result else True,
        } if docker_result else None,
        "repo_docs": {
            "files_written": repo_docs_result.files_written if repo_docs_result else [],
            "skipped": repo_docs_result.skipped if repo_docs_result else True,
        } if repo_docs_result else None,
        "journey": (runtime_result or {}).get("journey"),
        "stats": validation_stats,
        "pipeline_metrics": pipeline_metrics,
        "generation_time_seconds": total_time,
        "first_pass_compile": first_pass_compile,
        "first_pass_error_count": first_pass_error_count,
        "needs_repair": fix_attempts_used > 0,
        "stage_timings": _t,
        "cost_breakdown": {
            "generation_usd": round(_cost_before_repairs, 5),
            "repairs_usd":    round(_cost_after_repairs - _cost_before_repairs, 5),
            "total_usd":      round(_cost_after_repairs, 5),
        },
        "repair_efficiency": {
            "fix_attempts":        fix_attempts_used,
            "files_changed":       len(_repair_files_changed),
            "success_after_repair": validation.get("passed", False),
        },
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
