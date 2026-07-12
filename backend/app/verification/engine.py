"""
VerificationEngine — 10-stage verification pipeline with parallel post-runtime stages.

Execution order:
  [Sequential]
    1.  Static          — syntax, imports, ORM, router names, contract rules
    2.  Compile         — py_compile every .py file for fast syntax check
    2a. Import closure  — every import resolves to a file, a requirements.txt
                          dependency, or the stdlib -- statically, pre-boot
    2a-sym. Symbol closure — every `from local.module import X` name is
                          actually defined in that module, not just the
                          module itself -- statically, pre-boot
    2a2. Contract check — AppContract conformance, warn-only (LOW severity
                          only; never gates anything -- see app/contract/)
    2b. Frontend build  — vite build (see below)
    3.  Runtime         — install deps, start uvicorn, health check

  [Parallel — after backend is up]
    4.  HTTP            — direct HTTP calls to every endpoint
    4b. Schema/DB       — live OpenAPI response schema vs. actual JSON keys,
                          exact-diffed per endpoint (Pydantic/model mismatches)
    5.  Browser         — Playwright: page load, blank-page check
    6.  Console Logs    — browser console errors
    7.  Screenshots     — capture full-page PNG
    8.  Performance     — /health latency, response time distribution
    9.  Accessibility   — basic a11y: alt text, labels, contrast hints

  [Sequential — after all parallel stages]
   10.  LLM Judge       — vision LLM interprets screenshots + all failures:
                          "Submit button hidden behind modal" not just "element not found"

  [Analysis]
   11.  Failure Graph   — causal analysis: suppress downstream symptoms, surface roots
"""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from app.core.context import (
    BrowserTestResult, Diagnostic, ErrorCategory, ErrorSeverity,
    GenerationContext, RuntimeResult, StageStatus,
    VerificationResult,
)


# ═══════════════════════════════════════════════════════════════
#   STAGE 1 — Static validators
# ═══════════════════════════════════════════════════════════════

def _run_static_validators(ctx: GenerationContext) -> VerificationResult:
    t0 = time.time()
    try:
        from app.services.validator_service import validate_project
        validation = validate_project(str(ctx.project_path))
    except Exception as exc:
        return VerificationResult(
            stage="static", status=StageStatus.FAILED,
            diagnostics=[_diag("static", f"Validator service crashed: {exc}", ErrorSeverity.CRITICAL, ErrorCategory.RUNTIME)],
            duration_ms=_ms(t0),
        )

    # Exp060: validate_project() now optionally returns a "diagnostics" list
    # of native Diagnostic objects, one per migrated validator's error,
    # keyed here by exact message match (each migrated validator builds its
    # message once into a local var and uses that SAME string for both the
    # legacy `errors` entry and the Diagnostic's `.message`, so this lookup
    # is exact, not fuzzy). Where a native Diagnostic exists it's used
    # as-is -- validator-supplied file_path/category/severity, no regex
    # guessing needed. Any error NOT yet in `diagnostics` (validator not
    # migrated this cycle) falls through to the exact same regex-based
    # construction as before -- byte-identical behavior for those, per
    # docs/VALIDATOR_MIGRATION.md's "no flag day rewrite" design.
    _diag_by_message = {d.message: d for d in validation.get("diagnostics", [])}

    diagnostics = [
        _diag_by_message[err] if err in _diag_by_message else Diagnostic(
            error_id=Diagnostic.make_id("static", _categorise_static(err), err),
            category=_categorise_static(err),
            severity=_severity_static(err),
            source="static",
            message=err,
            file_path=_filepath_static(err),
            fix_hint=_hint_static(err),
            metadata=(
                {"extra_file_paths": efp}
                if (efp := _extra_filepaths_static(err)) else {}
            ),
        )
        for err in validation.get("errors", [])
    ] + [
        _diag("static", w, ErrorSeverity.LOW, ErrorCategory.CONTRACT)
        for w in validation.get("warnings", [])
    ]

    status = StageStatus.PASSED if not validation.get("errors") else StageStatus.FAILED
    return VerificationResult(
        stage="static", status=status, diagnostics=diagnostics,
        duration_ms=_ms(t0),
        metadata={"error_count": len(validation.get("errors", [])), "warning_count": len(validation.get("warnings", []))},
    )


# ═══════════════════════════════════════════════════════════════
#   STAGE 2 — Compile (py_compile, fast)
# ═══════════════════════════════════════════════════════════════

def _run_compile_check(ctx: GenerationContext) -> VerificationResult:
    """py_compile every .py file — fast catch for LLM-generated syntax errors."""
    import py_compile
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    py_files = list((ctx.project_path / "app").rglob("*.py")) if ctx.project_path else []
    for f in py_files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as exc:
            diagnostics.append(Diagnostic(
                error_id=Diagnostic.make_id("compile", ErrorCategory.SYNTAX, str(exc), str(f)),
                category=ErrorCategory.SYNTAX,
                severity=ErrorSeverity.CRITICAL,
                source="compile",
                message=str(exc),
                file_path=str(f),
                fix_hint="Fix Python syntax error — check for missing colons, unmatched parens, or indentation",
            ))
        except OSError:
            # Transient Windows filesystem lock (AV/indexer briefly holding the
            # freshly-written .pyc during rename) — retry once, then move on
            # rather than crashing the whole pipeline run over a non-code issue.
            try:
                time.sleep(0.2)
                py_compile.compile(str(f), doraise=True)
            except py_compile.PyCompileError as exc:
                diagnostics.append(Diagnostic(
                    error_id=Diagnostic.make_id("compile", ErrorCategory.SYNTAX, str(exc), str(f)),
                    category=ErrorCategory.SYNTAX,
                    severity=ErrorSeverity.CRITICAL,
                    source="compile",
                    message=str(exc),
                    file_path=str(f),
                    fix_hint="Fix Python syntax error — check for missing colons, unmatched parens, or indentation",
                ))
            except OSError:
                pass
    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="compile", status=status, diagnostics=diagnostics, duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 2a — Import closure (every import must resolve, statically)
# ═══════════════════════════════════════════════════════════════

def _canon_pkg(name: str) -> str:
    return name.strip().lower().replace("-", "_")


# PyPI distribution name -> the name(s) actually used in `import X` /
# `from X import ...`, for the packages ForgeAI's generated FastAPI/
# SQLAlchemy/React stack commonly declares. Comparison is otherwise exact
# (after normalising `-`/`_` and stripping extras/version specifiers), so
# this list only needs the cases where the two names genuinely differ.
# Keys are written in their normal PyPI form and canonicalised through
# _canon_pkg() below -- calibrated against 49 real ForgeAI-generated
# projects, where a raw (un-canonicalised) key lookup silently missed
# every `python-jose` -> `jose` match and produced 11 false positives.
_PACKAGE_IMPORT_ALIASES_RAW: dict[str, set[str]] = {
    "pyjwt":               {"jwt"},
    "python-jose":         {"jose"},
    "python-dotenv":       {"dotenv"},
    "python-multipart":    {"multipart"},
    "email-validator":     {"email_validator"},
    "python-dateutil":     {"dateutil"},
    "python-magic":        {"magic"},
    "pyyaml":              {"yaml"},
    "pillow":              {"PIL"},
    "beautifulsoup4":      {"bs4"},
    "scikit-learn":        {"sklearn"},
    "opencv-python":       {"cv2"},
    "psycopg2-binary":     {"psycopg2"},
    "google-cloud-storage": {"google"},
    "protobuf":            {"google"},
}
_PACKAGE_IMPORT_ALIASES: dict[str, set[str]] = {
    _canon_pkg(k): v for k, v in _PACKAGE_IMPORT_ALIASES_RAW.items()
}

# Hard (non-optional) transitive dependencies that generated code commonly
# imports directly even though only the parent package is declared in
# requirements.txt -- e.g. every FastAPI version pulls in Pydantic and
# Starlette unconditionally, so `import pydantic` never fails at runtime
# even when a project's requirements.txt lists only "fastapi". Calibrated
# against real projects (subscription_tracker, travel_planner_app) that
# declare fastapi but not pydantic and still work correctly in production.
_TRANSITIVE_DEPS: dict[str, set[str]] = {
    "fastapi": {"pydantic", "starlette"},
}


def _declared_python_deps(project_path: Path) -> set[str]:
    """
    Import-name vocabulary declared as available: every package listed in
    requirements.txt, expanded through _PACKAGE_IMPORT_ALIASES, plus the
    standard library. This is the "declared deps" half of the import
    closure -- the other half is the project's own file map (see
    _local_python_modules).
    """
    import sys
    names: set[str] = set(getattr(sys, "stdlib_module_names", ()))
    req_path = project_path / "app" / "requirements.txt"
    if not req_path.exists():
        req_path = project_path / "requirements.txt"
    if not req_path.exists():
        return names
    try:
        lines = req_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return names
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        # Strip extras (`uvicorn[standard]`), version specifiers, and
        # environment markers (`; python_version >= "3.8"`).
        pkg = re.split(r"[\[;<>=!~ ]", line, 1)[0].strip()
        if not pkg:
            continue
        canon = _canon_pkg(pkg)
        names.add(canon)
        names.update(_canon_pkg(a) for a in _PACKAGE_IMPORT_ALIASES.get(canon, ()))
        names.update(_canon_pkg(a) for a in _TRANSITIVE_DEPS.get(canon, ()))
    return names


