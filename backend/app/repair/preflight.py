"""
Preflight Deterministic Fix Registry

Deterministic fixes that don't need an LLM.  Every fix here saves tokens,
latency, and reduces the chance of LLM hallucination introducing new errors.

Registry pattern: each fix is a small callable that returns True if it changed
anything.  Fixes are attempted in priority order.  The registry can be extended
with @preflight.register() without modifying this file.

Current fixes (no LLM needed):
  1. Missing package in requirements.txt        → add it
  2. Wrong postgres:// URL scheme               → fix database.py
  3. PyJWT / bcrypt not in requirements         → add them
  4. Wrong router name (router vs task_router)  → rename
  5. Parameter ordering error                   → reorder params
  6. Missing .env (template)                    → generate skeleton
  7. Port conflict                              → assign free port
  8. Passlib/python-jose in requirements        → swap for PyJWT/bcrypt
  9. Missing __init__.py in app/               → touch it
 10. Unused passlib / werkzeug imports in code  → strip them
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional


# ── Registry ──────────────────────────────────────────────────────────────────

class PreflightRegistry:
    """
    Registry of deterministic fix functions.
    Each fix: (project_path: Path, diagnostics: list[dict]) → bool (changed?)
    """

    def __init__(self):
        self._fixes: list[tuple[int, str, Callable]] = []  # (priority, name, fn)

    def register(self, name: str, priority: int = 50):
        """Decorator: @preflight.register("fix_name", priority=10)"""
        def decorator(fn: Callable) -> Callable:
            self._fixes.append((priority, name, fn))
            self._fixes.sort(key=lambda x: x[0])
            return fn
        return decorator

    def run(self, project_path: Path, diagnostics: list | None = None) -> dict[str, bool]:
        """
        Run all registered fixes against the project.
        Returns {fix_name: changed?} for each fix applied.
        """
        diagnostics = diagnostics or []
        results: dict[str, bool] = {}
        for _, name, fn in self._fixes:
            try:
                changed = fn(project_path, diagnostics)
                results[name] = bool(changed)
                if changed:
                    print(f"  [preflight] {name}: applied")
            except Exception as exc:
                results[name] = False
                print(f"  [preflight] {name}: skipped ({exc})")
        total = sum(1 for v in results.values() if v)
        if total:
            print(f"  [preflight] {total} deterministic fix(es) applied without LLM")
        return results


preflight = PreflightRegistry()


# ── Fix implementations ───────────────────────────────────────────────────────

def _req_path(project_path: Path) -> Path:
    return project_path / "app" / "requirements.txt"


def _read_req(project_path: Path) -> list[str]:
    p = _req_path(project_path)
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _write_req(project_path: Path, lines: list[str]):
    _req_path(project_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


@preflight.register("fix_pyjwt", priority=10)
def _fix_pyjwt(project_path: Path, diagnostics: list) -> bool:
    """Add PyJWT if any file uses `import jwt` but PyJWT isn't in requirements."""
    # Check if any .py file imports jwt
    uses_jwt = any(
        "import jwt" in f.read_text(encoding="utf-8", errors="replace")
        for f in (project_path / "app").rglob("*.py")
        if f.exists()
    )
    if not uses_jwt:
        return False
    lines = _read_req(project_path)
    if any("pyjwt" in l.lower() or l.strip().lower() == "jwt" for l in lines):
        return False
    lines.append("PyJWT")
    _write_req(project_path, lines)
    return True


@preflight.register("fix_bcrypt", priority=11)
def _fix_bcrypt(project_path: Path, diagnostics: list) -> bool:
    """Add bcrypt if utils/auth.py uses it but it's not in requirements."""
    auth_file = project_path / "app" / "utils" / "auth.py"
    if not auth_file.exists():
        return False
    content = auth_file.read_text(encoding="utf-8", errors="replace")
    if "bcrypt" not in content:
        return False
    lines = _read_req(project_path)
    if any("bcrypt" in l.lower() for l in lines):
        return False
    lines.append("bcrypt")
    _write_req(project_path, lines)
    return True


@preflight.register("swap_passlib", priority=12)
def _swap_passlib(project_path: Path, diagnostics: list) -> bool:
    """Remove passlib / python-jose from requirements (incompatible with bcrypt 4+)."""
    lines = _read_req(project_path)
    new_lines = [
        l for l in lines
        if not re.match(r"passlib|python[-_]jose", l.strip(), re.IGNORECASE)
    ]
    if len(new_lines) == len(lines):
        return False
    # Ensure PyJWT and bcrypt are present
    pkgs = {l.lower().split("==")[0].split(">=")[0].strip() for l in new_lines}
    if "pyjwt" not in pkgs:
        new_lines.append("PyJWT")
    if "bcrypt" not in pkgs:
        new_lines.append("bcrypt")
    _write_req(project_path, new_lines)
    return True


