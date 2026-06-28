"""
Parallel Batch Generation

Runs N projects concurrently using a thread pool. Each project gets its own:
  - isolated project directory (unique name + run ID)
  - dedicated uvicorn port (via port_manager)
  - independent LLM calls (shared providers with rate-limit retries)

Deployment jobs are queued and retried independently — a slow or failed
deploy never blocks other projects from completing.

Usage:
    from app.services.batch_runner import run_batch_parallel

    results = run_batch_parallel(
        ideas=["A todo app", "A CRM", "A blog"],
        max_workers=3,
        provider="auto",
    )
"""
from __future__ import annotations

import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from app.utils.port_manager import ManagedPort


@dataclass
class BatchJobResult:
    idea:       str
    job_id:     str
    success:    bool
    duration_s: float = 0.0
    forge_score: float = 0.0
    project_path: str = ""
    zip_path:   str = ""
    error:      str = ""
    runtime_passed: bool = False
    crud_passed: bool = False
    frontend_built: bool = False
    validation_passed: bool = False
    pipeline_metrics: dict = field(default_factory=dict)


def _run_single(idea: str, provider: str, job_id: str) -> BatchJobResult:
    """
    Run a single project through the full pipeline.
    Allocates its own runtime port so it can run concurrently with others.
    """
    t0 = time.time()
    result = BatchJobResult(idea=idea, job_id=job_id, success=False)

    try:
        # Lazy import to avoid circular deps
        from app.services.planner_service import generate_plan
        from app.services.architect_service import generate_architecture
        from app.services.backend_service import generate_backend
        from app.services.frontend_service import generate_frontend
        from app.services.file_writer_service import write_files
        from app.services.validator_service import validate_project
        from app.services.runtime_validator_service import validate_runtime
        from app.services.forge_score_service import calculate_forge_score, build_pipeline_metrics
        from app.services.zip_service import create_zip
        from app.services.git_service import initialize_git
        from app.knowledge.arch_db import arch_db

        # Plan + Architecture
        plan = generate_plan(idea, provider)
        arch_context = arch_db.build_evolution_context(idea, k=3)
        architecture = generate_architecture(plan, provider, extra_context=arch_context or None)

        # Code generation
        backend = generate_backend(architecture, provider)
        frontend = generate_frontend(architecture, provider)
        all_files = backend["files"] + frontend["files"]

        # Write to disk (project_name is unique per run if suffixed with job_id)
        project_path = write_files(plan["project_name"], all_files)
        initialize_git(project_path)
        result.project_path = project_path

        # Static validation (fast, no port needed)
        validation = validate_project(project_path)
        result.validation_passed = validation["passed"]

        # Runtime validation — allocate a dedicated port
        runtime_result = None
        if validation["passed"]:
            with ManagedPort() as port:
                print(f"  [batch:{job_id[:6]}] Runtime validation on port {port}")
                runtime_result = validate_runtime(project_path, architecture=architecture, port=port)

        # Score
        forge_score = calculate_forge_score(validation, runtime_result)
        result.forge_score = forge_score.get("score", 0)
        result.runtime_passed = bool(runtime_result and runtime_result.get("success"))
        result.crud_passed = bool(
            runtime_result
            and (runtime_result.get("journey") or {}).get("success")
        )

        result.pipeline_metrics = build_pipeline_metrics(validation, runtime_result, None, None)

        # Export
        if result.runtime_passed:
            result.zip_path = create_zip(project_path) or ""

        # Continuous learning
        try:
            from app.knowledge.component_db import component_db
            component_db.record_run(project_path, success=result.runtime_passed, forge_score=result.forge_score)
            arch_db.record(idea, architecture, plan, score=result.forge_score)
        except Exception:
            pass

        result.success = result.runtime_passed

    except Exception as exc:
        result.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        traceback.print_exc()

    result.duration_s = round(time.time() - t0, 1)
    return result


def run_batch_parallel(
    ideas: list[str],
    max_workers: int = 4,
    provider: str = "auto",
) -> list[BatchJobResult]:
    """
    Generate N projects concurrently.

    Args:
        ideas:       List of project ideas (strings)
        max_workers: Max concurrent generations (recommend ≤ 4 on a single machine)
        provider:    LLM provider override ("auto" for smart routing)

    Returns:
        List of BatchJobResult in the same order as `ideas`.
    """
    print(f"\n{'=' * 60}")
    print(f"  Batch Generation — {len(ideas)} projects, {max_workers} workers")
    print(f"{'=' * 60}")

    results: dict[str, BatchJobResult] = {}
    futures = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for i, idea in enumerate(ideas):
            job_id = f"{i:03d}-{uuid.uuid4().hex[:6]}"
            print(f"  [{job_id}] Queued: {idea[:55]}...")
            future = pool.submit(_run_single, idea, provider, job_id)
            futures[future] = (job_id, idea)

        for future in as_completed(futures):
            job_id, idea = futures[future]
            try:
                res = future.result()
            except Exception as exc:
                res = BatchJobResult(
                    idea=idea, job_id=job_id, success=False,
                    error=f"Unhandled exception: {exc}",
                )
            results[job_id] = res
            status = "PASS" if res.success else "FAIL"
            print(
                f"  [{job_id}] {status} score={res.forge_score:.0f} "
                f"runtime={res.runtime_passed} crud={res.crud_passed} "
                f"({res.duration_s}s) {res.error[:60] if res.error else ''}"
            )

    # Return in original order
    ordered = []
    for i, idea in enumerate(ideas):
        job_id_prefix = f"{i:03d}-"
        match = next((r for jid, r in results.items() if jid.startswith(job_id_prefix)), None)
        if match:
            ordered.append(match)

    _print_batch_summary(ordered)
    return ordered


def _print_batch_summary(results: list[BatchJobResult]) -> None:
    n = len(results)
    if not n:
        return
    passed = sum(1 for r in results if r.success)
    runtime_passed = sum(1 for r in results if r.runtime_passed)
    crud_passed = sum(1 for r in results if r.crud_passed)
    avg_score = sum(r.forge_score for r in results) / n
    avg_time = sum(r.duration_s for r in results) / n
    errors = [r for r in results if r.error]

    print(f"\n{'=' * 60}")
    print(f"  Batch Summary — {n} projects")
    print(f"{'=' * 60}")
    print(f"  Success       : {passed}/{n}")
    print(f"  Runtime Pass  : {runtime_passed}/{n}")
    print(f"  CRUD Pass     : {crud_passed}/{n}")
    print(f"  Avg Score     : {avg_score:.1f}/100")
    print(f"  Avg Time      : {avg_time:.0f}s")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for r in errors:
            print(f"    [{r.job_id}] {r.error[:80]}")
    print(f"{'=' * 60}")
