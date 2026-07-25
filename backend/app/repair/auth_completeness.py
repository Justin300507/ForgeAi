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
from typing import Any, Mapping, Optional

REQUIRED_AUTH_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("POST", "/auth/signup"),
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

_AUTH_MODEL_STEMS = frozenset({"user", "users", "account", "accounts"})
_AUTH_CREDENTIAL_FIELDS = frozenset({
    "password", "password_hash", "hashed_password", "password_digest",
    "access_token", "refresh_token", "token",
})


def _has_auth_credential(fields: Any) -> bool:
    """Whether declared model fields are concrete authentication evidence."""
    if not isinstance(fields, list):
        return False
    for field in fields:
        name = field.get("name", "") if isinstance(field, Mapping) else field
        if str(name).strip().lower() in _AUTH_CREDENTIAL_FIELDS:
            return True
    return False


def _architecture_signals_auth(architecture: Any) -> bool:
    """Return whether a generation plan explicitly includes auth intent.

    This deliberately accepts only concrete architecture evidence: an API
    endpoint under the ``/auth`` path segment, or a User/Account entity that
    declares credential fields. Product-copy words and a generic business
    User record are not enough to turn an auth-free application into an
    authenticated one.
    """
    if not isinstance(architecture, Mapping):
        return False

    endpoints = architecture.get("api_endpoints", [])
    if isinstance(endpoints, list):
        for endpoint in endpoints:
            if isinstance(endpoint, Mapping) and _is_auth_path(str(endpoint.get("path", ""))):
                return True

    for key in ("database_entities", "db_entities", "models", "entities", "data_entities_detail"):
        entities = architecture.get(key, [])
        if not isinstance(entities, list):
            continue
        for entity in entities:
            name = entity.get("name", "") if isinstance(entity, Mapping) else entity
            if (
                isinstance(entity, Mapping)
                and str(name).strip().lower() in _AUTH_MODEL_STEMS
                and _has_auth_credential(entity.get("fields", []))
            ):
                return True
    return False


def _is_auth_path(path: str) -> bool:
    """Match the /auth path segment, not unrelated paths such as /author."""
    normalized = _normalize_path(path).lower()
    return normalized == "/auth" or normalized.endswith("/auth") or "/auth/" in normalized


def _model_declares_auth_credential(model_file: Path) -> bool:
    """Inspect assigned field names without treating comments as evidence."""
    try:
        tree = ast.parse(model_file.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id.lower() in _AUTH_CREDENTIAL_FIELDS for target in targets):
            return True
    return False


def project_signals_auth(project_path: str | Path, architecture: Any = None) -> bool:
    """Return whether generated files or their architecture require auth.

    A generated ``User``/``Account`` model is a signal only when it declares
    credentials. This preserves legitimate business-user records in auth-free
    applications while still recovering when an LLM repair removes every auth
    route.
    """
    if _architecture_signals_auth(architecture):
        return True

    root = Path(project_path)
    for models_dir in (root / "app" / "models", root / "models"):
        if not models_dir.is_dir():
            continue
        for stem in _AUTH_MODEL_STEMS:
            model_file = models_dir / f"{stem}.py"
            if model_file.is_file() and _model_declares_auth_credential(model_file):
                return True

    # Use the same route inventory as check_auth_completeness. In particular,
    # do not infer auth from nested or otherwise unrecognized layouts that the
    # completeness verifier cannot subsequently validate.
    found, _errors = _scan_project_routes(root)
    if any(_is_auth_path(path) for _method, path in found):
        return True
    return False


@dataclass
class AuthCompletenessResult:
    complete: bool
    found_endpoints: set = field(default_factory=set)
    missing_required: list = field(default_factory=list)
    missing_recommended: list = field(default_factory=list)
    stub_required: list = field(default_factory=list)
    router_import_present: bool = False
    router_include_present: bool = False
    router_var: Optional[str] = None
    router_module: Optional[str] = None
    duplicate_registrations: list = field(default_factory=list)
    parse_errors: list = field(default_factory=list)
    field_mismatches: list = field(default_factory=list)
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


def _is_stub_function_body(node) -> bool:
    """True if `node`'s body has no real logic -- just `pass`, `...`
    (Ellipsis), a bare docstring/comment-like string expression, or
    `raise NotImplementedError(...)`, in any combination.

    Reproduced live (habit_tracker, 2026-07-25): Architecture Repair
    regenerated auth_routes.py to add an unrelated PUT /auth/update
    endpoint, and in the same response rewrote POST /auth/signup and
    POST /auth/login down to bare `pass` bodies -- structurally still
    "the right decorator at the right path", so the existing
    presence/wiring checks below all passed, while the endpoints
    themselves would 500 or silently return None for every real caller.
    A stub is functionally identical to the endpoint not existing at all
    from a user's perspective; this makes it count as such here too.
    """
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)             and isinstance(body[0].value.value, str):
        body = body[1:]  # leading docstring is never "real logic" either way
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            if stmt.value.value is Ellipsis or isinstance(stmt.value.value, str):
                continue  # bare `...` or a comment-like string expression
        if (
            isinstance(stmt, ast.Raise)
            and isinstance(stmt.exc, ast.Call)
            and isinstance(stmt.exc.func, ast.Name)
            and stmt.exc.func.id == "NotImplementedError"
        ):
            continue
        return False
    return True


