"""
Experiment 071: deterministic auth-route completeness check + repair.

Root cause traced this cycle (docs/AUTH_COMPLETENESS.md Part 1 has the
full audit): `app/services/deterministic_patcher.py::_patch_auth_routes()`
already injects a known-good, role-aware auth_routes.py and wires it
into main.py -- but `app/services/v6_orchestrator.py` calls
`run_deterministic_patches(project_path, skip_protected_injections=True)`
at TWO points (lines 666 and 1191), both immediately after an
LLM-driven architecture repair (`generate_architecture_fix()`) writes
its own files via `write_fix()`. `skip_protected_injections=True`
deliberately disables the auth-routes/auth-utils injection there, on
the theory that the architecture repair's own output is authoritative
and shouldn't be clobbered -- but `generate_architecture_fix()` has no
explicit instruction to preserve auth wiring, so if its output touches
main.py or auth_routes.py, the safety net that would normally have
caught a resulting gap is exactly the one disabled at that moment.
This is the most concrete, evidenced explanation this cycle found for
why `POST /auth/register` 404s recurred in 9/14 of Experiment 068's
forensic bundles despite `_patch_auth_routes()` existing and working
correctly in the common case (confirmed empirically: both
generated_projects/todo_list_app and .../inventory_manager currently
carry a correctly-wired auth_routes.py on disk -- the mechanism works
when it runs; the gap is specifically the two call sites where it's
told not to).

This module does NOT replace `_patch_auth_routes()` -- it provides an
independent, unconditional completeness CHECK that runs regardless of
which code path or flag state produced the current file state, and a
REPAIR that reuses the existing template injection (not a
reimplementation) when the check fails. This closes the
skip_protected_injections gap without weakening it for the case it was
designed for (not clobbering a working architecture-repair fix) --
this module only overwrites when the end state is actually broken.
"""
from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REQUIRED_AUTH_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
)
# Not hard-required (Part 2: "if architecture requires it") -- reported
# for visibility but do not by themselves make the app "incomplete".
# The concrete evidence this cycle traced (Exp068's forensic bundles)
# is entirely about register/login; me/logout absence was never
# observed as an actual failure signature.
RECOMMENDED_AUTH_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", "/auth/me"),
    ("POST", "/auth/logout"),
)

_TELEMETRY_LOG_PATH = (
    Path(__file__).parent.parent.parent / "failure_memory" / "auth_completeness_log.jsonl"
)


@dataclass
class AuthCompletenessResult:
    complete: bool
    found_endpoints: set = field(default_factory=set)
    missing_required: list = field(default_factory=list)
    missing_recommended: list = field(default_factory=list)
    router_import_present: bool = False
    router_include_present: bool = False
    router_var: Optional[str] = None
    router_module: Optional[str] = None
    duplicate_registrations: list = field(default_factory=list)
    parse_errors: list = field(default_factory=list)
    reason: str = ""


