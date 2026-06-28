"""
Deterministic post-generation patcher.

Runs immediately after files are written, before the validation/runtime loop.
Fixes known LLM failure patterns that the contract can't fully prevent:

  1. passlib → bcrypt  (passlib breaks on bcrypt 4+ / Python 3.13)
  2. FK table imports missing from main.py  (NoReferencedTableError at startup)
  3. async def with sync SQLAlchemy calls  (RuntimeError: no running event loop)
  4. requirements.txt: ensure bcrypt present, remove passlib

All fixes are regex/AST-free pattern matching — deterministic, fast, no LLM cost.
"""
import re
from pathlib import Path


# ── 1. passlib → bcrypt ──────────────────────────────────────────────────────

_PASSLIB_IMPORT = re.compile(
    r"^from passlib[^\n]*\n|^import passlib[^\n]*\n",
    re.MULTILINE,
)
_CRYPT_CONTEXT_DEF = re.compile(
    r"pwd_context\s*=\s*CryptContext\([^)]*\)\s*\n?",
    re.DOTALL,
)
# hash call: pwd_context.hash(x)  →  bcrypt.hashpw(x.encode(), bcrypt.gensalt()).decode()
_HASH_CALL = re.compile(r"pwd_context\.hash\(([^)]+)\)")
# verify call: pwd_context.verify(plain, hashed)  →  bcrypt.checkpw(plain.encode(), hashed.encode())
_VERIFY_CALL = re.compile(r"pwd_context\.verify\(([^,]+),\s*([^)]+)\)")

BCRYPT_UTILS = (
    "import bcrypt\n\n"
    "def get_password_hash(password: str) -> str:\n"
    "    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')\n\n"
    "def verify_password(plain: str, hashed: str) -> bool:\n"
    "    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))\n"
)


def _patch_passlib(content: str) -> str:
    if "passlib" not in content:
        return content

    # Remove passlib imports
    content = _PASSLIB_IMPORT.sub("", content)
    # Remove CryptContext definition
    content = _CRYPT_CONTEXT_DEF.sub("", content)
    # Replace hash / verify calls
    content = _HASH_CALL.sub(
        lambda m: f"bcrypt.hashpw({m.group(1)}.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')",
        content,
    )
    content = _VERIFY_CALL.sub(
        lambda m: f"bcrypt.checkpw({m.group(1)}.encode('utf-8'), {m.group(2)}.encode('utf-8'))",
        content,
    )
    # Ensure bcrypt is imported at the top
    if "import bcrypt" not in content:
        content = "import bcrypt\n" + content

    return content


def _patch_requirements(req_path: Path) -> None:
    if not req_path.exists():
        return
    lines = req_path.read_text(encoding="utf-8").splitlines()
    out = []
    has_bcrypt = False
    for line in lines:
        stripped = line.strip().lower()
        # Drop passlib in all forms
        if stripped.startswith("passlib"):
            continue
        if stripped.startswith("bcrypt"):
            has_bcrypt = True
        out.append(line)
    if not has_bcrypt:
        out.append("bcrypt")
    req_path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ── 2a. Strip ALL back_populates / backref from model files ───────────────────
# Wave 2.5 strips these from the files it processes, but misses cross-model
# dangling references.  A back_populates on X pointing to a missing property on Y
# makes SQLAlchemy mapper configuration fail on first query → ALL endpoints hang.

_BACK_POPULATES_RE = re.compile(
    r",\s*back_populates\s*=\s*['\"][^'\"]*['\"]",
)
_BACKREF_RE = re.compile(
    r",\s*backref\s*=\s*['\"][^'\"]*['\"]",
)


def _patch_strip_back_populates(project_path: Path) -> int:
    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return 0
    patched = 0
    for mf in models_dir.glob("*.py"):
        if mf.name.startswith("_"):
            continue
        try:
            original = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        content = _BACK_POPULATES_RE.sub("", original)
        content = _BACKREF_RE.sub("", content)
        if content != original:
            mf.write_text(content, encoding="utf-8")
            patched += 1
    return patched


# ── 2b. Strip ForeignKey constraints that reference non-existent tables ────────

_FK_COLUMN_RE = re.compile(
    r"""ForeignKey\(\s*['"]([\w]+)\.\w+['"]\s*\)""",
)


