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
import json
import os
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
from app.templates.database_template import DATABASE_PY_TEMPLATE, AUTH_UTILS_TEMPLATE
from app.utils.json_cleaner import extract_json
from app.services.entity_metadata import (
    derive_relationship_kinds,
    extract_entity_definition,
    find_model_for_resource,
    render_field_manifest,
)

# Experiments 017/018: model-driven schema generation. PROMOTED TO DEFAULT
# 2026-07-06 after two canaries confirmed the mechanism (Exp017: direct
# file-level proof the Contact.name/first_name drift is eliminated, no
# unrelated regressions found; Exp018 clean confirming run: CANARY PASSED,
# both previously-blocked apps -- blog_cms and crm -- achieved full 11/11
# CRUD passes for the first time all cycle, the one remaining failure
# (todo, unseeded Priority-lookup table) independently confirmed unrelated
# and pre-existing). Set FORGE_MODEL_DRIVEN_SCHEMA=0 to roll back to the
# old naive filename-guess lookup if a regression is ever traced to this.
MODEL_DRIVEN_SCHEMA_GENERATION = os.environ.get("FORGE_MODEL_DRIVEN_SCHEMA", "1") == "1"


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
    """Call LLM and parse JSON response. Caches results by prompt hash — same prompt = free."""
    from app.utils.llm_cache import get_cached, set_cached
    _cache_payload = {"prompt": prompt, "mt": max_tokens}
    _cached = get_cached("backend_file", _cache_payload)
    if _cached is not None:
        print("      [cache hit]")
        return _cached

    for attempt in range(3):
        try:
            text = generate_content(prompt, provider, max_tokens=max_tokens, stage="backend_generation")
            if not text:
                return None
            text = text.replace("```json", "").replace("```", "").strip()
            result = extract_json(text)
            if result and "_error" not in result:
                set_cached("backend_file", _cache_payload, result)
            return result
        except json.JSONDecodeError as e:
            if attempt < 2:
                print(f"      [retry {attempt+1}/2] JSON decode error: {e}")
                continue
            return {"_error": f"JSONDecodeError after 3 attempts: {e}"}
        except Exception as e:
            return {"_error": str(e)}


def _repair_backslash_content(content: str) -> str:
    """Fix LLM-generated Python with stray backslash sequences."""
    import ast as _ast
    # Try 1: strip trailing whitespace from every line (fixes `\ ` at EOL)
    stripped = "\n".join(line.rstrip() for line in content.split("\n"))
    try:
        _ast.parse(stripped)
        return stripped
    except SyntaxError:
        pass
    # Try 2: replace literal \n (backslash+n chars) that appear in code context
    # This handles the mixed-newline case where LLM embedded \\n in JSON
    fixed = stripped.replace("\\n", "\n").replace("\\t", "    ")
    try:
        _ast.parse(fixed)
        return fixed
    except SyntaxError:
        pass
    return content  # return original if repairs failed


def _make_file_result(data: dict | None, expected_path: str, t0: float) -> FileResult:
    import ast as _ast
    elapsed = time.time() - t0
    if not data or "_error" in data:
        err = (data or {}).get("_error", "LLM returned None or invalid JSON")
        return FileResult(path=expected_path, content="", success=False, error=err, duration=elapsed)
    path = data.get("path", expected_path)
    content = data.get("content", "")
    if not content.strip():
        return FileResult(path=path, content="", success=False, error="Empty content", duration=elapsed)
    # Auto-repair backslash syntax errors before the file writer sees it
    if path.endswith(".py"):
        try:
            _ast.parse(content)
        except SyntaxError as e:
            if "continuation" in str(e) or "unexpected character" in str(e):
                content = _repair_backslash_content(content)
    return FileResult(path=path, content=content, success=True, duration=elapsed)


_PYTHON_KEYWORDS = frozenset({
    "class", "type", "import", "from", "for", "while", "if", "else", "elif",
    "return", "def", "lambda", "with", "as", "pass", "break", "continue",
    "try", "except", "finally", "raise", "yield", "async", "await",
    "global", "nonlocal", "del", "assert", "in", "is", "not", "and", "or",
    "True", "False", "None", "list", "dict", "set", "str", "int", "float",
})


def _sanitize_resource(name: str) -> str:
    """Rename resources that are Python keywords to avoid import-time SyntaxErrors."""
    return f"{name}_item" if name in _PYTHON_KEYWORDS else name


