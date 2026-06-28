"""
Deployment Fix Service

Given a structured deployment error (from deployment_error_parser),
generates and applies a code fix to the generated project, then records
the outcome to deployment_memory.json.

Pattern mirrors runtime_fix_service.py but targets deployment-time errors.
"""
import datetime
import json
import os
import time
from pathlib import Path

from app.providers.ai_provider import generate_content
from app.prompts.shared_contract import FASTAPI_CONTRACT
from app.utils.json_cleaner import extract_json

DEPLOYMENT_MEMORY_FILE = Path(__file__).parent.parent.parent / "failure_memory" / "deployment_memory.json"


# ── Deterministic fixes (no LLM needed) ─────────────────────────────────────

_DETERMINISTIC_FIXES: dict[str, callable] = {}


def _deterministic_fix(error_type: str):
    def decorator(fn):
        _DETERMINISTIC_FIXES[error_type] = fn
        return fn
    return decorator


@_deterministic_fix("HealthCheckFail")
def _fix_health_check(project_path: str, parsed_error: dict) -> dict | None:
    main_py = Path(project_path) / "app" / "main.py"
    if not main_py.exists():
        return None
    content = main_py.read_text(encoding="utf-8")
    if '"/health"' in content or "'/health'" in content:
        return None  # already has /health, different problem
    fixed = content.rstrip("\n") + '\n\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n'
    main_py.write_text(fixed, encoding="utf-8")
    return {"path": "app/main.py", "fix": "injected /health endpoint"}


@_deterministic_fix("PortError")
def _fix_port_error(project_path: str, parsed_error: dict) -> dict | None:
    # Port is fixed in the Dockerfile, not in generated code
    # The deployment_service already generates the right Dockerfile
    return None  # Dockerfile is written by deployment_service.py