@preflight.register("fix_config_missing_settings_instance", priority=13)
def _fix_config_missing_settings_instance(project_path: Path, diagnostics: list) -> bool:
    """
    Ensure app/config.py exports a module-level `settings` instance if it
    defines a Config/Settings class but never instantiated it under that
    exact name -- app/main.py and other files import `settings` by
    convention. This recurs often enough across generations (seen live,
    costing 1-2 otherwise-avoidable LLM fix calls each time) to fix for free.
    Must run before fix_config_missing_attrs (priority 14), which assumes
    the `settings = Config()` line already exists.
    """
    config_file = project_path / "app" / "config.py"
    if not config_file.exists():
        return False
    content = config_file.read_text(encoding="utf-8", errors="replace")

    if re.search(r'^settings\s*=', content, re.MULTILINE):
        return False  # already has a top-level `settings` name

    m = re.search(r'^class (Config|Settings)\b', content, re.MULTILINE)
    if not m:
        return False  # no class to instantiate

    class_name = m.group(1)
    config_file.write_text(content.rstrip() + f"\n\nsettings = {class_name}()\n", encoding="utf-8")
    return True


@preflight.register("fix_postgres_url", priority=15)
def _fix_postgres_url(project_path: Path, diagnostics: list) -> bool:
    """Fix postgres:// → postgresql:// in database.py (SQLAlchemy 1.4+ requirement)."""
    db_file = project_path / "app" / "database.py"
    if not db_file.exists():
        return False
    content = db_file.read_text(encoding="utf-8", errors="replace")
    if "postgres://" not in content:
        return False
    new_content = content.replace(
        '"postgres://"',
        '"postgresql://"'
    )
    # Handle the runtime replace pattern too
    if "postgres://" in new_content:
        new_content = re.sub(
            r'(DATABASE_URL\s*=\s*DATABASE_URL\.replace\s*\()(["\'])postgres://',
            lambda m: m.group(0).replace("postgres://", "postgresql://"),
            new_content
        )
    # If still present, add the runtime fix
    if "postgres://" in new_content:
        fix_line = '\nif DATABASE_URL.startswith("postgres://"):\n    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)\n'
        new_content = new_content + fix_line
    if new_content != content:
        db_file.write_text(new_content, encoding="utf-8")
        return True
    return False


@preflight.register("fix_config_missing_attrs", priority=14)
def _fix_config_missing_attrs(project_path: Path, diagnostics: list) -> bool:
    """
    Ensure commonly-referenced settings attributes (DATABASE_URL, SECRET_KEY,
    etc.) always exist on the config/settings object, even if the LLM's
    generated Config/Settings class forgot to define one of them. Appends a
    runtime guard after the class body rather than parsing/editing it, so it
    works regardless of whether Config is a plain class, dataclass, or
    pydantic BaseSettings -- and is a no-op if everything is already defined.

    Root cause this addresses: `AttributeError: 'Config' object has no
    attribute 'DATABASE_URL'` recurring across generations because
    app/main.py does `create_engine(settings.DATABASE_URL)` but the
    generated Config class sometimes omits that field.
    """
    config_file = project_path / "app" / "config.py"
    if not config_file.exists():
        return False
    content = config_file.read_text(encoding="utf-8", errors="replace")

    m = re.search(r'^(\w+)\s*=\s*(?:Config|Settings)\s*\(\s*\)', content, re.MULTILINE)
    if not m:
        return False
    instance_name = m.group(1)

    defaults = {
        "DATABASE_URL": 'os.getenv("DATABASE_URL", "sqlite:///./app.db")',
        "SECRET_KEY": 'os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")',
        "ALGORITHM": 'os.getenv("ALGORITHM", "HS256")',
        "ACCESS_TOKEN_EXPIRE_MINUTES": 'int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))',
    }
    missing = {attr: expr for attr, expr in defaults.items() if not re.search(rf'\b{attr}\b', content)}
    if not missing:
        return False

    guard_lines = []
    if not re.search(r'^import os\b', content, re.MULTILINE):
        guard_lines.append("import os")
    guard_lines.append("\n# Preflight patch: ensure commonly-referenced settings attributes always exist")
    for attr, expr in missing.items():
        guard_lines.append(f'if not hasattr({instance_name}, "{attr}"):')
        guard_lines.append(f'    {instance_name}.{attr} = {expr}')

    config_file.write_text(content.rstrip() + "\n" + "\n".join(guard_lines) + "\n", encoding="utf-8")
    return True


@preflight.register("fix_missing_init", priority=20)
def _fix_missing_init(project_path: Path, diagnostics: list) -> bool:
    """Add missing __init__.py files in app/ subdirectories."""
    changed = False
    for subdir in (project_path / "app").iterdir():
        if subdir.is_dir() and not subdir.name.startswith(("__", ".")):
            init = subdir / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")
                changed = True
    return changed


