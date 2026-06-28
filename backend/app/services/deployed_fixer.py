"""
Fixes errors found by deployed_checker in a live deployed app.
Applies deterministic file patches then pushes to GitHub (triggering Render redeploy).
"""
import os
import re
from pathlib import Path
from typing import Optional


# ── Auth template (same as in deterministic_patcher) ─────────────────────────

_AUTH_UTILS_TEMPLATE = '''\
import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db

SECRET_KEY = os.getenv("SECRET_KEY", "changeme-dev-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    from app.models.user import User
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise credentials_exception
    except Exception:
        raise credentials_exception
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise credentials_exception
    return user
'''

_AUTH_ROUTES_TEMPLATE = '''\
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.auth import (
    create_access_token, verify_password, get_password_hash,
    get_current_user, oauth2_scheme,
)

auth_router = APIRouter()


class SignupRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=6)
    display_name: str = ""


class AuthResponse(BaseModel):
    id: int
    email: str
    display_name: str = ""


@auth_router.post("/auth/signup", response_model=AuthResponse, status_code=201)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    from app.models.user import User
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=req.email,
        password_hash=get_password_hash(req.password),
        display_name=req.display_name or req.email.split("@")[0],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return AuthResponse(id=user.id, email=user.email,
                        display_name=getattr(user, "display_name", ""))


@auth_router.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    from app.models.user import User
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(data={"sub": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": user.email,
        "display_name": getattr(user, "display_name", ""),
    }


@auth_router.get("/auth/me")
def me(current_user=Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "display_name": getattr(current_user, "display_name", None),
        "role": getattr(current_user, "role", None),
    }
'''


# ── Fix helpers ───────────────────────────────────────────────────────────────

def _fix_cors(main_py_path: Path) -> bool:
    if not main_py_path.exists():
        return False
    content = main_py_path.read_text(encoding="utf-8", errors="replace")
    if "CORSMiddleware" in content and 'allow_origins=["*"]' in content:
        return False  # already correct

    cors_import = "from fastapi.middleware.cors import CORSMiddleware\n"
    cors_middleware = (
        'app.add_middleware(\n'
        '    CORSMiddleware,\n'
        '    allow_origins=["*"],\n'
        '    allow_credentials=True,\n'
        '    allow_methods=["*"],\n'
        '    allow_headers=["*"],\n'
        ')\n'
    )

    if "CORSMiddleware" not in content:
        # Add import after last fastapi import line
        content = re.sub(
            r"(from fastapi[^\n]*\n)",
            lambda m: m.group(0),
            content,
        )
        # Prepend import if missing
        if cors_import not in content:
            content = cors_import + content

    # Add middleware after `app = FastAPI(` line if not present
    if "CORSMiddleware" not in content or 'allow_origins=["*"]' not in content:
        content = re.sub(
            r"(app\s*=\s*FastAPI\([^)]*\)\s*\n)",
            rf"\1\n{cors_middleware}",
            content,
            count=1,
        )

    main_py_path.write_text(content, encoding="utf-8")
    return True


def _fix_auth_utils(project_path: Path) -> bool:
    utils_dir = project_path / "app" / "utils"
    utils_dir.mkdir(parents=True, exist_ok=True)
    auth_file = utils_dir / "auth.py"
    auth_file.write_text(_AUTH_UTILS_TEMPLATE, encoding="utf-8")
    # Ensure __init__.py exists
    init = utils_dir / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    return True


def _fix_auth_routes(project_path: Path) -> bool:
    """Regenerate auth_routes.py from a known-good template."""
    routes_dir = project_path / "app" / "routes"
    routes_dir.mkdir(parents=True, exist_ok=True)
    auth_routes = routes_dir / "auth_routes.py"
    auth_routes.write_text(_AUTH_ROUTES_TEMPLATE, encoding="utf-8")

    # Ensure auth_router is included in main.py
    main_py = project_path / "app" / "main.py"
    if main_py.exists():
        content = main_py.read_text(encoding="utf-8", errors="replace")
        if "auth_router" not in content:
            import_line = "from app.routes.auth_routes import auth_router\n"
            include_line = "app.include_router(auth_router)\n"
            content = import_line + content
            # Add include_router after app = FastAPI(...)
            content = re.sub(
                r"(app\s*=\s*FastAPI\([^)]*\)\s*\n)",
                rf"\1{include_line}",
                content,
                count=1,
            )
            main_py.write_text(content, encoding="utf-8")
    return True


def _fix_requirements(project_path: Path) -> bool:
    req_file = project_path / "app" / "requirements.txt"
    if not req_file.exists():
        return False
    lines = req_file.read_text(encoding="utf-8").splitlines()
    out = []
    has_pyjwt = has_bcrypt = False
    for line in lines:
        s = line.strip().lower()
        if s.startswith("passlib") or s.startswith("python-jose"):
            continue
        if s.startswith("pyjwt") or s == "jwt":
            has_pyjwt = True
        if s.startswith("bcrypt"):
            has_bcrypt = True
        out.append(line)
    if not has_pyjwt:
        out.append("PyJWT")
    if not has_bcrypt:
        out.append("bcrypt")
    req_file.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True


