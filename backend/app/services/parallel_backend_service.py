"""
V6 Parallel Backend Service

Generates backend files in focused, parallel waves instead of one
monolithic LLM call. Each file gets its own full-context prompt.

Wave order (respects dependency graph):
  1. Foundation  — database.py (template), requirements.txt (deterministic)
  2. Models      — one call per DB table, all parallel
  3. Schemas     — one call per resource, all parallel (uses model content)
  4. Routes      — one call per route file, all parallel (uses models + schemas)
  5. main.py     — sequential, after all route files are known

Drop-in replacement for generate_backend() — returns same dict shape:
  {"files": [{"path": "...", "content": "..."}, ...]}
"""
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from app.providers.ai_provider import generate_content
from app.prompts.parallel_backend_prompt import (
    FASTAPI_CONTRACT,
    build_model_prompt,
    build_schema_prompt,
    build_route_prompt,
    build_main_prompt,
    build_requirements_content,
)
from app.memory.failure_memory import build_prompt_injection
from app.templates.database_template import DATABASE_PY_TEMPLATE
from app.utils.json_cleaner import extract_json


@dataclass
class FileResult:
    path: str
    content: str
    success: bool
    error: str = ""
    duration: float = 0.0


@dataclass
class ParallelBackendResult:
    files: list[FileResult] = field(default_factory=list)
    total_duration: float = 0.0
    parallel_duration: float = 0.0
    failed_files: list[str] = field(default_factory=list)
    success: bool = True


# ── Helpers ──────────────────────────────────────────────────────────────────

def _call_llm(prompt: str, provider: str, max_tokens: int = 8000) -> dict | None:
    """Call LLM and parse JSON response. Returns None on failure."""
    try:
        text = generate_content(prompt, provider, max_tokens=max_tokens)
        if not text:
            return None
        text = text.replace("```json", "").replace("```", "").strip()
        return extract_json(text)
    except Exception as e:
        return {"_error": str(e)}


def _make_file_result(data: dict | None, expected_path: str, t0: float) -> FileResult:
    elapsed = time.time() - t0
    if not data or "_error" in data:
        err = (data or {}).get("_error", "LLM returned None or invalid JSON")
        return FileResult(path=expected_path, content="", success=False, error=err, duration=elapsed)
    path = data.get("path", expected_path)
    content = data.get("content", "")
    if not content.strip():
        return FileResult(path=path, content="", success=False, error="Empty content", duration=elapsed)
    return FileResult(path=path, content=content, success=True, duration=elapsed)


