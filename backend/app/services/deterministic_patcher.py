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


# ── 2a. Strip ALL relationship() declarations from model files ─────────────────
# relationship() calls that reference models with no FK path (e.g. many-to-many
# without secondary=) crash the mapper at first query → ALL endpoints hang with
# sqlalchemy.exc.NoForeignKeysError / InvalidRequestError.
# Route handlers always use explicit .filter() queries, never ORM relationship
# accessors, so stripping relationships is safe and prevents the crash.

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


def _patch_strip_relationships(project_path: Path) -> int:
    """
    Strip ALL relationship() attribute declarations from SQLAlchemy model files.

    Even after stripping back_populates/backref, the base relationship("Target")
    call remains — and if there is no FK path between the two tables (common with
    many-to-many schemas where the LLM forgets secondary=), SQLAlchemy raises
    NoForeignKeysError at mapper config time, which hangs every single endpoint.

    Generated route handlers never use ORM relationship accessors; they query
    with explicit .filter() calls.  So removing these declarations is always safe.
    """
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

        if "relationship(" not in original:
            continue

        # Remove relationship() attribute assignments — may span multiple lines
        lines = original.split("\n")
        new_lines: list[str] = []
        i = 0
        removed = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            if re.match(r'\w+\s*=\s*relationship\s*\(', stripped):
                # Track parens to find the closing line
                depth = 0
                j = i
                while j < len(lines):
                    for ch in lines[j]:
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                    if depth <= 0:
                        break
                    j += 1
                removed += 1
                i = j + 1
                continue
            new_lines.append(line)
            i += 1

        if removed == 0:
            continue

        content = "\n".join(new_lines)

        # Clean up 'relationship' from sqlalchemy.orm import lines
        content = re.sub(r",\s*relationship\b", "", content)
        content = re.sub(r"\brelationship\s*,\s*", "", content)
        content = re.sub(
            r"^from sqlalchemy\.orm import relationship\s*\n", "",
            content, flags=re.MULTILINE,
        )

        mf.write_text(content, encoding="utf-8")
        patched += 1
        print(f"  [patcher] Stripped {removed} relationship() declaration(s) from {mf.name}")

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
_DB_DEPENDS = re.compile(r"\bdb\s*:\s*Session\b")
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
            # Also strip if the signature has db: Session (sync SQLAlchemy dependency)
            has_db_depends = _DB_DEPENDS.search(line)

            # Strip async if:
            # - uses sync ORM calls (db.query/add/commit/etc.), OR
            # - is a route handler with no real await, OR
            # - has db: Session parameter (sync SQLAlchemy must NOT run in async context)
            should_strip = has_sync_orm or has_db_depends or (is_route_handler and not has_real_await)
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
    """
    Fix SQLAlchemy relationship() string references that use the wrong class name.

    Python-level aliases (User = Users) do NOT work for SQLAlchemy string resolution
    because the mapper registry is keyed by cls.__name__, not by variable names.
    The only fix is to update the relationship string to match the actual class name.
    e.g. relationship('User') → relationship('Users') when class is Users.
    """
    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return

    # Build map: class_name → file path (actual class definitions only)
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

    # Scan all model files for relationship('X') where X is not a real registered class
    for mf in models_dir.glob("*.py"):
        if mf.name.startswith("_"):
            continue
        try:
            text = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        new_text = text
        changed = False
        for name in _RELATIONSHIP_STR.findall(text):
            if name in class_to_file:
                continue  # already resolvable
            # Find closest match (e.g. User → Users, Genre → Genres)
            name_lower = name.lower()
            best = None
            for cls in sorted(class_to_file):
                c = cls.lower()
                if c == name_lower + "s" or c.rstrip("s") == name_lower.rstrip("s"):
                    best = cls
                    break
            if best:
                # Replace the string in the relationship() call so SQLAlchemy can resolve it
                new_text = re.sub(
                    rf"""(relationship\(["']){re.escape(name)}(["'])""",
                    rf"\g<1>{best}\2",
                    new_text,
                )
                changed = True
                print(f"  [patcher] Fixed relationship string '{name}' → '{best}' in {mf.name}")

        if changed:
            mf.write_text(new_text, encoding="utf-8")


# ── 8. Route parameter order fix ─────────────────────────────────────────────
# Python SyntaxError: "non-default argument follows default argument"
# Caused when Path(...)/Depends(...) params come before body (Schema) params.
# Contract: body params first, then Path/Query/Depends.

