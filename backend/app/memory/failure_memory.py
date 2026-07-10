"""
V4 Failure Learning Memory

Every time the pipeline encounters an error (static, runtime, or frontend),
it records the pattern here. Patterns accumulate across ALL runs.

Before each generation, the top patterns are injected into every LLM prompt
as hard-won rules — so ForgeAI stops making the same mistakes.

Storage: backend/failure_memory/patterns.json  (human-readable, git-trackable)
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

_STORE_PATH = Path(__file__).parent.parent.parent / "failure_memory" / "patterns.json"


def _load() -> dict:
    if _STORE_PATH.exists():
        try:
            return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"patterns": {}, "total_runs": 0, "last_updated": None}


def _save(data: dict):
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Human-readable rules injected into prompts for each error type ──────────
_PATTERN_RULES = {
    "NoReferencedTableError": (
        "Always import EVERY SQLAlchemy model in main.py before calling "
        "Base.metadata.create_all(). Missing imports mean FK columns can't "
        "resolve their target table at startup."
    ),
    "InvalidDependsType": (
        "NEVER pass a bare built-in type (str, int, bool) to Depends(). "
        "FastAPI cannot introspect built-ins. Use a proper callable dependency "
        "such as oauth2_scheme or a custom function."
    ),
    "ModuleNotFoundError": (
        "Every module imported in app/ must actually exist as a file. "
        "If you reference app.schemas.discussion, create app/schemas/discussion.py. "
        "Double-check all import paths before writing any route file."
    ),
    "PydanticOrmModeDeprecated": (
        "Pydantic v2 replaced orm_mode with from_attributes. "
        "All schema Config classes MUST use `from_attributes = True`, never `orm_mode = True`."
    ),
    "CircularImport": (
        "Avoid circular imports. Models must not import from routes; "
        "routes must not import from each other. Use a service layer to break cycles."
    ),
    "RouterExportMismatch": (
        "Every route file MUST export a variable named exactly {resource}_router "
        "(e.g. user_router, task_router). Never name it just `router`."
    ),
    "ValidationError": (
        "When response_model is set, the endpoint MUST return a dict or ORM object "
        "that matches the schema exactly. Never return None for a typed response."
    ),
    "SQLAlchemyError": (
        "SQLAlchemy model columns that participate in foreign keys must have the "
        "referenced model imported into the same metadata. Use relationship() carefully "
        "and always import all models in main.py."
    ),
    "MissingEndpoint": (
        "Every endpoint listed in the architecture plan MUST be implemented in the "
        "corresponding route file. Do not skip any endpoint from the plan."
    ),
    "SyntaxError": (
        "Always verify Python syntax before writing a file. Common causes: "
        "unmatched parentheses, missing colons after def/class, incorrect indentation."
    ),
    "FrontendBuildError": (
        "In JSX files, never use backslash-escaped quotes in attribute values "
        "(placeholder=\\\"User\\\" is wrong; placeholder=\"User\" is correct). "
        "Every import must reference a file that actually exists."
    ),
    "StubHandler": (
        "Every route handler must contain a real implementation. "
        "Never leave a handler body as just `pass` or a bare comment."
    ),
    "WerkzeugImportError": (
        "NEVER import from werkzeug — it is a Flask dependency and is NOT installed. "
        "For password hashing use passlib: `from passlib.context import CryptContext` "
        "and add passlib[bcrypt] to requirements.txt."
    ),
    "SchemasNamespaceError": (
        "NEVER access schemas as a namespace: `schemas.user.UserResponse` fails at runtime "
        "because the submodule is not auto-imported. Always use direct imports: "
        "`from app.schemas.user import UserResponse`."
    ),
    "AsyncEngineError": (
        "NEVER use `async with engine.begin()` with a synchronous SQLAlchemy engine. "
        "Use `with engine.begin()` or call `Base.metadata.create_all(bind=engine)` "
        "at module level (not inside an async lifespan)."
    ),
    "RelationshipMissingError": (
        "NEVER call joinedload() on a model attribute unless you have declared "
        "a SQLAlchemy relationship() for it. If Note only has `notebook_id`, "
        "you must also define `notebook = relationship('Notebook')` before "
        "using `joinedload(Note.notebook)`."
    ),
    "MissingAuthSchemasFile": (
        "NEVER put auth schemas (LoginRequest, RegisterRequest, Token) in a separate "
        "app/schemas/auth.py file that you then import from auth_routes.py. "
        "Define all three directly inside auth_routes.py as Pydantic BaseModel classes. "
        "If app/schemas/auth.py doesn't exist, the import crashes the server on startup."
    ),
    "MissingRouteFile": (
        "EVERY route file that main.py imports MUST be generated. If main.py says "
        "`from app.routes.auth_routes import auth_router`, you MUST create auth_routes.py. "
        "If main.py says `from app.routes.user_routes import user_router`, create user_routes.py. "
        "Missing route files cause ModuleNotFoundError at startup — the server never starts."
    ),
    "TimestampNotNullError": (
        "NEVER define `created_at` or `updated_at` as `nullable=False` without a server_default. "
        "Without a default, every INSERT raises IntegrityError at runtime. "
        "ALWAYS write: `created_at = Column(DateTime, server_default=func.now(), nullable=False)` "
        "and `updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)`. "
        "Import func with: `from sqlalchemy.sql import func`"
    ),
    "NameErrorTypo": (
        "NEVER use `auth2_scheme` — the correct variable name is `oauth2_scheme`. "
        "Always define: `oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/token')` "
        "Typos in OAuth variable names cause NameError crashes at startup."
    ),
    "JourneyCRUDFailure": (
        "The register->login->create->edit->delete journey fails most often from "
        "silent contract drift, not crashes. Field names MUST be byte-identical "
        "across the model, the create/update schemas, and the frontend payload "
        "(if the model says `is_complete`, the schema and JSX must never say "
        "`completed`). Every endpoint MUST return the status code and body shape "
        "its architecture entry declares (POST that declares 201 returns 201 with "
        "the created object; DELETE that declares 204 returns an empty 204). "
        "Update endpoints MUST apply every field present in the update schema, "
        "not just one."
    ),
    "ConfigAttributeError": (
        "Every settings attribute accessed ANYWHERE (settings.SECRET_KEY, "
        "settings.DATABASE_URL, settings.ALGORITHM, ...) MUST be defined on the "
        "Settings class in app/config.py with a safe default. Accessing an "
        "undefined settings attribute crashes the server at import time."
    ),
    "ImportError": (
        "Only import names the source module ACTUALLY defines in this same "
        "response. Never import helpers (SignupRequest, _make_user, get_current_"
        "user) from another route file unless that file genuinely defines and "
        "exports them — shared helpers belong in app/auth.py or app/dependencies.py, "
        "imported from there by every consumer."
    ),
    "AttributeError": (
        "Never call methods on values that can be str or None: convert datetimes "
        "with datetime.fromisoformat() before .date()/.strftime(), guard Optional "
        "relationship access, and never shadow a builtin or module name with a "
        "local variable (e.g. a local named `date` or `list`)."
    ),
    "NotNullViolationError": (
        "Every model column declared nullable=False WITHOUT a server_default MUST "
        "appear as a required field in the create schema (and be sent by the "
        "frontend form). A NOT NULL column missing from the create path is a "
        "guaranteed IntegrityError on first insert."
    ),
}

# ── Failure taxonomy: pattern key -> pipeline stage ──────────────────────────
# Five stages, assigned automatically at record time so trends are visible per
# stage, not just per pattern: generation (files/endpoints missing at
# generation), build (static/compile/bundle), runtime (server boots then
# crashes), integration (app runs but user journeys fail), deployment.
_PATTERN_STAGES = {
    "MissingRouteFile": "generation", "MissingAuthSchemasFile": "generation",
    "MissingEndpoint": "generation", "StubHandler": "generation",
    "MonolithicSchemaError": "generation", "MissingFile": "generation",
    "RouterExportMismatch": "build", "SyntaxError": "build",
    "FrontendBuildError": "build", "IconNotExported": "build",
    "JSXNestedBrace": "build",
    "ModuleNotFoundError": "runtime", "ImportError": "runtime",
    "CircularImport": "runtime", "SQLAlchemyError": "runtime",
    "NoReferencedTableError": "runtime", "ConfigAttributeError": "runtime",
    "AttributeError": "runtime", "NotNullViolationError": "runtime",
    "TimestampNotNullError": "runtime", "PydanticSerializationError": "runtime",
    "ResponseValidationError": "runtime", "ValidationError": "runtime",
    "RelationshipModelNotImported": "runtime", "RelationshipMissingError": "runtime",
    "FastAPIError": "runtime", "AsyncEngineError": "runtime",
    "WerkzeugImportError": "runtime", "SchemasNamespaceError": "runtime",
    "NameErrorTypo": "runtime", "UserIdNotInjectedError": "runtime",
    "InvalidDependsType": "runtime", "ModelFieldMismatchError": "runtime",
    "PydanticOrmModeDeprecated": "runtime",
    "JourneyCRUDFailure": "integration", "WorkflowStepFailure": "integration",
    "BrowserUXFailure": "integration",
    "DeployFailure": "deployment", "HealthCheckFailure": "deployment",
}


def stage_of(pattern_key: str) -> str:
    return _PATTERN_STAGES.get(pattern_key, "runtime")


def classify_failure(error_text: str) -> tuple[str, str] | None:
    """Classify a raw failure string into (stage, pattern_key). The single
    classification point for every failure signal — validation errors,
    dominant_errors from generation_log, runtime tracebacks."""
    key = _classify_validation_error(error_text)
    if key:
        return stage_of(key), key
    return None


def record_run(
    project_name: str,
    validation_errors: list[str],
    runtime_error_type: str | None,
    frontend_build_failed: bool,
    score: int,
):
    """
    Call this after every generation to record what went wrong (if anything).
    Pass empty lists / None for clean runs — we still increment total_runs.
    """
    data = _load()
    data["total_runs"] = data.get("total_runs", 0) + 1
    data["last_updated"] = datetime.utcnow().isoformat()

    patterns = data.setdefault("patterns", {})

    def _bump(key: str, error_text: str):
        entry = patterns.setdefault(key, {"count": 0, "examples": [], "first_seen": datetime.utcnow().isoformat()})
        entry["count"] += 1
        entry["stage"] = stage_of(key)
        if len(entry["examples"]) < 5:
            entry["examples"].append({"project": project_name, "error": error_text[:200]})
        entry["last_seen"] = datetime.utcnow().isoformat()

    # ── Static validation errors ─────────────────────────────────────────
    for error in validation_errors:
        key = _classify_validation_error(error)
        if key:
            _bump(key, error)

    # ── Runtime errors ───────────────────────────────────────────────────
    if runtime_error_type and runtime_error_type not in ("Unknown",):
        _bump(runtime_error_type, runtime_error_type)

    # ── Frontend build failures ──────────────────────────────────────────
    if frontend_build_failed:
        _bump("FrontendBuildError", "vite build failed")

    _save(data)


def _classify_validation_error(error: str) -> str | None:
    """Map a raw failure string to a pattern key. Ordered: more specific
    signatures first, so e.g. an icon-export build error never falls through
    to the generic FrontendBuildError bucket."""
    checks = [
        ("is not exported", "IconNotExported"),
        ("expected '}'", "JSXNestedBrace"),
        ("router export mismatch", "RouterExportMismatch"),
        ("missing endpoint", "MissingEndpoint"),
        ("missing symbol", "ImportError"),
        ("missing file", "MissingFile"),
        ("stub handler", "StubHandler"),
        ("orm_mode", "PydanticOrmModeDeprecated"),
        ("syntaxerror", "SyntaxError"),
        ("no module named", "ModuleNotFoundError"),
        ("circular", "CircularImport"),
        ("journeycrudfailure", "JourneyCRUDFailure"),
        ("crud journey", "JourneyCRUDFailure"),
        ("workflow step failed", "WorkflowStepFailure"),
        ("error during build", "FrontendBuildError"),
        ("operationalerror", "SQLAlchemyError"),
        ("integrityerror", "NotNullViolationError"),
        ("not null", "NotNullViolationError"),
        ("sqlalchemy", "SQLAlchemyError"),
        ("attributeerror", "AttributeError"),
        ("importerror", "ImportError"),
        ("config attribute", "ConfigAttributeError"),
        ("responsevalidationerror", "ResponseValidationError"),
        ("validationerror", "ValidationError"),
        ("deploy failed", "DeployFailure"),
        ("health check", "HealthCheckFailure"),
    ]
    error_lower = error.lower()
    for substr, key in checks:
        if substr in error_lower:
            return key
    return None


def get_top_patterns(min_count: int = 2, max_patterns: int = 8) -> list[dict]:
    """Return the most frequent failure patterns that have a known rule."""
    data = _load()
    patterns = data.get("patterns", {})

    results = []
    for key, entry in patterns.items():
        if entry["count"] >= min_count and key in _PATTERN_RULES:
            results.append({
                "key": key,
                "count": entry["count"],
                "rule": _PATTERN_RULES[key],
            })

    results.sort(key=lambda x: x["count"], reverse=True)
    return results[:max_patterns]


def build_prompt_injection() -> str:
    """
    Returns a block of learned rules to prepend to any LLM prompt.
    Returns empty string if there's nothing worth injecting yet.
    """
    patterns = get_top_patterns()
    if not patterns:
        return ""

    lines = ["[LEARNED FROM PAST FAILURES — these mistakes have occurred repeatedly:]"]
    for p in patterns:
        lines.append(f"  [{p['count']}x] {p['key']}: {p['rule']}")
    lines.append("")
    return "\n".join(lines)


def print_summary():
    """Print a human-readable summary of the failure memory."""
    data = _load()
    print(f"\n=== FAILURE MEMORY SUMMARY ({data.get('total_runs', 0)} total runs) ===")
    patterns = data.get("patterns", {})
    if not patterns:
        print("  No failures recorded yet.")
        return
    sorted_patterns = sorted(patterns.items(), key=lambda x: x[1]["count"], reverse=True)
    for key, entry in sorted_patterns:
        print(f"  {entry['count']:3d}x  {key}")
    print()