def _normalize_path(p: str) -> str:
    p = p.strip()
    if not p.startswith("/"):
        p = "/" + p
    # collapse "//" from prefix+path concatenation, strip a trailing slash
    # (except for the root path itself)
    p = re.sub(r"/{2,}", "/", p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """`X = APIRouter(prefix="/auth")` -> {"X": "/auth"}. Router
    variables with no explicit prefix map to ""."""
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Name) and func.id == "APIRouter"):
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _scan_file_routes(file_path: Path, prefixes: dict[str, str]) -> list[tuple[str, str, str]]:
    """Returns [(method, normalized_path, router_var), ...] for every
    @X.<verb>("...") decorator in this file. Verbs checked: get, post,
    put, patch, delete. Malformed files are skipped, not fatal (matches
    this project's established defensive-parsing convention)."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content)
    except (SyntaxError, OSError, UnicodeDecodeError):
        return []

    file_prefixes = dict(_router_prefixes(tree))
    file_prefixes.update(prefixes)  # caller-supplied overrides (cross-file prefix knowledge)

    routes: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            dec_func = dec.func
            if not isinstance(dec_func, ast.Attribute):
                continue
            method = dec_func.attr.upper()
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            router_obj = dec_func.value
            if not isinstance(router_obj, ast.Name):
                continue
            router_var = router_obj.id
            if not dec.args or not isinstance(dec.args[0], ast.Constant) or not isinstance(dec.args[0].value, str):
                continue
            raw_path = dec.args[0].value
            prefix = file_prefixes.get(router_var, "")
            routes.append((method, _normalize_path(prefix + raw_path), router_var))
    return routes


def _scan_project_routes(project_path: Path) -> tuple[dict[tuple[str, str], list[tuple[str, str]]], list[str]]:
    """Scans every .py file under app/routes/ and app/main.py itself
    (defensive -- some generated apps inline a route or two directly in
    main.py). Returns ({(method, path): [(file, router_var), ...]},
    parse_errors)."""
    found: dict[tuple[str, str], list[tuple[str, str]]] = {}
    errors: list[str] = []

    candidates: list[Path] = []
    routes_dir = project_path / "app" / "routes"
    if routes_dir.exists():
        candidates.extend(sorted(routes_dir.glob("*.py")))
    main_py = project_path / "app" / "main.py"
    if main_py.exists():
        candidates.append(main_py)

    for f in candidates:
        try:
            routes = _scan_file_routes(f, {})
        except Exception as e:
            errors.append(f"{f.name}: {e}")
            continue
        rel = str(f.relative_to(project_path)).replace("\\", "/")
        for method, path, router_var in routes:
            found.setdefault((method, path), []).append((rel, router_var))

    return found, errors


def _find_router_definition(project_path: Path, router_var: str) -> Optional[str]:
    """Which app/routes/*.py module actually defines `router_var =
    APIRouter(...)`. Returns the dotted import path (e.g.
    'app.routes.auth_routes') or None if not found."""
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return None
    for f in sorted(routes_dir.glob("*.py")):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(rf"^\s*{re.escape(router_var)}\s*=\s*APIRouter\(", content, re.MULTILINE):
            return f"app.routes.{f.stem}"
    return None


def _check_main_wiring(project_path: Path, router_module: str, router_var: str) -> tuple[bool, bool, int, int]:
    """Returns (has_import, has_include, import_count, include_count)."""
    main_py = project_path / "app" / "main.py"
    if not main_py.exists():
        return False, False, 0, 0
    try:
        content = main_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, False, 0, 0

    import_pat = re.compile(rf"from {re.escape(router_module)} import\s+.*\b{re.escape(router_var)}\b")
    include_pat = re.compile(rf"app\.include_router\(\s*{re.escape(router_var)}\b")

    import_count = len(import_pat.findall(content))
    include_count = len(include_pat.findall(content))
    return import_count > 0, include_count > 0, import_count, include_count


def check_auth_completeness(project_path: str) -> AuthCompletenessResult:
    """
    Part 2/3: the deterministic completeness check. Pure read-only --
    makes no changes. Verifies (in order): required endpoints exist
    somewhere in the route files, the router that defines them is
    actually importable, and main.py both imports and includes it.
    """
    root = Path(project_path)
    result = AuthCompletenessResult(complete=False)

    if not (root / "app").exists():
        result.reason = "no app/ directory found"
        return result

    found, parse_errors = _scan_project_routes(root)
    result.parse_errors = parse_errors
    result.found_endpoints = set(found.keys())

    for method, path in REQUIRED_AUTH_ENDPOINTS:
        if (method, path) not in found:
            result.missing_required.append(f"{method} {path}")
    for method, path in RECOMMENDED_AUTH_ENDPOINTS:
        if (method, path) not in found:
            result.missing_recommended.append(f"{method} {path}")

    for key, sites in found.items():
        if len(sites) > 1:
            result.duplicate_registrations.append(
                f"{key[0]} {key[1]} registered in {len(sites)} places: "
                + ", ".join(f"{f}:{rv}" for f, rv in sites)
            )

    if result.missing_required:
        result.reason = "missing required endpoint(s): " + ", ".join(result.missing_required)
        result.complete = False
        return result

    # All required endpoints exist somewhere -- now confirm the router
    # that defines POST /auth/register (the anchor endpoint) is actually
    # wired into main.py, not just present as dead code in an unincluded file.
    register_sites = found.get(("POST", "/auth/register"), [])
    if not register_sites:
        result.reason = "internal inconsistency: register site vanished after presence check"
        return result

    # If multiple files define it, check wiring for EVERY one -- as long
    # as at least one is actually reachable (imported + included), the
    # endpoint works; per _patch_forward_role_to_duplicate_registrars's
    # own confirmed-live finding, a second unwired copy is not itself
    # fatal (docs/AUTH_COMPLETENESS.md discusses this tradeoff).
    any_wired = False
    for rel_file, router_var in register_sites:
        module = rel_file[:-3].replace("/", ".") if rel_file.endswith(".py") else None
        if module is None:
            continue
        has_import, has_include, _, _ = _check_main_wiring(root, module, router_var)
        if has_import and has_include:
            any_wired = True
            result.router_import_present = True
            result.router_include_present = True
            result.router_var = router_var
            result.router_module = module
            break

    if not any_wired:
        # Report the first candidate's wiring state for diagnostics even
        # though none are fully wired.
        rel_file, router_var = register_sites[0]
        module = rel_file[:-3].replace("/", ".")
        has_import, has_include, _, _ = _check_main_wiring(root, module, router_var)
        result.router_import_present = has_import
        result.router_include_present = has_include
        result.router_var = router_var
        result.router_module = module
        missing = []
        if not has_import:
            missing.append("import")
        if not has_include:
            missing.append("include_router")
        result.reason = f"auth router defined but not wired into main.py (missing: {', '.join(missing)})"
        result.complete = False
        return result

    result.complete = True
    result.reason = "complete"
    return result


def _log_auth_completeness_result(project_name: str, status: str, result_before: AuthCompletenessResult,
                                   result_after: Optional[AuthCompletenessResult] = None) -> None:
    """Append-only JSONL telemetry, matching this project's own
    generation_log.jsonl convention -- one record per
    ensure_auth_completeness() call. Never raises (telemetry failures
    must not break generation, same convention as every other
    prevention-tracking write in this codebase)."""
    try:
        _TELEMETRY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project_name": project_name,
            "status": status,  # "complete" | "repaired" | "failed"
            "missing_required_before": result_before.missing_required,
            "reason_before": result_before.reason,
        }
        if result_after is not None:
            record["missing_required_after"] = result_after.missing_required
            record["reason_after"] = result_after.reason
        with open(_TELEMETRY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def ensure_auth_completeness(project_path: str, project_name: str = "") -> dict:
    """
    Part 4: check, and if incomplete, deterministically repair.

    Never calls an LLM. Repair reuses the EXISTING, already-tested
    template injection (_patch_auth_routes / _patch_auth_utils from
    deterministic_patcher.py) rather than reimplementing it -- this
    function's value is running that repair unconditionally at a point
    where the normal skip_protected_injections=True flag would
    otherwise have suppressed it, not replacing the template itself.

    Returns {"status": "complete"|"repaired"|"failed", "before": ...,
    "after": ...}.
    """
    project_name = project_name or Path(project_path).name
    before = check_auth_completeness(project_path)

    if before.complete:
        _log_auth_completeness_result(project_name, "complete", before)
        return {"status": "complete", "before": before, "after": before}

    # Deterministic repair: reuse the existing injection machinery.
    # Import locally to avoid a module-load-time circular import
    # (deterministic_patcher.py does not import this module).
    from app.services.deterministic_patcher import _patch_auth_routes, _patch_auth_utils, _patch_auth_requirements

    root = Path(project_path)
    try:
        _patch_auth_utils(root)
        _patch_auth_requirements(root)
        _patch_auth_routes(root)
    except Exception as e:
        after = check_auth_completeness(project_path)
        after.reason = f"repair raised {type(e).__name__}: {e}"
        _log_auth_completeness_result(project_name, "failed", before, after)
        return {"status": "failed", "before": before, "after": after}

    after = check_auth_completeness(project_path)
    if after.complete:
        print(f"  [auth-completeness] repaired: {', '.join(before.missing_required) or before.reason}")
        _log_auth_completeness_result(project_name, "repaired", before, after)
        return {"status": "repaired", "before": before, "after": after}

    # _patch_auth_routes()'s own has_user_model gate (only checks for
    # app/models/user.py or users.py) can decline to inject even when
    # this broader check found endpoints genuinely missing -- reported
    # honestly as "failed" rather than silently treated as success, and
    # NOT escalated to an LLM call (out of this experiment's scope; the
    # rules require deterministic-only repair this cycle).
    print(f"  [auth-completeness] deterministic repair could not restore completeness: {after.reason}")
    _log_auth_completeness_result(project_name, "failed", before, after)
    return {"status": "failed", "before": before, "after": after}