def _patch_dangling_foreign_keys(project_path: Path) -> int:
    """
    If a ForeignKey("X.id") references a table that has no model file,
    strip the ForeignKey() call so SQLAlchemy doesn't crash at startup.
    The column stays as a plain Integer.
    """
    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return 0

    # Build the set of table names that actually have a model file
    known_tables: set[str] = set()
    for mf in models_dir.glob("*.py"):
        if mf.name.startswith("_"):
            continue
        try:
            text = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r'__tablename__\s*=\s*["\'](\w+)["\']', text)
        if m:
            known_tables.add(m.group(1))

    if not known_tables:
        return 0

    patched = 0
    for mf in models_dir.glob("*.py"):
        if mf.name.startswith("_"):
            continue
        try:
            original = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        content = original
        dangling: list[str] = []

        def _strip_fk(m: re.Match) -> str:
            table = m.group(1)
            if table not in known_tables:
                dangling.append(table)
                return ""  # remove just the ForeignKey(...) call
            return m.group(0)

        content = _FK_COLUMN_RE.sub(_strip_fk, content)
        if dangling:
            # Clean up any trailing commas left after removal: Column(Integer, , nullable=...)
            content = re.sub(r",\s*,", ",", content)
            content = re.sub(r"\(\s*,", "(", content)
            content = re.sub(r",\s*\)", ")", content)
            mf.write_text(content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Stripped dangling FK(s) in {mf.name}: {dangling}")

    return patched


# ── 2b. Missing FK imports in main.py ─────────────────────────────────────────

_FK_RE = re.compile(r"""ForeignKey\(['"]([\w]+)\.""")
_FROM_APP_MODELS = re.compile(r"from app\.models\.([\w]+) import")


def _patch_main_fk_imports(project_path: Path) -> None:
    main_py = project_path / "app" / "main.py"
    if not main_py.exists():
        return

    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return

    # Build table→module map from model files
    table_to_module: dict[str, str] = {}
    for model_file in models_dir.glob("*.py"):
        if model_file.name.startswith("_"):
            continue
        text = model_file.read_text(encoding="utf-8", errors="replace")
        # Find __tablename__ = "xxx"
        m = re.search(r'__tablename__\s*=\s*["\'](\w+)["\']', text)
        if m:
            table_to_module[m.group(1)] = model_file.stem

    if not table_to_module:
        return

    main_text = main_py.read_text(encoding="utf-8", errors="replace")

    # Collect all FK table references across all model files
    referenced_tables: set[str] = set()
    for model_file in models_dir.glob("*.py"):
        if model_file.name.startswith("_"):
            continue
        text = model_file.read_text(encoding="utf-8", errors="replace")
        for m in _FK_RE.finditer(text):
            referenced_tables.add(m.group(1))

    # Find which table modules are already imported in main.py
    already_imported: set[str] = set()
    for m in _FROM_APP_MODELS.finditer(main_text):
        already_imported.add(m.group(1))

    def _real_class_name(module: str) -> str:
        """Read the actual Base-inheriting class name from a model file."""
        mf = models_dir / f"{module}.py"
        if mf.exists():
            txt = mf.read_text(encoding="utf-8", errors="replace")
            hits = re.findall(r"^class (\w+)\(Base\)", txt, re.MULTILINE)
            if hits:
                return hits[0]
        return "".join(w.capitalize() for w in module.split("_"))

    # Build import lines for anything missing
    missing_imports: list[str] = []
    for table in sorted(referenced_tables):
        module = table_to_module.get(table)
        if module and module not in already_imported:
            cls = _real_class_name(module)
            missing_imports.append(
                f"from app.models.{module} import {cls}  # noqa: F401  (FK dep)"
            )

    if not missing_imports:
        return

    # Inject after the last existing model import block
    insert_after = re.compile(r"(from app\.models\.[^\n]+\n)")
    last_match = None
    for last_match in insert_after.finditer(main_text):
        pass

    block = "\n".join(missing_imports) + "\n"
    if last_match:
        pos = last_match.end()
        main_text = main_text[:pos] + block + main_text[pos:]
    else:
        # No existing model imports — add before Base.metadata.create_all
        main_text = re.sub(
            r"(Base\.metadata\.create_all)",
            block + r"\1",
            main_text,
            count=1,
        )

    main_py.write_text(main_text, encoding="utf-8")
    print(f"  [patcher] Injected {len(missing_imports)} missing FK model import(s) into main.py")


# ── 3. async def with sync SQLAlchemy ────────────────────────────────────────

_ASYNC_DEF = re.compile(r"^async def (\w+)\(", re.MULTILINE)
_ROUTER_DECORATOR = re.compile(r"^@\w+_router\.(get|post|put|delete|patch)\b", re.MULTILINE)
_SYNC_ORM = re.compile(r"\bdb\.(query|add|commit|delete|refresh|execute|flush|rollback)\b")
_AWAIT_USAGE = re.compile(r"\bawait\b")


def _patch_async_sync(content: str) -> str:
    if "async def" not in content:
        return content

    lines = content.splitlines(keepends=True)
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _ASYNC_DEF.match(line)
        if m:
            # Look back for a router decorator on the immediately preceding non-blank line
            preceding = [result[k].rstrip() for k in range(max(0, len(result)-3), len(result)) if result[k].strip()]
            is_route_handler = any(_ROUTER_DECORATOR.match(p) for p in preceding)

            # Collect the function body
            body_lines = [line]
            j = i + 1
            while j < len(lines):
                if (lines[j].startswith("def ") or lines[j].startswith("async def ") or lines[j].startswith("class ")):
                    if not lines[j].startswith(" ") and not lines[j].startswith("\t"):
                        break
                body_lines.append(lines[j])
                j += 1
            body_text = "".join(body_lines)
            body_only = "".join(body_lines[1:])
            has_sync_orm = _SYNC_ORM.search(body_text)
            has_real_await = _AWAIT_USAGE.search(body_only)

            # Strip async if uses sync ORM, OR is a route handler with no real await
            should_strip = has_sync_orm or (is_route_handler and not has_real_await)
            if should_strip:
                body_text = body_text.replace("async def ", "def ", 1)
                result.append(body_text)
                i = j
                continue
        result.append(line)
        i += 1

    return "".join(result)


# ── 4. Model class name aliases (Games → Game, UserBadges → UserBadge, etc.) ─

_FROM_MODELS_ANY = re.compile(r"from app\.models\.(\w+) import ([^\n]+)")
_CLASS_IN_FILE = re.compile(r"^class (\w+)\(", re.MULTILINE)


def _patch_model_aliases(project_path: Path) -> None:
    """
    When Wave 2.5 renames plural class names (Games→Game) it only renames inside
    the model file and creates a shim file — but the original model file may not
    have the alias that routes expect.  This adds `Game = Games` aliases so that
    `from app.models.games import Game` works even when the class is still Games.
    """
    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return

    # Collect every class name that non-model files try to import from each module
    needed: dict[str, set[str]] = {}
    for py_file in project_path.rglob("*.py"):
        # skip model files themselves
        if py_file.parent == models_dir:
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _FROM_MODELS_ANY.finditer(content):
            module = m.group(1)
            for raw in m.group(2).split(","):
                name = raw.strip().split(" as ")[0].strip()
                if name:
                    needed.setdefault(module, set()).add(name)

    for module, names in needed.items():
        model_file = models_dir / f"{module}.py"
        if not model_file.exists():
            continue
        content = model_file.read_text(encoding="utf-8", errors="replace")
        defined = set(_CLASS_IN_FILE.findall(content))
        # Also pick up any existing top-level assignments (aliases already added)
        defined |= set(re.findall(r"^([A-Z]\w*)\s*=\s*\w+", content, re.MULTILINE))

        aliases_to_add: list[str] = []
        for name in sorted(names):
            if name in defined:
                continue
            # Find the closest defined class (case-insensitive, singular/plural match)
            best = None
            name_lower = name.lower()
            for cls in sorted(defined):
                c = cls.lower()
                if c == name_lower:
                    best = cls
                    break
                # e.g. name=Game cls=Games or name=Games cls=Game
                if c == name_lower + "s" or c + "s" == name_lower:
                    best = cls
                    break
                # e.g. name=UserBadge cls=UserBadges
                if c.rstrip("s") == name_lower.rstrip("s") and abs(len(c) - len(name_lower)) <= 2:
                    best = cls
                    break
            if best:
                aliases_to_add.append(f"{name} = {best}  # alias: patcher")

        if aliases_to_add:
            content = content.rstrip() + "\n\n" + "\n".join(aliases_to_add) + "\n"
            model_file.write_text(content, encoding="utf-8")
            print(f"  [patcher] Added aliases in {module}.py: {aliases_to_add}")


# ── 5. response_model using SQLAlchemy model instead of Pydantic schema ──────

_FROM_MODELS_IMPORT = re.compile(r"^from app\.models\.\w+ import ([\w,\s]+)", re.MULTILINE)
_RESPONSE_MODEL_ATTR = re.compile(r"\bresponse_model\s*=\s*(List\[)?(\w+)(])?")


def _patch_orm_response_model(content: str, filepath: str) -> str:
    norm = filepath.replace("\\", "/")
    if "response_model" not in content or ("/routes/" not in norm and not norm.startswith("app/routes")):
        return content

    # Collect all class names imported from app.models.*
    orm_classes: set[str] = set()
    for m in _FROM_MODELS_IMPORT.finditer(content):
        for name in m.group(1).split(","):
            orm_classes.add(name.strip().split(" as ")[-1].strip())

    if not orm_classes:
        return content

    def _replace_rm(m: re.Match) -> str:
        cls_name = m.group(2)
        if cls_name in orm_classes:
            return "response_model=None"
        return m.group(0)

    return _RESPONSE_MODEL_ATTR.sub(_replace_rm, content)


# ── 5. Pydantic v2: regex= → pattern= in Field() calls ──────────────────────

def _patch_pydantic_regex(content: str) -> str:
    """Pydantic v2 removed `regex=` kwarg from Field() — replace with `pattern=`."""
    if "regex=" not in content:
        return content
    return re.sub(r"\bregex\s*=\s*(r?['\"])", r"pattern=\1", content)


# ── 6. Smart quotes → ASCII quotes ───────────────────────────────────────────

_SMART_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "–": "-", "—": "-",
})