@_deterministic_fix("FrontendBuildError")
def _fix_frontend_build(project_path: str, parsed_error: dict) -> dict | None:
    """
    Deterministic fixes for npm/Cloudflare Pages build failures.

    Common causes of ENOENT mkdir node_modules/.bin:
      - package.json missing a build script
      - Wrong build root directory
      - Malformed package.json
    """
    root = Path(project_path)
    package_json_path = root / "package.json"
    if not package_json_path.exists():
        return None

    try:
        pkg = json.loads(package_json_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    changed = False

    # Ensure build script exists
    scripts = pkg.setdefault("scripts", {})
    if "build" not in scripts:
        scripts["build"] = "vite build"
        changed = True

    # Ensure preview script for Cloudflare compatibility
    if "preview" not in scripts:
        scripts["preview"] = "vite preview"
        changed = True

    # Ensure vite is in devDependencies
    dev_deps = pkg.setdefault("devDependencies", {})
    if "vite" not in dev_deps and "vite" not in pkg.get("dependencies", {}):
        dev_deps["vite"] = "^5.0.0"
        changed = True

    if "@vitejs/plugin-react" not in dev_deps:
        dev_deps["@vitejs/plugin-react"] = "^4.0.0"
        changed = True

    if changed:
        package_json_path.write_text(json.dumps(pkg, indent=2), encoding="utf-8")

    # Ensure vite.config.js exists at root
    vite_config = root / "vite.config.js"
    if not vite_config.exists():
        vite_config.write_text(
            'import { defineConfig } from "vite";\n'
            'import react from "@vitejs/plugin-react";\n'
            "export default defineConfig({ plugins: [react()] });\n",
            encoding="utf-8",
        )
        changed = True

    if changed:
        return {"path": "package.json", "fix": "ensured build script, vite devDep, vite.config.js"}
    return None


@_deterministic_fix("CloudflareBuildError")
def _fix_cloudflare_build(project_path: str, parsed_error: dict) -> dict | None:
    """
    Cloudflare Pages-specific fixes.
    Writes a _headers file and ensures the build output directory is correct.
    """
    root = Path(project_path)
    package_json_path = root / "package.json"
    if not package_json_path.exists():
        return None

    # Write _headers for Cloudflare Pages SPA routing
    headers_path = root / "public" / "_headers"
    headers_path.parent.mkdir(parents=True, exist_ok=True)
    if not headers_path.exists():
        headers_path.write_text(
            "/*\n"
            "  X-Frame-Options: DENY\n"
            "  X-Content-Type-Options: nosniff\n",
            encoding="utf-8",
        )

    # Write _redirects for SPA fallback
    redirects_path = root / "public" / "_redirects"
    if not redirects_path.exists():
        redirects_path.write_text("/* /index.html 200\n", encoding="utf-8")
        return {"path": "public/_redirects", "fix": "added Cloudflare SPA redirect rules"}

    return None


@_deterministic_fix("RenderTimeoutError")
def _fix_render_timeout(project_path: str, parsed_error: dict) -> dict | None:
    """
    Render deployment timeouts usually mean the app failed to bind to $PORT.
    Ensure main.py respects the PORT env var.
    """
    root = Path(project_path)
    render_yaml = root / "render.yaml"
    if render_yaml.exists():
        return None  # already configured

    render_yaml.write_text(
        "services:\n"
        "  - type: web\n"
        "    name: forgeai-app\n"
        "    env: python\n"
        "    buildCommand: pip install -r app/requirements.txt\n"
        "    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT\n"
        "    healthCheckPath: /health\n",
        encoding="utf-8",
    )
    return {"path": "render.yaml", "fix": "added render.yaml with $PORT binding"}


@_deterministic_fix("BuildError")
def _fix_requirements(project_path: str, parsed_error: dict) -> dict | None:
    req_file = Path(project_path) / "app" / "requirements.txt"
    if not req_file.exists():
        return None
    content = req_file.read_text(encoding="utf-8")
    # Known package name corrections
    replacements = {
        "PyJWT": "pyjwt",
        "Pydantic": "pydantic",
        "FastAPI": "fastapi",
        "Uvicorn": "uvicorn[standard]",
        "SQLAlchemy": "sqlalchemy",
        "passlib-bcrypt": "passlib[bcrypt]",
        "python_multipart": "python-multipart",
        "python.multipart": "python-multipart",
        "email_validator": "email-validator",
        "e-mail-validator": "email-validator",
        "jose": "python-jose[cryptography]",
        "PyJose": "python-jose[cryptography]",
    }
    fixed = content
    for wrong, correct in replacements.items():
        fixed = fixed.replace(wrong, correct)
    if fixed != content:
        req_file.write_text(fixed, encoding="utf-8")
        return {"path": "app/requirements.txt", "fix": "corrected package names"}
    return None


# ── LLM-powered fix ──────────────────────────────────────────────────────────

def _build_deployment_fix_prompt(
    project_path: str,
    parsed_error: dict,
    error_log: str,
) -> str:
    error_type = parsed_error.get("type", "Unknown")
    fix_hint = parsed_error.get("fix_hint", "")
    affected_file = parsed_error.get("affected_file")

    # Read the affected file if we know it
    file_content = ""
    file_to_fix = affected_file or "app/main.py"
    abs_path = Path(project_path) / file_to_fix.lstrip("/")
    if abs_path.exists():
        file_content = abs_path.read_text(encoding="utf-8", errors="replace")[:3000]

    # Also read requirements.txt for context
    req_content = ""
    req_path = Path(project_path) / "app" / "requirements.txt"
    if req_path.exists():
        req_content = req_path.read_text(encoding="utf-8")

    return f"""{FASTAPI_CONTRACT}

You are a deployment fix engineer. A generated FastAPI app failed to deploy on Railway.

DEPLOYMENT ERROR TYPE: {error_type}
ERROR MESSAGE: {parsed_error.get("message", "")}
FIX HINT: {fix_hint}

DEPLOYMENT LOG (last 1000 chars):
{error_log[-1000:]}

FILE TO FIX ({file_to_fix}):
{file_content or "(file not found — generate it from scratch)"}

REQUIREMENTS.TXT:
{req_content or "(not found)"}

TASK: Generate a fixed version of the file that resolves the deployment error.

Rules:
- Fix ONLY the deployment error described above
- Return the COMPLETE fixed file content
- For requirements.txt fixes: return ALL dependencies, one per line, no version pins unless needed
- For app/main.py fixes: ensure CORS middleware, /health endpoint, and all routers are present

Return ONLY valid JSON (no markdown):
{{"path": "{file_to_fix}", "content": "FULL CORRECTED FILE CONTENT"}}"""


def generate_deployment_fix(
    project_path: str,
    parsed_error: dict,
    error_log: str = "",
    provider: str = "auto",
) -> dict | None:
    """
    Generate a code fix for a deployment error.

    Returns {"path": "...", "content": "..."} or None if unfixable.
    """
    error_type = parsed_error.get("type", "Unknown")

    # Try deterministic fix first (faster, cheaper, more reliable)
    if error_type in _DETERMINISTIC_FIXES:
        fix = _DETERMINISTIC_FIXES[error_type](project_path, parsed_error)
        if fix:
            print(f"  [DeployFix] Deterministic fix applied for {error_type}: {fix.get('fix')}")
            record_deployment_fix(error_type, success=True, fix_method="deterministic")
            return fix

    # If not fixable at code level, return None
    if not parsed_error.get("fixable"):
        print(f"  [DeployFix] Error type {error_type} is not auto-fixable")
        return None

    # LLM-powered fix
    print(f"  [DeployFix] Generating LLM fix for {error_type}...")
    try:
        prompt = _build_deployment_fix_prompt(project_path, parsed_error, error_log)
        raw = generate_content(prompt, provider=provider, stage="deployment_fix")
        cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = extract_json(cleaned)
        if data and data.get("path") and data.get("content"):
            # Write the fix
            target = Path(project_path) / data["path"].lstrip("/").lstrip("\\")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data["content"], encoding="utf-8")
            print(f"  [DeployFix] LLM fix written to {data['path']}")
            record_deployment_fix(error_type, success=True, fix_method="llm")
            return data
    except Exception as e:
        print(f"  [DeployFix] LLM fix failed: {e}")

    record_deployment_fix(error_type, success=False)
    return None


