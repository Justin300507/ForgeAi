"""
V6 Autonomous Engineering Team Orchestrator

Pipeline:
  Product Manager → Architect → Tech Lead Review → Backend Team (parallel)
  → Frontend Team → QA Review → Security Review → Code Review
  → Performance Review → Validation Loop → Runtime → Export → V6 Score

Each agent contributes its findings to an AgentCollaboration shared memory
that subsequent agents use to make better decisions.
"""
import time
import traceback
import re
import os
from collections import defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.services.product_manager_service import run_product_manager
from app.services.tech_lead_service import run_tech_lead
from app.services.architect_service import generate_architecture
from app.services.frontend_service import generate_frontend
from app.services.file_writer_service import write_files
from app.services.frontend_scaffold_service import ensure_app_jsx
from app.services.validator_service import validate_project
from app.services.fixer_service import generate_fix
from app.services.missing_file_service import generate_missing_file
from app.services.fix_writer_service import write_fix
from app.services.fix_log_service import save_fix_log
from app.services.zip_service import create_zip, write_debug_report
from app.services.forge_score_service import calculate_forge_score
from app.services.security_reviewer_service import run_security_review, print_security_report
from app.services.qa_reviewer_service import run_qa_review
from app.services.code_review_service import run_code_review
from app.services.performance_review_service import run_performance_review
from app.services.frontend_critic_service import run_frontend_critic
from app.services.agent_collaboration import AgentCollaboration
from app.services.v6_score_service import calculate_v6_score
from app.services.metadata_service import save_metadata
from app.services.git_service import initialize_git
from app.services.architecture_fix_service import generate_architecture_fix
from app.memory.failure_memory import record_run, build_prompt_injection
from app.repair.auth_completeness import ensure_auth_completeness
from app.utils.cost_tracker import reset_session, flush_to_log, print_session_cost
from app.services.runtime_validator_service import validate_runtime
from app.services.deterministic_patcher import run_deterministic_patches


ARCHITECTURE_ERROR_MARKERS = (
    "Architecture violation",
    "Missing endpoint",
    "Router export mismatch",
    "Missing symbol",
    "Missing APIRouter",
    "No endpoints found",
    "Undefined symbol",
)


def _sanitize_path(path: str) -> str:
    safe = re.sub(r"[?{}:*<>|\"]", "_", path)
    safe = os.path.normpath(safe)
    # Block path traversal: if normpath still starts with ".." or is absolute, strip leading ..
    parts = safe.replace("\\", "/").split("/")
    parts = [p for p in parts if p and p != ".."]
    safe = os.path.join(*parts) if parts else safe
    return safe


def _sanitize_architecture_paths(architecture: dict) -> None:
    for ep in architecture.get("api_endpoints", []):
        if "file" in ep:
            ep["file"] = _sanitize_path(ep["file"])


def _run_initial_deterministic_patches(project_path: str) -> int:
    """
    Exp053: Stage 1 of the repair pipeline's 3-stage pattern (initial
    deterministic patch -> database-shape patches -> App.jsx scaffold),
    extracted after confirming byte-identical logic between
    generate_project_v6's initial pass and repair_project()'s initial pass
    (Experiment 051's audit flagged this as duplicated structure -- a
    future ordering/patcher-list change to one flow had no structural
    forcing function to also apply it to the other).

    Stages 2 (architecture repair) and 3 (runtime fix loop) were
    investigated too and found to have REAL behavioral divergence --
    the main flow gates architecture repair on a `target_files`
    extraction repair_project() doesn't have, and tracks LLM-call metrics
    repair_project() doesn't -- so those two stages were deliberately NOT
    extracted, to avoid changing either flow's behavior. See
    docs/REPAIR_ARCHITECTURE.md for the full comparison.

    Returns the field-mismatch fix count. generate_project_v6 logs it;
    repair_project() discards it -- both match their pre-existing
    behavior exactly (repair_project() never logged this count before
    this extraction either).
    """
    print("\n=== DETERMINISTIC PATCHER ===")
    run_deterministic_patches(project_path)
    from app.services.database_patcher import (
        patch_database_py, patch_model_field_mismatches, patch_add_missing_model_columns,
        patch_add_missing_schema_fields, patch_missing_required_constructor_kwargs,
        patch_filter_dict_unpack_constructor_kwargs,
    )
    patch_database_py(project_path)
    n_field_fixes = patch_model_field_mismatches(project_path)
    patch_add_missing_model_columns(project_path)
    patch_add_missing_schema_fields(project_path)
    patch_missing_required_constructor_kwargs(project_path)
    patch_filter_dict_unpack_constructor_kwargs(project_path)
    if ensure_app_jsx(project_path):
        print("  [scaffold] Synthesized missing src/App.jsx from existing pages")
    return n_field_fixes or 0