def _local_python_modules(project_path: Path, py_files: list[Path]) -> set[str]:
    """Every dotted module path resolvable from the project's own file map,
    including every package-prefix of each file (so `app.routes` resolves
    just as `app.routes.user_routes` does)."""
    modules: set[str] = set()
    for f in py_files:
        try:
            rel = f.relative_to(project_path).with_suffix("")
        except ValueError:
            continue
        parts = rel.parts
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(1, len(parts) + 1):
            modules.add(".".join(parts[:i]))
    return modules


def _run_import_closure_check(ctx: GenerationContext) -> VerificationResult:
    """
    Static check: every absolute `import X` / `from X import Y` in every
    generated .py file must resolve to a module in the project's own file
    map, a package declared in requirements.txt, or the standard library.

    This is a direct, statically-detectable prevention for ImportError and
    ModuleNotFoundError -- the #2 and #4 most frequent failure patterns in
    failure_memory/patterns.json (20 combined instances) -- catching them
    in under a second instead of ~30-60s later as a runtime boot crash.
    Relative imports (`from . import x`) are skipped: they resolve within
    the package they're written in by construction and add resolution
    complexity without adding real detection power.
    """
    import ast
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    try:
        app_dir = ctx.project_path / "app"
        if not app_dir.exists():
            return VerificationResult(stage="import_closure", status=StageStatus.SKIPPED,
                                      metadata={"reason": "no app/ directory"})

        py_files = list(app_dir.rglob("*.py"))
        main_py = ctx.project_path / "main.py"
        if main_py.exists():
            py_files.append(main_py)

        local_modules = _local_python_modules(ctx.project_path, py_files)
        declared_deps = _declared_python_deps(ctx.project_path)

        for f in py_files:
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue  # syntax errors are already caught by the compile stage

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level and node.level > 0:
                        continue  # relative import -- resolves within its own package
                    module = node.module
                elif isinstance(node, ast.Import):
                    module = node.names[0].name if node.names else None
                else:
                    continue
                if not module:
                    continue

                top = module.split(".")[0]
                if (module in local_modules or top in local_modules
                        or _canon_pkg(top) in declared_deps):
                    continue

                rel_path = str(f.relative_to(ctx.project_path))
                diagnostics.append(Diagnostic(
                    error_id=Diagnostic.make_id(
                        "import_closure", ErrorCategory.RUNTIME,
                        f"unresolved import '{module}'", rel_path),
                    category=ErrorCategory.RUNTIME,
                    severity=ErrorSeverity.CRITICAL,
                    source="import_closure",
                    message=(f"Unresolved import '{module}' in {rel_path} -- matches "
                             f"no file in the project, no dependency in requirements.txt, "
                             f"and no standard-library module. This will crash the server "
                             f"with ImportError/ModuleNotFoundError at startup."),
                    file_path=rel_path,
                    fix_hint=(f"Either create the missing module '{module}', fix the import "
                              f"path to an existing file, or add its package to requirements.txt."),
                ))
    except Exception as exc:
        return VerificationResult(
            stage="import_closure", status=StageStatus.FAILED,
            diagnostics=[_diag("import_closure", f"Import closure checker crashed: {exc}",
                               ErrorSeverity.MEDIUM, ErrorCategory.RUNTIME)],
            duration_ms=_ms(t0),
        )

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="import_closure", status=status, diagnostics=diagnostics, duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 2a-symbols — Symbol closure (every imported NAME must exist,
#   not just the module it's imported from)
# ═══════════════════════════════════════════════════════════════

def _module_to_file(module: str, project_path: Path) -> Optional[Path]:
    """Resolve a local dotted module path (e.g. 'app.schemas.contact') to
    the .py file it names, trying the plain-module form, the regular-package
    form (__init__.py), and the namespace-package form (PEP 420: a plain
    directory with no __init__.py is still importable in Python 3.3+ --
    confirmed live: todoapp's app/schemas/ has no __init__.py but IS a real
    importable package). Returns None if none of those exist (Stage 2a
    already flags that case for the module-resolution half of this check)."""
    rel = Path(*module.split("."))
    candidate = project_path / rel.with_suffix(".py")
    if candidate.exists():
        return candidate
    candidate = project_path / rel / "__init__.py"
    if candidate.exists():
        return candidate
    dir_candidate = project_path / rel
    if dir_candidate.is_dir():
        return dir_candidate  # namespace package -- caller treats as unparseable, not missing
    return None


def _module_defined_names(file_path: Path) -> Optional[set[str]]:
    """
    Names importable via `from <module> import X` for the module at
    file_path: top-level class/function/variable definitions, plus any
    name the module itself imports at top level (those become attributes
    of its namespace too -- a common re-export pattern). Returns None if
    the module uses `from x import *` -- which names that actually exports
    is undecidable statically, so the caller must skip symbol-checking
    anything sourced through this module rather than risk a false positive.
    """
    import ast
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None

    names: set[str] = set()
    for node in tree.body:  # top-level only -- names nested in a function/if aren't module attributes
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _run_symbol_closure_check(ctx: GenerationContext) -> VerificationResult:
    """
    Static check: for every `from <local module> import (A, B, ...)` where
    <local module> resolves to a project file, verify each imported NAME is
    actually defined in that file's namespace.

    Stage 2a (import closure) only checks that the MODULE resolves to a
    file -- a route file that imports a schema CLASS the schema file never
    defines (wrong name, a rename that missed one call site, a nested
    sub-resource schema like NoteCreate vs the actual ContactNoteCreate)
    passes Stage 2a silently and crashes at boot with a real ImportError,
    indistinguishable from any other startup failure until someone reads
    the traceback. Confirmed live (2026-07-11): a generated CRM app's
    contact_routes.py imported NoteCreate/InteractionCreate from
    app/schemas/contact.py, which only ever defined the prefixed
    ContactNoteCreate/ContactInteractionCreate -- the app could not boot at
    all, yet nothing upstream of the runtime crash flagged it. This is
    exactly the class of bug the ImportError/ModuleNotFoundError family
    already accounts for a large share of recorded failures for; this
    catches it statically, in under a second, naming the exact missing
    symbol instead of a runtime traceback.

    Conservative by construction, matching Stage 2a's own philosophy:
    skips relative imports, skips any module using `from x import *`
    (undecidable which names it exports), only checks modules that resolve
    to an actual project file, and treats `from package import submodule`
    as valid whenever that submodule file exists on disk -- regardless of
    whether __init__.py explicitly names it, since Python's import system
    resolves that directly (confirmed live: todoapp's empty app/__init__.py
    with real app/schemas.py, app/models/ would otherwise false-positive).
    """
    import ast
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    try:
        app_dir = ctx.project_path / "app"
        if not app_dir.exists():
            return VerificationResult(stage="symbol_closure", status=StageStatus.SKIPPED,
                                      metadata={"reason": "no app/ directory"})

        py_files = list(app_dir.rglob("*.py"))
        main_py = ctx.project_path / "main.py"
        if main_py.exists():
            py_files.append(main_py)

        defined_names_cache: dict[Path, Optional[set[str]]] = {}

        for f in py_files:
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue  # syntax errors are already caught by the compile stage

            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                    continue  # relative imports resolve within their own package -- skip

                target = _module_to_file(node.module, ctx.project_path)
                if target is None:
                    continue  # unresolved module path -- Stage 2a already flags this

                if target not in defined_names_cache:
                    defined_names_cache[target] = _module_defined_names(target)
                defined = defined_names_cache[target]
                if defined is None:
                    continue  # star-import in the target -- can't verify statically

                rel_path = str(f.relative_to(ctx.project_path))
                rel_target = str(target.relative_to(ctx.project_path))
                for alias in node.names:
                    if alias.name == "*" or alias.name in defined:
                        continue
                    # `from package import submodule` is valid Python whenever
                    # package/submodule.py (or package/submodule/__init__.py)
                    # exists on disk, regardless of whether __init__.py
                    # explicitly names it -- Python's import system resolves
                    # it directly. Confirmed live: todoapp's `from app import
                    # schemas, models` (both real files, empty __init__.py)
                    # would otherwise false-positive here.
                    if _module_to_file(f"{node.module}.{alias.name}", ctx.project_path) is not None:
                        continue
                    diagnostics.append(Diagnostic(
                        error_id=Diagnostic.make_id(
                            "symbol_closure", ErrorCategory.RUNTIME,
                            f"'{alias.name}' not defined in {node.module}", rel_path),
                        category=ErrorCategory.RUNTIME,
                        severity=ErrorSeverity.CRITICAL,
                        source="symbol_closure",
                        message=(f"{rel_path} imports '{alias.name}' from '{node.module}', but "
                                 f"{rel_target} never defines it. This will crash the server with "
                                 f"ImportError at startup."),
                        file_path=rel_path,
                        fix_hint=(f"Define '{alias.name}' in {rel_target}, fix the import to the "
                                  f"name that actually exists there, or remove the unused import."),
                    ))
    except Exception as exc:
        return VerificationResult(
            stage="symbol_closure", status=StageStatus.FAILED,
            diagnostics=[_diag("symbol_closure", f"Symbol closure checker crashed: {exc}",
                               ErrorSeverity.MEDIUM, ErrorCategory.RUNTIME)],
            duration_ms=_ms(t0),
        )

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="symbol_closure", status=status, diagnostics=diagnostics, duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 2a2 — Contract conformance (warn-only, see app/contract/)
# ═══════════════════════════════════════════════════════════════

def _run_contract_conformance_check(ctx: GenerationContext) -> VerificationResult:
    """
    Derives an AppContract from ctx.architecture (via the adapter-first
    migration path in docs/FORGEAI_VNEXT_REPORT.md #1/#2) and checks the
    generated project against it. Every finding is LOW severity /
    ErrorCategory.CONTRACT -- deliberately warn-only per the report's own
    risk mitigation (§14): observe real hit-rate across benchmark runs
    before this is ever allowed to gate anything. LOW severity cannot
    trigger the critical-static runtime-skip gate or the
    is_deployment_ready critical-stage hard gate.
    """
    t0 = time.time()
    if not ctx.architecture or not ctx.project_path:
        return VerificationResult(stage="contract_conformance", status=StageStatus.SKIPPED,
                                  metadata={"reason": "no architecture or project_path"}, duration_ms=_ms(t0))
    try:
        from app.contract.adapter import enrich_relationships_from_models, from_architecture_plan
        from app.contract.validator import check_contract_conformance
        from app.models.architecture_models import ArchitecturePlan

        arch_dict = _arch_dict(ctx)
        if not arch_dict:
            return VerificationResult(stage="contract_conformance", status=StageStatus.SKIPPED,
                                      metadata={"reason": "architecture not dict-convertible"}, duration_ms=_ms(t0))
        contract = from_architecture_plan(
            idea=ctx.idea, project_name=ctx.project_name,
            architecture=ArchitecturePlan(**arch_dict),
        )
        # ADR-001 extension, Phase B: feed real relationship data (parsed
        # from the already-generated models on disk) into the contract,
        # activating _check_relationship_targets_exist() -- previously
        # permanently inert since the architect-plan-only adapter never
        # populated ContractEntity.relationships at all.
        enrich_relationships_from_models(contract, ctx.project_path)
        diagnostics = check_contract_conformance(contract, ctx.project_path)
    except Exception as exc:
        return VerificationResult(
            stage="contract_conformance", status=StageStatus.FAILED,
            diagnostics=[_diag("contract_conformance", f"Contract conformance checker crashed: {exc}",
                               ErrorSeverity.LOW, ErrorCategory.CONTRACT)],
            duration_ms=_ms(t0),
        )

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="contract_conformance", status=status, diagnostics=diagnostics, duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 2b — Frontend build (produces dist/ for Playwright)
# ═══════════════════════════════════════════════════════════════

def _run_frontend_build(ctx: GenerationContext) -> VerificationResult:
    """
    Run `npm run build` so dist/ exists for the Playwright browser stage.
    Build errors become diagnostics for the main fix loop (patch_file /
    regenerate_module) rather than a separate frontend-specific LLM repair path.
    """
    t0 = time.time()
    try:
        from app.runtime.frontend_runner import FrontendRunner
        result = FrontendRunner().run(str(ctx.project_path))
    except Exception as exc:
        return VerificationResult(
            stage="frontend_build", status=StageStatus.FAILED,
            diagnostics=[_diag("frontend_build", f"Frontend build runner crashed: {exc}",
                               ErrorSeverity.MEDIUM, ErrorCategory.BROWSER)],
            duration_ms=_ms(t0),
        )

    if result.node_missing:
        return VerificationResult(stage="frontend_build", status=StageStatus.SKIPPED,
                                  metadata={"reason": "node not installed"}, duration_ms=_ms(t0))

    diagnostics: list[Diagnostic] = []
    if not result.success:
        for err in (result.errors or [])[:10]:
            diagnostics.append(_diag("frontend_build", err, ErrorSeverity.HIGH, ErrorCategory.BROWSER,
                                     hint="Fix the Vite/React build error in the referenced file"))
        if not diagnostics:
            # _parse_vite_errors's regexes didn't match anything in this
            # build's output, but the build genuinely failed (non-zero exit).
            # Without a diagnostic here the fix loop has literally nothing to
            # group/act on, so a broken build with an unrecognized error format
            # would silently never get fixed. Fall back to raw output so
            # there's at least something for the LLM to work from.
            # Vite/Rollup print the actual error near the START of the output;
            # the tail is just the node stack trace ("...:7) at file://...").
            # Showing raw[-500:] gave the fix LLM only the useless stack tail.
            raw = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
            excerpt = raw[:700] + ("\n...\n" + raw[-200:] if len(raw) > 900 else "")
            diagnostics.append(_diag(
                "frontend_build",
                f"Vite build failed (exit {result.exit_code}) with an unrecognized error format: "
                f"{excerpt if raw else 'no output captured'}",
                ErrorSeverity.HIGH, ErrorCategory.BROWSER,
                hint="Inspect the raw build output for the actual error and fix the referenced file",
            ))

    status = StageStatus.PASSED if result.success else StageStatus.FAILED
    return VerificationResult(stage="frontend_build", status=status, diagnostics=diagnostics,
                              duration_ms=_ms(t0), metadata={"build_time": result.build_time})


# ═══════════════════════════════════════════════════════════════
#   STAGE 3 — Runtime
# ═══════════════════════════════════════════════════════════════

def _find_route_file_for_entity(project_path: str, entity: str) -> Optional[str]:
    """
    Resolve the on-disk route file (relative path) for a CRUD entity name
    (e.g. "habits" -> "app/routes/habit_routes.py").

    A JourneyCRUDFailure diagnostic has no file of its own -- the server
    started fine, only a CRUD step failed. Without a file_path, the fix
    prompt has zero project-specific grounding (no file content, no
    required-endpoints list) and the LLM falls back to the FastAPI tutorial's
    canonical example (app/routers/items.py, ItemResponse) instead of
    touching the real project files -- three wasted, reverted fix attempts
    chasing a nonexistent app/routers/ directory. Mirrors the equivalent
    lookup already used by runtime_fix_service.py's JourneyCRUDFailure path.
    """
    if not entity:
        return None
    import os
    routes_dir = os.path.join(project_path, "app", "routes")
    if not os.path.isdir(routes_dir):
        return None
    route_files = [
        f for f in sorted(os.listdir(routes_dir))
        if f.endswith("_routes.py") and f != "auth_routes.py"
    ]
    for rf in route_files:
        stem = rf.replace("_routes.py", "")
        if stem == entity or stem == entity.rstrip("s") or entity.rstrip("s") == stem:
            return os.path.join("app", "routes", rf).replace(os.sep, "/")
    return None


def _write_journey_bundle(ctx, journey: dict) -> dict | None:
    """
    Persist a Forensic Bundle for the first failed journey step that
    captured request/response evidence (Task 2's _ExchangeRecorder).
    Returns None if the journey passed, or if the failing step never made
    an HTTP call before raising (nothing to bundle).
    """
    steps = journey.get("steps", [])
    failed = [s for s in steps if not s.get("passed") and (s.get("request") or s.get("response"))]
    if not failed:
        return None
    target = failed[0]
    from app.memory.forensic_bundle import write_bundle
    return write_bundle(
        project=getattr(ctx, "project_name", "unknown"),
        stage="runtime",
        failure_class="JourneyCRUDFailure",
        step=target.get("name"),
        provider=getattr(ctx, "current_provider", None),
        request=target.get("request"),
        response=target.get("response"),
    )


def _run_runtime_validation(ctx: GenerationContext) -> VerificationResult:
    t0 = time.time()
    try:
        from app.runtime.backend_runner import run_backend_validation
        result = run_backend_validation(str(ctx.project_path), port=ctx.backend_port,
                                        architecture=_arch_dict(ctx), keep_alive=True)
        ctx._backend_runner = result.get("_runner")  # stopped at the end of VerificationEngine.run()
        # Stashed unconditionally (success or fail) so Stage 10 can reuse this
        # exact journey instead of re-deriving the CRUD entity and re-running
        # it with playwright_workflow's separate, less accurate implementation.
        ctx.journey_result = result.get("journey") or {}
    except Exception as exc:
        crash_diag = _diag("runtime", f"RuntimeRunner crashed: {exc}", ErrorSeverity.CRITICAL, ErrorCategory.RUNTIME)
        ctx.runtime_result = RuntimeResult(success=False, diagnostics=[crash_diag], duration_ms=_ms(t0))
        return VerificationResult(
            stage="runtime", status=StageStatus.FAILED,
            diagnostics=[crash_diag],
            duration_ms=_ms(t0),
        )

    diagnostics: list[Diagnostic] = []
    success = result.get("success", False)

    if not success:
        stderr = result.get("stderr", "")
        parsed = result.get("parsed_error", {})
        err_type = parsed.get("type", "RuntimeError")
        message = parsed.get("message") or (stderr[:400] if stderr else "Backend failed to start")
        # A CRUD-journey failure on a HEALTHY backend used to fall through to
        # the "Backend failed to start" fallback (its parsed_error has no
        # "message" key and stderr is just uvicorn INFO noise) — the fix LLM
        # then chased a nonexistent startup crash instead of the actual
        # failing journey steps.
        #
        # In practice parse_runtime_error() (app/runtime/error_parser.py) never
        # produces type=="JourneyCRUDFailure" — that type is only built by the
        # separate legacy runtime_validator_service.py path. And when keep_alive=True
        # and the server is healthy, backend_runner.py deliberately blanks stdout/
        # stderr (lines ~263-268) so the live process can be handed to later
        # verification stages — so `parsed` stays `{}` here even though the journey
        # runner (result["journey"]) already recorded exactly which step failed and
        # why. Without this, every such failure collapsed to the same contentless
        # "Backend failed to start" message, which (a) sent the fix LLM chasing a
        # nonexistent startup crash and (b) — since FixCache hashes on message text —
        # made unrelated projects share one FixCache entry, silently replaying a
        # stale, irrelevant cached patch instead of getting a real diagnosis.
        journey = result.get("journey") or {}
        journey_failed = bool(journey) and not journey.get("skipped") and journey.get("success") is False
        failed_steps = parsed.get("failed_steps") or [
            (s.get("name"), s.get("detail")) for s in journey.get("steps", []) if not s.get("passed")
        ]
        steps_txt = "; ".join(f"{s[0]}: {s[1]}" for s in failed_steps[:4]
                              if isinstance(s, (list, tuple)) and len(s) >= 2)

        # backend_runner now drains stderr even in keep_alive mode, so a handler
        # 500 during the CRUD journey yields a REAL parsed error (type + hint +
        # file) from the traceback. That is far more actionable than the generic
        # journey-steps message, so prefer it and just append which flow tripped
        # it. Only fall back to the journey/endpoint message when the parse was
        # generic ("Unknown"/"RuntimeError") or empty.
        has_specific = bool(parsed) and parsed.get("type") not in (None, "Unknown", "RuntimeError")
        bundle_ref = None
        if has_specific:
            # The actual exception is at the TAIL of the traceback, not the head.
            tail = ""
            for line in reversed((stderr or "").splitlines()):
                s = line.strip()
                if s and ("Error" in s or "Exception" in s or "constraint failed" in s):
                    tail = s
                    break
            message = tail or parsed.get("hint") or parsed.get("type")
            if steps_txt:
                message = f"{message}  [CRUD journey failed — {steps_txt}]"
        elif err_type == "JourneyCRUDFailure" or journey_failed:
            message = (f"Backend healthy but CRUD journey failed — {steps_txt}"
                       if steps_txt else "Backend healthy but CRUD journey failed")
            err_type = "JourneyCRUDFailure"
            try:
                bundle_ref = _write_journey_bundle(ctx, journey)
            except Exception:
                bundle_ref = None  # never let bundle writing break verification
        elif not journey_failed and message == "Backend failed to start":
            # Not a journey failure either — likely the endpoint-smoke-test pass
            # rate tripped runtime_success = False. Surface that instead of the
            # contentless fallback so the message (and FixCache hash) carries signal.
            pass_rate = result.get("endpoint_pass_rate")
            issues = result.get("behavioral_issues") or []
            if pass_rate is not None and issues:
                issues_txt = "; ".join(
                    f"{i.get('method')} {i.get('path')} -> {i.get('issue')}" for i in issues[:4]
                )
                message = f"Endpoint pass rate {pass_rate:.0%} — {issues_txt}"
                err_type = "EndpointSmokeFailure"
        cat_map = {"ModuleNotFoundError": ErrorCategory.DEPENDENCY,
                   "EndpointSmokeFailure": ErrorCategory.API,
                   "SyntaxError": ErrorCategory.SYNTAX,
                   "ImportError": ErrorCategory.IMPORT,
                   "JourneyCRUDFailure": ErrorCategory.API}
        error_file = parsed.get("error_file")
        if not error_file and err_type == "JourneyCRUDFailure":
            error_file = _find_route_file_for_entity(str(ctx.project_path), journey.get("entity", ""))
        diagnostics.append(Diagnostic(
            error_id=Diagnostic.make_id("runtime", cat_map.get(err_type, ErrorCategory.RUNTIME),
                                        f"[{err_type}] {message}", error_file),
            category=cat_map.get(err_type, ErrorCategory.RUNTIME),
            severity=ErrorSeverity.CRITICAL,
            source="runtime",
            message=f"[{err_type}] {message}",
            file_path=error_file,
            # The exception + its frames are at the END of the captured stderr;
            # the head is just uvicorn's startup banner. Give the fix LLM the tail.
            stack_trace=stderr[-1500:] if stderr else None,
            fix_hint=parsed.get("hint"),
            metadata={"parsed_error": parsed, "bundle_ref": bundle_ref},
        ))

    # The URL is valid whenever the server answered its health check — even
    # if the CRUD journey failed — so later HTTP/browser stages can run.
    if result.get("health_passed") or success:
        ctx.backend_url = f"http://127.0.0.1:{ctx.backend_port}"

    ctx.runtime_result = RuntimeResult(
        success=success,
        backend_started=result.get("started", False),
        health_passed=result.get("health_passed", False),
        stderr=result.get("stderr", ""),
        stdout=result.get("stdout", ""),
        crash_reason=result.get("crash_reason"),
        endpoint_results=result.get("endpoint_results", {}),
        diagnostics=diagnostics,
        duration_ms=_ms(t0),
    )

    status = StageStatus.PASSED if success else StageStatus.FAILED
    return VerificationResult(
        stage="runtime", status=status, diagnostics=diagnostics,
        duration_ms=_ms(t0), metadata={"backend_url": ctx.backend_url},
    )


# ═══════════════════════════════════════════════════════════════
#   STAGE 3b — Schema/DB assertion (post-boot, parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _openapi_schema_ref(get_op: dict) -> Optional[str]:
    """First $ref found in a GET operation's 200 JSON response schema,
    unwrapping one level of `items` (list endpoints) or `allOf` (FastAPI's
    inheritance-composition style)."""
    try:
        schema = get_op["responses"]["200"]["content"]["application/json"]["schema"]
    except (KeyError, TypeError):
        return None
    return _first_ref(schema)


def _first_ref(schema: Any) -> Optional[str]:
    if not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        return schema["$ref"]
    if "items" in schema:
        return _first_ref(schema["items"])
    if schema.get("allOf"):
        return _first_ref(schema["allOf"][0])
    return None


def _resolve_required_fields(schema_name: str, schemas: dict) -> tuple[str, list[str]]:
    """Required field names for an OpenAPI component schema, resolving one
    level of `allOf` composition (FastAPI emits this for schemas built via
    class inheritance -- exactly the case the static AST-based
    schema_model_validator misses, since it inspects each ClassDef in
    isolation without following base-class fields)."""
    schema_def = schemas.get(schema_name, {})
    required = list(schema_def.get("required", []))
    title = schema_def.get("title") or schema_name
    for part in schema_def.get("allOf", []):
        ref = part.get("$ref")
        if ref:
            sub = schemas.get(ref.rsplit("/", 1)[-1], {})
            required.extend(sub.get("required", []))
    return title, sorted(set(required))


def _run_schema_db_assertion(ctx: GenerationContext) -> VerificationResult:
    """
    Post-boot assertion: for every list-style GET endpoint, fetch the live
    OpenAPI spec's declared response schema and diff its required fields
    against the ACTUAL JSON keys the running server returns.

    This is a runtime complement to the static, AST-based
    schema_model_validator (which pattern-matches Column()/mapped_column()
    calls and inherits nothing across class hierarchies): it inspects the
    server's own fully-resolved OpenAPI schema and a real response body,
    so it also catches mismatches the static check can miss (fields from a
    shared base schema, dynamically-added columns) -- and produces an
    EXACT missing-field diff instead of the HTTP stage's generic
    "GET /x returned 500", directly targeting the PydanticSerializationError/
    ResponseValidationError/ModelFieldMismatch failure patterns.

    Scoped to endpoints without a path parameter (list endpoints) so it
    never needs a real id and never has side effects -- pure GETs only.
    """
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    if not ctx.backend_url:
        return VerificationResult(stage="schema_db_assertion", status=StageStatus.SKIPPED,
                                  metadata={"reason": "no backend url"}, duration_ms=_ms(t0))
    try:
        import httpx
        base = ctx.backend_url.rstrip("/")
        spec_resp = httpx.get(base + "/openapi.json", timeout=5)
        if spec_resp.status_code != 200:
            return VerificationResult(stage="schema_db_assertion", status=StageStatus.SKIPPED,
                                      metadata={"reason": f"openapi.json returned {spec_resp.status_code}"},
                                      duration_ms=_ms(t0))
        spec = spec_resp.json()
        schemas = spec.get("components", {}).get("schemas", {})
        checked = 0

        for path, methods in spec.get("paths", {}).items():
            if "{" in path:
                continue  # detail routes need a real id -- list endpoints only
            get_op = methods.get("get")
            if not get_op:
                continue
            ref = _openapi_schema_ref(get_op)
            if not ref:
                continue
            schema_name, required = _resolve_required_fields(ref.rsplit("/", 1)[-1], schemas)
            if not required:
                continue

            try:
                r = httpx.get(base + path, timeout=5)
            except Exception:
                continue
            if r.status_code != 200:
                continue  # non-2xx is already the HTTP stage's diagnostic to raise

            try:
                body = r.json()
            except Exception:
                continue
            sample = body[0] if isinstance(body, list) and body else (body if isinstance(body, dict) else None)
            if sample is None:
                continue  # empty list -- nothing to diff against yet

            checked += 1
            actual_keys = set(sample.keys())
            missing = sorted(f for f in required if f not in actual_keys)
            if missing:
                diagnostics.append(Diagnostic(
                    error_id=Diagnostic.make_id(
                        "schema_db_assertion", ErrorCategory.CONTRACT,
                        f"missing fields {missing} in {path}", path),
                    category=ErrorCategory.CONTRACT,
                    severity=ErrorSeverity.HIGH,
                    source="schema_db_assertion",
                    message=(f"GET {path} declares response schema '{schema_name}' requiring "
                             f"field(s) {missing}, but the live JSON response only contains "
                             f"{sorted(actual_keys)}. This is a schema/model field mismatch."),
                    file_path=None,
                    fix_hint=(f"Add {missing} to the SQLAlchemy model backing '{schema_name}' "
                              f"(or remove the field from the schema if it was never meant to exist)."),
                ))

        status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
        return VerificationResult(stage="schema_db_assertion", status=status, diagnostics=diagnostics,
                                  duration_ms=_ms(t0), metadata={"endpoints_checked": checked})
    except Exception as exc:
        return VerificationResult(
            stage="schema_db_assertion", status=StageStatus.FAILED,
            diagnostics=[_diag("schema_db_assertion", f"Schema/DB assertion crashed: {exc}",
                               ErrorSeverity.MEDIUM, ErrorCategory.RUNTIME)],
            duration_ms=_ms(t0),
        )


# ═══════════════════════════════════════════════════════════════
#   STAGE 4 — HTTP endpoint tests (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_http_tests(ctx: GenerationContext) -> VerificationResult:
    """Direct HTTP calls to all planned endpoints. Separate from Playwright."""
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    if not ctx.backend_url:
        return VerificationResult(stage="http", status=StageStatus.SKIPPED,
                                  metadata={"reason": "no backend url"}, duration_ms=_ms(t0))
    try:
        import httpx
        endpoints = _get_endpoints(ctx)
        passed, failed = 0, 0
        for ep in endpoints[:20]:  # cap at 20 to avoid test bloat
            method = ep.get("method", "GET").upper()
            path   = ep.get("path", "/")
            url    = ctx.backend_url.rstrip("/") + path
            try:
                r = httpx.request(method, url, timeout=5)
                # 4xx/5xx that aren't 404/405 are failures
                if r.status_code not in (200, 201, 204, 400, 401, 403, 404, 405, 422):
                    # Ground the diagnostic in the endpoint's real implementing
                    # file (already known from the architecture plan) instead
                    # of leaving file_path unset -- an ungrounded diagnostic
                    # gets no file content or required-endpoints context in
                    # the fix prompt, and the LLM falls back to a generic
                    # FastAPI-tutorial file (app/routers/admin.py, task.py)
                    # that doesn't exist in this project. Same failure mode
                    # already fixed for JourneyCRUDFailure above.
                    diagnostics.append(_diag("http",
                        f"{method} {path} returned {r.status_code}",
                        ErrorSeverity.HIGH, ErrorCategory.API,
                        file_path=ep.get("file")))
                    failed += 1
                else:
                    passed += 1
            except Exception as exc:
                diagnostics.append(_diag("http",
                    f"{method} {path} request failed: {exc}",
                    ErrorSeverity.MEDIUM, ErrorCategory.API))
                failed += 1

        status = StageStatus.PASSED if not diagnostics else (
            StageStatus.FAILED if failed > passed else StageStatus.FAILED
        )
        return VerificationResult(stage="http", status=status, diagnostics=diagnostics,
                                  duration_ms=_ms(t0),
                                  metadata={"passed": passed, "failed": failed})
    except Exception as exc:
        return VerificationResult(stage="http", status=StageStatus.FAILED,
                                  diagnostics=[_diag("http", f"HTTP tester crashed: {exc}",
                                                     ErrorSeverity.MEDIUM, ErrorCategory.API)],
                                  duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 5+6+7 — Browser, Console, Screenshots (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_browser_and_screenshots(ctx: GenerationContext) -> tuple[VerificationResult, list[str]]:
    """
    Playwright page-load + console errors + screenshots.
    Returns (VerificationResult, [b64_png, ...])
    """
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    screenshots: list[str] = []

    try:
        from app.runtime.playwright_runner import run_playwright_tests
        pr = run_playwright_tests(
            str(ctx.project_path),
            architecture=_arch_dict(ctx),
            capture_screenshots=True,
        )
    except Exception as exc:
        pr = None
        diagnostics.append(_diag("browser", f"Playwright runner crashed: {exc}", ErrorSeverity.HIGH, ErrorCategory.BROWSER))

    if pr and not getattr(pr, "skipped", False):
        for ce in (pr.console_errors or []):
            diagnostics.append(_diag("browser", f"Console error: {ce}",
                                     ErrorSeverity.MEDIUM, ErrorCategory.BROWSER,
                                     hint="Fix the JS error; often a missing import or API mismatch"))
        for bp in (pr.blank_pages or []):
            diagnostics.append(_diag("browser", f"Blank page at route: {bp}",
                                     ErrorSeverity.HIGH, ErrorCategory.BROWSER,
                                     hint="React failed to render — check App.jsx for unhandled exceptions"))
        screenshots = [s.get("png_b64", "") for s in (pr.screenshots or []) if s.get("png_b64")]

    skipped = bool(pr and getattr(pr, "skipped", False))
    status = StageStatus.SKIPPED if skipped else (
        StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    )

    # Populate ctx.browser_result so scoring (Frontend Load / Browser UX / Integration)
    # sees real signal instead of always defaulting to "skipped" neutral scores.
    ctx.browser_result = BrowserTestResult(
        success=bool(pr and pr.success),
        page_loaded=bool(pr) and pr.pages_checked > 0 and not (pr.blank_pages or []),
        blank_page=bool(pr and (pr.blank_pages or [])),
        console_errors=list(pr.console_errors) if pr else [],
        screenshots=screenshots,
        navigation_passed=bool(pr and pr.success),
        diagnostics=diagnostics,
        duration_ms=_ms(t0),
        skipped=skipped,
        skip_reason=getattr(pr, "skip_reason", "") if pr else ("Playwright runner crashed" if diagnostics else ""),
    )

    vr = VerificationResult(stage="browser", status=status, diagnostics=diagnostics,
                            duration_ms=_ms(t0),
                            metadata={"screenshot_count": len(screenshots), "skipped": skipped})
    return vr, screenshots


# ═══════════════════════════════════════════════════════════════
#   STAGE 8 — Performance (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_performance_check(ctx: GenerationContext) -> VerificationResult:
    """Measure /health endpoint latency. Flag if > 500ms (deployment SLA)."""
    t0 = time.time()
    if not ctx.backend_url:
        return VerificationResult(stage="performance", status=StageStatus.SKIPPED,
                                  duration_ms=_ms(t0))
    diagnostics: list[Diagnostic] = []
    latencies: list[float] = []
    try:
        import httpx
        health_url = ctx.backend_url.rstrip("/") + "/health"
        for _ in range(3):
            t = time.time()
            try:
                r = httpx.get(health_url, timeout=5)
                latencies.append((time.time() - t) * 1000)
            except Exception:
                latencies.append(5000.0)

        avg_ms = sum(latencies) / len(latencies)
        if avg_ms > 500:
            diagnostics.append(_diag("performance",
                f"/health avg latency {avg_ms:.0f}ms (target <500ms)",
                ErrorSeverity.MEDIUM, ErrorCategory.PERFORMANCE,
                hint="Check for blocking I/O or heavy imports in startup; use lifespan events"))

        status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
        return VerificationResult(stage="performance", status=status, diagnostics=diagnostics,
                                  duration_ms=_ms(t0),
                                  metadata={"health_avg_ms": round(avg_ms, 1), "samples": latencies})
    except Exception as exc:
        return VerificationResult(stage="performance", status=StageStatus.FAILED,
                                  diagnostics=[_diag("performance", f"Perf check crashed: {exc}",
                                                     ErrorSeverity.LOW, ErrorCategory.PERFORMANCE)],
                                  duration_ms=_ms(t0))


# ═══════════════════════════════════════════════════════════════
#   STAGE 9 — Accessibility (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_accessibility_check(ctx: GenerationContext) -> VerificationResult:
    """
    Basic accessibility scan on JSX source files.
    Checks: img without alt, form inputs without labels, ARIA attributes.
    This is static source analysis (no browser needed).
    """
    import re
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    jsx_files = list((ctx.project_path).rglob("*.jsx")) if ctx.project_path else []
    jsx_files += list((ctx.project_path).rglob("*.tsx")) if ctx.project_path else []

    for f in jsx_files[:30]:
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # img without alt
        for m in re.finditer(r"<img(?![^>]*\balt=)[^>]*/?>", src):
            diagnostics.append(_diag("accessibility",
                f"{f.name}: <img> missing alt attribute",
                ErrorSeverity.LOW, ErrorCategory.CONTRACT,
                hint='Add alt="description" to the <img> tag'))

        # input without associated label or aria-label
        inputs = re.findall(r"<input(?![^>]*aria-label=)(?![^>]*aria-labelledby=)[^>]*/?>", src)
        for _ in inputs:
            # Check if there's a <label htmlFor=> nearby (rough check)
            if "htmlFor=" not in src and "<label" not in src:
                diagnostics.append(_diag("accessibility",
                    f"{f.name}: <input> may be missing a label",
                    ErrorSeverity.LOW, ErrorCategory.CONTRACT,
                    hint="Add aria-label='...' or a <label htmlFor=...> for each input"))
                break  # one warning per file is enough

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="accessibility", status=status, diagnostics=diagnostics,
                              duration_ms=_ms(t0),
                              metadata={"files_checked": len(jsx_files)})


# ═══════════════════════════════════════════════════════════════
#   STAGE 10 — Workflow tests (parallel-safe)
# ═══════════════════════════════════════════════════════════════

def _run_workflow_tests(ctx: GenerationContext, screenshots: list[str]) -> tuple[VerificationResult, list[str]]:
    """Register → Login → CRUD workflow via Playwright. Returns (result, screenshots)."""
    t0 = time.time()
    diagnostics: list[Diagnostic] = []
    new_screenshots = list(screenshots)

    if not ctx.backend_url:
        return VerificationResult(stage="workflow", status=StageStatus.SKIPPED,
                                  duration_ms=_ms(t0)), new_screenshots

    wf = None
    try:
        from app.runtime.playwright_workflow import run_workflow_tests
        wf = run_workflow_tests(
            str(ctx.project_path),
            architecture=_arch_dict(ctx),
            base_url=f"http://127.0.0.1:{ctx.frontend_port}",
            capture_screenshots=True,
            journey=ctx.journey_result,
            backend_port=ctx.backend_port,
        )
        # steps_failed (used for the Integration score denominator) includes
        # journey steps reused verbatim from Stage 3's already-executed
        # run_user_journey() -- Stage 3 already raised a CRITICAL
        # JourneyCRUDFailure diagnostic for those, so re-diagnosing them here
        # would just duplicate it. Only nav_steps_failed (the Playwright
        # browser-navigation checks, which Stage 3 never runs) is genuinely
        # new information and gets a diagnostic of its own.
        for step in (wf.nav_steps_failed or []):
            diagnostics.append(_diag("workflow", f"Workflow step failed: {step}",
                                     ErrorSeverity.HIGH, ErrorCategory.INTEGRATION,
                                     hint="Check API endpoint and frontend form binding for this step"))
        new_screenshots += [s.get("png_b64", "") for s in (wf.screenshots or []) if s.get("png_b64")]
    except Exception as exc:
        diagnostics.append(_diag("workflow", f"Workflow runner error: {exc}",
                                 ErrorSeverity.MEDIUM, ErrorCategory.BROWSER))

    # Feed workflow step results into the shared browser_result so the
    # Integration scoring dimension sees real pass/fail data.
    if ctx.browser_result:
        ctx.browser_result.workflow_steps_passed = list(wf.steps_passed) if wf else []
        ctx.browser_result.workflow_steps_failed = list(wf.steps_failed) if wf else []

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="workflow", status=status, diagnostics=diagnostics,
                              duration_ms=_ms(t0)), new_screenshots


# ═══════════════════════════════════════════════════════════════
#   STAGE 11 — LLM Judge (sequential, after all parallel stages)
# ═══════════════════════════════════════════════════════════════

_BLANK_CLAIM_RE = re.compile(
    r"blank|empty page|nothing (?:render|display)|did not (?:load|render)|"
    r"failed to render|no content|white screen|nothing (?:is )?(?:shown|visible)",
    re.IGNORECASE,
)


def _run_llm_judge(
    ctx: GenerationContext,
    all_pre_judge_diagnostics: list[Diagnostic],
    screenshots: list[str],
) -> VerificationResult:
    """
    Send screenshots + all accumulated failures to a vision LLM.
    Produces richer, human-interpretable assessments.
    """
    t0 = time.time()

    # Only run if there are failures worth interpreting
    failures = [d for d in all_pre_judge_diagnostics
                if d.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.HIGH)]
    if not failures and not screenshots:
        return VerificationResult(stage="llm_judge", status=StageStatus.SKIPPED,
                                  metadata={"reason": "no failures to interpret"},
                                  duration_ms=_ms(t0))

    console_errors = [d.message for d in all_pre_judge_diagnostics if "console error" in d.message.lower()]
    network_failures = [d.message for d in all_pre_judge_diagnostics if "http" in d.source.lower()]
    workflow_failures = [d.message for d in all_pre_judge_diagnostics if d.source == "workflow"]

    try:
        from app.verification.llm_judge import judge_screenshot
        # screenshots accumulate in capture order: the initial route sweep
        # (/login, /register, ...) first, then the CRUD workflow's navigation
        # steps. journey/workflow failures happen deep into that sequence
        # (post-login, mid-CRUD) -- judging screenshots[0] means the judge is
        # almost always looking at the login page while the diagnostics it's
        # asked to explain are about a completely different screen. The last
        # screenshot is the one closest to whatever state the failures above
        # actually describe.
        judgment = judge_screenshot(
            screenshot_b64=screenshots[-1] if screenshots else None,
            console_errors=console_errors,
            network_failures=network_failures,
            workflow_failures=workflow_failures,
            app_idea=getattr(ctx, "idea", ""),
        )
    except Exception as exc:
        return VerificationResult(stage="llm_judge", status=StageStatus.SKIPPED,
                                  metadata={"reason": f"judge error: {exc}"},
                                  duration_ms=_ms(t0))

    diagnostics: list[Diagnostic] = []
    sev_map = {"critical": ErrorSeverity.CRITICAL, "high": ErrorSeverity.HIGH,
               "medium": ErrorSeverity.MEDIUM, "low": ErrorSeverity.LOW}
    if judgment.assessment and judgment.severity not in ("info", ""):
        severity = sev_map.get(judgment.severity, ErrorSeverity.MEDIUM)

        # The vision judge reads a screenshot in isolation and can hallucinate
        # "blank page" for a UI that's merely sparse/minimal-looking, or for
        # a screenshot taken before a fast page finished its transition. The
        # browser stage already has a hard structural signal for this exact
        # claim -- #root's actual child count -- so cross-check before
        # trusting a blank/empty verdict enough to hard-block deployment.
        # Real run this fixes: judge said "the application is displaying a
        # completely blank page" about a screenshot that plainly showed a
        # populated dashboard (stat cards, chart, habit list) -- the DOM had
        # mounted real content and there were zero console errors, so the
        # verdict was wrong, not the app.
        if severity == ErrorSeverity.CRITICAL and _BLANK_CLAIM_RE.search(judgment.assessment):
            br = ctx.browser_result
            structural_contradicts = (
                br is not None and not br.skipped
                and not br.blank_page and br.page_loaded
                and not br.console_errors
            )
            if structural_contradicts:
                severity = ErrorSeverity.MEDIUM
                print("  [verify]       LLM Judge claimed a blank/empty page, but the browser "
                      "stage found mounted DOM content with zero console errors -- treating "
                      "as a likely vision misread and downgrading to medium severity")

        diagnostics.append(Diagnostic(
            error_id=Diagnostic.make_id("llm_judge", ErrorCategory.BROWSER, judgment.assessment),
            category=ErrorCategory.BROWSER,
            severity=severity,
            source="llm_judge",
            message=judgment.assessment,
            fix_hint=judgment.fix_hint or None,
            metadata={"confidence": judgment.confidence, "screenshot_used": judgment.screenshot_available},
        ))
        # Recorded unconditionally (any severity/confidence) for scoring —
        # see _dim_llm_judge_visual. is_deployment_ready applies its own
        # confidence bar before treating this as a hard deploy-block, so a
        # low-confidence vision misread can still show up in the score
        # without being able to fail an otherwise-sound generation outright.
        ctx.llm_judge_severity = severity
        ctx.llm_judge_confidence = judgment.confidence

    status = StageStatus.PASSED if not diagnostics else StageStatus.FAILED
    return VerificationResult(stage="llm_judge", status=status, diagnostics=diagnostics,
                              duration_ms=_ms(t0),
                              metadata={"confidence": judgment.confidence, "model": judgment.model_used})