def _group_endpoints_by_resource(endpoints: list[dict]) -> dict[str, list[dict]]:
    """Group api_endpoints by the first path segment (resource name)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for ep in endpoints:
        path = ep.get("path", "")
        file_field = ep.get("file", "").replace("\\", "/")  # normalize backslashes
        if file_field:
            resource = (
                file_field
                .replace("app/routes/", "")
                .replace("_routes.py", "")
                .replace(".py", "")
                .strip("/")
            )
        else:
            parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
            resource = parts[0] if parts else "misc"
        groups[_sanitize_resource(resource)].append(ep)
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


def _gen_schema(
    resource: str, model_content: str, endpoints: list[dict], provider: str,
    contract: str, memory: str, field_manifest: str = "",
) -> FileResult:
    t0 = time.time()
    prompt = build_schema_prompt(resource, model_content, endpoints, contract, memory, field_manifest=field_manifest)
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


def _ensure_main_py_quality(content: str) -> str:
    """
    Deterministically inject /health and CORS into main.py if the LLM omitted them.
    Called after LLM generation as a safety net — the prompt already requests both.
    """
    import re as _re

    # ── CORS middleware ────────────────────────────────────────────────────
    if "CORSMiddleware" not in content:
        # Add import on the line after the first `from fastapi` import
        content = _re.sub(
            r'(from fastapi(?:\.[^\n]*)? import [^\n]+\n)',
            r'\1from fastapi.middleware.cors import CORSMiddleware\n',
            content, count=1,
        )
        # Inject middleware call after `app = FastAPI(...)` on a single line
        content = _re.sub(
            r'(app\s*=\s*FastAPI\([^\n]*\))',
            (
                r'\1\n\n'
                r'app.add_middleware(\n'
                r'    CORSMiddleware,\n'
                r'    allow_origins=["*"],\n'
                r'    allow_credentials=True,\n'
                r'    allow_methods=["*"],\n'
                r'    allow_headers=["*"],\n'
                r')'
            ),
            content, count=1,
        )
        print("    [main] injected CORS middleware (was missing)")

    # ── /health endpoint ───────────────────────────────────────────────────
    if '"/health"' not in content and "'/health'" not in content:
        content = content.rstrip('\n') + (
            '\n\n\n@app.get("/health")\n'
            'def health():\n'
            '    return {"status": "ok"}\n'
        )
        print("    [main] injected /health endpoint (was missing)")

    return content


def _gen_main(route_files: list[str], project_name: str, provider: str, contract: str, model_files: list[str] | None = None) -> FileResult:
    t0 = time.time()
    prompt = build_main_prompt(route_files, project_name, contract, model_files=model_files or [])
    data = _call_llm(prompt, provider, max_tokens=4000)
    result = _make_file_result(data, "app/main.py", t0)
    if result.success and result.content:
        result.content = _ensure_main_py_quality(result.content)
    status = "OK" if result.success else f"FAIL:{result.error[:60]}"
    print(f"    [main] app/main.py: {status} ({result.duration:.1f}s)")
    return result


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_backend_parallel(
    architecture: dict | object,
    provider: str = "auto",
    tech_constraints: str = "",
) -> ParallelBackendResult:
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
        path="app/utils/auth.py",
        content=AUTH_UTILS_TEMPLATE,
        success=True, duration=0.0,
    ))
    all_files.append(FileResult(
        path="app/requirements.txt",
        content=build_requirements_content(arch),
        success=True, duration=0.0,
    ))
    # __init__ stubs
    for pkg in ("app", "app/routes", "app/models", "app/schemas", "app/services", "app/utils"):
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

    # ── Wave 2.5: Normalize model class names to singular ────────────────
    # Problem: LLM often generates plural class names (Users, Authors, Companies)
    # while code uses singular names in relationship("User"), imports, etc.
    # Fix: rename plural class names to singular IN-PLACE in model files, then
    # update relationship() strings across all models. Also create re-export shims
    # (singular_name.py) when only the plural file exists.
    import re as _re

    def _singularize(name: str) -> str:
        """Convert plural Python class name to singular (best-effort)."""
        if name.endswith("ies"):
            return name[:-3] + "y"       # Companies → Company, Categories → Category
        if name.endswith("ss") or name.endswith("us") or name.endswith("is"):
            return name                   # Status, Campus, Basis → unchanged
        if name.endswith("s") and len(name) > 2:
            return name[:-1]             # Users → User, Authors → Author
        return name

    # Pre-step: resolve module-level aliases in model files.
    # LLM sometimes generates: class BookModel(Base): ... \n Book = BookModel
    # SQLAlchemy only registers the actual class name ("BookModel"), so relationship("Book")
    # would fail. Fix: rename the class to the alias name and remove the alias line.
    for fr in all_files:
        if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
            continue
        if not fr.success or not fr.content or "__init__" in fr.path:
            continue
        modified = fr.content
        for alias_name, real_name in _re.findall(r'^([A-Z]\w*)\s*=\s*([A-Z]\w*)\s*$', modified, _re.MULTILINE):
            if alias_name == real_name:
                continue
            # Only resolve if real_name is a Base class defined in this file
            if _re.search(rf'^class {real_name}\(Base\)', modified, _re.MULTILINE):
                modified = modified.replace(f'class {real_name}(Base)', f'class {alias_name}(Base)')
                # Also update any other references to real_name within the file (e.g. __repr__)
                modified = _re.sub(rf'\b{real_name}\b', alias_name, modified)
                # Remove the alias line itself (now redundant)
                modified = _re.sub(rf'^{alias_name}\s*=\s*{alias_name}\s*\n?', '', modified, flags=_re.MULTILINE)
                print(f"  [V6] Wave 2.5 — resolved alias {real_name} → {alias_name} in {fr.path}")
        if modified != fr.content:
            fr.content = modified
            model_contents[fr.path] = modified

    # Strip back_populates= and backref= from all relationship() calls in model files.
    # These cause InvalidRequestError ("has no property X") when both sides of a bidirectional
    # relationship don't have the matching attribute, and ArgumentError ("backref X already exists")
    # when a backref name collides with an existing property. Relationships still work without them.
    for fr in all_files:
        if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
            continue
        if not fr.success or not fr.content or "__init__" in fr.path:
            continue
        if "back_populates=" not in fr.content and "backref=" not in fr.content:
            continue
        modified = fr.content
        # Strip back_populates=VALUE, (trailing comma consumed) and back_populates=VALUE (no trailing comma)
        modified = _re.sub(r'\bback_populates\s*=\s*["\'][^"\']*["\'],?\s*', '', modified)
        modified = _re.sub(r'\bbackref\s*=\s*["\'][^"\']*["\'],?\s*', '', modified)
        # Fix orphan leading comma before closing paren: , ) → )
        modified = _re.sub(r',(\s*\))', r'\1', modified)
        if modified != fr.content:
            fr.content = modified
            model_contents[fr.path] = modified
            print(f"  [V6] Wave 2.5 — stripped back_populates/backref from {fr.path}")

    model_paths = {r.path for r in model_results if r.success}
    # Build rename map: {plural_class: singular_class} for each model
    rename_map: dict[str, str] = {}  # {"Users": "User", "Authors": "Author", ...}
    for fr in list(all_files):
        if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
            continue
        if not fr.success or not fr.content or "__init__" in fr.path:
            continue
        for cls_match in _re.finditer(r"^class (\w+)\(Base\)", fr.content, _re.MULTILINE):
            cls_name = cls_match.group(1)
            singular = _singularize(cls_name)
            if singular != cls_name:
                rename_map[cls_name] = singular

    if rename_map:
        print(f"  [V6] Wave 2.5 — renaming plural class names: {rename_map}")
        # Step 1: Rename class definitions in-place
        for fr in all_files:
            if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
                continue
            if not fr.success or not fr.content:
                continue
            modified = fr.content
            for plural, singular in rename_map.items():
                modified = modified.replace(f"class {plural}(Base)", f"class {singular}(Base)")
            if modified != fr.content:
                fr.content = modified
                model_contents[fr.path] = modified

        # Step 2: Update relationship() strings in all model files
        for fr in all_files:
            if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
                continue
            if not fr.success or not fr.content:
                continue
            modified = fr.content
            for plural, singular in rename_map.items():
                # relationship("Users") → relationship("User")
                modified = _re.sub(
                    rf'relationship\(\s*["\']({plural})["\']\s*',
                    lambda m, s=singular: f'relationship("{s}"',
                    modified,
                )
                # back_populates / secondary that reference the class
                modified = modified.replace(f'"{plural}"', f'"{singular}"')
            if modified != fr.content:
                fr.content = modified
                model_contents[fr.path] = modified

    # Create singular re-export shims for routes that import `from app.models.user import User`
    # when only `app/models/users.py` exists (regardless of rename above).
    for fr in list(all_files):
        if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
            continue
        if not fr.success or "__init__" in fr.path:
            continue
        # e.g. app/models/users.py → singular would be app/models/user.py
        module_name = fr.path.replace("app/models/", "").replace(".py", "")
        singular_module = _singularize(module_name)
        if singular_module == module_name:
            continue  # already singular
        singular_path = f"app/models/{singular_module}.py"
        if singular_path in model_paths:
            continue  # singular file already exists
        # Find the (now possibly renamed) class name in the plural file
        cls_match = _re.search(r"^class (\w+)\(Base\)", fr.content, _re.MULTILINE)
        exported_cls = cls_match.group(1) if cls_match else module_name.capitalize()
        shim = (
            f"from app.models.{module_name} import {exported_cls}\n"
            f"__all__ = ['{exported_cls}']\n"
        )
        all_files.append(FileResult(path=singular_path, content=shim, success=True, duration=0.0))
        model_contents[singular_path] = shim
        print(f"  [V6] Wave 2.5 — created shim {singular_path} → {exported_cls}")

    # ForeignKey normalization: fix FK table name mismatches (singular ↔ plural)
    # Build map: table_name → model file path
    known_tables: set[str] = set()
    for fr in all_files:
        if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
            continue
        if not fr.success or not fr.content:
            continue
        tn = _re.search(r'__tablename__\s*=\s*["\'](\w+)["\']', fr.content)
        if tn:
            known_tables.add(tn.group(1))

    if known_tables:
        for fr in all_files:
            if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
                continue
            if not fr.success or not fr.content:
                continue
            modified = fr.content
            for fk_match in _re.finditer(r'ForeignKey\(["\'](\w+)\.(\w+)["\']\)', fr.content):
                ref_table = fk_match.group(1)
                ref_col = fk_match.group(2)
                if ref_table not in known_tables:
                    # Try both plural and singular forms
                    candidates = [
                        ref_table + "s",         # user → users (try plural)
                        _singularize(ref_table), # users → user (try singular)
                        # ies form: category → categories
                        ref_table[:-1] + "ies" if ref_table.endswith("y") else None,
                    ]
                    for candidate in candidates:
                        if candidate and candidate != ref_table and candidate in known_tables:
                            for q in ('"', "'"):
                                modified = modified.replace(
                                    f'ForeignKey({q}{ref_table}.{ref_col}{q})',
                                    f'ForeignKey({q}{candidate}.{ref_col}{q})',
                                )
                            print(f"  [V6] Wave 2.5 — fixed FK {ref_table}.{ref_col} → {candidate}.{ref_col} in {fr.path}")
                            break
            if modified != fr.content:
                fr.content = modified
                model_contents[fr.path] = modified

    # Bidirectional module shims: ensure both singular (user.py) and plural (users.py)
    # module names resolve to the same class. This prevents main.py from crashing when
    # the LLM generates `from app.models.users import *` but only `user.py` was generated.
    current_paths = {fr.path for fr in all_files if fr.success}
    for fr in list(all_files):
        if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
            continue
        if not fr.success or not fr.content or "__init__" in fr.path:
            continue
        module_name = fr.path.replace("app/models/", "").replace(".py", "")
        cls_match = _re.search(r"^class (\w+)\(Base\)", fr.content, _re.MULTILINE)
        if not cls_match:
            continue
        cls_name = cls_match.group(1)
        # For singular module (user.py): create plural shim (users.py)
        if _singularize(module_name) == module_name:  # already singular
            plural_module = module_name + "s"
            plural_path = f"app/models/{plural_module}.py"
            if plural_path not in current_paths:
                shim = f"from app.models.{module_name} import {cls_name}\n__all__ = ['{cls_name}']\n"
                all_files.append(FileResult(path=plural_path, content=shim, success=True, duration=0.0))
                model_contents[plural_path] = shim
                current_paths.add(plural_path)
                print(f"  [V6] Wave 2.5 — created plural shim {plural_path} → {cls_name}")

    # Strip relationship() declarations that have no FK backing (direct or secondary).
    # These cause NoForeignKeysError at startup when SQLAlchemy can't find the join condition.
    # Safe to strip because routes can still query via explicit db.query().filter() instead.
    _class_to_table: dict[str, str] = {}
    _fk_edges: set[tuple[str, str]] = set()
    for fr in all_files:
        if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
            continue
        if not fr.success or not fr.content or "__init__" in fr.path:
            continue
        tn = _re.search(r'__tablename__\s*=\s*["\'](\w+)["\']', fr.content)
        if not tn:
            continue
        tbl = tn.group(1)
        cls_m = _re.search(r'^class (\w+)\(Base\)', fr.content, _re.MULTILINE)
        if cls_m:
            _class_to_table[cls_m.group(1)] = tbl
        for fk_target in _re.findall(r'ForeignKey\(["\'](\w+)\.', fr.content):
            _fk_edges.add((tbl, fk_target))
            _fk_edges.add((fk_target, tbl))

    for fr in all_files:
        if not fr.path.startswith("app/models/") or not fr.path.endswith(".py"):
            continue
        if not fr.success or not fr.content or "__init__" in fr.path:
            continue
        tn = _re.search(r'__tablename__\s*=\s*["\'](\w+)["\']', fr.content)
        if not tn or "relationship(" not in fr.content:
            continue
        this_table = tn.group(1)
        lines = fr.content.split('\n')
        kept_lines: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            rel_m = _re.match(r'^\s*\w+\s*=\s*relationship\(["\'](\w+)["\']', line)
            if rel_m:
                target_class = rel_m.group(1)
                target_table = _class_to_table.get(target_class, target_class.lower() + "s")
                # Collect this relationship's full text (until balanced parens)
                depth = line.count('(') - line.count(')')
                end = i + 1
                while depth > 0 and end < len(lines):
                    depth += lines[end].count('(') - lines[end].count(')')
                    end += 1
                rel_block = '\n'.join(lines[i:end])
                has_secondary = 'secondary=' in rel_block
                has_fk = (
                    (this_table, target_table) in _fk_edges or
                    (target_table, this_table) in _fk_edges
                )
                if not has_fk and not has_secondary:
                    print(f"  [V6] Wave 2.5 — stripped no-FK relationship {this_table} → {target_table}")
                    i = end
                    continue
            kept_lines.append(line)
            i += 1
        modified = '\n'.join(kept_lines)
        if modified != fr.content:
            fr.content = modified
            model_contents[fr.path] = modified

    # ── Wave 3: Schemas (parallel) ────────────────────────────────────────
    resource_groups = _group_endpoints_by_resource(endpoints)
    print(f"  [V6] Wave 3 — Schemas ({len(resource_groups)} resources, parallel)")
    t_wave = time.time()

    # ADR-001 extension, Phase D integration: derive_relationship_kinds()
    # needs the FULL set of parsed entities at once (it cross-matches
    # back_populates pairs across two files) -- parse every model once,
    # derive kinds once, then look up the already-derived entity by
    # source_path below instead of re-parsing per resource.
    _kind_derived_by_source: dict[str, object] = {}
    if MODEL_DRIVEN_SCHEMA_GENERATION:
        _all_parsed = [
            e for e in (
                extract_entity_definition(c, source_path=p) for p, c in model_contents.items()
            ) if e is not None
        ]
        derive_relationship_kinds(_all_parsed)
        _kind_derived_by_source = {e.source_path: e for e in _all_parsed}

    schema_tasks = []
    for resource, eps in resource_groups.items():
        # Find matching model content
        model_content = (
            model_contents.get(f"app/models/{resource}.py")
            or model_contents.get(f"app/models/{resource[:-1]}.py")  # strip trailing 's'
            or ""
        )
        field_manifest = ""
        if MODEL_DRIVEN_SCHEMA_GENERATION:
            entity = find_model_for_resource(model_contents, resource)
            if entity is not None:
                # Prefer the kind-derived copy of this same entity (matched
                # by source_path) so the manifest includes accurate
                # relationship guidance; fall back to the freshly-parsed
                # one (kind=None on every relationship) if lookup ever
                # misses -- never worse than before this integration.
                entity = _kind_derived_by_source.get(entity.source_path, entity)
                field_manifest = render_field_manifest(entity)
                # The resolved real model may differ from the naive filename
                # guess above (e.g. that guess found a re-export shim with no
                # column data) -- prefer the real, parsed model's own source
                # content so the raw reference block in the prompt agrees
                # with the field manifest instead of contradicting it.
                model_content = model_contents.get(entity.source_path, model_content)
        schema_tasks.append(
            (_gen_schema, f"app/schemas/{resource}.py", resource, model_content, eps, provider, contract, memory, field_manifest)
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
    successful_models = [r.path for r in model_results if r.success]
    print(f"  [V6] Wave 5 — main.py (from {len(successful_routes)} route files, {len(successful_models)} models)")
    main_result = _gen_main(successful_routes, project_name, provider, contract, model_files=successful_models)
    all_files.append(main_result)

    # ── Finalise ──────────────────────────────────────────────────────────
    result.files = all_files
    result.failed_files = [r.path for r in all_files if not r.success]
    result.success = len([r for r in all_files if not r.success and r.path.endswith(".py") and "__init__" not in r.path]) == 0
    result.total_duration = round(time.time() - t_total, 2)

    passed = sum(1 for r in all_files if r.success)
    print(f"\n  [V6] Parallel backend done: {passed}/{len(all_files)} files OK "
          f"in {result.total_duration}s (parallel phase: {result.parallel_duration}s)")

    return result
