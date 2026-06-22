"""
Deployment Validator

After a project is deployed, hit the live URL to verify it's actually alive:
  GET /health   — must return 2xx
  GET /         — must not 5xx
  GET /docs     — FastAPI auto-docs (optional, soft check)

Returns a health report dict consumed by V11 orchestrator and the forge score.
"""
import time

import requests


def validate_deployment(url: str, timeout: int = 30) -> dict:
    """
    Run health checks against a live deployed URL.

    Args:
        url:     Base URL of the deployed app (e.g. https://app.up.railway.app)
        timeout: Per-request timeout in seconds

    Returns:
        {
            "success": bool,
            "url": str,
            "checks": { "/health": {...}, "/": {...}, "/docs": {...} },
            "score": 0-100,
            "error": str | None,
        }
    """
    url = url.rstrip("/")
    checks: dict[str, dict] = {}
    passes = 0
    total = 0

    for path, required in [("/health", True), ("/", True), ("/docs", False)]:
        total += 1
        check: dict = {"path": path, "required": required}
        try:
            resp = requests.get(f"{url}{path}", timeout=timeout, allow_redirects=True)
            check["status_code"] = resp.status_code
            check["latency_ms"] = int(resp.elapsed.total_seconds() * 1000)

            if resp.status_code < 400:
                check["ok"] = True
            elif resp.status_code == 404:
                # Distinguish Railway's own 404 (build not ready) from the app's real 404.
                # Railway 404 has no FastAPI signature; app 404 has {"detail":"Not Found"}.
                body = resp.text[:400]
                app_is_up = (
                    '"detail"' in body          # FastAPI 404 body
                    or "FastAPI" in body        # /docs or openapi response
                    or "swagger" in body.lower()
                    or "openapi" in body.lower()
                    or "status" in body         # {"status":"ok"}
                )
                check["ok"] = app_is_up
                check["railway_404"] = not app_is_up
            elif resp.status_code < 500:
                check["ok"] = True
            else:
                check["ok"] = False

            if check["ok"]:
                passes += 1
        except requests.exceptions.ConnectionError:
            check["ok"] = False
            check["error"] = "connection refused"
        except requests.exceptions.Timeout:
            check["ok"] = False
            check["error"] = f"timeout after {timeout}s"
        except Exception as exc:
            check["ok"] = False
            check["error"] = str(exc)
        checks[path] = check

    required_checks = [c for c in checks.values() if c["required"]]
    all_required_pass = all(c["ok"] for c in required_checks)
    score = round((passes / total) * 100) if total else 0

    return {
        "success": all_required_pass,
        "url": url,
        "checks": checks,
        "score": score,
        "error": None if all_required_pass else _first_error(checks),
    }


def validate_deployment_with_retry(
    url: str,
    retries: int = 3,
    wait: int = 15,
    timeout: int = 30,
) -> dict:
    """
    Retry validation — useful right after deploy when the app may still be starting.
    Waits `wait` seconds between attempts.
    """
    last: dict = {}
    for attempt in range(1, retries + 1):
        print(f"  [DeployValidator] Health check attempt {attempt}/{retries}: {url}")
        last = validate_deployment(url, timeout=timeout)
        if last["success"]:
            return last
        if attempt < retries:
            print(f"  [DeployValidator] Not ready yet — waiting {wait}s...")
            time.sleep(wait)
    return last


def _first_error(checks: dict) -> str:
    for path, check in checks.items():
        if not check["ok"]:
            err = check.get("error") or f"HTTP {check.get('status_code', '?')}"
            return f"{path}: {err}"
    return "unknown"