# ── Deployment memory ────────────────────────────────────────────────────────

def _load_memory() -> dict:
    if DEPLOYMENT_MEMORY_FILE.exists():
        try:
            return json.loads(DEPLOYMENT_MEMORY_FILE.read_text())
        except Exception:
            pass
    return {"total_deployments": 0, "successful_deployments": 0, "errors": {}}


def _save_memory(data: dict) -> None:
    try:
        DEPLOYMENT_MEMORY_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"  [DeployFix] Memory write failed (non-fatal): {e}")


def record_deployment_outcome(
    success: bool,
    health_latency_ms: int | None = None,
    error_types: list[str] | None = None,
) -> None:
    """
    Record one complete deployment attempt (success or failure).
    Called once per V11 run at the end of the pipeline.
    Appends to deployment_log for time-windowed leaderboard queries.
    """
    data = _load_memory()
    data["total_deployments"] = data.get("total_deployments", 0) + 1
    if success:
        data["successful_deployments"] = data.get("successful_deployments", 0) + 1
    if health_latency_ms is not None and success:
        prev_avg = data.get("avg_health_latency_ms", 0)
        prev_n = data.get("successful_deployments", 1) - 1
        data["avg_health_latency_ms"] = (
            (prev_avg * prev_n + health_latency_ms) / max(prev_n + 1, 1)
        )

    # Append to rolling log for 7d/30d window queries (keep last 500 entries)
    log_entry = {
        "ts": datetime.datetime.utcnow().isoformat(),
        "success": success,
        "health_latency_ms": health_latency_ms,
        "error_types": error_types or [],
    }
    log: list = data.setdefault("deployment_log", [])
    log.append(log_entry)
    if len(log) > 500:
        data["deployment_log"] = log[-500:]

    _save_memory(data)