def _find_stub_required_endpoints(project_path: Path, found: dict) -> list[str]:
    """For each REQUIRED_AUTH_ENDPOINTS entry already confirmed present in
    `found`, check whether every site that registers it is a stub body.
    A required endpoint registered in multiple files (duplicate
    registration, already tracked separately) only counts as a stub here
    if ALL of its sites are stubs -- a real implementation anywhere is
    sufficient, mirroring the existing "any_wired" tolerance for wiring."""
    stubs: list[str] = []
    for method, path in REQUIRED_AUTH_ENDPOINTS:
        sites = found.get((method, path), [])
        if not sites:
            continue  # already reported as missing_required
        any_real_impl = False
        for rel_file, router_var in sites:
            full_path = project_path / rel_file
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(content)
            except Exception:
                any_real_impl = True  # can't confirm it's a stub -- don't false-flag
                continue
            prefixes = _router_prefixes(tree)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    dec_func = dec.func
                    if not isinstance(dec_func, ast.Attribute) or dec_func.attr.upper() != method:
                        continue
                    if not (isinstance(dec_func.value, ast.Name) and dec_func.value.id == router_var):
                        continue
                    if not dec.args or not isinstance(dec.args[0], ast.Constant)                             or not isinstance(dec.args[0].value, str):
                        continue
                    raw_path = dec.args[0].value
                    prefix = prefixes.get(router_var, "")
                    if _normalize_path(prefix + raw_path) != path:
                        continue
                    if not _is_stub_function_body(node):
                        any_real_impl = True
        if not any_real_impl:
            stubs.append(f"{method} {path}")
    return stubs


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

    result.stub_required = _find_stub_required_endpoints(root, found)
    if result.stub_required:
        result.reason = "required endpoint(s) present but stubbed (no real implementation): " + ", ".join(result.stub_required)
        result.complete = False
        return result

    # All required endpoints exist somewhere -- now confirm the router
    # that defines POST /auth/signup (the anchor endpoint) is actually
    # wired into main.py, not just present as dead code in an unincluded file.
    signup_sites = found.get(("POST", "/auth/signup"), [])
    if not signup_sites:
        result.reason = "internal inconsistency: signup site vanished after presence check"
        return result

    # If multiple files define it, check wiring for EVERY one -- as long
    # as at least one is actually reachable (imported + included), the
    # endpoint works; per _patch_forward_role_to_duplicate_registrars's
    # own confirmed-live finding, a second unwired copy is not itself
    # fatal (docs/AUTH_COMPLETENESS.md discusses this tradeoff).
    any_wired = False
    for rel_file, router_var in signup_sites:
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
        rel_file, router_var = signup_sites[0]
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

    # Exp085: endpoints exist and are wired, but that alone doesn't mean
    # the handler works -- Exp084 traced a confirmed-live failure class
    # where Architecture Repair regenerates auth_routes.py referencing a
    # request schema field that doesn't actually exist (e.g.
    # `req.username` when SignupRequest only declares email/password/
    # display_name). Reuses Exp064's existing field-consistency AST
    # machinery (fix_writer_service.py), extended with cross-file
    # resolution for exactly this reason -- ordinary architecture-authored
    # routes import their request schemas from app/schemas/*.py rather
    # than defining them inline, which is the shape Exp064's own
    # same-file-only scope couldn't see. Scoped to the specific files that
    # actually define an auth endpoint (not every .py file in the
    # project) -- this is an auth-completeness check, not a general
    # write-time gate.
    from app.services.fix_writer_service import _check_request_field_consistency

    auth_files = {
        rel_file
        for method, path in (*REQUIRED_AUTH_ENDPOINTS, *RECOMMENDED_AUTH_ENDPOINTS)
        for rel_file, _router_var in found.get((method, path), [])
    }
    for rel_file in sorted(auth_files):
        full_path = root / rel_file
        try:
            file_content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ok, mismatch_reason = _check_request_field_consistency(
            rel_file, file_content, project_path=str(root)
        )
        if not ok:
            result.field_mismatches.append(mismatch_reason)

    if result.field_mismatches:
        result.reason = "request-field mismatch: " + "; ".join(result.field_mismatches)
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


def ensure_auth_completeness_if_signaled(
    project_path: str | Path,
    project_name: str = "",
    architecture: Any = None,
) -> dict:
    """Run deterministic auth convergence only for apps that require auth.

    V15 invokes this after its normal deterministic/preflight convergence and
    after an LLM repair convergence.  Returning an explicit ``skipped``
    status makes the gate observable without logging a misleading auth-repair
    event for applications that never asked for authentication.
    """
    if not project_signals_auth(project_path, architecture):
        return {"status": "skipped", "reason": "auth_not_signaled"}
    return ensure_auth_completeness(str(project_path), project_name)