def generate_project_v6(
    idea: str,
    provider: str = "auto",
    use_parallel_backend: bool = True,
    collab: AgentCollaboration | None = None,
    skip_reviews: bool = False,
    frontend_target: str = "web",
    arch_template: str = "",     # optional: Architecture DB template injection
    style_override: str | None = None,
    motion_intensity: str | None = None,
    include_landing_page: bool = False,
) -> dict[str, Any]:
    """
    Full V6 multi-agent pipeline.

    Agents in order:
    1. Product Manager  — richer spec with user stories + acceptance criteria
    2. Architect        — generates architecture plan
    3. Tech Lead        — reviews architecture, sets security/pagination constraints
    4. Backend Team     — parallel file-by-file generation
    5. Frontend Team    — React generation
    6. QA Team          — validates against user stories
    7. Security Team    — LLM-based security audit + static pattern checks
    8. Code Review      — naming, architecture quality, maintainability, tech debt
    9. Performance Review — N+1, missing indexes, large payloads, slow APIs
    10. Validation Loop  — static analysis + fix attempts
    11. Runtime          — uvicorn smoke tests
    12. Export + V6 Score
    """
    start = time.time()
    reset_session()

    if collab is None:
        collab = AgentCollaboration()

    # LLM call counter — printed at end of every run for visibility
    _llm = {"planner": 0, "architect": 0, "tech_lead": 0, "backend": 0,
            "frontend": 0, "reviews": 0, "polish": 0, "repairs": 0, "runtime_fixes": 0}

    print(f"\n{'#'*70}")
    print(f"# V6 AUTONOMOUS ENGINEERING TEAM")
    print(f"# Idea: {idea[:65]}")
    print(f"{'#'*70}")

    # ------------------------------------------------------------------
    # Stage 1: Product Manager
    # ------------------------------------------------------------------
    product_spec = run_product_manager(idea, provider)
    _llm["planner"] += 1
    plan = product_spec.to_plan_dict()
    collab.record_decision(
        agent="product_manager",
        decision="product_spec_created",
        rationale=product_spec.tagline,
        data={
            "features": len(product_spec.core_features),
            "user_stories": len(product_spec.user_stories),
            "must_have": [f.name for f in product_spec.core_features if f.priority == "must_have"],
        },
    )

    # ------------------------------------------------------------------
    # Stage 2: Architect
    # ------------------------------------------------------------------
    print("\n=== ARCHITECT (V6) ===")
    learned = build_prompt_injection()
    pm_context = (
        f"PRODUCT SPEC SUMMARY:\n"
        f"  Tagline: {product_spec.tagline}\n"
        f"  Users: {', '.join(product_spec.target_users[:2])}\n"
        f"  Must-have features: {', '.join(f.description for f in product_spec.core_features if f.priority == 'must_have')[:300]}\n"
        f"  Non-functional: {product_spec.non_functional_requirements}\n"
        f"  User stories: {len(product_spec.user_stories)}\n"
    )
    extra_context = learned + "\n\n" + pm_context if learned else pm_context
    if arch_template:
        extra_context = arch_template + "\n\n" + extra_context
    architecture = generate_architecture(plan, provider, extra_context=extra_context)
    _llm["architect"] += 1
    _sanitize_architecture_paths(architecture)
    collab.record_decision(
        agent="architect",
        decision="architecture_created",
        rationale=f"{len(architecture.get('api_endpoints', []))} endpoints across {len(set(ep.get('file','') for ep in architecture.get('api_endpoints', [])))} files",
        data={"endpoints": len(architecture.get("api_endpoints", []))},
    )

    # ------------------------------------------------------------------
    # Stage 3: Tech Lead Review
    # ------------------------------------------------------------------
    tech_constraints = run_tech_lead(product_spec.raw if hasattr(product_spec, "raw") else plan, architecture, provider)
    _llm["tech_lead"] += 1
    collab.record_decision(
        agent="tech_lead",
        decision="constraints_set",
        rationale=tech_constraints.tech_review_summary[:150],
        data={
            "critical_issues": sum(1 for i in tech_constraints.architecture_issues if i.severity == "critical"),
            "auth_endpoints": len(tech_constraints.security_requirements.get("authenticated_endpoints", [])),
            "pagination_required": len(tech_constraints.pagination_required),
        },
    )

    # ------------------------------------------------------------------
    # Stage 4+5: Backend Team + Frontend Team — run in parallel
    # Both stages only need `architecture` as input so they can overlap.
    # Backend takes ~7s (internally parallel per wave); frontend ~28s.
    # Parallel total = max(7, 28) = 28s instead of 35s sequential.
    # ------------------------------------------------------------------
    def _run_backend():
        nonlocal use_parallel_backend
        if use_parallel_backend:
            try:
                from app.services.parallel_backend_service import generate_backend_parallel
                print("\n=== BACKEND TEAM — PARALLEL GENERATION (V6) ===")
                result = generate_backend_parallel(
                    architecture, provider,
                    tech_constraints=tech_constraints.to_prompt_context(),
                )
                files = [{"path": f.path, "content": f.content} for f in result.files if f.success]
                if result.failed_files:
                    print(f"  Parallel failures: {result.failed_files}")
                print(
                    f"  Generated {len(files)} files in {result.total_duration:.1f}s "
                    f"(parallel phase: {result.parallel_duration:.1f}s)"
                )
                return files
            except Exception as pe:
                print(f"  Parallel backend failed ({pe}), falling back to monolithic")
                use_parallel_backend = False
        from app.services.backend_service import generate_backend
        print("\n=== BACKEND TEAM — MONOLITHIC (fallback) ===")
        resp = generate_backend(architecture, provider)
        return resp.get("files", []) if isinstance(resp, dict) else []

    def _run_frontend():
        print("\n=== FRONTEND TEAM (V6) ===")
        resp = generate_frontend(
            architecture, provider, frontend_target=frontend_target, idea=idea,
            style_override=style_override, motion_intensity=motion_intensity,
            include_landing_page=include_landing_page,
        )
        return resp.get("files", []) if isinstance(resp, dict) else []

    print("\n=== BACKEND + FRONTEND — PARALLEL ===")
    t_gen_start = time.time()
    with ThreadPoolExecutor(max_workers=2) as _pool:
        _f_backend = _pool.submit(_run_backend)
        _f_frontend = _pool.submit(_run_frontend)
        backend_files = _f_backend.result()
        frontend_files = _f_frontend.result()
    # backend generates N files (1 LLM call per file); frontend = 1 call
    _llm["backend"] = len(backend_files)
    _llm["frontend"] = 1
    print(f"  Backend+Frontend done in {time.time() - t_gen_start:.1f}s")

    # ------------------------------------------------------------------
    # Stage 6: Write Files
    # ------------------------------------------------------------------
    all_files = []
    for f in backend_files + frontend_files:
        f["path"] = _sanitize_path(f.get("path", ""))
        if f["path"]:
            all_files.append(f)

    # Deduplicate
    seen = {}
    for f in all_files:
        seen[f["path"]] = f
    all_files = list(seen.values())

    # Ensure main.py always has /health and CORS (covers both parallel and monolithic paths)
    from app.services.parallel_backend_service import _ensure_main_py_quality
    for f in all_files:
        if f.get("path") in ("app/main.py", "app\\main.py") and f.get("content"):
            f["content"] = _ensure_main_py_quality(f["content"])

    project_name = plan["project_name"]
    project_path = write_files(project_name, all_files, frontend_target=frontend_target, idea=idea)
    initialize_git(project_path)
    print(f"\n=== FILES WRITTEN: {project_path} ({len(all_files)} files) ===")

    # ------------------------------------------------------------------
    # Deterministic patcher — runs before validation, no LLM cost
    # Fixes: passlib→bcrypt, missing FK imports, async+sync ORM, smart quotes
    # ------------------------------------------------------------------
    _n_field_fixes = _run_initial_deterministic_patches(project_path)
    if _n_field_fixes:
        print(f"  [field_patcher] Fixed model-field mismatches in {_n_field_fixes} route file(s)")

    total_time_so_far = round(time.time() - start, 2)
    metadata_path = save_metadata(project_path, plan, architecture, provider, total_time_so_far)

    # Reviews run after validation (Stage 11) — see below.
    qa_report = None
    security_report = None
    code_review_report = None
    performance_report = None
    frontend_critic_report = None

    # ------------------------------------------------------------------
    # Stage 7: Validation Loop (up to 4 fix attempts)
    # ------------------------------------------------------------------
    print("\n=== VALIDATION LOOP (V6) ===")
    validation = validate_project(project_path)
    print(f"  Validation: {'PASS' if validation['passed'] else 'FAIL'} — {len(validation['errors'])} errors")

    fix_attempts_used = 0
    # Files whose cache-hit fix didn't resolve errors — bypass cache next round
    _bypass_cache_files: set[str] = set()
    # Bug 3: track the error set from the last completed attempt; if it's identical
    # after a revert, there's no point retrying the same approach.
    _last_pre_attempt_errors: frozenset = frozenset()

    # Print initial errors for visibility
    if not validation["passed"] and validation.get("errors"):
        print(f"\n  Validation errors:")
        for _e in validation["errors"][:8]:
            print(f"    • {_e}")

    for attempt in range(4):
        if validation["passed"]:
            break

        # Bug 3: if the error set is identical to the last completed attempt, no LLM
        # approach will make progress — skip straight to Architecture Repair.
        _curr_errors = frozenset(validation["errors"])
        if attempt > 0 and _curr_errors == _last_pre_attempt_errors:
            print(f"  [fix loop] Error set unchanged after revert — skipping to Architecture Repair")
            break
        _last_pre_attempt_errors = _curr_errors

        fix_attempts_used = attempt + 1
        _prev_err_count = len(validation["errors"])
        print(f"\n  Fix attempt {attempt + 1}/4 ...")

        errors_by_file = defaultdict(list)
        for err in validation["errors"]:
            if err.startswith("Unknown dependency:"):
                errors_by_file["app/requirements.txt"].append(err)
                continue

            if "Missing endpoint" in err or "Router export mismatch" in err:
                m = re.search(r"expected in (app[\\/][^\s]+\.py)", err)
                if m:
                    errors_by_file[m.group(1).replace("\\", "/")].append(err)
                    continue

            # "Missing symbol 'X' in PROTECTED_FILE (imported from SOURCE_FILE)"
            # Re-attribute to the importing file — fixing a protected file via LLM
            # is blocked, so the fix must go to the file that has the bad import.
            _PROTECTED = {"app/utils/auth.py", "app/routes/auth_routes.py", "app/database.py"}
            ms = re.search(
                r"Missing symbol .+ in (app[\\/][^\s:]+?\.py) \(imported from (app[\\/][^\s:]+?\.py)\)",
                err,
            )
            if ms:
                target = ms.group(1).replace("\\", "/")
                source = ms.group(2).replace("\\", "/")
                attributed = source if target in _PROTECTED else target
                errors_by_file[attributed].append(err)
                continue

            m = re.search(r"(app[\\/][^\s:]+?\.(?:py|txt))", err)
            if m:
                errors_by_file[m.group(1).replace("\\", "/")].append(err)
                continue

            # "Frontend auth field mismatch: src/pages/RegisterPage.jsx POSTs to..."
            # (validate_route_quality's auth-contract check). None of the patterns
            # above match a bare src/... path, so without this rule the diagnostic
            # is silently dropped from errors_by_file every attempt — no fix is ever
            # dispatched for it and it recurs identically until the fix loop gives up
            # (seen live: 3 attempts, zero LLM calls against RegisterPage.jsx, same
            # error each time).
            m = re.search(r"Frontend auth field mismatch:\s+(\S+\.(?:jsx|js|tsx|ts))", err)
            if m:
                errors_by_file[m.group(1).replace("\\", "/")].append(err)
                continue

            m = re.search(r"Missing frontend import target:\s+(\S+)", err)
            if m:
                imp = m.group(1)
                # Preserve the import's own subdirectory (e.g. "./contexts/AuthContext"
                # -> "src/contexts/AuthContext.jsx") instead of collapsing everything
                # to a flat components/pages guess — the missing file must be created
                # at the exact path the bundler will actually resolve.
                imp_rel = imp
                while imp_rel.startswith("../") or imp_rel.startswith("./"):
                    imp_rel = imp_rel[3:] if imp_rel.startswith("../") else imp_rel[2:]
                imp_rel = imp_rel.replace(".jsx", "").replace(".js", "")
                if "/" in imp_rel:
                    errors_by_file[f"src/{imp_rel}.jsx"].append(err)
                else:
                    name = imp_rel
                    for subdir in ("components", "pages"):
                        if subdir in imp:
                            errors_by_file[f"src/{subdir}/{name}.jsx"].append(err)
                            break
                    else:
                        errors_by_file[f"src/{name}.jsx"].append(err)

        # Save current contents so we can revert if the fixes make things worse
        _saved_for_revert: dict[str, str] = {}
        for _fp in errors_by_file:
            _ap = os.path.join(project_path, _sanitize_path(_fp))
            if os.path.exists(_ap):
                try:
                    with open(_ap, "r", encoding="utf-8") as _fh:
                        _saved_for_revert[_ap] = _fh.read()
                except Exception:
                    pass

        # app/main.py has no error of its own but is rewritten in-place by the
        # post-fix patcher batch below (orphan-router wiring, service-stub creation,
        # etc.) — snapshot it unconditionally so a revert can restore it too.
        _main_py_path = os.path.join(project_path, "app", "main.py")
        if os.path.exists(_main_py_path) and _main_py_path not in _saved_for_revert:
            try:
                with open(_main_py_path, "r", encoding="utf-8") as _fh:
                    _saved_for_revert[_main_py_path] = _fh.read()
            except Exception:
                pass

        # Full file-path snapshot so a revert can also delete files newly created
        # during this attempt (by write_fix/generate_missing_file or by the post-fix
        # patcher batch) — otherwise a "reverted" attempt still leaves orphaned files
        # (dangling imports, duplicate classes, mis-wired routers) that corrupt the
        # next fix iteration.
        _EXCLUDE_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv"}
        _pre_attempt_paths: set[str] = set()
        for _root, _dirs, _fnames in os.walk(project_path):
            _dirs[:] = [d for d in _dirs if d not in _EXCLUDE_DIRS]
            for _fn in _fnames:
                _pre_attempt_paths.add(os.path.normpath(os.path.join(_root, _fn)))

        for filepath, file_errors in errors_by_file.items():
            try:
                safe_path = _sanitize_path(filepath)
                abs_path = os.path.join(project_path, safe_path)

                if any("Orphan file:" in e for e in file_errors):
                    # Wire the orphan router into main.py instead of deleting it —
                    # deleting removes all its endpoints, turning 1 error into many.
                    from app.services.deterministic_patcher import _patch_wire_orphan_routers
                    _patch_wire_orphan_routers(Path(project_path))
                    continue

                if any("Router export mismatch" in e for e in file_errors):
                    # The file exists and almost always already declares a router —
                    # just under a different name than router_export_validator.py
                    # expects. Alias it deterministically instead of an LLM rename,
                    # which risks missing a decorator reference. Falls through to
                    # the LLM path below only if THIS file's mismatch is still
                    # present after the patch (zero or multiple APIRouter()
                    # assignments — ambiguous) — never worse than today.
                    from app.services.deterministic_patcher import _patch_router_export_mismatch
                    _patch_router_export_mismatch(Path(project_path))
                    _expected_router = os.path.basename(filepath).replace("_routes.py", "") + "_router"
                    if os.path.exists(abs_path):
                        with open(abs_path, "r", encoding="utf-8") as _rf:
                            _post_patch_content = _rf.read()
                        if re.search(
                            rf"^{re.escape(_expected_router)}\s*(?::|=)",
                            _post_patch_content, re.MULTILINE,
                        ):
                            continue

                # Never LLM-fix patcher-injected files — the patcher owns these
                # and always injects known-good templates.  A cached LLM fix would
                # overwrite the template with a potentially broken version.
                if filepath in ("app/database.py", "app\\database.py"):
                    from app.services.database_patcher import patch_database_py
                    patch_database_py(project_path)
                    continue
                if filepath in ("app/routes/auth_routes.py", "app\\routes\\auth_routes.py",
                                "app/utils/auth.py", "app\\utils\\auth.py"):
                    from app.services.deterministic_patcher import _patch_auth_routes, _patch_auth_utils
                    _patch_auth_utils(Path(project_path))
                    _patch_auth_routes(Path(project_path))
                    continue

                if not os.path.exists(abs_path):
                    # seed_routes.py: never call the LLM -- it generates wrong-project content
                    # (gym/hospital models) because it has no project context. Try the
                    # deterministic ADR-002 seeder first (reuses entity_metadata.py to seed
                    # real lookup/reference tables); fall back to the static zero-insert
                    # stub only if that returns nothing usable. Never let this branch fail
                    # generation outright -- the static stub is always the safety net.
                    if filepath in ("app/routes/seed_routes.py", "app\\routes\\seed_routes.py"):
                        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                        from app.services.deterministic_seed_generator import generate
                        _source, _telemetry = generate(project_path)
                        if _source is not None:
                            with open(abs_path, "w", encoding="utf-8") as _sf:
                                _sf.write(_source)
                            print(
                                f"  [patcher] ADR-002 deterministic seed_routes.py generated "
                                f"({_telemetry['lookup_entities']} lookup entities, "
                                f"{_telemetry['generation_time_ms']}ms)"
                            )
                            for _line in _telemetry["exclusions"]:
                                print(f"  [patcher]   {_line}")
                        else:
                            with open(abs_path, "w", encoding="utf-8") as _sf:
                                _sf.write(
                                    "from fastapi import APIRouter, Depends\n"
                                    "from sqlalchemy.orm import Session\n"
                                    "from app.database import get_db\n\n"
                                    "seed_router = APIRouter()\n\n"
                                    "@seed_router.post('/seed')\n"
                                    "def seed_data(db: Session = Depends(get_db)):\n"
                                    "    return {'seeded': True, 'message': 'Demo data ready'}\n"
                                )
                            print(
                                f"  [patcher] Generated minimal seed_routes.py stub "
                                f"(ADR-002 fallback: {_telemetry['fallback_reason']})"
                            )
                        continue
                    fix = generate_missing_file(filepath, "\n".join(file_errors), provider, project_path=project_path)
                    _llm["repairs"] += 1
                    if fix and fix.get("content"):
                        fix["path"] = _sanitize_path(fix["path"])
                        write_fix(project_path, fix)
                        save_fix_log(project_path, "Missing File", fix)
                    continue

                with open(abs_path, "r", encoding="utf-8") as fh:
                    file_content = fh.read()

                fix = generate_fix(filepath, file_content, file_errors, provider,
                                   bypass_cache=filepath in _bypass_cache_files)
                _llm["repairs"] += 1
                if not isinstance(fix, dict) or not fix.get("path") or not fix.get("content"):
                    continue

                fix["path"] = _sanitize_path(fix["path"])
                written = write_fix(project_path, fix)
                if not written and fix.get("content"):
                    # Fix had SyntaxError — retry once with explicit feedback
                    import ast as _ast
                    try:
                        _ast.parse(fix["content"])
                    except SyntaxError as se:
                        syntax_hint = [f"Your previous fix had a SyntaxError: {se}. Fix the syntax error before returning."]
                        fix2 = generate_fix(filepath, fix["content"], file_errors + syntax_hint, provider)
                        _llm["repairs"] += 1
                        if isinstance(fix2, dict) and fix2.get("path") and fix2.get("content"):
                            fix2["path"] = _sanitize_path(fix2["path"])
                            write_fix(project_path, fix2)
                            fix = fix2
                save_fix_log(project_path, "\n".join(file_errors), fix)

            except Exception as e:
                print(f"  Fix failed for {filepath}: {e}")

        # Re-run key patchers so LLM-regenerated files get common issues fixed immediately.
        # This prevents "fix makes things worse" cascades where the LLM introduces
        # wrong field names, missing Optional fields, or missing service stubs.
        try:
            from pathlib import Path as _Path
            from app.services.deterministic_patcher import (
                _patch_model_aliases, _patch_schemas_from_attributes,
                _patch_missing_pydantic_imports, _patch_attr_access_mismatches,
                _patch_missing_ownership_assignment,
                _patch_response_schemas_optional, _patch_create_missing_service_stubs,
                _patch_wire_orphan_routers, _patch_wire_orphan_frontend_routes,
                _patch_login_redirect_target, _patch_frontend_package_json,
            )
            _patch_model_aliases(_Path(project_path))
            _patch_schemas_from_attributes(_Path(project_path))
            _patch_missing_pydantic_imports(_Path(project_path))
            _patch_attr_access_mismatches(_Path(project_path))
            _patch_missing_ownership_assignment(_Path(project_path))
            _patch_response_schemas_optional(_Path(project_path))
            _patch_create_missing_service_stubs(_Path(project_path))
            _patch_wire_orphan_routers(_Path(project_path))
            # Frontend mirror of the router-wiring line above: pages created by
            # this very fix attempt (e.g. BadgesPage.jsx, because Navigation.jsx
            # already linked to /badges) need to be imported and routed into
            # App.jsx too, or they stay permanently orphaned -- App.jsx is only
            # ever scaffolded once, before these pages exist. Invisible to every
            # automated check since none of them click through the sidebar.
            _patch_wire_orphan_frontend_routes(_Path(project_path))
            # Must run after the line above: if the app's main authenticated
            # page isn't literally named "Dashboard", login/register's
            # hardcoded navigate('/dashboard') matches no route at all and
            # silently bounces back to /login even though auth succeeded.
            _patch_login_redirect_target(_Path(project_path))
            _patch_frontend_package_json(_Path(project_path))
        except Exception as _pe:
            print(f"  [post-fix patcher] {_pe}")

        validation = validate_project(project_path)
        _new_err_count = len(validation["errors"])
        print(f"  Post-fix {attempt + 1}: {'PASS' if validation['passed'] else 'FAIL'} — {_new_err_count} errors")

        # Revert if this fix attempt made things significantly worse.
        # Happens when a fallback provider (DeepSeek, Groq) writes partial/wrong code.
        if _new_err_count > _prev_err_count + 1:
            print(f"  [revert] Errors jumped {_prev_err_count}→{_new_err_count} — reverting bad fixes")
            _post_attempt_paths: set[str] = set()
            for _root, _dirs, _fnames in os.walk(project_path):
                _dirs[:] = [d for d in _dirs if d not in _EXCLUDE_DIRS]
                for _fn in _fnames:
                    _post_attempt_paths.add(os.path.normpath(os.path.join(_root, _fn)))
            for _new_path in _post_attempt_paths - _pre_attempt_paths:
                try:
                    os.remove(_new_path)
                except Exception:
                    pass
            for _ap, _content in _saved_for_revert.items():
                try:
                    with open(_ap, "w", encoding="utf-8") as _fh:
                        _fh.write(_content)
                except Exception:
                    pass
            validation = validate_project(project_path)
            print(f"  [revert] Restored: {len(validation['errors'])} errors")
        elif not validation["passed"] and validation.get("errors"):
            for _e in validation["errors"][:5]:
                print(f"    • {_e}")

        # Track files that still have errors after this attempt — if those errors were
        # handled via a cache hit, bypass the cache next attempt to get a fresh fix.
        still_broken: set[str] = set()
        for err in validation["errors"]:
            m = re.search(r"(app[\\/][^\s:]+?\.py)", err)
            if m:
                still_broken.add(m.group(1).replace("\\", "/"))
        _bypass_cache_files = still_broken & errors_by_file.keys()

    # Architecture-level repair
    architecture_errors = [
        e for e in validation.get("errors", [])
        if any(marker in e for marker in ARCHITECTURE_ERROR_MARKERS)
    ]
    if architecture_errors and not validation["passed"]:
        print("\n=== ARCHITECTURE REPAIR (V6) ===")
        target_files = sorted({
            m.group(1)
            for e in architecture_errors
            for m in [re.search(r"(app[\\/][^\s:]+?\.py)", e)]
            if m
        })
        if target_files:
            arch_fix = generate_architecture_fix(
                architecture, architecture_errors, provider,
                required_exports={}, required_endpoints={}, existing_symbols={}
            )
            _llm["repairs"] += 1
            if arch_fix and isinstance(arch_fix, dict) and arch_fix.get("files"):
                for f in arch_fix["files"]:
                    f["path"] = _sanitize_path(f["path"])
                    write_fix(project_path, f)
                # Re-run patchers after arch repair — but skip auth_routes/auth_utils
                # injection so the repair's output is not overwritten by the static template.
                run_deterministic_patches(project_path, skip_protected_injections=True)
                from app.services.database_patcher import patch_database_py
                patch_database_py(project_path)
                # Exp071: skip_protected_injections above deliberately trusts
                # arch_fix's own output for auth_routes.py/auth_utils.py --
                # but generate_architecture_fix() has no instruction to
                # preserve auth wiring, so if its output touched main.py or
                # auth_routes.py, the one safety net that would normally
                # catch a resulting gap was just disabled. ensure_auth_completeness()
                # is an independent, unconditional check -- only overwrites
                # if the end state is actually broken, so a correct arch_fix
                # output is untouched; a broken one is deterministically healed.
                ensure_auth_completeness(project_path, project_name=project_name)
                validation = validate_project(project_path)
                print(f"  Post-arch-repair: {'PASS' if validation['passed'] else 'FAIL'}")

    # ------------------------------------------------------------------
    # Stage 8-11: Review Agents — only run on validated code
    # Skipped if validation still fails after all fix attempts (broken code
    # would produce stale review findings that get thrown away anyway).
    # ------------------------------------------------------------------
    if validation["passed"] and not skip_reviews:
        print("\n=== REVIEWS (QA / Security / Code / Performance) ===")
        qa_report = run_qa_review(
            project_path,
            product_spec.raw if hasattr(product_spec, "raw") and product_spec.raw else plan,
            architecture,
            provider,
        )
        collab.record_decision(
            agent="qa",
            decision="qa_review_complete",
            rationale=f"QA score: {qa_report.qa_score}/100",
            data={"score": qa_report.qa_score, "blockers": qa_report.blockers},
        )

        security_report = run_security_review(project_path, provider)
        print_security_report(security_report)
        collab.record_decision(
            agent="security",
            decision="security_review_complete",
            rationale=f"Security score: {security_report.security_score}/100",
            data={"score": security_report.security_score},
        )

        code_review_report = run_code_review(project_path, architecture, provider)
        collab.record_decision(
            agent="code_review",
            decision="code_review_complete",
            rationale=f"Maintainability: {code_review_report.maintainability_score}/100",
            data={"maintainability_score": code_review_report.maintainability_score},
        )

        performance_report = run_performance_review(project_path, architecture, provider)
        collab.record_decision(
            agent="performance",
            decision="performance_review_complete",
            rationale=f"Performance score: {performance_report.performance_score}/100",
            data={"score": performance_report.performance_score},
        )

        frontend_critic_report = run_frontend_critic(
            project_path, idea, provider,
            style_override=style_override, motion_intensity=motion_intensity,
            include_landing_page=include_landing_page,
        )
        collab.record_decision(
            agent="frontend_critic",
            decision="frontend_critic_complete",
            rationale=f"Design score: {frontend_critic_report.design_score}/100",
            data={"score": frontend_critic_report.design_score},
        )
        _llm["reviews"] = 5

        # UI Polish pass — only spend an extra LLM call per flagged file when
        # the critic found something genuinely critical/high severity (same
        # bar the validation fix loop implicitly uses). Reuses the EXISTING
        # generate_fix()/write_fix() machinery from the validation loop above
        # instead of new patch-writing code.
        polish_targets = frontend_critic_report.polish_targets
        if polish_targets:
            print(f"\n=== UI POLISH PASS ({len(polish_targets)} file(s)) ===")
            _polish_snapshot: dict[str, str] = {}
            _polished_any = False
            for rel_path, fix_notes in polish_targets[:5]:
                try:
                    abs_path = os.path.join(project_path, _sanitize_path(rel_path))
                    if not os.path.exists(abs_path):
                        continue
                    with open(abs_path, "r", encoding="utf-8") as fh:
                        file_content = fh.read()
                    _polish_snapshot[abs_path] = file_content
                    polish_fix = generate_fix(rel_path, file_content, fix_notes, provider)
                    _llm["polish"] += 1
                    if isinstance(polish_fix, dict) and polish_fix.get("path") and polish_fix.get("content"):
                        polish_fix["path"] = _sanitize_path(polish_fix["path"])
                        write_fix(project_path, polish_fix)
                        _polished_any = True
                        print(f"  [polish] {rel_path} patched")
                except Exception as pe:
                    print(f"  [polish] {rel_path} failed: {pe}")

            # A bad polish edit should never be allowed to ship a previously-
            # passing app broken — revert every touched file if validation
            # regresses, same safety net the main fix loop uses.
            if _polished_any:
                _prev_passed = validation["passed"]
                validation = validate_project(project_path)
                print(f"  Post-polish validation: {'PASS' if validation['passed'] else 'FAIL'}")
                if _prev_passed and not validation["passed"]:
                    print("  [polish] Regressed a passing app — reverting polish edits")
                    for _ap, _content in _polish_snapshot.items():
                        try:
                            with open(_ap, "w", encoding="utf-8") as _fh:
                                _fh.write(_content)
                        except Exception:
                            pass
                    validation = validate_project(project_path)
                    print(f"  [polish] Reverted: {'PASS' if validation['passed'] else 'FAIL'}")
    elif skip_reviews:
        print("\n  [skip_reviews=True] Skipping QA/Security/Code/Performance reviews")
    else:
        print("\n  [skip_reviews] Validation failed — skipping reviews to save credits")

    # ------------------------------------------------------------------
    # Stage 12: Runtime Validation
    # ------------------------------------------------------------------
    runtime_result = None
    if validation["passed"]:
        # Re-run database patcher: the validation fix loop may have overwritten
        # database.py from fix cache with a version missing create_tables().
        # Exp057: also brings the 5 model/schema cleanup patchers the
        # runtime-fix loop below calls (line ~840) into scope. Before
        # Exp053, those names were imported inline right at the
        # top of this same function and stayed in scope for the rest of its
        # body; Exp053 moved that import into a separate helper function
        # (_run_initial_deterministic_patches) without noticing this loop,
        # ~40 lines later but still in the SAME function, depended on it
        # staying in scope -- silently turning every one of these bare calls
        # into a NameError that the loop's own try/except swallowed. Widening
        # this already-in-scope, already-correct import (proven correct by
        # patch_database_py's own working use at line ~829 below) is the
        # minimal fix -- no new import statement, no duplicate of the
        # helper's own separately-scoped import.
        from app.services.database_patcher import (
            patch_database_py, patch_model_field_mismatches, patch_add_missing_model_columns,
            patch_add_missing_schema_fields, patch_missing_required_constructor_kwargs,
            patch_filter_dict_unpack_constructor_kwargs,
        )
        patch_database_py(project_path)

        print("\n=== RUNTIME VALIDATION (V6) ===")
        max_runtime_attempts = 3
        try:
            from app.services.runtime_fix_service import generate_runtime_fix
            _last_rt_sig: object = None
            for r_attempt in range(max_runtime_attempts + 1):
                runtime_result = validate_runtime(project_path, architecture=architecture)
                print(f"  Runtime: {'PASS' if runtime_result.get('success') else 'FAIL'}")

                if runtime_result.get("success"):
                    break
                if r_attempt == max_runtime_attempts:
                    break

                # Stagnation guard: if failure signature is identical to last attempt,
                # further LLM calls will produce the same broken fix — stop early.
                # .get("journey", {}) only substitutes {} when the key is absent —
                # when the runner completes but never reaches the CRUD journey (e.g.
                # health check failed first), "journey" is present and explicitly
                # None, so .get(..., {}) still returns None and crashes on .get("steps").
                _rt_sig = frozenset(
                    (s.get("name", ""), s.get("passed", False), str(s.get("detail", "")))
                    for s in (runtime_result.get("journey") or {}).get("steps", [])
                )
                if r_attempt > 0 and _rt_sig == _last_rt_sig:
                    print("  [runtime fix] Failure signature unchanged — stopping retries")
                    break
                _last_rt_sig = _rt_sig

                rt_fix = generate_runtime_fix(runtime_result, project_path, provider)
                _llm["runtime_fixes"] += 1
                if rt_fix and rt_fix.get("path") and rt_fix.get("content"):
                    rt_fix["path"] = _sanitize_path(rt_fix["path"])
                    write_fix(project_path, rt_fix)
                # Re-run patcher so LLM fixes get cleaned up (response_model=ORM class,
                # missing schema stubs, broken bcrypt, etc.) before next runtime check
                run_deterministic_patches(project_path)
                patch_model_field_mismatches(project_path)
                patch_add_missing_model_columns(project_path)
                patch_add_missing_schema_fields(project_path)
                patch_missing_required_constructor_kwargs(project_path)
                patch_filter_dict_unpack_constructor_kwargs(project_path)
                # Re-inject database.py — the LLM fix may have overwritten it
                patch_database_py(project_path)
                # Delete stale SQLite db + WAL files so the next uvicorn process
                # starts with a clean db instead of replaying a broken WAL
                # (a failed db.commit() leaves the WAL in a dirty state; each busy_timeout
                # retry during startup costs 5s, causing 30s+ startup hangs)
                for _db_fname in (
                    "app.db", "app.db-wal", "app.db-shm",
                    "forgeai.db", "forgeai.db-wal", "forgeai.db-shm",
                ):
                    _db_path = os.path.join(project_path, _db_fname)
                    try:
                        if os.path.isfile(_db_path):
                            os.remove(_db_path)
                    except Exception:
                        pass
        except Exception as re_err:
            runtime_result = {"success": False, "error": str(re_err)}
            print(f"  Runtime validation error: {re_err}")

    # ------------------------------------------------------------------
    # Stage 13: Export
    # ------------------------------------------------------------------
    can_export = (
        validation["passed"]
        and runtime_result
        and runtime_result.get("success", False)
    )
    if not can_export:
        write_debug_report(project_path, validation=validation, runtime_result=runtime_result)
    zip_path = create_zip(project_path)
    print(f"\n  ZIP: {zip_path}")
    if can_export:
        try:
            from app.services.repo_intelligence_service import generate_repo_docs
            generate_repo_docs(project_path, plan, architecture)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Stage 14: Score
    # ------------------------------------------------------------------
    forge_score = calculate_forge_score(
        validation, runtime_result,
        frontend_build_result=None, playwright_result=None,
        vision_result=None, docker_result=None,
    )

    v6_score = calculate_v6_score(
        forge_score=forge_score,
        security_report=security_report,
        performance_report=performance_report,
        code_review_report=code_review_report,
        qa_report=qa_report,
        runtime_result=runtime_result,
    )

    total_time = round(time.time() - start, 2)
    print(f"\n=== V6 SCORE: {v6_score['total']}/100 ({v6_score['grade']}) ===")
    print(f"    Runtime:      {v6_score['dimensions']['runtime']}/100")
    print(f"    Security:     {v6_score['dimensions']['security']}/100")
    print(f"    Performance:  {v6_score['dimensions']['performance']}/100")
    print(f"    Maintainability: {v6_score['dimensions']['maintainability']}/100")
    print(f"    Review:       {v6_score['dimensions']['review']}/100")

    # LLM call summary — target: ≤6 calls per run
    _total_llm = sum(_llm.values())
    print(f"\n{'─'*40}")
    print(f"  LLM Calls")
    print(f"  {'Planner':<18} {_llm['planner']}")
    print(f"  {'Architect':<18} {_llm['architect']}")
    print(f"  {'Tech Lead':<18} {_llm['tech_lead']}")
    print(f"  {'Backend':<18} {_llm['backend']}")
    print(f"  {'Frontend':<18} {_llm['frontend']}")
    print(f"  {'Reviews':<18} {_llm['reviews']}")
    print(f"  {'Polish':<18} {_llm['polish']}")
    print(f"  {'Repairs':<18} {_llm['repairs']}")
    print(f"  {'Runtime Fixes':<18} {_llm['runtime_fixes']}")
    print(f"  {'─'*20}")
    print(f"  {'Total':<18} {_total_llm}")
    print(f"{'─'*40}")

    # Cost tracking
    try:
        print_session_cost()
        flush_to_log(project_name, v6_score.get("total", 0), total_time)
    except Exception:
        pass

    # Failure memory
    try:
        runtime_error_type = (
            runtime_result.get("parsed_error", {}).get("type")
            if runtime_result and not runtime_result.get("success")
            else None
        )
        record_run(
            project_name=project_name,
            validation_errors=validation.get("errors", []),
            runtime_error_type=runtime_error_type,
            frontend_build_failed=False,
            score=v6_score.get("total", 0),
        )
    except Exception:
        pass

    # Save collaboration log
    collab.record_decision(
        agent="orchestrator",
        decision="pipeline_complete",
        rationale=f"V6 score: {v6_score['total']}/100",
        data={"total_time": total_time, "can_export": can_export},
    )
    collab.save(project_path)

    return {
        "pipeline": "v6",
        "project_name": project_name,
        "project_path": project_path,
        "zip_path": zip_path,
        "metadata_path": metadata_path,
        "plan": plan,
        "architecture": architecture,
        "product_spec": {
            "display_name": product_spec.display_name,
            "tagline": product_spec.tagline,
            "user_stories": len(product_spec.user_stories),
            "must_have_features": [f.name for f in product_spec.core_features if f.priority == "must_have"],
        },
        "tech_lead": {
            "summary": tech_constraints.tech_review_summary,
            "critical_issues": sum(1 for i in tech_constraints.architecture_issues if i.severity == "critical"),
            "warnings": sum(1 for i in tech_constraints.architecture_issues if i.severity == "warning"),
        },
        "backend_generation": {
            "parallel": use_parallel_backend,
            "files": len(backend_files),
        },
        "qa": {
            "score": qa_report.qa_score if qa_report else None,
            "stories_covered": sum(1 for s in qa_report.user_story_coverage if s.covered) if qa_report else 0,
            "blockers": qa_report.blockers if qa_report else [],
            "skipped": True if qa_report is None else qa_report.skipped,
        },
        "security": {
            "score": security_report.security_score if security_report else None,
            "risk_level": security_report.risk_level if security_report else "skipped",
            "critical_count": security_report.critical_count if security_report else 0,
            "high_count": security_report.high_count if security_report else 0,
            "skipped": True if security_report is None else security_report.skipped,
        },
        "code_review": {
            "naming_score": code_review_report.naming_score if code_review_report else None,
            "architecture_score": code_review_report.architecture_score if code_review_report else None,
            "maintainability_score": code_review_report.maintainability_score if code_review_report else None,
            "tech_debt_score": code_review_report.tech_debt_score if code_review_report else None,
            "overall_score": code_review_report.overall_score if code_review_report else None,
            "skipped": True if code_review_report is None else code_review_report.skipped,
        },
        "performance": {
            "score": performance_report.performance_score if performance_report else None,
            "n_plus_one_count": performance_report.n_plus_one_count if performance_report else 0,
            "missing_indexes": performance_report.missing_indexes if performance_report else 0,
            "large_payload_risks": performance_report.large_payload_risks if performance_report else 0,
            "slow_api_warnings": performance_report.slow_api_warnings if performance_report else 0,
            "skipped": True if performance_report is None else performance_report.skipped,
        },
        "frontend_critic": {
            "design_score": frontend_critic_report.design_score if frontend_critic_report else None,
            "hierarchy_score": frontend_critic_report.hierarchy_score if frontend_critic_report else None,
            "compliance_score": frontend_critic_report.compliance_score if frontend_critic_report else None,
            "polish_score": frontend_critic_report.polish_score if frontend_critic_report else None,
            "consistency_score": frontend_critic_report.consistency_score if frontend_critic_report else None,
            "files_polished": _llm["polish"],
            "skipped": True if frontend_critic_report is None else frontend_critic_report.skipped,
        },
        "validation": validation,
        "runtime": runtime_result,
        "forge_score": forge_score,
        "v6_score": v6_score,
        "collaboration_log": collab.get_summary(),
        "generation_time_seconds": total_time,
        "fix_attempts": fix_attempts_used,
    }