# ═══════════════════════════════════════════════════════════════
#   Main VerificationEngine
# ═══════════════════════════════════════════════════════════════

class VerificationEngine:
    """
    Orchestrates all 11 verification stages.

    Parallel execution model:
      After the backend is confirmed running, HTTP / Browser / Performance /
      Accessibility stages run concurrently in a thread pool.
      Only the LLM Judge and Failure Graph run sequentially after (they
      need all other results first).
    """

    def __init__(self, run_runtime: bool = True, run_browser: bool = True):
        self.run_runtime = run_runtime
        self.run_browser = run_browser
        self._extra: list[Any] = []

    def register(self, verifier) -> "VerificationEngine":
        self._extra.append(verifier)
        return self

    def run(self, ctx: GenerationContext) -> list[VerificationResult]:
        # A prior run() may have left a backend alive (keep_alive=True) for its
        # own later stages -- stop it before starting a fresh one on the same port.
        prior_runner = getattr(ctx, "_backend_runner", None)
        if prior_runner:
            try:
                prior_runner.stop()
            except Exception:
                pass

        results: list[VerificationResult] = []
        ctx.static_results  = []
        ctx.runtime_result  = None
        ctx.browser_result  = None
        ctx.extra_results   = []
        ctx._backend_runner = None
        all_screenshots:  list[str] = []

        # ── Stage 1: Static ───────────────────────────────────────────────────
        print("  [verify] 1/11 Static analysis...")
        evt = ctx.begin_stage("static-validation")
        sr = _run_static_validators(ctx)
        ctx.static_results.append(sr)
        results.append(sr)
        ctx.end_stage(evt, sr.status, errors=len([d for d in sr.diagnostics if d.severity != ErrorSeverity.LOW]))
        print(f"  [verify]       {sr.status.value} — {len(sr.diagnostics)} issues")

        # ── Stage 2: Compile ──────────────────────────────────────────────────
        print("  [verify] 2/11 Compile check...")
        cr = _run_compile_check(ctx)
        ctx.static_results.append(cr)
        results.append(cr)
        print(f"  [verify]       {cr.status.value} — {len(cr.diagnostics)} syntax errors")

        # ── Stage 2a: Import closure ──────────────────────────────────────────
        print("  [verify] 2a Import closure check...")
        ic = _run_import_closure_check(ctx)
        ctx.static_results.append(ic)
        results.append(ic)
        print(f"  [verify]       {ic.status.value} — {len(ic.diagnostics)} unresolved imports")

        # ── Stage 2a-symbols: Symbol closure ──────────────────────────────────
        print("  [verify] 2a Symbol closure check...")
        sc = _run_symbol_closure_check(ctx)
        ctx.static_results.append(sc)
        results.append(sc)
        print(f"  [verify]       {sc.status.value} — {len(sc.diagnostics)} undefined symbols")

        # ── Stage 2a2: Contract conformance (warn-only) ───────────────────────
        # FORGE_CONTRACT_CHECK=0 skips this stage entirely -- added solely to
        # allow a controlled A/B (contract on vs off, all else identical) to
        # measure whether it actually reduces contract-coherence failures.
        # Defaults to on; unset/"1" is identical to before this flag existed.
        if os.environ.get("FORGE_CONTRACT_CHECK", "1") != "0":
            print("  [verify] 2a2 Contract conformance check (warn-only)...")
            cc = _run_contract_conformance_check(ctx)
            ctx.static_results.append(cc)
            results.append(cc)
            print(f"  [verify]       {cc.status.value} — {len(cc.diagnostics)} contract findings")

        # ── Stage 2b: Frontend build (produces dist/ for Playwright) ──────────
        print("  [verify] 2b Frontend build...")
        fb = _run_frontend_build(ctx)
        ctx.static_results.append(fb)
        results.append(fb)
        print(f"  [verify]       {fb.status.value} — {len(fb.diagnostics)} build errors")
        # Print the actual error text — logs used to say "failed — 1 build
        # errors" for entire runs without ever showing WHAT failed.
        for d in fb.diagnostics[:5]:
            print(f"  [verify]         ↳ {d.message[:200]}")

        # ── Extra custom verifiers ────────────────────────────────────────────
        for verifier in self._extra:
            try:
                vr = verifier(ctx) if callable(verifier) else verifier.verify(ctx)
                ctx.static_results.append(vr)
                results.append(vr)
            except Exception as exc:
                results.append(VerificationResult(
                    stage="custom", status=StageStatus.FAILED,
                    diagnostics=[_diag("custom", f"Custom verifier crashed: {exc}",
                                       ErrorSeverity.MEDIUM, ErrorCategory.RUNTIME)],
                ))

        # ── Stage 3: Runtime ──────────────────────────────────────────────────
        critical_static = sum(1 for r in ctx.static_results for d in r.diagnostics
                              if d.severity == ErrorSeverity.CRITICAL)

        if self.run_runtime:
            if critical_static > 0:
                print(f"  [verify] 3/11 Runtime: SKIPPED ({critical_static} critical static errors)")
                results.append(VerificationResult(stage="runtime", status=StageStatus.SKIPPED,
                                                  metadata={"reason": "critical static errors"}))
            else:
                print("  [verify] 3/11 Runtime startup...")
                evt = ctx.begin_stage("runtime-validation")
                rr = _run_runtime_validation(ctx)
                results.append(rr)
                ctx.end_stage(evt, rr.status)
                print(f"  [verify]       {rr.status.value}")
        else:
            results.append(VerificationResult(stage="runtime", status=StageStatus.SKIPPED))

        # ── Stages 4–9: Parallel post-runtime ────────────────────────────────
        # Gate on the server being ALIVE (health passed), not on full runtime
        # success: a failing CRUD journey used to skip every browser/HTTP/
        # integration stage even though the server was up, silently capping
        # the score around 59 and making deployment unreachable.
        runtime_ok = bool(
            ctx.runtime_result
            and (ctx.runtime_result.health_passed or ctx.runtime_result.success)
            and getattr(ctx, "_backend_runner", None)
        )

        if runtime_ok and self.run_browser:
            print("  [verify] 4-9: Running HTTP / Schema-DB / Browser / Performance / Accessibility in parallel...")
            evt = ctx.begin_stage("parallel-checks")

            # browser_result captures + screenshots stored separately
            browser_vr: Optional[VerificationResult] = None
            http_vr: Optional[VerificationResult]    = None
            schema_vr: Optional[VerificationResult]  = None
            perf_vr: Optional[VerificationResult]    = None
            a11y_vr: Optional[VerificationResult]    = None

            with ThreadPoolExecutor(max_workers=5) as pool:
                fut_http   = pool.submit(_run_http_tests, ctx)
                fut_schema = pool.submit(_run_schema_db_assertion, ctx)
                fut_browser = pool.submit(_run_browser_and_screenshots, ctx)
                fut_perf   = pool.submit(_run_performance_check, ctx)
                fut_a11y   = pool.submit(_run_accessibility_check, ctx)

                # Collect results as they finish
                for fut in as_completed([fut_http, fut_schema, fut_browser, fut_perf, fut_a11y]):
                    try:
                        result = fut.result()
                        if fut is fut_browser:
                            browser_vr, screenshots = result
                            all_screenshots.extend(s for s in screenshots if s)
                        elif fut is fut_http:
                            http_vr = result
                        elif fut is fut_schema:
                            schema_vr = result
                        elif fut is fut_perf:
                            perf_vr = result
                        elif fut is fut_a11y:
                            a11y_vr = result
                    except Exception as exc:
                        results.append(VerificationResult(
                            stage="parallel", status=StageStatus.FAILED,
                            diagnostics=[_diag("parallel", f"Parallel stage crashed: {exc}",
                                               ErrorSeverity.MEDIUM, ErrorCategory.RUNTIME)],
                        ))

            ctx.end_stage(evt, StageStatus.PASSED)

            # http/schema/performance/accessibility diagnostics used to only live
            # in the local `results` list (used for printing) and were never
            # visible to the fix loop or regression detection -- ctx.extra_results
            # fixes that.
            for stage_result in [http_vr, schema_vr, perf_vr, a11y_vr]:
                if stage_result:
                    results.append(stage_result)
                    ctx.extra_results.append(stage_result)
                    print(f"  [verify]       {stage_result.stage}: {stage_result.status.value} "
                          f"— {len(stage_result.diagnostics)} issues")
            if browser_vr:
                results.append(browser_vr)
                print(f"  [verify]       {browser_vr.stage}: {browser_vr.status.value} "
                      f"— {len(browser_vr.diagnostics)} issues")

            # ── Stage 10: Workflow (sequential, needs browser to be done) ────
            print("  [verify] 10/11 Workflow tests...")
            wf_vr, all_screenshots = _run_workflow_tests(ctx, all_screenshots)
            results.append(wf_vr)
            ctx.extra_results.append(wf_vr)
            print(f"  [verify]       {wf_vr.status.value}")

        else:
            skip_reason = "backend not running" if not runtime_ok else "browser disabled"
            for stage in ("http", "schema_db_assertion", "browser", "performance", "accessibility", "workflow"):
                results.append(VerificationResult(stage=stage, status=StageStatus.SKIPPED,
                                                  metadata={"reason": skip_reason}))
                print(f"  [verify] SKIPPED {stage}: {skip_reason}")

        # ── Stage 11: LLM Judge (sequential, needs all previous results) ─────
        # Deliberately NOT added to ctx.extra_results: its diagnostic message is
        # free-form LLM text that varies in wording between calls even for the
        # same underlying issue, so content-hashing it for regression detection
        # would make almost every judge comment look "new" and falsely revert
        # real improvements. It stays informational (printed) only.
        print("  [verify] 11/11 LLM Judge...")
        all_pre_judge_diags = [d for r in results for d in r.diagnostics]
        judge_vr = _run_llm_judge(ctx, all_pre_judge_diags, all_screenshots)
        results.append(judge_vr)
        if judge_vr.diagnostics:
            print(f"  [verify]       LLM Judge: {judge_vr.diagnostics[0].message[:80]}")
        else:
            print(f"  [verify]       LLM Judge: {judge_vr.status.value}")

        # ── Failure Graph analysis ────────────────────────────────────────────
        all_diags = [d for r in results for d in r.diagnostics]
        graph = _build_graph(all_diags)
        if graph and graph.suppressed_count > 0:
            print(f"  [verify] Failure Graph: {len(graph.roots)} root cause(s), "
                  f"{graph.suppressed_count} downstream suppressed")
            print(f"  [verify] {graph.explain()[:300]}")
            # Store graph on ctx for the fix orchestrator to use
            ctx.failure_graph = graph  # type: ignore[attr-defined]

        # All stages that needed a live backend (HTTP/browser/perf/workflow) are
        # done -- stop the server kept alive by _run_runtime_validation.
        if getattr(ctx, "_backend_runner", None):
            try:
                ctx._backend_runner.stop()
            except Exception:
                pass
            ctx._backend_runner = None

        return results