def _group_endpoints_by_resource(endpoints: list[dict]) -> dict[str, list[dict]]:
    """Group api_endpoints by the first path segment (resource name)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for ep in endpoints:
        path = ep.get("path", "")
        file_field = ep.get("file", "")
        if file_field:
            # Use explicit file field if present
            resource = file_field.replace("app/routes/", "").replace("_routes.py", "")
        else:
            parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
            resource = parts[0] if parts else "misc"
        groups[resource].append(ep)
    return dict(groups)


def _parallel_wave(tasks: list[tuple], max_workers: int = 5) -> list[FileResult]:
    """
    Run tasks in parallel. Each task is (fn, *args) -> FileResult.
    Returns results in completion order.
    """
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fn, *args): label for fn, label, *args in tasks}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                label = futures[future]
                results.append(FileResult(path=label, content="", success=False, error=str(e)))
    return results


# ── Wave generators ───────────────────────────────────────────────────────────

def _gen_model(entity: dict, all_tables: list[dict], provider: str, contract: str, memory: str) -> FileResult:
    t0 = time.time()
    name = entity.get("table_name", "unknown")
    columns = [c if isinstance(c, dict) else c.dict() for c in entity.get("columns", [])]
    prompt = build_model_prompt(name, columns, all_tables, contract, memory)
    data = _call_llm(prompt, provider)
    result = _make_file_result(data, f"app/models/{name.lower()}.py", t0)
    status = "OK" if result.success else f"FAIL:{result.error[:60]}"
    print(f"    [model] {name}: {status} ({result.duration:.1f}s)")
    return result


def _gen_schema(resource: str, model_content: str, endpoints: list[dict], provider: str, contract: str, memory: str) -> FileResult:
    t0 = time.time()
    prompt = build_schema_prompt(resource, model_content, endpoints, contract, memory)
    data = _call_llm(prompt, provider)
    result = _make_file_result(data, f"app/schemas/{resource}.py", t0)
    status = "OK" if result.success else f"FAIL:{result.error[:60]}"
    print(f"    [schema] {resource}: {status} ({result.duration:.1f}s)")
    return result


def _gen_route(
    resource: str,
    route_file: str,
    endpoints: list[dict],
    model_contents: dict[str, str],
    schema_contents: dict[str, str],
    provider: str,
    contract: str,
    memory: str,
    project_name: str,
    tech_constraints: str = "",
) -> FileResult:
    t0 = time.time()
    effective_contract = f"{contract}\n\n{tech_constraints}" if tech_constraints else contract
    prompt = build_route_prompt(resource, route_file, endpoints, model_contents, schema_contents, effective_contract, memory, project_name)
    data = _call_llm(prompt, provider, max_tokens=10000)
    result = _make_file_result(data, route_file, t0)
    status = "OK" if result.success else f"FAIL:{result.error[:60]}"
    print(f"    [route] {route_file}: {status} ({result.duration:.1f}s)")
    return result


def _gen_main(route_files: list[str], project_name: str, provider: str, contract: str) -> FileResult:
    t0 = time.time()
    prompt = build_main_prompt(route_files, project_name, contract)
    data = _call_llm(prompt, provider, max_tokens=4000)
    result = _make_file_result(data, "app/main.py", t0)
    status = "OK" if result.success else f"FAIL:{result.error[:60]}"
    print(f"    [main] app/main.py: {status} ({result.duration:.1f}s)")
    return result


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_backend_parallel(
    architecture: dict | object,
    provider: str = "auto",
    tech_constraints: str = "",
) -> dict:
    """
    Drop-in replacement for generate_backend(). Returns {"files": [...]} dict
    in the same shape as BackendPlan.model_dump().
    tech_constraints: optional string from Tech Lead agent injected into route prompts.
    """
    t_total = time.time()
    result = ParallelBackendResult()

    # Normalise architecture to dict
    if hasattr(architecture, "model_dump"):
        arch = architecture.model_dump()
    elif hasattr(architecture, "dict"):
        arch = architecture.dict()
    elif isinstance(architecture, dict):
        arch = architecture
    else:
        arch = dict(architecture)

    endpoints = arch.get("api_endpoints", [])
    db_schema = arch.get("database_schema", [])
    project_name = arch.get("project_name", "project")

    # Normalise list items to dicts
    endpoints = [e if isinstance(e, dict) else e.dict() for e in endpoints]
    db_schema = [t if isinstance(t, dict) else t.dict() for t in db_schema]

    contract = FASTAPI_CONTRACT
    memory = build_prompt_injection()
    all_files: list[FileResult] = []

    # ── Wave 1: Foundation (no LLM needed) ───────────────────────────────
    print("\n  [V6] Wave 1 — Foundation")
    all_files.append(FileResult(
        path="app/database.py",
        content=DATABASE_PY_TEMPLATE,
        success=True, duration=0.0,
    ))
    all_files.append(FileResult(
        path="app/requirements.txt",
        content=build_requirements_content(arch),
        success=True, duration=0.0,
    ))
    # __init__ stubs
    for pkg in ("app", "app/routes", "app/models", "app/schemas", "app/services"):
        all_files.append(FileResult(path=f"{pkg}/__init__.py", content="", success=True))

    # ── Wave 2: Models (parallel) ─────────────────────────────────────────
    print(f"  [V6] Wave 2 — Models ({len(db_schema)} tables, parallel)")
    t_wave = time.time()
    if db_schema:
        tasks = [
            (_gen_model, f"app/models/{t['table_name'].lower()}.py", t, db_schema, provider, contract, memory)
            for t in db_schema
        ]
        model_results = _parallel_wave(tasks, max_workers=5)
        all_files.extend(model_results)
    else:
        model_results = []
    print(f"  [V6] Wave 2 done in {time.time()-t_wave:.1f}s")

    # Build model content lookup for later waves
    model_contents: dict[str, str] = {
        r.path: r.content for r in model_results if r.success
    }

    # ── Wave 3: Schemas (parallel) ────────────────────────────────────────
    resource_groups = _group_endpoints_by_resource(endpoints)
    print(f"  [V6] Wave 3 — Schemas ({len(resource_groups)} resources, parallel)")
    t_wave = time.time()

    schema_tasks = []
    for resource, eps in resource_groups.items():
        # Find matching model content
        model_content = (
            model_contents.get(f"app/models/{resource}.py")
            or model_contents.get(f"app/models/{resource[:-1]}.py")  # strip trailing 's'
            or ""
        )
        schema_tasks.append(
            (_gen_schema, f"app/schemas/{resource}.py", resource, model_content, eps, provider, contract, memory)
        )

    schema_results = _parallel_wave(schema_tasks, max_workers=5)
    all_files.extend(schema_results)
    print(f"  [V6] Wave 3 done in {time.time()-t_wave:.1f}s")

    schema_contents: dict[str, str] = {
        r.path: r.content for r in schema_results if r.success
    }

    # ── Wave 4: Routes (parallel, the big win) ────────────────────────────
    print(f"  [V6] Wave 4 — Routes ({len(resource_groups)} files, parallel)")
    t_wave = time.time()

    route_tasks = []
    for resource, eps in resource_groups.items():
        route_file = f"app/routes/{resource}_routes.py"
        route_tasks.append((
            _gen_route, route_file,
            resource, route_file, eps, model_contents, schema_contents, provider, contract, memory, project_name, tech_constraints
        ))

    route_results = _parallel_wave(route_tasks, max_workers=5)
    all_files.extend(route_results)
    print(f"  [V6] Wave 4 done in {time.time()-t_wave:.1f}s")

    result.parallel_duration = round(time.time() - t_wave, 2)

    # ── Wave 5: main.py (sequential) ─────────────────────────────────────
    successful_routes = [r.path for r in route_results if r.success]
    print(f"  [V6] Wave 5 — main.py (from {len(successful_routes)} route files)")
    main_result = _gen_main(successful_routes, project_name, provider, contract)
    all_files.append(main_result)

    # ── Finalise ──────────────────────────────────────────────────────────
    result.files = all_files
    result.failed_files = [r.path for r in all_files if not r.success]
    result.success = len([r for r in all_files if not r.success and r.path.endswith(".py") and "__init__" not in r.path]) == 0
    result.total_duration = round(time.time() - t_total, 2)

    passed = sum(1 for r in all_files if r.success)
    print(f"\n  [V6] Parallel backend done: {passed}/{len(all_files)} files OK "
          f"in {result.total_duration}s (parallel phase: {result.parallel_duration}s)")

    # Return in the same shape as generate_backend()
    return {
        "files": [
            {"path": r.path, "content": r.content}
            for r in all_files
            if r.success and r.content is not None
        ]
    }