def _patch_smart_quotes(content: str) -> str:
    return content.translate(_SMART_QUOTE_MAP)


# ── 7. Relationship string name → alias in model file ────────────────────────
# e.g. relationship('Genre') but class is Genres — add Genre = Genres alias

_RELATIONSHIP_STR = re.compile(r"""relationship\(['"]([\w]+)['"]\s*[,)]""")


def _patch_relationship_string_aliases(project_path: Path) -> None:
    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return

    # Build map: class_name → file path
    class_to_file: dict[str, Path] = {}
    for mf in models_dir.glob("*.py"):
        if mf.name.startswith("_"):
            continue
        try:
            text = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for cls in re.findall(r"^class (\w+)\(", text, re.MULTILINE):
            class_to_file[cls] = mf

    # Scan all model files for relationship('X') where X has no class
    for mf in models_dir.glob("*.py"):
        if mf.name.startswith("_"):
            continue
        try:
            text = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for name in _RELATIONSHIP_STR.findall(text):
            if name in class_to_file:
                continue  # already resolvable
            # Find closest match (e.g. Genre → Genres)
            name_lower = name.lower()
            best = None
            for cls in class_to_file:
                c = cls.lower()
                if c == name_lower + "s" or c.rstrip("s") == name_lower.rstrip("s"):
                    best = cls
                    break
            if best and best in class_to_file:
                target_file = class_to_file[best]
                try:
                    target_text = target_file.read_text(encoding="utf-8", errors="replace")
                    alias = f"{name} = {best}  # alias"
                    if alias not in target_text:
                        target_text = target_text.rstrip() + f"\n\n{alias}\n"
                        target_file.write_text(target_text, encoding="utf-8")
                        print(f"  [patcher] Added relationship alias {alias} in {target_file.name}")
                        class_to_file[name] = target_file
                except Exception:
                    pass