# ═══════════════════════════════════════════════════════════════
#   Helpers
# ═══════════════════════════════════════════════════════════════

def _ms(t0: float) -> float:
    return (time.time() - t0) * 1000


def _diag(source: str, msg: str, sev: ErrorSeverity, cat: ErrorCategory,
          hint: Optional[str] = None, file_path: Optional[str] = None) -> Diagnostic:
    return Diagnostic(
        error_id=Diagnostic.make_id(source, cat, msg, file_path),
        category=cat,
        severity=sev,
        source=source,
        message=msg,
        file_path=file_path,
        fix_hint=hint,
    )


def _get_endpoints(ctx: GenerationContext) -> list[dict]:
    arch = ctx.architecture
    if not arch:
        return []
    if isinstance(arch, dict):
        return arch.get("api_endpoints", [])
    return getattr(arch, "api_endpoints", []) or []


def _arch_dict(ctx: GenerationContext) -> Optional[dict]:
    if ctx.architecture and hasattr(ctx.architecture, "dict"):
        return ctx.architecture.dict()
    if isinstance(ctx.architecture, dict):
        return ctx.architecture
    return None


def _build_graph(diagnostics: list[Diagnostic]):
    try:
        from app.verification.graph import build_failure_graph
        return build_failure_graph(diagnostics)
    except Exception:
        return None


