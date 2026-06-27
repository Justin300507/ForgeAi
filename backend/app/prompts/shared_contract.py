FASTAPI_CONTRACT = """
========================================
PROJECT CONTRACT — DO NOT DEVIATE
========================================

MANDATORY IN EVERY main.py (non-negotiable — validator will reject missing items)
- CORS middleware (import + add_middleware):
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
- Health endpoint:
    @app.get("/health")
    def health():
        return {"status": "ok"}

MANDATORY ROUTES IN EVERY GENERATED PROJECT:
- POST /seed  (in app/routes/seed_routes.py)
    Inserts realistic demo data for every table. Called once after deploy.
    MUST insert at least 5 records per major entity using hardcoded realistic values.
    On repeat calls, skip rows that already exist (use try/except IntegrityError or check first).
    Example seed records — use domain-appropriate names, NOT "User 1" or "test@test.com":
      Gym: members named "Alex Chen", "Maria Garcia", "James Kim"; classes "7:00 AM Yoga", "6:30 PM HIIT"
      Finance: transactions "Whole Foods $89.40", "Netflix $15.99", "Rent $1,200"
      Tasks: "Launch Q3 campaign" (High/In Progress), "Fix login bug" (Critical/Done)

- GET /stats/summary  (in app/routes/stats_routes.py or merged into relevant module)
    Returns aggregate counts and key metrics the dashboard frontend will display.
    Must return a JSON object with at least 4 numeric fields.
    Example: {{"total_members": 42, "active_today": 8, "revenue_this_month": 3200, "classes_today": 5}}

LIST ENDPOINTS (any GET that returns multiple records)
- MUST include pagination params: `limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)`
- Apply them: `return db.query(Model).offset(offset).limit(limit).all()`
- Import: `from fastapi import Query`

SCHEMAS — required string fields
- Use `Field(min_length=1)` on required string fields in Create schemas:
    name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    email: str = Field(min_length=1)
- Import: `from pydantic import BaseModel, Field`

ROUTE ERROR HANDLING
- Return 404 Not Found when a record doesn't exist — NEVER let it crash with 500:
    obj = db.query(Model).filter(Model.id == id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Not found")

FRAMEWORK & ROUTING
- FastAPI only. No Flask, no Django, no Blueprint.
- Every route file: `{resource}_router = APIRouter()` (e.g. user_router, task_router). NEVER just `router`.
- NEVER use prefix= in APIRouter(). Route decorators include the full path.
  RIGHT: task_router = APIRouter(); @task_router.get("/tasks")
- main.py: `from app.routes.user_routes import user_router; app.include_router(user_router)`
- NEVER list query-param variants as separate endpoints. Use Optional[X] = Query(None) in one handler.

IMPORTS & PATHS
- All imports absolute, starting with `app.`. Paths start with `app/`, forward slashes only.
- NEVER import from package root: `from app.models import X` or `from app.schemas import X`.
  ALWAYS import from the specific submodule: `from app.models.user import User`.
- `get_db` MUST be imported from `app.database`, not `app.dependencies.database` or `app.dependencies`.

MODELS & SCHEMAS
- SQLAlchemy models: inherit from Base, live in app/models/. Never BaseModel (Pydantic).
- Pydantic schemas: inherit from BaseModel, live in app/schemas/. Never in app/models/.
- Nullable DB column → schema field MUST be `Optional[Type] = None`, never `Type = None`.
- Pydantic v2: use `from_attributes = True` in Config, NOT `orm_mode = True`.
- NEVER generate monolithic `app/schemas/schemas.py`. ALWAYS one file per resource:
  app/schemas/user.py, app/schemas/account.py, etc. Routes import from the specific submodule.
- NEVER access schemas as namespace: `schemas.user.X`. Use direct imports.

DATABASE
- NEVER use Flask-SQLAlchemy: no `db = SQLAlchemy()`, `class X(db.Model)`, `from app.database import db`.
- DB dependency always named `get_db`. Models inherit from `Base`: `from app.database import Base`.
- ForeignKey: always string form matching actual table name: `ForeignKey('users.id')`.
  Every FK-referenced model file MUST be imported in main.py so Base.metadata can create it.
- SQLAlchemy relationship() MUST use string names: `relationship("OpeningHour")` NOT `relationship(OpeningHour)`.
  Every model referenced by string in a relationship MUST be imported in main.py.
  WRONG: main.py never imports opening_hour.py → `relationship("OpeningHour")` crashes at startup.
  RIGHT: `from app.models.opening_hour import OpeningHour  # noqa: F401` in main.py.
- NEVER import other model classes inside a model file. Models must NOT do `from app.models.user import User`.
  Use ForeignKey("users.id") with the string table name — no import needed.
  WRONG: companies.py does `from app.models.user import User` → duplicate table registration crash.
  RIGHT: companies.py just uses `ForeignKey("users.id")` — main.py imports all models independently.
- Timestamps: NEVER `nullable=False` without a server default.
  RIGHT: `created_at = Column(DateTime, server_default=func.now(), nullable=False)`
  RIGHT: `updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)`
  Add `from sqlalchemy.sql import func` in every model file using timestamps.
- NEVER use `joinedload()` on an attribute that is not a declared `relationship()`.
- NEVER use `async with engine.begin()` with a synchronous engine. Use `with engine.begin()`.
- SQLAlchemy sync Session (`db.query`, `db.commit`) is NEVER used with `async def` / `await`.

AUTH
- All files main.py imports MUST be generated. Missing route files → ModuleNotFoundError on startup.
- Define LoginRequest, RegisterRequest, Token schemas directly inside auth_routes.py.
  NEVER `from app.schemas.auth import LoginRequest` unless you generate app/schemas/auth.py.
- `oauth2_scheme` (not `auth2_scheme`): `oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")`
- NEVER use a bare built-in type as Depends(): `Depends(str)` crashes. Use `Depends(oauth2_scheme)`.
- NEVER hardcode SECRET_KEY in route files. ALWAYS import from app.utils.auth:
    from app.utils.auth import create_access_token, verify_password, get_password_hash, get_current_user, oauth2_scheme
  The auth utils file already exists (pre-generated). SECRET_KEY is loaded from env there.
- NEVER define SECRET_KEY, ALGORITHM, pwd_context, or ACCESS_TOKEN_EXPIRE_MINUTES in route files.
  These are ONLY in app/utils/auth.py — import from there, never redefine.
- NEVER use werkzeug or passlib. passlib is broken on Python 3.13+ (Render/Railway default). Use bcrypt directly:
    import bcrypt
    def get_password_hash(password: str) -> str: return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    def verify_password(plain: str, hashed: str) -> bool: return bcrypt.checkpw(plain.encode(), hashed.encode())
  In requirements.txt use `bcrypt` (NOT `passlib[bcrypt]`).
- Routes that CREATE/UPDATE user-owned resources (model with `user_id` NOT NULL FK) MUST:
  1. Include `current_user: User = Depends(get_current_user)` in the signature
  2. Set `obj.user_id = current_user.id` before `db.add(obj)`
- NEVER import a function from app.services.auth or app.utils.auth unless it is explicitly defined there:
  • app/utils/auth.py: verify_password, get_password_hash, create_access_token, get_current_user, oauth2_scheme
  • app/services/auth.py: get_user_by_email, get_user_by_username, authenticate_user
- auth_routes.py must import `from app.models.user import User` inside the function body (lazy),
  NOT at module level. Module-level model imports in route files cause duplicate table crashes
  when main.py also imports models. Use `current_user: User = Depends(get_current_user)` only —
  the type hint can use a string: `current_user: "User"` to avoid the module-level import.
- NEVER use Python line-continuation backslash at end of lines in generated code.
  Always use parentheses for multi-line expressions instead.
- NEVER use Pydantic v1 `constr()`. Use `Field(min_length=X, pattern=r"...")` instead.
- Regex patterns in Pydantic `Field(pattern=...)` MUST NOT use lookahead `(?=...)` or
  lookbehind `(?<=...)` — Pydantic v2 uses Rust regex which does not support them.
- NEVER use smart quotes (' ' " ") in Python strings or JSX. Always use ASCII ' and ".

ROUTE HANDLERS
- Every handler must have a real implementation. Never `pass` or placeholder bodies.
- Parameter order: body params first, then `Path()`/`Query()`/`Depends()` params.
  RIGHT: `def update_task(task_in: TaskUpdate, task_id: int = Path(...), db: Session = Depends(get_db))`
- `async def` only if the body uses `await`. Never mix sync Session calls with async def.
- `# type: ignore` is a comment. `type: ignore` as a bare statement is invalid code.
"""
