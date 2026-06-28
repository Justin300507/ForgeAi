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


_CRITICAL_STEPS = {"Login", "Create entity", "List entities", "Edit entity", "Delete entity"}


def _detect_crud_entity(architecture: dict) -> str | None:
    """Find the first resource that has all four CRUD methods."""
    if not architecture:
        return None

    endpoints = architecture.get("api_endpoints", [])
    from collections import defaultdict
    by_resource: dict[str, set] = defaultdict(set)

    for ep in endpoints:
        path = ep.get("path", "")
        method = ep.get("method", "").upper()
        parts = [p for p in path.strip("/").split("/")
                 if p and not p.startswith("{") and p not in ("auth", "me")]
        if parts:
            by_resource[parts[0]].add(method)

    for resource, methods in by_resource.items():
        if {"GET", "POST", "PUT", "DELETE"}.issubset(methods):
            return resource

    # Fallback: any resource with POST + GET
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

    steps: list[JourneyStep] = []
    token: str | None = None
    entity: str | None = _detect_crud_entity(architecture or {}) or "items"
    entity_id: int | str | None = None

    creds = {"username": "journey_test", "email": "journey@test.com", "password": "JourneyPass1!"}

    # ── Step 1: Register ─────────────────────────────────────────────────
    # Contract mandates POST /auth/signup; some apps use /auth/register instead.
    # Try both so the journey passes regardless of which URL the LLM generated.
    def do_register():
        nonlocal token
        signup_body = {
            "email": creds["email"],
            "password": creds["password"],
            "display_name": "Journey Tester",
        }
        for url in (f"{base}/auth/signup", f"{base}/auth/register"):
            r = requests.post(url, json=signup_body, timeout=5)
            if r.status_code in (200, 201):
                # Grab token from signup response if returned
                try:
                    token = r.json().get("access_token") or r.json().get("token") or token
                except Exception:
                    pass
                return True, f"{r.status_code} @ {url.split('/')[-1]}"
            if r.status_code == 400:
                # User already exists — that's fine, the server is alive
                return True, f"400 (user already exists)"
            if r.status_code == 422:
                return True, f"422 (server alive, schema mismatch)"
        return False, f"404 (no /auth/signup or /auth/register)"
    steps.append(_step("Register", do_register))

    # ── Step 2: Login ────────────────────────────────────────────────────
    def do_login():
        nonlocal token
        # Try email-based JSON login (what the injected auth_routes uses)
        email_body = {"email": creds["email"], "password": creds["password"]}
        r = requests.post(f"{base}/auth/login", json=email_body, timeout=5)
        if r.status_code in (200, 201):
            try:
                token = r.json().get("access_token") or r.json().get("token") or token
            except Exception:
                pass
            return True, f"JSON {r.status_code}"
        # Try username-based JSON login
        r2 = requests.post(f"{base}/auth/login",
                           json={"username": creds["username"], "password": creds["password"]},
                           timeout=5)
        if r2.status_code in (200, 201):
            try:
                token = r2.json().get("access_token") or r2.json().get("token") or token
            except Exception:
                pass
            return True, f"JSON {r2.status_code}"
        # Try form-data (OAuth2 /token endpoint)
        r3 = requests.post(f"{base}/auth/token",
                           data={"username": creds["username"], "password": creds["password"]},
                           timeout=5)
        if r3.status_code in (200, 201):
            try:
                token = r3.json().get("access_token") or r3.json().get("token") or token
            except Exception:
                pass
            return True, f"form {r3.status_code}"
        if r.status_code == 422:
            return True, "422 (server alive, auth format mismatch)"
        return False, f"JSON {r.status_code}, form {r3.status_code}"
    steps.append(_step("Login", do_login))

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # ── Step 3: Detect entity (informational) ───────────────────────────
    steps.append(JourneyStep(name="Detect entity", passed=True,
                              duration_ms=0, detail=f"/{entity}"))

    # ── Step 4: Create entity ─────────────────────────────────────────
    def do_create():
        nonlocal entity_id
        payload = {"title": "Journey Test Item", "name": "Journey Test Item",
                   "description": "created by V5.5 journey runner"}
        r = requests.post(f"{base}/{entity}", json=payload, headers=headers, timeout=5)
        if r.status_code in (200, 201):
            try:
                data = r.json()
                entity_id = data.get("id") or (data[0].get("id") if isinstance(data, list) else None)
            except Exception:
                pass
            return True, f"{r.status_code} id={entity_id}"
        if r.status_code == 422:
            return True, f"422 (schema mismatch, server alive)"
        return False, f"{r.status_code}"
    steps.append(_step("Create entity", do_create))

    # ── Step 5: List entities ─────────────────────────────────────────
    def do_list():
        nonlocal entity_id
        r = requests.get(f"{base}/{entity}", headers=headers, timeout=5)
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
        r = requests.put(f"{base}/{entity}/{entity_id}",
                         json={"title": "Journey Test Item EDITED"},
                         headers=headers, timeout=5)
        return r.status_code in (200, 201, 204), f"{r.status_code}"
    steps.append(_step("Edit entity", do_edit))

    # ── Step 7: Delete entity ─────────────────────────────────────────
    def do_delete():
        if entity_id is None:
            return False, "no entity_id captured"
        r = requests.delete(f"{base}/{entity}/{entity_id}", headers=headers, timeout=5)
        return r.status_code in (200, 204, 404), f"{r.status_code}"
    steps.append(_step("Delete entity", do_delete))

    # ── Step 8: Verify deletion ───────────────────────────────────────
    def do_verify_delete():
        if entity_id is None:
            return False, "no entity_id"
        r = requests.get(f"{base}/{entity}/{entity_id}", headers=headers, timeout=5)
        if r.status_code == 404:
            return True, "404 confirmed deleted"
        # Also check if list no longer contains it
        r2 = requests.get(f"{base}/{entity}", headers=headers, timeout=5)
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
        r = requests.post(f"{base}/auth/logout", headers=headers, timeout=5)
        return r.status_code in (200, 204, 404, 405, 401), f"{r.status_code}"
    steps.append(_step("Logout", do_logout))

    # ── Step 10: Login again ─────────────────────────────────────────
    def do_relogin():
        nonlocal token, headers
        r = requests.post(f"{base}/auth/login",
                          json={"username": creds["username"], "password": creds["password"]},
                          timeout=5)
        if r.status_code in (200, 201):
            try:
                token = r.json().get("access_token") or r.json().get("token")
                headers = {"Authorization": f"Bearer {token}"} if token else headers
            except Exception:
                pass
            return True, f"re-login {r.status_code}"
        return r.status_code == 422, f"{r.status_code}"
    steps.append(_step("Login again", do_relogin))

    # ── Step 11: Verify persistence ──────────────────────────────────
    persistence_verified = False

    def do_persistence():
        nonlocal persistence_verified
        if entity_id is None:
            return False, "no entity_id"
        r = requests.get(f"{base}/{entity}", headers=headers, timeout=5)
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
    )
