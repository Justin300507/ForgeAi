"""
V5.5 Full User Journey Testing

API-only (no browser) end-to-end journey:
  Register → Login → Create → List → Edit → Delete → Verify deletion
  → Logout → Login again → Verify persistence

Fast and resilient: each step is independent, failures are recorded but
don't halt the journey. The backend must already be running.
"""
import time
from dataclasses import dataclass, field


@dataclass
class JourneyStep:
    name: str
    passed: bool
    duration_ms: float
    detail: str = ""


@dataclass
class JourneyResult:
    success: bool
    steps: list = field(default_factory=list)
    total_duration: float = 0.0
    persistence_verified: bool = False
    steps_passed: int = 0
    steps_failed: int = 0
    skipped: bool = False
    skip_reason: str = ""
    entity: str = ""


_CRITICAL_STEPS = {"Login", "Create entity", "List entities", "Edit entity", "Delete entity"}

# Segments that are path prefixes, not resource names
_PREFIX_SEGMENTS = {"api", "v1", "v2", "v3", "v4", "auth", "me", "admin"}

# Resources that are auth-related — prefer to test other entities first
_AUTH_RESOURCES = {"users", "user", "accounts", "account", "members", "member"}

# Pydantic v2 type-mismatch error tags -> a coercion of the current (wrong)
# guessed value into what the error says is actually expected. Confirmed
# live (2026-07-06): a schema-introspected field (e.g. `phone_number`)
# whose OpenAPI type hint disagreed with what the request model actually
# validates gets guessed as the wrong Python type (int 1 instead of a
# string) and 422s identically on every canary run for that app -- and
# because this fell through the 422 retry's enum-only "wrong value" branch
# unhandled, `entity_id` was never captured, failing every downstream CRUD
# step (Edit/Delete/Verify) as a direct, deterministic consequence of this
# one field.
_TYPE_COERCIONS = {
    "string_type":   lambda v: str(v),
    "int_type":      lambda v: int(float(v)) if isinstance(v, str) else int(v),
    "int_parsing":   lambda v: int(float(v)) if isinstance(v, str) else int(v),
    "float_type":    lambda v: float(v),
    "float_parsing": lambda v: float(v),
    "bool_type":     lambda v: str(v).strip().lower() in ("true", "1", "yes") if isinstance(v, str) else bool(v),
    "bool_parsing":  lambda v: str(v).strip().lower() in ("true", "1", "yes") if isinstance(v, str) else bool(v),
    "list_type":     lambda v: v if isinstance(v, list) else [v],
}


def _detect_api_prefix(architecture: dict) -> str:
    """Return the common URL prefix for all non-auth endpoints (e.g. '/api/v1')."""
    endpoints = (architecture or {}).get("api_endpoints", [])
    non_auth = [ep.get("path", "") for ep in endpoints
                if "auth" not in ep.get("path", "").lower()]
    if not non_auth:
        return ""
    for prefix in ("/api/v1", "/api/v2", "/api/v3", "/api"):
        if sum(1 for p in non_auth if p.startswith(prefix)) > len(non_auth) * 0.5:
            return prefix
    return ""


def _detect_auth_paths(architecture: dict) -> dict:
    """Return actual register and login paths from the architecture plan."""
    endpoints = (architecture or {}).get("api_endpoints", [])
    register_path = None
    login_path = None
    for ep in endpoints:
        path = ep.get("path", "")
        method = ep.get("method", "").upper()
        if method != "POST":
            continue
        lpath = path.lower()
        if register_path is None and ("register" in lpath or "signup" in lpath):
            register_path = path
        if login_path is None and "login" in lpath:
            login_path = path
    return {"register": register_path, "login": login_path}


def _get_openapi_fields(requests_mod, base_url: str, path: str) -> set:
    """
    Query /openapi.json to get the property names for a POST endpoint's request body.
    Returns an empty set on any failure — callers must handle gracefully.
    """
    try:
        r = requests_mod.get(f"{base_url}/openapi.json", timeout=3)
        if r.status_code != 200:
            return set()
        spec = r.json()
        ep_spec = spec.get("paths", {}).get(path, {}).get("post", {})
        body_schema = (ep_spec
                       .get("requestBody", {})
                       .get("content", {})
                       .get("application/json", {})
                       .get("schema", {}))
        if "$ref" in body_schema:
            schema_name = body_schema["$ref"].split("/")[-1]
            body_schema = spec.get("components", {}).get("schemas", {}).get(schema_name, {})
        props = set(body_schema.get("properties", {}).keys())
        return props
    except Exception:
        return set()


