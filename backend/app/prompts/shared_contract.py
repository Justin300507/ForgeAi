FASTAPI_CONTRACT = """
========================================
PROJECT CONTRACT — DO NOT DEVIATE
========================================

- Framework: FastAPI only. No Flask. No Django.
- Every route file MUST use APIRouter, never Flask Blueprint.
- Router naming convention: {resource}_router (NEVER just `router`)
  Example: app/routes/user_routes.py defines: user_router = APIRouter()
  Example: app/routes/task_routes.py defines: task_router = APIRouter()
- NEVER list query-parameter variants as separate endpoints.
  WRONG: GET /tasks?tag={tag_id}  — this is NOT a separate route
  RIGHT: GET /tasks               — use Optional[X] = Query(None) in the handler
- NEVER use prefix= in APIRouter(). Route decorators MUST include the full path.
  WRONG: task_router = APIRouter(prefix="/tasks")
         @task_router.get("/")
  RIGHT: task_router = APIRouter()
         @task_router.get("/tasks")
- main.py imports and registers routers like:
  from app.routes.user_routes import user_router
  app.include_router(user_router)
- Database models inherit from Base (SQLAlchemy), never BaseModel (Pydantic).
- API schemas inherit from BaseModel, live in app/schemas/, never in app/models/.
- All imports are absolute, starting with app.
- Paths must start with app/ and use forward slashes only.
- For any nullable database column, the corresponding Pydantic schema
  field MUST be typed Optional[Type] = None, never bare Type = None.
- NEVER import directly from a package root, e.g. `from app.models import X`
  or `from app.schemas import X`. ALWAYS import from the specific submodule:
  `from app.models.user import User`, `from app.schemas.user import UserCreate`.
- NEVER use Flask-SQLAlchemy style: no `db = SQLAlchemy()`, no
  `class X(db.Model)`, no `from app.database import db`.
- The FastAPI DB dependency function is ALWAYS named exactly
  `get_db`, never `db`. Database models inherit directly from
  `Base`: `from app.database import Base` then `class X(Base):`.
- `get_db` MUST be imported from `app.database`, NOT from `app.dependencies.database`.
  CORRECT:  from app.database import get_db
  WRONG:    from app.dependencies.database import get_db
  WRONG:    from app.dependencies import get_db
- If you ever need to suppress a type checker, it MUST be written as
  a comment: `# type: ignore`. NEVER write `type: ignore` as a bare
  statement — that is not a comment, it is invalid/meaningless code.
- Every route handler MUST contain a real, working implementation —
  never a placeholder body of just `pass` or a comment. If
  response_model is set, the function MUST actually return a value
  matching that shape, not None.
- When using Path(), Query(), or Depends() as defaults, any parameter
  WITHOUT a default must come BEFORE them in the function signature.
  This is a Python SyntaxError if violated, not just a style issue.
  Wrong: def update_task(task_id: int = Path(...), task_in: TaskUpdate, db: Session = Depends(get_db))
  Right: def update_task(task_in: TaskUpdate, task_id: int = Path(...), db: Session = Depends(get_db))
  Rule: put the parameter with NO default (like a request body) FIRST,
  then all Path/Query/Depends parameters after it.
  - If a function uses `await` anywhere in its body, it MUST be
  declared `async def`, never plain `def`.
- SQLAlchemy's synchronous Session (db.query, db.commit, db.add) is
  NEVER used with async/await. Do not mix sync Session calls with
  async def or await unless using an actual async ORM driver.
- Pydantic schemas MUST use `from_attributes = True` in Config, NOT `orm_mode = True`.
  `orm_mode` was renamed in Pydantic v2 and causes warnings/errors.
  CORRECT:
    class UserResponse(BaseModel):
        id: int
        class Config:
            from_attributes = True
  WRONG:
    class UserResponse(BaseModel):
        id: int
        class Config:
            orm_mode = True
- NEVER use a bare Python built-in type (str, int, bool) as a FastAPI Depends() argument.
  FastAPI cannot introspect built-in types and will raise ValueError at startup.
  WRONG: def endpoint(token: str = Depends(str))
  RIGHT: def endpoint(token: str = Depends(oauth2_scheme))
- NEVER use werkzeug for password hashing — it is a Flask dependency and is NOT installed.
  Use passlib instead:
  WRONG: from werkzeug.security import generate_password_hash, check_password_hash
  RIGHT: from passlib.context import CryptContext
         pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
         hashed = pwd_context.hash(plain_password)
         valid  = pwd_context.verify(plain_password, hashed)
  Add passlib[bcrypt] to requirements.txt.
- NEVER access schemas as a namespace object (schemas.user.X). Always use direct imports.
  WRONG: response_model=schemas.user.UserResponse
         import app.schemas as schemas; schemas.user.UserResponse
  RIGHT: from app.schemas.user import UserResponse
         response_model=UserResponse
- NEVER use `async with engine.begin()` with a synchronous SQLAlchemy engine.
  Synchronous engines only support `with engine.begin()`. Using async context managers
  on a sync engine raises TypeError at startup.
  WRONG: async with engine.begin() as conn: ...
  RIGHT: with engine.begin() as conn: ...
  Or move table creation to module level: Base.metadata.create_all(bind=engine)
- NEVER use joinedload() on an attribute that is not a declared relationship().
  If you write joinedload(Note.notebook), then Note MUST have:
    notebook = relationship("Notebook", back_populates="notes")
  Otherwise use joinedload on a FK column that doesn't map to a relationship,
  which raises AttributeError at runtime.
- NEVER import auth schemas from a separate file (app.schemas.auth). Define
  LoginRequest, RegisterRequest, and Token schemas directly inside auth_routes.py.
  If you write `from app.schemas.auth import LoginRequest` but never generate
  app/schemas/auth.py, the server crashes immediately on startup.
  RIGHT: Define LoginRequest, RegisterRequest, Token as Pydantic BaseModel classes
  at the TOP of app/routes/auth_routes.py.
- EVERY file that main.py imports MUST actually be generated. If main.py has
  `from app.routes.auth_routes import auth_router`, you MUST generate auth_routes.py.
  If main.py has `from app.routes.user_routes import user_router`, you MUST generate
  user_routes.py. Missing route files crash the server on startup with ModuleNotFoundError.
- All SQLAlchemy ForeignKey references MUST use the string form and match an actual
  table name: ForeignKey('users.id') not ForeignKey(User.id). Every table referenced
  by a ForeignKey must have its model file imported in main.py so Base.metadata
  can create it. Missing imports cause NoReferencedTableError on startup.
- NEVER define `created_at` or `updated_at` as `nullable=False` without a server default.
  Without a default, every INSERT raises IntegrityError. ALWAYS use:
  WRONG: created_at = Column(DateTime, nullable=False)
  RIGHT: created_at = Column(DateTime, server_default=func.now(), nullable=False)
         updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
  Add `from sqlalchemy.sql import func` at the top of every model file that uses timestamps.
- NEVER use `auth2_scheme` — the correct name is `oauth2_scheme`.
  Define it as: oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
"""