def repair_project(
    project_path: str,
    plan: dict,
    architecture: dict,
    provider: str = "auto",
) -> dict:
    """
    Resume from an existing project directory — skip all generation stages.
    Runs: deterministic patcher → validation fix loop → runtime fix loop → score.
    Called by retry logic when files already exist on disk.
    """
    start = time.time()
    project_name = plan.get("project_name", os.path.basename(project_path))

    print(f"\n{'#'*70}")
    print(f"# REPAIR (skip generation — files already exist)")
    print(f"# Project: {project_name}")
    print(f"{'#'*70}")

    # Deterministic patches first
    _run_initial_deterministic_patches(project_path)

    # Validation fix loop
    print("\n=== VALIDATION LOOP (REPAIR) ===")
    validation = validate_project(project_path)
    print(f"  Validation: {'PASS' if validation['passed'] else 'FAIL'} — {len(validation['errors'])} errors")

    fix_attempts_used = 0
    for attempt in range(4):
        if validation["passed"]:
            break
        fix_attempts_used = attempt + 1
        print(f"\n  Fix attempt {attempt + 1}/4 ...")
        errors_by_file = defaultdict(list)
        for err in validation["errors"]:
            if err.startswith("Unknown dependency:"):
                errors_by_file["app/requirements.txt"].append(err)
                continue
            if "Missing endpoint" in err or "Router export mismatch" in err:
                m = re.search(r"expected in (app[\\/][^\s]+\.py)", err)
                if m:
                    errors_by_file[m.group(1).replace("\\", "/")].append(err)
                    continue
            _PROTECTED2 = {"app/utils/auth.py", "app/routes/auth_routes.py", "app/database.py"}
            ms2 = re.search(
                r"Missing symbol .+ in (app[\\/][^\s:]+?\.py) \(imported from (app[\\/][^\s:]+?\.py)\)",
                err,
            )
            if ms2:
                target2 = ms2.group(1).replace("\\", "/")
                source2 = ms2.group(2).replace("\\", "/")
                errors_by_file[source2 if target2 in _PROTECTED2 else target2].append(err)
                continue
            m = re.search(r"(app[\\/][^\s:]+?\.(?:py|txt))", err)
            if m:
                errors_by_file[m.group(1).replace("\\", "/")].append(err)
                continue
            m = re.search(r"Missing frontend import target:\s+(\S+)", err)
            if m:
                imp = m.group(1)
                imp_rel = imp
                while imp_rel.startswith("../") or imp_rel.startswith("./"):
                    imp_rel = imp_rel[3:] if imp_rel.startswith("../") else imp_rel[2:]
                imp_rel = imp_rel.replace(".jsx", "").replace(".js", "")
                if "/" in imp_rel:
                    errors_by_file[f"src/{imp_rel}.jsx"].append(err)
                else:
                    name = imp_rel
                    for subdir in ("components", "pages"):
                        if subdir in imp:
                            errors_by_file[f"src/{subdir}/{name}.jsx"].append(err)
                            break
                    else:
                        errors_by_file[f"src/{name}.jsx"].append(err)

        for filepath, file_errors in errors_by_file.items():
            try:
                safe_path = _sanitize_path(filepath)
                abs_path = os.path.join(project_path, safe_path)
                if any("Orphan file:" in e for e in file_errors):
                    from app.services.deterministic_patcher import _patch_wire_orphan_routers
                    _patch_wire_orphan_routers(Path(project_path))
                    continue
                if any("Router export mismatch" in e for e in file_errors):
                    from app.services.deterministic_patcher import _patch_router_export_mismatch
                    _patch_router_export_mismatch(Path(project_path))
                    _expected_router = os.path.basename(filepath).replace("_routes.py", "") + "_router"
                    if os.path.exists(abs_path):
                        with open(abs_path, "r", encoding="utf-8") as _rf:
                            _post_patch_content = _rf.read()
                        if re.search(
                            rf"^{re.escape(_expected_router)}\s*(?::|=)",
                            _post_patch_content, re.MULTILINE,
                        ):
                            continue
                if not os.path.exists(abs_path):
                    fix = generate_missing_file(filepath, "\n".join(file_errors), provider, project_path=project_path)
                    if fix and fix.get("content"):
                        fix["path"] = _sanitize_path(fix["path"])
                        write_fix(project_path, fix)
                        save_fix_log(project_path, "Missing File", fix)
                    continue
                with open(abs_path, "r", encoding="utf-8") as fh:
                    file_content = fh.read()
                fix = generate_fix(filepath, file_content, file_errors, provider)
                if not isinstance(fix, dict) or not fix.get("path") or not fix.get("content"):
                    continue
                fix["path"] = _sanitize_path(fix["path"])
                write_fix(project_path, fix)
                save_fix_log(project_path, "\n".join(file_errors), fix)
            except Exception as e:
                print(f"  Fix failed for {filepath}: {e}")

        # Re-run key patchers so LLM-regenerated files get common issues fixed immediately.
        try:
            from pathlib import Path as _Path
            from app.services.deterministic_patcher import (
                _patch_model_aliases, _patch_schemas_from_attributes,
                _patch_missing_pydantic_imports, _patch_attr_access_mismatches,
                _patch_missing_ownership_assignment,
                _patch_response_schemas_optional, _patch_create_missing_service_stubs,
                _patch_wire_orphan_routers, _patch_wire_orphan_frontend_routes,
                _patch_login_redirect_target, _patch_frontend_package_json,
            )
            # This batch previously imported _patch_passlib_references and
            # _patch_field_alignment, neither of which exist in
            # deterministic_patcher.py -- the ImportError was swallowed by the
            # except below, so this entire post-fix patcher batch has been a
            # silent no-op on every "Fix & Retry" repair run. Replaced with the
            # same working list generate_project_v6's fix loop uses.
            _patch_model_aliases(_Path(project_path))
            _patch_schemas_from_attributes(_Path(project_path))
            _patch_missing_pydantic_imports(_Path(project_path))
            _patch_attr_access_mismatches(_Path(project_path))
            _patch_missing_ownership_assignment(_Path(project_path))
            _patch_response_schemas_optional(_Path(project_path))
            _patch_create_missing_service_stubs(_Path(project_path))
            _patch_wire_orphan_routers(_Path(project_path))
            _patch_wire_orphan_frontend_routes(_Path(project_path))
            _patch_login_redirect_target(_Path(project_path))
            _patch_frontend_package_json(_Path(project_path))
        except Exception as _pe:
            print(f"  [post-fix patcher] {_pe}")

        validation = validate_project(project_path)
        print(f"  Post-fix {attempt + 1}: {'PASS' if validation['passed'] else 'FAIL'} — {len(validation['errors'])} errors")

    # Architecture repair
    architecture_errors = [
        e for e in validation.get("errors", [])
        if any(marker in e for marker in ARCHITECTURE_ERROR_MARKERS)
    ]
    if architecture_errors and not validation["passed"]:
        print("\n=== ARCHITECTURE REPAIR ===")
        arch_fix = generate_architecture_fix(
            architecture, architecture_errors, provider,
            required_exports={}, required_endpoints={}, existing_symbols={}
        )
        if arch_fix and isinstance(arch_fix, dict) and arch_fix.get("files"):
            for f in arch_fix["files"]:
                f["path"] = _sanitize_path(f["path"])
                write_fix(project_path, f)
            run_deterministic_patches(project_path, skip_protected_injections=True)
            from app.services.database_patcher import patch_database_py as _pdb2
            _pdb2(project_path)
            # Exp071: see the matching comment in generate_project_v6() --
            # skip_protected_injections above trusts arch_fix's own output;
            # this is the independent, unconditional safety net for when
            # that trust turns out to be misplaced.
            ensure_auth_completeness(project_path, project_name=project_name)
            validation = validate_project(project_path)
            print(f"  Post-arch-repair: {'PASS' if validation['passed'] else 'FAIL'}")

    # Runtime validation
    runtime_result = None
    if validation["passed"]:
        from app.services.database_patcher import patch_database_py
        patch_database_py(project_path)

        print("\n=== RUNTIME VALIDATION (REPAIR) ===")
        try:
            from app.services.runtime_fix_service import generate_runtime_fix
            for r_attempt in range(3):
                runtime_result = validate_runtime(project_path, architecture=architecture)
                print(f"  Runtime: {'PASS' if runtime_result.get('success') else 'FAIL'}")
                if runtime_result.get("success"):
                    break
                if r_attempt == 2:
                    break
                rt_fix = generate_runtime_fix(runtime_result, project_path, provider)
                if rt_fix and rt_fix.get("path") and rt_fix.get("content"):
                    rt_fix["path"] = _sanitize_path(rt_fix["path"])
                    write_fix(project_path, rt_fix)
                # Re-run patcher after each LLM runtime fix
                run_deterministic_patches(project_path)
                # Re-inject database.py — the LLM fix may have overwritten it
                patch_database_py(project_path)
        except Exception as re_err:
            runtime_result = {"success": False, "error": str(re_err)}
            print(f"  Runtime error: {re_err}")

    # Export
    can_export = validation["passed"] and runtime_result and runtime_result.get("success", False)
    if not can_export:
        write_debug_report(project_path, validation=validation, runtime_result=runtime_result)
    zip_path = create_zip(project_path)

    forge_score = calculate_forge_score(
        validation, runtime_result,
        frontend_build_result=None, playwright_result=None,
        vision_result=None, docker_result=None,
    )

    total_time = round(time.time() - start, 2)
    print(f"\n=== REPAIR COMPLETE in {total_time}s — forge_score={forge_score} ===")

    return {
        "pipeline": "repair",
        "project_name": project_name,
        "project_path": project_path,
        "zip_path": zip_path,
        "validation": validation,
        "runtime": runtime_result,
        "forge_score": forge_score,
        "generation_time_seconds": total_time,
        "fix_attempts": fix_attempts_used,
    }