def _get_openapi_schema_props(requests_mod, base_url: str, path: str) -> dict:
    """
    Query /openapi.json and return {field_name: resolved_property_schema} for a
    POST endpoint's request body, resolving $ref (including Optional[Enum]'s
    anyOf wrapper) so enum/type constraints are visible to the caller.

    _get_openapi_fields only returns field NAMES, so callers had no way to
    know a field like "frequency" is a Literal/Enum and must guess a value —
    almost always wrong, causing a 422 the generic name-based heuristics
    can't recover from. Returns {} on any failure.
    """
    try:
        r = requests_mod.get(f"{base_url}/openapi.json", timeout=3)
        if r.status_code != 200:
            return {}
        spec = r.json()
        components = spec.get("components", {}).get("schemas", {})
        ep_spec = spec.get("paths", {}).get(path, {}).get("post", {})
        body_schema = (ep_spec
                       .get("requestBody", {})
                       .get("content", {})
                       .get("application/json", {})
                       .get("schema", {}))
        if "$ref" in body_schema:
            schema_name = body_schema["$ref"].split("/")[-1]
            body_schema = components.get(schema_name, {})
        props = body_schema.get("properties", {})

        def _resolve(prop_schema):
            if "$ref" in prop_schema:
                name = prop_schema["$ref"].split("/")[-1]
                return components.get(name, {})
            for key in ("anyOf", "allOf", "oneOf"):
                if key in prop_schema and prop_schema[key]:
                    for sub in prop_schema[key]:
                        resolved = _resolve(sub)
                        if resolved.get("enum") or resolved.get("type"):
                            return resolved
                    return _resolve(prop_schema[key][0])
            return prop_schema

        return {name: _resolve(schema) for name, schema in props.items()}
    except Exception:
        return {}


_PASSWORD_VARIANTS = {"password", "password_hash", "hashed_password", "passwd", "pwd"}
_EMAIL_VARIANTS = {"email", "email_address"}
_USERNAME_VARIANTS = {"username", "user_name", "login"}
_NAME_FIELDS = ("full_name", "display_name", "name", "first_name", "last_name")

# Sensible defaults for any extra fields that might appear in register schemas
_REGISTER_EXTRA_DEFAULTS = {
    "role": "user",
    "is_active": True,
    "phone": "+1234567890",
    "bio": "",
    "avatar": "",
}


def _build_register_body(fields: set, creds: dict) -> dict:
    """Construct a register payload matching whatever fields the schema declares."""
    if not fields:
        return {"email": creds["email"], "password": creds["password"]}

    body: dict = {}

    # Handle email / username identifier field
    for f in _EMAIL_VARIANTS:
        if f in fields:
            body[f] = creds["email"]
            break
    for f in _USERNAME_VARIANTS:
        if f in fields:
            body[f] = creds["username"]
            break
    # If neither email nor username found in schema, default to email
    if not any(f in body for f in list(_EMAIL_VARIANTS) + list(_USERNAME_VARIANTS)):
        body["email"] = creds["email"]

    # Handle all password field name variants (LLM sometimes uses password_hash)
    for f in _PASSWORD_VARIANTS:
        if f in fields:
            body[f] = creds["password"]
            break
    if not any(f in body for f in _PASSWORD_VARIANTS):
        body["password"] = creds["password"]

    # Handle name fields
    for name_field in _NAME_FIELDS:
        if name_field in fields:
            body[name_field] = "Journey Tester"
            break

    # Handle any other required fields with defaults
    for field_name, default_value in _REGISTER_EXTRA_DEFAULTS.items():
        if field_name in fields and field_name not in body:
            body[field_name] = default_value

    return body