_DEFAULT_MARKERS = re.compile(r"\b(Path|Query|Depends|Header|Cookie|Body|Form)\s*\(")


def _split_params(sig: str) -> list[str]:
    """Split a full parameter string into individual params respecting nested parens."""
    raw: list[str] = []
    buf = ""
    depth = 0
    for ch in sig:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            p = buf.strip()
            if p:
                raw.append(p)
            buf = ""
        else:
            buf += ch
    p = buf.strip().rstrip(",")
    if p:
        raw.append(p)
    return raw


def _param_has_default(p: str) -> bool:
    if _DEFAULT_MARKERS.search(p):
        return True
    after_colon = p.split(":", 1)[1] if ":" in p else p
    return bool(re.search(r"=\s*\S", after_colon))


def _extract_full_sig(content: str, def_pos: int) -> tuple[int, int] | None:
    """
    Given position of 'def ' in content, find the matching ')' that closes the
    parameter list.  Returns (open_paren_pos, close_paren_pos) or None.
    """
    # Find opening paren
    p = content.find("(", def_pos)
    if p == -1:
        return None
    depth = 1
    i = p + 1
    while i < len(content) and depth > 0:
        if content[i] == "(":
            depth += 1
        elif content[i] == ")":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return p, i - 1  # positions of ( and )


def _reorder_sig(content: str, open_p: int, close_p: int, indent: str) -> str | None:
    """Reorder params inside (open_p, close_p) so non-defaults come first."""
    raw_sig = content[open_p + 1: close_p]
    params = _split_params(raw_sig)
    if len(params) < 2:
        return None

    needs_reorder = False
    seen_default = False
    for p in params:
        has_def = _param_has_default(p)
        if has_def:
            seen_default = True
        elif seen_default:
            needs_reorder = True
            break

    if not needs_reorder:
        return None

    no_default = [p for p in params if not _param_has_default(p)]
    with_default = [p for p in params if _param_has_default(p)]
    reordered = no_default + with_default
    param_indent = indent + "    "
    inner = "\n" + "\n".join(f"{param_indent}{p}," for p in reordered) + "\n" + indent
    return content[:open_p + 1] + inner + content[close_p:]


def _patch_param_order(project_path: Path) -> int:
    """Reorder route params to fix 'non-default argument follows default argument'."""
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        if rf.name.startswith("_"):
            continue
        try:
            original = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Fast skip: only process files with syntax errors of this type
        try:
            compile(original, str(rf), "exec")
            continue  # No syntax error — skip
        except SyntaxError as e:
            if "non-default argument follows default argument" not in (e.msg or ""):
                continue

        content = original
        # Find all 'def ' positions and try to fix each
        for m in list(re.finditer(r"^([ \t]*)def \w+\(", content, re.MULTILINE)):
            coords = _extract_full_sig(content, m.start())
            if coords is None:
                continue
            open_p, close_p = coords
            indent = m.group(1)
            fixed = _reorder_sig(content, open_p, close_p, indent)
            if fixed is not None:
                content = fixed

        if content != original:
            rf.write_text(content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed param order in {rf.name}")

    return patched


# ── 8b. Router name fixer (router → {resource}_router) ────────────────────────
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

# rounds=4 keeps bcrypt fast (<10ms) so route handlers don't block uvicorn threads
_BCRYPT_ROUNDS = 4


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(_BCRYPT_ROUNDS)).decode("utf-8")


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


# ── 10. Auth routes injection ─────────────────────────────────────────────────
# When the architect skips auth endpoints (common for simple apps), the patcher
# injects a known-good auth_routes.py with signup/login/me.  Without this,
# every CRUD endpoint that requires auth returns 401 and the journey fails entirely.