def _categorise_static(err: str) -> ErrorCategory:
    e = err.lower()
    if "syntax" in e:                         return ErrorCategory.SYNTAX
    if "auth field mismatch" in e:            return ErrorCategory.CONTRACT
    if "import" in e or "module" in e:        return ErrorCategory.IMPORT
    if "orm" in e or "flask" in e:            return ErrorCategory.CONTRACT
    if "router" in e or "apirouter" in e:     return ErrorCategory.CONTRACT
    if "missing endpoint" in e:               return ErrorCategory.API
    if "schema" in e or "model" in e:         return ErrorCategory.CONTRACT
    if "database" in e or "session" in e:     return ErrorCategory.CONTRACT
    if "stub" in e or "placeholder" in e:     return ErrorCategory.CONTRACT
    if "undefined" in e or "symbol" in e:     return ErrorCategory.IMPORT
    return ErrorCategory.CONTRACT


def _severity_static(err: str) -> ErrorSeverity:
    e = err.lower()
    if "syntax error" in e:                   return ErrorSeverity.CRITICAL
    if "missing app/main.py" in e:            return ErrorSeverity.CRITICAL
    if "missing route file" in e:             return ErrorSeverity.HIGH
    if "orm violation" in e:                  return ErrorSeverity.HIGH
    if "router export" in e:                  return ErrorSeverity.HIGH
    if "undefined symbol" in e:               return ErrorSeverity.HIGH
    # 100%-repro, no other check catches it: the journey runner builds its
    # request from the OpenAPI schema, not the actual frontend form, so a
    # broken register form passes every existing runtime/journey check while
    # every real user's signup 422s.
    if "auth field mismatch" in e:            return ErrorSeverity.HIGH
    if "session leak" in e:                   return ErrorSeverity.MEDIUM
    if "stub handler" in e:                   return ErrorSeverity.MEDIUM
    return ErrorSeverity.MEDIUM