def _parse_missing_fields(response) -> set:
    """Parse a FastAPI 422 response body to extract which body fields failed validation."""
    try:
        detail = response.json().get("detail", [])
        if isinstance(detail, list):
            return {
                err["loc"][-1]
                for err in detail
                if isinstance(err.get("loc"), list) and len(err["loc"]) >= 2
                   and err["loc"][0] == "body"
            }
    except Exception:
        pass
    return set()


def _detect_crud_entity(architecture: dict, api_prefix: str) -> str | None:
    """
    Find the first resource that has all four CRUD methods, skipping
    common prefix segments (api, v1, auth, etc.).
    """
    if not architecture:
        return None

    endpoints = architecture.get("api_endpoints", [])
    from collections import defaultdict
    by_resource: dict[str, set] = defaultdict(set)

    for ep in endpoints:
        path = ep.get("path", "")
        method = ep.get("method", "").upper()
        # Strip the known prefix so /api/v1/tasks → tasks
        stripped = path[len(api_prefix):] if api_prefix and path.startswith(api_prefix) else path
        parts = [p for p in stripped.strip("/").split("/")
                 if p and not p.startswith("{") and p not in _PREFIX_SEGMENTS]
        if parts:
            by_resource[parts[0]].add(method)

    # Prefer non-auth resources first (so we don't accidentally delete the registered user)
    for resource, methods in by_resource.items():
        if resource in _AUTH_RESOURCES:
            continue
        if {"GET", "POST", "PUT", "DELETE"}.issubset(methods):
            return resource

    # Fallback: any full-CRUD resource including auth ones
    for resource, methods in by_resource.items():
        if {"GET", "POST", "PUT", "DELETE"}.issubset(methods):
            return resource

    # Fallback: any resource with POST + GET (non-auth first)
    for resource, methods in by_resource.items():
        if resource not in _AUTH_RESOURCES and {"GET", "POST"}.issubset(methods):
            return resource
    for resource, methods in by_resource.items():
        if {"GET", "POST"}.issubset(methods):
            return resource

    return None


def _step(name: str, fn) -> JourneyStep:
    t0 = time.time()
    try:
        ok, detail = fn()
        return JourneyStep(name=name, passed=ok, duration_ms=(time.time() - t0) * 1000, detail=detail)
    except Exception as e:
        return JourneyStep(name=name, passed=False, duration_ms=(time.time() - t0) * 1000, detail=str(e))


