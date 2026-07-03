"""
Checks a live deployed FastAPI backend for common errors after deployment.
Tests: health, auth flows (register/login/me), CORS, endpoint access.
Returns structured error list for deployed_fixer to act on.
"""
import time
from datetime import datetime, timezone
from typing import Optional

import requests


_TIMEOUT = 10
_TEST_EMAIL = "forge_check@forgeai.dev"
_TEST_PASSWORD = "ForgeCheck123!"
_TEST_DISPLAY = "ForgeAI Check"


def _err(type_: str, endpoint: str, status: Optional[int], detail: str, fix_hint: str) -> dict:
    return {"type": type_, "endpoint": endpoint, "status": status, "detail": detail, "fix_hint": fix_hint}


def check_deployed_app(backend_url: str, architecture: Optional[dict] = None) -> dict:
    backend_url = backend_url.rstrip("/")
    errors: list[dict] = []
    passed: list[str] = []
    auth_token: Optional[str] = None

    # 1. Health check
    try:
        r = requests.get(f"{backend_url}/health", timeout=_TIMEOUT)
        if r.status_code == 200:
            passed.append("GET /health → 200 OK")
            ready = True
        else:
            errors.append(_err("HealthError", "/health", r.status_code,
                               f"Health returned {r.status_code}", "Check uvicorn startup logs in Render dashboard"))
            return _result(backend_url, errors, passed, None, ready=False)
    except requests.Timeout:
        errors.append(_err("HealthError", "/health", None, "Timeout after 10s — backend still starting up",
                           "Render free tier takes 5-10 min on first deploy. Wait and retry."))
        return _result(backend_url, errors, passed, None, ready=False)
    except Exception as e:
        errors.append(_err("HealthError", "/health", None, f"Connection failed: {e}",
                           "Backend may still be building. Check Render dashboard."))
        return _result(backend_url, errors, passed, None, ready=False)

    # 2. CORS check
    try:
        r = requests.options(
            f"{backend_url}/auth/login",
            headers={"Origin": "https://test.pages.dev", "Access-Control-Request-Method": "POST"},
            timeout=_TIMEOUT,
        )
        cors_ok = (
            "access-control-allow-origin" in r.headers
            or "Access-Control-Allow-Origin" in r.headers
        )
        if cors_ok:
            passed.append("CORS headers present on /auth/login")
        else:
            errors.append(_err("CORSError", "/auth/login", r.status_code,
                               "No Access-Control-Allow-Origin header on OPTIONS",
                               "Add CORSMiddleware to main.py: app.add_middleware(CORSMiddleware, allow_origins=['*'], ...)"))
    except Exception:
        pass  # CORS check failure is non-fatal

    # 3. Register
    try:
        r = requests.post(
            f"{backend_url}/auth/signup",
            json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD, "display_name": _TEST_DISPLAY},
            timeout=_TIMEOUT,
        )
        if r.status_code in (200, 201):
            passed.append(f"POST /auth/signup → {r.status_code}")
        elif r.status_code == 400:
            # Email already exists — not an error, just means the check ran before
            passed.append("POST /auth/signup → 400 (user exists from prior check — OK)")
        else:
            body = r.text[:300]
            errors.append(_err("RegistrationError", "/auth/signup", r.status_code,
                               f"Signup failed: {body}",
                               "Check User model has email+password fields; check auth_routes.py /auth/signup handler"))
    except Exception as e:
        errors.append(_err("RegistrationError", "/auth/signup", None, f"Request failed: {e}",
                           "Ensure /auth/signup endpoint exists in auth_routes.py"))

    # 4. Login (JSON body -- matches the pipeline's own known-good
    # auth_routes.py template: LoginRequest{email, password} as JSON, not
    # OAuth2PasswordRequestForm form-data. Every generated frontend posts
    # JSON here too, so testing form-data produced a permanent false-positive
    # 422 that, if auto-"fixed", would swap in an OAuth2-form login
    # incompatible with the app's own working frontend.)
    try:
        r = requests.post(
            f"{backend_url}/auth/login",
            json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            auth_token = data.get("access_token")
            if auth_token:
                passed.append("POST /auth/login → 200 + access_token")
            else:
                errors.append(_err("LoginError", "/auth/login", 200,
                                   f"Login returned 200 but no access_token in body: {r.text[:200]}",
                                   "Login handler must return {'access_token': ..., 'token_type': 'bearer'}"))
        elif r.status_code == 422:
            errors.append(_err("LoginError", "/auth/login", 422,
                               f"Validation error — endpoint expects a different body: {r.text[:300]}",
                               "Login endpoint must accept a JSON body {email, password} "
                               "(e.g. a Pydantic LoginRequest model), not OAuth2PasswordRequestForm form-data — "
                               "every generated frontend posts JSON here"))
        elif r.status_code == 401:
            errors.append(_err("LoginError", "/auth/login", 401,
                               "Wrong password or password hash mismatch",
                               "Re-inject app/utils/auth.py with bcrypt verify_password; ensure hash stored on signup"))
        elif r.status_code == 500:
            errors.append(_err("LoginError", "/auth/login", 500,
                               f"Server crash on login: {r.text[:300]}",
                               "app/utils/auth.py likely uses passlib or python-jose which crash on Python 3.13. Inject bcrypt+PyJWT version."))
        elif r.status_code == 404:
            errors.append(_err("LoginError", "/auth/login", 404,
                               "/auth/login not found",
                               "auth_routes.py must define POST /auth/login; ensure it's included in main.py"))
        else:
            errors.append(_err("LoginError", "/auth/login", r.status_code,
                               f"Unexpected status: {r.text[:200]}",
                               "Check auth_routes.py login handler"))
    except Exception as e:
        errors.append(_err("LoginError", "/auth/login", None, f"Request failed: {e}",
                           "Ensure /auth/login endpoint exists"))

    # 5. /auth/me
    if auth_token:
        try:
            r = requests.get(
                f"{backend_url}/auth/me",
                headers={"Authorization": f"Bearer {auth_token}"},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                passed.append("GET /auth/me → 200")
            elif r.status_code == 401:
                errors.append(_err("AuthError", "/auth/me", 401,
                                   "Token rejected by /auth/me — JWT decode or User lookup failing",
                                   "Check get_current_user in app/utils/auth.py; ensure SECRET_KEY env var is set"))
            elif r.status_code == 500:
                errors.append(_err("AuthError", "/auth/me", 500,
                                   f"Server crash on /auth/me: {r.text[:200]}",
                                   "Check get_current_user; likely jwt.decode error or User model import issue"))
            else:
                errors.append(_err("AuthError", "/auth/me", r.status_code,
                                   f"Unexpected: {r.text[:200]}", "Check /auth/me endpoint"))
        except Exception as e:
            errors.append(_err("AuthError", "/auth/me", None, f"Request failed: {e}", "Check /auth/me endpoint"))

    # 6. Smoke GET endpoints (if architecture provided)
    if architecture and auth_token:
        import re
        for ep in architecture.get("api_endpoints", [])[:10]:  # cap at 10
            if ep.get("method", "GET").upper() != "GET":
                continue
            path = ep.get("path", "")
            if not path or "auth" in path:
                continue
            url_path = re.sub(r"\{[^}]+\}", "1", path)
            try:
                r = requests.get(
                    f"{backend_url}{url_path}",
                    headers={"Authorization": f"Bearer {auth_token}"},
                    timeout=_TIMEOUT,
                )
                if r.status_code < 400:
                    passed.append(f"GET {path} → {r.status_code}")
                elif r.status_code == 500:
                    errors.append(_err("EndpointError", path, 500,
                                       f"Handler crashed: {r.text[:200]}",
                                       f"Fix GET {path} handler — likely DB query or missing import"))
            except Exception:
                pass

    # 7. Seed
    try:
        r = requests.post(f"{backend_url}/seed", timeout=_TIMEOUT)
        if r.status_code in (200, 201):
            passed.append("POST /seed → OK")
        elif r.status_code == 500:
            errors.append(_err("EndpointError", "/seed", 500,
                               f"Seed crashed: {r.text[:200]}",
                               "Fix seed handler — check DB model field names match schema"))
    except Exception:
        pass

    return _result(backend_url, errors, passed, auth_token, ready=True)


def _result(backend_url: str, errors: list, passed: list, auth_token: Optional[str], ready: bool) -> dict:
    return {
        "backend_url": backend_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ready": ready,
        "errors": errors,
        "passed": passed,
        "auth_token": auth_token,
        "healthy": ready and not any(e["type"] in ("LoginError", "CORSError", "AuthError") for e in errors),
    }