def _hint_static(err: str) -> Optional[str]:
    e = err.lower()
    if "orm violation" in e:
        return "Replace db.Model / db = SQLAlchemy() with SQLAlchemy Base"
    if "router export" in e:
        return "Rename `router` to `{resource}_router = APIRouter()`"
    if "syntax error" in e:
        return "Fix Python syntax; check for missing colons, bad indentation"
    return None


_STATIC_FILEPATH_RE = re.compile(r"\b((?:app|src)[\\/][\w./\\-]+?\.(?:py|jsx|js|tsx|ts|txt))\b")


def _filepath_static(err: str) -> Optional[str]:
    """
    Extract an embedded file path from a validate_project() error string.

    Every Diagnostic built from these errors used to leave file_path unset,
    so the repair grouper (app/repair/grouper.py) saw an empty affected_files
    list for ALL of them regardless of whether the message actually named a
    file -- e.g. "Frontend auth field mismatch: src/pages/RegisterPage.jsx
    POSTs to..." names the exact broken file, but the group still fell back to
    ungrounded fix context. For a frontend-only diagnostic like that one, the
    backend-files listing added for ungrounded groups doesn't help either --
    it nudges the model toward "fixing" an unrelated backend schema instead of
    the actual named frontend file (observed live: the diagnostic recurred
    unchanged after the LLM patched app/schemas/user.py instead of
    RegisterPage.jsx). Grounding the group in the file the message already
    names fixes both problems at once.
    """
    m = _STATIC_FILEPATH_RE.search(err)
    return m.group(1).replace("\\", "/") if m else None


def _extra_filepaths_static(err: str) -> list[str]:
    """
    Return every embedded file path in `err` BEYOND the first (which
    `_filepath_static` already captures as the diagnostic's primary
    file_path).

    A message like "Duplicate class definition: 'UserUpdate' is defined in
    multiple files (app/schemas/user.py, app/routes/user_routes.py)" names
    TWO files, but `_filepath_static`'s `.search()` only grabs the first —
    the repair grouper then groups this diagnostic under file_path alone
    and `_build_fix_prompt` only ever shows the LLM app/schemas/user.py's
    content. The LLM dutifully edits that file every fix round, but the
    actual duplicate copy living in app/routes/user_routes.py is never in
    its context to remove, so the identical diagnostic recurs unchanged
    across every fix attempt. Callers stash this list on
    Diagnostic.metadata["extra_file_paths"] so the grouper can fold them
    into affected_files alongside the primary file_path.
    """
    paths = _STATIC_FILEPATH_RE.findall(err)
    seen = set()
    extra = []
    for p in paths:
        norm = p.replace("\\", "/")
        if norm in seen:
            continue
        seen.add(norm)
        extra.append(norm)
    return extra[1:]