def _llm_fix(error: dict, project_path: Path) -> Optional[dict]:
    """Use LLM to fix complex errors not covered by deterministic patches."""
    try:
        from app.providers.ai_provider import generate_content
        endpoint = error.get("endpoint", "")
        detail = error.get("detail", "")
        fix_hint = error.get("fix_hint", "")

        # Find the relevant file
        route_hint = endpoint.strip("/").split("/")[0] if endpoint else ""
        candidate = project_path / "app" / "routes" / f"{route_hint}_routes.py"
        file_content = ""
        rel_path = ""
        if candidate.exists():
            file_content = candidate.read_text(encoding="utf-8", errors="replace")[:4000]
            rel_path = f"app/routes/{route_hint}_routes.py"

        prompt = f"""Fix this error in a deployed FastAPI app:

Error type: {error.get('type')}
Endpoint: {endpoint}
HTTP status: {error.get('status')}
Detail: {detail}
Fix hint: {fix_hint}

File path: {rel_path or 'unknown'}
Current file content:
```python
{file_content or '(file not found)'}
```

Return JSON with EXACTLY this structure:
{{"path": "app/routes/..._routes.py", "content": "...full fixed file content..."}}
"""
        from app.utils.json_cleaner import extract_json
        text = generate_content(prompt, "auto", max_tokens=6000)
        text = text.replace("```json", "").replace("```", "").strip()
        fix = extract_json(text)
        if fix.get("path") and fix.get("content"):
            return fix
    except Exception as e:
        print(f"  [deployed_fixer] LLM fix failed: {e}")
    return None


# ── Public entry point ────────────────────────────────────────────────────────

def fix_deployed_app(
    check_result: dict,
    project_path: Optional[str],
    github_url: Optional[str],
    creds: Optional[dict] = None,
) -> dict:
    """
    Apply fixes for errors found by deployed_checker, then push to GitHub.

    Returns:
        {"fixes": [...], "pushed": bool, "error": str|None}
    """
    fixes: list[str] = []
    pushed = False

    if not project_path or not Path(project_path).exists():
        return {"fixes": [], "pushed": False, "error": "Project files not found on disk — cannot apply fixes"}

    root = Path(project_path)
    main_py = root / "app" / "main.py"
    errors = check_result.get("errors", [])

    # Temporarily apply creds to env
    _saved: dict = {}
    if creds:
        import os
        for k, v in creds.items():
            if v:
                _saved[k] = os.environ.get(k)
                os.environ[k] = v

    try:
        for error in errors:
            etype = error.get("type", "")

            if etype == "CORSError":
                if _fix_cors(main_py):
                    fixes.append("Fixed CORS: added CORSMiddleware(allow_origins=['*']) to main.py")

            elif etype in ("LoginError", "RegistrationError", "AuthError"):
                status_code = error.get("status")
                detail = error.get("detail", "").lower()

                # Re-inject auth.py for all auth failures
                _fix_auth_utils(root)
                _fix_requirements(root)
                fixes.append("Replaced app/utils/auth.py with known-good bcrypt+PyJWT version")

                # If 404 or auth routes entirely missing — regenerate them
                if status_code == 404 or "not found" in detail:
                    _fix_auth_routes(root)
                    fixes.append("Regenerated app/routes/auth_routes.py from template")

                # 422 means wrong body format — check login handler accepts form data
                elif status_code == 422:
                    auth_routes = root / "app" / "routes" / "auth_routes.py"
                    if auth_routes.exists():
                        content = auth_routes.read_text(encoding="utf-8", errors="replace")
                        if "OAuth2PasswordRequestForm" not in content:
                            _fix_auth_routes(root)
                            fixes.append("Regenerated auth_routes.py: login now accepts OAuth2PasswordRequestForm")

            elif etype == "EndpointError":
                llm_fix = _llm_fix(error, root)
                if llm_fix:
                    fix_path = root / llm_fix["path"].replace("/", os.sep)
                    fix_path.parent.mkdir(parents=True, exist_ok=True)
                    fix_path.write_text(llm_fix["content"], encoding="utf-8")
                    fixes.append(f"LLM fix applied to {llm_fix['path']}")

        if fixes and github_url:
            # Push fixes to GitHub so Render auto-redeploys
            try:
                from app.services.github_service import push_to_github
                project_name = root.name
                push_result = push_to_github(str(root), project_name, description="ForgeAI post-deploy fix")
                pushed = push_result.get("success", False)
                if pushed:
                    fixes.append(f"Pushed fixes to GitHub → {push_result.get('repo_url')} — Render will redeploy automatically")
                else:
                    fixes.append(f"Fix push failed: {push_result.get('error', 'unknown')}")
            except Exception as pe:
                fixes.append(f"Could not push to GitHub: {pe}")

    finally:
        # Restore env vars
        import os
        for k, orig in _saved.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig

    return {"fixes": fixes, "pushed": pushed, "error": None}
