"""
V6 Parallel Backend Prompts

One focused prompt per file type. Each prompt gives the LLM a single
clear job rather than asking it to produce 20+ files at once.
"""
from app.prompts.shared_contract import FASTAPI_CONTRACT


def build_model_prompt(
    entity_name: str,
    columns: list[dict],
    all_tables: list[dict],
    contract: str,
    failure_memory: str,
) -> str:
    col_lines = "\n".join(
        f"  - {c['name']}: {c['type']}"
        + (" (PK)" if c.get("is_primary_key") else "")
        + (" NOT NULL" if not c.get("is_nullable", True) else "")
        for c in columns
    )
    other_tables = [t["table_name"] for t in all_tables if t["table_name"] != entity_name]
    fk_hint = (
        f"Other tables you may FK into: {', '.join(other_tables)}"
        if other_tables else ""
    )

    return f"""You are a ForgeAI Backend Engineer. Generate EXACTLY ONE SQLAlchemy model file.

{failure_memory}

{contract}

FILE TO GENERATE: app/models/{entity_name.lower()}.py

TABLE: {entity_name}
COLUMNS:
{col_lines}
{fk_hint}

RULES:
- Class name: {entity_name.title().replace('_', '')} inheriting from Base
- Import: from app.database import Base
- Use Column() for every field. ForeignKey uses string form: ForeignKey('tablename.id')
- Add relationship() for every FK column so joinedload works
- Do NOT define get_db or Base here — those are in app/database.py
- No Pydantic. Only SQLAlchemy.

Return ONLY valid JSON (no markdown, no code fences):
{{"path": "app/models/{entity_name.lower()}.py", "content": "<escaped python code>"}}"""


def build_schema_prompt(
    resource: str,
    model_content: str,
    endpoints: list[dict],
    contract: str,
    failure_memory: str,
) -> str:
    ep_lines = "\n".join(
        f"  {e['method']} {e['path']} — {e.get('description', '')}"
        for e in endpoints
    )

    return f"""You are a ForgeAI Backend Engineer. Generate EXACTLY ONE Pydantic schema file.

{failure_memory}

{contract}

FILE TO GENERATE: app/schemas/{resource}.py

ENDPOINTS USING THIS RESOURCE:
{ep_lines}

CORRESPONDING MODEL (for field reference):
{model_content or '(not yet generated)'}

RULES:
- Create: {resource.title().replace('_', '')}Create, {resource.title().replace('_', '')}Update, {resource.title().replace('_', '')}Response
- All inherit from pydantic BaseModel
- Response schema includes id: int and model_config = ConfigDict(from_attributes=True)
- Nullable DB columns -> Optional[Type] = None
- Do NOT inherit from Base. No SQLAlchemy here.
- If auth is needed, also define LoginRequest, RegisterRequest, Token in this file — NOT in a separate auth.py

Return ONLY valid JSON (no markdown, no code fences):
{{"path": "app/schemas/{resource}.py", "content": "<escaped python code>"}}"""


def build_route_prompt(
    resource: str,
    route_file: str,
    endpoints: list[dict],
    model_contents: dict[str, str],
    schema_contents: dict[str, str],
    contract: str,
    failure_memory: str,
    project_name: str = "",
) -> str:
    ep_lines = "\n".join(
        f"  {e['method']} {e['path']} — {e.get('description', '')}"
        for e in endpoints
    )

    models_summary = "\n".join(
        f"  {path}: {content[:300]}..." if len(content) > 300 else f"  {path}: {content}"
        for path, content in list(model_contents.items())[:5]
    )
    schemas_summary = "\n".join(
        f"  {path}: {content[:300]}..." if len(content) > 300 else f"  {path}: {content}"
        for path, content in list(schema_contents.items())[:5]
    )

    return f"""You are a ForgeAI Backend Engineer. Generate EXACTLY ONE FastAPI route file.

{failure_memory}

{contract}

FILE TO GENERATE: {route_file}
PROJECT: {project_name}
RESOURCE: {resource}

ENDPOINTS TO IMPLEMENT (implement ALL of them):
{ep_lines}

AVAILABLE MODELS:
{models_summary or '  (none generated yet — define inline if needed)'}

AVAILABLE SCHEMAS:
{schemas_summary or '  (none generated yet — define inline if needed)'}

RULES:
- Router variable: {resource}_router = APIRouter()
- Import get_db from app.database
- Use Depends(get_db) for all DB operations
- Auth routes: define LoginRequest/RegisterRequest/Token schemas INLINE in this file, NOT imported from app.schemas.auth
- oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token") — NEVER auth2_scheme
- Every endpoint in the list above MUST be implemented. No stubs, no `pass`.
- Body parameters first, then Path()/Query()/Depends() parameters

Return ONLY valid JSON (no markdown, no code fences):
{{"path": "{route_file}", "content": "<escaped python code>"}}"""


def build_main_prompt(
    route_files: list[str],
    project_name: str,
    contract: str,
) -> str:
    imports = "\n".join(
        f"from {f.replace('/', '.').replace('.py', '')} import "
        f"{f.split('/')[-1].replace('.py', '').replace('_routes', '')}_router"
        for f in route_files
    )
    includes = "\n".join(
        f"app.include_router("
        f"{f.split('/')[-1].replace('.py', '').replace('_routes', '')}_router)"
        for f in route_files
    )

    return f"""You are a ForgeAI Backend Engineer. Generate app/main.py for a FastAPI project.

{contract}

PROJECT: {project_name}
ROUTE FILES GENERATED:
{chr(10).join('  ' + f for f in route_files)}

REQUIRED IMPORTS (use exactly these):
{imports}

REQUIRED ROUTER REGISTRATIONS:
{includes}

RULES:
- Import ALL routers listed above — every single one
- Call Base.metadata.create_all(bind=engine) BEFORE including routers
- Add CORS middleware (allow_origins=["*"] for development)
- Include GET /health endpoint returning {{"status": "ok"}}
- No extra routers. No missing routers.

Return ONLY valid JSON (no markdown, no code fences):
{{"path": "app/main.py", "content": "<escaped python code>"}}"""


def build_requirements_content(architecture: dict) -> str:
    """Generate requirements.txt content deterministically — no LLM needed."""
    base = [
        "fastapi",
        "uvicorn[standard]",
        "sqlalchemy",
        "pydantic[email]",
        "python-jose[cryptography]",
        "passlib[bcrypt]",
        "python-multipart",
        "email-validator",
    ]
    # Add extras based on architecture hints
    arch_str = str(architecture).lower()
    if "redis" in arch_str:
        base.append("redis")
    if "celery" in arch_str:
        base.append("celery")
    if "boto" in arch_str or "s3" in arch_str:
        base.append("boto3")
    return "\n".join(base)