# ── 8. Router name fixer (router → {resource}_router) ────────────────────────
# LLM often writes `router = APIRouter()` instead of `task_router = APIRouter()`
# The router_export_validator rejects this.  We fix it deterministically.

_APIROUTER_ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*APIRouter\(", re.MULTILINE)


def _patch_router_names(project_path: Path) -> int:
    routes_dir = project_path / "app" / "routes"
    main_py = project_path / "app" / "main.py"
    if not routes_dir.exists():
        return 0

    patched = 0
    rename_pairs: list[tuple[str, str, str]] = []  # (resource, old_name, new_name)

    for rf in routes_dir.glob("*.py"):
        if rf.name.startswith("_"):
            continue
        stem = rf.stem
        if stem.endswith("_routes"):
            resource = stem[:-7]
        elif stem.endswith("_route"):
            resource = stem[:-6]
        else:
            resource = stem
        expected = f"{resource}_router"

        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Already correct?
        if re.search(rf"^\s*{re.escape(expected)}\s*=\s*APIRouter\(", content, re.MULTILINE):
            continue

        m = _APIROUTER_ASSIGN.search(content)
        if not m:
            continue

        actual = m.group(1).strip()
        if actual == expected:
            continue
        if actual.endswith("_router") and actual != "router":
            # Already a named router (e.g. auth_router) — don't clobber
            continue

        new_content = re.sub(rf"\b{re.escape(actual)}\b", expected, content)
        if new_content != content:
            rf.write_text(new_content, encoding="utf-8")
            rename_pairs.append((resource, actual, expected))
            patched += 1
            print(f"  [patcher] Router: {actual} → {expected} in {rf.name}")

    if rename_pairs and main_py.exists():
        try:
            main_content = main_py.read_text(encoding="utf-8", errors="replace")
            changed = False
            for resource, old_name, new_name in rename_pairs:
                # Fix import: from app.routes.X_routes import <old_name>
                new_main = re.sub(
                    rf"(from\s+app\.routes\.{re.escape(resource)}_routes\s+import\s+){re.escape(old_name)}\b",
                    rf"\g<1>{new_name}",
                    main_content,
                )
                # Fix bare usage: include_router(<old_name>, ...)
                new_main = re.sub(rf"\b{re.escape(old_name)}\b", new_name, new_main)
                if new_main != main_content:
                    main_content = new_main
                    changed = True
            if changed:
                main_py.write_text(main_content, encoding="utf-8")
                print(f"  [patcher] Updated main.py for {len(rename_pairs)} router rename(s)")
        except Exception as e:
            print(f"  [patcher] main.py router update failed: {e}")

    return patched


# ── 9. Known-good app/utils/auth.py injection ────────────────────────────────
# LLM-generated auth files often use passlib, python-jose, or miss get_current_user.
# We overwrite with a template that uses bcrypt + PyJWT directly.

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


def _patch_auth_utils(project_path: Path) -> None:
    utils_dir = project_path / "app" / "utils"
    auth_file = utils_dir / "auth.py"

    if not utils_dir.exists():
        return

    should_inject = False
    if not auth_file.exists():
        should_inject = True
    else:
        try:
            content = auth_file.read_text(encoding="utf-8", errors="replace")
            if "passlib" in content or "werkzeug" in content:
                should_inject = True
            elif "python_jose" in content or "from jose" in content:
                should_inject = True
            elif "get_current_user" not in content or "verify_password" not in content:
                should_inject = True
        except Exception:
            should_inject = True

    if should_inject:
        auth_file.write_text(_AUTH_UTILS_TEMPLATE, encoding="utf-8")
        print(f"  [patcher] Injected known-good app/utils/auth.py")


def _patch_auth_requirements(project_path: Path) -> None:
    req_file = project_path / "app" / "requirements.txt"
    if not req_file.exists():
        return
    try:
        lines = req_file.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    out = []
    has_pyjwt = False
    has_bcrypt = False
    for line in lines:
        stripped = line.strip().lower()
        # Remove broken/replaced auth packages
        if stripped.startswith("passlib") or stripped.startswith("python-jose"):
            continue
        if stripped.startswith("pyjwt") or stripped == "jwt":
            has_pyjwt = True
        if stripped.startswith("bcrypt"):
            has_bcrypt = True
        out.append(line)

    if not has_pyjwt:
        out.append("PyJWT")
    if not has_bcrypt:
        out.append("bcrypt")

    req_file.write_text("\n".join(out) + "\n", encoding="utf-8")


# ── Main entry point ──────────────────────────────────────────────────────────

def run_deterministic_patches(project_path: str) -> int:
    """
    Run all deterministic patches on a generated project.
    Returns the number of files modified.
    """
    root = Path(project_path)
    modified = 0

    py_files = list(root.rglob("*.py"))
    for py_file in py_files:
        try:
            original = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel = str(py_file.relative_to(root)).replace("\\", "/")
        patched = original
        patched = _patch_smart_quotes(patched)
        patched = _patch_passlib(patched)
        patched = _patch_pydantic_regex(patched)
        patched = _patch_async_sync(patched)
        patched = _patch_orm_response_model(patched, rel)

        if patched != original:
            py_file.write_text(patched, encoding="utf-8")
            modified += 1

    # requirements.txt
    for req in root.rglob("requirements.txt"):
        _patch_requirements(req)

    # Strip ALL back_populates/backref first — prevents SQLAlchemy mapper crash
    # that hangs ALL endpoints when one relationship points to a missing property
    _patch_strip_back_populates(root)

    # Strip FKs to non-existent tables (prevents NoReferencedTableError at startup)
    _patch_dangling_foreign_keys(root)

    # Model class aliases (Games→Game etc) — run before FK import patcher
    _patch_model_aliases(root)

    # Relationship string aliases (Genre→Genres etc) — must run before FK import patcher
    _patch_relationship_string_aliases(root)

    # FK imports in main.py (must run after alias patch so class names are correct)
    _patch_main_fk_imports(root)

    # Router names: `router` → `{resource}_router` (eliminates RouterExportMismatch)
    _patch_router_names(root)

    # Auth utils: inject known-good app/utils/auth.py (eliminates passlib/jose login crashes)
    _patch_auth_utils(root)
    _patch_auth_requirements(root)

    if modified:
        print(f"  [patcher] Patched {modified} file(s) — passlib→bcrypt, async→sync, smart quotes")

    return modified