@preflight.register("fix_router_names", priority=25)
def _fix_router_names(project_path: Path, diagnostics: list) -> bool:
    """Rename bare `router = APIRouter()` to `{resource}_router = APIRouter()`."""
    try:
        from app.services.deterministic_patcher import _patch_router_names
        result = _patch_router_names(project_path)
        return bool(result)
    except Exception:
        return False


@preflight.register("fix_param_order", priority=26)
def _fix_param_order(project_path: Path, diagnostics: list) -> bool:
    """Reorder route params: body params before Path/Query/Depends."""
    try:
        from app.services.deterministic_patcher import _patch_param_order
        result = _patch_param_order(project_path)
        return bool(result)
    except Exception:
        return False


@preflight.register("fix_missing_env", priority=30)
def _fix_missing_env(project_path: Path, diagnostics: list) -> bool:
    """Generate a .env skeleton if missing (uses sensible defaults)."""
    env_file = project_path / ".env"
    if env_file.exists():
        return False
    # Read main.py to find what env vars are used
    main_py = project_path / "app" / "main.py"
    used_vars: list[str] = []
    if main_py.exists():
        text = main_py.read_text(encoding="utf-8", errors="replace")
        used_vars = re.findall(r'os\.getenv\(["\']([A-Z_]+)["\']', text)
    # Also check all py files for os.getenv
    for f in (project_path / "app").rglob("*.py"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            used_vars.extend(re.findall(r'os\.getenv\(["\']([A-Z_]+)["\']', text))
        except Exception:
            pass

    defaults = {
        "DATABASE_URL":  "sqlite:///./app.db",
        "SECRET_KEY":    "dev-secret-key-change-in-production",
        "ALGORITHM":     "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "10080",
        "DEBUG":         "true",
    }
    lines = ["# Auto-generated .env skeleton by ForgeAI preflight"]
    seen: set[str] = set()
    for var in dict.fromkeys(used_vars):  # deduplicate, preserve order
        if var in seen:
            continue
        seen.add(var)
        default = defaults.get(var, "")
        lines.append(f"{var}={default}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


@preflight.register("fix_strip_passlib_imports", priority=35)
def _fix_strip_passlib_imports(project_path: Path, diagnostics: list) -> bool:
    """Remove passlib/werkzeug imports from Python files (incompatible libraries)."""
    changed = False
    passlib_import_re = re.compile(
        r"^(from passlib\S*|import passlib\S*|from werkzeug\.security\S*|import werkzeug\S*)\s*.*$",
        re.MULTILINE,
    )
    for f in (project_path / "app").rglob("*.py"):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            new_content = passlib_import_re.sub("", content)
            if new_content != content:
                f.write_text(new_content, encoding="utf-8")
                changed = True
        except Exception:
            pass
    return changed


@preflight.register("fix_cors_missing", priority=40)
def _fix_cors_missing(project_path: Path, diagnostics: list) -> bool:
    """Add CORS middleware to main.py if missing (required for frontend→backend)."""
    main_py = project_path / "app" / "main.py"
    if not main_py.exists():
        return False
    content = main_py.read_text(encoding="utf-8", errors="replace")
    if "CORSMiddleware" in content:
        return False
    cors_block = (
        '\nfrom fastapi.middleware.cors import CORSMiddleware\n'
        'app.add_middleware(\n'
        '    CORSMiddleware,\n'
        '    allow_origins=["*"],\n'
        '    allow_credentials=True,\n'
        '    allow_methods=["*"],\n'
        '    allow_headers=["*"],\n'
        ')\n'
    )
    # Insert after app = FastAPI(...)
    new_content = re.sub(
        r'(app\s*=\s*FastAPI\([^)]*\))',
        r'\1' + cors_block,
        content,
        count=1,
    )
    if new_content != content:
        main_py.write_text(new_content, encoding="utf-8")
        return True
    return False


@preflight.register("fix_missing_health_endpoint", priority=45)
def _fix_missing_health_endpoint(project_path: Path, diagnostics: list) -> bool:
    """Add GET /health endpoint to main.py if missing."""
    main_py = project_path / "app" / "main.py"
    if not main_py.exists():
        return False
    content = main_py.read_text(encoding="utf-8", errors="replace")
    if '"/health"' in content or "'/health'" in content:
        return False
    health_route = '\n@app.get("/health")\ndef health_check():\n    return {"status": "ok"}\n'
    main_py.write_text(content + health_route, encoding="utf-8")
    return True


@preflight.register("fix_database_py", priority=50)
def _fix_database_py(project_path: Path, diagnostics: list) -> bool:
    """Inject known-good database.py if missing or broken."""
    try:
        from app.services.database_patcher import patch_database_py
        return bool(patch_database_py(str(project_path)))
    except Exception:
        return False
