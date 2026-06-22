"""
Deployment Error Parser

Parses Railway/Render/Fly.io deployment logs into structured, actionable errors.

Categories:
  BuildError      — pip install failed, Docker build failed
  StartupCrash    — app crashes on startup (ModuleNotFoundError, ImportError, etc.)
  HealthCheckFail — app runs but /health returns non-200 or times out
  MissingEnvVar   — app crashes due to missing environment variable
  PortError       — app bound to wrong port (not $PORT)
  TimeoutError    — deployment timed out
  Unknown         — unrecognized error
"""
import re


def parse_deployment_error(
    logs: str = "",
    error: str | None = None,
    url: str | None = None,
    health_report: dict | None = None,
) -> dict:
    """
    Parse deployment logs + error string into a structured error dict.

    Args:
        logs:         Combined stdout+stderr from `railway up` or equivalent
        error:        The error field from DeploymentResult
        url:          Live URL if deployment succeeded but health check failed
        health_report: Result of validate_deployment() if health check was run

    Returns:
        {
            "type":        str,     # error category
            "message":     str,     # human-readable summary
            "fix_hint":    str,     # what to fix in the generated code
            "fixable":     bool,    # whether we can auto-fix it
            "affected_file": str | None,
            "raw":         str,     # raw log excerpt
        }
    """
    combined = f"{logs or ''}\n{error or ''}"
    snippet = combined[-3000:]  # last 3000 chars have the most relevant info

    # ── 1. Build errors (pip install / Docker) ──────────────────────────
    if _matches(snippet, [
        "ERROR: Could not find a version",
        "No matching distribution found",
        "pip install",
        "error: could not find a version that satisfies",
    ]):
        pkg_match = re.search(
            r"Could not find a version that satisfies the requirement (\S+)",
            snippet, re.IGNORECASE,
        )
        pkg = pkg_match.group(1) if pkg_match else None
        return _result(
            error_type="BuildError",
            message=f"pip install failed{f': {pkg}' if pkg else ''}",
            fix_hint=(
                f"Remove or replace '{pkg}' in app/requirements.txt with a valid PyPI package name. "
                "Check spelling — common mistakes: 'pyjwt' not 'PyJWT', 'passlib[bcrypt]' not 'passlib-bcrypt'."
                if pkg else
                "Fix app/requirements.txt — one or more packages failed to install."
            ),
            fixable=True,
            snippet=snippet[:500],
        )

    # ── 2. Missing environment variable ─────────────────────────────────
    env_match = re.search(
        r"KeyError: ['\"](\w+)['\"]|os\.environ\[['\"](\w+)['\"]|getenv\(['\"](\w+)['\"]",
        snippet,
    )
    if env_match or "KeyError" in snippet:
        var_name = next((g for g in (env_match.groups() if env_match else []) if g), None)
        return _result(
            error_type="MissingEnvVar",
            message=f"Missing environment variable{f': {var_name}' if var_name else ''}",
            fix_hint=(
                f"The app references os.environ['{var_name}'] which is not set on Railway. "
                f"Use os.environ.get('{var_name}', 'default') instead, or set the env var in Railway dashboard."
                if var_name else
                "Use os.environ.get() with a default instead of os.environ[] for optional config."
            ),
            fixable=True,
            snippet=snippet[:500],
        )

    # ── 3. Port binding errors ───────────────────────────────────────────
    if _matches(snippet, ["address already in use", "port", "bind"]):
        return _result(
            error_type="PortError",
            message="App bound to wrong port — Railway requires $PORT",
            fix_hint=(
                "The app must listen on $PORT, not a hardcoded port. "
                "In the Dockerfile CMD: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`. "
                "Never hardcode port 8000 or 3000."
            ),
            fixable=True,
            snippet=snippet[:500],
        )

    # ── 4. Runtime startup crash (same types as local runtime) ───────────
    if "ModuleNotFoundError" in snippet or "No module named" in snippet:
        mod_match = re.search(r"No module named ['\"](.+?)['\"]", snippet)
        mod = mod_match.group(1) if mod_match else None
        file_match = re.search(r'File "(.+?)", line \d+', snippet)
        affected = file_match.group(1) if file_match else None
        return _result(
            error_type="StartupCrash",
            message=f"ModuleNotFoundError: {mod or 'unknown module'}",
            fix_hint=(
                f"'{mod}' is not installed or doesn't exist. "
                "If it's a third-party package, add it to app/requirements.txt. "
                "If it's a local module (app.xxx), check the file exists and is imported correctly."
                if mod else
                "A module import failed at startup. Check app/requirements.txt and import paths."
            ),
            fixable=True,
            affected_file=affected,
            snippet=snippet[:500],
        )

    if "ImportError" in snippet or "cannot import name" in snippet:
        sym_match = re.search(r"cannot import name ['\"](.+?)['\"]", snippet)
        sym = sym_match.group(1) if sym_match else None
        return _result(
            error_type="StartupCrash",
            message=f"ImportError: cannot import '{sym or 'symbol'}'",
            fix_hint=(
                f"'{sym}' is not defined in the imported module. "
                "Check the module exports and the import statement."
                if sym else
                "An import failed. Check symbol names match what's exported."
            ),
            fixable=True,
            snippet=snippet[:500],
        )

    if "SyntaxError" in snippet:
        return _result(
            error_type="StartupCrash",
            message="SyntaxError in generated code",
            fix_hint="A Python file has a syntax error. Run ast.parse() on each .py file to find it.",
            fixable=True,
            snippet=snippet[:500],
        )

    if "Table" in snippet and "is already defined" in snippet:
        return _result(
            error_type="StartupCrash",
            message="SQLAlchemy: duplicate table definition",
            fix_hint=(
                "A model file imports another model at module level, causing double registration. "
                "Model files must NOT import other models. Use ForeignKey('table.id') strings. "
                "All model imports belong ONLY in main.py."
            ),
            fixable=True,
            snippet=snippet[:500],
        )

    # ── 5. Health check failure ──────────────────────────────────────────
    if health_report and not health_report.get("success"):
        checks = health_report.get("checks", {})
        health_check = checks.get("/health", {})
        if not health_check.get("ok"):
            status = health_check.get("status_code")
            if status == 404:
                return _result(
                    error_type="HealthCheckFail",
                    message=f"GET /health returned 404 — endpoint missing",
                    fix_hint=(
                        "Add @app.get('/health') to app/main.py: "
                        "@app.get('/health')\ndef health(): return {'status': 'ok'}"
                    ),
                    fixable=True,
                    affected_file="app/main.py",
                    snippet=str(health_check),
                )
            elif status and status >= 500:
                return _result(
                    error_type="HealthCheckFail",
                    message=f"GET /health returned {status}",
                    fix_hint="The /health endpoint is crashing. Simplify it to just return {'status': 'ok'}.",
                    fixable=True,
                    affected_file="app/main.py",
                    snippet=str(health_check),
                )
            elif not health_check.get("ok"):
                return _result(
                    error_type="HealthCheckFail",
                    message=f"Health check failed: {health_check.get('error', 'unknown')}",
                    fix_hint="App may not be listening on the right host/port. Ensure host=0.0.0.0 and port=$PORT.",
                    fixable=False,
                    snippet=str(health_report),
                )

    # ── 6. Generic deployment timeout ────────────────────────────────────
    if _matches(snippet, ["timeout", "timed out", "No domain yet"]):
        return _result(
            error_type="TimeoutError",
            message="Deployment timed out — app may be starting too slowly",
            fix_hint=(
                "App took too long to start. Check for heavy initialization (DB migrations, ML model loading) "
                "at startup. Move heavy work to background tasks or lazy-load."
            ),
            fixable=False,
            snippet=snippet[:500],
        )

    # ── 7. Railway project creation errors ───────────────────────────────
    if _matches(snippet, ["Railway API error", "serviceCreate", "projectCreate"]):
        return _result(
            error_type="ProjectCreationError",
            message="Railway API failed to create project/service",
            fix_hint="Railway API error — check RAILWAY_TOKEN is valid and the account has quota.",
            fixable=False,
            snippet=snippet[:300],
        )

    if _matches(snippet, ["No linked Railway project", "railway link"]):
        return _result(
            error_type="NotLinkedError",
            message="Railway project not linked",
            fix_hint="Run `railway link` in the project directory to link a Railway project before deploying.",
            fixable=False,
            snippet=snippet[:300],
        )

    return _result(
        error_type="Unknown",
        message=error or "Deployment failed for unknown reason",
        fix_hint="Check Railway dashboard build logs for details.",
        fixable=False,
        snippet=snippet[:500],
    )


def _matches(text: str, patterns: list[str]) -> bool:
    return any(p.lower() in text.lower() for p in patterns)


def _result(
    error_type: str,
    message: str,
    fix_hint: str,
    fixable: bool,
    snippet: str = "",
    affected_file: str | None = None,
) -> dict:
    return {
        "type": error_type,
        "message": message,
        "fix_hint": fix_hint,
        "fixable": fixable,
        "affected_file": affected_file,
        "raw": snippet,
    }