_AUTH_ROUTES_TEMPLATE = '''\
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.auth import (
    get_password_hash, verify_password, create_access_token, get_current_user,
)

auth_router = APIRouter()


class SignupRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str = ""


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


def _get_user_model():
    import importlib
    for mod, cls in (("app.models.user", "User"), ("app.models.users", "Users")):
        try:
            m = importlib.import_module(mod)
            return getattr(m, cls)
        except (ImportError, AttributeError):
            continue
    raise ImportError("No User model found in app.models.user or app.models.users")


def _make_user(email: str, password: str, display_name: str = ""):
    """Build a User instance regardless of which password field the model uses."""
    User = _get_user_model()
    cols = {c.name for c in User.__table__.columns}
    kw: dict = {"email": email}
    pwd_hash = get_password_hash(password)
    for field in ("hashed_password", "password_hash", "password"):
        if field in cols:
            kw[field] = pwd_hash
            break
    if "display_name" in cols:
        kw["display_name"] = display_name or email.split("@")[0]
    if "username" in cols:
        kw["username"] = email.split("@")[0]
    if "is_active" in cols:
        kw["is_active"] = True
    if "role" in cols:
        kw["role"] = "user"
    return User(**kw)


def _read_password(user) -> str | None:
    for field in ("hashed_password", "password_hash", "password"):
        val = getattr(user, field, None)
        if val:
            return val
    return None


@auth_router.post("/auth/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    User = _get_user_model()
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = _make_user(req.email, req.password, req.display_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(data={"sub": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": user.email,
        "display_name": getattr(user, "display_name", req.email.split("@")[0]),
    }


@auth_router.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    User = _get_user_model()
    user = db.query(User).filter(User.email == req.email).first()
    stored = _read_password(user) if user else None
    if not user or not stored or not verify_password(req.password, stored):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_access_token(data={"sub": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": user.email,
        "display_name": getattr(user, "display_name", user.email.split("@")[0]),
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


def _patch_auth_routes(project_path: Path) -> None:
    """
    Inject a known-good auth_routes.py if the project has a User model but
    no working auth endpoints.  Also wires the router into main.py.
    """
    routes_dir = project_path / "app" / "routes"
    main_py = project_path / "app" / "main.py"
    if not routes_dir.exists() or not main_py.exists():
        return

    # Only inject when a User-like model exists
    models_dir = project_path / "app" / "models"
    has_user_model = any(
        (models_dir / name).exists() for name in ("user.py", "users.py")
    ) if models_dir.exists() else False
    if not has_user_model:
        return

    auth_routes_file = routes_dir / "auth_routes.py"
    needs_inject = False

    if not auth_routes_file.exists():
        needs_inject = True
    else:
        try:
            existing = auth_routes_file.read_text(encoding="utf-8", errors="replace")
            # Always inject our known-good template unless it's already there.
            # The known-good template is identified by the _read_password sentinel.
            # Generated auth_routes.py commonly uses user.password (wrong field name),
            # user.hashed_password, or bcrypt with rounds=12 (blocks uvicorn threads).
            # Our template detects the password field dynamically and uses rounds=4.
            if "_read_password" not in existing:
                needs_inject = True
        except Exception:
            needs_inject = True

    if needs_inject:
        auth_routes_file.write_text(_AUTH_ROUTES_TEMPLATE, encoding="utf-8")
        print("  [patcher] Injected known-good app/routes/auth_routes.py (dynamic password field + fast bcrypt)")

    # Ensure main.py imports and includes auth_router
    try:
        main_content = main_py.read_text(encoding="utf-8", errors="replace")
        changed = False

        import_line = "from app.routes.auth_routes import auth_router"
        if import_line not in main_content:
            # Insert after the last routes import, or at the top of import block
            last_routes_import = None
            for m in re.finditer(r"from app\.routes\.\w+ import \w+_router\n", main_content):
                last_routes_import = m
            if last_routes_import:
                pos = last_routes_import.end()
                main_content = main_content[:pos] + import_line + "\n" + main_content[pos:]
            else:
                main_content = import_line + "\n" + main_content
            changed = True

        include_line = "app.include_router(auth_router)"
        if include_line not in main_content:
            # Insert before Base.metadata.create_all or at end of include_router block
            last_include = None
            for m in re.finditer(r"app\.include_router\(\w+_router\)\n", main_content):
                last_include = m
            if last_include:
                pos = last_include.end()
                main_content = main_content[:pos] + include_line + "\n" + main_content[pos:]
            else:
                main_content = re.sub(
                    r"(Base\.metadata\.create_all)",
                    include_line + "\n" + r"\1",
                    main_content,
                    count=1,
                )
            changed = True

        if changed:
            main_py.write_text(main_content, encoding="utf-8")
            print("  [patcher] Wired auth_router into main.py")
    except Exception as e:
        print(f"  [patcher] auth_routes main.py update failed: {e}")


# ── 11. Seed IndexError guard ──────────────────────────────────────────────────
# Seed scripts index queried lists like user_objs[0] assuming inserts succeeded.
# If a required model field is missing, inserts fail silently and the list is empty,
# causing IndexError.  Add a null guard after each list-building query.

def _patch_seed_robustness(project_path: Path) -> int:
    """
    1. Wrap every _create_X(db, ...) helper in seed_routes.py with try/except
       so that a TypeError (wrong User model kwargs) rolls back the DB session
       instead of holding a SQLite write lock and cascading timeouts.
    2. Add null guards before list[0] accesses.
    """
    seed_file = project_path / "app" / "routes" / "seed_routes.py"
    if not seed_file.exists():
        return 0
    try:
        content = seed_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    original = content

    # ── Fix 1: wrap every _create_* helper body in try/except + db.rollback() ──
    # Matches: def _create_foo(db, ...) with a body that does NOT already have try:
    def _wrap_create_helper(m: re.Match) -> str:
        sig = m.group(1)          # 'def _create_...(db, ...):'
        body_indent = m.group(2)  # base indent of body (e.g. '    ')
        body = m.group(3)         # body lines (already stripped of one level)
        if "try:" in body or "except" in body:
            return m.group(0)     # already wrapped
        wrapped = (
            f"{sig}\n"
            f"{body_indent}try:\n"
            + "\n".join(f"{body_indent}    {ln}" if ln.strip() else ln
                        for ln in body.rstrip("\n").split("\n"))
            + f"\n{body_indent}except Exception:\n"
            f"{body_indent}    db.rollback()\n"
            f"{body_indent}    return None\n"
        )
        return wrapped

    content = re.sub(
        r"(def _create_\w+\([^)]*\):)\n([ \t]+)((?:(?!\ndef )[\s\S])*?)(?=\ndef |\Z)",
        _wrap_create_helper,
        content,
    )

    if "[0]" not in content:
        if content != original:
            seed_file.write_text(content, encoding="utf-8")
            return 1
        return 0

    # Find all variable names accessed with [0]
    indexed_vars: set[str] = set(re.findall(r'\b([a-z_]\w*)\[0\]', content))
    if not indexed_vars:
        return 0

    modified = False
    for var in sorted(indexed_vars):
        guard_check = f"if not {var}:"
        if guard_check in content:
            continue  # already guarded

        # Find the last assignment to this variable before the first [0] usage
        first_index_pos = content.find(f"{var}[0]")
        if first_index_pos == -1:
            continue

        assign_pat = re.compile(rf'^([ \t]*){re.escape(var)}\s*=\s*', re.MULTILINE)
        last_assign = None
        for m in assign_pat.finditer(content, 0, first_index_pos):
            last_assign = m

        if last_assign is None:
            continue

        # Insert the guard right after the assignment line ends
        line_end = content.find('\n', last_assign.start())
        if line_end == -1:
            continue
        indent = last_assign.group(1)
        guard = f"\n{indent}if not {var}:\n{indent}    return {{\"message\": \"Seed skipped: {var} is empty\"}}"
        content = content[:line_end] + guard + content[line_end:]
        modified = True
        print(f"  [patcher] Added null guard for '{var}' in seed_routes.py")

    if modified:
        seed_file.write_text(content, encoding="utf-8")
        return 1
    return 0


# ── 12a. Fix = Depends() on body parameters in route handlers ──────────────────
# LLM sometimes generates: `entry_in: MySchema = Depends()` instead of `entry_in: MySchema`
# This is wrong — Depends() on a Pydantic model treats it as a query-param dependency,
# not a JSON body. FastAPI reads JSON bodies from plain type annotations.

_DEPENDS_BODY_RE = re.compile(
    r"(\w+):\s+([A-Z]\w+(?:Create|Update|In|Request|Schema)?)\s*=\s*Depends\(\s*\)",
)


def _patch_depends_body(content: str, filepath: str) -> str:
    """Remove = Depends() from Pydantic schema body params in route files."""
    norm = filepath.replace("\\", "/")
    if "Depends()" not in content or "/routes/" not in norm:
        return content
    return _DEPENDS_BODY_RE.sub(r"\1: \2", content)


# ── 12. Pydantic v2 from_orm → model_validate ────────────────────────────────
# Pydantic v2 deprecated from_orm(); calling it still works but only if the
# schema has model_config = ConfigDict(from_attributes=True).  The real failure
# mode is that the LLM generates schemas whose field names don't match the ORM
# model.  Replacing .from_orm(obj) with .model_validate(obj, from_attributes=True)
# is the v2 idiomatic form and surfaces a clearer ValidationError.

_FROM_ORM_RE = re.compile(r'(\w+)\.from_orm\(([^)]+)\)')


def _patch_from_orm(content: str) -> str:
    if "from_orm" not in content:
        return content
    return _FROM_ORM_RE.sub(
        lambda m: f"{m.group(1)}.model_validate({m.group(2)}, from_attributes=True)",
        content,
    )


# ── 13. Create stub schema files for missing imports ─────────────────────────
# After architecture repair, the LLM may generate route files importing from
# app.schemas.X where X.py doesn't exist.  We scan all route imports and create
# minimal Pydantic stub files so uvicorn can at least start and run the endpoint.

_SCHEMA_IMPORT_RE = re.compile(
    r"^from app\.schemas\.(\w+) import ([^\n]+)", re.MULTILINE
)
_COL_RE = re.compile(r"^\s*(\w+)\s*=\s*Column\s*\(\s*(\w+)", re.MULTILINE)
_TYPE_MAP = {
    "Integer": "int", "BigInteger": "int", "SmallInteger": "int",
    "Float": "float", "Numeric": "float",
    "String": "str", "Text": "str", "VARCHAR": "str",
    "Boolean": "bool",
    "DateTime": "Optional[str]", "Date": "Optional[str]", "Time": "Optional[str]",
}


def _patch_create_missing_schemas(project_path: Path) -> int:
    """
    Create minimal Pydantic schema files when route files import from
    app.schemas.X but X.py doesn't exist.
    """
    schemas_dir = project_path / "app" / "schemas"
    routes_dir = project_path / "app" / "routes"
    models_dir = project_path / "app" / "models"

    if not routes_dir.exists() or not schemas_dir.exists():
        return 0

    # Collect all schema module imports from route files
    schema_imports: dict[str, set[str]] = {}
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _SCHEMA_IMPORT_RE.finditer(content):
            module = m.group(1)
            names = {n.strip() for n in m.group(2).split(",") if n.strip()}
            schema_imports.setdefault(module, set()).update(names)

    created = 0
    for module, needed_classes in schema_imports.items():
        schema_file = schemas_dir / f"{module}.py"
        if schema_file.exists():
            continue

        # Try to find the matching model file (handle list_member / listmember)
        model_file: Path | None = None
        candidates = [module, module.replace("_", ""), module + "s", module.rstrip("s")]
        for mf in models_dir.glob("*.py"):
            if mf.stem in candidates or mf.stem.replace("_", "") in candidates:
                model_file = mf
                break

        # Build field lines from model columns
        columns: list[tuple[str, str]] = []
        if model_file and model_file.exists():
            model_text = model_file.read_text(encoding="utf-8", errors="replace")
            for cm in _COL_RE.finditer(model_text):
                col_name = cm.group(1)
                py_type = _TYPE_MAP.get(cm.group(2), "str")
                if col_name not in ("id",):
                    columns.append((col_name, py_type))

        base_name = "".join(w.capitalize() for w in module.split("_"))

        lines = ["from typing import Optional", "from pydantic import BaseModel", "", ""]
        for cls_name in sorted(needed_classes):
            if columns:
                field_lines = "\n".join(
                    f"    {name}: Optional[{typ}] = None" for name, typ in columns
                )
            else:
                field_lines = "    pass"
            lines += [
                f"class {cls_name}(BaseModel):",
                field_lines,
                "    model_config = {'from_attributes': True}",
                "",
                "",
            ]

        schema_file.write_text("\n".join(lines), encoding="utf-8")
        created += 1
        print(f"  [patcher] Created missing schema stub {module}.py ({len(needed_classes)} class(es))")

    return created


# ── 14. Make Response schema fields Optional so ORM mismatches don't crash ────
# LLM-generated schemas like UserResponse often have fields (e.g. username: str)
# that don't exist on the ORM model.  Pydantic v2 raises ValidationError when
# model_validate() finds a required field missing.  Making all fields on
# *Response/*Out/*Read schemas Optional[...] = None prevents the crash while
# still returning whatever fields are present.

_REQUIRED_FIELD_RE = re.compile(
    r'^(\s{4,})(\w+)\s*:\s*(?!Optional\b)([^\n=#]+?)\s*$',
    re.MULTILINE,
)
_OPTIONAL_IMPORT_RE = re.compile(r'from typing import ([^\n]+)')
_RESPONSE_CLASS_RE = re.compile(
    r'^class (\w+(?:Response|Out|Read|List|Detail|Schema)\w*)\s*\(BaseModel\)',
    re.MULTILINE | re.IGNORECASE,
)


def _patch_response_schemas_optional(project_path: Path) -> int:
    """
    For every Pydantic class whose name ends in Response/Out/Read/Schema/List/Detail,
    make all required fields Optional[T] = None.  This prevents ValidationError
    when ORM objects are missing fields the schema declares as required.
    """
    schemas_dir = project_path / "app" / "schemas"
    if not schemas_dir.exists():
        return 0

    patched = 0
    for sf in schemas_dir.glob("*.py"):
        if sf.name.startswith("_"):
            continue
        try:
            content = sf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if not _RESPONSE_CLASS_RE.search(content):
            continue

        # Find all Response-like class blocks and make their fields Optional
        new_content = content

        # Collect line ranges for each Response class body
        lines = new_content.split("\n")
        out_lines = list(lines)
        in_response_class = False
        class_indent = ""

        for idx, line in enumerate(lines):
            # Detect class declaration
            cm = re.match(r'^(class \w+(?:Response|Out|Read|List|Detail|Schema)\w*\s*\(BaseModel\))', line, re.IGNORECASE)
            if cm:
                in_response_class = True
                class_indent = ""
                continue

            if in_response_class:
                # End of class when we hit a non-indented non-blank line that's a new definition
                stripped = line.lstrip()
                if line and not line[0].isspace() and stripped and not stripped.startswith("#"):
                    in_response_class = False
                    continue

                # Skip special lines
                if not stripped or stripped.startswith("#") or stripped.startswith("model_config") or stripped.startswith("class Config"):
                    continue

                # Match a field: "    fieldname: SomeType" with no default
                fm = re.match(r'^(\s+)(\w+)\s*:\s*(?!Optional\b)([^\n=#]+?)\s*$', line)
                if fm:
                    indent = fm.group(1)
                    fname = fm.group(2)
                    ftype = fm.group(3).strip()
                    # Skip if it already has Optional or a default
                    if fname in ("id", "class", "pass") or ftype.startswith("ClassVar"):
                        continue
                    out_lines[idx] = f"{indent}{fname}: Optional[{ftype}] = None"

        new_content = "\n".join(out_lines)

        # Ensure Optional is imported
        if "Optional" not in content and "Optional[" in new_content:
            if _OPTIONAL_IMPORT_RE.search(new_content):
                new_content = _OPTIONAL_IMPORT_RE.sub(
                    lambda m: m.group(0).rstrip(")") + ", Optional)" if "Optional" not in m.group(1) else m.group(0),
                    new_content, count=1,
                )
            else:
                new_content = "from typing import Optional\n" + new_content

        if new_content != content:
            sf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Made Response schema fields Optional in {sf.name}")

    return patched


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
        patched = _patch_depends_body(patched, rel)
        patched = _patch_from_orm(patched)
        patched = _patch_orm_response_model(patched, rel)

        if patched != original:
            py_file.write_text(patched, encoding="utf-8")
            modified += 1

    # requirements.txt
    for req in root.rglob("requirements.txt"):
        _patch_requirements(req)

    # Strip ALL relationship() declarations first — prevents SQLAlchemy mapper crash
    # (NoForeignKeysError) that hangs ALL endpoints when FK path is missing/ambiguous.
    # Must run before back_populates strip since we remove the whole statement.
    _patch_strip_relationships(root)

    # Also strip residual back_populates/backref kwargs (defensive, in case any remain)
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

    # Parameter ordering: Path(...) before body param → SyntaxError → reorder
    _patch_param_order(root)

    # Auth utils: inject known-good app/utils/auth.py (eliminates passlib/jose login crashes)
    _patch_auth_utils(root)
    _patch_auth_requirements(root)

    # Auth routes: inject signup/login/me if project has a User model but no auth endpoints.
    # Without this, every authenticated endpoint returns 401 and the journey fails entirely.
    _patch_auth_routes(root)

    # Seed robustness: guard against IndexError when parent entity inserts fail
    _patch_seed_robustness(root)

    # Create stub schema files for any route imports that point to missing modules
    # (common after architecture repair generates new route files)
    _patch_create_missing_schemas(root)

    # Make Response schema fields Optional so ORM field-name mismatches don't crash
    # (e.g. UserResponse.username required but User model uses email only)
    _patch_response_schemas_optional(root)

    if modified:
        print(f"  [patcher] Patched {modified} file(s) — passlib→bcrypt, async→sync, smart quotes")

    return modified
