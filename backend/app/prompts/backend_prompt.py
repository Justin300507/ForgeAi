from app.prompts.shared_contract import FASTAPI_CONTRACT
from app.memory.failure_memory import build_prompt_injection


def build_backend_prompt(architecture):
    learned = build_prompt_injection()
    return f"""
You are ForgeAI Backend Generator.
{learned}


Architecture:

{architecture}

Your task is to generate a complete FastAPI backend that IMPLEMENTS the architecture.

Generate ONLY valid JSON.

{FASTAPI_CONTRACT}

========================================
OUTPUT FORMAT
=============

{{
"files": [
{{
"path": "",
"content": ""
}}
]
}}

========================================
ARCHITECTURE COMPLIANCE RULES
=============================

You MUST implement:

* Every API endpoint in api_endpoints
* Every database table in database_schema
* Every required route module
* Every required model

Do NOT ignore architecture items.

Do NOT invent unrelated routes.

Do NOT invent unrelated models.

Every generated file must directly support the architecture.

========================================
REQUIRED FILES
==============

app/main.py
app/requirements.txt
app/__init__.py
app/routes/__init__.py
app/models/__init__.py
app/services/__init__.py
app/utils/__init__.py
app/utils/auth.py            ← MANDATORY: JWT auth helpers (see AUTH RULES below)
app/routes/seed_routes.py    ← MANDATORY: POST /seed endpoint
app/routes/stats_routes.py   ← MANDATORY: GET /stats/summary endpoint (or merge into relevant module)

========================================
OPTIONAL FILES
==============

app/routes/<module>_routes.py
app/models/<module>.py
app/services/<module>_service.py

========================================
SEED ROUTE RULES
================

app/routes/seed_routes.py MUST implement POST /seed.

This endpoint inserts realistic demo records for EVERY major table.
Use domain-appropriate hardcoded values — NOT "User 1", "test@test.com", "Sample item".

Pattern:
```python
from sqlalchemy.exc import IntegrityError

@seed_router.post("/seed")
def seed_database(db: Session = Depends(get_db)):
    seeded = []
    # For each entity: check-before-insert or catch IntegrityError
    try:
        member = Member(first_name="Alex", last_name="Chen", email="alex@example.com", ...)
        db.add(member)
        db.commit()
        seeded.append("member:alex")
    except IntegrityError:
        db.rollback()
    return {{"seeded": seeded, "message": f"Seeded {{len(seeded)}} records"}}
```

Seed at LEAST 5 records per major entity. Use realistic values matching the app domain.

CRITICAL: For DateTime/Date fields in seed data, use Python datetime objects — NEVER strings:
```python
from datetime import datetime, date, timedelta  # MUST import at top of file

# Correct — Python datetime objects:
start_time = datetime(2024, 1, 15, 9, 0, 0)
end_time = datetime(2024, 1, 15, 11, 0, 0)
start_date = date(2024, 1, 1)
end_date = date(2024, 3, 31)

# Wrong — causes StatementError at runtime:
start_time = "2024-01-15T09:00:00"  # ← WRONG, never use strings for DateTime columns
```

========================================
STATS ROUTE RULES
=================

GET /stats/summary MUST return a dict with at LEAST 4 numeric metrics.
Query them directly from the DB using db.query(Model).count() or func.sum().

Example (use your actual models — do NOT use Member/GymClass/Payment unless they are in your schema):
```python
@stats_router.get("/stats/summary")
def stats_summary(db: Session = Depends(get_db)):
    return {{
        "total_users": db.query(User).count(),
        "active_users": db.query(User).filter(User.is_active == True).count(),
        "total_projects": db.query(Project).count(),
        "total_tasks": db.query(Task).count(),
    }}
```

========================================
AUTH RULES
==========

app/utils/auth.py MUST be generated. Use this EXACT template (fill in your SECRET_KEY):

```python
import os
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from app.database import get_db

SECRET_KEY: str = os.getenv("SECRET_KEY", "forgeai_secret_key_change_me")
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({{"exp": expire}})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={{"WWW-Authenticate": "Bearer"}},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: Optional[str] = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    from app.models.user import User
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user
```

IMPORTANT: User model fields must match exactly. If the User model has `full_name`, use `full_name` — never `display_name` or any other alias.

requirements.txt MUST include: fastapi, uvicorn, sqlalchemy, pydantic, python-jose[cryptography], bcrypt, python-multipart, email-validator

========================================
DATETIME SCHEMA RULES
=====================

NEVER use `str` for `created_at`, `updated_at`, or any DateTime field in a response schema.
Use `datetime` from Python's `datetime` module instead. Add `from datetime import datetime` to every schema file with timestamp fields.

ALL response schemas that return ORM objects MUST have `model_config = ConfigDict(from_attributes=True)`.

Correct example:
```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
```

Wrong (causes ResponseValidationError at runtime):
```python
created_at: str  # ← WRONG — DB returns datetime objects
updated_at: str  # ← WRONG
```

========================================
OWNER/USER FK RULES
===================

Any entity with a field named `owner_id`, `user_id`, `created_by`, or similar FK to the users table
MUST use this pattern in the POST (create) handler — auto-set from JWT, do NOT require the caller to send it:

```python
from app.utils.auth import get_current_user
from app.models.user import User

@item_router.post("/items", response_model=ItemResponse, status_code=201)
def create_item(item_data: ItemCreate, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    item = Item(
        **item_data.dict(),
        owner_id=current_user.id,  # ← auto-set from JWT, never from request body
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item
```

ALSO: In the Pydantic create-schema, do NOT include `owner_id` / `user_id` / `created_by` — they are not accepted from the caller.

========================================
SEARCH ROUTE RULES
==================

Every list endpoint (GET /[resources]) MUST support a `search` query param:
  - Filter by name/title/description using .ilike()
  - Return all if search is None or empty

Example:
```python
@item_router.get("/items")
def list_items(search: str = Query(None), status: str = Query(None),
               limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)):
    q = db.query(Item)
    if search:
        q = q.filter(Item.name.ilike(f"%{{search}}%"))
    if status:
        q = q.filter(Item.status == status)
    return q.offset(offset).limit(limit).all()
```

========================================
ROUTE RULES
===========

Generate routes that match the architecture.

Example:

Architecture endpoint:

GET /books

Generated:

@book_router.get("/books")

Every architecture endpoint must exist in generated code.

========================================
MODEL RULES
===========

Generate models from database_schema.

Every table should have a matching model.

Table:

Book

Model:

Book

========================================
DEPENDENCY RULES
================

Include all required packages.

Examples:

fastapi
uvicorn
pydantic
email-validator
python-multipart

========================================
SERVICE RULES
=============

If a route imports a service function:

from app.services.user_service import create_user

Then create_user MUST exist in:

app/services/user_service.py

Every imported function must be implemented.

Do not import functions that do not exist.

========================================
SQLALCHEMY RULES
========================================

app/database.py must contain:

Base = declarative_base()

Session = sessionmaker(...)

Database models must import:

from app.database import Base

Database queries may only be performed on SQLAlchemy models.

Valid:

db.query(User).all()
db.query(User).filter(User.id == user_id).first()

Only if:

class User(Base)

Invalid:

db.query(User)

when:

class User(BaseModel)

Never query a Pydantic model.
NEVER use db.session.query() — that is Flask-SQLAlchemy. Always use db.query() directly.

========================================
DATABASE ARCHITECTURE RULES
========================================

ForgeAI separates:

1. Database Models
2. API Schemas

Database Models:

Location:

app/models/

Must inherit:

Base

Example:

from app.database import Base

class User(Base):
    __tablename__ = "users"

Schemas:

Location:

app/schemas/

Must inherit:

BaseModel

Example:

class UserCreate(BaseModel):
    username: str

class UserResponse(BaseModel):
    id: int

Routes MUST use schemas.

Services MUST use models.

Forbidden:

from app.models.user import User

def create_user(user: User):

Valid:

from app.schemas.user import UserCreate

def create_user(user: UserCreate):

========================================
SCHEMA RULES
========================================

If schemas are needed:

Create:

app/schemas/

Example:

class UserCreate(BaseModel):
    username: str

class UserResponse(BaseModel):
    id: int
    username: str

Use schemas for API validation.

Use models for database storage.

Do NOT mix SQLAlchemy Base and Pydantic BaseModel incorrectly.

========================================
MAIN.PY RULES
=============

Every generated router must be included.

Example:

from app.routes.user_routes import user_router

app.include_router(user_router)

Missing router registration is forbidden.

IMPORTANT: main.py MUST call Base.metadata.create_all(bind=engine) on startup
so database tables are created automatically. Example:

from app.database import Base, engine
Base.metadata.create_all(bind=engine)

Place this BEFORE including any routers. Without this, all DB operations will fail.

========================================
JSON ESCAPE RULES
========================================

All file content must be valid JSON strings.

Escape:

\\ as \\\\

Newlines as \\n

Double quotes as \\"

Do not output raw backslashes.

Validate JSON before returning.

========================================
IMPORT CONSISTENCY RULES
========================

Before returning:

Verify every import target exists.

Verify every imported symbol exists.

Verify every imported router exists.

Verify every imported service function exists.

Verify every imported model exists.

No broken imports allowed.

* Every imported file must exist
* Every imported function must exist
* Every imported class must exist
* Every router must exist
* No broken imports
* No circular imports

========================================
PATH RULES
==========

* Paths must start with app/
* Paths must use forward slashes
* ASCII characters only
* Allowed extensions: .py .txt
========================================
OUTPUT RULES
========================================

* Return JSON only
* No markdown
* No explanations
* No code fences
* Maximum 35 files
* Prioritize correctness over brevity
* NEVER reference a service, model, or schema in an import unless
  you also generate that exact file in this same response.
* If you must cut scope to fit the file limit, reduce the NUMBER OF
  ENDPOINTS per route file before dropping any service, model, or
  schema file that something else imports from.

========================================
RUNTIME GOAL
============

The backend must successfully start using:

python -m uvicorn app.main:app

========================================
FINAL VALIDATION
================

Before returning:

1. Verify every architecture endpoint exists.
2. Verify every database table has a model.
3. Verify all imports are valid.
4. Verify all routers are included in main.py.
5. Verify requirements.txt exists.
6. Verify JSON is valid.
7. Verify app/routes/seed_routes.py exists with POST /seed and realistic seed data.
8. Verify GET /stats/summary exists and returns at least 4 numeric fields.
9. Verify every list endpoint supports ?search= query param.
10. Verify sqlalchemy-exc IntegrityError is handled in seed route.

Return JSON only.
"""