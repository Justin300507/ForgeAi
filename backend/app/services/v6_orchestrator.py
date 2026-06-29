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
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.services.product_manager_service import run_product_manager
from app.services.tech_lead_service import run_tech_lead
from app.services.architect_service import generate_architecture
from app.services.frontend_service import generate_frontend
from app.services.file_writer_service import write_files
from app.services.validator_service import validate_project
from app.services.fixer_service import generate_fix
from app.services.missing_file_service import generate_missing_file
from app.services.fix_writer_service import write_fix
from app.services.fix_log_service import save_fix_log
from app.services.zip_service import create_zip
from app.services.forge_score_service import calculate_forge_score
from app.services.security_reviewer_service import run_security_review, print_security_report
from app.services.qa_reviewer_service import run_qa_review
from app.services.code_review_service import run_code_review
from app.services.performance_review_service import run_performance_review
from app.services.agent_collaboration import AgentCollaboration
from app.services.v6_score_service import calculate_v6_score
from app.services.metadata_service import save_metadata
from app.services.git_service import initialize_git
from app.services.architecture_fix_service import generate_architecture_fix
from app.memory.failure_memory import record_run, build_prompt_injection
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


def generate_project_v6(
    idea: str,
    provider: str = "auto",
    use_parallel_backend: bool = True,
    collab: AgentCollaboration | None = None,
    skip_reviews: bool = False,
    frontend_target: str = "web",
    arch_template: str = "",     # optional: Architecture DB template injection
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
            "frontend": 0, "reviews": 0, "repairs": 0, "runtime_fixes": 0}

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
        resp = generate_frontend(architecture, provider, frontend_target=frontend_target, idea=idea)
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
    project_path = write_files(project_name, all_files, frontend_target=frontend_target)
    initialize_git(project_path)
    print(f"\n=== FILES WRITTEN: {project_path} ({len(all_files)} files) ===")

    # ------------------------------------------------------------------
    # Deterministic patcher — runs before validation, no LLM cost
    # Fixes: passlib→bcrypt, missing FK imports, async+sync ORM, smart quotes
    # ------------------------------------------------------------------
    print("\n=== DETERMINISTIC PATCHER ===")
    run_deterministic_patches(project_path)
    from app.services.database_patcher import patch_database_py
    patch_database_py(project_path)

    total_time_so_far = round(time.time() - start, 2)
    metadata_path = save_metadata(project_path, plan, architecture, provider, total_time_so_far)

    # ------------------------------------------------------------------
    # Stage 7-10: Review Agents (QA, Security, Code Review, Performance)
    # Skip when skip_reviews=True to save ~14% token cost per app
    # ------------------------------------------------------------------
    qa_report = None
    security_report = None
    code_review_report = None
    performance_report = None

    if not skip_reviews:
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
        _llm["reviews"] = 4  # QA + Security + CodeReview + Performance
    else:
        print("\n  [skip_reviews=True] Skipping QA/Security/Code/Performance reviews")

    # ------------------------------------------------------------------
    # Stage 11: Validation Loop (up to 4 fix attempts)
    # ------------------------------------------------------------------
    print("\n=== VALIDATION LOOP (V6) ===")
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

            m = re.search(r"(app[\\/][^\s:]+?\.(?:py|txt))", err)
            if m:
                errors_by_file[m.group(1).replace("\\", "/")].append(err)
                continue

            m = re.search(r"Missing frontend import target:\s+(\S+)", err)
            if m:
                imp = m.group(1)
                name = os.path.basename(imp.replace(".jsx", ""))
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
                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                    continue

                # Never LLM-fix database.py — patch_database_py() injects a
                # known-good version after this loop.  LLM fixes cache incorrectly
                # and overwrite the template with broken versions.
                if filepath in ("app/database.py", "app\\database.py"):
                    from app.services.database_patcher import patch_database_py
                    patch_database_py(project_path)
                    continue

                if not os.path.exists(abs_path):
                    fix = generate_missing_file(filepath, "\n".join(file_errors), provider)
                    _llm["repairs"] += 1
                    if fix and fix.get("content"):
                        fix["path"] = _sanitize_path(fix["path"])
                        write_fix(project_path, fix)
                        save_fix_log(project_path, "Missing File", fix)
                    continue

                with open(abs_path, "r", encoding="utf-8") as fh:
                    file_content = fh.read()

                fix = generate_fix(filepath, file_content, file_errors, provider)
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

        validation = validate_project(project_path)
        print(f"  Post-fix {attempt + 1}: {'PASS' if validation['passed'] else 'FAIL'} — {len(validation['errors'])} errors")

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
                # Re-run patcher so any new route files get schema stubs created
                run_deterministic_patches(project_path)
                validation = validate_project(project_path)
                print(f"  Post-arch-repair: {'PASS' if validation['passed'] else 'FAIL'}")

    # ------------------------------------------------------------------
    # Stage 12: Runtime Validation
    # ------------------------------------------------------------------
    runtime_result = None
    if validation["passed"]:
        # Re-run database patcher: the validation fix loop may have overwritten
        # database.py from fix cache with a version missing create_tables().
        from app.services.database_patcher import patch_database_py
        patch_database_py(project_path)

        print("\n=== RUNTIME VALIDATION (V6) ===")
        max_runtime_attempts = 2
        try:
            from app.services.runtime_fix_service import generate_runtime_fix
            for r_attempt in range(max_runtime_attempts + 1):
                runtime_result = validate_runtime(project_path, architecture=architecture)
                print(f"  Runtime: {'PASS' if runtime_result.get('success') else 'FAIL'}")

                if runtime_result.get("success"):
                    break
                if r_attempt == max_runtime_attempts:
                    break

                rt_fix = generate_runtime_fix(runtime_result, project_path, provider)
                _llm["runtime_fixes"] += 1
                if rt_fix and rt_fix.get("path") and rt_fix.get("content"):
                    rt_fix["path"] = _sanitize_path(rt_fix["path"])
                    write_fix(project_path, rt_fix)
                # Re-run patcher so LLM fixes get cleaned up (response_model=ORM class,
                # missing schema stubs, broken bcrypt, etc.) before next runtime check
                run_deterministic_patches(project_path)
                # Re-inject database.py — the LLM fix may have overwritten it
                patch_database_py(project_path)
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
    zip_path = None
    if can_export:
        zip_path = create_zip(project_path)
        print(f"\n  ZIP: {zip_path}")
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
    print("\n=== DETERMINISTIC PATCHER ===")
    run_deterministic_patches(project_path)
    from app.services.database_patcher import patch_database_py
    patch_database_py(project_path)

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
            m = re.search(r"(app[\\/][^\s:]+?\.(?:py|txt))", err)
            if m:
                errors_by_file[m.group(1).replace("\\", "/")].append(err)
                continue
            m = re.search(r"Missing frontend import target:\s+(\S+)", err)
            if m:
                imp = m.group(1)
                name = os.path.basename(imp.replace(".jsx", ""))
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
                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                    continue
                if not os.path.exists(abs_path):
                    fix = generate_missing_file(filepath, "\n".join(file_errors), provider)
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
            # Re-run patcher so any new route files get schema stubs created
            run_deterministic_patches(project_path)
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
    zip_path = None
    if can_export:
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