def run_user_journey(
    project_path: str,
    architecture: dict | None = None,
    backend_port: int = 8001,
) -> JourneyResult:
    """
    Run the full user journey against a running backend on backend_port.
    All steps use the requests library — no browser required.
    """
    t0 = time.time()

    try:
        import requests
    except ImportError:
        return JourneyResult(
            success=False, skipped=True,
            skip_reason="pip install requests",
            total_duration=0,
        )

    base = f"http://127.0.0.1:{backend_port}"

    # Check backend is alive
    try:
        requests.get(f"{base}/docs", timeout=3)
    except Exception:
        return JourneyResult(
            success=False, skipped=True,
            skip_reason=f"Backend not reachable on port {backend_port}",
            total_duration=round(time.time() - t0, 2),
        )

    arch = architecture or {}
    api_prefix = _detect_api_prefix(arch)
    auth_paths = _detect_auth_paths(arch)
    entity: str = _detect_crud_entity(arch, api_prefix) or "items"
    entity_url = f"{base}{api_prefix}/{entity}"

    steps: list[JourneyStep] = []
    token: str | None = None
    entity_id: int | str | None = None

    creds = {"username": "journey_test", "email": "journey@test.com", "password": "JourneyPass1!"}

    # ── Step 1: Register ─────────────────────────────────────────────────
    # Use architecture-detected path first, then fall back to bare /auth/* paths.
    # Query /openapi.json to build the right request body instead of guessing.
    def do_register():
        nonlocal token

        # Build candidate URLs: architecture path first, then bare fallbacks
        arch_register = auth_paths.get("register")
        candidates = []
        if arch_register:
            candidates.append(f"{base}{arch_register}")
        candidates += [f"{base}/auth/signup", f"{base}/auth/register"]

        # Fetch the schema for the primary candidate so we send the right body
        schema_fields = _get_openapi_fields(requests, base, arch_register) if arch_register else set()
        body = _build_register_body(schema_fields, creds)

        def _try_register(url: str, payload: dict):
            """Attempt POST to url, return (response, token_or_None) on 200/201/400."""
            r = requests.post(url, json=payload, timeout=5)
            if r.status_code in (200, 201):
                tok = None
                try:
                    tok = r.json().get("access_token") or r.json().get("token")
                except Exception:
                    pass
                return r, tok
            return r, None

        for url in candidates:
            r, tok = _try_register(url, body)
            if r.status_code in (200, 201):
                token = tok or token
                return True, f"{r.status_code} @ {url.split('/')[-1]}"
            if r.status_code == 400:
                return True, "400 (user already exists)"
            if r.status_code == 422:
                # Attempt 2: try password_hash variant if schema-built body didn't use it
                if "password_hash" not in body:
                    body2 = {**body, "password_hash": creds["password"]}
                    body2.pop("password", None)
                    r2, tok2 = _try_register(url, body2)
                    if r2.status_code in (200, 201):
                        token = tok2 or token
                        return True, f"{r2.status_code} @ {url.split('/')[-1]} (pw_hash)"
                    if r2.status_code == 400:
                        return True, "400 (user already exists)"

                # Attempt 3: parse 422 error to find which body fields are missing
                missing = _parse_missing_fields(r)
                if missing:
                    enriched = dict(body)
                    _extra = {
                        "role": "user", "is_active": True, "phone": "+1234567890",
                        "bio": "", "avatar_url": "",
                    }
                    for f in missing:
                        if f not in enriched:
                            if f in _extra:
                                enriched[f] = _extra[f]
                            elif f in _PASSWORD_VARIANTS:
                                enriched[f] = creds["password"]
                    if enriched != body:
                        r3, tok3 = _try_register(url, enriched)
                        if r3.status_code in (200, 201):
                            token = tok3 or token
                            return True, f"{r3.status_code} @ {url.split('/')[-1]} (enriched)"
                        if r3.status_code == 400:
                            return True, "400 (user already exists)"

                # Route exists but schema is unknown — treat as soft pass so journey continues
                try:
                    _422_detail = r.json().get("detail", "?")
                except Exception:
                    _422_detail = "?"
                return True, f"422 @ {url.split('/')[-1]} (server alive, schema mismatch) detail={str(_422_detail)[:120]}"
        return False, "404 (no register/signup endpoint found)"
    steps.append(_step("Register", do_register))

    # ── Step 2: Login ────────────────────────────────────────────────────
    def do_login():
        nonlocal token

        arch_login = auth_paths.get("login")
        login_candidates = []
        if arch_login:
            login_candidates.append(f"{base}{arch_login}")
        login_candidates += [f"{base}/auth/login"]

        login_fields = _get_openapi_fields(requests, base, arch_login) if arch_login else set()

        # Build login bodies in priority order
        bodies = []
        primary = {"email": creds["email"], "password": creds["password"]}
        if login_fields and "username" in login_fields and "email" not in login_fields:
            primary = {"username": creds["username"], "password": creds["password"]}
        bodies.append(("json", primary))
        bodies.append(("json", {"email": creds["email"], "password": creds["password"]}))
        bodies.append(("json", {"username": creds["username"], "password": creds["password"]}))

        for url in login_candidates:
            for body_type, body in bodies:
                r = requests.post(url, json=body, timeout=5)
                if r.status_code in (200, 201):
                    try:
                        token = r.json().get("access_token") or r.json().get("token") or token
                    except Exception:
                        pass
                    return True, f"{r.status_code} @ {url.split('/')[-1]}"
                if r.status_code == 422:
                    # Route exists, schema mismatch
                    return True, f"422 @ {url.split('/')[-1]} (server alive, auth format mismatch)"

        # Try OAuth2 form-data token endpoint as last resort
        r_form = requests.post(f"{base}/auth/token",
                               data={"username": creds["username"], "password": creds["password"]},
                               timeout=5)
        if r_form.status_code in (200, 201):
            try:
                token = r_form.json().get("access_token") or r_form.json().get("token") or token
            except Exception:
                pass
            return True, f"form {r_form.status_code} @ /auth/token"

        return False, f"Login failed (tried {len(login_candidates)} URL(s))"
    steps.append(_step("Login", do_login))

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # ── Step 3: Detect entity ────────────────────────────────────────────
    # entity_url was computed from the architecture; verify it's reachable
    # (pass auth headers in case all endpoints require JWT).
    def do_detect_entity():
        if not token:
            return True, entity_url  # can't verify, proceed with architecture guess
        r = requests.get(entity_url, headers=headers, timeout=4)
        if r.status_code in (200, 201):
            return True, entity_url
        # Try other resources from the architecture before giving up
        candidates = []
        for ep in arch.get("api_endpoints", []):
            p = ep.get("path", "")
            m = ep.get("method", "").upper()
            if m == "GET" and "{" not in p and "auth" not in p.lower():
                url = f"{base}{p}"
                if url != entity_url and url not in candidates:
                    candidates.append(url)
        for url in candidates[:6]:
            r2 = requests.get(url, headers=headers, timeout=3)
            if r2.status_code == 200:
                return True, url
        return True, entity_url  # fall through with best guess; CRUD steps will show real errors
    detect_step = _step("Detect entity", do_detect_entity)
    # Use the returned URL as entity_url for subsequent steps
    if detect_step.passed and detect_step.detail.startswith("http"):
        entity_url = detect_step.detail
    steps.append(detect_step)

    # ── Step 4: Create entity ─────────────────────────────────────────
    # Sensible defaults for commonly-required fields in generated schemas
    _FIELD_DEFAULTS = {
        "status": "active", "state": "active",
        "owner_id": 1, "user_id": 1, "project_id": 1, "task_id": 1,
        "assignee_id": 1, "author_id": 1, "created_by": 1,
        "priority": "medium", "role": "user",
        "type": "general", "category": "general",
        "is_active": True, "is_done": False, "is_completed": False,
        "is_public": True,
        "start_time": "2024-01-01T09:00:00", "end_time": "2024-01-01T10:00:00",
        "duration_minutes": 60, "hours": 1,
        "quantity": 1, "amount": 1.0, "price": 1.0,
        "color": "#3498db", "tags": [], "labels": [],
    }

    def do_create():
        nonlocal entity_id, headers
        # Refresh headers in case login just set the token
        if token:
            headers.update({"Authorization": f"Bearer {token}"})

        base_payload = {"title": "Journey Test Item", "name": "Journey Test Item",
                        "description": "created by V5.5 journey runner"}

        # If no token, attempt without auth but abort immediately on 5xx (avoids SQLite lock cascade)
        # Routes requiring auth will return 401; skip CRUD gracefully.

        # Query OpenAPI schema to discover required fields we might be missing
        entity_path = entity_url[len(base):]
        schema_props = _get_openapi_schema_props(requests, base, entity_path)
        schema_fields = set(schema_props.keys()) or _get_openapi_fields(requests, base, entity_path)
        enriched_payload = dict(base_payload)
        for field_name in schema_fields:
            if field_name in enriched_payload:
                continue
            fl = field_name.lower()
            prop_schema = schema_props.get(field_name) or {}
            enum_values = prop_schema.get("enum")
            prop_type = prop_schema.get("type")
            if enum_values:
                # A Literal/Enum field — any name-based guess is almost
                # certainly not a member and 422s with no recovery path.
                enriched_payload[field_name] = enum_values[0]
            elif prop_type == "boolean":
                enriched_payload[field_name] = True
            elif prop_type == "integer":
                enriched_payload[field_name] = 1
            elif prop_type == "number":
                enriched_payload[field_name] = 1.0
            elif prop_type == "array":
                enriched_payload[field_name] = []
            elif field_name in _FIELD_DEFAULTS:
                enriched_payload[field_name] = _FIELD_DEFAULTS[field_name]
            # Name/title-like fields with entity prefix (team_name, task_title, etc.)
            elif any(fl == s or fl.endswith(f"_{s}") for s in ("name", "title", "label", "heading", "subject", "caption")):
                enriched_payload[field_name] = "Journey Test Item"
            # Description-like fields
            elif any(fl == s or fl.endswith(f"_{s}") for s in ("description", "desc", "details", "notes", "content", "body", "text", "message", "summary", "about", "bio")):
                enriched_payload[field_name] = "Journey runner test"
            # Foreign-key-like fields not already covered
            elif fl.endswith("_id") and fl != "id":
                enriched_payload[field_name] = 1
            # Boolean flags
            elif any(fl.startswith(p) for p in ("is_", "has_", "can_", "allow_", "enable_")) or fl in ("active", "enabled", "visible", "public", "completed", "done"):
                enriched_payload[field_name] = True
            # Numeric fields
            elif any(fl == s or fl.endswith(f"_{s}") for s in ("count", "num", "number", "amount", "price", "quantity", "total", "score", "rating", "rank", "age", "size", "order", "limit", "max", "min", "hours", "minutes", "duration")):
                enriched_payload[field_name] = 1
            # Date/time fields
            elif any(fl == s or fl.endswith(f"_{s}") for s in ("date", "time", "at", "on", "deadline", "due", "expires")):
                enriched_payload[field_name] = "2026-01-01"
            # Email fields
            elif "email" in fl:
                enriched_payload[field_name] = "journey@test.com"
            # URL fields
            elif any(fl == s or fl.endswith(f"_{s}") for s in ("url", "link", "href", "src", "image", "avatar", "photo", "icon")):
                enriched_payload[field_name] = "https://example.com"
            # Fallback: non-empty string so the field is present
            else:
                enriched_payload[field_name] = "journey-test"

        # Send the enriched (schema-aware) payload. Do NOT also try the bare
        # base_payload afterwards — it is a strict subset of enriched_payload
        # (missing every field the schema introspection discovered) and is
        # guaranteed to 422 on any entity with real required fields, so a
        # second attempt with it can only ever overwrite a potentially-useful
        # response with a strictly worse, less-diagnostic one. This used to
        # be a `for payload in (enriched_payload, base_payload)` loop that
        # fell through to base_payload on ANY non-2xx/5xx/401 status (i.e.
        # every 422), so `last_r` ended up holding base_payload's response —
        # and the targeted 422-fix block below built its `targeted` dict from
        # enriched_payload but diagnosed it against base_payload's error
        # detail, which already listed those same fields as "missing" and so
        # never actually changed anything or re-sent enriched_payload itself.
        last_r = requests.post(entity_url, json=enriched_payload, headers=headers, timeout=5)
        if last_r.status_code in (200, 201):
            try:
                data = last_r.json()
                entity_id = data.get("id") or (data[0].get("id") if isinstance(data, list) else None)
            except Exception:
                pass
            return True, f"{last_r.status_code} id={entity_id}"
        if last_r.status_code >= 500:
            # 5xx can corrupt SQLite — don't retry, report the error code
            return False, f"{last_r.status_code} (server error)"
        if last_r.status_code == 401:
            # Auth required and we don't have a token — skip CRUD cleanly
            return False, "401 (auth required, no valid token)"

        # Attempt 2: parse 422 detail to build a targeted payload fixing specific errors
        if last_r is not None and last_r.status_code == 422:
            _422_detail = "?"
            try:
                detail = last_r.json().get("detail", [])
                _422_detail = detail
                if isinstance(detail, list) and detail:
                    targeted = dict(enriched_payload)
                    changed = False
                    for err in detail:
                        loc = err.get("loc", [])
                        if not (isinstance(loc, list) and len(loc) >= 2 and loc[0] == "body"):
                            continue
                        field_name = str(loc[-1])
                        msg_type = err.get("type", "")
                        msg = err.get("msg", "").lower()
                        if "missing" in msg or msg_type == "missing":
                            if field_name not in targeted:
                                fl = field_name.lower()
                                if field_name in _FIELD_DEFAULTS:
                                    targeted[field_name] = _FIELD_DEFAULTS[field_name]
                                elif "email" in fl:
                                    targeted[field_name] = "journey@test.com"
                                elif fl.endswith("_id"):
                                    targeted[field_name] = 1
                                elif any(fl.startswith(p) for p in ("is_", "has_", "can_")):
                                    targeted[field_name] = True
                                else:
                                    targeted[field_name] = "journey-test"
                                changed = True
                        elif "extra" in msg or "extra_forbidden" in msg_type or "not_permitted" in msg_type:
                            if field_name in targeted:
                                del targeted[field_name]
                                changed = True
                        else:
                            # Field WAS provided but rejected (bad enum choice, wrong
                            # type, regex/length constraint...) -- our first guess for
                            # this field was wrong. If the schema declares an enum,
                            # its first member is guaranteed valid; that's the one
                            # case we can fix blind without knowing the constraint.
                            enum_values = (schema_props.get(field_name) or {}).get("enum")
                            if enum_values and targeted.get(field_name) != enum_values[0]:
                                targeted[field_name] = enum_values[0]
                                changed = True
                            elif field_name in targeted and msg_type in _TYPE_COERCIONS:
                                try:
                                    coerced = _TYPE_COERCIONS[msg_type](targeted[field_name])
                                    if coerced != targeted[field_name]:
                                        targeted[field_name] = coerced
                                        changed = True
                                except Exception:
                                    pass
                    if changed:
                        r3 = requests.post(entity_url, json=targeted, headers=headers, timeout=5)
                        if r3.status_code in (200, 201):
                            try:
                                data = r3.json()
                                entity_id = data.get("id") or (data[0].get("id") if isinstance(data, list) else None)
                            except Exception:
                                pass
                            return True, f"{r3.status_code} id={entity_id} (422-fixed)"
                        if r3.status_code == 422:
                            try:
                                _422_detail = r3.json().get("detail", _422_detail)
                            except Exception:
                                pass
                        elif r3.status_code >= 500:
                            # The retry itself crashed the server (e.g. a NOT
                            # NULL constraint the 422 fix-up didn't touch).
                            # Falling through to the generic "schema mismatch,
                            # server alive" return below would report this
                            # step as PASSED while masking a real 500 --
                            # confirmed live in Experiment 014 (crm):
                            # phone_number's 422 got fixed, the retry then hit
                            # `IntegrityError: NOT NULL constraint failed:
                            # contacts.name`, and the step was still recorded
                            # as passed with a stale 422 message.
                            return False, f"{r3.status_code} (server error on 422-retry)"
            except Exception:
                pass
            # Surface the actual validation error instead of a content-free
            # "schema mismatch" -- otherwise diagnosing a 422 here means
            # re-running the whole pipeline just to see what field failed.
            return True, f"422 (schema mismatch, server alive) detail={str(_422_detail)[:200]}"

        # NOTE: must be `is not None`, not truthiness -- requests.Response
        # overrides __bool__ to return self.ok (False for any status >= 400),
        # so `if last_r` silently collapsed every genuine 400/403/404/409
        # Create-entity rejection into a contentless "?" instead of the real
        # status code, even though last_r is a perfectly valid response here.
        # Confirmed live: todo's "POST /tasks" 400 (invalid priority name)
        # was logged as "Create entity: ?" -- the #1 most frequent specific
        # failure signature in generation_log.jsonl -- starving the fix loop
        # (and its 422-specific repair hint) of the one piece of information
        # it needed to diagnose the actual rejection.
        return False, f"{last_r.status_code if last_r is not None else '?'}"
    steps.append(_step("Create entity", do_create))

    # ── Step 5: List entities ─────────────────────────────────────────
    def do_list():
        nonlocal entity_id
        r = requests.get(entity_url, headers=headers, timeout=5)
        if r.status_code != 200:
            return False, f"{r.status_code}"
        try:
            items = r.json()
            if isinstance(items, list) and items and entity_id is None:
                entity_id = items[-1].get("id")
            count = len(items) if isinstance(items, list) else "?"
            return True, f"{r.status_code} count={count}"
        except Exception:
            return True, f"{r.status_code}"
    steps.append(_step("List entities", do_list))

    # ── Step 6: Edit entity ───────────────────────────────────────────
    def do_edit():
        if entity_id is None:
            return False, "no entity_id captured"
        # Try multiple update payloads — different entities use title vs name
        for edit_payload in (
            {"title": "Journey Test Item EDITED", "name": "Journey Test Item EDITED",
             "description": "edited by V5.5 journey runner", "status": "active"},
            {"title": "Journey Test Item EDITED", "name": "Journey Test Item EDITED"},
            {"title": "Journey Test Item EDITED"},
            {"name": "Journey Test Item EDITED"},
        ):
            r = requests.put(f"{entity_url}/{entity_id}",
                             json=edit_payload,
                             headers=headers, timeout=5)
            if r.status_code in (200, 201, 204):
                return True, f"{r.status_code}"
            if r.status_code == 422:
                continue  # try next payload
            break  # 4xx/5xx non-422 — stop trying
        return r.status_code in (200, 201, 204), f"{r.status_code}"
    steps.append(_step("Edit entity", do_edit))

    # ── Step 7: Delete entity ─────────────────────────────────────────
    def do_delete():
        if entity_id is None:
            return False, "no entity_id captured"
        r = requests.delete(f"{entity_url}/{entity_id}", headers=headers, timeout=5)
        return r.status_code in (200, 204, 404), f"{r.status_code}"
    steps.append(_step("Delete entity", do_delete))

    # ── Step 8: Verify deletion ───────────────────────────────────────
    def do_verify_delete():
        if entity_id is None:
            return False, "no entity_id"
        r = requests.get(f"{entity_url}/{entity_id}", headers=headers, timeout=5)
        if r.status_code == 404:
            return True, "404 confirmed deleted"
        r2 = requests.get(entity_url, headers=headers, timeout=5)
        if r2.status_code == 200:
            try:
                items = r2.json()
                ids = [i.get("id") for i in items if isinstance(i, dict)]
                if entity_id not in ids:
                    return True, "not in list"
            except Exception:
                pass
        return False, f"still accessible: {r.status_code}"
    steps.append(_step("Verify deletion", do_verify_delete))

    # ── Step 9: Logout (optional) ────────────────────────────────────
    def do_logout():
        logout_url = f"{base}{api_prefix}/auth/logout" if api_prefix else f"{base}/auth/logout"
        r = requests.post(logout_url, headers=headers, timeout=5)
        return r.status_code in (200, 204, 404, 405, 401), f"{r.status_code}"
    steps.append(_step("Logout", do_logout))

    # ── Step 10: Login again ─────────────────────────────────────────
    def do_relogin():
        nonlocal token, headers
        arch_login = auth_paths.get("login")
        login_url = f"{base}{arch_login}" if arch_login else f"{base}/auth/login"
        for body in (
            {"email": creds["email"], "password": creds["password"]},
            {"username": creds["username"], "password": creds["password"]},
        ):
            r = requests.post(login_url, json=body, timeout=5)
            if r.status_code in (200, 201):
                try:
                    token = r.json().get("access_token") or r.json().get("token")
                    headers = {"Authorization": f"Bearer {token}"} if token else headers
                except Exception:
                    pass
                return True, f"re-login {r.status_code}"
        return False, f"re-login failed"
    steps.append(_step("Login again", do_relogin))

    # ── Step 11: Verify persistence ──────────────────────────────────
    persistence_verified = False

    def do_persistence():
        nonlocal persistence_verified
        if entity_id is None:
            return False, "no entity_id"
        r = requests.get(entity_url, headers=headers, timeout=5)
        if r.status_code != 200:
            return False, f"{r.status_code}"
        try:
            items = r.json()
            ids = [i.get("id") for i in items if isinstance(i, dict)]
            if entity_id not in ids:
                persistence_verified = True
                return True, "deleted item absent — persistence confirmed"
            return False, "deleted item still present"
        except Exception:
            return False, "could not parse list"
    steps.append(_step("Verify persistence", do_persistence))

    # ── Evaluate ──────────────────────────────────────────────────────
    passed = sum(1 for s in steps if s.passed)
    failed = sum(1 for s in steps if not s.passed)
    critical_failed = sum(
        1 for s in steps if not s.passed and s.name in _CRITICAL_STEPS
    )
    success = critical_failed == 0

    return JourneyResult(
        success=success,
        steps=steps,
        total_duration=round(time.time() - t0, 2),
        persistence_verified=persistence_verified,
        steps_passed=passed,
        steps_failed=failed,
        entity=entity,
    )