def record_deployment_fix(
    error_type: str,
    success: bool,
    fix_method: str = "unknown",
) -> None:
    """Record a per-error fix attempt and outcome."""
    data = _load_memory()
    errors = data.setdefault("errors", {})
    entry = errors.setdefault(error_type, {
        "seen": 0, "fixed": 0, "fix_methods": {},
    })
    entry["seen"] = entry.get("seen", 0) + 1
    if success:
        entry["fixed"] = entry.get("fixed", 0) + 1
        fm = entry.setdefault("fix_methods", {})
        fm[fix_method] = fm.get(fix_method, 0) + 1
    entry["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save_memory(data)


def get_deployment_stats() -> dict:
    """
    Return deployment benchmark stats:
      total, successful, failed, success_rate, top_errors, error_fix_rates
    """
    data = _load_memory()
    total = data.get("total_deployments", 0)
    successful = data.get("successful_deployments", 0)
    failed = total - successful
    success_rate = round(successful / total * 100, 1) if total > 0 else 0.0

    errors = data.get("errors", {})
    sorted_errors = sorted(errors.items(), key=lambda x: x[1].get("seen", 0), reverse=True)

    top_errors = [k for k, _ in sorted_errors[:5]]

    error_details = {}
    for k, v in sorted_errors:
        seen = v.get("seen", 0)
        fixed = v.get("fixed", 0)
        error_details[k] = {
            "seen": seen,
            "fixed": fixed,
            "fix_rate": round(fixed / seen * 100, 1) if seen else 0.0,
            "last_seen": v.get("last_seen"),
        }

    return {
        "total_deployments": total,
        "successful": successful,
        "failed": failed,
        "success_rate": success_rate,
        "avg_health_latency_ms": round(data.get("avg_health_latency_ms", 0)),
        "top_errors": top_errors,
        "error_fix_rates": error_details,
    }


def get_deployment_leaderboard() -> dict:
    """
    Time-windowed deployment success leaderboard.

    Shows 7-day vs 30-day success rates, improvement velocity,
    and which error types ForgeAI has learned to fix best/worst.
    """
    data = _load_memory()
    log: list[dict] = data.get("deployment_log", [])

    now = datetime.datetime.utcnow()
    cutoff_7d = (now - datetime.timedelta(days=7)).isoformat()
    cutoff_30d = (now - datetime.timedelta(days=30)).isoformat()

    def window(entries: list[dict], cutoff: str) -> dict:
        subset = [e for e in entries if (e.get("ts") or "") >= cutoff]
        if not subset:
            return {"count": 0, "success_rate": 0.0}
        n_success = sum(1 for e in subset if e.get("success"))
        return {
            "count": len(subset),
            "success_rate": round(n_success / len(subset) * 100, 1),
        }

    w7 = window(log, cutoff_7d)
    w30 = window(log, cutoff_30d)

    # Improvement velocity: recent 7d vs prior 30d window
    velocity = w7["success_rate"] - w30["success_rate"]
    velocity_str = f"{velocity:+.1f}%"

    # Best/worst fix patterns by fix rate (min 2 seen to qualify)
    errors = data.get("errors", {})
    fix_rates = {
        k: v.get("fixed", 0) / v["seen"]
        for k, v in errors.items()
        if v.get("seen", 0) >= 2
    }
    best = max(fix_rates, key=fix_rates.get) if fix_rates else None
    worst = min(fix_rates, key=fix_rates.get) if fix_rates else None

    total = data.get("total_deployments", 0)
    successful = data.get("successful_deployments", 0)
    all_time_rate = round(successful / total * 100, 1) if total else 0.0

    return {
        "deployment_success_last_7_days": w7["success_rate"],
        "deployment_success_last_30_days": w30["success_rate"],
        "improvement_velocity": velocity_str,
        "best_fix_pattern": best,
        "worst_fix_pattern": worst,
        "all_time_success_rate": all_time_rate,
        "total_deployments": total,
        "avg_health_latency_ms": round(data.get("avg_health_latency_ms", 0)),
        "deployments_last_7_days": w7["count"],
        "deployments_last_30_days": w30["count"],
    }


def get_deployment_memory_summary() -> dict:
    """Raw memory dump — used by /deployment/memory endpoint."""
    return _load_memory()


# ── Deployment Verification & Recovery Engine ─────────────────────────────────

def verify_and_recover_deployment(
    project_path: str,
    live_url: str | None = None,
    deploy_logs: str = "",
    provider: str = "auto",
    max_fix_attempts: int = 2,
) -> dict:
    """
    Full deployment verification and recovery loop.

    Steps:
      1. Collect deployment logs / hit live URL for health data
      2. Classify the failure type (deterministic, not LLM)
      3. Apply deterministic fix if available
      4. Fall back to LLM fix only for complex errors
      5. Record outcome to deployment memory

    Returns a structured result with what was tried and whether recovery succeeded.
    """
    from app.runtime.deployment_error_parser import parse_deployment_error
    from app.runtime.deployment_validator import validate_deployment_with_retry

    results: list[dict] = []
    health_report: dict | None = None

    # Step 1: Health check against live URL if provided
    if live_url:
        print(f"\n  [DeployVerify] Checking live URL: {live_url}")
        health_report = validate_deployment_with_retry(live_url, retries=2, wait=10)
        status = "PASS" if health_report.get("success") else "FAIL"
        print(f"  [DeployVerify] Health check {status}: {health_report.get('error', 'ok')}")

        if health_report.get("success"):
            record_deployment_outcome(success=True)
            return {
                "success": True,
                "health_report": health_report,
                "fixes_applied": [],
                "attempts": 0,
                "message": "Deployment healthy — no recovery needed",
            }

    for attempt in range(1, max_fix_attempts + 1):
        print(f"\n  [DeployVerify] Recovery attempt {attempt}/{max_fix_attempts}")

        # Step 2: Classify failure
        parsed_error = parse_deployment_error(
            logs=deploy_logs,
            url=live_url,
            health_report=health_report,
        )
        error_type = parsed_error.get("type", "Unknown")
        print(f"  [DeployVerify] Error classified: {error_type} — {parsed_error.get('message', '')}")

        # Step 3: Apply fix
        fix = generate_deployment_fix(
            project_path=project_path,
            parsed_error=parsed_error,
            error_log=deploy_logs,
            provider=provider,
        )

        if fix:
            results.append({
                "attempt": attempt,
                "error_type": error_type,
                "fix": fix.get("fix") or fix.get("path"),
            })
            print(f"  [DeployVerify] Fix applied: {fix.get('fix') or fix.get('path')}")
        else:
            print(f"  [DeployVerify] No fix available for {error_type}")
            # If it's not fixable and we have no suggestions, stop retrying
            if not parsed_error.get("fixable"):
                break

        # Step 4: Re-verify if we have a URL to check
        if live_url and fix:
            print(f"  [DeployVerify] Re-checking health after fix...")
            # Short wait for potential redeploy (if auto-redeploy was triggered)
            time.sleep(5)
            health_report = validate_deployment_with_retry(live_url, retries=2, wait=10)
            if health_report.get("success"):
                print(f"  [DeployVerify] Recovery succeeded after {attempt} attempt(s)")
                record_deployment_outcome(
                    success=True,
                    health_latency_ms=_extract_latency(health_report),
                )
                return {
                    "success": True,
                    "health_report": health_report,
                    "fixes_applied": results,
                    "attempts": attempt,
                    "message": f"Recovered after {attempt} attempt(s)",
                }

    # All attempts exhausted
    error_types = [r["error_type"] for r in results]
    record_deployment_outcome(success=False, error_types=error_types)
    return {
        "success": False,
        "health_report": health_report,
        "fixes_applied": results,
        "attempts": max_fix_attempts,
        "message": (
            f"Could not recover deployment after {max_fix_attempts} attempt(s). "
            f"Error types seen: {', '.join(set(error_types)) or 'Unknown'}"
        ),
    }


def _extract_latency(health_report: dict) -> int | None:
    checks = health_report.get("checks") or {}
    check = checks.get("/health") or {}
    return check.get("latency_ms")
